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

    # API key: check secrets → env → user input
    secrets_key = ""
    try:
        secrets_key = st.secrets.get("WOS_API_KEY", "")
    except Exception:
        pass

    env_key = os.environ.get("WOS_API_KEY", "")
    default_key = secrets_key or env_key

    api_key = st.text_input(
        "Clarivate API key",
        value=default_key,
        type="password",
        help=(
            "Your WoS Starter API key from the Clarivate Developer Portal. "
            "Leave blank to run in csl-only mode (no API calls)."
        ),
    )
    if secrets_key:
        st.caption("✓ Loaded from `st.secrets`")
    elif env_key:
        st.caption("✓ Loaded from `WOS_API_KEY` env var")

    plan = st.selectbox(
        "Plan tier",
        options=list(PLAN_INTERVALS.keys()),
        index=1,
        help="Sets the default rate limit. Free=2s, subscriber=0.25s, advanced=0.1s.",
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
            "API base URL",
            value=WosStarterClient.DEFAULT_BASE_URL,
            help="Override only if Clarivate has given you a different endpoint.",
        )
        rate_limit = st.number_input(
            "Rate limit (seconds between calls)",
            value=PLAN_INTERVALS[plan],
            min_value=0.0,
            max_value=10.0,
            step=0.05,
            format="%.2f",
        )

    st.markdown("---")

    # "New run" button — clears stored results and lets user upload again
    if st.session_state.result is not None:
        if st.button("🔄 New run", width="stretch"):
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
    "Upload a PSIR XML export, enrich each record with the "
    "**WoS Identifier** and **PubMed ID** from the Clarivate WoS Starter API, "
    "then download a patch XML ready to re-import into PSIR."
)

with st.expander("ℹ️ What this tool does"):
    st.markdown(
        """
        For every `<ns2:article>` in your input XML, the tool:

        1. **Surveys** existing `<extid>` blocks — if WoSId and PubMedID are
           both present the record is skipped entirely (zero API calls).
        2. **Promotes csl-WoS** — if the `csl` JSON field already contains a
           `WOS:` ID but no proper extid block, it's promoted for free.
        3. **Looks up by DOI** — calls Clarivate `/documents?q=DO=<doi>` for
           any record still missing identifiers.
        4. **Falls back to UID lookup** — for records with a WoS UT but no
           PMID, calls `/documents/{uid}`.
        5. **Excludes meeting abstracts** from PubMed lookups (configurable).
        6. **Outputs a patch XML** containing only the enriched records in
           PSIR's native `<collection><ns2:article>` format, ready to import.
        """
    )

with st.expander("🔑 How do I get a Clarivate API key?"):
    st.markdown(
        """
        1. Go to the [Clarivate Developer Portal](https://developer.clarivate.com/).
        2. Sign in with your Web of Science institutional credentials.
        3. Register a new application.
        4. Subscribe it to **Web of Science Starter API**.
        5. Copy the API key from the application page.

        The **Subscriber** plan (5,000 requests/day) is the right tier for
        regular institutional use. The **Free** plan (50/day) is fine for testing.
        """
    )

with st.expander("🆔 PubMedID dictionary UUID — auto-detected"):
    st.markdown(
        """
        **No action needed.** The PubMedID dictionary UUID is automatically read
        from the input XML — any record that already has a PubMedID extid provides
        it, and it is reused for all new entries.

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
                api_base=api_base.strip() or None,
                skip_meeting_abstracts=skip_meetings,
                rate_limit=rate_limit,
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
        st.rerun()   # re-render to show the results panel cleanly

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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Articles in input", result.n_articles)
    c2.metric("Records enriched", result.n_enriched)
    c3.metric("API calls made", result.n_api_calls)
    c4.metric(
        "API errors", result.n_api_errors,
        delta=None if result.n_api_errors == 0 else "check audit",
        delta_color="inverse",
    )

    if result.n_api_errors > 0:
        st.warning(
            f"There were {result.n_api_errors} API errors. "
            f"See the `notes` column in the audit table below."
        )

    if result.n_enriched == 0:
        st.info(
            "No records needed enrichment — all articles already have "
            "both WoSId and PubMedID. Nothing to import."
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
            width="stretch",
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
            width="stretch",
        )
        st.caption("All input records with match type, new IDs, and any notes.")

    # --- Audit table ---
    st.markdown("### 📋 Audit table")
    st.dataframe(
        result.audit_df,
        width="stretch",
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
            "title": st.column_config.TextColumn("Title", width="medium"),
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
    "[Clarivate WoS Starter API docs](https://developer.clarivate.com/apis/wos-starter)"
)
