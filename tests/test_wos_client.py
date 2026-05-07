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
    """The /documents/{uid} path must contain the full 'WOS:...' form.
    Clarivate rejects bare accession numbers with HTTP 400."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uid": "WOS:001234", "identifiers": {}},
        )
        client.lookup_by_uid("WOS:001234")

    # Inspect the URL that was actually requested
    called_url = mock_get.call_args[0][0]
    # The colon must be URL-encoded as %3A so it isn't read as a port sep
    assert "WOS%3A001234" in called_url, \
        f"Expected URL-encoded 'WOS:' in path, got: {called_url}"
    # And the bare form must NOT appear as a path segment
    assert "/documents/001234" not in called_url, \
        f"Bare accession in path will return HTTP 400, got: {called_url}"


def test_lookup_by_uid_re_adds_prefix_when_stripped(client):
    """Caller passes bare '001234' — we should re-add WOS:."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uid": "WOS:001234", "identifiers": {}},
        )
        client.lookup_by_uid("001234")

    called_url = mock_get.call_args[0][0]
    assert "WOS%3A001234" in called_url


def test_lookup_by_uid_normalises_isi_prefix(client):
    """ISI: is the legacy prefix — we should normalise to WOS:."""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"uid": "WOS:001234", "identifiers": {}},
        )
        client.lookup_by_uid("ISI:001234")

    called_url = mock_get.call_args[0][0]
    assert "WOS%3A001234" in called_url
    assert "ISI" not in called_url


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
        client.lookup_by_doi("10.1234/test")

    # The DOI must be in params, not in the URL path
    call = mock_get.call_args
    params = call.kwargs.get("params") or {}
    assert params.get("q") == "DO=10.1234/test"
    assert params.get("db") == "WOS"
    # Path should be the documents collection, not a specific document
    called_url = call[0][0]
    assert called_url.endswith("/documents")


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
