# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
