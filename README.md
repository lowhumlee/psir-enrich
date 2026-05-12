# psir-enrich

**Enrich [OMEGA-PSIR](https://omega-psir.atlassian.net/) publication records with Web of Science Identifiers and PubMed IDs via the [Clarivate WoS Starter API](https://developer.clarivate.com/apis/wos-starter).**

A web-based GUI (Streamlit) for everyday use, plus a command-line interface for automation and batch jobs. Built and tested for the OMEGA-PSIR 4.6.4 installation at the Medical University of Varna, but the institution-specific dictionary UUIDs are configurable.

---

## Quick demo

1. Upload your PSIR XML export.
2. Paste your Clarivate API key.
3. Click **Run enrichment**.
4. Download the patch XML (ready to re-import into PSIR) and the audit CSV.

That's it.

---

## What it does

For each `<ns2:article>` in your input XML, the tool:

1. **Surveys** existing `<extid>` blocks — if both `WoSId` and `PubMedID` are already there, the record is skipped (zero API calls).
2. **Promotes csl-WoS** — when the `csl` JSON metadata already has a `WOS:` ID but no proper extid block, it's promoted for free.
3. **Looks up by DOI** — calls Clarivate `/documents?q=DO=<doi>&db=WOS` for any record still missing identifiers.
4. **Falls back to UID lookup** — for records with a known WoS UT but missing PMID, calls `/documents/{uid}`.
5. **Excludes meeting abstracts** from PubMed lookups by default.
6. **Outputs a patch XML** with only the changed records, plus an audit CSV showing every decision.

---

## Run the GUI locally

### Prerequisites
- Python 3.9 or newer
- A Clarivate WoS Starter API key ([how to get one](#how-do-i-get-a-clarivate-api-key))

### Install + launch

```bash
git clone https://github.com/YOUR_GITHUB_USER/psir-enrich.git
cd psir-enrich
pip install -e .
streamlit run app.py
```

Your default browser opens at `http://localhost:8501`. Paste your API key in the sidebar, upload an XML, click **Run**.

### Storing the API key locally

If you don't want to paste the key every time, copy the secrets template:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit and put your real key inside
```

`secrets.toml` is git-ignored — it will never be committed.

Alternatively, set the `WOS_API_KEY` environment variable before launching Streamlit and the app will pick it up automatically.

---

## Deploy to Streamlit Community Cloud

[Streamlit Community Cloud](https://share.streamlit.io/) hosts public Streamlit apps for free. Five-minute setup:

1. Push this repo to your GitHub account (see [DEPLOY.md](DEPLOY.md) for the full guide).
2. Go to [share.streamlit.io](https://share.streamlit.io/), sign in with GitHub.
3. Click **New app** → pick your repo + branch → set "Main file path" to `app.py`.
4. Before clicking Deploy, click **Advanced settings** → **Secrets** and paste:
   ```toml
   WOS_API_KEY = "your-clarivate-key"
   ```
5. Click **Deploy**. Public URL is yours in ~2 minutes.

To restrict access (recommended given the API key is institution-bound), enable [Authentication](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app/share-your-app-with-viewers-outside-your-workspace) on the app's dashboard or deploy on internal infrastructure instead — see "Self-host" below.

### Self-host on your own server

```bash
# On a server with Python 3.9+
git clone https://github.com/YOUR_GITHUB_USER/psir-enrich.git
cd psir-enrich
pip install -e .

# Run with a process manager (systemd, supervisor, etc.) - simplest:
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

Put it behind a reverse proxy (nginx, Caddy) with HTTPS for production use.

---

## Use the CLI instead

For automation, scripting, or running on machines without a display, use the `psir-enrich` command:

```bash
pip install -e .  # or pip install git+https://github.com/YOUR_GITHUB_USER/psir-enrich.git

# Dry run (csl-only, no API):
psir-enrich -i my_export.xml -o patch.xml --no-api

# Real run:
export WOS_API_KEY="your-key"
psir-enrich \
    --input  my_export.xml \
    --output patch.xml \
    --pmid-idtype-uuid "WUTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
    --plan subscriber
```

Run `psir-enrich --help` for all options.

---

## How do I get a Clarivate API key?

1. Go to the [Clarivate Developer Portal](https://developer.clarivate.com/).
2. Sign in with your Web of Science institutional credentials.
3. Register a new application (any name).
4. Subscribe it to **Web of Science Starter API**.
5. Copy the API key from the application page.

For everyday institutional use, the **Subscriber** plan (5,000 requests/day) is the right tier. The **Free** plan (50/day) is enough for testing.

---

## Project structure

```
psir-enrich/
├── app.py                       # Streamlit GUI — the main user-facing entry
├── pyproject.toml               # Package metadata + dependencies
├── requirements.txt             # For Streamlit Community Cloud deployment
├── README.md
├── DEPLOY.md                    # GitHub + Streamlit Cloud deployment guide
├── CHANGELOG.md
├── LICENSE
├── .streamlit/
│   ├── config.toml              # Streamlit defaults (theme, upload limits)
│   └── secrets.toml.example     # Template — copy to secrets.toml and edit
├── .github/workflows/ci.yml     # CI: tests on push/PR, all OS × Py versions
├── src/psir_enrich/
│   ├── __init__.py              # Public API
│   ├── core.py                  # XML parsing, state, ID normalisation
│   ├── wos_client.py            # Clarivate WoS Starter API client
│   ├── enrich.py                # Shared enrichment pipeline (used by GUI + CLI)
│   └── cli.py                   # Command-line entry point
└── tests/
    ├── test_core.py             # Pure logic tests
    ├── test_cli.py              # Pipeline integration tests (mocked API)
    └── fixtures/
        └── mini.xml
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "401 Unauthorized" in error log | Wrong API key | Check the key in the sidebar |
| "404 Not found" on every call | Wrong base URL | Reset to default in sidebar Advanced |
| "429 Rate limited" | Too fast for plan | Increase rate-limit in sidebar Advanced or pick a lower plan |
| Patch XML has WoS but no PMID extids | PubMedID UUID was empty | Fill it in the sidebar and re-run |
| All API calls return "no hits" | DOIs not in WoS | Spot-check a DOI on webofscience.com |
| Streamlit fails to start: "ImportError: lxml" | Missing platform wheel | `pip install lxml --upgrade` |
| Streamlit Cloud deploy fails | `requirements.txt` issue | Check the deploy logs; ensure `-e .` is at the top |

---

## Development

```bash
git clone https://github.com/YOUR_GITHUB_USER/psir-enrich.git
cd psir-enrich
pip install -e ".[dev]"
pytest tests/ -v
```

44 unit + integration tests covering normalisation, XML parsing, the enrichment ladder, and the CLI. The tests use a mocked WoS client so no real API calls are made.

---

## License

MIT — see [LICENSE](LICENSE).
