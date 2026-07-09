"""Clarivate WoS Starter API client.

Minimal client covering:
  GET /documents?q=DO=<doi>     — DOI lookup, returns first hit
  GET /documents/{uid}          — direct UID lookup

If Clarivate does not find a DOI in WoS Core / MEDLINE, the client falls
back to NCBI PubMed ESearch so PubMed-only / MEDLINE records can still get
PubMedID and a MEDLINE:<PMID> identifier for the PSIR WoSId field.

Authentication:
  Clarivate: HTTP header X-ApiKey
  NCBI: no key needed for low-volume use
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests

from psir_enrich.core import norm_doi, norm_pmid, norm_wos_ut


class WosStarterClient:
    """Tiny client for the Web of Science Starter API.

    Args:
        api_key: Clarivate API key.
        base_url: Defaults to the documented production URL.
        min_interval: Minimum seconds between Clarivate requests.
        pubmed_email: Optional email sent to NCBI E-utilities.
            If omitted, NCBI_EMAIL or ENTREZ_EMAIL env var is used if present.
    """

    DEFAULT_BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1"
    NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        min_interval: float = 0.25,
        pubmed_email: Optional[str] = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.min_interval = min_interval
        self._last_call = 0.0

        self.session = requests.Session()
        self.session.headers.update({
            "X-ApiKey": api_key,
            "Accept": "application/json",
            "User-Agent": "psir-enrich/0.4.0",
        })

        self.pubmed_email = (
            pubmed_email
            or os.environ.get("NCBI_EMAIL")
            or os.environ.get("ENTREZ_EMAIL")
        )

        self.daily_count = 0
        self.errors = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        """Return first hit from /documents query, or the doc itself."""
        if not payload or "_error" in payload:
            return None

        if "uid" in payload or "UID" in payload:
            return payload

        for key in ("hits", "data", "Records"):
            arr = payload.get(key)
            if isinstance(arr, list) and arr:
                return arr[0]

        return None

    @staticmethod
    def _ids_from_doc(doc: dict) -> dict:
        """Extract UID, DOI, PMID, and document type from a Starter doc."""
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

        types = doc.get("types") or doc.get("type") or doc.get("documentType")
        if isinstance(types, list) and types:
            out["doc_type"] = str(types[0])
        elif isinstance(types, str):
            out["doc_type"] = types

        return out

    def _lookup_pubmed_by_doi(self, doi: str) -> dict:
        """Fallback DOI -> PMID lookup using NCBI PubMed ESearch.

        Returns:
          {
            "uid": "MEDLINE:42392011",
            "pmid": "42392011",
            "doi": "...",
            "source_db": "NCBI_PUBMED",
            "doc_type": "PubMed/MEDLINE"
          }

        or:
          {"_error": "..."}
        """
        clean_doi = norm_doi(doi)
        if not clean_doi:
            return {"_error": "PubMed: invalid DOI"}

        params = {
            "db": "pubmed",
            "retmode": "json",
            "retmax": 2,
            "term": f"{clean_doi}[AID]",
            "tool": "psir-enrich",
        }

        if self.pubmed_email:
            params["email"] = self.pubmed_email

        try:
            r = requests.get(self.NCBI_ESEARCH_URL, params=params, timeout=30)
        except requests.RequestException as e:
            self.errors += 1
            return {"_error": f"PubMed network: {e}"}

        self.daily_count += 1

        if r.status_code != 200:
            self.errors += 1
            return {"_error": f"PubMed HTTP {r.status_code}: {r.text[:200]}"}

        try:
            payload = r.json()
        except json.JSONDecodeError:
            self.errors += 1
            return {"_error": "PubMed invalid JSON in response"}

        ids = (payload.get("esearchresult") or {}).get("idlist") or []
        ids = [norm_pmid(x) for x in ids if norm_pmid(x)]

        if not ids:
            return {"_error": "PubMed: no hits"}

        if len(ids) > 1:
            return {"_error": f"PubMed: ambiguous DOI match ({', '.join(ids)})"}

        pmid = ids[0]

        return {
            "uid": norm_wos_ut(f"MEDLINE:{pmid}"),
            "pmid": pmid,
            "doi": clean_doi,
            "source_db": "NCBI_PUBMED",
            "doc_type": "PubMed/MEDLINE",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup_by_doi(self, doi: str) -> dict:
        """Search for a document by DOI.

        Lookup order:
          1. Clarivate WoS Core Collection
          2. Clarivate MEDLINE, if exposed by Starter API
          3. NCBI PubMed DOI fallback

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

            # Some MEDLINE responses may expose PMID but not UID.
            # In that case synthesize MEDLINE:<PMID>.
            if not out.get("uid") and out.get("pmid") and db == "MEDLINE":
                out["uid"] = norm_wos_ut(f"MEDLINE:{out['pmid']}")

            if out.get("uid") or out.get("pmid"):
                return out

            errors.append(f"{db}: hit but no usable identifiers")

        pubmed = self._lookup_pubmed_by_doi(clean_doi)
        if "_error" not in pubmed:
            return pubmed

        errors.append(pubmed["_error"])
        return {"_error": " | ".join(errors) if errors else "no hits"}

    def lookup_by_uid(self, uid: str) -> dict:
        """Fetch a document by its WoS UT."""
        s = (uid or "").strip()
        if not s:
            return {"_error": "empty uid"}

        if ":" not in s:
            if re.match(r"^\d{8,}$", s):
                s = f"WOS:{s}"
            else:
                return {"_error": f"unrecognised UT format: {s!r}"}

        from urllib.parse import quote
        path_uid = quote(s, safe="")

        payload = self._request(f"/documents/{path_uid}")

        if "_error" in payload:
            return payload

        doc = self._extract_first_doc(payload)
        if not doc:
            return {"_error": "no hits"}

        return self._ids_from_doc(doc)
