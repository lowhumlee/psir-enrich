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
            "User-Agent": "psir-enrich/0.3.3",
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
    """Search for a document by DOI.

    Lookup order:
      1. WoS Core Collection
      2. MEDLINE / PubMed via WoS Starter, if available

    Returns dict with keys:
      uid, doi, pmid, doc_type, source_db — or _error.
    """
    clean_doi = norm_doi(doi)
    if not clean_doi:
        return {"_error": "invalid DOI"}

    errors = []

    for db in ("WOS", "MEDLINE"):
        payload = self._request(
            "/documents",
            params={"q": f"DO={clean_doi}", "db": db, "limit": 1},
        )

        if "_error" in payload:
            errors.append(f"{db}: {payload['_error']}")
            continue

        doc = self._extract_first_doc(payload)
        if not doc:
            errors.append(f"{db}: no hits")
            continue

        out = self._ids_from_doc(doc)
        out["source_db"] = db

        # If Clarivate MEDLINE returns PMID but no UID, keep the PMID.
        if out.get("pmid") or out.get("uid"):
            return out

        errors.append(f"{db}: hit but no usable identifiers")

    return {"_error": " | ".join(errors) if errors else "no hits"}

    def lookup_by_uid(self, uid: str) -> dict:
        """Fetch a document by its WoS UT (any collection prefix).

        Clarivate's /documents/{uid} endpoint requires the full <DB>:<id>
        form, URL-encoded. Works for any valid WoS UT prefix — WOS:, MEDLINE:,
        CABI:, BCI:, ZOOREC:, etc. — not just the Core Collection.
        """
        s = (uid or "").strip()
        if not s:
            return {"_error": "empty uid"}
        # Ensure the prefix is present — bare numeric strings get WOS:
        if ":" not in s:
            if re.match(r"^\d{8,}$", s):
                s = f"WOS:{s}"
            else:
                return {"_error": f"unrecognised UT format: {s!r}"}
        # URL-encode the colon so it isn't misread as a port separator
        from urllib.parse import quote
        path_uid = quote(s, safe="")
        payload = self._request(f"/documents/{path_uid}")
        if "_error" in payload:
            return payload
        doc = self._extract_first_doc(payload)
        if not doc:
            return {"_error": "no hits"}
        return self._ids_from_doc(doc)
