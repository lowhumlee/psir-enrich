"""Scopus API client for psir-enrich."""
from __future__ import annotations
import re
import time
from typing import Optional
import requests

SEARCH_BASE_URL = "https://api.elsevier.com/content/search/scopus"
ABSTRACT_BASE_URL = "https://api.elsevier.com/content/abstract"

class ScopusClient:
    def __init__(self, api_key: str, min_interval: float = 0.35):
        self.api_key = api_key
        self.min_interval = min_interval
        self.last_call = 0.0
        self.daily_count = 0
        self.errors = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _wait(self) -> None:
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

    def _headers(self) -> dict:
        return {"Accept": "application/json", "X-ELS-APIKey": self.api_key}

    @staticmethod
    def _extract_numeric_id(eid_or_str: str) -> Optional[str]:
        if not eid_or_str:
            return None
        match = re.search(r'2-s2\.0-(\d+)', eid_or_str)
        if match:
            return match.group(1)
        match = re.search(r'SCOPUS_ID:(\d+)', eid_or_str, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r'(\d{8,})', eid_or_str)
        return match.group(1) if match else None

    @staticmethod
    def _extract_article_no(starting_page, ending_page, explicit=None) -> Optional[str]:
        """Derive an article number from Scopus page fields.
        Returns an article number only for electronic (single-location)
        articles; returns None for genuine page ranges.
        """
        explicit = str(explicit or "").strip()
        if explicit:
            return explicit
        sp = str(starting_page or "").strip()
        ep = str(ending_page or "").strip()
        if not sp:
            return None
        # Letter-prefixed (e70932, S123) -> article number
        if re.match(r'^[a-zA-Z]', sp):
            return sp
        # begin == end -> article number (e.g. 1947)
        if ep and sp == ep:
            return sp
        # begin with no end -> article number
        if not ep:
            return sp
        return None

    @staticmethod
    def _clean_query_text(text: str, max_len: int = 100) -> str:
        """Strip characters that break Scopus query syntax (* : ( ) " etc.)."""
        cleaned = re.sub(r'[^\w\s\-]', ' ', text or "")
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned[:max_len].strip()

    def _search(self, query: str) -> dict:
        """One Scopus Search API call (COMPLETE view, properly URL-encoded).
        COMPLETE view returns eid, pubmed_id and prism page fields in a
        single request (same quota cost as STANDARD).
        """
        self._wait()
        self.daily_count += 1
        params = {"query": query, "view": "COMPLETE"}
        try:
            resp = requests.get(
                SEARCH_BASE_URL, params=params, headers=self._headers(), timeout=15
            )
        except requests.RequestException as e:
            self.errors += 1
            return {"_error": f"network: {e}"}
        if resp.status_code == 429:
            self.errors += 1
            return {"_error": "Rate limit exceeded (429)"}
        if resp.status_code != 200:
            self.errors += 1
            return {"_error": f"HTTP {resp.status_code}: {resp.text[:100]}"}
        try:
            data = resp.json()
        except ValueError:
            self.errors += 1
            return {"_error": "invalid JSON in Scopus response"}
        entries = data.get("search-results", {}).get("entry", [])
        if not entries:
            return {"_error": "No Scopus results found"}
        entry = entries[0]
        eid = entry.get("eid", "")
        return {
            "scopus_id": self._extract_numeric_id(eid),
            "eid": eid,
            "pmid": entry.get("pubmed_id") or entry.get("prism:pubmed_id"),
            "articleNo": self._extract_article_no(
                entry.get("prism:startingPage"),
                entry.get("prism:endingPage"),
                entry.get("prism:articleNumber"),
            ),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def lookup_by_doi(self, doi: str) -> dict:
        result = self._search(f"DOI({doi})")
        if result.get("_error") == "No Scopus results found":
            result["_error"] = "No Scopus results found for DOI"
        return result

    def lookup_by_title_author(self, title: str, author: str) -> dict:
        clean_title = self._clean_query_text(title, 100)
        if not clean_title:
            return {"_error": "empty title"}
        # Quoted phrase avoids wildcard/operator side effects; the author
        # clause is only appended when an author name is actually present.
        query = f'TITLE("{clean_title}")'
        clean_author = self._clean_query_text((author or "").split(",")[0], 50)
        if clean_author:
            query += f' AND AUTHFIRST("{clean_author}")'
        result = self._search(query)
        if result.get("_error") == "No Scopus results found":
            result["_error"] = "No Scopus results found for Title/Author"
        return result

    def lookup_abstract(self, scopus_id: str) -> dict:
        self._wait()
        self.daily_count += 1
        url = f"{ABSTRACT_BASE_URL}/scopus_id/{scopus_id}"
        try:
            resp = requests.get(
                url, params={"view": "FULL"}, headers=self._headers(), timeout=15
            )
            if resp.status_code != 200:
                self.errors += 1
                return {"_error": f"HTTP {resp.status_code}: {resp.text[:100]}"}
            data = resp.json()
            response_data = data.get("abstracts-retrieval-response", {})
            coredata = response_data.get("coredata", {})
            abstract = coredata.get("dc:description")
            keywords_list = []
            authkeywords = response_data.get("authkeywords", {})
            if isinstance(authkeywords, dict):
                kws = authkeywords.get("author-keyword", [])
                if isinstance(kws, list):
                    keywords_list = [kw.get("$", "") for kw in kws if kw.get("$")]
                elif isinstance(kws, dict):
                    keywords_list = [kws.get("$", "")]
            pmid = coredata.get("pubmed_id") or coredata.get("prism:pubmed_id")
            # Article number — coredata prism fields first
            article_no = self._extract_article_no(
                coredata.get("prism:startingPage"),
                coredata.get("prism:endingPage"),
                coredata.get("prism:articleNumber"),
            )
            # Fallback: item.bibrecord.head.source.volisspag.pagerange
            if not article_no:
                try:
                    pagerange = (
                        response_data.get("item", {})
                        .get("bibrecord", {})
                        .get("head", {})
                        .get("source", {})
                        .get("volisspag", {})
                        .get("pagerange", {})
                    )
                    if isinstance(pagerange, dict):
                        article_no = self._extract_article_no(
                            pagerange.get("first"),
                            pagerange.get("last"),
                        )
                except (TypeError, AttributeError):
                    pass
            return {
                "abstract": abstract,
                "keywords": "; ".join(keywords_list) if keywords_list else None,
                "pmid": pmid,
                "articleNo": article_no,
            }
        except Exception as e:
            self.errors += 1
            return {"_error": str(e)}
