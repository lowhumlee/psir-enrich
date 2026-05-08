"""Core PSIR XML logic — parsing, state, normalisation, XML emission.

This module is API-free: it knows about the PSIR XML format and per-article
state, but does not call Clarivate or any other external service.

Output strategy (v0.3.0)
-------------------------
The output XML is the **complete input collection** with extid blocks
added or updated in-place on each article that was enriched. Articles that
needed no enrichment are included unchanged. This is what PSIR's XML import
requires — it cannot patch from a minimal file.

Import settings to use in PSIR:
  - Tab: XML
  - Update record action: overwrite
  - Update external identifiers: ✓ CHECKED  ← critical
  - Default field update action: overwrite or Add new values
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
# MU-Varna PSIR 4.6.4 constants — taken verbatim from real exported XML.
# --------------------------------------------------------------------------

NS_URI = "http://ii.pw.edu.pl/lib"
NS = {"ns2": NS_URI}

AFFILIATION_OWNER = "UMV"
LOCAL_ID_PREFIX = "UMV"

# Confirmed from 13sample.xml — the external_id termtype shared by all extids
EXTERNAL_ID_TERMTYPE_UUID = "WUT6deee7292297435fb414dcd87dd84f0d"

# Confirmed from 13sample.xml — real UUIDs from actual extid blocks
EXTID_DEFINITIONS = {
    "WoSId": {
        "idtype_uuid": "WUT8451786af88c4580b1198f347a0048f5",
        "name_en": "WoS Identifier",
        "name_pl": "Идентификатор WoS",
        "priority": "EI110",
    },
    "PubMedID": {
        # Confirmed from 13sample.xml article[0] PubMedID extid block
        "idtype_uuid": "WUT0bfbfdfcb0974f4db3731f2527055a27",
        "name_en": "PubMed ID",
        "name_pl": "Идентификатор PubMed",
        "priority": "EI130",
    },
}

# Clarivate document types that should never get a PubMed lookup
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


# All known WoS collection UT prefixes (from Clarivate API documentation).
# UTs from any of these collections are valid and stored verbatim in PSIR.
_WOS_UT_PREFIXES = frozenset({
    "WOS",      # Web of Science Core Collection
    "ISI",      # Legacy ISI prefix (treated identically to WOS)
    "MEDLINE",  # MEDLINE / NLM
    "BCI",      # BIOSIS Citation Index
    "BIOABS",   # Biological Abstracts
    "BIOSIS",   # BIOSIS Previews
    "CCC",      # Current Contents Connect
    "DIIDW",    # Derwent Innovations Index
    "DRCI",     # Data Citation Index
    "ZOOREC",   # Zoological Records
    "PPRN",     # Preprint Citation Index
    "CABI",     # CAB Abstracts (via WoS platform)
    "WOK",      # All-databases umbrella
})

# Prefixes NOT supported by the Starter API /documents/{uid} endpoint.
# The API error message states allowed databases are: BCI, BIOABS, BIOSIS,
# CCC, DIIDW, DRCI — plus WOS/ISI (Core Collection).
# Records with these prefixes are stored in PSIR as-is but UID lookup
# is skipped to avoid a 400 error. DOI lookup is still attempted.
_STARTER_API_UID_UNSUPPORTED = frozenset({
    "MEDLINE",  # Not in Starter uid endpoint
    "CABI",     # Not in Starter uid endpoint
    "ZOOREC",   # Not in Starter uid endpoint
    "PPRN",     # Not in Starter uid endpoint
    "WOK",      # All-databases umbrella — not a valid uid prefix
})


def norm_wos_ut(value) -> Optional[str]:
    """Normalise a WoS UT to its canonical stored form.

    Accepts any UT whose prefix is a known WoS collection code and returns
    it verbatim (uppercasing only the prefix for consistency).  Also accepts
    a bare numeric string (≥8 digits) and promotes it to WOS:<digits>.

    Rejects Zotero-style csl keys, Scopus EIDs, DOIs used as csl ids, and
    any other non-WoS identifier — returns None for those so the caller
    falls through to the API lookup instead.

    Examples:
        "WOS:001711258800001"  → "WOS:001711258800001"
        "MEDLINE:32832713"     → "MEDLINE:32832713"
        "CABI:20250175695"     → "CABI:20250175695"
        "ISI:001234567"        → "WOS:001234567"  (ISI normalised to WOS)
        "milkov2026posturographic" → None
        "yaneva_diagnostic_2026"   → None
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
        # Normalise the legacy ISI prefix to WOS
        if prefix_upper == "ISI":
            prefix_upper = "WOS"
        return f"{prefix_upper}:{accession}"

    # No colon — accept only bare numeric accession numbers (≥8 digits)
    if re.match(r"^\d{8,}$", s):
        return f"WOS:{s}"

    return None


# --------------------------------------------------------------------------
# ID normalisation
# --------------------------------------------------------------------------

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
    psir_id: str = ""
    title: str = ""
    doi: Optional[str] = None
    existing_wos: Optional[str] = None
    existing_pmid: Optional[str] = None
    csl_wos: Optional[str] = None
    new_wos: Optional[str] = None
    new_pmid: Optional[str] = None
    api_doc_type: Optional[str] = None
    skip_pmid_lookup: bool = False
    actions: list = field(default_factory=list)
    api_calls: int = 0
    notes: list = field(default_factory=list)

    def needs_wos(self) -> bool:
        return not self.existing_wos and not self.new_wos

    def needs_pmid(self) -> bool:
        if self.skip_pmid_lookup:
            return False
        return not self.existing_pmid and not self.new_pmid

    def has_known_wos(self) -> Optional[str]:
        return self.existing_wos or self.new_wos

    def was_enriched(self) -> bool:
        return bool(self.new_wos or self.new_pmid)


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
    for f in article.findall("ns2:field", NS):
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


def survey_article(article) -> ArticleState:
    """Build an ArticleState from a parsed <ns2:article> element."""
    s = ArticleState()
    pid = article.find("id")
    s.psir_id = pid.text.strip() if pid is not None and pid.text else ""
    t = article.find("title")
    s.title = (t.text or "").strip() if t is not None else ""
    d = article.find("doi")
    s.doi = norm_doi(d.text) if d is not None and d.text else None
    extids = parse_existing_extids(article)
    s.existing_wos = norm_wos_ut(extids.get("WoSId"))
    s.existing_pmid = norm_pmid(extids.get("PubMedID"))
    s.csl_wos = parse_csl_wos(article)
    return s


# --------------------------------------------------------------------------
# XML writing — full collection, extids updated in-place
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
    """Mutate article in-place: add new_wos and/or new_pmid extid elements.

    Strategy:
    - If an extid with the same systemName already exists, update its
      <value> in-place (preserves the existing element's id/owner).
    - If no such extid exists, insert a new element immediately after the
      last existing extid block (or after <verifier> if there are none).
    """
    if not state.new_wos and not state.new_pmid:
        return

    # Build a map of systemName -> existing extid element
    existing_extid_els = {}
    last_extid_idx = -1
    children = list(article)
    for i, child in enumerate(children):
        if child.tag == "extid":
            last_extid_idx = i
            sn = child.find(".//systemName")
            if sn is not None and sn.text:
                existing_extid_els[sn.text] = child

    # Find the insertion point: right after the last extid, or after verifier
    if last_extid_idx >= 0:
        insert_after = last_extid_idx
    else:
        # No extids yet — insert after <verifier> if present, else after <id>
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
            # Update value in the existing element
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

    # Insert new elements after the anchor position, preserving order
    for offset, el in enumerate(to_insert):
        article.insert(insert_after + 1 + offset, el)


def build_full_output_xml(
    input_tree: etree._ElementTree,
    states: list,
    input_label: str = "input.xml",
) -> tuple[etree._ElementTree, int]:
    """Return a new <collection> containing only the enriched articles.

    Each enriched article is a deep copy of the original with the new extid
    blocks inserted in-place. Articles that needed no changes are omitted.
    The root tag and namespace match PSIR's native export format exactly,
    so the file can be re-imported directly.

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

    # Apply enrichment mutations to all articles in the copy
    n_enriched = 0
    for article, state in zip(all_articles, states):
        if state.was_enriched():
            _update_extids_on_article(article, state)
            n_enriched += 1

    # Build a new <collection> containing only the enriched articles.
    # Deep-copy each article individually before appending — lxml moves
    # elements when append() is called on an element that already belongs
    # to another tree, which can leave stale references and cause child
    # elements (like indicators) to appear twice in the output.
    from copy import deepcopy
    out_root = etree.Element("collection", nsmap=full_root.nsmap)
    for article, state in zip(all_articles, states):
        if state.was_enriched():
            out_root.append(deepcopy(article))

    return etree.ElementTree(out_root), n_enriched
