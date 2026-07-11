# psir-enrich

**Enrich OMEGA-PSIR publication XML records with Web of Science identifiers, PubMed IDs, and selected WoS Expanded metadata.**

`psir-enrich` provides a Streamlit GUI for routine use and a CLI for batch work. It was built for the OMEGA-PSIR 4.6.4 installation at the Medical University of Varna, with MU-Varna dictionary UUIDs currently embedded in the XML writer.

---

## Current capabilities

For each `<ns2:article>` in a PSIR XML export, the tool builds an audit trail and, when needed, writes an output `<collection>` containing only the enriched records.

### Identifier enrichment

1. Reads direct article-level `<extid>` blocks for `WoSId` and `PubMedID`.
2. Promotes publication WoS IDs already present in CSL JSON:
   - `id`: `WOS:...`, `CABI:...`, `MEDLINE:...`, etc.
   - `note` / `annote`: text such as `Web of Science ID: WOS:...` or `Web of Science ID: CABI:...`.
3. Searches DOI through Clarivate Starter API:
   - first `db=WOS`
   - then `db=MEDLINE`, when available through the plan/API response.
4. Falls back to NCBI PubMed ESearch when Clarivate does not resolve the DOI but PubMed does. In that case it stores:
   - `PubMedID = <PMID>`
   - `WoSId = MEDLINE:<PMID>`
5. For records with a known supported WoS UT but missing PMID, tries Starter UID lookup.
6. Skips PubMed lookup for meeting abstracts by default, but still stores available WoS identifiers.

### Expanded metadata enrichment

When a WoS Expanded API key is provided, records with an eligible UT are fetched through Expanded `/id/{uid}?optionView=FR`.

The tool fills these fields only when missing in PSIR:

- `abstractEN`
- `keywordsEN`
- `collation`
- journal issue `vol`
- journal issue `no`

For issue number, WoS `issue` is preferred. If there is no regular issue, supplement/special issue values are mapped to PSIR `<no>`, for example:

- `supplement = 1` → `Suppl 1`
- `supplement = 1`, `special_issue = SI` → `Suppl 1, SI`

The tool writes or updates these WoS-derived userfields:

- `wos_categories`
- `wos_research_areas`
- `wos_keywords_plus`
- `wos_grant_agencies`
- `wos_grant_ids`
- `wos_fund_text`

`wos_fund_text` is always written or overwritten from WoS funding text when available. The generic PSIR `Funding` userfield is left untouched.

### Output files

Each run produces:

- enriched XML containing only changed records
- audit CSV containing all input records and the action/skip reason for each one

---

## Quick GUI use

1. Upload a PSIR XML export.
2. Paste a Clarivate Starter API key, or leave it blank for CSL-only enrichment.
3. Optionally paste a WoS Expanded API key for metadata enrichment.
4. Click **Run enrichment**.
5. Download the enriched XML and audit CSV.

---

## Run locally

```bash
git clone https://github.com/lowhumlee/psir-enrich.git
cd psir-enrich
python -m pip install -e .
streamlit run app.py
```

Open `http://localhost:8501` if the browser does not open automatically.

### API keys

You can paste keys in the sidebar, or use environment variables:

```bash
export WOS_API_KEY="your-starter-key"
export WOS_EXPANDED_API_KEY="your-expanded-key"      # optional
export NCBI_EMAIL="you@example.org"                  # optional but recommended for PubMed fallback
```

For Streamlit secrets, use:

```toml
WOS_API_KEY = "your-starter-key"
WOS_EXPANDED_API_KEY = "your-expanded-key"
NCBI_EMAIL = "you@example.org"
```

The PubMed fallback does not require an NCBI API key for low-volume use.

---

## Deploy to Streamlit Community Cloud

1. Push the repository to GitHub.
2. Go to Streamlit Community Cloud.
3. Create a new app using:
   - repository: `lowhumlee/psir-enrich`
   - branch: `main`
   - main file path: `app.py`
4. Add secrets before deploying:

```toml
WOS_API_KEY = "your-starter-key"
WOS_EXPANDED_API_KEY = "your-expanded-key"
NCBI_EMAIL = "you@example.org"
```

Only `WOS_API_KEY` is required for Starter enrichment. `WOS_EXPANDED_API_KEY` enables Expanded metadata enrichment. `NCBI_EMAIL` is optional but recommended for the PubMed DOI fallback.

See [DEPLOY.md](DEPLOY.md) for the fuller deployment and maintenance guide.

---

## CLI

```bash
# install locally
python -m pip install -e .

# csl-only run, no API calls
psir-enrich --input my_export.xml --output enriched.xml --no-api

# Starter run
export WOS_API_KEY="your-starter-key"
psir-enrich --input my_export.xml --output enriched.xml --plan subscriber
```

Run:

```bash
psir-enrich --help
```

for the current CLI options.

---

## Import into PSIR

Use the XML import tab and import the enriched XML as an update file.

Recommended import settings:

- Tab: XML
- Update record action: overwrite
- Update external identifiers: checked
- Default field update action: overwrite

Check the audit CSV before import when the run contains API errors or unexpected skip notes.

---

## Project structure

```text
psir-enrich/
├── app.py                         # Streamlit GUI
├── DEPLOY.md                      # Deployment guide
├── CHANGELOG.md                   # Release notes
├── README.md
├── requirements.txt               # Streamlit Cloud install file
├── pyproject.toml                 # Package metadata
├── src/psir_enrich/
│   ├── core.py                    # PSIR XML parsing/writing and state model
│   ├── enrich.py                  # Shared enrichment pipeline
│   ├── wos_client.py              # Starter API + PubMed fallback client
│   ├── wos_expanded_client.py     # Expanded API client and extractors
│   └── cli.py                     # CLI entry point
└── tests/
    ├── test_core.py
    ├── test_cli.py
    ├── test_wos_client.py
    ├── test_wos_expanded_client.py
    └── test_expanded.py           # legacy/extended Expanded tests, ignored by the standard CI command
```

---

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/ -v --ignore=tests/test_expanded.py
```

The standard test command currently runs 73 tests covering XML parsing, CSL note extraction, Starter lookup construction, PubMed fallback, Expanded extraction helpers, funding handling, and CLI behavior. API calls are mocked in tests.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Wrong Starter or Expanded API key | Recheck the relevant key in the sidebar/secrets |
| DOI is PubMed-only and not in WoS Core | Starter `db=WOS` returns no hit | The NCBI PubMed fallback should fill `PubMedID` and `MEDLINE:<PMID>` |
| `CABI:` ID is found but Expanded is skipped | CABI is not supported by the Expanded `/id` endpoint | This is expected; the ID is still written |
| No `no` issue field from an abstract/supplement | WoS used supplement/special issue metadata | Current code maps this to `Suppl ...` / `SI` |
| Funding text not changing | Look for `wos_fund_text`, not generic `Funding` | Generic `Funding` is preserved; `wos_fund_text` is overwritten |
| Streamlit app keeps old behavior after commit | App did not redeploy or failed to pull files | Reboot/redeploy app and inspect Streamlit Cloud logs |
| Streamlit deploy fails during import | Dependency/install problem | Check that `requirements.txt` starts with `-e .` |

---

## License

MIT — see [LICENSE](LICENSE).
