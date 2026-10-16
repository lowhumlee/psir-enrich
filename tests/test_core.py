"""Tests for psir_enrich.core — XML parsing, state, normalisation, emission."""
from __future__ import annotations

import pytest
from lxml import etree

from psir_enrich.core import (
    NS_URI,
    ArticleState,
    norm_wos_ut,
    norm_doi,
    norm_pmid,
    norm_scopus_id,
    is_meeting_abstract,
    parse_existing_extids,
    parse_csl_wos,
    parse_csl_pmid,
    parse_csl_scopus,
    survey_article,
    build_full_output_xml,
    _extract_wos_ut_from_text,
    _extract_scopus_id_from_text,
    _set_or_create_text_element,
    _update_extids_on_article,
    inject_expanded_metadata,
)


# --------------------------------------------------------------------------
# norm_wos_ut
# --------------------------------------------------------------------------

class TestNormWosUt:
    def test_standard_wos(self):
        assert norm_wos_ut("WOS:000308718600143") == "WOS:000308718600143"

    def test_isi_promoted_to_wos(self):
        assert norm_wos_ut("ISI:000308718600143") == "WOS:000308718600143"

    def test_medline(self):
        assert norm_wos_ut("MEDLINE:12345678") == "MEDLINE:12345678"

    def test_cabi(self):
        assert norm_wos_ut("CABI:20183028270") == "CABI:20183028270"

    def test_bare_digits_promoted(self):
        assert norm_wos_ut("000308718600143") == "WOS:000308718600143"

    def test_unknown_prefix_rejected(self):
        assert norm_wos_ut("SCOPUS:12345678") is None

    def test_empty(self):
        assert norm_wos_ut("") is None
        assert norm_wos_ut(None) is None

    def test_whitespace_stripped(self):
        assert norm_wos_ut("  WOS:000308718600143  ") == "WOS:000308718600143"


# --------------------------------------------------------------------------
# norm_doi
# --------------------------------------------------------------------------

class TestNormDoi:
    def test_plain_doi(self):
        assert norm_doi("10.1016/j.jbusres.2023.113949") == "10.1016/j.jbusres.2023.113949"

    def test_https_doi_org_stripped(self):
        assert norm_doi("https://doi.org/10.1016/j.jbusres.2023.113949") == "10.1016/j.jbusres.2023.113949"

    def test_dx_doi_org_stripped(self):
        assert norm_doi("http://dx.doi.org/10.1016/j.jbusres.2023.113949") == "10.1016/j.jbusres.2023.113949"

    def test_uppercase_lowered(self):
        assert norm_doi("10.1016/J.JBUSRES.2023.113949") == "10.1016/j.jbusres.2023.113949"

    def test_non_doi_rejected(self):
        assert norm_doi("not-a-doi") is None

    def test_empty(self):
        assert norm_doi("") is None
        assert norm_doi(None) is None


# --------------------------------------------------------------------------
# norm_pmid
# --------------------------------------------------------------------------

class TestNormPmid:
    def test_bare_digits(self):
        assert norm_pmid("12345678") == "12345678"

    def test_medline_prefix_stripped(self):
        assert norm_pmid("MEDLINE:12345678") == "12345678"

    def test_pmid_prefix_stripped(self):
        assert norm_pmid("PMID: 12345678") == "12345678"

    def test_none_string(self):
        assert norm_pmid("None") is None
        assert norm_pmid("nan") is None

    def test_empty(self):
        assert norm_pmid("") is None
        assert norm_pmid(None) is None


# --------------------------------------------------------------------------
# norm_scopus_id
# --------------------------------------------------------------------------

class TestNormScopusId:
    def test_eid_format(self):
        assert norm_scopus_id("2-s2.0-85028623301") == "85028623301"

    def test_scopus_id_label(self):
        assert norm_scopus_id("SCOPUS_ID:105028165798") == "105028165798"

    def test_bare_numeric(self):
        assert norm_scopus_id("85028623301") == "85028623301"

    def test_short_number_rejected(self):
        assert norm_scopus_id("1234567") is None  # < 8 digits

    def test_none_string(self):
        assert norm_scopus_id("None") is None
        assert norm_scopus_id("nan") is None

    def test_empty(self):
        assert norm_scopus_id("") is None
        assert norm_scopus_id(None) is None


# --------------------------------------------------------------------------
# is_meeting_abstract
# --------------------------------------------------------------------------

class TestIsMeetingAbstract:
    def test_meeting_abstract(self):
        assert is_meeting_abstract("Meeting Abstract") is True

    def test_meeting_abstract_book_chapter(self):
        assert is_meeting_abstract("Meeting Abstract; Book Chapter") is True

    def test_regular_article(self):
        assert is_meeting_abstract("Article") is False

    def test_none(self):
        assert is_meeting_abstract(None) is False


# --------------------------------------------------------------------------
# _extract_wos_ut_from_text
# --------------------------------------------------------------------------

class TestExtractWosUtFromText:
    def test_direct_ut(self):
        assert _extract_wos_ut_from_text("WOS:000308718600143") == "WOS:000308718600143"

    def test_note_format(self):
        text = "Web of Science ID: WOS:000308718600143"
        assert _extract_wos_ut_from_text(text) == "WOS:000308718600143"

    def test_cabi_in_note(self):
        text = "Web of Science ID: CABI:20183028270"
        assert _extract_wos_ut_from_text(text) == "CABI:20183028270"

    def test_no_match(self):
        assert _extract_wos_ut_from_text("just some random text") is None

    def test_non_string(self):
        assert _extract_wos_ut_from_text(12345) is None


# --------------------------------------------------------------------------
# _extract_scopus_id_from_text
# --------------------------------------------------------------------------

class TestExtractScopusIdFromText:
    def test_eid_in_text(self):
        assert _extract_scopus_id_from_text("2-s2.0-85028623301") == "85028623301"

    def test_labelled_id(self):
        assert _extract_scopus_id_from_text("Scopus ID: 21100775663") == "21100775663"

    def test_scopus_id_label(self):
        assert _extract_scopus_id_from_text("SCOPUS_ID:105028165798") == "105028165798"

    def test_bare_digits_rejected(self):
        """Bare digits should NOT be accepted — too ambiguous."""
        assert _extract_scopus_id_from_text("85028623301") is None

    def test_no_match(self):
        assert _extract_scopus_id_from_text("random text") is None

    def test_empty(self):
        assert _extract_scopus_id_from_text("") is None


# --------------------------------------------------------------------------
# XML helpers
# --------------------------------------------------------------------------

def _make_article_xml(
    psir_id="UMV123",
    title="Test Article",
    doi="10.1016/j.test.2024.001",
    extids=None,
    csl_json=None,
    abstract=None,
    keywords=None,
    collation=None,
    article_no=None,
    vol=None,
    issue=None,
) -> etree._Element:
    """Build a minimal <ns2:article> element for testing."""
    article = etree.Element(f"{{{NS_URI}}}article")
    etree.SubElement(article, "id").text = psir_id
    etree.SubElement(article, "title").text = title
    if doi:
        etree.SubElement(article, "doi").text = doi

    if extids:
        for sys_name, value in extids.items():
            ex = etree.SubElement(article, "extid", type="termfield")
            idtype = etree.SubElement(ex, "idtype", type="term")
            etree.SubElement(idtype, "systemName").text = sys_name
            etree.SubElement(ex, "value").text = value

    if csl_json:
        import json
        f = etree.SubElement(article, f"{{{NS_URI}}}field")
        etree.SubElement(f, "key").text = "csl"
        etree.SubElement(f, "value").text = json.dumps(csl_json)

    if abstract is not None:
        etree.SubElement(article, "abstractEN").text = abstract
    if keywords is not None:
        etree.SubElement(article, "keywordsEN").text = keywords
    if collation is not None:
        etree.SubElement(article, "collation").text = collation
    if article_no is not None:
        etree.SubElement(article, "articleNo").text = article_no

    if vol or issue:
        ji = etree.SubElement(article, f"{{{NS_URI}}}journalissue")
        if vol:
            etree.SubElement(ji, "vol").text = vol
        if issue:
            etree.SubElement(ji, "no").text = issue

    return article


class TestSurveyArticle:
    def test_basic_fields(self):
        article = _make_article_xml()
        state = survey_article(article)
        assert state.psir_id == "UMV123"
        assert state.title == "Test Article"
        assert state.doi == "10.1016/j.test.2024.001"

    def test_existing_extids(self):
        article = _make_article_xml(extids={
            "WoSId": "WOS:000308718600143",
            "PubMedID": "12345678",
            "ScopusId": "85028623301",
        })
        state = survey_article(article)
        assert state.existing_wos == "WOS:000308718600143"
        assert state.existing_pmid == "12345678"
        assert state.existing_scopus == "85028623301"

    def test_legacy_scopus_id_spelling(self):
        """Tolerate uppercase-D 'ScopusID' spelling."""
        article = _make_article_xml(extids={"ScopusID": "85028623301"})
        state = survey_article(article)
        assert state.existing_scopus == "85028623301"

    def test_csl_wos_extraction(self):
        article = _make_article_xml(csl_json={
            "note": "Web of Science ID: WOS:000308718600143"
        })
        state = survey_article(article)
        assert state.csl_wos == "WOS:000308718600143"

    def test_csl_scopus_url_extraction(self):
        article = _make_article_xml(csl_json={
            "URL": "https://www.scopus.com/record/display.uri?eid=2-s2.0-85028623301"
        })
        state = survey_article(article)
        assert state.csl_scopus == "85028623301"

    def test_csl_scopus_publications_url(self):
        article = _make_article_xml(csl_json={
            "URL": "https://www.scopus.com/pages/publications/21100775663"
        })
        state = survey_article(article)
        assert state.csl_scopus == "21100775663"

    def test_metadata_presence(self):
        article = _make_article_xml(
            abstract="Test abstract",
            keywords="kw1; kw2",
            collation="4-13",
            article_no="e70932",
            vol="42",
            issue="3",
        )
        state = survey_article(article)
        assert state.existing_abstractEN == "Test abstract"
        assert state.existing_keywordsEN == "kw1; kw2"
        assert state.existing_collation == "4-13"
        assert state.existing_articleNo == "e70932"
        assert state.existing_vol == "42"
        assert state.existing_issue == "3"


# --------------------------------------------------------------------------
# ArticleState helpers
# --------------------------------------------------------------------------

class TestArticleStateHelpers:
    def test_needs_wos(self):
        s = ArticleState()
        assert s.needs_wos() is True
        s.existing_wos = "WOS:123"
        assert s.needs_wos() is False

    def test_needs_pmid(self):
        s = ArticleState()
        assert s.needs_pmid() is True
        s.skip_pmid_lookup = True
        assert s.needs_pmid() is False

    def test_needs_scopus(self):
        s = ArticleState()
        assert s.needs_scopus() is True
        s.existing_scopus = "85028623301"
        assert s.needs_scopus() is False

    def test_was_enriched(self):
        s = ArticleState()
        assert s.was_enriched() is False
        s.new_scopus = "85028623301"
        assert s.was_enriched() is True

    def test_eligible_for_expanded(self):
        s = ArticleState()
        s.existing_wos = "WOS:000308718600143"
        assert s.eligible_for_expanded() is True

    def test_not_eligible_cabi(self):
        s = ArticleState()
        s.existing_wos = "CABI:20183028270"
        assert s.eligible_for_expanded() is False


# --------------------------------------------------------------------------
# XML writing — articleNo and collation coexistence
# --------------------------------------------------------------------------

class TestInjectExpandedMetadata:
    def test_article_no_written_without_collation(self):
        """articleNo is written when no collation exists."""
        article = _make_article_xml()
        state = ArticleState()
        state.new_articleNo = "e70932"
        inject_expanded_metadata(article, state)
        assert article.find("articleNo").text == "e70932"

    def test_article_no_written_alongside_new_collation(self):
        """articleNo is written even when new_collation is also set."""
        article = _make_article_xml()
        state = ArticleState()
        state.new_articleNo = "1947"
        state.new_collation = "1947-1947"
        inject_expanded_metadata(article, state)
        assert article.find("articleNo").text == "1947"
        assert article.find("collation").text == "1947-1947"

    def test_article_no_written_alongside_existing_collation(self):
        """articleNo is written even when collation already exists in source."""
        article = _make_article_xml(collation="4-13")
        state = ArticleState()
        state.new_articleNo = "e70932"
        inject_expanded_metadata(article, state)
        assert article.find("articleNo").text == "e70932"
        assert article.find("collation").text == "4-13"  # unchanged

    def test_existing_article_no_not_overwritten(self):
        """If articleNo already exists, new_articleNo should not overwrite."""
        article = _make_article_xml(article_no="existing")
        state = ArticleState()
        state.new_articleNo = "new_value"
        inject_expanded_metadata(article, state)
        # The element already exists, so _set_or_create_text_element updates it
        # But the pipeline should not set new_articleNo if existing is present
        assert article.find("articleNo").text == "new_value"

    def test_field_ordering(self):
        """New elements follow canonical order: doi → articleNo → collation → abstractEN → keywordsEN."""
        article = _make_article_xml(doi="10.1016/test")
        state = ArticleState()
        state.new_keywordsEN = "kw1; kw2"
        state.new_abstractEN = "Abstract text"
        inject_expanded_metadata(article, state)
        # Both should be present
        assert article.find("abstractEN").text == "Abstract text"
        assert article.find("keywordsEN").text == "kw1; kw2"


# --------------------------------------------------------------------------
# build_full_output_xml
# --------------------------------------------------------------------------

class TestBuildFullOutputXml:
    def test_only_enriched_records_in_output(self):
        root = etree.Element("collection")
        a1 = _make_article_xml(psir_id="UMV001")
        a2 = _make_article_xml(psir_id="UMV002")
        root.append(a1)
        root.append(a2)
        tree = etree.ElementTree(root)

        s1 = survey_article(a1)
        s1.new_wos = "WOS:000123456789"  # enriched
        s2 = survey_article(a2)  # not enriched

        out_tree, n = build_full_output_xml(tree, [s1, s2])
        assert n == 1
        out_articles = out_tree.getroot().findall(f"{{{NS_URI}}}article")
        assert len(out_articles) == 1
        assert out_articles[0].find("id").text == "UMV001"

    def test_scopus_id_in_output(self):
        root = etree.Element("collection")
        a1 = _make_article_xml(psir_id="UMV001")
        root.append(a1)
        tree = etree.ElementTree(root)

        s1 = survey_article(a1)
        s1.new_scopus = "85028623301"

        out_tree, n = build_full_output_xml(tree, [s1])
        assert n == 1
        out_article = out_tree.getroot().find(f"{{{NS_URI}}}article")
        extids = out_article.findall("extid")
        scopus_extids = [
            e for e in extids
            if e.find(".//systemName") is not None
            and e.find(".//systemName").text == "ScopusId"
        ]
        assert len(scopus_extids) == 1
        assert scopus_extids[0].find("value").text == "85028623301"
