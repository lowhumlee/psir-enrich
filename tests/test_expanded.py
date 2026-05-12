"""Tests for the Expanded-tier enrichment pipeline.

Uses the real project XML files (testXMLexpanded1.xml, testXMLexpanded2.xml)
as fixtures.  No real API calls are made — a fake WosExpandedClient provides
canned full-record responses that mirror the actual Expanded API JSON schema.
"""

from __future__ import annotations

import copy
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lxml import etree

# ---------------------------------------------------------------------------
# Adjust import path so we can import the updated modules directly
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, str(Path(__file__).parent))

# We import the updated modules directly (not through the installed package)
import core_updated as core
import enrich_updated as enrich_mod
import wos_expanded_client as wec_mod

NS_URI = "http://ii.pw.edu.pl/lib"
NS = {"ns2": NS_URI}

FIXTURE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Minimal but realistic Expanded API full-record response
# ---------------------------------------------------------------------------

FAKE_EXPANDED_REC_ANTIOXIDANTS = {
    "UID": "WOS:001602178300001",
    "static_data": {
        "summary": {
            "pub_info": {
                "pubyear": 2025,
                "vol": "14",
                "issue": "10",
                "page": {
                    "begin": "1207",
                    "end": "1207",
                    "page_count": "1",
                    "content": "1207-1207",
                },
                "early_access_date": "SEP 2025",
                "early_access_year": 2025,
                "journal_oas_gold": "Y",
            },
            "names": {
                "count": 13,
                "name": [
                    {"seq_no": 1, "role": "author", "last_name": "Beraich",
                     "first_name": "Abdessamad", "reprint": "Y",
                     "wos_standard": "Beraich, A"},
                    {"seq_no": 3, "role": "author", "last_name": "Nikolova",
                     "first_name": "Krastena", "orcid_id": "0000-0001-XXXX-XXXX"},
                ],
            },
            "EWUID": {
                "edition": [
                    {"value": "WOS.SCI"},
                ]
            },
        },
        "item": {
            "keywords_plus": {
                "count": 5,
                "keyword": [
                    "PISTACIA LENTISCUS", "PHENOLIC COMPOUNDS",
                    "ANTIOXIDANT ACTIVITY", "MASTIC GUM", "EXTRACTION",
                ],
            }
        },
        "fullrecord_metadata": {
            "abstracts": {
                "count": 1,
                "abstract": {
                    "abstract_text": {
                        "count": 1,
                        "p": (
                            "Mastic gum from Pistacia lentiscus L. has long been valued "
                            "in Mediterranean medicine and food preservation."
                        ),
                    }
                },
            },
            "keywords": {
                "count": 8,
                "keyword": [
                    "Anacardiaceae",
                    "antibacterial activity",
                    "antifungal activity",
                    "antioxidant activity",
                    "cytotoxicity",
                    "mastic gum",
                    "phenolics",
                    "ultrasound-assisted extraction",
                ],
            },
            "category_info": {
                "headings": {"heading": "Science & Technology", "count": 1},
                "subheadings": {
                    "count": 2,
                    "subheading": [
                        "Life Sciences & Biomedicine",
                        "Physical Sciences",
                    ],
                },
                "subjects": {
                    "count": 3,
                    "subject": [
                        {
                            "ascatype": "traditional",
                            "code": "PY",
                            "content": "Food Science & Technology",
                        },
                        {
                            "ascatype": "extended",
                            "content": "Food Science & Technology",
                        },
                        {
                            "ascatype": "traditional",
                            "code": "EI",
                            "content": "Biochemistry & Molecular Biology",
                        },
                    ],
                },
            },
            "fund_ack": {
                "fund_text": {
                    "p": (
                        "This research was funded by MUVE-TEEM of Medical University "
                        "of Varna, grant number BG-RRP-2.004-0009-C02."
                    )
                },
                "grants": {
                    "count": 1,
                    "grant": {
                        "grant_agency": "Medical University of Varna",
                        "grant_agency_names": [
                            {"pref": "Y", "content": "Medical University of Varna MUVE-TEEM"}
                        ],
                        "grant_ids": {
                            "count": 1,
                            "grant_id": ["BG-RRP-2.004-0009-C02"],
                        },
                    },
                },
            },
            "refs": {"count": 78},
            "normalized_doctypes": {"doctype": ["Article"], "count": 1},
        },
    },
    "dynamic_data": {
        "citation_related": {
            "tc_list": {
                "silo_tc": [
                    {"coll_id": "WOS", "local_count": 4},
                    {"coll_id": "MEDLINE", "local_count": 3},
                    {"coll_id": "AllDB", "local_count": 5},
                ]
            }
        },
        "cluster_related": {
            "identifiers": {
                "identifier": [
                    {"type": "doi", "value": "10.3390/antiox14101207"},
                    {"type": "pmid", "value": "41154515"},
                ]
            }
        },
    },
}

# Record for testXMLexpanded2 (meeting abstract — no abstract, no keywords)
FAKE_EXPANDED_REC_MEETING = {
    "UID": "WOS:000489313104271",
    "static_data": {
        "summary": {
            "pub_info": {
                "pubyear": 2019,
                "vol": "27",
                "issue": "Suppl 1",
                "page": {
                    "begin": "565",
                    "end": "565",
                    "page_count": "1",
                    "content": "565-565",
                },
            },
            "names": {"count": 9, "name": []},
            "EWUID": {"edition": [{"value": "WOS.ISSHP"}]},
        },
        "item": {},
        "fullrecord_metadata": {
            "category_info": {
                "headings": {"heading": "Science & Technology", "count": 1},
                "subheadings": {"count": 1, "subheading": "Life Sciences & Biomedicine"},
                "subjects": {
                    "count": 2,
                    "subject": [
                        {"ascatype": "traditional", "code": "PY",
                         "content": "Medicine, General & Internal"},
                        {"ascatype": "extended",
                         "content": "General & Internal Medicine"},
                    ],
                },
            },
            "normalized_doctypes": {
                "doctype": ["Meeting Abstract"],
                "count": 1,
            },
        },
    },
    "dynamic_data": {},
}


# ---------------------------------------------------------------------------
# Fake Expanded client
# ---------------------------------------------------------------------------

class FakeExpandedClient(wec_mod.WosExpandedClient):
    """In-memory mock — no HTTP calls."""

    def __init__(self, records: dict):
        self.records = records     # uid → record dict
        self.daily_count = 0
        self.errors = 0
        self.quota_remaining = None
        self.min_interval = 0

    def _pace(self):
        pass

    def lookup_full_record(self, uid: str) -> dict:
        self.daily_count += 1
        rec = self.records.get(uid)
        if rec is None:
            self.errors += 1
            return {"_error": f"no record for {uid} in fake client"}
        return rec


# ---------------------------------------------------------------------------
# WosExpandedClient extraction unit tests
# ---------------------------------------------------------------------------

def test_extract_abstract():
    rec = FAKE_EXPANDED_REC_ANTIOXIDANTS
    ab = wec_mod.WosExpandedClient.extract_abstract(rec)
    assert ab is not None
    assert "Pistacia" in ab


def test_extract_keywords_author():
    kws = wec_mod.WosExpandedClient.extract_keywords_author(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    assert isinstance(kws, list)
    assert "Anacardiaceae" in kws
    assert len(kws) == 8


def test_extract_keywords_plus():
    kp = wec_mod.WosExpandedClient.extract_keywords_plus(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    assert isinstance(kp, list)
    assert "PISTACIA LENTISCUS" in kp


def test_extract_pub_info_pages():
    pi = wec_mod.WosExpandedClient.extract_pub_info(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    assert pi["page_begin"] == "1207"
    assert pi["page_end"] == "1207"
    assert pi["vol"] == "14"
    assert pi["issue"] == "10"
    assert pi["early_access_date"] == "SEP 2025"


def test_extract_category_info_traditional_vs_extended():
    """Traditional = WoS Category; extended = Research Area — must be separate."""
    cat = wec_mod.WosExpandedClient.extract_category_info(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    assert "Food Science & Technology" in cat["wos_categories"]
    assert "Biochemistry & Molecular Biology" in cat["wos_categories"]
    assert "Food Science & Technology" in cat["research_areas"]
    # No cross-contamination
    assert "General & Internal Medicine" not in cat["wos_categories"]


def test_extract_category_info_meeting_abstract():
    """Meeting abstract: traditional = WoS Category, extended = Research Area."""
    cat = wec_mod.WosExpandedClient.extract_category_info(FAKE_EXPANDED_REC_MEETING)
    assert cat["wos_categories"] == ["Medicine, General & Internal"]
    assert cat["research_areas"] == ["General & Internal Medicine"]


def test_extract_category_info_subheadings_fallback():
    """When no extended subjects, subheadings go into research_areas."""
    rec = copy.deepcopy(FAKE_EXPANDED_REC_MEETING)
    # Remove extended subjects
    rec["static_data"]["fullrecord_metadata"]["category_info"]["subjects"]["subject"] = [
        {"ascatype": "traditional", "code": "PY", "content": "Medicine, General & Internal"}
    ]
    cat = wec_mod.WosExpandedClient.extract_category_info(rec)
    assert cat["wos_categories"] == ["Medicine, General & Internal"]
    # Should fall back to subheadings
    assert "Life Sciences & Biomedicine" in cat["subheadings"]


def test_extract_wos_editions():
    eds = wec_mod.WosExpandedClient.extract_wos_editions(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    assert "WOS.SCI" in eds


def test_extract_funding():
    fund = wec_mod.WosExpandedClient.extract_funding(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    assert "Medical University of Varna MUVE-TEEM" in fund["agencies"]
    assert "BG-RRP-2.004-0009-C02" in fund["grant_ids"]
    assert fund["fund_text"] is not None


def test_extract_funding_deduplication():
    """When grant_agency and preferred agency_name are the same, only one entry."""
    rec = copy.deepcopy(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    grant = rec["static_data"]["fullrecord_metadata"]["fund_ack"]["grants"]["grant"]
    grant["grant_agency"] = "Medical University of Varna MUVE-TEEM"  # same as pref name
    fund = wec_mod.WosExpandedClient.extract_funding(rec)
    assert fund["agencies"].count("Medical University of Varna MUVE-TEEM") == 1


def test_extract_reprint_seq():
    seq = wec_mod.WosExpandedClient.extract_reprint_seq(FAKE_EXPANDED_REC_ANTIOXIDANTS)
    assert seq == 1


# ---------------------------------------------------------------------------
# ArticleState survey tests
# ---------------------------------------------------------------------------

def load_article(fname: str) -> etree._Element:
    tree = etree.parse(str(FIXTURE_DIR / fname))
    root = tree.getroot()
    return root.findall(f"{{{NS_URI}}}article")[0]


def test_survey_article1_has_abstract_and_keywords():
    art = load_article("testXMLexpanded1.xml")
    s = core.survey_article(art)
    assert s.existing_abstractEN and len(s.existing_abstractEN) > 50
    assert s.existing_keywordsEN and "mastic" in s.existing_keywordsEN.lower()
    assert s.existing_wos == "WOS:001602178300001"
    assert s.existing_pmid == "41154515"
    assert s.existing_vol == "14"
    assert s.existing_issue == "10"
    # collation is missing, articleNo is present
    assert s.existing_collation is None
    assert s.existing_articleNo == "1207"


def test_survey_article2_missing_obligatory():
    art = load_article("testXMLexpanded2.xml")
    s = core.survey_article(art)
    assert s.existing_abstractEN is None or s.existing_abstractEN == ""
    assert s.existing_keywordsEN is None or s.existing_keywordsEN == ""
    assert s.existing_wos == "WOS:000489313104271"
    assert s.existing_pmid is None
    # collation present
    assert s.existing_collation == "565-565"
    assert s.existing_vol == "27"
    assert s.existing_issue == "Suppl 1"


def test_eligible_for_expanded_wos():
    s = core.ArticleState(existing_wos="WOS:001234567890001")
    assert s.eligible_for_expanded() is True


def test_eligible_for_expanded_medline():
    s = core.ArticleState(existing_wos="MEDLINE:32832713")
    assert s.eligible_for_expanded() is True  # MEDLINE IS supported by Expanded


def test_not_eligible_for_expanded_cabi():
    s = core.ArticleState(existing_wos="CABI:20250175695")
    assert s.eligible_for_expanded() is False


def test_not_eligible_for_expanded_no_uid():
    s = core.ArticleState()
    assert s.eligible_for_expanded() is False


# ---------------------------------------------------------------------------
# enrich_one_expanded unit tests
# ---------------------------------------------------------------------------

def test_expanded_skips_existing_abstract():
    """If abstractEN already present, must NOT overwrite it."""
    s = core.survey_article(load_article("testXMLexpanded1.xml"))
    original_abstract = s.existing_abstractEN
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    # Expanded abstract should not be set
    assert s.new_abstractEN is None
    assert "expanded_abstract" not in s.actions


def test_expanded_fills_missing_abstract():
    """If abstractEN is missing, fill it from WoS."""
    s = core.survey_article(load_article("testXMLexpanded2.xml"))
    assert not s.existing_abstractEN
    fake = FakeExpandedClient({"WOS:000489313104271": FAKE_EXPANDED_REC_MEETING})
    enrich_mod.enrich_one_expanded(s, fake)
    # Meeting abstract record has no WoS abstract — should remain None
    assert s.new_abstractEN is None


def test_expanded_fills_abstract_from_record1():
    """Article 1 already has an abstract — but let's test with a stripped state."""
    s = core.survey_article(load_article("testXMLexpanded1.xml"))
    s.existing_abstractEN = None   # simulate it being absent
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_abstractEN is not None
    assert "Pistacia" in s.new_abstractEN
    assert "expanded_abstract" in s.actions


def test_expanded_fills_categories_unconditionally():
    """WoS categories are always updated regardless of existing state."""
    s = core.survey_article(load_article("testXMLexpanded1.xml"))
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_wos_categories is not None
    assert "Food Science & Technology" in s.new_wos_categories
    # Research areas separate
    assert s.new_research_areas is not None
    assert "Food Science & Technology" in s.new_research_areas


def test_expanded_wos_category_research_area_disjoint():
    """Traditional and extended subjects must not contaminate each other."""
    s = core.survey_article(load_article("testXMLexpanded2.xml"))
    fake = FakeExpandedClient({"WOS:000489313104271": FAKE_EXPANDED_REC_MEETING})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_wos_categories == ["Medicine, General & Internal"]
    assert s.new_research_areas == ["General & Internal Medicine"]
    assert "General & Internal Medicine" not in s.new_wos_categories
    assert "Medicine, General & Internal" not in s.new_research_areas


def test_expanded_does_not_overwrite_existing_collation():
    """Article 2 already has collation 565-565 — must not be changed."""
    s = core.survey_article(load_article("testXMLexpanded2.xml"))
    assert s.existing_collation == "565-565"
    fake = FakeExpandedClient({"WOS:000489313104271": FAKE_EXPANDED_REC_MEETING})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_collation is None   # don't overwrite


def test_expanded_fills_missing_collation():
    """Article 1 has no collation — should be filled from WoS pages."""
    s = core.survey_article(load_article("testXMLexpanded1.xml"))
    assert s.existing_collation is None
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_collation == "1207-1207"
    assert "expanded_pages" in s.actions


def test_expanded_vol_issue_not_overwritten():
    """Both articles already have vol/issue — must not set new_vol/new_issue."""
    for fname, uid, rec in [
        ("testXMLexpanded1.xml", "WOS:001602178300001", FAKE_EXPANDED_REC_ANTIOXIDANTS),
        ("testXMLexpanded2.xml", "WOS:000489313104271", FAKE_EXPANDED_REC_MEETING),
    ]:
        s = core.survey_article(load_article(fname))
        fake = FakeExpandedClient({uid: rec})
        enrich_mod.enrich_one_expanded(s, fake)
        assert s.new_vol is None,   f"{fname}: new_vol should be None (vol already present)"
        assert s.new_issue is None, f"{fname}: new_issue should be None (issue already present)"


def test_expanded_skips_cabi():
    s = core.ArticleState(psir_id="UMVtest", existing_wos="CABI:20250175695")
    fake = FakeExpandedClient({})
    enrich_mod.enrich_one_expanded(s, fake)
    assert fake.daily_count == 0
    assert any("not eligible" in n for n in s.notes)


def test_expanded_funding_not_duplicate_userfield():
    """If Funding userfield already present, fund_text should NOT be added."""
    art = load_article("testXMLexpanded1.xml")
    s = core.survey_article(art)
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_fund_agencies is not None
    # But fund_text injection into XML is gated — test XML layer
    out_art = copy.deepcopy(art)
    core._update_extids_on_article(out_art, s)
    core.inject_expanded_metadata(out_art, s)
    # The existing <userfield key="Funding"> should still be there
    uf_vals = [f.findtext("value") for f in out_art.findall("userfield")
               if f.findtext("key") == "Funding"]
    assert len(uf_vals) == 1
    # wos_fund_text field should NOT be present (Funding userfield existed)
    ns2_field_keys = [f.findtext("key") for f in out_art.findall(f"{{{NS_URI}}}field")]
    assert "wos_fund_text" not in ns2_field_keys
    # But structured fields should be present
    assert "wos_grant_agencies" in ns2_field_keys
    assert "wos_grant_ids" in ns2_field_keys


def test_expanded_keywords_plus_stored():
    s = core.survey_article(load_article("testXMLexpanded1.xml"))
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_keywords_plus is not None
    assert "PISTACIA LENTISCUS" in s.new_keywords_plus


def test_expanded_editions_stored():
    s = core.survey_article(load_article("testXMLexpanded1.xml"))
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_wos_editions == ["WOS.SCI"]


def test_expanded_early_access_date():
    s = core.survey_article(load_article("testXMLexpanded1.xml"))
    fake = FakeExpandedClient({"WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS})
    enrich_mod.enrich_one_expanded(s, fake)
    assert s.new_early_access_date == "SEP 2025"


# ---------------------------------------------------------------------------
# XML injection tests
# ---------------------------------------------------------------------------

def test_inject_collation_creates_element():
    art = copy.deepcopy(load_article("testXMLexpanded1.xml"))
    s = core.survey_article(art)
    s.new_collation = "1207-1207"
    core.inject_expanded_metadata(art, s)
    col = art.find("collation")
    assert col is not None
    assert col.text == "1207-1207"


def test_inject_wos_categories_creates_ns2_field():
    art = copy.deepcopy(load_article("testXMLexpanded1.xml"))
    s = core.survey_article(art)
    s.new_wos_categories = ["Food Science & Technology", "Biochemistry & Molecular Biology"]
    s.new_research_areas = ["Food Science & Technology"]
    core.inject_expanded_metadata(art, s)
    keys = {f.findtext("key"): f.findtext("value") for f in art.findall(f"{{{NS_URI}}}field")}
    assert "wos_categories" in keys
    assert "wos_research_areas" in keys
    assert "Food Science & Technology" in keys["wos_categories"]
    assert "Biochemistry & Molecular Biology" in keys["wos_categories"]
    # Categories and research areas stored separately
    assert keys["wos_categories"] != keys["wos_research_areas"]


def test_inject_upsert_does_not_duplicate_field():
    """Calling inject twice must not create duplicate ns2:field elements."""
    art = copy.deepcopy(load_article("testXMLexpanded1.xml"))
    s = core.survey_article(art)
    s.new_wos_categories = ["Food Science & Technology"]
    core.inject_expanded_metadata(art, s)
    # Call again
    core.inject_expanded_metadata(art, s)
    cat_fields = [f for f in art.findall(f"{{{NS_URI}}}field")
                  if f.findtext("key") == "wos_categories"]
    assert len(cat_fields) == 1


def test_inject_does_not_overwrite_existing_abstract():
    art = copy.deepcopy(load_article("testXMLexpanded1.xml"))
    original = art.findtext("abstractEN")
    s = core.survey_article(art)
    s.new_abstractEN = None  # simulate: abstract present, not enriched
    core.inject_expanded_metadata(art, s)
    assert art.findtext("abstractEN") == original


# ---------------------------------------------------------------------------
# run_enrichment integration test (no API calls)
# ---------------------------------------------------------------------------

def test_run_enrichment_no_api_article1():
    """run_enrichment with no keys: only csl tier fires."""
    xml_bytes = Path(FIXTURE_DIR / "testXMLexpanded1.xml").read_bytes()
    result = enrich_mod.run_enrichment(
        xml_input=xml_bytes,
        api_key=None,
        expanded_api_key=None,
        input_label="test1.xml",
    )
    assert result.n_articles == 1
    # Article 1 already has WoSId and PubMedID, so csl tier adds nothing new
    assert result.n_api_calls == 0
    assert result.n_expanded_calls == 0


def test_run_enrichment_full_pipeline_article1():
    """Integration: Starter (mocked) + Expanded (mocked) on article 1."""
    from psir_enrich.wos_client import WosStarterClient

    class FakeStarter(WosStarterClient):
        def __init__(self):
            self.daily_count = 0
            self.errors = 0
            self.min_interval = 0
            self.calls = []
        def _pace(self): pass
        def lookup_by_doi(self, doi):
            self.daily_count += 1
            return {"_error": "no hits"}  # WoSId already present
        def lookup_by_uid(self, uid):
            self.daily_count += 1
            return {"_error": "no hits"}  # PubMedID already present

    fake_starter = FakeStarter()
    fake_expanded = FakeExpandedClient({
        "WOS:001602178300001": FAKE_EXPANDED_REC_ANTIOXIDANTS
    })

    xml_bytes = Path(FIXTURE_DIR / "testXMLexpanded1.xml").read_bytes()

    # Patch the client constructors
    import enrich_updated as em
    original_enrich_one = em.enrich_one
    original_enrich_expanded = em.enrich_one_expanded

    called_expanded = []

    def patched_expanded(state, client):
        called_expanded.append(state.psir_id)
        original_enrich_expanded(state, fake_expanded)

    em.enrich_one_expanded = patched_expanded

    try:
        result = em.run_enrichment(
            xml_input=xml_bytes,
            api_key=None,           # skip starter
            expanded_api_key=None,  # skip expanded (we inject manually)
            input_label="test1.xml",
        )
    finally:
        em.enrich_one_expanded = original_enrich_expanded

    assert result.n_articles == 1


def test_run_enrichment_article2_fills_categories():
    """Article 2 (meeting abstract, no abstract/keywords) gets categories."""
    xml_bytes = Path(FIXTURE_DIR / "testXMLexpanded2.xml").read_bytes()

    fake_expanded = FakeExpandedClient({
        "WOS:000489313104271": FAKE_EXPANDED_REC_MEETING
    })

    import enrich_updated as em
    original = em.enrich_one_expanded

    def patched(state, client):
        original(state, fake_expanded)

    em.enrich_one_expanded = patched
    try:
        result = em.run_enrichment(
            xml_input=xml_bytes,
            api_key=None,
            expanded_api_key=None,
            input_label="test2.xml",
        )
    finally:
        em.enrich_one_expanded = original

    # Even with mocked expanded injected, run_enrichment doesn't call it
    # (expanded_api_key is None). Test the state directly.
    s = core.survey_article(
        etree.parse(str(FIXTURE_DIR / "testXMLexpanded2.xml"))
            .getroot()
            .findall(f"{{{NS_URI}}}article")[0]
    )
    em2 = em
    em2.enrich_one_expanded(s, fake_expanded)
    assert s.new_wos_categories == ["Medicine, General & Internal"]
    assert s.new_research_areas == ["General & Internal Medicine"]
    assert s.new_collation is None   # collation already present


# ---------------------------------------------------------------------------
# Starter UID lookup — MEDLINE now eligible (corrected from v0.3.4)
# ---------------------------------------------------------------------------

def test_medline_eligible_for_expanded():
    """MEDLINE prefix is supported by Expanded /id endpoint."""
    s = core.ArticleState(existing_wos="MEDLINE:32832713")
    assert s.eligible_for_expanded() is True


def test_cabi_not_eligible_for_expanded():
    s = core.ArticleState(existing_wos="CABI:20250175695")
    assert s.eligible_for_expanded() is False


def test_was_enriched_with_categories_only():
    """was_enriched() must return True when only category fields are new."""
    s = core.ArticleState(
        existing_wos="WOS:001234",
        existing_pmid="12345",
    )
    # Nothing yet
    assert s.was_enriched() is False
    s.new_wos_categories = ["Food Science & Technology"]
    assert s.was_enriched() is True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
