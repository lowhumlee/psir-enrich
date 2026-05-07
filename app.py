"""Streamlit GUI for psir-enrich.

Run with:
    streamlit run app.py

Lets a user upload a PSIR XML, enrich it via the Clarivate WoS Starter API,
and download the patch XML and audit CSV — no terminal needed.
"""

from __future__ import annotations

import os
from datetime import datetime

import streamlit as st

from psir_enrich import __version__
from psir_enrich.enrich import run_enrichment
from psir_enrich.wos_client import WosStarterClient


# --------------------------------------------------------------------------
# Page config — must be first Streamlit command
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="psir-enrich",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)


PLAN_INTERVALS = {"free": 2.0, "subscriber": 0.25, "advanced": 0.1}


# --------------------------------------------------------------------------
# Sidebar — settings
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Settings")

    # API key — try secrets first, fall back to text input
    secrets_key = ""
    try:
        secrets_key = st.secrets.get("WOS_API_KEY", "")
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        # No .streamlit/secrets.toml — that's fine for local dev
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

    pmid_uuid = st.text_input(
        "PubMedID dictionary UUID",
        value="",
        help=(
            "The internal UUID of the 'PubMed ID' dictionary entry in your "
            "PSIR. Required to write PubMed extids — see the 'Where do I "
            "find this?' help below the file uploader. Leave blank to "
            "include WoS only."
        ),
    )

    plan = st.selectbox(
        "Plan tier",
        options=list(PLAN_INTERVALS.keys()),
        index=1,  # subscriber
        help=(
            "Sets the default rate limit between API calls. Free=2s, "
            "subscriber=0.25s, advanced=0.1s."
        ),
    )

    skip_meetings = st.checkbox(
        "Skip PubMed lookup for meeting abstracts",
        value=True,
        help=(
            "Meeting abstracts are very rarely indexed in PubMed. By "
            "default, when the API reports a record as 'Meeting Abstract', "
            "we record the WoS UT but skip the PubMed call to save quota."
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
        owner = st.text_input(
            "Owner attribute on new extids",
            value="system",
            help="Written into the <owner> tag of new extid elements.",
        )

    st.markdown("---")
    st.caption(f"psir-enrich v{__version__}")


# --------------------------------------------------------------------------
# Main panel
# --------------------------------------------------------------------------

st.title("📚 OMEGA-PSIR enrichment")
st.markdown(
    "Upload a PSIR XML export, enrich each record with the **WoS Identifier** "
    "and **PubMed ID** from the Clarivate WoS Starter API, then download a "
    "patch XML ready to re-import into PSIR."
)

with st.expander("ℹ️ What this tool does"):
    st.markdown(
        """
        For every `<ns2:article>` in your input XML, this tool:

        1. **Surveys** the existing `<extid>` blocks. If both WoSId and PubMedID are
           already present, the record is skipped — zero API calls.
        2. **Promotes csl-WoS** — if the record's `csl` JSON metadata already contains
           a WoS ID but no proper extid block, it's promoted for free.
        3. **Looks up by DOI** — calls `/documents?q=DO=<doi>` for any record still
           missing identifiers.
        4. **Falls back to UID lookup** — for records with a known WoS UT but missing
           PMID, calls `/documents/{uid}`.
        5. **Excludes meeting abstracts** from PubMed lookups (configurable).
        6. **Builds a patch XML** with only the changed records.
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

        For ongoing/automated use, the **Subscriber** plan (5,000 requests/day) is
        the right tier. The **Free** plan (50/day) is fine for testing.
        """
    )

with st.expander("🆔 Where do I find the PubMedID dictionary UUID?"):
    st.markdown(
        """
        Open any PSIR record that already has a PubMedID extid (any record imported
        from Scopus or PubMed will work). Export it as XML and look for a block like:

        ```xml
        <extid type="termfield">
          ...
          <idtype type="term">
            <id>WUTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</id>     ← THIS IS THE UUID
            ...
            <systemName>PubMedID</systemName>
            ...
          </idtype>
          <value>12345678</value>
        </extid>
        ```

        Copy the UUID inside the inner `<idtype><id>` element. You only need this
        once — note it down for future runs.
        """
    )

st.markdown("---")

# File uploader
uploaded = st.file_uploader(
    "📄 Upload PSIR XML export",
    type=["xml"],
    accept_multiple_files=False,
    help="The XML you exported from PSIR's admin → Publications → Export.",
)

if uploaded is not None:
    st.success(f"Loaded **{uploaded.name}** ({uploaded.size:,} bytes)")

# Run button
col1, col2 = st.columns([1, 4])
with col1:
    run_clicked = st.button(
        "▶ Run enrichment",
        type="primary",
        disabled=(uploaded is None),
        use_container_width=True,
    )

# --------------------------------------------------------------------------
# Run pipeline
# --------------------------------------------------------------------------

if run_clicked and uploaded is not None:
    # Reset uploaded file pointer (Streamlit may have read it earlier)
    uploaded.seek(0)
    xml_bytes = uploaded.read()

    # Set up live progress UI
    progress_bar = st.progress(0.0, text="Initialising...")
    status_box = st.empty()

    def _progress(i, total, state):
        progress_bar.progress(
            i / total,
            text=f"Article {i}/{total} · {state.psir_id[:24] or '(no id)'}"
        )
        if state.actions:
            tag = " | ".join(state.actions)
            status_box.write(f"`[{i}]` `{state.psir_id[:32]}` → **{tag}**")
        elif state.notes:
            status_box.write(f"`[{i}]` `{state.psir_id[:32]}` ⚠ {state.notes[0]}")

    try:
        result = run_enrichment(
            xml_input=xml_bytes,
            api_key=api_key.strip() or None,
            api_base=api_base.strip() or None,
            pmid_idtype_uuid=pmid_uuid.strip() or None,
            skip_meeting_abstracts=skip_meetings,
            rate_limit=rate_limit,
            owner=owner,
            input_label=uploaded.name,
            progress_cb=_progress,
        )
        progress_bar.progress(1.0, text="Done")
    except Exception as exc:
        progress_bar.empty()
        st.error(f"Enrichment failed: {exc}")
        st.exception(exc)
        st.stop()

    # ---------------- Summary ----------------
    st.markdown("## ✅ Done")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Articles in input", result.n_articles)
    c2.metric("Records enriched", result.n_enriched)
    c3.metric("API calls made", result.n_api_calls)
    c4.metric("API errors", result.n_api_errors,
              delta=None if result.n_api_errors == 0 else "check audit",
              delta_color="inverse")

    if result.n_api_errors > 0:
        st.warning(
            f"There were {result.n_api_errors} API errors. Look at the "
            f"`notes` column in the audit table below for details."
        )

    # PMID-without-uuid warning
    if any(s.new_pmid for s in result.states) and not pmid_uuid.strip():
        st.warning(
            "⚠ PubMed IDs were resolved from the API, but no PubMed UUID "
            "was provided, so they were **NOT** written to the patch XML "
            "(they are still in the audit CSV). Add the PubMedID UUID in "
            "the sidebar and re-run to include them."
        )

    # ---------------- Downloads ----------------
    st.markdown("### 📥 Downloads")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = uploaded.name.rsplit(".", 1)[0]

    dc1, dc2 = st.columns(2)
    with dc1:
        st.download_button(
            label=f"⬇ Download patch XML ({result.n_enriched} record(s))",
            data=result.patch_xml_bytes,
            file_name=f"{base_name}_patch_{timestamp}.xml",
            mime="application/xml",
            use_container_width=True,
            disabled=(result.n_enriched == 0),
        )
        if result.n_enriched == 0:
            st.caption("Nothing was enriched — no patch to download.")
    with dc2:
        st.download_button(
            label=f"⬇ Download audit CSV ({len(result.audit_df)} row(s))",
            data=result.audit_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{base_name}_audit_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # ---------------- Audit table ----------------
    st.markdown("### 📋 Audit table")
    st.markdown(
        "Every input record, what was found, and what was written to the patch."
    )
    st.dataframe(
        result.audit_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "wrote_to_xml": st.column_config.CheckboxColumn(
                "→ XML", help="True if this record made it into the patch XML"
            ),
            "skipped_pmid": st.column_config.CheckboxColumn(
                "Skip PMID",
                help="True for records identified as meeting abstracts",
            ),
            "title": st.column_config.TextColumn("Title", width="medium"),
            "actions": st.column_config.TextColumn("Actions", width="medium"),
            "notes": st.column_config.TextColumn("Notes", width="medium"),
        },
    )

    # ---------------- Patch preview ----------------
    if result.n_enriched > 0:
        with st.expander("👁 Preview patch XML"):
            st.code(
                result.patch_xml_bytes.decode("utf-8"),
                language="xml",
                line_numbers=True,
            )

else:
    if uploaded is None:
        st.info("⬆ Upload a PSIR XML to get started.")


# Footer
st.markdown("---")
st.caption(
    "Built for OMEGA-PSIR 4.6.4 · "
    "[Clarivate WoS Starter API docs](https://developer.clarivate.com/apis/wos-starter)"
)
