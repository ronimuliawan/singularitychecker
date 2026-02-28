# Redeem Checker

Redeem Checker is a FastAPI web app for validating large batches of redeem codes (10,000+). It is designed for non-technical staff:

- Paste codes or upload `.txt` / `.csv` files
- Select a site profile
- Start job and monitor progress live
- Download CSV results

It uses a two-stage strategy:

1. Fast HTTP checks (rule-based)
2. Browser fallback with Playwright for blocked/uncertain results

## Features

- Handles large batches with async workers and deduplication
- Stores job/results in SQLite for persistence
- Profile-driven rules for different redeem sites
- Logged-in session support via Playwright `storage_state` JSON
- Re-run only `unknown` / `blocked` / `error` results

## Quick Start (Local)

1. Create and activate a virtual environment.
2. Install dependencies.
3. Install Playwright Chromium.
4. Copy `.env.example` to `.env` and update credentials.
5. Start the app.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://<server-ip>:8000`.

## Capturing Logged-In Session State

If a redeem flow requires login, create a Playwright storage-state file and upload it in the UI.

```bash
python -m playwright codegen --save-storage=sessions/duolingo.json https://www.duolingo.com
```

After login is complete in the codegen browser, close it. Then upload that JSON in **Upload Logged-In Session**.

## Profile Files

Profiles live in `profiles/*.yaml`.

Included samples:

- `profiles/example_duolingo.yaml` for `url_template` flows (`.../redeem?code={code}`)
- `profiles/example_form_site.yaml` for `form` flows (fill input + click submit)

Core keys:

- `mode`: `url_template` or `form`
- `url_template`: e.g. `https://site/redeem?code={code}`
- `http.success` / `http.failure` / `http.blocked`
- `browser.success_text_any` / `browser.failure_text_any` / `browser.blocked_text_any`
- `browser.storage_state_path`

### Form Profile Quick Checklist

For sites that need form interaction, configure these first:

1. `form.url` with the redeem page URL.
2. `form.code_selector` with the input selector for the code field.
3. `form.submit_selector` with the redeem button selector.
4. `browser.result_selector` (optional but recommended) where result text appears.
5. `browser.success_text_any` and `browser.failure_text_any` with exact phrases from the site.

Tip: use browser DevTools inspect to verify selectors before running a large batch.

## API Endpoints (used by UI)

- `GET /api/profiles`
- `POST /api/profiles/reload`
- `POST /api/profiles/{profile_name}/session-state`
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/results`
- `GET /api/jobs/{job_id}/export.csv`
- `POST /api/jobs/{job_id}/rerun-uncertain`

## Deploy on Ubuntu VPS

See `docs/DEPLOY_UBUNTU.md` for a full production guide (systemd + Nginx + HTTPS).

For faster copy-paste deployment and troubleshooting:

- `docs/QUICK_NGINX_SETUP.md`
- `docs/INITIAL_RELEASE_WALKTHROUGH.md`

## Notes

- Respect each target site's terms of service.
- Use conservative concurrency/delay settings to reduce rate limiting.
- Session-state files can contain sensitive cookies; protect file permissions.
