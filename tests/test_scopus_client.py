"""Tests for psir_enrich.scopus_client — Scopus API client."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from psir_enrich.scopus_client import ScopusClient


class TestExtractNumericId:
    def test_eid_format(self):
        assert ScopusClient._extract_numeric_id("2-s2.0-85028623301") == "85028623301"

    def test_scopus_id_label(self):
        assert ScopusClient._extract_numeric_id("SCOPUS_ID:105028165798") == "105028165798"

    def test_bare_digits(self):
        assert ScopusClient._extract_numeric_id("85028623301") == "85028623301"

    def test_empty(self):
        assert ScopusClient._extract_numeric_id("") is None
        assert ScopusClient._extract_numeric_id(None) is None


class TestExtractArticleNo:
    def test_explicit_article_number(self):
        assert ScopusClient._extract_article_no(None, None, "e70932") == "e70932"

    def test_letter_prefixed_starting_page(self):
        assert ScopusClient._extract_article_no("e70932", "e70932") == "e70932"

    def test_single_page_is_article_no(self):
        assert ScopusClient._extract_article_no("1947", "1947") == "1947"

    def test_starting_page_no_ending(self):
        assert ScopusClient._extract_article_no("1947", None) == "1947"

    def test_genuine_page_range_returns_none(self):
        assert ScopusClient._extract_article_no("4", "13") is None

    def test_empty_returns_none(self):
        assert ScopusClient._extract_article_no(None, None) is None
        assert ScopusClient._extract_article_no("", "") is None


class TestCleanQueryText:
    def test_special_chars_removed(self):
        result = ScopusClient._clean_query_text("Test: (article) *with* special chars")
        assert ":" not in result
        assert "(" not in result
        assert ")" not in result
        assert "*" not in result

    def test_max_length(self):
        long_text = "a" * 200
        result = ScopusClient._clean_query_text(long_text, max_len=100)
        assert len(result) <= 100

    def test_whitespace_normalised(self):
        result = ScopusClient._clean_query_text("multiple   spaces   here")
        assert "   " not in result


class TestSearchResponse:
    @patch("psir_enrich.scopus_client.requests.get")
    def test_successful_search(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "search-results": {
                "entry": [{
                    "eid": "2-s2.0-85028623301",
                    "pubmed_id": "12345678",
                    "prism:startingPage": "e70932",
                    "prism:endingPage": "e70932",
                }]
            }
        }
        mock_get.return_value = mock_resp

        client = ScopusClient(api_key="test_key")
        result = client.lookup_by_doi("10.1016/j.test.2024.001")

        assert result["scopus_id"] == "85028623301"
        assert result["pmid"] == "12345678"
        assert result["articleNo"] == "e70932"
        assert client.daily_count == 1

    @patch("psir_enrich.scopus_client.requests.get")
    def test_no_results(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"search-results": {"entry": []}}
        mock_get.return_value = mock_resp

        client = ScopusClient(api_key="test_key")
        result = client.lookup_by_doi("10.1016/nonexistent")

        assert "_error" in result
        assert "No Scopus results found" in result["_error"]

    @patch("psir_enrich.scopus_client.requests.get")
    def test_rate_limit_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        client = ScopusClient(api_key="test_key")
        result = client.lookup_by_doi("10.1016/j.test.2024.001")

        assert "_error" in result
        assert "Rate limit" in result["_error"]
        assert client.errors == 1

    @patch("psir_enrich.scopus_client.requests.get")
    def test_http_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp

        client = ScopusClient(api_key="bad_key")
        result = client.lookup_by_doi("10.1016/j.test.2024.001")

        assert "_error" in result
        assert "401" in result["_error"]


class TestLookupAbstract:
    @patch("psir_enrich.scopus_client.requests.get")
    def test_successful_abstract_retrieval(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "abstracts-retrieval-response": {
                "coredata": {
                    "dc:description": "This is the abstract.",
                    "pubmed_id": "12345678",
                    "prism:startingPage": "e70932",
                    "prism:endingPage": "e70932",
                },
                "authkeywords": {
                    "author-keyword": [
                        {"$": "keyword1"},
                        {"$": "keyword2"},
                    ]
                },
            }
        }
        mock_get.return_value = mock_resp

        client = ScopusClient(api_key="test_key")
        result = client.lookup_abstract("85028623301")

        assert result["abstract"] == "This is the abstract."
        assert result["keywords"] == "keyword1; keyword2"
        assert result["pmid"] == "12345678"
        assert result["articleNo"] == "e70932"

    @patch("psir_enrich.scopus_client.requests.get")
    def test_abstract_with_pagerange_fallback(self, mock_get):
        """When prism fields are absent, fall back to bibrecord pagerange."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "abstracts-retrieval-response": {
                "coredata": {
                    "dc:description": "Abstract text",
                },
                "item": {
                    "bibrecord": {
                        "head": {
                            "source": {
                                "volisspag": {
                                    "pagerange": {
                                        "first": "1947",
                                        "last": "1947",
                                    }
                                }
                            }
                        }
                    }
                },
            }
        }
        mock_get.return_value = mock_resp

        client = ScopusClient(api_key="test_key")
        result = client.lookup_abstract("85028623301")

        assert result["articleNo"] == "1947"

    @patch("psir_enrich.scopus_client.requests.get")
    def test_abstract_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not found"
        mock_get.return_value = mock_resp

        client = ScopusClient(api_key="test_key")
        result = client.lookup_abstract("99999999999")

        assert "_error" in result
        assert "404" in result["_error"]
