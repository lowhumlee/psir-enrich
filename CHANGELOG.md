# Changelog

All notable changes to this project are documented here.

The format broadly follows Keep a Changelog, and the project uses semantic versioning when formal releases are tagged. The package version is currently `0.4.0`; the top section describes changes already present on `main` after that release.

---

## [Unreleased] — current `main`

### Added

- **NCBI PubMed DOI fallback** after Clarivate Starter DOI lookup fails. PubMed-only DOI records can now receive:
  - `PubMedID = <PMID>`
  - `WoSId = MEDLINE:<PMID>`
- Starter DOI lookup now tries `db=WOS` first and then `db=MEDLINE` before falling back to NCBI PubMed.
- CSL WoS extraction now reads publication WoS IDs from CSL `note` / `annote`, not only CSL `id`. This supports values such as:
  - `Web of Science ID: WOS:000308718600143`
  - `Web of Science ID: CABI:20183028270`
- Expanded publication issue fallback now maps supplement and special issue metadata into PSIR `<no>` when a regular issue is absent:
  - `supplement = 1` → `Suppl 1`
  - `supplement = 1`, `special_issue = SI` → `Suppl 1, SI`
- Added tests for:
  - CSL note WoS extraction
  - PubMed fallback producing `MEDLINE:<PMID>`
  - robust funding extraction
  - supplement/special issue handling
  - `wos_fund_text` overwrite behavior

### Changed

- Raw WoS funding text is now always written or overwritten in `wos_fund_text` when available. The generic PSIR `Funding` userfield is preserved and no longer suppresses `wos_fund_text`.
- Documentation now distinguishes:
  - generic PSIR `Funding`
  - WoS raw funding text: `wos_fund_text`
  - structured WoS agencies: `wos_grant_agencies`
  - structured WoS grant IDs: `wos_grant_ids`
- README and deployment guidance now describe the actual secrets:
  - `WOS_API_KEY`
  - `WOS_EXPANDED_API_KEY`
  - `NCBI_EMAIL`

### Fixed

- Expanded funding extraction no longer crashes when `fund_ack` is `None` or malformed.
- Clarified that `CABI` IDs may be stored as WoS IDs but are not eligible for Expanded `/id` lookup.
- Updated test count and standard CI command to reflect the active test suite:
  - `python -m pytest tests/ -v --ignore=tests/test_expanded.py`

---

## [0.4.0] — 2026-05-12

### Added

- **WoS Expanded API client** (`wos_expanded_client.py`) for full-record enrichment through `GET /id/{uid}?optionView=FR`.
- Optional `WOS_EXPANDED_API_KEY` support.
- Metadata fill from Expanded records:
  - `abstractEN`
  - `keywordsEN`
  - `collation`
  - journal issue `vol`
  - journal issue `no`
- WoS subject classification fields:
  - `wos_categories`
  - `wos_research_areas`
- KeyWords Plus field:
  - `wos_keywords_plus`
- Structured funding fields:
  - `wos_grant_agencies`
  - `wos_grant_ids`
- `EnrichmentResult` counters for Starter and Expanded calls/errors.

### Fixed

- MEDLINE / BCI / BIOABS / BIOSIS / CCC / DIIDW / DRCI / ZOOREC / PPRN prefixes are eligible for Expanded `/id` lookup where supported. `CABI` and `WOK` remain unsupported by Expanded `/id`.

---

## [0.3.5] — 2026-05-08

### Fixed

- **Duplicate indicators in output XML**: citation counts and other indicator elements could appear twice in enriched output. Fixed by deep-copying enriched articles before appending them to the output collection.

---

## [0.3.4] — 2026-05-08

### Fixed

- **HTTP 400 on unsupported Starter UID lookup**: records with prefixes unsupported by Starter `/documents/{uid}` now skip UID lookup and log a clear audit note. DOI lookup remains available.

---

## [0.3.3] — 2026-05-08

### Fixed

- `norm_wos_ut()` accepts known WoS collection prefixes such as `MEDLINE`, `CABI`, `BCI`, `BIOABS`, `BIOSIS`, `CCC`, `DIIDW`, `DRCI`, `ZOOREC`, `PPRN`, and `WOK`.
- Legacy `ISI:` is normalized to `WOS:`.
- Non-WoS identifiers such as Zotero keys and Scopus IDs are rejected.

---

## [0.3.2] — 2026-05-08

### Fixed

- Zotero/CSL keys are no longer treated as WoS IDs. Values such as `milkov2026posturographic` are rejected and fall through to API lookup.

---

## [0.3.1] — 2026-05-08

### Fixed

- Output XML now contains only enriched records, not the entire original collection.
- Streamlit results persist after download button clicks using `st.session_state`.

---

## [0.3.0] — 2026-05-07

### Fixed

- Replaced the older custom patch format with a PSIR-compatible `<collection>` XML output.

### Changed

- PubMedID dictionary UUID is auto-detected from input XML.
- Output filename changed from patch-style naming to enriched XML naming.
- GUI added import instructions.
- `EnrichmentResult.patch_xml_bytes` renamed to `output_xml_bytes`.

---

## [0.2.1] — 2026-05-07

### Fixed

- UID lookup URL construction now preserves and URL-encodes the full `<DB>:<id>` identifier.
- Replaced deprecated Streamlit `use_container_width=True` usage where applicable.

### Added

- Regression tests for Starter client URL construction.

---

## [0.2.0] — 2026-05-07

### Added

- Streamlit web GUI.
- Streamlit Community Cloud deployment support.
- `.streamlit/config.toml` and secrets template.
- Deployment guide.

### Changed

- Shared enrichment pipeline extracted into `enrich.py`.

---

## [0.1.0] — 2026-05-07

### Added

- Initial public release.
- Reads OMEGA-PSIR XML exports.
- CSL WoS promotion.
- Clarivate WoS Starter API client.
- Enriched XML output.
- Audit CSV.
- Meeting abstract PMID exclusion.
- Unit/integration tests.
