# NLSchool

NLSchool is a multitenant school-management SaaS. This repository is a fresh reconstruction from a partial recovery bundle, not a restored copy of the original repository.

## Structure

- `frontend/`: Next.js App Router application with TypeScript and Tailwind CSS.
- `backend/`: Django 5.2 LTS and Django REST Framework API.
- `docs/recovery/`: preserved requirements, source transcript, and recovery notes.

## Recovered backend foundation

The initial Django app includes the recovered custom email-based user model and tenant, membership, campus, academic-year, subscription, invitation, email-action-token, and audit-event models. Initial migrations are included. The API exposes `GET /api/health/`, one-time initial school setup status and creation endpoints, staff invitation endpoints, and Django admin.

The recovered requirements specify invitation-only staff access, verified parent registration and student linking, student enrollment records independent of student login accounts, single-use expiring invitations or activation tokens, and backend-enforced tenant isolation and role permissions. The initial school setup page creates the first tenant and its owner membership; the owner must verify their email before signing in.

The recovery bundle did not include the original settings, API workflows, tests, or Next.js app. The current authentication endpoints are reconstructed; student/guardian relationship and enrollment models, finance ledgers/fees/invoices/payments, and school dashboards have since been implemented in this reconstruction. Finance supports tenant-scoped fee setup, bulk billing, double-entry postings, staff-recorded payments, receipts, statements, reports, and accounting-period controls. Online payment gateways and automated SMS/WhatsApp reminders are not configured; mobile-money payments require staff confirmation.

## Local setup

Use Node.js 22 or newer for the frontend. From this directory:

```sh
nvm use
cd frontend && npm install && npm run dev
```

In another terminal:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

The frontend runs at `http://localhost:3000`; the API health endpoint is `http://localhost:8000/api/health/` and Django admin is at `http://localhost:8000/admin/`.

The backend loads `backend/.env` when Django starts; environment variables already set in the process take precedence. Keep that file private and out of source control. The project defaults to PostgreSQL when no database engine is configured. Set `DJANGO_DB_ENGINE` to `django.db.backends.sqlite3` to use SQLite, or configure `DJANGO_DB_ENGINE`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, and `DJANGO_DB_PORT` for PostgreSQL. The PostgreSQL driver and `.env` loader are included in `backend/requirements.txt`.

## Staff invitations

An active school owner or school admin can create an invitation with `POST /api/tenants/{tenant_id}/invitations/`, using an authenticated API request and a JSON body such as `{"email":"staff@example.com","role":"teacher"}`. Roles are limited to `school_admin`, `accountant`, and `teacher`. The invitation email links to `/invitations/accept`; tokens are stored as hashes, expire after seven days, and are single-use. An authorized owner or school admin can revoke a pending invitation with `DELETE /api/tenants/{tenant_id}/invitations/{invitation_id}/`. Acceptance creates a verified staff account or adds the role to an existing account after the existing password is confirmed.

## Account authentication

All account types use the same email/password sign-in and JWT API. Sign-in returns a short-lived access token and an `HttpOnly` refresh-token cookie; refresh tokens rotate and are blacklisted on logout. Account flows:

- `POST /api/auth/login/` and `POST /api/auth/refresh/` issue and refresh the shared login session; `POST /api/auth/logout/` revokes its refresh token; `GET /api/auth/me/` returns the signed-in account's active school memberships.
- `POST /api/auth/email/verification-request/` sends a verification email; `POST /api/auth/email/verify/` consumes its single-use link. New owners receive this message during initial school setup.
- `POST /api/auth/password-reset/` requests a reset email; `POST /api/auth/password-reset/confirm/` consumes its single-use token, changes the password, and revokes existing refresh sessions.
- Frontend routes: `/login`, `/account/verify`, `/account/verification-request`, `/password-reset`, and `/password-reset/confirm`.

Staff, parents, and students all enter through single-use school invitations; there is no public registration.

## Enrollment, guardians, and student portal

All routes are under `/api/tenants/{tenant_id}/` and tenant-scoped. Owners and school admins manage; teachers and accountants can read.

- `GET/POST students/` and `GET/PATCH students/{id}/` — enrollment records (unique admission number per school). Records exist independently of any login.
- `POST students/{id}/guardians/` (`email`, `relationship`, `is_primary`) — emails a parent invitation. A parent already in the school is linked to the additional child directly. `DELETE students/{id}/guardians/{link_id}/` unlinks.
- `POST students/{id}/portal-invitation/` (`email`) — invites the student to activate one portal account.
- `GET me/children/` (parent) and `GET me/student/` (student) — a parent sees only linked children; a student sees only their own record.

Invitations are accepted at `/invitations/accept` and can be revoked with the existing invitation `DELETE` route.

The frontend page `/school` uses these routes by role: owners and school admins enroll students and send guardian or portal invitations, teachers and accountants get a read-only list, parents see their linked children, and students see their own profile. Run the frontend checks with Node 22 (`npm run lint`, `npx tsc --noEmit`, `npm test`, `npm run build`).

## School finance

Finance screens are under `/finance` and are available to school owners, school admins, and accountants. The tenant-scoped API is under `/api/tenants/{tenant_id}/finance/`.

- **Setup:** configure invoice, receipt, and credit-note numbering; add ledger accounts and payment methods; create, close, and reopen accounting periods. The currency is currently fixed to GHS.
- **Accounting:** manage a tenant-scoped chart of accounts, preview a CSV (`account_code,debit,credit`, optional `description`) or enter balanced opening balances for approval before posting, inspect the posted journal register and general ledger, and view a trial balance. Posted entries are immutable; corrections use reversing entries.
- **Supplier payables:** maintain suppliers and school-configured purchase-tax codes, record multi-line supplier bills with campus cost-centre allocations, calculate configured inclusive or exclusive line tax, securely upload/download private PDF/PNG/JPEG bill documents, post bills to accounts payable, prepare and approve payment vouchers, record bill allocations or supplier advances, reverse posted transactions, and review supplier statements and ageing.
- **Supplier opening balances:** allocate suppliers' debit/credit detail against an already-posted accounts-payable opening import without creating duplicate journal entries. The payables page compares the accounts-payable control account with supplier and expense subledger balances and displays any difference.
- **Direct expenses:** submit expenses for approval, post approved expenses to accounts payable, record full or partial payments from a selected asset account, and review expense reports.
- **Fees and billing:** create academic years, fee categories and schedules, discounts, scholarships, exemptions, optional services, per-unit usage, invoice previews, bulk draft invoices, and batch issue.
- **Collections:** record staff-entered payments, confirm mobile-money receipts after external verification, allocate advances, create receipts/PDFs, and manage reversals, refunds, and credit notes.
- **Reports:** dashboard collections, outstanding and overdue balances, student advances, statements, ledger transactions, a trial balance, supplier payables ageing, expense reports, and account-level ledger activity.
- **Reminders:** review overdue students and whether guardian phone numbers are available. No SMS/WhatsApp provider is configured, so reminders are not sent.

Online checkout and automatic payment-provider callbacks are not enabled. Payments are recorded by staff, and mobile-money payments remain pending until an authorized finance user confirms them. Provider integrations require selecting a provider and configuring its credentials and webhook verification before they can be enabled.

Bill attachment storage uses private local media files by default and is suitable only for development or self-hosted deployments with protected, persistent storage. Set `DJANGO_MEDIA_ROOT` to a protected filesystem location; do not expose that directory as a public static/media URL. Production deployments should configure private object storage and backups before enabling financial uploads. Tax rates and posting accounts are entered by school admins; no jurisdictional statutory rates are assumed. Opening-balance CSV preview checks the school chart of accounts and debit/credit balance but does not create or post an import until a finance user reviews the lines.

The backend reads `backend/.env` and now accepts Django's standard `EMAIL_*` variable names as well as the earlier `DJANGO_EMAIL_*` aliases. Gmail SMTP on port 587 uses `EMAIL_USE_TLS=True` and `EMAIL_USE_SSL=False`; port 465 uses SSL instead, and both must not be enabled together. Store a Google App Password in `EMAIL_HOST_PASSWORD`, never a regular Gmail password. `EMAIL_HOST_USER` should be the sending Gmail account and `DEFAULT_FROM_EMAIL` a permitted sender address. Set `NLSCHOOL_FRONTEND_URL` to the frontend origin so emailed account links open the correct application; it is also the default allowed CORS origin. Set `CORS_ALLOWED_ORIGINS` to a comma-separated list if more than one frontend origin is required. SMTP connection/authentication was tested successfully; no email was sent as a test. In debug mode, the console backend remains the default only when no email backend is explicitly configured.

### Production configuration and release checklist

The application has production-mode configuration and deployment checks, but that is not a certification of a specific live deployment. A real release still needs an operator to verify the target database, TLS termination, private file storage, backups, email delivery, and restore procedure.

Set `DJANGO_ENV=production` and configure these environment variables through the deployment platform's secret manager (do not commit them):

- `DJANGO_SECRET_KEY` (at least 50 characters), `DJANGO_DEBUG=false`, and `DJANGO_ALLOWED_HOSTS` (comma-separated API hostnames; no wildcard or local development hosts).
- `DJANGO_DB_ENGINE=django.db.backends.postgresql`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, and `DJANGO_DB_HOST`. PostgreSQL connections use TLS with `DJANGO_DB_SSLMODE=verify-full` by default; `verify-ca` is also accepted. Configure the database CA certificate where required by the provider.
- `NLSCHOOL_FRONTEND_URL` (one HTTPS origin), plus `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` when additional HTTPS frontend origins are needed.
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS` or `EMAIL_USE_SSL` (never both), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL` for authenticated SMTP.
- `DJANGO_MEDIA_ROOT` as an absolute directory outside the application tree. It must be persistent, private to the application, backed up, and not served directly by the web server. The current storage backend is local filesystem storage, so single-node storage is not automatically suitable for a multi-instance deployment.
- `NLSCHOOL_SETUP_TOKEN` only if the one-time initial school setup flow is still needed. Keep it secret; the endpoint becomes unavailable after initial setup.

Terminate HTTPS at a trusted reverse proxy/load balancer. Set `DJANGO_SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO` only when that proxy removes any client-supplied forwarded-protocol header and sets the trusted value itself. Do not enable `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` unless every subdomain is HTTPS-only; HSTS preload is intentionally disabled and requires a separate domain-wide eligibility review.

For each release, use a single deployment job to take and verify a database backup, apply migrations, collect static files, run deployment checks, and then start the application. From `backend/`:

```sh
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py check --deploy
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --access-logfile - --error-logfile -
```

Run migrations once per release, not concurrently from every application instance. The production static-file middleware is WhiteNoise. Confirm the application health endpoint and a finance workflow after release. Configure automated encrypted database and private-media backups outside the application host, define retention and access controls, and regularly rehearse restoring both into an isolated environment. A successful `check --deploy` or test suite does not replace these operational checks or a PostgreSQL-backed deployment smoke test.

### Backend setup in Windows PowerShell

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py test
python manage.py runserver
```

For PostgreSQL, configure the database variables in the PowerShell session before `migrate`:

```powershell
$env:DJANGO_DB_ENGINE = "django.db.backends.postgresql"
$env:DJANGO_DB_NAME = "nlschool"
$env:DJANGO_DB_USER = "nlschool"
$env:DJANGO_DB_PASSWORD = "<set this securely>"
$env:DJANGO_DB_HOST = "localhost"
$env:DJANGO_DB_PORT = "5432"
```

In a separate PowerShell terminal, with Node.js 22.13 or newer installed:

```powershell
cd frontend
npm install
npm run dev
```

Set `DJANGO_SECRET_KEY` to a private value before any non-local deployment. In production, also set `NLSCHOOL_SETUP_TOKEN` to a private, one-time setup secret and enter it in the initial setup form. The endpoint is disabled outside debug mode when that secret is not configured, and it becomes unavailable once the first school is created. The default settings are for local development only.