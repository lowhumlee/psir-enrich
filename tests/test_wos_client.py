"""Tests for the Clarivate WoS Starter API client URL construction.

These tests do NOT make real API calls. They verify our requests have the
right shape so we don't regress on issues like the 400 Invalid syntax bug
that was fixed in v0.2.1.
"""

from unittest.mock import MagicMock, patch

import pytest

from psir_enrich.wos_client import WosStarterClient


@pytest.fixture
def client():
    return WosStarterClient(api_key="dummy", min_interval=0.0)


def test_lookup_by_uid_keeps_wos_prefix(client):
    """The /documents/{uid} path must contain the full URL-encoded UT."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uid": "WOS:001234", "identifiers": {}},
        )
        client.lookup_by_uid("WOS:001234")

    called_url = mock_get.call_args[0][0]
    assert "WOS%3A001234" in called_url, \
        f"Expected URL-encoded 'WOS:' in path, got: {called_url}"
    assert "/documents/001234" not in called_url


def test_lookup_by_uid_preserves_medline_prefix(client):
    """MEDLINE: prefix must be passed verbatim — not rewritten to WOS:."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uid": "MEDLINE:32832713", "identifiers": {}},
        )
        client.lookup_by_uid("MEDLINE:32832713")

    called_url = mock_get.call_args[0][0]
    assert "MEDLINE%3A32832713" in called_url, \
        f"Expected URL-encoded 'MEDLINE:' in path, got: {called_url}"


def test_lookup_by_uid_preserves_cabi_prefix(client):
    """CABI: prefix must be passed verbatim."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uid": "CABI:20250175695", "identifiers": {}},
        )
        client.lookup_by_uid("CABI:20250175695")

    called_url = mock_get.call_args[0][0]
    assert "CABI%3A20250175695" in called_url


def test_lookup_by_uid_re_adds_wos_prefix_for_bare_numeric(client):
    """Bare numeric string gets WOS: prefix added."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uid": "WOS:001234", "identifiers": {}},
        )
        client.lookup_by_uid("001234567890123")

    called_url = mock_get.call_args[0][0]
    assert "WOS%3A001234567890123" in called_url


def test_lookup_by_uid_handles_empty(client):
    """Empty/None uid should return an error, not crash."""
    result = client.lookup_by_uid("")
    assert "_error" in result


def test_lookup_by_doi_uses_query_param(client):
    """DOI lookup must use ?q=DO=<doi> not a path segment."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"hits": []},
        )

        with patch.object(client, "_lookup_pubmed_by_doi") as mock_pubmed:
            mock_pubmed.return_value = {"_error": "PubMed: no hits"}
            client.lookup_by_doi("10.1234/test")

    # First Clarivate call must still be WOS.
    first_call = mock_get.call_args_list[0]
    first_params = first_call.kwargs.get("params") or {}

    assert first_params.get("q") == "DO=10.1234/test"
    assert first_params.get("db") == "WOS"

    first_url = first_call[0][0]
    assert first_url.endswith("/documents")

    # Second Clarivate call is now the intended MEDLINE fallback.
    second_call = mock_get.call_args_list[1]
    second_params = second_call.kwargs.get("params") or {}

    assert second_params.get("q") == "DO=10.1234/test"
    assert second_params.get("db") == "MEDLINE"

    second_url = second_call[0][0]
    assert second_url.endswith("/documents")

def test_lookup_by_doi_pubmed_fallback_sets_medline_uid(client):
    """If Clarivate has no DOI hit, PubMed fallback should set PMID and MEDLINE UID."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"hits": []},
        )

        with patch("psir_enrich.wos_client.requests.get") as mock_pubmed_get:
            mock_pubmed_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "esearchresult": {
                        "idlist": ["42392011"]
                    }
                },
            )

            result = client.lookup_by_doi("10.1016/j.vaccine.2026.128839")

    assert result["pmid"] == "42392011"
    assert result["uid"] == "MEDLINE:42392011"
    assert result["source_db"] == "NCBI_PUBMED"
    
def test_lookup_by_uid_400_error_surfaces(client):
    """The exact error from your Streamlit log should be propagated cleanly."""
    error_body = (
        '{"error":{"status":400,'
        '"title":"Invalid syntax for the request",'
        '"details":"The value \'001691554700119\' is not valid for the '
        'selected filter \'uid\'. Required pattern is: <DB>:<id>"}}'
    )
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(status_code=400, text=error_body)
        result = client.lookup_by_uid("WOS:001691554700119")
    # The fix should prevent this 400 in real life, but if it ever comes
    # back, the error must be visible in the result
    assert "_error" in result
    assert "400" in result["_error"]
