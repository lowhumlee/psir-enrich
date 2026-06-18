"""Core PSIR XML logic — parsing, state, normalisation, XML emission.

This module is API-free: it knows about the PSIR XML format and per-article
state, but does not call Clarivate or any other external service.

Output strategy (v0.3.x+)
--------------------------
The output XML is a ``<collection>`` containing only the records that were
enriched.  Each enriched article is a deep copy of the original with new
fields, extids, and free-key fields added or updated in-place.  Import into
PSIR with:

  Tab: XML
  Update record action: overwrite
  Update external identifiers: ✓ CHECKED
  Default field update action: overwrite

Enrichment ladder
-----------------
Tier 0  csl-WoS promotion       free
Tier ID Starter API lookup       WoSId + PubMedID   (Starter key)
Tier X  Expanded full record     metadata, abstract, keywords,
                                  categories, funding, pages, …  (Expanded key)
"""

from __future__ import annotations

import json
import re
import uuid as uuidlib
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree

# --------------------------------------------------------------------------
# MU-Varna PSIR 4.6.4 constants
# --------------------------------------------------------------------------

NS_URI = "http://ii.pw.edu.pl/lib"
NS = {"ns2": NS_URI}

AFFILIATION_OWNER = "UMV"
LOCAL_ID_PREFIX = "UMV"

EXTERNAL_ID_TERMTYPE_UUID = "WUT6deee7292297435fb414dcd87dd84f0d"

EXTID_DEFINITIONS = {
    "WoSId": {
        "idtype_uuid": "WUT8451786af88c4580b1198f347a0048f5",
        "name_en": "WoS Identifier",
        "name_pl": "Идентификатор WoS",
        "priority": "EI110",
    },
    "PubMedID": {
        "idtype_uuid": "WUT0bfbfdfcb0974f4db3731f2527055a27",
        "name_en": "PubMed ID",
        "name_pl": "Идентификатор PubMed",
        "priority": "EI130",
    },
}

# citationsWoS indicator — confirmed from testXMLexpanded1.xml
CITATIONS_WOS_NAME_UUID = "WUTe0f43d022c60406e898cc3ac4720359d"
CITATIONS_WOS_TERMTYPE_UUID = "WUTde3a681a169d462f8f2ff0aecda362c1"

# WoS collection UT prefixes
_WOS_UT_PREFIXES = frozenset({
    "WOS", "ISI", "MEDLINE", "BCI", "BIOABS", "BIOSIS",
    "CCC", "DIIDW", "DRCI", "ZOOREC", "PPRN", "CABI", "WOK",
})

# Prefixes that cannot use Expanded /id/{uid} — only DOI lookup
_EXPANDED_UID_UNSUPPORTED = frozenset({"CABI", "WOK"})

# Prefixes that cannot use Starter /documents/{uid}
_STARTER_API_UID_UNSUPPORTED = frozenset({"MEDLINE", "CABI", "ZOOREC", "PPRN", "WOK"})

EXCLUDED_DOC_TYPES_FOR_PMID = (
    "meeting abstract",
    "meeting abstract; book chapter",
    "abstract of published item",
)


# --------------------------------------------------------------------------
# UUID helpers
# --------------------------------------------------------------------------

def gen_local_uuid() -> str:
    return LOCAL_ID_PREFIX + uuidlib.uuid4().hex


# --------------------------------------------------------------------------
# ID normalisation
# --------------------------------------------------------------------------

def norm_wos_ut(value) -> Optional[str]:
    """Normalise a WoS UT to its canonical stored form.

    Accepts any UT whose prefix is a known WoS collection code and returns
    it verbatim (uppercasing the prefix).  Also accepts bare numeric strings
    (≥8 digits) and promotes them to ``WOS:<digits>``.  Rejects Zotero-style
    csl keys, Scopus EIDs, and non-WoS identifiers — returns None so the
    caller falls through to an API lookup instead.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None

    if ":" in s:
        prefix, _, accession = s.partition(":")
        prefix_upper = prefix.strip().upper()
        accession = accession.strip()
        if prefix_upper not in _WOS_UT_PREFIXES or not accession:
            return None
        if prefix_upper == "ISI":
            prefix_upper = "WOS"
        return f"{prefix_upper}:{accession}"

    if re.match(r"^\d{8,}$", s):
        return f"WOS:{s}"
    return None


def norm_doi(value) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    return s if s.startswith("10.") else None


def norm_pmid(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan"):
        return None
    digits = re.sub(r"\D", "", s)
    return digits or None


def is_meeting_abstract(doc_type: Optional[str]) -> bool:
    if not doc_type:
        return False
    dt = str(doc_type).strip().lower()
    return any(excl in dt for excl in EXCLUDED_DOC_TYPES_FOR_PMID)


# --------------------------------------------------------------------------
# Per-article state
# --------------------------------------------------------------------------

@dataclass
class ArticleState:
    # --- identity ---
    psir_id: str = ""
    title: str = ""
    doi: Optional[str] = None

    # --- Starter-tier identifiers (existing + newly found) ---
    existing_wos: Optional[str] = None
    existing_pmid: Optional[str] = None
    csl_wos: Optional[str] = None
    new_wos: Optional[str] = None
    new_pmid: Optional[str] = None

    # --- Starter lookup state ---
    api_doc_type: Optional[str] = None
    skip_pmid_lookup: bool = False

    # --- Expanded-tier metadata ---
    # Abstract: None = not checked; "" = confirmed absent in WoS; str = value
    existing_abstractEN: Optional[str] = None
    new_abstractEN: Optional[str] = None

    # Keywords: same sentinel convention
    existing_keywordsEN: Optional[str] = None
    new_keywordsEN: Optional[str] = None        # semicolon-separated string

    # Pages / collation
    existing_collation: Optional[str] = None    # "begin-end" or None
    existing_articleNo: Optional[str] = None
    new_collation: Optional[str] = None         # "begin-end" from WoS page block
    new_articleNo: Optional[str] = None         # article number if no pages

    # Vol / issue (journalissue level — only filled if missing)
    existing_vol: Optional[str] = None
    existing_issue: Optional[str] = None
    new_vol: Optional[str] = None
    new_issue: Optional[str] = None

    # WoS categories (free fields)
    new_wos_categories: Optional[list] = None       # traditional ascatype
    new_research_areas: Optional[list] = None        # extended ascatype
    new_wos_editions: Optional[list] = None
    new_keywords_plus: Optional[list] = None
    new_early_access_date: Optional[str] = None
    new_early_access_year: Optional[str] = None

    # Funding (structured)
    new_fund_agencies: Optional[list] = None
    new_fund_grant_ids: Optional[list] = None
    new_fund_text: Optional[str] = None

    # Bookkeeping
    actions: list = field(default_factory=list)
    api_calls: int = 0
    notes: list = field(default_factory=list)

    # --- Computed helpers ---

    def needs_wos(self) -> bool:
        return not self.existing_wos and not self.new_wos

    def needs_pmid(self) -> bool:
        if self.skip_pmid_lookup:
            return False
        return not self.existing_pmid and not self.new_pmid

    def has_known_wos(self) -> Optional[str]:
        return self.existing_wos or self.new_wos

    def was_enriched(self) -> bool:
        return bool(
            self.new_wos
            or self.new_pmid
            or self.new_abstractEN
            or self.new_keywordsEN
            or self.new_collation
            or self.new_articleNo
            or self.new_vol
            or self.new_issue
            or self.new_wos_categories
            or self.new_research_areas
            or self.new_wos_editions
            or self.new_keywords_plus
            or self.new_early_access_date
            or self.new_fund_agencies
        )

    def eligible_for_expanded(self) -> bool:
        """True if the article has a WoS UT that the Expanded API can handle."""
        uid = self.has_known_wos()
        if not uid:
            return False
        prefix = uid.split(":")[0].upper() if ":" in uid else "WOS"
        return prefix not in _EXPANDED_UID_UNSUPPORTED


# --------------------------------------------------------------------------
# XML reading
# --------------------------------------------------------------------------

def parse_existing_extids(article) -> dict:
    """Return {systemName: value} for all <extid> direct children."""
    found = {}
    for ex in article.findall("extid"):
        sn = ex.find(".//systemName")
        v = ex.find("value")
        if sn is not None and sn.text and v is not None and v.text:
            found[sn.text.strip()] = v.text.strip()
    return found


def parse_csl_wos(article) -> Optional[str]:
    """Extract WoS ID from csl JSON in <ns2:field><key>csl</key>."""
    for f in article.findall(f"{{{NS_URI}}}field", NS):
        k = f.find("key")
        v = f.find("value")
        if k is None or k.text != "csl" or v is None or not v.text:
            continue
        try:
            d = json.loads(v.text)
        except json.JSONDecodeError:
            continue
        cid = d.get("id", "")
        if isinstance(cid, str) and cid.upper().startswith("WOS:"):
            return norm_wos_ut(cid)
    return None


def parse_csl_pmid(article) -> Optional[str]:
    """Extract PubMed ID from csl JSON if present."""
    for f in article.findall(f"{{{NS_URI}}}field"):
        k = f.find("key")
        v = f.find("value")
        if k is None or k.text != "csl" or v is None or not v.text:
            continue
        try:
            d = json.loads(v.text)
        except json.JSONDecodeError:
            continue
        pmid = d.get("PMID") or d.get("pmid")
        if pmid:
            return norm_pmid(pmid)
    return None


def survey_article(article) -> ArticleState:
    """Build an ArticleState from a parsed <ns2:article> element."""
    s = ArticleState()

    pid = article.find("id")
    s.psir_id = pid.text.strip() if pid is not None and pid.text else ""
    t = article.find("title")
    s.title = (t.text or "").strip() if t is not None else ""
    d = article.find("doi")
    s.doi = norm_doi(d.text) if d is not None and d.text else None

    # Extids on the article
    extids = parse_existing_extids(article)
    s.existing_wos  = norm_wos_ut(extids.get("WoSId"))
    s.existing_pmid = norm_pmid(extids.get("PubMedID"))

    # csl
    s.csl_wos = parse_csl_wos(article)
    if not s.existing_pmid:
        s.existing_pmid = parse_csl_pmid(article)

    # Obligatory metadata presence checks
    ab = article.find("abstractEN")
    s.existing_abstractEN = (ab.text or "").strip() if ab is not None else None

    kw = article.find("keywordsEN")
    s.existing_keywordsEN = (kw.text or "").strip() if kw is not None else None

    col = article.find("collation")
    s.existing_collation = (col.text or "").strip() if col is not None else None

    ano = article.find("articleNo")
    s.existing_articleNo = (ano.text or "").strip() if ano is not None else None

    # Vol / issue live on the <journalissue> child
    ji = article.find(f"{{{NS_URI}}}journalissue")
    if ji is not None:
        vol_el  = ji.find("vol")
        no_el   = ji.find("no")
        s.existing_vol   = vol_el.text.strip()  if vol_el  is not None and vol_el.text  else None
        s.existing_issue = no_el.text.strip()   if no_el   is not None and no_el.text   else None

    return s


# --------------------------------------------------------------------------
# XML writing — extids (Starter tier)
# --------------------------------------------------------------------------

def _build_extid_element(system_name: str, value: str) -> etree._Element:
    """Build a new <extid type="termfield"> element matching PSIR structure."""
    cfg = EXTID_DEFINITIONS[system_name]
    extid = etree.Element("extid", type="termfield")
    etree.SubElement(extid, "id").text = gen_local_uuid()
    etree.SubElement(extid, "owner").text = AFFILIATION_OWNER.lower() + "@mu-varna.bg"
    etree.SubElement(extid, "affiliationowner").text = AFFILIATION_OWNER

    idtype = etree.SubElement(extid, "idtype", type="term")
    etree.SubElement(idtype, "id").text = cfg["idtype_uuid"]
    etree.SubElement(idtype, "owner").text = "system"
    etree.SubElement(idtype, "affiliationowner").text = AFFILIATION_OWNER

    termtype = etree.SubElement(idtype, f"{{{NS_URI}}}termtype", type="termtype")
    etree.SubElement(termtype, "id").text = EXTERNAL_ID_TERMTYPE_UUID
    etree.SubElement(termtype, "owner").text = "system"
    etree.SubElement(termtype, "affiliationowner").text = AFFILIATION_OWNER
    etree.SubElement(termtype, "code").text = "external_id"
    etree.SubElement(termtype, "name").text = "External id"
    etree.SubElement(termtype, "nonRepeatableByDefault").text = "false"

    etree.SubElement(idtype, "systemName").text = system_name
    etree.SubElement(idtype, "namePL").text = cfg["name_pl"]
    etree.SubElement(idtype, "nameEN").text = cfg["name_en"]
    etree.SubElement(idtype, "priority").text = cfg["priority"]
    etree.SubElement(idtype, "notActive").text = "false"

    etree.SubElement(extid, "value").text = value
    return extid


def _update_extids_on_article(article, state: ArticleState) -> None:
    """Add new_wos / new_pmid extid elements to article in-place."""
    if not state.new_wos and not state.new_pmid:
        return

    existing_extid_els = {}
    last_extid_idx = -1
    children = list(article)
    for i, child in enumerate(children):
        if child.tag == "extid":
            last_extid_idx = i
            sn = child.find(".//systemName")
            if sn is not None and sn.text:
                existing_extid_els[sn.text] = child

    if last_extid_idx >= 0:
        insert_after = last_extid_idx
    else:
        anchor_tags = ["verifier", "affiliationowner", "owner", "id"]
        insert_after = 0
        for tag in anchor_tags:
            for i, child in enumerate(children):
                if child.tag == tag:
                    insert_after = i
                    break

    to_insert = []
    if state.new_wos:
        if "WoSId" in existing_extid_els:
            v = existing_extid_els["WoSId"].find("value")
            if v is not None:
                v.text = state.new_wos
        else:
            to_insert.append(_build_extid_element("WoSId", state.new_wos))

    if state.new_pmid:
        if "PubMedID" in existing_extid_els:
            v = existing_extid_els["PubMedID"].find("value")
            if v is not None:
                v.text = state.new_pmid
        else:
            to_insert.append(_build_extid_element("PubMedID", state.new_pmid))

    for offset, el in enumerate(to_insert):
        article.insert(insert_after + 1 + offset, el)


# --------------------------------------------------------------------------
# XML writing — obligatory metadata (Expanded tier)
# --------------------------------------------------------------------------

def _set_or_create_text_element(article, tag: str, value: str) -> None:
    """Set text on an existing element, or create it as a direct child."""
    el = article.find(tag)
    if el is not None:
        el.text = value
    else:
        new_el = etree.SubElement(article, tag)
        new_el.text = value


def _update_journalissue_field(article, tag: str, value: str) -> None:
    """Update vol/no inside <ns2:journalissue>."""
    ji = article.find(f"{{{NS_URI}}}journalissue")
    if ji is None:
        return
    el = ji.find(tag)
    if el is not None:
        el.text = value
    else:
        etree.SubElement(ji, tag).text = value


def _build_ns2_field(key: str, value: str) -> etree._Element:
    """Build an <ns2:field type="field"> element for free metadata."""
    f = etree.Element(f"{{{NS_URI}}}field", type="field")
    etree.SubElement(f, "id").text = gen_local_uuid()
    etree.SubElement(f, "owner").text = AFFILIATION_OWNER.lower() + "@mu-varna.bg"
    etree.SubElement(f, "affiliationowner").text = AFFILIATION_OWNER
    etree.SubElement(f, "key").text = key
    etree.SubElement(f, "value").text = value
    return f


def _upsert_ns2_field(article, key: str, value: str) -> None:
    """Update an existing <ns2:field key=...> or append a new one."""
    for f in article.findall(f"{{{NS_URI}}}field"):
        k = f.find("key")
        if k is not None and k.text == key:
            v = f.find("value")
            if v is not None:
                v.text = value
            return
    article.append(_build_ns2_field(key, value))


def inject_expanded_metadata(article, state: ArticleState) -> None:
    """Apply all Expanded-tier enrichment to article element in-place.

    Called after enrich_one_expanded() has populated state.new_* fields.
    Only fields with non-None new values are touched; existing populated
    fields are never overwritten.
    """
    # --- Abstract ---
    if state.new_abstractEN:
        _set_or_create_text_element(article, "abstractEN", state.new_abstractEN)

    # --- Author keywords ---
    if state.new_keywordsEN:
        _set_or_create_text_element(article, "keywordsEN", state.new_keywordsEN)

    # --- Collation (pages) ---
    if state.new_collation:
        _set_or_create_text_element(article, "collation", state.new_collation)

    # --- Article number (only if no collation available) ---
    if state.new_articleNo and not state.new_collation:
        _set_or_create_text_element(article, "articleNo", state.new_articleNo)

    # --- Vol / issue on journalissue ---
    if state.new_vol:
        _update_journalissue_field(article, "vol", state.new_vol)
    if state.new_issue:
        _update_journalissue_field(article, "no", state.new_issue)

    # --- WoS subject categories (traditional) ---
    if state.new_wos_categories:
        _upsert_ns2_field(
            article,
            "wos_categories",
            " | ".join(state.new_wos_categories),
        )

    # --- Research areas (extended ascatype) ---
    if state.new_research_areas:
        _upsert_ns2_field(
            article,
            "wos_research_areas",
            " | ".join(state.new_research_areas),
        )

    
    # --- KeyWords Plus ---
    if state.new_keywords_plus:
        _upsert_ns2_field(
            article,
            "wos_keywords_plus",
            " | ".join(state.new_keywords_plus),
        )

    
    # --- Funding (structured) — stored separately from existing Funding userfield ---
    if state.new_fund_agencies:
        _upsert_ns2_field(
            article,
            "wos_grant_agencies",
            " | ".join(state.new_fund_agencies),
        )
    if state.new_fund_grant_ids:
        _upsert_ns2_field(
            article,
            "wos_grant_ids",
            " | ".join(state.new_fund_grant_ids),
        )
    # Raw WoS funding text only if the existing Funding userfield is absent
    if state.new_fund_text:
        has_funding_userfield = any(
            f.findtext("key") == "Funding"
            for f in article.findall("userfield")
        )
        if not has_funding_userfield:
            _upsert_ns2_field(article, "wos_fund_text", state.new_fund_text)


# --------------------------------------------------------------------------
# XML output — full collection
# --------------------------------------------------------------------------

def build_full_output_xml(
    input_tree: etree._ElementTree,
    states: list,
    input_label: str = "input.xml",
) -> tuple[etree._ElementTree, int]:
    """Return a new <collection> containing only the enriched articles.

    Each enriched article is a deep copy of the original with new extid
    blocks and metadata fields inserted in-place.  Articles that needed no
    changes are omitted.

    Returns (tree, n_enriched).
    """
    from io import BytesIO

    # Deep-copy the whole input tree so we can mutate freely
    buf = BytesIO()
    input_tree.write(buf, xml_declaration=True, encoding="UTF-8")
    buf.seek(0)
    parser = etree.XMLParser(remove_blank_text=False)
    full_tree = etree.parse(buf, parser)
    full_root = full_tree.getroot()

    all_articles = full_root.findall(f"{{{NS_URI}}}article")

    if len(all_articles) != len(states):
        raise ValueError(
            f"Article count mismatch: tree has {len(all_articles)}, "
            f"states has {len(states)}"
        )

    n_enriched = 0
    for article, state in zip(all_articles, states):
        if state.was_enriched():
            _update_extids_on_article(article, state)
            inject_expanded_metadata(article, state)
            n_enriched += 1

    out_root = etree.Element("collection", nsmap=full_root.nsmap)
    for article, state in zip(all_articles, states):
        if state.was_enriched():
            out_root.append(deepcopy(article))

    return etree.ElementTree(out_root), n_enriched
