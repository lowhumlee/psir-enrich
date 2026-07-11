"""Clarivate WoS Expanded API client.

Wraps the two operations we need: 
  GET /id/{uid}?databaseId=WOS&optionView=FR   — full record by UT
  GET /id/{uid}?databaseId=MEDLINE&optionView=FR — same for MEDLINE/BCI/etc.

Authentication: HTTP header ``X-ApiKey: <key>`` (separate subscription
from the Starter API; store as WOS_EXPANDED_API_KEY).

Documentation:
  https://developer.clarivate.com/apis/wos
  https://wos-api.clarivate.com/api/wos  (swagger)

Quota is tracked via the response header
  X-REC-AmtPerYear-Remaining
which is surfaced on the client object so callers can log it.

Supported UT prefixes for full-record lookup
(from Clarivate documentation + confirmed behaviour):
  WOS, ISI       – Web of Science Core Collection
  BCI            – BIOSIS Citation Index
  BIOABS         – Biological Abstracts
  BIOSIS         – BIOSIS Previews
  CCC            – Current Contents Connect
  DIIDW          – Derwent Innovations Index
  DRCI           – Data Citation Index
  MEDLINE        – MEDLINE / NLM
  ZOOREC         – Zoological Records
  PPRN           – Preprint Citation Index

  NOT SUPPORTED by Expanded /id endpoint:
  CABI           – only DOI search is possible
  WOK            – all-databases umbrella, not a valid uid prefix
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

import requests

from psir_enrich.core import norm_doi, norm_pmid, norm_wos_ut

# Prefixes that CANNOT be used in the /id/{uid} endpoint even with Expanded.
_EXPANDED_UID_UNSUPPORTED = frozenset({"CABI", "WOK"})


class WosExpandedClient:
    """Minimal client for the Web of Science Expanded API.

    Args:
        api_key:       Expanded API key from Clarivate Developer Portal.
        base_url:      Defaults to the production endpoint.
        min_interval:  Minimum seconds between requests (rate-limit guard).
    """

    DEFAULT_BASE_URL = "https://wos-api.clarivate.com/api/wos"

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        min_interval: float = 0.5,
    ):
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.min_interval = min_interval
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "X-ApiKey": api_key,
            "Accept": "application/json",
            "User-Agent": "psir-enrich-expanded/1.0",
        })
        self.daily_count = 0
        self.errors = 0
        self.quota_remaining: Optional[str] = None   # X-REC-AmtPerYear-Remaining

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call
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

        # Surface quota header while we have the response
        remaining = r.headers.get("X-REC-AmtPerYear-Remaining")
        if remaining:
            self.quota_remaining = remaining

        self.daily_count += 1
        if r.status_code == 200:
            try:
                return r.json()
            except json.JSONDecodeError:
                self.errors += 1
                return {"_error": "invalid JSON in response"}
        if r.status_code == 401:
            self.errors += 1
            return {"_error": "401 Unauthorized — Expanded API key invalid or missing"}
        if r.status_code == 404:
            return {"_error": "404 Not found"}
        if r.status_code == 429:
            self.errors += 1
            return {"_error": "429 Rate limited — slow down"}
        self.errors += 1
        return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}

    @staticmethod
    def _first_rec(payload: dict) -> Optional[dict]:
        """Return the first REC from a full-record response."""
        if not payload or "_error" in payload:
            return None
        # /id/{uid} returns {"Data":{"Records":{"records":{"REC":[...]}}}}
        try:
            recs = (
                payload.get("Data", {})
                       .get("Records", {})
                       .get("records", {})
                       .get("REC", [])
            )
            if recs:
                return recs[0] if isinstance(recs, list) else recs
        except (AttributeError, TypeError):
            pass
        # Fallback: flat record (some edge cases)
        if "static_data" in payload:
            return payload
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup_full_record(self, uid: str) -> dict:
        """Fetch a full WoS record by UT.

        Returns the parsed record dict on success, or ``{"_error": ...}``
        on failure.  The record is the raw ``WosRecord`` object from the
        Expanded API; callers use the ``extract_*`` helpers below.
        """
        s = (uid or "").strip()
        if not s:
            return {"_error": "empty uid"}

        # Ensure prefix present
        if ":" not in s:
            if re.match(r"^\d{8,}$", s):
                s = f"WOS:{s}"
            else:
                return {"_error": f"unrecognised UT format: {s!r}"}

        prefix = s.split(":")[0].upper()
        if prefix in _EXPANDED_UID_UNSUPPORTED:
            return {"_error": f"UT prefix {prefix}: not supported by Expanded /id endpoint"}

        # Determine databaseId from prefix
        db = "WOS"
        if prefix == "MEDLINE":
            db = "MEDLINE"
        elif prefix in ("BCI", "BIOABS", "BIOSIS"):
            db = prefix
        elif prefix in ("CCC", "DIIDW", "DRCI", "ZOOREC", "PPRN"):
            db = prefix

        from urllib.parse import quote
        path_uid = quote(s, safe="")
        payload = self._request(
            f"/id/{path_uid}",
            params={"databaseId": db, "optionView": "FR"},
        )
        if "_error" in payload:
            return payload
        rec = self._first_rec(payload)
        if rec is None:
            return {"_error": "no record in response"}
        return rec

    # ------------------------------------------------------------------
    # Extraction helpers  (all accept the raw WosRecord dict)
    # ------------------------------------------------------------------

    @staticmethod
    def extract_abstract(rec: dict) -> Optional[str]:
        """Return the English abstract text, or None."""
        try:
            ab = (
                rec["static_data"]["fullrecord_metadata"]
                   ["abstracts"]["abstract"]["abstract_text"]
            )
            p = ab.get("p")
            if isinstance(p, list):
                return " ".join(str(x) for x in p if x)
            return str(p) if p else None
        except (KeyError, TypeError):
            return None

    @staticmethod
    def extract_keywords_author(rec: dict) -> Optional[list]:
        """Return author keywords list, or None."""
        try:
            kw = (
                rec["static_data"]["fullrecord_metadata"]["keywords"]["keyword"]
            )
            if isinstance(kw, list):
                return [str(k) for k in kw if k]
            return [str(kw)] if kw else None
        except (KeyError, TypeError):
            return None

    @staticmethod
    def extract_keywords_plus(rec: dict) -> Optional[list]:
        """Return KeyWords Plus list, or None."""
        try:
            kw = rec["static_data"]["item"]["keywords_plus"]["keyword"]
            if isinstance(kw, list):
                return [str(k) for k in kw if k]
            return [str(kw)] if kw else None
        except (KeyError, TypeError):
            return None

    @staticmethod
    def extract_pub_info(rec: dict) -> dict:
        """Return publication info fields: vol, issue, pages, early_access.

        Returns dict with keys (all Optional[str]):
          vol, issue, page_begin, page_end, page_count, article_no,
          early_access_date, early_access_year
        """
        out: dict = {}
        try:
            pi = rec["static_data"]["summary"]["pub_info"]
        except (KeyError, TypeError):
            return out

        def _s(v) -> Optional[str]:
            return str(v).strip() if v not in (None, "", "null") else None

        out["vol"] = _s(pi.get("vol"))

        # WoS may store issue-like information as:
        #   issue
        #   supplement
        #   special_issue
        #
        # PSIR has only <no>, so:
        #   issue wins if present
        #   otherwise use supplement / special issue as fallback
        #
        # Examples:
        #   supplement = "1"                     -> "Suppl 1"
        #   supplement = "1", special_issue="SI" -> "Suppl 1, SI"
        #   special_issue = "SI"                 -> "SI"
        issue = _s(pi.get("issue"))

        raw_supplement = (
            _s(pi.get("supplement"))
            or _s(pi.get("supp"))
        )

        raw_special_issue = (
            _s(pi.get("special_issue"))
            or _s(pi.get("specialIssue"))
            or _s(pi.get("specialissue"))
        )

        def _format_supplement(value) -> Optional[str]:
            s = _s(value)
            if not s:
                return None

            low = s.lower().strip()

            if low.startswith("suppl"):
                rest = s[5:].lstrip(". ").strip()
                return f"Suppl {rest}".strip()

            if low.startswith("supplement"):
                rest = s[10:].lstrip(". ").strip()
                return f"Suppl {rest}".strip()

            return f"Suppl {s}"

        def _format_special_issue(value) -> Optional[str]:
            s = _s(value)
            if not s:
                return None

            low = s.lower().strip()

            # WoS may expose special issue as SI, yes/true, or similar.
            if low in {"si", "special issue", "special_issue", "y", "yes", "true", "1"}:
                return "SI"

            # If WoS gives a more specific label, preserve it.
            return s

        supplement = _format_supplement(raw_supplement)
        special_issue = _format_special_issue(raw_special_issue)

        fallback_issue_parts = []
        if supplement:
            fallback_issue_parts.append(supplement)
        if special_issue:
            fallback_issue_parts.append(special_issue)

        fallback_issue = ", ".join(fallback_issue_parts) if fallback_issue_parts else None

        out["issue"] = issue or fallback_issue
        out["supplement"] = supplement
        out["special_issue"] = special_issue

        out["early_access_date"] = _s(pi.get("early_access_date"))
        out["early_access_year"] = _s(pi.get("early_access_year"))
        page = pi.get("page") or {}
        if isinstance(page, dict):
            out["page_begin"] = _s(page.get("begin"))
            out["page_end"]   = _s(page.get("end"))
            out["page_count"] = _s(page.get("page_count"))
            content = page.get("content")
            if content:
                out["collation"] = _s(content)

        return out

    @staticmethod
    def extract_category_info(rec: dict) -> dict:
        """Return WoS subject categories and research areas.

        Returns dict:
          wos_categories  : list[str]  – ascatype=='traditional' subjects
                                         e.g. ['Medicine, General & Internal']
          research_areas  : list[str]  – ascatype=='extended' subjects
                                         e.g. ['General & Internal Medicine']
          heading         : str        – broad heading ('Science & Technology')
          subheadings     : list[str]  – ['Life Sciences & Biomedicine']
        """
        out: dict = {
            "wos_categories": [],
            "research_areas": [],
            "heading": None,
            "subheadings": [],
        }
        try:
            ci = (
                rec["static_data"]["fullrecord_metadata"]["category_info"]
            )
        except (KeyError, TypeError):
            return out

        # Subjects — split traditional vs extended
        subjects = ci.get("subjects", {}).get("subject", [])
        if isinstance(subjects, dict):
            subjects = [subjects]
        for s in (subjects or []):
            content = str(s.get("content", "")).strip()
            if not content:
                continue
            if s.get("ascatype") == "traditional":
                out["wos_categories"].append(content)
            elif s.get("ascatype") == "extended":
                out["research_areas"].append(content)

        # Heading (broad: 'Science & Technology')
        heading = ci.get("headings", {})
        if isinstance(heading, dict):
            h = heading.get("heading")
            if isinstance(h, list):
                out["heading"] = "; ".join(str(x) for x in h if x)
            elif h:
                out["heading"] = str(h).strip()

        # Subheadings ('Life Sciences & Biomedicine', 'Physical Sciences', …)
        subh = ci.get("subheadings", {}).get("subheading", [])
        if isinstance(subh, str):
            subh = [subh]
        out["subheadings"] = [str(s).strip() for s in (subh or []) if s]

        return out

    @staticmethod
    def extract_wos_editions(rec: dict) -> list:
        """Return list of WoS edition strings, e.g. ['WOS.SCI', 'WOS.SSCI']."""
        try:
            ed = rec["static_data"]["summary"]["EWUID"]["edition"]
            if isinstance(ed, dict):
                ed = [ed]
            return [str(e.get("value", "")).strip() for e in (ed or []) if e.get("value")]
        except (KeyError, TypeError):
            return []
    
    @staticmethod
    def extract_funding(rec: dict) -> dict:
        """Return structured funding data.

        Returns dict:
          fund_text      : str | None
          agencies       : list[str]
          grant_ids      : list[str]
        """
        out: dict = {"fund_text": None, "agencies": [], "grant_ids": []}

        try:
            fa = rec["static_data"]["fullrecord_metadata"].get("fund_ack")
        except (KeyError, AttributeError, TypeError):
            return out

        # WoS Expanded may return fund_ack as None, empty string, list,
        # or another unexpected shape. Only dict is usable here.
        if not isinstance(fa, dict):
            return out

        # ---- Raw funding text ------------------------------------------
        fund_text = fa.get("fund_text")
        p = None

        if isinstance(fund_text, dict):
            p = fund_text.get("p")
        elif isinstance(fund_text, (str, list)):
            p = fund_text

        if isinstance(p, list):
            text = " ".join(str(x).strip() for x in p if x)
            out["fund_text"] = text or None
        elif p:
            out["fund_text"] = str(p).strip() or None

        # ---- Structured grants -----------------------------------------
        grants_block = fa.get("grants")
        if not isinstance(grants_block, dict):
            return out

        grants = grants_block.get("grant")
        if grants is None:
            return out

        if isinstance(grants, dict):
            grants = [grants]
        elif not isinstance(grants, list):
            return out

        for g in grants:
            if not isinstance(g, dict):
                continue

            agency = g.get("grant_agency")
            if agency:
                out["agencies"].append(str(agency).strip())

            # grant_agency_names may be list, dict, None, or malformed
            agency_names = g.get("grant_agency_names") or []
            if isinstance(agency_names, dict):
                agency_names = [agency_names]
            elif not isinstance(agency_names, list):
                agency_names = []

            for an in agency_names:
                if isinstance(an, dict) and an.get("pref") == "Y":
                    name = an.get("content", "")
                    if name:
                        out["agencies"].append(str(name).strip())

            # grant_ids may be dict, string, list, None, or malformed
            gids = g.get("grant_ids") or {}
            ids = []

            if isinstance(gids, dict):
                ids = gids.get("grant_id", [])
            elif isinstance(gids, (str, int, float)):
                ids = [gids]

            if isinstance(ids, (str, int, float)):
                ids = [ids]
            elif not isinstance(ids, list):
                ids = []

            for gid in ids:
                if gid:
                    out["grant_ids"].append(str(gid).strip())

        # Deduplicate, preserve order
        out["agencies"] = list(dict.fromkeys(x for x in out["agencies"] if x))
        out["grant_ids"] = list(dict.fromkeys(x for x in out["grant_ids"] if x))

        return out
    @staticmethod
    def extract_reprint_seq(rec: dict) -> Optional[int]:
        """Return the 1-based sequence number of the reprint (corresponding) author."""
        try:
            names = rec["static_data"]["summary"]["names"]["name"]
            if isinstance(names, dict):
                names = [names]
            for n in names:
                if n.get("reprint") == "Y":
                    return int(n["seq_no"])
        except (KeyError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def extract_identifiers(rec: dict) -> dict:
        """Return additional identifiers from cluster_related block.

        Returns dict of {type_lower: value}, e.g.:
          {"doi": "10.xxx", "pmid": "12345678", "xref_doi": "..."}
        """
        out: dict = {}
        try:
            ids = (
                rec["dynamic_data"]["cluster_related"]
                   ["identifiers"]["identifier"]
            )
            if isinstance(ids, dict):
                ids = [ids]
            for item in ids:
                t = str(item.get("type", "")).lower()
                v = str(item.get("value", "")).strip()
                if t and v:
                    out[t] = v
        except (KeyError, TypeError):
            pass
        return out
