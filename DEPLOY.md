# Deployment Guide — psir-enrich

Enriches OMEGA-PSIR 4.6.4 publication XML with WoS identifiers, PubMed IDs,
Scopus IDs and metadata. Supports three independent API tiers:
Clarivate Starter, Clarivate Expanded, and Elsevier Scopus.

---

## 1. Prerequisites

- Python 3.10 or newer
- Git
- API keys from your institution (only `WOS_API_KEY` is required;
  `WOS_EXPANDED_API_KEY` and `SCOPUS_API_KEY` are optional)

---

## 2. Deploy to Streamlit Community Cloud

1. Push the repository to GitHub.
2. Go to https://share.streamlit.io and click **New app**.
3. Set:
   - Repository: `lowhumlee/psir-enrich`
   - Branch: `main`
   - Main file path: `app.py`
4. Open **Advanced settings → Secrets** and add:

```toml
WOS_API_KEY = "your-starter-key"
WOS_EXPANDED_API_KEY = "your-expanded-key"
SCOPUS_API_KEY = "your-scopus-key"
NCBI_EMAIL = "you@example.org"
```

5. Click **Deploy**.

**Notes**
- `requirements.txt` must start with `-e .` or the deployment will fail
  to find the `psir_enrich` package.
- Leave optional keys blank if you do not have that subscription.
- Never commit real keys to GitHub.

---

## 3. Run locally on Windows 10 (PowerShell, no admin)

### 3.1 Install Python

Download Python 3.10+ from https://www.python.org/downloads/ and run the
installer. Check **Add Python to PATH** on the first screen.
"Install for me only" is sufficient — no administrator rights needed.

Verify:

```powershell
python --version
```

### 3.2 Clone the repository

```powershell
cd $HOME
git clone https://github.com/lowhumlee/psir-enrich.git
cd psir-enrich
```

### 3.3 Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script, run this once and retry:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Your prompt should now start with `(.venv)`.

### 3.4 Install the package

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[gui,dev]"
```

This installs `lxml`, `requests`, `pandas`, `streamlit` and the test tools.

### 3.5 Run the tests

```powershell
python -m pytest tests/ -v --ignore=tests/test_expanded.py
```

### 3.6 Launch the GUI

```powershell
streamlit run app.py
```

Open http://localhost:8501 if the browser does not open automatically.

### 3.7 Supply API keys

Either paste them into the sidebar, or set them for the current session:

```powershell
$env:WOS_API_KEY = "your-starter-key"
$env:WOS_EXPANDED_API_KEY = "your-expanded-key"
$env:SCOPUS_API_KEY = "your-scopus-key"
$env:NCBI_EMAIL = "you@example.org"
streamlit run app.py
```

To persist keys for your user (no admin required):

```powershell
[Environment]::SetEnvironmentVariable("WOS_API_KEY", "your-starter-key", "User")
[Environment]::SetEnvironmentVariable("SCOPUS_API_KEY", "your-scopus-key", "User")
```

Restart PowerShell afterwards. Alternatively create
`.streamlit/secrets.toml` with the same keys as in section 2, and make
sure that file is listed in `.gitignore`.

### 3.8 Finish

```powershell
deactivate
```

### Everyday shortcut

```powershell
cd $HOME\psir-enrich
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

---

## 4. CLI usage

Activate the virtual environment first.

```powershell
.\.venv\Scripts\Activate.ps1
```

CSL-only run, no API calls:

```powershell
psir-enrich -i export.xml -o enriched.xml --no-api
```

Full run with all three tiers (keys from environment or flags):

```powershell
psir-enrich -i export.xml -o enriched.xml `
  --api-key "starter-key" `
  --expanded-api-key "expanded-key" `
  --scopus-api-key "scopus-key"
```

All options:

```powershell
psir-enrich --help
```

Each run produces `enriched.xml` (changed records only) and an audit CSV.

---

## 5. Import the result into PSIR

1. Open **PSIR Admin → Publications → Import**.
2. Select the **XML** tab.
3. Set:
   - Update record action: `overwrite`
   - Update external identifiers: **CHECKED**
   - Default field update action: `overwrite`
4. Upload the enriched XML.

Check the audit CSV first if the run reported API errors or unexpected skips.

---

## 6. Docker (optional)

Dockerfile:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -e ".[gui]"
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Build and run:

```powershell
docker build -t psir-enrich .
docker run -p 8501:8501 `
  -e WOS_API_KEY="your-starter-key" `
  -e WOS_EXPANDED_API_KEY="your-expanded-key" `
  -e SCOPUS_API_KEY="your-scopus-key" `
  psir-enrich
```

---

## 7. Updating after code changes

Local:

```powershell
git pull
python -m pip install -e ".[gui,dev]"
python -m pytest tests/ -v --ignore=tests/test_expanded.py
streamlit run app.py
```

Streamlit Cloud: open the app dashboard → **⋮** → **Reboot app**,
then check the logs for import errors.

---

## 8. API rate limits

| API                  | Default interval | Approx. throughput |
|----------------------|------------------|--------------------|
| Clarivate Starter    | 0.25 s           | ~4 requests/s      |
| Clarivate Expanded   | 0.50 s           | ~2 requests/s      |
| Elsevier Scopus      | 0.35 s           | ~3 requests/s      |

Adjust in the GUI under **Advanced**, or with `--rate-limit` in the CLI.
Expanded calls consume your annual record quota; the tool skips records
that already have all available fields.

---

## 9. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `ModuleNotFoundError: psir_enrich` | `requirements.txt` must start with `-e .`; reinstall with `pip install -e .` |
| `401 Unauthorized` | Wrong or missing API key; check the relevant sidebar field or secret |
| `429 Too Many Requests` | Increase the rate limit interval for that API |
| Scopus ID not added | Missing `SCOPUS_API_KEY`, or the record is not indexed in Scopus |
| `articleNo` missing from output | Confirm the API actually returns an article number; `articleNo` and `collation` can both be written in v0.5.0 |
| Meeting abstract gets no PMID | Expected; enable "Skip PubMed lookup for meeting abstracts" only if you want to force the lookup |
| GUI shows old behaviour after a push | Reboot the Streamlit Cloud app and inspect its logs |
| PowerShell refuses to activate the venv | Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |

---

## 10. Verification checklist

Before a production import:

- [ ] Tests pass locally.
- [ ] A small test XML produces the expected `WoSId`, `ScopusId` and `PubMedID`.
- [ ] Records with an electronic article number contain `<articleNo>` in the patch XML.
- [ ] Records with both pages and an article number contain both `<collation>` and `<articleNo>`.
- [ ] Audit CSV shows no unexpected API errors.
- [ ] API keys are not committed to the repository.
