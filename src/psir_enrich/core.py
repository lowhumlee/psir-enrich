"""Core PSIR XML logic — parsing, state, normalisation, XML emission.

This module is API-free: it knows about the PSIR XML format and per-article
state, but does not call Clarivate or any other external service.
"""

from __future__ import annotations

import json
import re
import uuid as uuidlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from lxml import etree

# --------------------------------------------------------------------------
# MU-Varna PSIR 4.6.4 constants — taken verbatim from a real exported XML.
# Override via env vars or the CLI when deploying at another institution.
# --------------------------------------------------------------------------

NS_URI = "http://ii.pw.edu.pl/lib"
NS = {"ns2": NS_URI}

AFFILIATION_OWNER = "UMV"
LOCAL_ID_PREFIX = "UMV"
EXTERNAL_ID_TERMTYPE_UUID = "WUT6deee7292297435fb414dcd87dd84f0d"

# Each external-identifier dictionary entry has its own UUID + system name.
# WoS values come from the real exported XML.
# PubMedID UUID must be filled from local PSIR before PubMed extids can be
# written — supplied at runtime via --pmid-idtype-uuid.
EXTID_DEFINITIONS = {
    "WoSId": {
        "idtype_uuid": "WUT8451786af88c4580b1198f347a0048f5",
        "name_en": "WoS Identifier",
        "name_pl": "Идентификатор WoS",
        "priority": "EI110",
    },
    "PubMedID": {
        "idtype_uuid": None,  # set at runtime
        "name_en": "PubMed ID",
        "name_pl": "Идентификатор PubMed",
        "priority": "EI120",
    },
}

# Clarivate document type strings that should never get a PubMed lookup.
# Compared case-insensitively. Keep as a tuple for cheap iteration.
EXCLUDED_DOC_TYPES_FOR_PMID = (
    "meeting abstract",
    "meeting abstract; book chapter",  # rare combined type
    "abstract of published item",       # rare WoS type for republished abstracts
)


# --------------------------------------------------------------------------
# Helpers — UUID + ID normalisation
# --------------------------------------------------------------------------


def gen_local_uuid() -> str:
    """Generate a PSIR-local UUID with the institution prefix."""
    return LOCAL_ID_PREFIX + uuidlib.uuid4().hex


def norm_doi(value) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    return s if s.startswith("10.") else None


def norm_wos_ut(value) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"^(WOS:|ISI:|WoS:|wos:)", "", s, flags=re.IGNORECASE)
    return f"WOS:{s}" if s else None


def norm_pmid(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan"):
        return None
    digits = re.sub(r"\D", "", s)
    return digits or None


def is_meeting_abstract(doc_type: Optional[str]) -> bool:
    """True if the WoS document type indicates a meeting abstract.
    Case-insensitive substring match against EXCLUDED_DOC_TYPES_FOR_PMID."""
    if not doc_type:
        return False
    dt_lower = str(doc_type).strip().lower()
    return any(excl in dt_lower for excl in EXCLUDED_DOC_TYPES_FOR_PMID)


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
    # Outputs of enrichment:
    new_wos: Optional[str] = None
    new_pmid: Optional[str] = None
    # Discovered metadata from API:
    api_doc_type: Optional[str] = None
    skip_pmid_lookup: bool = False  # set when known to be meeting abstract
    # Audit:
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


# --------------------------------------------------------------------------
# XML reading
# --------------------------------------------------------------------------


def parse_existing_extids(article) -> dict:
    """Return {systemName: value} for all <extid> direct children of an article."""
    found = {}
    for ex in article.findall("extid"):
        sn = ex.find(".//systemName")
        v = ex.find("value")
        if sn is not None and sn.text and v is not None and v.text:
            found[sn.text.strip()] = v.text.strip()
    return found


def parse_csl_wos(article) -> Optional[str]:
    """Look at every <ns2:field><key>csl</key><value>...</value></ns2:field>
    block and pull the WoS id from the JSON if present."""
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
# Patch XML emission
# --------------------------------------------------------------------------


def build_extid_element(system_name: str,
                        value: str,
                        owner: str) -> etree._Element:
    """Build an <extid type="termfield"> matching MU-Varna's exact structure."""
    cfg = EXTID_DEFINITIONS[system_name]
    if cfg["idtype_uuid"] is None:
        raise ValueError(
            f"Cannot create extid for '{system_name}': idtype_uuid not configured."
        )

    extid = etree.Element("extid", type="termfield")
    etree.SubElement(extid, "id").text = gen_local_uuid()
    etree.SubElement(extid, "owner").text = owner
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


def build_patch_xml(states: list,
                    owner: str,
                    input_path: Path) -> etree._ElementTree:
    """Build the patch XML <publications> root with one <publication> per
    record that gained at least one new extid."""
    NSMAP = {"ns2": NS_URI}
    root = etree.Element("publications", nsmap=NSMAP)

    comment = etree.Comment(
        f" PSIR enrichment patch — generated "
        f"{datetime.now().isoformat(timespec='seconds')} "
        f"from {input_path.name}. Each <publication> carries its existing "
        f"PSIR id plus only the newly added external identifiers. "
    )
    root.append(comment)

    written = 0
    for s in states:
        new_extids = []
        if s.new_wos:
            new_extids.append(("WoSId", s.new_wos))
        if s.new_pmid and EXTID_DEFINITIONS["PubMedID"]["idtype_uuid"]:
            new_extids.append(("PubMedID", s.new_pmid))
        if not new_extids:
            continue

        pub = etree.SubElement(root, "publication")
        etree.SubElement(pub, "id").text = s.psir_id
        etree.SubElement(pub, "affiliationowner").text = AFFILIATION_OWNER
        for sysname, value in new_extids:
            try:
                pub.append(build_extid_element(sysname, value, owner))
            except ValueError as e:
                s.notes.append(str(e))
                pub.remove(pub[-1])
                continue
        written += 1

    return etree.ElementTree(root), written
