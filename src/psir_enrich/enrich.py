"""Reusable enrichment pipeline — Starter + Expanded tiers.

Both the CLI and the Streamlit app call run_enrichment().  The output XML
is a <collection> containing only the enriched records, ready for direct
re-import into PSIR's XML import dialog.

Enrichment ladder
-----------------
Tier 0   csl-WoS promotion        free, no API call
Tier S1  Starter DOI lookup       fills WoSId + PubMedID
Tier S2  Starter UID lookup       fills PubMedID if UT known
Tier X   Expanded full record     fills abstract, keywords, pages, vol,
                                   issue, categories, research areas,
                                   editions, KeyWords Plus, funding, …
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
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
)
from psir_enrich.wos_client import WosStarterClient

# The expanded client is optional — only imported if an expanded_api_key
# is supplied, so the Starter-only path has no extra dependencies.
try:
    from psir_enrich.wos_expanded_client import WosExpandedClient
    _HAS_EXPANDED = True
except ImportError:
    _HAS_EXPANDED = False


ProgressCallback = Callable[[int, int, ArticleState], None]


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
                state.new_pmid = result["pmid"]
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
                    state.new_pmid = result["pmid"]
                    state.actions.append("api_uid_lookup_pmid")


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
      • collation         — from pub_info.page.{begin}-{end}
      • articleNo         — from pub_info page or article number
      • vol               — from pub_info.vol  (journalissue level)
      • issue             — from pub_info.issue (journalissue level)

    Fields enriched unconditionally (always updated from WoS):
      • wos_categories    — ascatype=traditional subjects
      • wos_research_areas— ascatype=extended subjects
      • wos_keywords_plus — KeyWords Plus
      • wos_grant_agencies, wos_grant_ids  (structured funding)
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

    # ---- Abstract (only fill if missing) --------------------------------
    if not state.existing_abstractEN:
        ab = expanded_client.extract_abstract(rec)
        if ab:
            state.new_abstractEN = ab
            state.actions.append("expanded_abstract")

    # ---- Author keywords (only fill if missing) -------------------------
    if not state.existing_keywordsEN:
        kws = expanded_client.extract_keywords_author(rec)
        if kws:
            state.new_keywordsEN = "; ".join(kws)
            state.actions.append("expanded_keywords")

    # ---- Pages / collation / vol / issue (only fill if missing) ---------
    pi = expanded_client.extract_pub_info(rec)

    if pi.get("page_begin") and pi.get("page_end"):
        collation = f"{pi['page_begin']}-{pi['page_end']}"
        if not state.existing_collation:
            state.new_collation = collation
            state.actions.append("expanded_pages")
    elif pi.get("page_begin"):
        if not state.existing_collation:
            state.new_collation = pi["page_begin"]
            state.actions.append("expanded_pages")

    if not state.existing_collation and not state.new_collation:
        # No pages — try to use article number as location
        # We don't overwrite existing articleNo (could be a conference code etc.)
        pass  # articleNo is populated by the record itself; don't derive from WoS

    if not state.existing_vol and pi.get("vol"):
        state.new_vol = pi["vol"]
        state.actions.append("expanded_vol")

    if not state.existing_issue and pi.get("issue"):
        state.new_issue = pi["issue"]
        state.actions.append("expanded_issue")

    # ---- Early access date (always take if present) ----------------------
    

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

    # ---- WoS editions (always update) -----------------------------------


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
    pmid_idtype_uuid: Optional[str] = None,   # kept for API compat; auto-detected
    skip_meeting_abstracts: bool = True,
    rate_limit: float = 0.25,
    expanded_rate_limit: float = 0.5,
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
    skip_meeting_abstracts :
        Skip PubMed lookup for meeting abstracts (default True).
    rate_limit :
        Min seconds between Starter API calls.
    expanded_rate_limit :
        Min seconds between Expanded API calls (default 0.5 s = 2 req/s).
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

    # --- Enrich each article ---
    total = len(states)
    for i, s in enumerate(states):
        # Tier 0 / S1 / S2 — Starter
        enrich_one(s, starter_client, skip_meeting_abstracts=skip_meeting_abstracts)
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
            # Obligatory metadata — gaps filled
            "had_abstract":         bool(s.existing_abstractEN),
            "new_abstract":         bool(s.new_abstractEN),
            "had_keywords":         bool(s.existing_keywordsEN),
            "new_keywords":         bool(s.new_keywordsEN),
            "had_collation":        bool(s.existing_collation),
            "new_collation":        s.new_collation or "",
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
    )
