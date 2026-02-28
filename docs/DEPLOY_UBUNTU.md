# Deploy Redeem Checker on Ubuntu VPS

This guide deploys the app with:

- `uvicorn` app server managed by `systemd`
- `nginx` reverse proxy
- HTTPS via Let's Encrypt (`certbot`)

Tested for Ubuntu 22.04/24.04.

---

## 1) Prepare the Server

### 1.1 Create a non-root deploy user (optional but recommended)

```bash
sudo adduser redeemapp
sudo usermod -aG sudo redeemapp
```

Log in as that user afterward.

### 1.2 Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

### 1.3 Install Playwright system dependencies + Chromium

Inside the project virtualenv (later step), run:

```bash
python -m playwright install --with-deps chromium
```

This installs browser binaries and Ubuntu libraries required for headless browser checks.

---

## 2) Place the Project on the VPS

Example target path:

```bash
sudo mkdir -p /opt/redeem-checker
sudo chown -R $USER:$USER /opt/redeem-checker
```

Copy project files into `/opt/redeem-checker` (git clone, rsync, or SCP).

---

## 3) Python Environment Setup

```bash
cd /opt/redeem-checker
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

---

## 4) Configure Environment Variables

Create `.env` from the template:

```bash
cd /opt/redeem-checker
cp .env.example .env
```

Edit `.env`:

```ini
APP_NAME="Redeem Checker"
SECRET_KEY=use-a-long-random-secret

ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-this-now

DATABASE_PATH=./data/app.db
PROFILES_DIR=./profiles
SESSIONS_DIR=./sessions
TEMPLATES_DIR=./templates
STATIC_DIR=./static

DEFAULT_HTTP_CONCURRENCY=20
DEFAULT_BROWSER_CONCURRENCY=1
DEFAULT_MAX_RETRIES=2
DEFAULT_REQUEST_DELAY_MS=100
```

Notes:

- Quote values that contain spaces (like `APP_NAME`).
- Keep `ADMIN_PASSWORD` at 72 bytes or fewer for bcrypt compatibility.

Generate a secure secret quickly:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

---

## 5) First App Run (Smoke Test)

```bash
cd /opt/redeem-checker
source .venv/bin/activate
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://SERVER_IP:8000` temporarily (or via SSH tunnel) and confirm:

- Login page appears
- You can sign in using `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- `profiles/example_duolingo.yaml` and `profiles/example_form_site.yaml` are visible in profile dropdown

Stop with `Ctrl+C`.

---

## 6) Run as a systemd Service

Create service file:

```bash
sudo nano /etc/systemd/system/redeem-checker.service
```

Paste:

```ini
[Unit]
Description=Redeem Checker FastAPI service
After=network.target

[Service]
Type=simple
User=redeemapp
Group=redeemapp
WorkingDirectory=/opt/redeem-checker
EnvironmentFile=/opt/redeem-checker/.env
ExecStart=/opt/redeem-checker/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

If your deploy user is not `redeemapp`, adjust `User` and `Group`.

Enable/start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable redeem-checker
sudo systemctl start redeem-checker
sudo systemctl status redeem-checker
```

View logs:

```bash
sudo journalctl -u redeem-checker -f
```

---

## 7) Configure Nginx Reverse Proxy

Create site config:

```bash
sudo nano /etc/nginx/sites-available/redeem-checker
```

Paste (replace `your-domain.com`):

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/redeem-checker /etc/nginx/sites-enabled/redeem-checker
sudo nginx -t
sudo systemctl reload nginx
```

---

## 8) Enable HTTPS (Let's Encrypt)

```bash
sudo certbot --nginx -d your-domain.com
```

Choose redirect to HTTPS when prompted.

Verify auto-renew timer:

```bash
systemctl list-timers | grep certbot
```

---

## 9) Firewall (UFW)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

---

## 10) Profile and Session Management

### 10.1 Add or edit site profiles

- Put YAML files under `/opt/redeem-checker/profiles/`
- In the UI, click **Reload Profiles**

### 10.2 Upload logged-in session state

- Use Playwright codegen from your workstation or the server
- Save storage state JSON
- Upload it in UI under **Upload Logged-In Session**

Example capture command:

```bash
cd /opt/redeem-checker
source .venv/bin/activate
python -m playwright codegen --save-storage=sessions/duolingo.json https://www.duolingo.com
```

For form-mode sites, run the same command against that site's redeem page and save to the path used in `browser.storage_state_path`.

---

## 11) Updates and Maintenance

### 11.1 Deploy updated code

```bash
cd /opt/redeem-checker
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart redeem-checker
```

### 11.2 Backups

Back up:

- `data/app.db`
- `profiles/*.yaml`
- `sessions/*.json`
- `.env`

Quick backup example:

```bash
cd /opt/redeem-checker
tar czf backup_$(date +%F_%H%M%S).tar.gz data profiles sessions .env
```

---

## 12) Troubleshooting

### App not starting

- Check logs: `sudo journalctl -u redeem-checker -n 200 --no-pager`
- Validate env file path and permissions
- Ensure `requirements.txt` installed in `.venv`

### `.env` loads with `command not found`

- Usually caused by unquoted values with spaces, e.g. `APP_NAME=Redeem Checker`
- Fix by quoting: `APP_NAME="Redeem Checker"`

### Missing `itsdangerous` module

- Reinstall dependencies in the active venv: `pip install -r requirements.txt`

### bcrypt/passlib startup issues

- Ensure dependencies match `requirements.txt` (includes a compatible bcrypt pin)
- Reinstall dependencies in the active venv: `pip install -r requirements.txt`

### Browser fallback always fails

- Run `python -m playwright install --with-deps chromium` again
- Verify session state JSON path in profile (`browser.storage_state_path`)
- Check if the target site now has stronger anti-bot protections

### Too many blocked/rate-limited results

- Lower `HTTP concurrency`
- Increase `Delay per request`
- Reduce retry count
- Use session state and rely more on browser checks

### Nginx shows 502

- Verify service is running: `sudo systemctl status redeem-checker`
- Confirm app listens on `127.0.0.1:8000`
- Re-check Nginx config with `sudo nginx -t`

---

## 13) Security Checklist

- Change admin defaults in `.env`
- Use long random `SECRET_KEY`
- Restrict SSH access and keep OS updated
- Use HTTPS only
- Keep `sessions/*.json` private (`chmod 600` if needed)
