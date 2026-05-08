"""Integration tests for the CLI — uses a mocked WoS client so no real
API calls are made."""

import shutil
from pathlib import Path

import pandas as pd
import pytest
from lxml import etree

import psir_enrich.enrich as cli_mod
import psir_enrich.cli as cli_module
from psir_enrich.core import EXTID_DEFINITIONS, ArticleState, survey_article
from psir_enrich.wos_client import WosStarterClient


# --- Fake client ---------------------------------------------------------


class FakeWosClient(WosStarterClient):
    """In-memory mock of WosStarterClient for tests."""

    def __init__(self, by_doi=None, by_uid=None):
        self.by_doi = by_doi or {}
        self.by_uid = by_uid or {}
        self.daily_count = 0
        self.errors = 0
        self.min_interval = 0
        self.calls = []

    def _pace(self):
        pass

    def lookup_by_doi(self, doi):
        self.daily_count += 1
        self.calls.append(("doi", doi))
        return self.by_doi.get(doi, {"_error": "no hits"})

    def lookup_by_uid(self, uid):
        self.daily_count += 1
        self.calls.append(("uid", uid))
        return self.by_uid.get(uid, {"_error": "no hits"})


# --- enrich_one tests ----------------------------------------------------


def test_enrich_csl_only_no_api():
    s = ArticleState(psir_id="UMV1", title="t", doi="10.1/a",
                     csl_wos="WOS:001234")
    cli_mod.enrich_one(s, client=None)
    assert s.new_wos == "WOS:001234"
    assert "csl_wos_id" in s.actions


def test_enrich_doi_lookup_returns_both():
    s = ArticleState(psir_id="UMV1", doi="10.1/a")
    fake = FakeWosClient(by_doi={
        "10.1/a": {"uid": "WOS:00111", "pmid": "999", "doc_type": "Article"},
    })
    cli_mod.enrich_one(s, fake)
    assert s.new_wos == "WOS:00111"
    assert s.new_pmid == "999"
    assert "api_doi_lookup_wos" in s.actions
    assert "api_doi_lookup_pmid" in s.actions


def test_enrich_meeting_abstract_excludes_pmid():
    """When API returns Meeting Abstract, we take the UT but skip PMID
    even if the response had one (defensive)."""
    s = ArticleState(psir_id="UMV1", doi="10.1/abstract")
    fake = FakeWosClient(by_doi={
        "10.1/abstract": {
            "uid": "WOS:00222",
            "pmid": "888",  # API gave one
            "doc_type": "Meeting Abstract",
        },
    })
    cli_mod.enrich_one(s, fake, skip_meeting_abstracts=True)
    assert s.new_wos == "WOS:00222"
    # PMID should NOT have been written, since needs_pmid() returned False
    # after skip_pmid_lookup was set
    assert s.new_pmid is None
    assert s.skip_pmid_lookup is True
    assert any(a.startswith("skipped_pmid:") for a in s.actions)


def test_enrich_meeting_abstract_inclusion_flag():
    """With --include-meeting-abstracts-pmid, PMID is taken even from a
    meeting abstract response."""
    s = ArticleState(psir_id="UMV1", doi="10.1/abstract")
    fake = FakeWosClient(by_doi={
        "10.1/abstract": {
            "uid": "WOS:00222",
            "pmid": "888",
            "doc_type": "Meeting Abstract",
        },
    })
    cli_mod.enrich_one(s, fake, skip_meeting_abstracts=False)
    assert s.new_wos == "WOS:00222"
    assert s.new_pmid == "888"


def test_enrich_uid_fallback_when_no_doi():
    """No DOI but existing UT: should call lookup_by_uid."""
    s = ArticleState(psir_id="UMV1", existing_wos="WOS:00333")
    fake = FakeWosClient(by_uid={
        "WOS:00333": {"uid": "WOS:00333", "pmid": "777", "doc_type": "Article"},
    })
    cli_mod.enrich_one(s, fake)
    assert s.new_pmid == "777"
    assert ("uid", "WOS:00333") in fake.calls
    assert not any(call[0] == "doi" for call in fake.calls)


def test_enrich_skips_uid_lookup_for_unsupported_prefix():
    """CABI:, MEDLINE: etc. must not trigger a UID lookup — Starter API
    returns 400 for those databases in the /documents/{uid} endpoint."""
    # Record has a CABI UT (already known from csl) but needs PMID
    s = ArticleState(psir_id="UMV1", doi="10.1/a",
                     existing_wos="CABI:20260138642")
    fake = FakeWosClient()  # will fail the test if called for uid lookup
    cli_mod.enrich_one(s, fake)
    # DOI lookup may be attempted (fine), but uid lookup must NOT be called
    uid_calls = [c for c in fake.calls if c[0] == "uid"]
    assert uid_calls == [], \
        f"UID lookup must be skipped for CABI: prefix, got calls: {uid_calls}"
    assert any("skipped" in n for n in s.notes), \
        f"Expected a skip note, got: {s.notes}"


def test_enrich_skips_uid_lookup_for_medline_prefix():
    """Same behaviour for MEDLINE: prefix."""
    s = ArticleState(psir_id="UMV2", existing_wos="MEDLINE:32832713")
    fake = FakeWosClient()
    cli_mod.enrich_one(s, fake)
    uid_calls = [c for c in fake.calls if c[0] == "uid"]
    assert uid_calls == []
    s = ArticleState(
        psir_id="UMV1", doi="10.1/done",
        existing_wos="WOS:00444", existing_pmid="666",
    )
    fake = FakeWosClient()
    cli_mod.enrich_one(s, fake)
    assert fake.daily_count == 0  # zero API calls
    assert s.new_wos is None
    assert s.new_pmid is None


# --- end-to-end CLI test (via mocking WosStarterClient) ------------------


def test_no_indicator_duplication_in_output():
    """Indicator elements must appear exactly once in the output XML.

    Regression test for the lxml append() move-semantics bug — calling
    out_root.append(article) on an element already belonging to another tree
    caused child elements (ns2:indicator etc.) to appear twice in the output.
    Uses the real ns2: namespace and structure from actual PSIR exports.
    """
    NS_URI = "http://ii.pw.edu.pl/lib"
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<collection xmlns:ns2="{NS_URI}">'
        f'<ns2:article type="article">'
        f'<id>UMVtest001</id>'
        f'<title>Test with indicators</title>'
        f'<doi>10.1234/test</doi>'
        f'<ns2:indicator type="indicator">'
        f'<id>UMVind001</id>'
        f'<name type="term"><systemName>citationsScopus</systemName></name>'
        f'<ns2:indicatordata type="indicatordata"><id>UMVind001d</id><value>0</value></ns2:indicatordata>'
        f'</ns2:indicator>'
        f'<ns2:indicator type="indicator">'
        f'<id>UMVind002</id>'
        f'<name type="term"><systemName>citationsGS</systemName></name>'
        f'<ns2:indicatordata type="indicatordata"><id>UMVind002d</id><value>1</value></ns2:indicatordata>'
        f'</ns2:indicator>'
        f'<ns2:indicator type="indicator">'
        f'<id>UMVind003</id>'
        f'<name type="term"><systemName>citationsWoS</systemName></name>'
        f'<ns2:indicatordata type="indicatordata"><id>UMVind003d</id><value>2</value></ns2:indicatordata>'
        f'</ns2:indicator>'
        f'<ns2:field><key>csl</key>'
        f'<value>{{"id":"WOS:001234567890001","type":"article-journal"}}</value>'
        f'</ns2:field>'
        f'</ns2:article>'
        f'</collection>'
    )

    from psir_enrich.enrich import run_enrichment
    result = run_enrichment(
        xml_input=xml.encode("utf-8"),
        api_key=None,
        input_label="test.xml",
    )

    assert result.n_enriched == 1
    out_root = etree.fromstring(result.output_xml_bytes)
    articles = out_root.findall(f"{{{NS_URI}}}article")
    assert len(articles) == 1

    indicators = articles[0].findall(f"{{{NS_URI}}}indicator")
    assert len(indicators) == 3, \
        f"Expected 3 indicators, got {len(indicators)} — duplication detected"

    ind_ids = [ind.findtext("id") for ind in indicators]
    assert len(ind_ids) == len(set(ind_ids)), \
        f"Duplicate indicator ids: {ind_ids}"

    values = sorted(
        ind.find(f"{{{NS_URI}}}indicatordata").findtext("value")
        for ind in indicators
    )
    assert values == ["0", "1", "2"], f"Indicator values corrupted: {values}"


def test_cli_no_api_runs_clean(tmp_path: Path):
    """CLI in --no-api mode should produce a full-collection XML and audit CSV."""
    sample_xml = Path(__file__).parent / "fixtures" / "mini.xml"

    out_xml = tmp_path / "patch.xml"
    out_csv = tmp_path / "audit.csv"

    rc = cli_module.main([
        "-i", str(sample_xml),
        "-o", str(out_xml),
        "-r", str(out_csv),
        "--no-api",
    ])
    assert rc == 0
    assert out_xml.exists()
    assert out_csv.exists()

    NS_URI = "http://ii.pw.edu.pl/lib"
    tree = etree.parse(str(out_xml))
    root = tree.getroot()
    assert root.tag == "collection"
    articles = root.findall(f"{{{NS_URI}}}article")
    assert len(articles) == 1, \
        f"Only enriched articles should be in output, got {len(articles)}"

    csl_art = next(
        a for a in articles
        if (a.find("id") is not None and a.find("id").text == "UMVcslonly")
    )
    extid_vals = {
        ex.find(".//systemName").text: ex.find("value").text
        for ex in csl_art.findall("extid")
        if ex.find(".//systemName") is not None and ex.find("value") is not None
    }
    assert "WoSId" in extid_vals
    assert extid_vals["WoSId"] == "WOS:002000"

    df = pd.read_csv(out_csv)
    assert len(df) == 2
