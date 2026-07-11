# Deployment guide

This guide reflects the current `psir-enrich` Streamlit/CLI tool: Starter enrichment, optional WoS Expanded enrichment, and PubMed DOI fallback.

---

## 1. Local setup

```bash
git clone https://github.com/lowhumlee/psir-enrich.git
cd psir-enrich
python -m pip install -e ".[dev]"
```

Run the standard test suite:

```bash
python -m pytest tests/ -v --ignore=tests/test_expanded.py
```

At the current state this command runs 73 tests. The legacy/extended `tests/test_expanded.py` file is intentionally ignored by the standard CI command.

Run the app locally:

```bash
streamlit run app.py
```

Open `http://localhost:8501`.

---

## 2. API keys and secrets

The app can run in several modes:

| Mode | Required key | What works |
|---|---|---|
| CSL-only | none | Promotes WoS IDs already present in CSL JSON |
| Starter | `WOS_API_KEY` | WoS/PubMed identifier enrichment |
| Starter + PubMed fallback | `WOS_API_KEY`; optional `NCBI_EMAIL` | PubMed-only DOI records can become `PubMedID` + `MEDLINE:<PMID>` |
| Starter + Expanded | `WOS_API_KEY` + `WOS_EXPANDED_API_KEY` | Identifier enrichment plus metadata enrichment |

Environment variable example:

```bash
export WOS_API_KEY="your-starter-key"
export WOS_EXPANDED_API_KEY="your-expanded-key"
export NCBI_EMAIL="you@example.org"
```

Streamlit secrets example:

```toml
WOS_API_KEY = "your-starter-key"
WOS_EXPANDED_API_KEY = "your-expanded-key"
NCBI_EMAIL = "you@example.org"
```

`NCBI_EMAIL` is optional but recommended for NCBI E-utilities requests. No NCBI API key is required for normal low-volume use.

Never commit `.streamlit/secrets.toml`, real PSIR XML exports, audit CSVs, or API keys.

---

## 3. Deploy to Streamlit Community Cloud

1. Push the repo to GitHub.
2. Go to Streamlit Community Cloud.
3. Create a new app:
   - repository: `lowhumlee/psir-enrich`
   - branch: `main`
   - main file path: `app.py`
4. Open **Advanced settings → Secrets** and add:

```toml
WOS_API_KEY = "your-starter-key"
WOS_EXPANDED_API_KEY = "your-expanded-key"
NCBI_EMAIL = "you@example.org"
```

5. Deploy.

`requirements.txt` starts with `-e .`, which installs the repository as a package so `app.py` can import `psir_enrich` cleanly.

---

## 4. Streamlit Cloud maintenance

Streamlit Cloud normally redeploys automatically when `main` changes.

If the app continues to show old behavior after a commit:

1. Open the Streamlit app dashboard.
2. Check the deploy log for messages such as `Updating the app files has failed`.
3. Click **Reboot app** or **Redeploy**.
4. Confirm the log shows the latest commit was pulled.
5. Run a small XML test file and inspect the audit CSV.

---

## 5. Self-hosting

Install on a server:

```bash
git clone https://github.com/lowhumlee/psir-enrich.git
cd psir-enrich
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Run:

```bash
streamlit run app.py --server.port 8501 --server.address 127.0.0.1
```

Use a reverse proxy such as nginx/Caddy for HTTPS and access control.

Example systemd service:

```ini
[Unit]
Description=psir-enrich Streamlit GUI
After=network.target

[Service]
Type=simple
User=psir-enrich
WorkingDirectory=/opt/psir-enrich
Environment="PATH=/opt/psir-enrich/.venv/bin"
Environment="WOS_API_KEY=your-starter-key"
Environment="WOS_EXPANDED_API_KEY=your-expanded-key"
Environment="NCBI_EMAIL=you@example.org"
ExecStart=/opt/psir-enrich/.venv/bin/streamlit run app.py \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    --server.headless true
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

After editing the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now psir-enrich
```

---

## 6. Normal development workflow

```bash
git status
python -m pytest tests/ -v --ignore=tests/test_expanded.py
git add .
git commit -m "Describe the change"
git push
```

For behavior changes, update:

- `README.md`
- `DEPLOY.md`, if deployment/secrets/test commands changed
- `CHANGELOG.md`
- `pyproject.toml` and `src/psir_enrich/__init__.py`, if making a formal version release

Current package version remains `0.4.0` unless those version files are intentionally bumped.

---

## 7. Import into PSIR

Import the generated XML with:

- Tab: XML
- Update record action: overwrite
- Update external identifiers: checked
- Default field update action: overwrite

The enriched XML contains only changed records. The audit CSV contains all records and should be reviewed before import.
