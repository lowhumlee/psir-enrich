"""Streamlit GUI for psir-enrich.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import os
from datetime import datetime

import streamlit as st

from psir_enrich import __version__
from psir_enrich.enrich import run_enrichment
from psir_enrich.wos_client import WosStarterClient


# --------------------------------------------------------------------------
# Page config — must be the very first Streamlit call
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="OMEGA-PSIR enrichment",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

PLAN_INTERVALS = {"free": 2.0, "subscriber": 0.25, "advanced": 0.1}

# --------------------------------------------------------------------------
# Session state — keeps results alive across reruns triggered by downloads
# --------------------------------------------------------------------------
# Keys stored:
#   result       — EnrichmentResult object from the last successful run
#   run_filename — name of the file that produced the result
#   timestamp    — formatted timestamp for file naming

for _k in ("result", "run_filename", "timestamp"):
    if _k not in st.session_state:
        st.session_state[_k] = None


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Settings")

    # ── Starter API key ────────────────────────────────────────────────────
    # Check secrets → env var → let user type it
    secrets_key = ""
    try:
        secrets_key = st.secrets.get("WOS_API_KEY", "")
    except Exception:
        pass

    env_key = os.environ.get("WOS_API_KEY", "")
    default_key = secrets_key or env_key

    api_key = st.text_input(
        "Clarivate Starter API key",
        value=default_key,
        type="password",
        help=(
            "Your WoS Starter API key from the Clarivate Developer Portal. "
            "Leave blank to run in csl-only mode (no API calls). "
            "Used for WoS UT and PubMed ID lookup."
        ),
    )
    if secrets_key:
        st.caption("✓ Starter key loaded from `st.secrets`")
    elif env_key:
        st.caption("✓ Starter key loaded from `WOS_API_KEY` env var")

    # ── Expanded API key ───────────────────────────────────────────────────
    # Separate institutional subscription — enables metadata fill tier
    expanded_secrets_key = ""
    try:
        expanded_secrets_key = st.secrets.get("WOS_EXPANDED_API_KEY", "")
    except Exception:
        pass

    expanded_env_key = os.environ.get("WOS_EXPANDED_API_KEY", "")
    expanded_default = expanded_secrets_key or expanded_env_key

    expanded_api_key = st.text_input(
        "WoS Expanded API key (optional)",
        value=expanded_default,
        type="password",
        help=(
            "Institutional WoS Expanded API key. Enables metadata fill: "
            "abstract, keywords, pages/collation, volume, issue, "
            "WoS categories, research areas, WoS editions, "
            "KeyWords Plus, early access date, and structured funding. "
            "This is a separate subscription from the Starter key above — "
            "leave blank if you only have the Starter key."
        ),
    )
    if expanded_secrets_key:
        st.caption("✓ Expanded key loaded from `st.secrets`")
    elif expanded_env_key:
        st.caption("✓ Expanded key loaded from `WOS_EXPANDED_API_KEY` env var")

    # ── Plan / rate limit ─────────────────────────────────────────────────
    plan = st.selectbox(
        "Plan tier (Starter API)",
        options=list(PLAN_INTERVALS.keys()),
        index=1,
        help="Sets the default rate limit for Starter API calls. "
             "Free=2s, subscriber=0.25s, advanced=0.1s.",
    )

    skip_meetings = st.checkbox(
        "Skip PubMed lookup for meeting abstracts",
        value=True,
        help=(
            "Meeting abstracts are rarely in PubMed. When the API identifies "
            "a record as 'Meeting Abstract', the WoS UT is captured but no "
            "PubMed call is made, saving quota."
        ),
    )

    with st.expander("Advanced"):
        api_base = st.text_input(
            "Starter API base URL",
            value=WosStarterClient.DEFAULT_BASE_URL,
            help="Override only if Clarivate has given you a different endpoint.",
        )
        rate_limit = st.number_input(
            "Starter rate limit (seconds between calls)",
            value=PLAN_INTERVALS[plan],
            min_value=0.0,
            max_value=10.0,
            step=0.05,
            format="%.2f",
        )
        expanded_rate_limit = st.number_input(
            "Expanded rate limit (seconds between calls)",
            value=0.5,
            min_value=0.1,
            max_value=10.0,
            step=0.1,
            format="%.1f",
            help="Expanded API allows ~2 requests/second. Default 0.5 s is safe.",
        )

    st.markdown("---")

    # "New run" button — clears stored results so user can upload a new file
    if st.session_state.result is not None:
        if st.button("🔄 New run", use_container_width=True):
            st.session_state.result = None
            st.session_state.run_filename = None
            st.session_state.timestamp = None
            st.rerun()

    st.caption(f"psir-enrich v{__version__}")


# --------------------------------------------------------------------------
# Main panel — header + help expanders
# --------------------------------------------------------------------------

st.title("📚 OMEGA-PSIR enrichment")
st.markdown(
    "Upload a PSIR XML export, enrich each record with identifiers and metadata "
    "from the Clarivate APIs, then download a patch XML ready to re-import into PSIR."
)

with st.expander("ℹ️ What this tool does — two tiers"):
    st.markdown(
        """
        **Tier 1 — Identifier enrichment (Starter API key)**

        For every `<ns2:article>` in your input XML, the tool:

        1. **Surveys** existing `<extid>` blocks — if WoSId and PubMedID are
           both present the record is skipped (zero API calls).
        2. **Promotes csl-WoS** — if the `csl` JSON field already contains a
           `WOS:` ID, it's promoted for free (no API call).
        3. **Looks up by DOI** — calls Clarivate `/documents?q=DO=<doi>` for
           any record still missing identifiers.
        4. **Falls back to UID lookup** — for records with a WoS UT but no
           PMID, calls `/documents/{uid}`.
        5. **Excludes meeting abstracts** from PubMed lookups (configurable).

        ---

        **Tier 2 — Metadata enrichment (Expanded API key)**

        For each article that has a WoS UT, fetches the full WoS record and:

        - Fills **abstract** and **author keywords** if missing in PSIR.
        - Fills **pages/collation**, **volume**, **issue** if missing.
        - Always writes **WoS subject categories** (e.g. "Food Science & Technology")
          and **research areas** (e.g. "General & Internal Medicine") — stored
          in separate fields because they are different classification systems.
        - Always writes **WoS editions** (SCI / SSCI / ESCI / AHCI).
        - Always writes **KeyWords Plus** (algorithmically derived from references).
        - Writes **early access date** if present.
        - Writes **structured funding** (grant agencies and grant IDs) in
          separate fields alongside the existing free-text Funding field.

        ---

        **Output**: a patch XML containing only the changed records, plus an
        audit CSV showing every decision made for every article.
        """
    )

with st.expander("🔑 How do I get a Clarivate API key?"):
    st.markdown(
        """
        **Starter API** (for WoS UT + PubMed ID lookup):
        1. Go to the [Clarivate Developer Portal](https://developer.clarivate.com/).
        2. Sign in with your Web of Science institutional credentials.
        3. Register a new application.
        4. Subscribe it to **Web of Science Starter API**.
        5. Copy the API key from the application page.

        The **Subscriber** plan (5,000 requests/day) is the right tier for
        regular institutional use. The **Free** plan (50/day) is fine for testing.

        ---

        **Expanded API** (for full metadata enrichment):
        - This requires an institutional subscription to the WoS Expanded API.
        - Contact your Clarivate account manager — it is separate from the
          Starter API and has its own key and annual record quota.
        - The key goes in the "WoS Expanded API key" field in the sidebar.
        """
    )

with st.expander("🆔 PubMedID dictionary UUID — auto-detected"):
    st.markdown(
        """
        **No action needed.** The PubMedID dictionary UUID is automatically read
        from the input XML itself.

        UUID confirmed for this PSIR installation:
        `WUT0bfbfdfcb0974f4db3731f2527055a27`
        """
    )

st.markdown("---")

# --------------------------------------------------------------------------
# Upload + run  (only shown when no result is stored)
# --------------------------------------------------------------------------

if st.session_state.result is None:

    uploaded = st.file_uploader(
        "📄 Upload PSIR XML export",
        type=["xml"],
        help="The XML exported from PSIR Admin → Publications → Export.",
    )

    if uploaded is not None:
        st.success(f"Loaded **{uploaded.name}** ({uploaded.size:,} bytes)")

    run_clicked = st.button(
        "▶ Run enrichment",
        type="primary",
        disabled=(uploaded is None),
    )

    if run_clicked and uploaded is not None:
        uploaded.seek(0)
        xml_bytes = uploaded.read()
        fname = uploaded.name

        progress_bar = st.progress(0.0, text="Initialising...")
        status_box = st.empty()

        def _progress(i, total, state):
            frac = i / total
            progress_bar.progress(
                frac,
                text=f"Article {i}/{total} · {state.psir_id[:24] or '(no id)'}"
            )
            if state.actions:
                status_box.write(
                    f"`[{i}]` `{state.psir_id[:32]}` → **{' | '.join(state.actions)}**"
                )
            elif state.notes:
                status_box.write(
                    f"`[{i}]` `{state.psir_id[:32]}` ⚠ {state.notes[0]}"
                )

        try:
            result = run_enrichment(
                xml_input=xml_bytes,
                api_key=api_key.strip() or None,
                expanded_api_key=expanded_api_key.strip() or None,
                api_base=api_base.strip() or None,
                skip_meeting_abstracts=skip_meetings,
                rate_limit=rate_limit,
                expanded_rate_limit=expanded_rate_limit,
                input_label=fname,
                progress_cb=_progress,
            )
            progress_bar.progress(1.0, text="Done ✓")
            status_box.empty()
        except Exception as exc:
            progress_bar.empty()
            st.error(f"Enrichment failed: {exc}")
            st.exception(exc)
            st.stop()

        # Store in session state — survives download button reruns
        st.session_state.result = result
        st.session_state.run_filename = fname
        st.session_state.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.rerun()   # re-render cleanly to show results panel

    elif uploaded is None:
        st.info("⬆ Upload a PSIR XML to get started.")


# --------------------------------------------------------------------------
# Results panel  (shown whenever session_state.result is set)
# --------------------------------------------------------------------------

else:
    result = st.session_state.result
    base_name = st.session_state.run_filename.rsplit(".", 1)[0]
    timestamp = st.session_state.timestamp

    # --- Summary metrics ---
    st.markdown("## ✅ Done")

    # Row 1: article counts
    c1, c2, c3 = st.columns(3)
    c1.metric("Articles in input", result.n_articles)
    c2.metric("Records enriched", result.n_enriched)
    c3.metric(
        "API errors",
        result.n_api_errors + result.n_expanded_errors,
        delta=None if (result.n_api_errors + result.n_expanded_errors) == 0
              else "check audit",
        delta_color="inverse",
    )

    # Row 2: API call counts
    c4, c5, c6 = st.columns(3)
    c4.metric("Starter API calls", result.n_api_calls)
    c5.metric("Expanded API calls", result.n_expanded_calls)
    c6.metric(
        "Quota used (Expanded)",
        result.n_expanded_calls,
        help="Each Expanded call consumes 1 record from your annual quota.",
    )

    if result.n_api_errors + result.n_expanded_errors > 0:
        st.warning(
            f"There were {result.n_api_errors + result.n_expanded_errors} API error(s). "
            "See the `notes` column in the audit table below."
        )

    if result.n_enriched == 0:
        st.info(
            "No records needed enrichment — all articles already have "
            "all available fields. Nothing to import."
        )

    # --- PSIR import instructions ---
    with st.expander("📋 How to import into PSIR", expanded=(result.n_enriched > 0)):
        st.markdown(
            """
            1. Go to **PSIR Admin → Publications → Import**
            2. Select the **XML** tab
            3. Set:
               - **Update record action**: `overwrite`
               - **Update external identifiers**: ✅ **CHECKED** ← critical
               - **Default field update action**: `overwrite`
            4. Upload the downloaded XML file below
            """
        )

    # --- Downloads ---
    st.markdown("### 📥 Downloads")
    dc1, dc2 = st.columns(2)

    with dc1:
        xml_label = (
            f"⬇ Patch XML  ({result.n_enriched} enriched record(s))"
            if result.n_enriched > 0
            else "⬇ Patch XML  (nothing to download)"
        )
        st.download_button(
            label=xml_label,
            data=result.output_xml_bytes,
            file_name=f"{base_name}_enriched_{timestamp}.xml",
            mime="application/xml",
            use_container_width=True,
            disabled=(result.n_enriched == 0),
        )
        st.caption(
            f"Contains only the {result.n_enriched} enriched record(s) "
            f"(not all {result.n_articles}). Import with settings above."
        )

    with dc2:
        st.download_button(
            label=f"⬇ Audit CSV  ({len(result.audit_df)} row(s))",
            data=result.audit_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{base_name}_audit_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.caption(
            "All input records with match type, new IDs, metadata gaps filled, "
            "WoS categories, and any error notes."
        )

    # --- Audit table ---
    st.markdown("### 📋 Audit table")
    st.dataframe(
        result.audit_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "enriched": st.column_config.CheckboxColumn(
                "→ XML",
                help="True if this record is in the patch XML",
            ),
            "skipped_pmid": st.column_config.CheckboxColumn(
                "Skip PMID",
                help="True = meeting abstract; PubMed lookup skipped",
            ),
            "had_abstract": st.column_config.CheckboxColumn(
                "Had abstract",
                help="Abstract was already in PSIR before this run",
            ),
            "new_abstract": st.column_config.CheckboxColumn(
                "Abstract added",
                help="Abstract was filled from WoS Expanded API",
            ),
            "had_keywords": st.column_config.CheckboxColumn(
                "Had keywords",
            ),
            "new_keywords": st.column_config.CheckboxColumn(
                "Keywords added",
            ),
            "had_collation": st.column_config.CheckboxColumn(
                "Had pages",
            ),
            "had_vol": st.column_config.CheckboxColumn(
                "Had vol",
            ),
            "had_issue": st.column_config.CheckboxColumn(
                "Had issue",
            ),
            "title": st.column_config.TextColumn("Title", width="medium"),
            "wos_categories": st.column_config.TextColumn(
                "WoS categories", width="medium",
                help="WoS subject categories (traditional classification)",
            ),
            "research_areas": st.column_config.TextColumn(
                "Research areas", width="medium",
                help="WoS research areas (extended classification)",
            ),
            "actions": st.column_config.TextColumn("Actions", width="medium"),
            "notes": st.column_config.TextColumn("Notes", width="medium"),
        },
    )

    # --- XML preview ---
    if result.n_enriched > 0:
        with st.expander("👁 Preview patch XML"):
            st.code(
                result.output_xml_bytes.decode("utf-8"),
                language="xml",
                line_numbers=True,
            )

# --------------------------------------------------------------------------
# Footer
# --------------------------------------------------------------------------

st.markdown("---")
st.caption(
    "Built for OMEGA-PSIR 4.6.4 · "
    "[Clarivate WoS Starter API](https://developer.clarivate.com/apis/wos-starter) · "
    "[Clarivate WoS Expanded API](https://developer.clarivate.com/apis/wos)"
)
