# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] — 2026-05-07

### Fixed
- **UID lookup HTTP 400 error**: `lookup_by_uid()` was stripping the
  `WOS:` prefix from the URL path, but Clarivate's `/documents/{uid}`
  endpoint requires the full `<DB>:<id>` form. The colon is now URL-
  encoded as `%3A` so it isn't misread as a port separator. (Caught by
  user testing on Streamlit Cloud — thanks!)
- **Streamlit 1.57 deprecation warning**: replaced
  `use_container_width=True` with `width="stretch"` everywhere. Bumps
  the minimum Streamlit version to 1.45.

### Added
- 6 regression tests in `tests/test_wos_client.py` pinning the URL
  construction so the 400 bug can't recur silently.

## [0.2.0] — 2026-05-07

### Added
- **Streamlit web GUI** (`app.py`) — upload XML, configure in the sidebar,
  download patch + audit. The recommended interface for everyday use.
- Streamlit Community Cloud deployment support via `requirements.txt`.
- `.streamlit/config.toml` with sensible defaults; secrets template.
- `DEPLOY.md` with full step-by-step deployment guide for GitHub +
  Streamlit Community Cloud + self-hosting via nginx.

### Changed
- The enrichment pipeline was refactored out of `cli.py` into a new
  shared module `enrich.py`. Both the CLI and the Streamlit app now call
  the same `run_enrichment()` function, so they stay in sync forever.
- README rewritten to lead with the GUI; CLI documented as the
  alternate path for automation.

### Internal
- Tests updated to import from `psir_enrich.enrich` instead of
  `psir_enrich.cli`. All 44 tests still pass.
- CI now also smoke-tests that `app.py` is importable.

## [0.1.0] — 2026-05-07

### Added
- Initial public release.
- Reads OMEGA-PSIR 4.6.4 native XML exports.
- csl-WoS promotion (Tier 0): extracts WoS IDs already present in csl JSON.
- Clarivate WoS Starter API client (DOI lookup + UID lookup).
- Patch XML output for surgical re-import.
- Audit CSV with full per-record decision trail.
- Meeting abstract exclusion: skips PubMed lookups for meeting abstracts.
- Pluggable per-institution configuration.
- 44 unit + integration tests.

## [0.3.0] — 2026-05-07

### Fixed (breaking change in output format)
- **PSIR import failure**: the previous "patch XML" format (`<publications><publication>`)
  was rejected by PSIR's XML import because it didn't match the exported format.
  The output is now a **full-fidelity collection XML** — identical structure to
  the input, with new extid blocks added in-place on enriched articles and all
  other articles preserved unchanged. Import this directly with:
  - Tab: XML, Update record action: overwrite
  - **Update external identifiers: ✓ CHECKED** (critical)

### Changed
- **PubMedID dictionary UUID is now auto-detected** from the input XML itself.
  The `--pmid-idtype-uuid` CLI flag and the sidebar UUID input in the GUI have
  been removed — no manual configuration required.
- Output filename changed from `*_patch_*.xml` to `*_enriched_*.xml` to better
  reflect that the file contains the full collection.
- Added "How to import" instructions panel in the GUI results section.
- `EnrichmentResult.patch_xml_bytes` renamed to `output_xml_bytes`.

## [0.3.1] — 2026-05-08

### Fixed
- **Output XML contains only enriched records**: the v0.3.0 output included
  all articles (enriched or not). Output is now a `<collection>` with only
  the records that gained new extids — smaller file, faster PSIR import.
- **UI results survive download button clicks**: downloads previously
  triggered a full Streamlit rerun, wiping the audit table and download
  buttons. Results are now stored in `st.session_state` and persist until
  the user explicitly clicks "New run" in the sidebar.

## [0.3.2] — 2026-05-08

### Fixed
- **Zotero/csl keys no longer treated as WoS IDs**: `norm_wos_ut()` now
  rejects any value that doesn't start with `WOS:` or `ISI:` prefix, or
  isn't a purely numeric bare accession (≥8 digits). Values like
  `milkov2026posturographic` or `yaneva_diagnostic_2026` — Zotero-style
  csl id fields — are now silently skipped rather than being emitted as
  `WOS:milkov2026posturographic`. Those records fall through to the API
  lookup instead.

## [0.3.3] — 2026-05-08

### Fixed
- **Non-WOS collection UTs now stored verbatim**: `norm_wos_ut()` now
  accepts the full set of WoS collection prefixes (MEDLINE, CABI, BCI,
  BIOABS, BIOSIS, CCC, DIIDW, DRCI, ZOOREC, PPRN, WOK) and passes them
  through unchanged — e.g. `CABI:20250175695` stays `CABI:20250175695`,
  `MEDLINE:32832713` stays `MEDLINE:32832713`. Only the legacy `ISI:` prefix
  is normalised (to `WOS:`). Non-WoS identifiers (Zotero keys, Scopus EIDs,
  etc.) are still rejected and fall through to the API lookup.
- `lookup_by_uid()` in the Clarivate client now passes any valid WoS UT
  prefix through verbatim in the URL path, not just `WOS:`.
