# Initial Release Walkthrough

This document covers two things:

1. How the app works for both non-technical and technical users.
2. The full debugging path we used from `git clone` to verified browser access.

---

## 1) How the app works

### 1.1 Non-technical explanation

Think of the app as a staff dashboard for bulk code checking.

- Sign in to the web app.
- Choose a site profile (for example, a Duolingo redeem profile).
- Add codes either by:
  - pasting into the large text box, or
  - uploading `.txt` / `.csv` files.
- Click **Start Job**.
- Watch progress in real time.
- Download CSV results when complete.

Each code ends up with one status:

- `valid`: the code appears redeemable.
- `invalid`: the code appears not redeemable (used, expired, wrong, etc.).
- `unknown`: app cannot confidently classify it.
- `blocked`: anti-bot/captcha/challenge prevented a clear result.
- `error`: technical failure while checking that code.

You can re-run only uncertain results (`unknown`, `blocked`, `error`) without rechecking everything.

### 1.2 Technical explanation

#### Stack

- Backend: FastAPI
- UI: server-rendered HTML + vanilla JS
- Storage: SQLite (`data/app.db`)
- HTTP checker: `httpx` async client
- Browser fallback: Playwright Chromium
- Auth: session cookie + username/password

#### Core flow

1. User submits a job (`POST /api/jobs`).
2. App parses pasted/uploaded codes, normalizes them, removes duplicates.
3. Job + per-code rows are saved in SQLite.
4. Background workers process codes:
   - HTTP stage first (fast).
   - Browser stage for uncertain/blocked/error cases.
5. UI polls job + results endpoints every few seconds.
6. Final CSV is exported from DB records.

#### Why this scales to large batches

- Async HTTP workers with configurable concurrency.
- Configurable request delay + retry behavior.
- Browser fallback used only when needed.
- Persistent DB state allows reload/recovery.

#### Profile system

Profiles in `profiles/*.yaml` define site-specific behavior.

- `mode: url_template` for direct URL patterns (`...{code}`).
- `mode: form` for input+submit flows.
- Rule sections define success/failure/blocked signals.
- `storage_state_path` allows logged-in browser/session cookies.

Included examples:

- `profiles/example_duolingo.yaml`
- `profiles/example_form_site.yaml`

#### Main API routes used by UI

- `GET /api/profiles`
- `POST /api/profiles/reload`
- `POST /api/profiles/{profile_name}/session-state`
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/results`
- `GET /api/jobs/{job_id}/export.csv`
- `POST /api/jobs/{job_id}/rerun-uncertain`

---

## 2) Debugging path: git clone -> verified browser access

This section is written as a practical runbook based on the exact bootstrap issues we hit.

### 2.1 Clone and inspect

```bash
cd /opt
git clone https://github.com/ronimuliawan/singularitychecker.git /opt/singularitychecker
cd /opt/singularitychecker
ls -la
```

### 2.2 Create virtualenv and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

### 2.3 Create `.env` correctly

```bash
cp .env.example .env
nano .env
```

Important notes:

- Dotfiles are hidden with `dir`; use `ls -la` to confirm `.env` exists.
- `APP_NAME` must be quoted if it has spaces, for example:

```ini
APP_NAME="SingularityChecker"
```

### 2.4 Load env and run smoke test

```bash
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 2.5 Error we saw: missing `itsdangerous`

Symptom:

- `ModuleNotFoundError: No module named 'itsdangerous'`

Fix:

- Added `itsdangerous` to `requirements.txt`.
- Reinstall dependencies:

```bash
pip install -r requirements.txt
```

### 2.6 Error we saw: bcrypt/passlib startup failure

Symptoms included:

- `(trapped) error reading bcrypt version`
- startup failure around passlib bcrypt backend

Fixes applied:

- Pinned `bcrypt>=3.2.0,<4.0.0` in `requirements.txt`.
- Added `ADMIN_PASSWORD` byte-length guard in startup.
- Kept `.env` guidance: `ADMIN_PASSWORD` should be 72 bytes or fewer.

If needed, reinstall:

```bash
pip install -r requirements.txt
```

### 2.7 Error we saw: remote curl connection reset

Root causes:

- app process had crashed, and/or
- app bound only to `127.0.0.1` while testing from a remote machine.

Checks:

```bash
curl http://127.0.0.1:8000/healthz
ss -lntp | grep 8000
```

Temporary external test mode:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Production mode recommendation:

- Keep app on `127.0.0.1:8000`.
- Put Nginx in front on `80/443`.

### 2.8 Browser verification

After fixes, open:

- `http://SERVER_IP:8000` (temporary test mode), or
- `https://your-domain.com` (Nginx + Certbot mode).

Expected checks:

- Login page renders.
- Auth works.
- Profiles are visible.
- `/healthz` returns `{"status":"ok"}`.

---

## 3) Initial release checklist

Before tagging an initial release on GitHub:

- Confirm `requirements.txt` installed cleanly on Ubuntu.
- Confirm `.env.example` reflects real parsing rules.
- Confirm both sample profiles load in UI.
- Confirm job run + CSV export works with a small test batch.
- Confirm docs exist and are linked:
  - `README.md`
  - `docs/DEPLOY_UBUNTU.md`
  - `docs/QUICK_NGINX_SETUP.md`
  - `docs/INITIAL_RELEASE_WALKTHROUGH.md`
