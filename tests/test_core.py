"""Unit tests for psir_enrich.core."""

import json
from io import StringIO

import pytest
from lxml import etree

from psir_enrich.core import (
    NS_URI,
    ArticleState,
    is_meeting_abstract,
    norm_doi,
    norm_pmid,
    norm_wos_ut,
    parse_csl_wos,
    parse_existing_extids,
    survey_article,
)


# --- Normalisation --------------------------------------------------------


@pytest.mark.parametrize("inp,exp", [
    ("10.1234/abc", "10.1234/abc"),
    ("https://doi.org/10.1234/abc", "10.1234/abc"),
    ("https://dx.doi.org/10.1234/abc", "10.1234/abc"),
    ("HTTP://DOI.ORG/10.1234/AbC", "10.1234/abc"),
    ("not a doi", None),
    ("", None),
    (None, None),
])
def test_norm_doi(inp, exp):
    assert norm_doi(inp) == exp


@pytest.mark.parametrize("inp,exp", [
    ("WOS:001234567", "WOS:001234567"),
    ("001234567", "WOS:001234567"),
    ("ISI:001234567", "WOS:001234567"),
    ("wos:001234567", "WOS:001234567"),
    ("", None),
    (None, None),
])
def test_norm_wos_ut(inp, exp):
    assert norm_wos_ut(inp) == exp


@pytest.mark.parametrize("inp,exp", [
    ("12345678", "12345678"),
    ("PMID: 12345678", "12345678"),
    (12345678, "12345678"),
    ("none", None),
    ("nan", None),
    ("", None),
    (None, None),
])
def test_norm_pmid(inp, exp):
    assert norm_pmid(inp) == exp


# --- Meeting abstract detection -------------------------------------------


@pytest.mark.parametrize("dt,exp", [
    ("Meeting Abstract", True),
    ("meeting abstract", True),
    ("MEETING ABSTRACT", True),
    ("Meeting Abstract; Book Chapter", True),
    ("Article", False),
    ("Review", False),
    ("Editorial Material", False),
    (None, False),
    ("", False),
])
def test_is_meeting_abstract(dt, exp):
    assert is_meeting_abstract(dt) == exp


# --- ArticleState ---------------------------------------------------------


def test_articlestate_defaults():
    s = ArticleState()
    assert s.needs_wos() is True
    assert s.needs_pmid() is True
    assert s.has_known_wos() is None


def test_articlestate_skip_pmid_when_meeting_abstract():
    s = ArticleState()
    s.skip_pmid_lookup = True
    assert s.needs_pmid() is False  # even though existing_pmid is None


def test_articlestate_existing_wos_means_not_needed():
    s = ArticleState(existing_wos="WOS:001")
    assert s.needs_wos() is False
    assert s.has_known_wos() == "WOS:001"


# --- XML parsing ----------------------------------------------------------


SAMPLE_ARTICLE = f"""<?xml version="1.0" encoding="UTF-8"?>
<collection xmlns:ns2="{NS_URI}">
  <ns2:article type="article">
    <id>UMV1234567890abcdef</id>
    <title>Test Article With WoS And PubMed</title>
    <doi>10.1234/test.001</doi>
    <extid>
      <id>UMVext1</id>
      <idtype>
        <systemName>WoSId</systemName>
      </idtype>
      <value>WOS:001234567</value>
    </extid>
    <extid>
      <id>UMVext2</id>
      <idtype>
        <systemName>PubMedID</systemName>
      </idtype>
      <value>12345678</value>
    </extid>
    <ns2:field>
      <key>csl</key>
      <value>{{"id":"WOS:001234567","type":"article-journal","title":"Test"}}</value>
    </ns2:field>
  </ns2:article>
</collection>
"""


def test_parse_existing_extids():
    root = etree.fromstring(SAMPLE_ARTICLE.encode())
    art = root[0]
    extids = parse_existing_extids(art)
    assert extids == {"WoSId": "WOS:001234567", "PubMedID": "12345678"}


def test_parse_csl_wos():
    root = etree.fromstring(SAMPLE_ARTICLE.encode())
    art = root[0]
    assert parse_csl_wos(art) == "WOS:001234567"


def test_survey_article_full():
    root = etree.fromstring(SAMPLE_ARTICLE.encode())
    art = root[0]
    s = survey_article(art)
    assert s.psir_id == "UMV1234567890abcdef"
    assert s.title == "Test Article With WoS And PubMed"
    assert s.doi == "10.1234/test.001"
    assert s.existing_wos == "WOS:001234567"
    assert s.existing_pmid == "12345678"
    assert s.csl_wos == "WOS:001234567"
    assert s.needs_wos() is False
    assert s.needs_pmid() is False


def test_survey_article_missing_pmid():
    """Article without PubMedID extid should signal needs_pmid()."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<collection xmlns:ns2="{NS_URI}">
  <ns2:article type="article">
    <id>UMVtest</id>
    <title>No PMID yet</title>
    <doi>10.1234/foo</doi>
    <extid>
      <idtype><systemName>WoSId</systemName></idtype>
      <value>WOS:001999</value>
    </extid>
  </ns2:article>
</collection>
"""
    art = etree.fromstring(xml.encode())[0]
    s = survey_article(art)
    assert s.existing_wos == "WOS:001999"
    assert s.existing_pmid is None
    assert s.needs_pmid() is True


def test_survey_article_csl_only():
    """csl has WoS but no extid yet — classic Tier 0 case."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<collection xmlns:ns2="{NS_URI}">
  <ns2:article type="article">
    <id>UMVcsl</id>
    <title>Csl-only WoS</title>
    <doi>10.1234/bar</doi>
    <ns2:field>
      <key>csl</key>
      <value>{{"id":"WOS:001abc","type":"article-journal"}}</value>
    </ns2:field>
  </ns2:article>
</collection>
"""
    art = etree.fromstring(xml.encode())[0]
    s = survey_article(art)
    assert s.existing_wos is None
    assert s.csl_wos == "WOS:001abc"
    assert s.needs_wos() is True
