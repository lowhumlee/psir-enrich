"""Reusable enrichment pipeline — Starter + Scopus + Expanded tiers.

Both the CLI and the Streamlit app call run_enrichment().  The output XML
is a <collection> containing only the enriched records, ready for direct
re-import into PSIR's XML import dialog.

Enrichment ladder
-----------------
Tier 0   csl-WoS promotion        free, no API call
Tier S1  Starter DOI lookup       fills WoSId + PubMedID
Tier S2  Starter UID lookup       fills PubMedID if UT known
Tier SC  Scopus lookup            fills ScopusId, PubMedID, articleNo,
                                  abstract, keywords (only when missing)
Tier X   Expanded full record     fills abstract, keywords, pages/collation,
                                  articleNo, vol, issue, categories, research
                                  areas, KeyWords Plus, funding, …

Article number vs collation
---------------------------
OMEGA-PSIR stores these as two separate fields:
  <collation>  — page range, e.g. "4-13"
  <articleNo>  — electronic article number, e.g. "1947" or "e70932"
Both can be present simultaneously. Article numbers are sourced
ONLY from the APIs — never derived from the DOI.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Optional
import pandas as pd
from lxml import etree

from psir_enrich.core import (
    NS_URI,
    ArticleState,
    build_full_output_xml,
    is_meeting_abstract,
    survey_article,
    _STARTER_API_UID_UNSUPPORTED,
    norm_pmid,
)
from psir_enrich.wos_client import WosStarterClient

# The expanded client is optional — only imported if an expanded_api_key
# is supplied, so the Starter-only path has no extra dependencies.
try:
    from psir_enrich.wos_expanded_client import WosExpandedClient
    _HAS_EXPANDED = True
except ImportError:
    _HAS_EXPANDED = False

# The Scopus client is optional in the same way.
try:
    from psir_enrich.scopus_client import ScopusClient
    _HAS_SCOPUS = True
except ImportError:
    _HAS_SCOPUS = False

ProgressCallback = Callable[[int, int, ArticleState], None]

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _needs_article_no(state: ArticleState) -> bool:
    """True when the record has no article number."""
    return (
        not state.existing_articleNo
        and not state.new_articleNo
    )

# --------------------------------------------------------------------------
# Starter-tier enrichment (Tier 0 / S1 / S2)
# --------------------------------------------------------------------------

def enrich_one(
    state: ArticleState,
    client: Optional[WosStarterClient],
    skip_meeting_abstracts: bool = True,
) -> None:
    """Run the Starter enrichment ladder on one ArticleState in place.
    
    Tier 0:  csl-WoS promotion (free, no API).
    Tier S1: Clarivate /documents?q=DO=<doi> (DOI lookup).
    Tier S2: Clarivate /documents/{uid} (UID lookup, PMID fallback).
    """
    # Tier 0 — csl-WoS
    if state.needs_wos() and state.csl_wos:
        state.new_wos = state.csl_wos
        state.actions.append("csl_wos_id")

    if client is None:
        if state.needs_wos() or state.needs_pmid():
            state.notes.append("api skipped (no client)")
        return

    if not state.needs_wos() and not state.needs_pmid():
        return

    # Tier S1 — DOI lookup
    if state.doi and (state.needs_wos() or state.needs_pmid()):
        result = client.lookup_by_doi(state.doi)
        state.api_calls += 1
        if "_error" in result:
            state.notes.append(f"DOI lookup: {result['_error']}")
        else:
            dt = result.get("doc_type")
            if dt:
                state.api_doc_type = dt
                if skip_meeting_abstracts and is_meeting_abstract(dt):
                    state.skip_pmid_lookup = True
                    state.actions.append(f"skipped_pmid:{dt}")
            if state.needs_wos() and result.get("uid"):
                state.new_wos = result["uid"]
                state.actions.append("api_doi_lookup_wos")
            if state.needs_pmid() and result.get("pmid"):
                state.new_pmid = norm_pmid(result["pmid"])
                state.actions.append("api_doi_lookup_pmid")

    # Tier S2 — UID lookup
    known_uid = state.has_known_wos()
    if known_uid and state.needs_pmid():
        prefix = known_uid.split(":")[0].upper() if ":" in known_uid else "WOS"
        if prefix in _STARTER_API_UID_UNSUPPORTED:
            state.notes.append(
                f"UID lookup skipped for {prefix}: prefix not supported by Starter API"
            )
        else:
            result = client.lookup_by_uid(known_uid)
            state.api_calls += 1
            if "_error" in result:
                state.notes.append(f"UID lookup: {result['_error']}")
            else:
                dt = result.get("doc_type")
                if dt and not state.api_doc_type:
                    state.api_doc_type = dt
                    if skip_meeting_abstracts and is_meeting_abstract(dt):
                        state.skip_pmid_lookup = True
                        state.actions.append(f"skipped_pmid:{dt}")
                        return
                if state.needs_pmid() and result.get("pmid"):
                    state.new_pmid = norm_pmid(result["pmid"])
                    state.actions.append("api_uid_lookup_pmid")

# --------------------------------------------------------------------------
# Scopus-tier enrichment (Tier SC)
# --------------------------------------------------------------------------

def enrich_one_scopus(
    state: ArticleState,
    client: Optional[ScopusClient],
) -> None:
    """Run the Scopus enrichment ladder on one ArticleState in place.

    Tier 0:  csl-Scopus promotion (free, no API).
    Tier SC1: Scopus Search API by DOI -> ScopusId, PubMedID, articleNo.
    Tier SC2: Scopus Search API by title (fallback when no DOI).
    Tier SC3: Scopus Abstract Retrieval API -> abstract / keywords /
              PubMedID / articleNo, only for fields still missing.
    
    Article numbers are taken ONLY from the Scopus API response
    (prism:startingPage / prism:endingPage / prism:articleNumber or the
    FULL-view pagerange).  They are never derived from the DOI.
    """
    # Tier 0 — csl-Scopus promotion
    if state.needs_scopus() and state.csl_scopus:
        state.new_scopus = state.csl_scopus
        state.actions.append("csl_scopus_id")

    if client is None:
        if state.needs_scopus():
            state.notes.append("scopus api skipped (no client)")
        return

    needs_scopus_id = state.needs_scopus()
    needs_pmid = state.needs_pmid()
    needs_abstract = not state.existing_abstractEN and not state.new_abstractEN
    needs_keywords = not state.existing_keywordsEN and not state.new_keywordsEN
    needs_articleNo = _needs_article_no(state)

    # Nothing at all to do — skip any API call.
    if not (needs_scopus_id or needs_pmid or needs_abstract
            or needs_keywords or needs_articleNo):
        return

    scopus_id = state.existing_scopus or state.new_scopus

    # A Search API call is only useful if it can still return something new
    # (id, PMID or article number).  When only abstract/keywords are missing
    # and the Scopus id is already known, jump straight to the Abstract
    # Retrieval API and save search quota.
    needs_search = needs_scopus_id or needs_pmid or needs_articleNo

    # Tier SC1 — Search API by DOI (returns id + pmid + articleNo in one call)
    if state.doi and needs_search:
        result = client.lookup_by_doi(state.doi)
        state.api_calls += 1
        if "_error" in result:
            state.notes.append(f"Scopus DOI lookup: {result['_error']}")
        else:
            if needs_scopus_id and result.get("scopus_id"):
                state.new_scopus = result["scopus_id"]
                scopus_id = result["scopus_id"]
                state.actions.append("api_scopus_doi")
            if needs_pmid and result.get("pmid"):
                state.new_pmid = norm_pmid(result["pmid"])
                state.actions.append("api_scopus_doi_pmid")
            if needs_articleNo and result.get("articleNo"):
                state.new_articleNo = result["articleNo"]
                state.actions.append("api_scopus_doi_articleNo")

    elif state.title and needs_scopus_id:
        # Tier SC2 — fallback title search (only when we still need the id)
        result = client.lookup_by_title_author(state.title, "")
        state.api_calls += 1
        if "_error" in result:
            state.notes.append(f"Scopus title lookup: {result['_error']}")
        elif result.get("scopus_id"):
            state.new_scopus = result["scopus_id"]
            scopus_id = result["scopus_id"]
            state.actions.append("api_scopus_title")
            if needs_pmid and result.get("pmid"):
                state.new_pmid = norm_pmid(result["pmid"])
                state.actions.append("api_scopus_title_pmid")
            if needs_articleNo and result.get("articleNo"):
                state.new_articleNo = result["articleNo"]
                state.actions.append("api_scopus_title_articleNo")
        else:
            state.notes.append("Scopus title lookup: no results")

    if not scopus_id:
        return

    # Tier SC3 — Abstract Retrieval for anything still missing
    needs_metadata = (
        (needs_abstract and not state.new_abstractEN)
        or (needs_keywords and not state.new_keywordsEN)
        or (needs_pmid and not state.new_pmid)
        or (needs_articleNo and not state.new_articleNo)
    )
    if not needs_metadata:
        return

    result = client.lookup_abstract(scopus_id)
    state.api_calls += 1
    if "_error" in result:
        state.notes.append(f"Scopus abstract lookup: {result['_error']}")
        return

    state.actions.append("api_scopus_abstract")

    if needs_abstract and not state.new_abstractEN and result.get("abstract"):
        state.new_abstractEN = result["abstract"]
        state.actions.append("api_scopus_abstract_fill")
    if needs_keywords and not state.new_keywordsEN and result.get("keywords"):
        state.new_keywordsEN = result["keywords"]
        state.actions.append("api_scopus_keywords_fill")
    if needs_pmid and not state.new_pmid and result.get("pmid"):
        state.new_pmid = norm_pmid(result["pmid"])
        state.actions.append("api_scopus_abstract_pmid")
    if needs_articleNo and not state.new_articleNo and result.get("articleNo"):
        state.new_articleNo = result["articleNo"]
        state.actions.append("api_scopus_abstract_articleNo")

# --------------------------------------------------------------------------
# Expanded-tier enrichment (Tier X)
# --------------------------------------------------------------------------

def enrich_one_expanded(
    state: ArticleState,
    expanded_client,        # WosExpandedClient | None
) -> None:
    """Fill obligatory metadata and category fields via Expanded API.

    Only called when expanded_client is not None and the article has a
    WoS UT that the Expanded API supports.

    Fields enriched (only if currently missing / empty):
      • abstractEN        — from WoS full record abstract
      • keywordsEN        — from WoS author keywords
      • PubMedID          — from WoS cluster_related identifiers
      • collation         — from pub_info page range (begin-end)
      • articleNo         — from pub_info article number (mutually
                            exclusive with collation)
      • vol               — from pub_info.vol  (journalissue level)
      • issue             — from pub_info.issue (journalissue level)

    Fields enriched unconditionally (always updated from WoS):
      • wos_categories    — ascatype=traditional subjects
      • wos_research_areas— ascatype=extended subjects
      • wos_keywords_plus — KeyWords Plus
      • wos_grant_agencies, wos_grant_ids, wos_fund_text (funding)
    """
    if expanded_client is None:
        return
    if not state.eligible_for_expanded():
        state.notes.append("expanded skipped: UT not eligible")
        return

    uid = state.has_known_wos()
    rec = expanded_client.lookup_full_record(uid)
    state.api_calls += 1
    if "_error" in rec:
        state.notes.append(f"Expanded lookup: {rec['_error']}")
        return

    state.actions.append("expanded_full_record")

    # ---- PubMedID from cluster_related identifiers (only if missing) ----
    if state.needs_pmid():
        ids = expanded_client.extract_identifiers(rec)
        pmid = ids.get("pmid")
        if pmid:
            state.new_pmid = norm_pmid(pmid)
            state.actions.append("expanded_pmid")

    # ---- Abstract (only fill if missing) --------------------------------
    if not state.existing_abstractEN and not state.new_abstractEN:
        ab = expanded_client.extract_abstract(rec)
        if ab:
            state.new_abstractEN = ab
            state.actions.append("expanded_abstract")

    # ---- Author keywords (only fill if missing) -------------------------
    if not state.existing_keywordsEN and not state.new_keywordsEN:
        kws = expanded_client.extract_keywords_author(rec)
        if kws:
            state.new_keywordsEN = "; ".join(kws)
            state.actions.append("expanded_keywords")

    # ---- Pages / collation / article number / vol / issue ---------------
    pi = expanded_client.extract_pub_info(rec)
    
    # Collation (page range).
    if not state.existing_collation and pi.get("collation"):
        state.new_collation = pi["collation"]
        state.actions.append("expanded_pages")

    # Article number — write whenever available (if not already in existing)
    if not state.existing_articleNo and not state.new_articleNo and pi.get("article_no"):
        state.new_articleNo = pi["article_no"]
        state.actions.append("expanded_articleNo")

    if not state.existing_vol and pi.get("vol"):
        state.new_vol = pi["vol"]
        state.actions.append("expanded_vol")
    if not state.existing_issue and pi.get("issue"):
        state.new_issue = pi["issue"]
        state.actions.append("expanded_issue")

    # ---- WoS categories / research areas (always update) ----------------
    cat = expanded_client.extract_category_info(rec)
    if cat["wos_categories"]:
        state.new_wos_categories = cat["wos_categories"]
        state.actions.append("expanded_wos_categories")
    if cat["research_areas"]:
        state.new_research_areas = cat["research_areas"]
    # Subheadings go into wos_research_areas if no extended subjects
    if not cat["research_areas"] and cat["subheadings"]:
        state.new_research_areas = cat["subheadings"]

    # ---- KeyWords Plus (always update) ----------------------------------
    kp = expanded_client.extract_keywords_plus(rec)
    if kp:
        state.new_keywords_plus = kp
        state.actions.append("expanded_kw_plus")

    # ---- Funding (structured) -------------------------------------------
    fund = expanded_client.extract_funding(rec)
    if fund["agencies"]:
        state.new_fund_agencies = fund["agencies"]
        state.actions.append("expanded_funding")
    if fund["grant_ids"]:
        state.new_fund_grant_ids = fund["grant_ids"]
    if fund["fund_text"]:
        state.new_fund_text = fund["fund_text"]

# --------------------------------------------------------------------------
# EnrichmentResult
# --------------------------------------------------------------------------

@dataclass
class EnrichmentResult:
    states: list
    output_xml_bytes: bytes
    audit_df: pd.DataFrame
    n_articles: int
    n_enriched: int
    n_api_calls: int          # Starter API calls
    n_api_errors: int         # Starter API errors
    n_expanded_calls: int     # Expanded API calls
    n_expanded_errors: int    # Expanded API errors
    n_scopus_calls: int       # Scopus API calls
    n_scopus_errors: int      # Scopus API errors

# --------------------------------------------------------------------------
# Master pipeline
# --------------------------------------------------------------------------

def run_enrichment(
    xml_input,
    *,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    expanded_api_key: Optional[str] = None,
    expanded_api_base: Optional[str] = None,
    scopus_api_key: Optional[str] = None,
    pmid_idtype_uuid: Optional[str] = None,   # kept for API compat; auto-detected
    skip_meeting_abstracts: bool = True,
    rate_limit: float = 0.25,
    expanded_rate_limit: float = 0.5,
    scopus_rate_limit: float = 0.35,
    owner: str = "system",
    input_label: str = "input.xml",
    progress_cb: Optional[ProgressCallback] = None,
) -> EnrichmentResult:
    """Run the full enrichment pipeline on PSIR XML.

    Parameters
    ----------
    xml_input :
        Path, bytes blob, or Streamlit UploadedFile with the PSIR XML.
    api_key :
        Clarivate WoS Starter API key. If None, only csl-WoS tier runs.
    expanded_api_key :
        Clarivate WoS Expanded API key. If None, Expanded tier is skipped.
    scopus_api_key :
        Elsevier Scopus API key. If None, Scopus tier is skipped.
    skip_meeting_abstracts :
        Skip PubMed lookup for meeting abstracts (default True).
    rate_limit :
        Min seconds between Starter API calls.
    expanded_rate_limit :
        Min seconds between Expanded API calls (default 0.5 s = 2 req/s).
    scopus_rate_limit :
        Min seconds between Scopus API calls (default 0.35 s ≈ 3 req/s).
    """
    # --- Parse input ---
    parser = etree.XMLParser(remove_blank_text=False)
    if hasattr(xml_input, "read"):
        input_tree = etree.parse(xml_input, parser)
    elif isinstance(xml_input, (bytes, bytearray)):
        input_tree = etree.parse(BytesIO(xml_input), parser)
    else:
        input_tree = etree.parse(str(xml_input), parser)

    root = input_tree.getroot()
    articles = root.findall(f"{{{NS_URI}}}article")
    states = [survey_article(a) for a in articles]

    # --- Build Starter client ---
    starter_client: Optional[WosStarterClient] = None
    if api_key:
        starter_client = WosStarterClient(
            api_key=api_key,
            base_url=api_base,
            min_interval=rate_limit,
        )

    # --- Build Expanded client ---
    expanded_client = None
    if expanded_api_key and _HAS_EXPANDED:
        expanded_client = WosExpandedClient(
            api_key=expanded_api_key,
            base_url=expanded_api_base,
            min_interval=expanded_rate_limit,
        )

    # --- Build Scopus client ---
    scopus_client = None
    if scopus_api_key and _HAS_SCOPUS:
        scopus_client = ScopusClient(
            api_key=scopus_api_key,
            min_interval=scopus_rate_limit,
        )

    # --- Enrich each article ---
    total = len(states)
    for i, s in enumerate(states):
        # Tier 0 / S1 / S2 — Starter
        enrich_one(s, starter_client, skip_meeting_abstracts=skip_meeting_abstracts)
        # Tier SC — Scopus
        enrich_one_scopus(s, scopus_client)
        # Tier X — Expanded
        enrich_one_expanded(s, expanded_client)
        if progress_cb is not None:
            progress_cb(i + 1, total, s)

    # --- Build output XML ---
    out_tree, n_enriched = build_full_output_xml(input_tree, states, input_label)
    buf = BytesIO()
    out_tree.write(
        buf,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    output_bytes = buf.getvalue()

    # --- Build audit dataframe ---
    rows = []
    for s in states:
        rows.append({
            "psir_id":              s.psir_id,
            "title":                s.title[:120],
            "doi":                  s.doi or "",
            # Starter tier
            "existing_wos":         s.existing_wos or "",
            "existing_pmid":        s.existing_pmid or "",
            "csl_wos":              s.csl_wos or "",
            "new_wos":              s.new_wos or "",
            "new_pmid":             s.new_pmid or "",
            "api_doc_type":         s.api_doc_type or "",
            "skipped_pmid":         s.skip_pmid_lookup,
            # Scopus tier
            "existing_scopus":      s.existing_scopus or "",
            "csl_scopus":           s.csl_scopus or "",
            "new_scopus":           s.new_scopus or "",
            # Obligatory metadata — gaps filled
            "had_abstract":         bool(s.existing_abstractEN),
            "new_abstract":         bool(s.new_abstractEN),
            "had_keywords":         bool(s.existing_keywordsEN),
            "new_keywords":         bool(s.new_keywordsEN),
            "had_collation":        bool(s.existing_collation),
            "new_collation":        s.new_collation or "",
            "had_articleNo":        bool(s.existing_articleNo),
            "new_articleNo":        s.new_articleNo or "",
            "had_vol":              bool(s.existing_vol),
            "new_vol":              s.new_vol or "",
            "had_issue":            bool(s.existing_issue),
            "new_issue":            s.new_issue or "",
            # Expanded tier — categories
            "wos_categories":       " | ".join(s.new_wos_categories or []),
            "research_areas":       " | ".join(s.new_research_areas or []),
            "kw_plus_count":        len(s.new_keywords_plus or []),
            # Funding
            "grant_agencies":       " | ".join(s.new_fund_agencies or []),
            "grant_ids":            " | ".join(s.new_fund_grant_ids or []),
            # Bookkeeping
            "actions":              " | ".join(s.actions),
            "api_calls":            s.api_calls,
            "notes":                " | ".join(s.notes),
            "enriched":             s.was_enriched(),
        })
    audit_df = pd.DataFrame(rows)

    return EnrichmentResult(
        states=states,
        output_xml_bytes=output_bytes,
        audit_df=audit_df,
        n_articles=len(states),
        n_enriched=n_enriched,
        n_api_calls=starter_client.daily_count if starter_client else 0,
        n_api_errors=starter_client.errors    if starter_client else 0,
        n_expanded_calls=expanded_client.daily_count if expanded_client else 0,
        n_expanded_errors=expanded_client.errors     if expanded_client else 0,
        n_scopus_calls=scopus_client.daily_count if scopus_client else 0,
        n_scopus_errors=scopus_client.errors     if scopus_client else 0,
    )
