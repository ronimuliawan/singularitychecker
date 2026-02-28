# Quick Production Setup: systemd + Nginx + HTTPS (Ubuntu)

This is a short copy-paste setup for your current server.

Assumptions:

- App path: `/opt/singularitychecker`
- You have root access
- Python virtualenv already exists at `/opt/singularitychecker/.venv`

Replace `your-domain.com` before running.

---

## 1) Set variables

```bash
export APP_DIR="/opt/singularitychecker"
export APP_PORT="8000"
export DOMAIN="your-domain.com"
```

---

## 2) Create systemd service (app stays on localhost)

```bash
cat >/etc/systemd/system/singularitychecker.service <<EOF
[Unit]
Description=SingularityChecker FastAPI service
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${APP_PORT} --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable/start/check:

```bash
systemctl daemon-reload
systemctl enable singularitychecker
systemctl restart singularitychecker
systemctl status singularitychecker --no-pager
journalctl -u singularitychecker -n 100 --no-pager
```

---

## 3) Configure Nginx reverse proxy

```bash
cat >/etc/nginx/sites-available/singularitychecker <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
```

Enable site and reload:

```bash
ln -sf /etc/nginx/sites-available/singularitychecker /etc/nginx/sites-enabled/singularitychecker
nginx -t
systemctl reload nginx
```

---

## 4) Enable HTTPS with Certbot

```bash
apt update
apt install -y certbot python3-certbot-nginx
certbot --nginx -d ${DOMAIN}
```

Check auto-renew timer:

```bash
systemctl list-timers | grep certbot
```

---

## 5) Firewall

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw status
```

If you opened port `8000` during testing, close it now:

```bash
ufw delete allow 8000/tcp
```

---

## 6) Final checks

```bash
curl -I http://${DOMAIN}
curl -I https://${DOMAIN}
curl -k https://${DOMAIN}/healthz
```

Expected health response body:

`{"status":"ok"}`

---

## Notes

- Keep app bound to `127.0.0.1`; only Nginx should be public.
- If DNS is not ready yet, run Nginx on HTTP first and run Certbot later.
- Running as root works, but using a dedicated Linux user is safer long-term.
