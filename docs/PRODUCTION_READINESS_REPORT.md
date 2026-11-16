# NLSchool Production Readiness Preflight

**Review type:** Local repository/configuration inspection plus public,
non-mutating HTTPS/HTTP GET and HEAD checks. No shell access to production.  
**Decision:** **REQUIRES MANUAL VERIFICATION**. NLSchool is not approved for
private deployment or a pilot until the blockers and checks below are resolved.

## Scope and evidence

This review inspected the local `backend/`, `frontend/`, `docs/DEPLOYMENT.md`,
`README.md`, and `docs/DEVELOPMENT_STATUS.md`. No local SSH config exists and
the Docker CLI is unavailable, so no production shell, Docker daemon, reverse
proxy configuration, or database was inspected. Public GET/HEAD requests and
a TLS handshake were made to the supplied production domain. No production
state was changed. Secrets and credential values were not inspected or
included here.

The local Git checkout is on branch `main` at `c3222d4`, with many modified and
untracked files. This is not a clean, identified release artifact; the
production server's deployed revision and its similarity to this checkout are
unknown. Do not build or deploy from this worktree as-is.

## 1. Current deployment architecture

From the local Compose files and the supplied environment description:

| Component | Local Compose definition | Local binding | Runtime |
|---|---|---|---|
| Backend | `backend/docker-compose.yml`; container `nlschool-backend` | `127.0.0.1:8020` → container port `8000` | Gunicorn, 3 workers, 120-second timeout; restart `unless-stopped` |
| Frontend | `frontend/docker-compose.yml`; container `nlschool-frontend` | `127.0.0.1:3020` → container port `3000` | Next.js standalone Node server; restart `unless-stopped` |
| PostgreSQL | Not defined in either project Compose file | Not published by these files | Supplied shared container `nexlogs-postgres`, expected on external network `nexlogs-internal-network` |

Both Compose projects attach to the same externally managed Docker network.
The backend has no Compose dependency on PostgreSQL and there are no Compose
health checks. Both web containers bind host ports only on loopback; a
CyberPanel/OpenLiteSpeed proxy must be configured to reach them on the host.
The intended same-origin frontend bundle uses `NEXT_PUBLIC_API_URL=/api`.
Accordingly, the proxy must send `/api/` (including `/api/health/`) and Django
admin `/admin/` to the backend and other application paths to the frontend.
The proxy must preserve the path and forward the original `Host`.

The backend Compose file mounts `./staticfiles` at `/app/staticfiles` and
`/var/lib/nlschool/media` at the matching container path; it also mounts an
additional `./media` at `/app/media`. Django's `MEDIA_ROOT` is configured by
`DJANGO_MEDIA_ROOT`; the supplied `.env.example` expects it to be
`/var/lib/nlschool/media`. The Compose command does not run `collectstatic`,
and no startup hook runs migrations. Static collection and migration are
separate operator actions. The extra `/app/media` mount is not the configured
production media path unless `DJANGO_MEDIA_ROOT` is set differently.

The reverse proxy, TLS termination, path routing, forwarded headers, actual
container names/status, network attachments, PostgreSQL service DNS, firewall
and persistent host paths remain unverified. `docs/DEPLOYMENT.md` describes a
separate systemd/Nginx/local-PostgreSQL setup under `/srv/nlschool`; it is not
an operational runbook for the specified Docker/CyberPanel server at
`/opt/nexlogs/nlschool`.

## 2. Production configuration findings

### Confirmed safeguards in local settings

- Production mode requires `DJANGO_SECRET_KEY` (at least 50 characters),
  `DJANGO_DEBUG=false`, explicit non-wildcard `DJANGO_ALLOWED_HOSTS`, an HTTPS
  `NLSCHOOL_FRONTEND_URL`, HTTPS-only configured CORS/CSRF origins, PostgreSQL,
  database connection values, SMTP, and an absolute media directory outside
  the application tree.
- PostgreSQL TLS defaults to `verify-full` in production and rejects
  `disable`. `verify-ca` is also allowed. An optional CA file may be supplied.
- Production enables HTTPS redirect, HSTS for one year, secure session and
  CSRF cookies, content-type sniffing protection, a restrictive referrer
  policy, and `X-Frame-Options: DENY`.
- JWT access tokens are short-lived; refresh tokens rotate and are blacklisted
  on rotation. The refresh cookie is HttpOnly, SameSite=Lax, and secure when
  Django is not in debug mode.
- Django/DRF defaults to authenticated requests; endpoint code applies
  tenant-scoped membership and role checks. Login and email actions have
  scoped rate limits.
- Invitation and email-action tokens are single-use, stored hashed, and
  expiring. The current source contains the user-before-token locking and
  post-lock expiry recheck corrections.
- WhiteNoise and compressed manifest static storage are configured. File
  uploads are subject to configured size and filesystem permission limits.

These are source-code findings, not proof that the server uses these settings.
`DJANGO_SECURE_PROXY_SSL_HEADER` is optional in settings but essential when
TLS terminates at the reverse proxy. With `SECURE_SSL_REDIRECT=True`, failing
to set it correctly can cause redirect loops. The proxy must overwrite rather
than trust a client-supplied `X-Forwarded-Proto`, and send `https` for HTTPS
requests.

### Deployment conditions and risks

- **Public tenant registration:** `POST /api/register/` is a public,
  self-service school-registration endpoint, separately rate-limited to five
  email actions per hour. This is distinct from the one-time setup endpoint.
  If the private deployment is meant to onboard only one approved school,
  explicitly block or otherwise restrict `/api/register/` at the proxy until
  multi-tenant self-service registration is approved. The initial setup
  endpoint is disabled in production unless `NLSCHOOL_SETUP_TOKEN` is set;
  remove/rotate that token after the one-time setup.
- **Rate-limit scope:** no shared cache configuration is present in the
  inspected settings. Django's default in-process cache means these throttles
  are not a single reliable quota across three Gunicorn workers or replicas.
  Do not treat them as a substitute for edge rate limiting.
- **Database connection budget:** persistent connections are enabled with a
  60-second lifetime and health checks. Three Gunicorn workers can hold
  multiple database connections; confirm the shared PostgreSQL capacity,
  `max_connections`, and other applications' peak usage before launch.
- **Single database credential:** the same `DJANGO_DB_USER` setting is used by
  the runtime and management commands. Migration 0021 requires DDL ownership
  on NLSchool's tables. Least-privilege separation between a migration owner
  and a runtime role is not represented by the current settings interface.
  Confirm the actual account is non-superuser, limited to a dedicated
  NLSchool database/schema, and does not have privileges over other Nexlogs
  applications. Do not grant broader shared-server permissions to make a
  migration succeed.
- **SMTP:** production settings require a TLS/SSL SMTP backend and host, but
  do not prove credentials, sender-domain authorization, deliverability or
  outbound network access. Verification and password-reset flows depend on
  working email.
- **Static/media:** verify `DJANGO_MEDIA_ROOT` exactly matches a persistent,
  private mount. Run and verify `collectstatic` before exposing Django admin.
  Uploaded documents must only be available through authorized application
  routes; do not make the media tree a public static alias.
- **Logging/health:** Gunicorn access/error logs go to container stdout/stderr
  at `info`, which is suitable for container collection, but no retention,
  alerting, structured application logging or metrics configuration is in
  these Compose files. `/api/health/` returns a constant JSON status; it does
  not verify database, migrations, SMTP, or file storage. A healthy response
  alone is not a readiness check.
- **Scheduled work:** the repository includes an `expire_subscriptions`
  command and manual subscription activation/admission-audit commands. No
  scheduler, Celery worker, cron container, or timer is defined in Compose.
  Decide and verify how subscription expiry and backups are scheduled; do not
  schedule mutating jobs from an unverified old code revision.
- **Container hardening/reproducibility:** the backend Dockerfile does not
  set a non-root `USER`; image tags are floating minor/patch tags, and Python
  requirements are ranges rather than a fully locked requirements file. The
  frontend uses `npm ci` and a lockfile, but the Node base tag is also not
  digest-pinned. Review image provenance, patch policy, file ownership,
  resource limits and container log rotation before production.

## 3. Database and migration readiness

The local migration graph currently ends at:

- Academics: `0021_class_campus_freeze`
- Core: `0007_term_closure_headteacher`
- Finance: `0008_feeschedule_class_level`

Django's built-in auth, admin, sessions, contenttypes and token-blacklist
migrations are also required. These are expected code targets only: the
production database's `django_migrations` state is unknown.

Migration 0021 is a PostgreSQL-only `RunPython` migration. It adds a hidden
class freeze marker, backfills it from current enrollment rows, and creates
triggers that freeze a class campus after enrollment and validate selected
class/enrollment tenant and campus relationships. It does not add a Django
model field. The migration is technically reversible: its reverse operation
drops both triggers and the marker column. That is **not a safe routine
rollback**: deleted enrollment history cannot be reconstructed, so removing
the marker can lose the permanent freeze state. Treat 0021 as forward-only in
production unless a separately reviewed preservation/recovery procedure is
approved. No migration was applied to production during this review.

The report-provenance protection inspected here adds no schema migration; it
stores verification identifiers in newly reviewed report snapshots and uses
transactional application locks. The existing 0021 triggers protect their
specific PostgreSQL class/enrollment writes, not arbitrary report DML or
publication by old code.

The Compose files point at an external shared PostgreSQL container, but do
not identify the actual target database, schema, role, owner, grants,
`pg_hba.conf`, TLS certificate or server version. A separate **NLSchool
database** on the shared PostgreSQL server is a deployment prerequisite.
Running Django migrations against a database/schema shared with other
Nexlogs applications risks collisions and unintended effects; table prefixes
alone are not adequate isolation because Django also owns a
`django_migrations` table and migration-created functions/triggers. Confirm
the exact database identity and target-only privileges before any migration.
Do not run `migrate` against an ambiguous shared database.

Production-schema verification must confirm the complete migration plan,
that `academics.0021_class_campus_freeze` is applied, and that both
`academics_guard_class_campus` and `academics_freeze_enrolled_class` exist on
the expected tables. Also verify the actual trigger behavior against the
server's PostgreSQL version/schema in an approved staging clone. This
preflight made no production database connection and cannot verify migration
state or permissions.

## 4. Stage 2A production risks

| Risk | Confirmed from local source | Not established for production |
|---|---|---|
| Old report-serving processes | Current report review/publication paths validate campus provenance; publication rejects snapshots without matching verified identifiers. | An older backend instance can still create and publish reports without those checks. No process inventory, routing inventory, image revision, worker drain or scheduled-job inventory was inspected. |
| Direct report writes | Application transactions lock and validate term-relevant enrollment, student, class, academic year and campus; PostgreSQL `NOWAIT` conflicts are retried a bounded three times and then return 409. | Direct ORM, SQL, admin, management, external integrations or old endpoints can bypass application validation and snapshot/version controls. There is no report-provenance database trigger. |
| Campus freeze | Migration 0021 installs PostgreSQL triggers for class-campus freeze and selected enrollment/class campus/tenant checks, and the migration-built PostgreSQL test suite exercises direct ORM/SQL and concurrency paths. | Production migration application, trigger presence, grants, other writers, trigger disabling and exact schema compatibility are unknown. The trigger does not certify every campus/student mutation. |
| Shared-user corrections | Current local `auth_email.py` and invitation acceptance code contain the previously tested lock-order fixes. | Server image/source revision and all old worker processes are unknown. Do not assume the deployed code contains these changes. |
| Coordinated rollout | Application code preserves term-before-report ordering; current tests use fresh migration-built disposable PostgreSQL databases. | Local documented Stage 2A status remains **NOT READY** pending writer fencing, rollout verification and production-schema checks. The local 280/280 test result does not certify production or every writer/order. |

Before deploying, freeze and identify a reviewed, clean commit/image. Put the
site into maintenance mode or block mutating/report routes at the edge, drain
all backend replicas and in-flight requests, and stop old scheduled jobs and
one-off workers. Confirm no old process can continue serving report,
enrollment, identity or invitation writes. Back up and verify restore before
migration. Apply migrations to the dedicated NLSchool database, verify 0021's
marker/triggers, deploy the same reviewed backend revision to every replica,
then route traffic and run smoke tests. Do not allow old and new report
writers to run concurrently. Plan rollback as a forward fix or an explicitly
approved database restore; do not reverse migration 0021 as an ordinary
application rollback.

## 5. Critical blockers before private deployment

1. **Server is unverified:** inspect actual running container revisions,
   Compose labels, network, proxy routes, TLS, firewall, volumes, environment
   variable names/presence (not values), PostgreSQL identity, and backups using
   authorized read-only access.
2. **No clean deployable artifact:** local source has substantial modified and
   untracked changes. Produce/review a clean commit and immutable backend and
   frontend images; verify that this exact code includes all claimed Stage 2A
   fixes.
3. **Stage 2A is not ready:** approve and rehearse writer fencing and a
   coordinated drain/cutover. Ensure no old app, job or direct writer can
   publish unvalidated reports.
4. **Database isolation/permissions unknown:** establish a dedicated
   NLSchool database and narrowly scoped non-superuser access on the shared
   PostgreSQL server. Confirm migration privileges without granting access to
   other Nexlogs databases. Verify TLS hostname/CA and connection budget.
5. **Proxy and HTTPS unknown:** verify same-origin routing, host preservation,
   HTTPS redirect behavior and trusted forwarded-protocol handling. Keep
   backend/frontend ports loopback-only and PostgreSQL unexposed.
6. **Operational readiness unknown:** verify backup plus an actual isolated
   restore, persistent private uploads, collected static files, SMTP
   delivery, edge throttling, log retention/alerting and a subscription
   expiry schedule.
7. **Onboarding policy:** decide whether `/api/register/` is blocked for the
   private/one-school pilot and securely configure, use once, then remove or
   rotate the initial setup token.

## 6. Recommended deployment sequence

1. **Release preparation:** review Stage 2A blockers; freeze concurrent feature
   work; produce a clean reviewed commit and immutable image tags/digests;
   record the exact revision and rollback artifact.
2. **Read-only server verification:** inspect the running Compose project,
   container versions, external network and bindings, proxy routes, HTTPS
   certificate, disk capacity, database endpoint/version and target database
   grants. Confirm host paths and backups. Never print `.env` or Docker
   environment values.
3. **Staging rehearsal:** use a production-like isolated PostgreSQL clone and
   the intended image. Run `check --deploy`, migration plan, migrations,
   `collectstatic`, health checks, tenant-isolation checks and critical
   identity/admission/finance/report flows. Verify migration 0021 triggers
   and restore from backup.
4. **Backup gate:** enter the approved maintenance window, stop/drain
   mutating writers and scheduled jobs, create a database backup and media
   backup, and verify archive readability plus an actual restore into an
   isolated instance. Do not back up or restore other Nexlogs databases as
   part of this app's operation.
5. **Migration gate:** verify that the connection is the dedicated NLSchool
   database, inspect `migrate --plan`, apply migrations once, then verify the
   migration table and both 0021 triggers. Preserve the pre-migration backup;
   do not reverse 0021.
6. **Deploy:** run `collectstatic`, deploy the reviewed backend and frontend
   images, verify all replicas run the same revision, then configure/confirm
   proxy routing. Do not expose public traffic until all old processes are
   drained and readiness checks pass.
7. **Private smoke test:** test HTTPS, `/api/health/`, login/refresh/logout,
   email verification, invitation acceptance, tenant isolation, student
   enrollment, invoice/payment/receipt, report review/publication and
   authorized document download with approved test data. Confirm logs,
   alerts, backups and recovery owner.
8. **Pilot gate:** restrict access to the named school and users, disable
   unapproved public registration, limit roles and features, monitor daily,
   and define a rollback/incident contact before real student data is entered.

## 7. Pilot launch checklist

### Private deployment

- [ ] Confirm legal/service owner, approved domains, privacy/data-retention
  obligations and incident contact.
- [ ] Approve a clean release commit and immutable image digests; preserve
  the previous known-good image and database backup.
- [ ] Confirm server OS, Docker/Compose versions, CyberPanel/OpenLiteSpeed
  version, network, host bindings, firewall and available disk/backup space.
- [ ] Configure production-only environment variables privately. Verify
  presence without printing values; ensure `DEBUG=false`, strong secret,
  exact allowed host, HTTPS frontend origin, trusted proxy header, SMTP and
  verified PostgreSQL TLS.
- [ ] Verify `NEXT_PUBLIC_API_URL=/api` was baked into the frontend image.
- [ ] Configure proxy routes for `/api/` and `/admin/` to backend port 8020
  and frontend traffic to 3020; preserve `Host`, set trusted
  `X-Forwarded-Proto`, and verify redirect/cookie behavior.
- [ ] Confirm `/api/register/` policy; restrict it for one-school private
  access unless public tenant signup is expressly approved.
- [ ] Verify independent NLSchool database and least-privilege account,
  TLS CA/hostname, migration permissions and connection limits.
- [ ] Verify Docker restart policy, log retention, monitoring/alerts and
  resource limits; schedule subscription expiry and backups.
- [ ] Confirm upload mount path is persistent/private and static files have
  been collected.

### Backup, migration and access

- [ ] Create a pre-deploy PostgreSQL backup and media backup; record secure
  off-host location and checksum.
- [ ] Restore both into an isolated test environment; verify critical rows,
  files and application startup.
- [ ] Run `check --deploy` and inspect `migrate --plan` against the confirmed
  NLSchool database.
- [ ] Confirm expected core, finance, academics, auth and token-blacklist
  migrations; verify 0021 migration record and both triggers after migration.
- [ ] Never reverse 0021 without a separately approved data-preservation
  plan; do not run migrations against other Nexlogs databases.
- [ ] Drain all old application workers, report writers, scheduled jobs and
  one-off processes before enabling the new report-provenance path.

### One-school pilot workflows

- [ ] Create the first approved school/owner using the protected setup
  procedure; remove or rotate the setup token immediately after use.
- [ ] Verify the owner's email arrives, link is HTTPS, token expires and
  verification is required for login.
- [ ] Invite one administrator and a small set of staff; verify role limits,
  token expiry/revocation and tenant isolation.
- [ ] Admit test students, create classes and enrollments; confirm campus
  freeze behavior and no unsupported cross-campus transfers.
- [ ] Configure fee schedules; preview and issue invoices; verify totals,
  numbering, audit trail and term-closure rules.
- [ ] Record a test payment, confirm allocation and receipt PDF, and verify
  no payment gateway is implied (mobile-money payments require staff
  confirmation).
- [ ] Review and publish a report only with complete term enrollment/campus
  provenance; verify revisions create new reviewed versions and published
  snapshots remain unchanged.
- [ ] Confirm parent/student visibility is tenant-scoped and only published
  reports/documents are accessible where policy allows.
- [ ] Review errors, lock conflicts, 409 retries, email failures, database
  capacity, backup completion and restore status daily during pilot.

### Keep disabled or restricted

- [ ] Public school registration unless self-service multitenant onboarding
  has been explicitly approved.
- [ ] Unapproved administrator/staff invitations and broad role assignment.
- [ ] Cross-campus transfers; the workflow is intentionally unsupported.
- [ ] Transfer-aware report issuance and provenance-attestation-linked
  publication, which remain gated.
- [ ] Any direct SQL, manual report edits, or scripts that bypass application
  review/audit workflows.
- [ ] Online payment claims or automated SMS/WhatsApp notifications; those
  providers are not configured in the inspected repository.

## 8. Exact commands for a future approved run (NOT EXECUTED)

The following are templates for an authorized server operator after the
blockers above are cleared. **None was executed during this read-only
preflight.** Replace placeholders only after verifying the target. Never put
passwords or tokens on a command line or paste environment output into logs.

### A. Read-only host and Compose inventory

```sh
cd /opt/nexlogs/nlschool
git rev-parse HEAD
git status --short
docker ps --filter name=nlschool --filter name=nexlogs-postgres \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network inspect nexlogs-internal-network \
  --format '{{.Name}} {{range .Containers}}{{.Name}} {{end}}'
docker inspect --format '{{.Name}} {{.State.Status}} {{json .NetworkSettings.Ports}}' \
  nlschool-backend nlschool-frontend nexlogs-postgres
docker compose -f backend/docker-compose.yml config --services
docker compose -f frontend/docker-compose.yml config --services
```

Do not run unrestricted `docker inspect` or `docker compose config` into a
terminal/log: rendered environment configuration may expose secrets. Inspect
only selected metadata and verify secret presence through an approved secret
manager without displaying values.

### B. Backup and restore gate

```sh
umask 077
docker exec nexlogs-postgres pg_dump \
  --format=custom --no-owner --no-acl \
  --username='<NLSCHOOL_BACKUP_ROLE>' --dbname='<NLSCHOOL_DATABASE>' \
  > /secure/backup/nlschool-predeploy.dump
tar -C /var/lib/nlschool/media -czf /secure/backup/nlschool-media-predeploy.tar.gz .
pg_restore --list /secure/backup/nlschool-predeploy.dump
```

The PostgreSQL authentication method and role are unknown; verify them first
and use an approved protected password file or local auth mechanism, never a
password argument. `pg_restore --list` checks archive readability only. Also
restore the database and media into an isolated disposable environment and
exercise application reads before proceeding.

### C. Pre-migration checks and migration

```sh
cd /opt/nexlogs/nlschool
docker compose -f backend/docker-compose.yml run --rm backend \
  python manage.py check --deploy
docker compose -f backend/docker-compose.yml run --rm backend \
  python manage.py showmigrations
docker compose -f backend/docker-compose.yml run --rm backend \
  python manage.py migrate --plan
```

Only after verifying the selected connection is the dedicated NLSchool
database, obtaining approval, and completing the backup/restore gate:

```sh
docker compose -f backend/docker-compose.yml run --rm backend \
  python manage.py migrate --noinput
docker compose -f backend/docker-compose.yml run --rm backend \
  python manage.py collectstatic --noinput
```

Then verify migration 0021 and triggers using a read-only query on the
confirmed target database (adapt the authentication mechanism and target
placeholders):

```sql
SELECT app, name, applied
FROM django_migrations
WHERE (app = 'academics' AND name = '0021_class_campus_freeze')
   OR (app = 'core' AND name = '0007_term_closure_headteacher')
   OR (app = 'finance' AND name = '0008_feeschedule_class_level');

SELECT tgname, tgrelid::regclass
FROM pg_trigger
WHERE NOT tgisinternal
  AND tgname IN ('academics_guard_class_campus', 'academics_freeze_enrolled_class');
```

Do not issue the SQL until the target database is independently confirmed.
It is a verification query, not a substitute for application and permission
checks.

### D. Deploy and smoke test

Only after the approved cutover, migration, static and drain gates:

```sh
cd /opt/nexlogs/nlschool
docker compose -f backend/docker-compose.yml up -d --build backend
docker compose -f frontend/docker-compose.yml up -d --build frontend
curl --fail --show-error https://nlschool.nexlogssolutions.com/api/health/
curl --fail --show-error --head https://nlschool.nexlogssolutions.com/
```

These Compose commands can recreate containers and the database command can
modify schema; they are **NOT EXECUTED** and require a separately authorized
maintenance window. The health endpoint currently checks only that Django
answered; perform authenticated workflow and database checks as a supervised
pilot, not as an automated unauthenticated write probe.

## 9. Production server verification (read-only, 2026-11-12)

### Access and method

**NOT VERIFIED:** SSH access was unavailable from this workspace: no local
`~/.ssh/config` is present. The Docker CLI is not installed here. No SSH
connection, remote shell, `docker` command, production SQL session, server
backup listing, or filesystem inspection was attempted. The preflight did
not inspect credentials or print secret values.

The following checks were external, non-mutating requests to the specified
public hostname. They establish public endpoint behavior only, not which
container, host port, source revision or database served the request.

### Actual check results

| Check | Status | Finding |
|---|---|---|
| Deployed Git branch/commit and dirty state | **NOT VERIFIED** | The only known local checkout is `main` at `c3222d4` and has many modified and untracked files. This is not evidence of the server commit. |
| Whether deployed commit includes Stage 2A fixes | **NOT VERIFIED** | No server code, image label/revision, or container filesystem was inspected. |
| Backend/frontend container status, health and restart policy | **NOT VERIFIED** | No Docker CLI or host access. |
| Multiple old backend processes/containers | **NOT VERIFIED** | No process or container inventory was available. |
| Docker network membership and internal connectivity | **NOT VERIFIED** | No container/network inspection was performed. |
| Persistent media/static mounts and upload permissions | **NOT VERIFIED** | No host/container mount or file permission inspection was possible. |
| Public HTTPS application routes | **VERIFIED** | HTTPS `HEAD /` returned 200 with Next.js response headers; HTTPS `GET /api/health/` returned `{"status":"ok","service":"nlschool-api"}`. This health view is a static application response and does not prove database readiness. |
| Public proxy identity/routing behavior | **VERIFIED (limited)** | Responses identify `LiteSpeed`; `/` returned Next.js headers and `/api/health/` returned the Django health JSON. Exact upstream addresses, ports, rewrite rules and trusted forwarded headers remain **NOT VERIFIED**. |
| TLS certificate hostname/chain and validity | **VERIFIED** | Normal certificate-validating HTTPS requests succeeded. Certificate subject/SAN is `nlschool.nexlogssolutions.com`, issuer Let's Encrypt YE1; validity observed from 2026-10-31 09:24:36 GMT through 2027-01-29 09:24:35 GMT. Expiry is not imminent on the check date, but renewal monitoring is unverified. |
| HTTP-to-HTTPS behavior | **FAILED (inconsistent)** | `HEAD http://.../api/health/` returned 301 to the HTTPS health URL. `HEAD http://.../` returned 200 from LiteSpeed/Next.js, not an HTTPS redirect. Plain-HTTP site content is therefore publicly served at the root path. Correct this at the proxy before private deployment; this review made no change. |
| Production database identity, version and isolation | **NOT VERIFIED** | No database connection was made. Intended database name, host, user, role attributes and separation from other Nexlogs databases are unknown. |
| Applied migration state, including 0021 | **NOT VERIFIED** | No `django_migrations` query or `showmigrations` command was run on production. |
| Campus freeze triggers, enabled state and exact definitions | **NOT VERIFIED** | Production trigger existence, enablement and function definitions were not queried. |
| Old code using reset/verification and invitation/admission locking | **NOT VERIFIED** | Local source contains the fixes, but deployed code and live processes are unknown. |
| Backups, last success, off-host copy and restore test | **NOT VERIFIED** | No backup directory, scheduler, object store, backup logs or restore evidence was inspected. |
| `docs/DEVELOPMENT_STATUS.md` Stage 2A gate | **VERIFIED (repository record)** | The local status document says Stage 2A remains **NOT READY** pending fencing/rollout and related verification. This is not server evidence. |

### Critical blockers and next action

1. **FAILED HTTP root redirect:** Configure/verify a universal HTTP-to-HTTPS
   redirect at the approved CyberPanel/OpenLiteSpeed layer, then repeat GET
   and HEAD checks for `/`, `/api/health/` and `/admin/`. Do not expose login,
   registration or admin over plain HTTP.
2. **No server access evidence:** An authorized operator must execute the
   read-only commands below and return redacted outputs. In particular,
   establish the exact deployed commit/image revision and whether the tree is
   clean; verify every backend replica runs one reviewed revision and no old
   processes remain.
3. **Database and 0021 remain unknown:** Confirm the NLSchool database is
   dedicated, its role is appropriately scoped, expected migrations are
   applied, and the two enabled triggers and trigger functions match the
   reviewed migration. Do so in a read-only transaction against the
   explicitly confirmed database.
4. **Backups are unknown:** Inspect existing schedule and history without
   starting a backup. Require an off-host backup and a documented successful
   restore rehearsal in an isolated temporary database before migration or
   pilot data.
5. **Stage 2A is still NOT READY:** Do not enable private pilot writes until
   writer fencing, full process drain, migration/trigger verification and
   coordinated cutover have been approved and rehearsed.

### Safe manual server inspection commands (NOT EXECUTED)

Connect only using an authorized account and known server address. The
following commands are read-only; do not paste secret values, `.env`
contents, full Docker environment configuration, customer data or private
keys into output.

**Git and process/container inventory:**

```sh
cd /opt/nexlogs/nlschool
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --short --untracked-files=normal

docker ps -a --no-trunc \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker inspect --format \
  'name={{.Name}} image={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restart={{.HostConfig.RestartPolicy.Name}} ports={{json .NetworkSettings.Ports}} mounts={{range .Mounts}}{{.Source}}:{{.Destination}}:rw={{.RW}};{{end}}' \
  nlschool-backend nlschool-frontend nexlogs-postgres
docker network inspect nexlogs-internal-network \
  --format '{{.Name}} {{range .Containers}}{{.Name}} {{end}}'
docker top nlschool-backend -eo pid,comm
```

Compare container image IDs/digests and any non-secret OCI revision labels
with the approved release record. `git status` must be clean for the deployed
checkout unless every local change is individually reviewed and intentionally
part of the release. `docker ps -a` output must be checked for old NLSchool
containers, not just the two expected names. `docker top` lists process names
only, not full command-line arguments.

If environment names must be checked, print **names only**, never values:

```sh
docker exec nlschool-backend sh -c \
  'tr "\000" "\n" < /proc/1/environ | sed "s/=.*//" | sort'
```

Inspect proxy configuration only through the authorized CyberPanel/
OpenLiteSpeed interface or a reviewed, read-only command that prints routing
directives only. Do not dump whole configuration files that may contain
credentials. Verify HTTP-to-HTTPS redirect for every route, HTTPS host
preservation, `/api/` and `/admin/` routing to backend host port 8020,
frontend routing to host port 3020, and a trusted `X-Forwarded-Proto`.

**Dedicated database identification and trigger/migration verification:**

Use the approved read-only database role and confirm the database name
independently before running SQL. The example uses interactive `psql`; do not
put a password in a command argument or shell history. Run in one session:

```sql
BEGIN READ ONLY;

SELECT current_database() AS database_name,
       current_user AS connected_role,
       inet_server_addr() AS server_address,
       inet_server_port() AS server_port,
       version() AS postgres_version;

SELECT app, name, applied
FROM django_migrations
WHERE app IN ('academics', 'core', 'finance', 'auth', 'admin',
              'contenttypes', 'sessions', 'token_blacklist')
ORDER BY app, applied, name;

SELECT t.tgname, t.tgenabled, t.tgrelid::regclass AS table_name,
       pg_get_triggerdef(t.oid) AS trigger_definition,
       p.proname AS function_name,
       pg_get_functiondef(p.oid) AS function_definition
FROM pg_trigger AS t
JOIN pg_proc AS p ON p.oid = t.tgfoid
WHERE NOT t.tgisinternal
  AND t.tgname IN ('academics_guard_class_campus',
                   'academics_freeze_enrolled_class')
ORDER BY t.tgname;

ROLLBACK;
```

The expected target is a **dedicated NLSchool database**, not another
Nexlogs application's database. Compare both trigger function bodies with
`backend/academics/migrations/0021_class_campus_freeze.py`; check `tgenabled`
is `O` (enabled for origin sessions). Do not select or display application
records. A DBA should separately verify database/role grants without
expanding permissions or exposing other applications' data.

**Backup history (read-only):**

First identify the approved backup directory and schedule without printing
credentials from scripts or environment files. Then list metadata only:

```sh
systemctl list-timers --all --no-pager
find <approved-backup-directory> -maxdepth 2 -type f \
  -printf '%TY-%Tm-%TdT%TH:%TM:%TS %s %p\n' | sort
```

Confirm successful job logs, off-host replication and restore-test evidence
through the backup system's read-only interface. Do not initiate `pg_dump`,
`pg_restore`, a backup job, or a restore during this verification.

## Final decision

**REQUIRES MANUAL VERIFICATION.** Public HTTPS is responding and its observed
certificate is valid, and the API health route works. However, the public
root route does not redirect from HTTP, while all host/container/database/
trigger/backup facts remain unverified because no SSH or Docker access is
configured in this workspace. Stage 2A remains **NOT READY**. Do not deploy
or begin the pilot until the HTTP redirect is corrected, the manual server
checks above pass, backups are restored successfully in isolation, and a
fully fenced no-old-writer cutover is approved.
