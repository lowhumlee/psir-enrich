"""Reusable enrichment pipeline.

Both the CLI and the Streamlit app call run_enrichment(). The output XML
is the full input collection with extids added/updated in-place — suitable
for direct re-import into PSIR's XML import dialog.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from lxml import etree

from psir_enrich.core import (
    NS_URI,
    ArticleState,
    build_full_output_xml,
    is_meeting_abstract,
    survey_article,
)
from psir_enrich.wos_client import WosStarterClient


ProgressCallback = Callable[[int, int, ArticleState], None]


def enrich_one(
    state: ArticleState,
    client: Optional[WosStarterClient],
    skip_meeting_abstracts: bool = True,
) -> None:
    """Run the full enrichment ladder on one ArticleState in place.

    Tier 0: csl-WoS promotion (free, no API).
    Tier 1: Clarivate /documents?q=DO=<doi> (DOI lookup).
    Tier 2: Clarivate /documents/{uid} (UID lookup, PMID fallback).
    """
    # Tier 0 — csl-WoS
    if state.needs_wos() and state.csl_wos:
        state.new_wos = state.csl_wos
        state.actions.append("csl_wos_id")

    if client is None:
        if state.needs_wos() or state.needs_pmid():
            state.notes.append("api skipped (no client)")
        return

    if not state.needs_wos() and not state.needs_pmid():
        return

    # Tier 1 — DOI lookup
    if state.doi and (state.needs_wos() or state.needs_pmid()):
        result = client.lookup_by_doi(state.doi)
        state.api_calls += 1
        if "_error" in result:
            state.notes.append(f"DOI lookup: {result['_error']}")
        else:
            dt = result.get("doc_type")
            if dt:
                state.api_doc_type = dt
                if skip_meeting_abstracts and is_meeting_abstract(dt):
                    state.skip_pmid_lookup = True
                    state.actions.append(f"skipped_pmid:{dt}")
            if state.needs_wos() and result.get("uid"):
                state.new_wos = result["uid"]
                state.actions.append("api_doi_lookup_wos")
            if state.needs_pmid() and result.get("pmid"):
                state.new_pmid = result["pmid"]
                state.actions.append("api_doi_lookup_pmid")

    # Tier 2 — UID lookup
    known_uid = state.has_known_wos()
    if known_uid and state.needs_pmid():
        result = client.lookup_by_uid(known_uid)
        state.api_calls += 1
        if "_error" in result:
            state.notes.append(f"UID lookup: {result['_error']}")
        else:
            dt = result.get("doc_type")
            if dt and not state.api_doc_type:
                state.api_doc_type = dt
                if skip_meeting_abstracts and is_meeting_abstract(dt):
                    state.skip_pmid_lookup = True
                    state.actions.append(f"skipped_pmid:{dt}")
                    return
            if state.needs_pmid() and result.get("pmid"):
                state.new_pmid = result["pmid"]
                state.actions.append("api_uid_lookup_pmid")


@dataclass
class EnrichmentResult:
    states: list
    output_xml_bytes: bytes       # full collection XML, ready for PSIR import
    audit_df: pd.DataFrame
    n_articles: int
    n_enriched: int
    n_api_calls: int
    n_api_errors: int


def run_enrichment(
    xml_input,
    *,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    pmid_idtype_uuid: Optional[str] = None,   # kept for API compat; now auto-detected
    skip_meeting_abstracts: bool = True,
    rate_limit: float = 0.25,
    owner: str = "system",
    input_label: str = "input.xml",
    progress_cb: Optional[ProgressCallback] = None,
) -> EnrichmentResult:
    """Run the full enrichment pipeline on PSIR XML.

    Parameters
    ----------
    xml_input : path-like, bytes, or file-like
        The PSIR XML export — path, bytes blob, or a Streamlit UploadedFile.
    api_key : str, optional
        Clarivate WoS Starter API key. If None, runs csl-only.
    api_base : str, optional
        Override Clarivate base URL.
    pmid_idtype_uuid : str, optional
        Deprecated — PubMedID UUID is now read from the input XML itself.
        Kept for backwards compatibility; ignored if the UUID is found in data.
    skip_meeting_abstracts : bool
        Skip PubMed lookup for meeting abstracts (default True).
    rate_limit : float
        Min seconds between API calls.
    owner : str
        Ignored — kept for API compatibility.
    input_label : str
        Label for progress messages.
    progress_cb : callable, optional
        Called as progress_cb(i, total, state) after each article.

    Returns
    -------
    EnrichmentResult
    """
    # Parse input
    parser = etree.XMLParser(remove_blank_text=False)
    if hasattr(xml_input, "read"):
        input_tree = etree.parse(xml_input, parser)
    elif isinstance(xml_input, (bytes, bytearray)):
        input_tree = etree.parse(BytesIO(xml_input), parser)
    else:
        input_tree = etree.parse(str(xml_input), parser)

    root = input_tree.getroot()
    articles = root.findall(f"{{{NS_URI}}}article")
    states = [survey_article(a) for a in articles]

    # Build API client
    client: Optional[WosStarterClient] = None
    if api_key:
        client = WosStarterClient(
            api_key=api_key,
            base_url=api_base,
            min_interval=rate_limit,
        )

    # Enrich
    total = len(states)
    for i, s in enumerate(states):
        enrich_one(s, client, skip_meeting_abstracts=skip_meeting_abstracts)
        if progress_cb is not None:
            progress_cb(i + 1, total, s)

    # Build full output XML
    out_tree, n_enriched = build_full_output_xml(
        input_tree, states, input_label
    )
    buf = BytesIO()
    out_tree.write(
        buf,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    output_bytes = buf.getvalue()

    # Build audit dataframe
    rows = []
    for s in states:
        rows.append({
            "psir_id": s.psir_id,
            "title": s.title[:120],
            "doi": s.doi or "",
            "existing_wos": s.existing_wos or "",
            "existing_pmid": s.existing_pmid or "",
            "csl_wos": s.csl_wos or "",
            "new_wos": s.new_wos or "",
            "new_pmid": s.new_pmid or "",
            "api_doc_type": s.api_doc_type or "",
            "skipped_pmid": s.skip_pmid_lookup,
            "actions": " | ".join(s.actions),
            "api_calls": s.api_calls,
            "notes": " | ".join(s.notes),
            "enriched": s.was_enriched(),
        })
    audit_df = pd.DataFrame(rows)

    return EnrichmentResult(
        states=states,
        output_xml_bytes=output_bytes,
        audit_df=audit_df,
        n_articles=len(states),
        n_enriched=n_enriched,
        n_api_calls=client.daily_count if client else 0,
        n_api_errors=client.errors if client else 0,
    )
