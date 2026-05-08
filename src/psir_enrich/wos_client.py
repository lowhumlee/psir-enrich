"""Clarivate WoS Starter API client.

Minimal client covering the two endpoints we use:
  GET /documents?q=DO=<doi>     — DOI lookup, returns first hit
  GET /documents/{uid}          — direct UID lookup

Authentication: HTTP header `X-ApiKey: <key>`.
Documentation: https://developer.clarivate.com/apis/wos-starter
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

import requests

from psir_enrich.core import norm_doi, norm_pmid, norm_wos_ut


class WosStarterClient:
    """Tiny client for the Web of Science Starter API.

    Args:
        api_key: API key from Clarivate Developer Portal.
        base_url: Defaults to the documented production URL.
        min_interval: Minimum seconds between requests (rate limit).
    """

    DEFAULT_BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1"

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        min_interval: float = 0.25,
    ):
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.min_interval = min_interval
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "X-ApiKey": api_key,
            "Accept": "application/json",
            "User-Agent": "psir-enrich/0.3.1",
        })
        self.daily_count = 0
        self.errors = 0

    # -- Internal --------------------------------------------------------

    def _pace(self):
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def _request(self, path: str, params: Optional[dict] = None) -> dict:
        self._pace()
        url = f"{self.base_url}{path}"
        try:
            r = self.session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            self.errors += 1
            return {"_error": f"network: {e}"}
        self.daily_count += 1
        if r.status_code == 200:
            try:
                return r.json()
            except json.JSONDecodeError:
                self.errors += 1
                return {"_error": "invalid JSON in response"}
        if r.status_code == 401:
            self.errors += 1
            return {"_error": "401 Unauthorized — API key invalid or missing"}
        if r.status_code == 404:
            return {"_error": "404 Not found"}
        if r.status_code == 429:
            self.errors += 1
            return {"_error": "429 Rate limited — slow down or upgrade plan"}
        self.errors += 1
        return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}

    @staticmethod
    def _extract_first_doc(payload: dict) -> Optional[dict]:
        """Return the first hit from a /documents query, or the doc itself
        if it's a single-document response."""
        if not payload or "_error" in payload:
            return None
        # /documents/{uid} — direct doc
        if "uid" in payload or "UID" in payload:
            return payload
        # /documents?q=... — wrapped in 'hits' or 'data'
        for key in ("hits", "data", "Records"):
            arr = payload.get(key)
            if isinstance(arr, list) and arr:
                return arr[0]
        return None

    @staticmethod
    def _ids_from_doc(doc: dict) -> dict:
        """Extract UID, DOI, PMID, and Document Type from a Starter doc."""
        if not isinstance(doc, dict):
            return {}
        out = {}
        uid = doc.get("uid") or doc.get("UID")
        if uid:
            out["uid"] = norm_wos_ut(uid)
        ids = doc.get("identifiers") or {}
        if isinstance(ids, dict):
            if ids.get("doi"):
                out["doi"] = norm_doi(ids["doi"])
            if ids.get("pmid"):
                out["pmid"] = norm_pmid(ids["pmid"])
        # Document type lives under 'types' (may be a list) or 'type'
        types = doc.get("types") or doc.get("type") or doc.get("documentType")
        if isinstance(types, list) and types:
            out["doc_type"] = str(types[0])
        elif isinstance(types, str):
            out["doc_type"] = types
        return out

    # -- Public API ------------------------------------------------------

    def lookup_by_doi(self, doi: str) -> dict:
        """Search for a document by DOI. Returns dict with keys:
        uid, doi, pmid, doc_type — or _error."""
        payload = self._request(
            "/documents",
            params={"q": f"DO={doi}", "db": "WOS", "limit": 1},
        )
        if "_error" in payload:
            return payload
        doc = self._extract_first_doc(payload)
        if not doc:
            return {"_error": "no hits"}
        return self._ids_from_doc(doc)

    def lookup_by_uid(self, uid: str) -> dict:
        """Fetch a document by its WoS accession number.

        Clarivate's /documents/{uid} endpoint requires the full <DB>:<id>
        form (e.g. 'WOS:001691554700119'), not the bare accession number.
        We re-add the prefix if the caller stripped it, then URL-encode
        the colon so it's not misread as a port separator.
        """
        s = (uid or "").strip()
        if not s:
            return {"_error": "empty uid"}
        # Normalise to canonical "WOS:<id>" — accept ISI:, wos:, or bare.
        s = re.sub(r"^(WOS:|ISI:|wos:|isi:)", "", s, flags=re.IGNORECASE)
        full_uid = f"WOS:{s}"
        # urllib.parse.quote with safe='' encodes ':' as %3A
        from urllib.parse import quote
        path_uid = quote(full_uid, safe="")
        payload = self._request(f"/documents/{path_uid}")
        if "_error" in payload:
            return payload
        doc = self._extract_first_doc(payload)
        if not doc:
            return {"_error": "no hits"}
        return self._ids_from_doc(doc)
