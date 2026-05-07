# Deployment guide

How to put `psir-enrich` on GitHub and (optionally) deploy the Streamlit
GUI to Streamlit Community Cloud.

---

## Phase 1 — Get the code into your environment

### 1.1 Extract the archive

Download `psir-enrich.tar.gz` and extract:

```bash
# macOS / Linux
tar -xzf psir-enrich.tar.gz
cd psir-enrich
```

```cmd
:: Windows
tar -xzf psir-enrich.tar.gz
cd psir-enrich
```

### 1.2 Verify it works locally before publishing

```bash
python -m pip install -e ".[dev]" --user
python -m pytest tests/ -v
```

You should see all 44 tests pass.

### 1.3 Try the Streamlit app locally

```bash
streamlit run app.py
```

Browser opens at `http://localhost:8501`. Upload your `13sample.xml` (or the `tests/fixtures/mini.xml` shipped with the repo), leave the API key blank, click **Run enrichment** — you'll get the csl-only enrichment result.

If everything works, proceed to Phase 2.

---

## Phase 2 — Create the GitHub repository

### 2.1 Sign in to GitHub

If you don't have an account, create one at [github.com](https://github.com). Use your `mu-varna.bg` email — institutional accounts get treated more seriously by Clarivate and others.

### 2.2 Create the repository

Click **+ → New repository** in the top-right. Fill in:

- **Repository name**: `psir-enrich`
- **Description**: "Enrich OMEGA-PSIR records with WoS UT and PubMed ID via Clarivate API"
- **Public** vs **Private**: I'd suggest **Public** since the code contains no secrets and may be useful to other PSIR-using institutions. If institution policy requires Private, choose Private.
- **Do NOT** check "Add a README", "Add .gitignore", or "Choose a license" — we already have all three locally.

Click **Create repository**.

### 2.3 Note your repo URL

GitHub shows you a Quick setup page with `https://github.com/YOUR_USER/psir-enrich.git`. Copy that URL — you'll need it.

---

## Phase 3 — Push your local code

### 3.1 Replace the placeholder in `pyproject.toml` and `README.md`

Both files have `YOUR_GITHUB_USER` placeholders — replace with your actual username:

```bash
# macOS / Linux
sed -i.bak 's|YOUR_GITHUB_USER|sglinkov|g' pyproject.toml README.md DEPLOY.md
rm *.bak
```

```cmd
:: Windows — open each file in Notepad and Find/Replace
notepad pyproject.toml
notepad README.md
notepad DEPLOY.md
```

### 3.2 Initialise git and commit

```bash
git init
git add .
git status         # sanity-check — make sure no secrets or real PSIR data
git commit -m "Initial commit: psir-enrich 0.2.0"
git branch -M main
```

The `git status` check is important. **Verify** that none of these are about to be committed:
- `.streamlit/secrets.toml` (real secrets file)
- Real PSIR XML exports
- Audit CSVs from earlier runs
- Any file containing your actual API key

If you see anything sensitive, stop, add it to `.gitignore`, and re-run `git status`.

### 3.3 Push to GitHub

```bash
git remote add origin https://github.com/YOUR_USER/psir-enrich.git
git push -u origin main
```

You'll be prompted for credentials. **GitHub no longer accepts your password here** — you need a *Personal Access Token*.

To create one: GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → **Generate new token (classic)**. Required scope: just `repo`. Copy the token immediately (you can't see it again) and use it in place of your password.

For long-term convenience, install [GitHub CLI](https://cli.github.com/) and run `gh auth login` once — it handles auth seamlessly thereafter.

### 3.4 Confirm

Refresh the GitHub repo page. You should see all your files, the README rendered nicely, and (after ~1 minute) a green ✓ next to the latest commit indicating CI passed. If CI is red ❌, click into it on the **Actions** tab to see what failed.

---

## Phase 4a — Deploy to Streamlit Community Cloud (free public hosting)

Best for: trying it out, low-traffic internal use, sharing with colleagues. The app gets a public URL like `https://your-user-psir-enrich.streamlit.app/`.

### 4a.1 Sign in

Go to [share.streamlit.io](https://share.streamlit.io/) and click **Continue with GitHub**. Authorise Streamlit to read your repos.

### 4a.2 Create the app

Click **New app**.

- **Repository**: pick `YOUR_USER/psir-enrich`
- **Branch**: `main`
- **Main file path**: `app.py`
- **App URL** (optional): pick a custom subdomain

### 4a.3 Add secrets BEFORE deploying

Click **Advanced settings** → **Secrets** and paste:

```toml
WOS_API_KEY = "your-real-clarivate-key-here"
```

This is the same format as `.streamlit/secrets.toml.example`. Streamlit injects these into `st.secrets` at runtime, and they're never visible to anyone except app admins.

### 4a.4 Deploy

Click **Deploy!**. First boot takes 2–3 minutes (installs `requirements.txt`). Watch the log for any errors.

### 4a.5 Manage access

By default, Streamlit Community Cloud apps are **public**. Anyone with the URL can use them. Two implications:

1. **Your API key is at risk if anyone abuses the public URL** — the requests will count against your daily quota and Clarivate may ask questions.

2. **PSIR XML uploaded to the app does not leave the Streamlit instance** — the file is processed in memory and never written to disk. But the app's IP is at Streamlit's hosting provider, not your institution.

To restrict access, the app's dashboard → **Settings** → **Sharing** lets you:
- Disable public access entirely
- Allow only specific GitHub users (free for up to 3 viewers)

For full institutional access control, deploy on your own infrastructure instead — see Phase 4b.

---

## Phase 4b — Self-host on your own infrastructure

Best for: institutional production use, sensitive data, custom auth.

### 4b.1 On the target server

Requires Python 3.9+. Linux server example:

```bash
git clone https://github.com/YOUR_USER/psir-enrich.git
cd psir-enrich
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 4b.2 Provide the API key

Either via env var:

```bash
export WOS_API_KEY="your-key"
```

Or via a secrets file:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit to put your real key
chmod 600 .streamlit/secrets.toml   # only the running user can read it
```

### 4b.3 Run as a service

For one-off use:

```bash
streamlit run app.py --server.port 8501 --server.address 127.0.0.1
```

For production, run as a systemd service. Create `/etc/systemd/system/psir-enrich.service`:

```ini
[Unit]
Description=psir-enrich Streamlit GUI
After=network.target

[Service]
Type=simple
User=psir-enrich
WorkingDirectory=/opt/psir-enrich
Environment="PATH=/opt/psir-enrich/.venv/bin"
ExecStart=/opt/psir-enrich/.venv/bin/streamlit run app.py \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    --server.headless true
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now psir-enrich
```

### 4b.4 Put nginx in front

For HTTPS, custom domain, and auth:

```nginx
server {
    listen 443 ssl http2;
    server_name psir-enrich.your-institution.bg;

    ssl_certificate     /etc/letsencrypt/live/psir-enrich.your-institution.bg/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/psir-enrich.your-institution.bg/privkey.pem;

    # Optional: HTTP basic auth or institutional SSO via auth_request
    auth_basic "PSIR Enrichment";
    auth_basic_user_file /etc/nginx/htpasswd;

    location / {
        proxy_pass         http://127.0.0.1:8501/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;   # important for Streamlit websockets
    }
}
```

---

## Phase 5 — Day-to-day workflow when you make changes

```bash
# edit code locally
vim src/psir_enrich/core.py

# run tests
pytest tests/ -v

# bump version in pyproject.toml AND src/psir_enrich/__init__.py
# (e.g., 0.2.0 → 0.3.0 for behaviour changes; 0.2.1 for bug fixes)
# add an entry to CHANGELOG.md

# commit and push
git add .
git commit -m "Add Scopus enrichment support"
git push

# tag the release
git tag v0.3.0
git push --tags
```

If you deployed via Streamlit Community Cloud, the app **auto-redeploys on every push to `main`** within a minute. No further action needed.

If you self-host, restart the service:

```bash
ssh your-server
cd /opt/psir-enrich
git pull
sudo systemctl restart psir-enrich
```

---

## Things to handle carefully

1. **Never commit your API key.** The `.gitignore` already excludes `.env*`, `*.key`, and `.streamlit/secrets.toml`. Set `WOS_API_KEY` as an environment variable, paste it into the GUI, or use Streamlit's secrets — never hard-code it.

2. **Never commit real PSIR data.** The `.gitignore` excludes `patch_*.xml`, `*_audit.csv`, and `local_runs/`. Store test data outside the repo.

3. **The MU-Varna constants (`UMV` prefix, dictionary UUIDs) are in `src/psir_enrich/core.py`.** If sharing with other institutions, document that they need to fork and customise those values, or accept a config file via `--config institution.toml`.

4. **CI runs on every push and PR.** If a future change breaks anything, you'll see ❌ on GitHub. Don't merge until it's green.

If anything goes wrong during deployment, the most useful debugging output is `git status` (shows what's about to be committed), `pip show psir-enrich` (shows where it's installed), and the GitHub Actions log on the **Actions** tab.
