# Deploying NLSchool to your own server (staging / testing)

Assumes Ubuntu 22.04+, Nginx, systemd, gunicorn, PostgreSQL and Let's Encrypt.
Adapt the commands if your server differs. Use two subdomains of one parent
domain (for example `app.example.com` and `api.example.com`). The login refresh
cookie is `SameSite=Lax`, so a frontend and API on unrelated domains will log
users out on every reload.

This guide has not been run against a real server. Treat the first deploy as a
rehearsal with fake data.

## 1. Server prerequisites
```bash
sudo apt update && sudo apt install -y python3-venv python3-dev libpq-dev nginx certbot python3-certbot-nginx postgresql
# Node 22 (see .nvmrc): install via nvm or NodeSource
```
Create a service user and folders:
```bash
sudo useradd --system --create-home --shell /bin/bash nlschool
sudo mkdir -p /srv/nlschool /var/lib/nlschool/media /etc/nlschool
sudo chown -R nlschool:nlschool /srv/nlschool /var/lib/nlschool
sudo chmod 700 /var/lib/nlschool/media
```
Copy the project to `/srv/nlschool` (`rsync` or git). **Do not copy** your local
`backend/.env`, `backend/db.sqlite3`, `backend/.venv` or `frontend/node_modules`.

## 2. PostgreSQL with verified TLS
Production refuses to start with `DJANGO_DB_SSLMODE` weaker than `verify-ca` or
`verify-full`. For a database on the same server you must enable SSL on
PostgreSQL and give Django the CA that signed its certificate:

1. Create a private CA and a server certificate whose CN/SAN equals the value
   you put in `DJANGO_DB_HOST` (for example `db.example.com`, mapped to
   `127.0.0.1` in `/etc/hosts`).
2. Set `ssl = on`, `ssl_cert_file`, `ssl_key_file` in `postgresql.conf`.
3. In `pg_hba.conf` use `hostssl nlschool nlschool_user 127.0.0.1/32 scram-sha-256`.
4. Put the CA certificate at `/etc/ssl/nlschool/db-ca.crt` and set
   `DJANGO_DB_SSLROOTCERT` to it.

Then create the database:
```sql
CREATE USER nlschool_user WITH PASSWORD '<strong password>';
CREATE DATABASE nlschool OWNER nlschool_user;
```
A managed PostgreSQL service that offers a CA bundle is simpler than this.

## 3. Backend
```bash
sudo -u nlschool bash
cd /srv/nlschool/backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env && nano .env     # fill every value
python manage.py check --deploy
python manage.py migrate
python manage.py collectstatic --noinput
```
`check --deploy` must pass before you continue. Production mode raises
`ImproperlyConfigured` for any weak setting, so read the message and fix the
`.env` value it names.

`/etc/systemd/system/nlschool-api.service`:
```ini
[Unit]
Description=NLSchool API
After=network.target postgresql.service

[Service]
User=nlschool
WorkingDirectory=/srv/nlschool/backend
ExecStart=/srv/nlschool/backend/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 4. Frontend
`NEXT_PUBLIC_API_URL` is baked in at build time, so set it before building.
```bash
cd /srv/nlschool/frontend
npm ci
NEXT_PUBLIC_API_URL=https://api.example.com/api npm run build
```
`/etc/systemd/system/nlschool-web.service`:
```ini
[Unit]
Description=NLSchool web
After=network.target

[Service]
User=nlschool
WorkingDirectory=/srv/nlschool/frontend
Environment=NODE_ENV=production
ExecStart=/usr/bin/env npm run start -- -p 3000 -H 127.0.0.1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nlschool-api nlschool-web
```

## 5. Nginx and HTTPS
`/etc/nginx/sites-available/nlschool`:
```nginx
server {
    server_name api.example.com;
    client_max_body_size 12m;           # supplier documents are limited to 10 MB
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server {
    server_name app.example.com;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/nlschool /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.example.com -d app.example.com
```
Bind both apps to `127.0.0.1` and open only ports 22, 80 and 443 in the firewall.
Never expose PostgreSQL publicly.

## 6. Smoke test on the real domain
1. `curl -I https://api.example.com/api/setup/` returns 200 (not a redirect loop).
2. Sign in, reload a deep link such as `/finance/fees`, and confirm you stay
   signed in.
3. A verification or password-reset email actually arrives.
4. Run the finance flow: fee, subscription, preview, issue, payment, receipt PDF.
5. Open the receipt PDF and an uploaded supplier document.

## 7. Backups (do this before any real data)
Daily database dump and media copy, kept off the server, with a restore test:
```bash
pg_dump -Fc -h db.example.com -U nlschool_user nlschool > /backup/nlschool-$(date +%F).dump
rsync -a /var/lib/nlschool/media/ /backup/media/
# restore test, into a scratch database:
createdb nlschool_restore && pg_restore -d nlschool_restore /backup/nlschool-YYYY-MM-DD.dump
```
A backup that has never been restored is not a backup.

## 8. Updating
```bash
cd /srv/nlschool && # sync new code
cd backend && . .venv/bin/activate && pip install -r requirements.txt
python manage.py migrate && python manage.py collectstatic --noinput
cd ../frontend && npm ci && NEXT_PUBLIC_API_URL=https://api.example.com/api npm run build
sudo systemctl restart nlschool-api nlschool-web
```
Take a database dump before every `migrate`.

## Known limits (staging only)
- No real mobile-money, SMS or WhatsApp provider; mobile-money payments stay
  pending until staff confirm them.
- Accounting, expense and supplier screens (migrations 0003-0005) were not
  reviewed by the author of this guide; have an accountant review the ledger rules.
- No monitoring or error alerting is set up. Check `journalctl -u nlschool-api`.
