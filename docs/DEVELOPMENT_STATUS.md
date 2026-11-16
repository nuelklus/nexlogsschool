# NLSchool Development Status

Details and evidence live in the root [README.md](../README.md), section "Stage 2A remaining cross-module audit". This file records status only.

## Stage 2A cross-module concurrency

**Implementation decision: NOT READY.** Steps 1–3 below describe application corrections, but direct database writers and old processes are not fenced, and the complete coordination rollout is not verified.

**Finalization is the current highest development priority.** The historical
recommendation below to continue with an independent milestone is superseded
by the Stage 2 Finalization request and the findings at the end of this file.

### Verified results
- The invoice/term-closure and report publish/withdraw row-lock corrections are implemented and covered by PostgreSQL concurrency tests.
- Step 3 writer-bypass tests pass on fresh disposable PostgreSQL databases; the invitation, admission/finance, report and closure regressions are retained and rerun as noted below.
- `manage.py check` and `makemigrations --check` are clean.
- No migration was created or applied; migrations 0018–0020 remain unapplied.

### Open issues
- Direct SQL or external database writers are not protected by application row locks or advisory-lock helpers.
- Old application processes and external schedulers are not fenced.
- The tenant advisory lock is a foundation and test-proven helper, not a universal application writer fence.
- Test schemas are model-built, not migration-built.
- Transfer-aware report issuance remains disabled.

### Recommended next action
Continue with an independent product milestone while keeping concurrency and transactional provenance validation on the release-readiness list. Before relying on the lock protocol, approve and execute a coordinated writer-fencing rollout.

## Stage 2A step 1: invoice creation vs term closure (implemented, Stage 2A still NOT complete)

- **Defect:** `finance.billing.generate_invoice` checked `is_term_closed` with a plain read after taking `FinanceSettings`; term closure locks the term and year rows but never `FinanceSettings`. A term completed between the check and the invoice INSERT, so an invoice was created for a closed term (confirmed by audit test B1).
- **Correction:** `academics.closure.is_term_closed(..., lock=True)` locks the term row `FOR UPDATE OF self` (no `is_completed` filter, so a stale snapshot still waits) and reads `is_completed` from the locked row. `generate_invoice` uses it, so the lock is held until the invoice transaction ends. Order is FinanceSettings, then term; closure never takes FinanceSettings, so no inversion. Only the one tenant's term row is locked; no advisory or global lock. Default `lock=False` keeps other callers unchanged.
- **Files changed:** `backend/academics/closure.py`, `backend/finance/billing.py`, `backend/academics/test_cross_module_workflow_races.py` (B1/B2 rewritten from defect characterisation to the two fixed schedules; B3 tenant isolation added).
- **PostgreSQL tests:** 15 tests in the race module passed on two fresh disposable databases, no 40P01. Schedule A (invoice first): closure waits on the term lock, invoice 201, then closure 200, term completed. Schedule B (closure first): invoice waits, then is rejected 409 with no invoice, line or journal rows. B3: another tenant's term lock is not blocked.
- **Regressions:** 19 identity and admission/finance tests pass; `academics.test_closure` (11) passes; `manage.py check` clean; `makemigrations --check` no changes. `manage.py test finance` shows 6 errors in `FinanceConcurrencyTests` / `OptionalFeeConcurrencyTests` (`column "class_level_id" of relation "finance_feeschedule" does not exist`); they occur identically with the fix reverted and pass when run alone, so they are a pre-existing test-ordering problem, not investigated here.
- **Remaining risks:** invoice creation now blocks other term-lock holders (marks, transfers) for the invoice transaction; other finance paths that read term state are unreviewed; unfenced commands/old processes/direct SQL and the model-built schema remain open. The report publication/withdrawal lock-order issue is addressed in Step 2 below. No migrations created or applied.

## Stage 2A step 2: report publication and withdrawal lock order (implemented, Stage 2A still NOT complete)

- **Root cause:** review and term closure lock the term first, but publication and withdrawal locked the report first and then the term. The inverse acquisition order could form a cycle with a term-first workflow that subsequently needs the same report. Publication/withdrawal also needed to validate current report and term state after any wait.
- **Correction:** both operations now use a shared helper that performs an unlocked, tenant-scoped lookup of the report's term ID, locks that term row, then locks the report row and verifies that the report still belongs to that term. Status and completion checks happen against the locked, refreshed rows inside the existing atomic transaction. Review and closure already take the term first. The resulting order is **term → report** for review, publication, and withdrawal; closure takes **term/year → closure**. No advisory/global lock was introduced. Existing permissions, provenance blocks, snapshot validation, versions and audit writes are unchanged.
- **Files changed:** `backend/academics/report_cards.py`; new `backend/academics/test_report_publication_concurrency.py`; this status document. No finance/admission implementation changed; the Step 1 invoice-vs-closure fix was not modified.
- **PostgreSQL race tests:** all 7 focused tests passed on two fresh disposable PostgreSQL databases, with deterministic synchronization and observed lock waits; no SQLSTATE 40P01. Publication-first/withdrawal-second yielded 200/400 and preserved a published version plus its audit event. Withdrawal-first/publication-second yielded 200/400 and preserved a withdrawn version plus its audit event. Review/publication races in both orders serialized and rechecked status. Publication/term-closure races in both orders serialized; publication succeeds before closure, or is rejected after closure. A forced audit failure returned 500 and rolled back the report status change with no lost/extra audit record.
- **Regression tests:** 55 report lock-order, provenance, report batch, term-closure, invitation deadlock and shared-user tests passed on a fresh disposable database. `manage.py check` is clean; `makemigrations --check --dry-run` reports no changes; `git diff --check` is clean. No migrations were created or applied.
- **Remaining Stage 2A risks:** model-built rather than migration-built test schemas; production schema and triggers unverified; management commands, old application processes, admin and direct SQL writers not fully fenced; other finance/academic lock interactions and rollout controls remain unresolved. Transfer-aware report issuance remains disabled. **Stage 2A is not complete.**

## Stage 2A step 3: writer inventory and bypass protection (application changes implemented; Stage 2A NOT complete)

### Writer classification

Classification is about the particular invariant each path must preserve; it does not imply a universal tenant advisory lock.

| Writer | Classification and current protection |
|---|---|
| Student admission endpoint | **Transaction + row locks:** academic year → class → tenant row through `next_admission_number`; student, enrollment and audit writes commit together. Sequence/number uniqueness uses the tenant and sequence row constraints/locks. It does not take the advisory helper. |
| `audit_admission_numbers --assign-blank` | **Bypass corrected:** initial scan is diagnostic only; per candidate it now locks tenant → student, rechecks blank state after waiting, then reserves a number and writes student + audit in one transaction. |
| Enrollment, transfer, promotion, attendance | **Transaction + resource row locks** in their actual endpoints; transfers and attendance lock terms before student/enrollment rows. No general admin write route bypasses these endpoints. |
| Invoice creation and term closure/reopen | **Transaction + compatible row locks:** invoice holds FinanceSettings → term until commit; closure locks term/year → closure. The closed-state check reads after waiting. This is the preserved Step 1 correction. |
| Finance payments/allocation, issue/void, journals | **Finance row-lock strategy:** finance writers use FinanceSettings and affected payment/invoice rows; AP, expenses, supplier bills and opening balances use FinanceSettings then their document rows; journal posting uses FinanceSettings then journal writes. Period/account/supplier/tax-code setup paths have partial or no row locks. Receipt/report paths are reads except initialization/expiry maintenance. These do not all take the academic advisory helper. |
| Marks and report review/publish/withdraw | **Transaction + term-first row locks:** term → examination/results or term → report. This is the preserved Step 2 correction. Publication and withdrawal recheck status on locked rows. |
| Provenance attestation/revocation | **Transaction + academic row locks:** term first, then student/enrollment or attestation, with audit changes in the transaction. Provenance-linked report publication remains blocked. |
| Subscription activation | **Indirect application workflow:** admin renewal and `activate_subscription` command call the service, which locks Tenant → live subscription and writes audit in the transaction. |
| Subscription expiration | **Conditional row update:** web access checks and `expire_subscriptions` command use the same expiry predicates and PostgreSQL updates; activation additionally serializes on Tenant and re-evaluates expired/live rows. No direct SQL caller was verified. |
| New-school trial assignment | **Indirect:** onboarding calls `start_trial_subscription` inside its outer atomic create-school transaction for a newly created tenant. It is not a general existing-tenant subscription edit path. |
| TenantSubscription admin | **Bypass corrected:** direct add/edit/delete is now denied. The changelist renewal action remains and calls `activate_subscription`. |
| SubscriptionPlan admin | **Unsafe edit surface restricted:** an existing plan's code is readonly, and plan deletion is denied; code changes could race trial-plan lookup or invalidate the fixed trial code. Other plan fields remain editable as platform-wide catalogue maintenance. |
| ClassLevelCatalogue admin | **Not a tenant lock participant:** this is global reference data; code is readonly and deletion is already denied. Its edits do not mutate a tenant's academic records. |
| Other management/background writers | Repository inspection found only `audit_admission_numbers`, `activate_subscription`, and `expire_subscriptions` management commands; no Celery/RQ/scheduler tasks, Django signals, or `on_commit` database writers. Activation/expiry classifications are above. External cron/schedulers remain unknown. |

### Bypasses corrected and evidence

- **Admission-number command stale candidate:** command candidate IDs were selected before the transaction and then updated without a per-student lock/recheck. It now takes the same tenant row reservation lock used by admission, locks that student, and skips if another writer filled or removed the candidate before its turn. The number reservation, student update and audit event are atomic. This avoids overwriting a manually/admission-assigned number and avoids consuming a sequence number for a stale candidate.
- **Subscription admin bypass:** raw subscription CRUD could evade service validation, row locking and audit history. Admin add/change/delete are denied; renewal remains through the service. Existing plan code changes/deletes are denied in admin, preventing a rename/delete race against trial lookup and subscription FK creation.
- **Real PostgreSQL tests:** `core.test_writer_bypass_protection` (5 tests) passed twice on fresh disposable model-built databases. Tests invoke the management command and admin views; they verify the command waits on the tenant lock and rechecks a candidate, command rollback restores student/sequence/audit state, another tenant proceeds independently, plan code tampering is ignored and deletion denied, and direct subscription edit is denied.
- Existing admin renewal action tests remain; the cross-module test H2 was updated to assert a trial code cannot be renamed through the actual admin form.

### Remaining writer and fencing limits

- Application endpoints do not universally acquire `acquire_academic_coordination`; this helper is still a foundation, not a global fence. Writers above are protected only by their described row-lock strategy, not by the helper.
- SQL clients with table write privileges can bypass Django entirely. To claim database-level protection, the app must use a non-owner, least-privilege role and direct writers must be revoked or constrained by an approved database-enforced interface/protocol. A trigger/permission/role change has **not** been made; obtain approval before any such database change. The configured database owner role and external SQL integrations have not been verified here.
- Old application instances may continue the former lock orders during a rolling deployment. Do not rely on the new ordering until all web workers and scheduled/management writers are upgraded and drained. Recommended sequence: freeze writes or use a compatible dual-order release; deploy compatible code to every instance; stop and drain old workers/commands and wait for old transactions to finish; verify active database sessions and scheduled writers; enable/rely on the protocol; monitor lock waits/40P01 and rollback with a coordinated code rollback. Deployment/process controls were not changed or tested.
- Direct command invocation remains possible only to trusted operators; access control to hosts/commands and external schedulers is operational, not enforced by the application code.
- Other global/shared catalogue edits, incomplete finance/admission hierarchy integration, production schema fidelity and transactional provenance validation remain release-readiness items. Transfer-aware report issuance is still disabled.

**Step 3 verification:** `core.test_writer_bypass_protection` passed twice (5 tests each) on separate disposable PostgreSQL DBs. After updating the existing H2 schedule to assert that admin cannot rename the trial plan, the combined 96-test batch passed. It covered subscription admin/service behavior, admission/finance races, invitation concurrency, invoice/closure, report and provenance concurrency, report batches, and closure. `manage.py check` is clean; `makemigrations --check --dry-run` reports no changes; `git diff --check` is clean. No migrations or production changes were made. **Stage 2A is not complete.**

## Stage 2A step 4: deployment and database writer protection (inspection only)

This section records repository configuration, not the state of a running deployment. No production host, container, scheduler, PostgreSQL session, role, grant or database was inspected or modified. Actual image versions, active processes, external integrations, database ownership and privileges remain **unknown**. No old process or scheduler was found by runtime inspection because none was performed; an old instance or external job **could** still write with the previous lock order.

### Processes, identities and writers found in the repository

| Surface | Repository evidence | Write capability and protection |
|---|---|---|
| Backend Compose service | `backend/docker-compose.yml`: `nlschool-backend`, Gunicorn with three workers, restart enabled, `.env`, shared external `nexlogs-internal-network`; host port bound to loopback | Django API and `/admin/` execute writes. Current code protects the specific invitation, invoice/closure and report races with their respective row locks; it does not enforce a universal advisory protocol. Old images could bypass these fixes. |
| Backend systemd alternative | `docs/DEPLOYMENT.md` proposes `nlschool-api`, three Gunicorn workers, Unix user `nlschool`; guide explicitly says it has not been run on a real server | Same Django writers; the guide is not evidence this unit exists or is the only service. |
| Frontend | `frontend/docker-compose.yml` runs the Next.js `nlschool-frontend` service; the systemd guide also proposes `nlschool-web` | No direct PostgreSQL connection was found in the frontend source search. It can initiate writes through the backend API, so blocking only database clients or admin is insufficient. |
| Management commands | `audit_admission_numbers --assign-blank`, `activate_subscription`, `expire_subscriptions` in `backend/core/management/commands/` | Current command code uses the Step 3 protections described above. Older copies, ad hoc invocations and external schedules may not. The first command is read-only without `--assign-blank`; subscription expiry is a writer. |
| Django admin | `/admin/` is mounted in `backend/config/urls.py`; current registrations include subscription plans, tenant subscriptions and the class-level catalogue | Subscription CRUD restrictions and the service-backed renewal action are application-level controls; plan fields other than existing code and catalogue fields remain editable. Admin uses backend database credentials, not a separate database identity. |
| Access checks and maintenance | Request paths call subscription expiry; some finance read/report paths call initialization helpers | Some nominal GET/read flows can write. A rollout freeze must cover these paths, not only POST/PATCH/DELETE. |
| Migrations, shells and other SQL clients | Deployment guide runs `manage.py migrate` and uses `pg_dump`; Django management commands use the configured default connection. Interactive Django shell, `psql`, integrations and scripts are possible if separately authorized | No dedicated management or migration DB alias/role is configured. Data migrations and arbitrary SQL need separate operational review; direct SQL does not automatically follow Django authorization, audit or lock order. |
| Schedulers/workers/integrations | Repository search found no configured Celery/RQ/cron scheduler or additional custom management commands | External cron, hosted jobs, container execs, webhooks, plugins and scripts outside the repository remain unknown; absence from the repository is not proof of absence in deployment. |

`backend/config/settings.py` defines one default database connection from `DJANGO_DB_*`; its development defaults include database `nlschool`, user `nlschool_user` and host `localhost`. Production requires explicit database name, user, password and host, PostgreSQL and verified TLS; the actual values and effective role are unknown. Django keeps connections for up to 60 seconds (`CONN_MAX_AGE`), but this is not a hard deadline for terminating active requests, transactions or external pooler sessions. Compose loads its `.env`; its contents were not read. The staging guide **proposes** creating `nlschool_user` as database owner and running migrations with the same environment; it does not verify actual production ownership, inherited privileges, table ownership, grants or other database users.

### Direct SQL and realistic controls

- Subject to actual privileges, direct clients can insert, update or delete student/admission, term, invoice, payment, report, provenance, subscription or audit rows, or perform DDL. Constraints and foreign keys still apply where present, and ordinary SQL acquires PostgreSQL locks, but neither a constraint nor an automatically acquired row lock forces the application's complete order, permission checks, state revalidation or audit events. The advisory lock is voluntary and is not universally acquired even by current application writers.
- A least-privilege **non-owner** runtime role, a separately controlled migration/owner role, and distinct read-only/reporting and operator identities are recommended after an inventory of actual grants, ownership, role membership, `PUBLIC` privileges, default privileges, schema access, extensions, connection pools and writers. Restrict DDL and direct table DML for identities that do not need them; verify privileges on existing **and future** objects. A database owner, superuser or other sufficiently privileged role can bypass such restrictions.
- Separating roles alone cannot prevent an operator who can obtain or use the application's DML-capable credentials from issuing arbitrary SQL. Stronger database-enforced guarantees would require a separately designed and tested boundary, such as revoking direct DML and granting narrowly scoped stored procedures, or carefully designed constraints/triggers; every permitted writer must use that boundary. Triggers must account for transaction ordering, deferred checks, bulk writes, rollback and performance; neither triggers nor procedures are implemented or shown to enforce the complete protocol. Treat them as options requiring design and approval, not existing protection.
- PostgreSQL roles are cluster-wide, and the Compose network is shared with other Nexlogs applications. Before any role, credential, privilege, network or session change, identify all dependencies and coordinate with their owners. Prefer NLSchool-specific identities and database/schema-scoped grants; do not revoke shared roles, terminate shared sessions or alter server-wide policy without impact review and approval.

### Proposed safe deployment and draining gate

1. Inventory all NLSchool deployment targets, image/code versions, workers, admin access, jobs, command operators, poolers, direct SQL and integrations, including role-to-application ownership. Identify GET paths that write and any shared Nexlogs dependencies. Take and verify an appropriate backup and establish an owner-approved rollback plan.
2. Freeze or route away **all NLSchool writes**, including admin, write-capable GETs, scheduled tasks, command invocations and external writers. Block new old-version work at ingress and scheduler/automation sources. A rolling deployment with old and new lock orders must not be assumed safe; no compatible dual-order release has been verified.
3. During an approved maintenance window, deploy the compatible version to every intended writer; drain old requests and transactions and stop old writers according to the operator's procedure. Check orchestrator/systemd versions and database sessions/transactions, including persistent connections, poolers and jobs; verify process ownership and account for unidentifiable sessions. Do not rely solely on an image tag, restart policy, credential rotation or `CONN_MAX_AGE`. If any old or unidentified writer cannot be accounted for, keep the write freeze in place.
4. If approved, establish NLSchool-specific runtime/migration identities and restrictions, and fence obsolete credentials and existing sessions through controlled operator procedures. Credential changes alone do not terminate existing sessions. Confirm no shared Nexlogs application or authorized integration is disrupted. Do not perform role or privilege changes as part of this inspection.
5. Verify the deployed versions, roles, effective privileges and schema, then run approved smoke checks and release writes deliberately. Monitor errors, `40P01`, lock waits and long-running transactions; have named owners for alerting and rollback. Rollback must first re-freeze and drain incompatible writers; restoring an older image while new writers remain active can reintroduce the old ordering.

### Verification still required and approvals

- In disposable PostgreSQL, exercise real management commands, admin actions and endpoint races under candidate roles; test lock waits, state revalidation, rollback, tenant independence and `40P01` handling. Repeat the invitation, invoice/closure and report regressions against a migration-built schema after a separately approved migration-fidelity plan; the existing model-built fixture results do not prove deployed schema fidelity.
- On a disposable isolated cluster or suitably isolated test environment, test proposed effective grants, inherited roles, ownership/default privileges, denied direct SQL and any proposed database-enforced interface, without affecting shared roles. Verify allowed service operations, audit, schema changes, connection pools and rollback. PostgreSQL changes are not authorized by this document.
- Rehearse draining, scheduler fencing, startup/version attestation, monitoring and rollback in an isolated staging deployment; explicitly verify other Nexlogs applications continue to operate. Obtain deployment-owner, database-owner/security and affected-application-owner approval before executing any operational or database-role/permission/trigger change. Any migration or production change requires its own approval.

No new runtime or database experiments were performed in Step 4. **Stage 2A remains NOT READY / incomplete:** database-level enforcement, live writer inventory and fencing, a verified compatible rollout, migration-built schema validation and transactional provenance validation are outstanding; transfer-aware report issuance remains disabled.

## Stage 2A step 5: database-level writer protection design (audit and design only)

**Design assessment complete; implementation and approval remain outstanding.** This is a repository-only assessment, not evidence of deployed roles, privileges, schemas, triggers, processes or active jobs. Step 4 records the Compose, systemd and connection inventory and the draining gate. This step inspected `backend/config/settings.py`, both Compose files, `docs/DEPLOYMENT.md`, the three custom management commands, representative business services and migration files, including the subscription backfill and examination-publication version backfill. No database or production connection, experiment, application change or migration was performed.

### Critical write boundaries

The tables below are principal writes, not an exhaustive grant list. Input tables, join tables, Django authentication/session/admin tables, audit and sequences must be included in any later permission inventory. Every direct-SQL bypass below is **possible only if the connecting role has the relevant effective privileges**; actual grants are unknown. Existing application constraints still apply, but they do not prove the full cross-table business rules.

| Operation and actual writer | Principal tables written | Existing transaction and locking | Direct SQL / need for database enforcement |
|---|---|---|---|
| Finance journal posting: `finance.ledger.post_entry`, called by invoice/payment and other services | `finance_financesettings`, `finance_journalentry`, `finance_journalline`; account/category/method seeds when needed | Caller must be in `atomic`; FinanceSettings row lock, then journal insertion; journal-line balance and account checks are application logic | DML can bypass balancing, period validation and idempotent workflow. High-priority candidate for a controlled write boundary or carefully scoped invariant; simple grants cannot enforce balanced multi-row posting. |
| Record, confirm or allocate payment: `finance.payments.record_payment`, `confirm_payment`, `allocate_advance` and API views | `finance_payment`, `finance_paymentallocation`, `finance_invoice`, `finance_journalentry`, `finance_journalline`, `finance_financesettings` | `atomic`; FinanceSettings first, then payment when existing and relevant invoice rows; journal written within the service transaction | DML can bypass allocation limits, status transitions and corresponding journal. Strong candidate for controlled writes; preserve idempotency, reversal, receipts and tenant/campus rules. |
| Invoice create, issue and void: `finance.billing.generate_invoice`, `issue_invoice`, `void_invoice` | `finance_invoice`, `finance_invoiceline`, `finance_financesettings`; issue/void also journal entries/lines | `atomic`; create locks FinanceSettings then matching term before checking completion; issue/void lock FinanceSettings then invoice. The view writes its invoice audit **after** the service returns, not within the service's atomic block | Direct DML can bypass term closure, fee calculations, journal/reversal and audit. A controlled boundary is justified; transactional audit enforcement cannot be introduced safely without addressing the existing service/view boundary. |
| Term final authorization / reopen: `academics.closure.final_decision`, `reopen`, closure API | `academics_academicterm`, `academics_termclosure`, `academics_termclosurechecklistitem`, `academics_termclosureapproval` | `atomic`; locked term joined to academic year, then closure; validates state and records approval history | Direct DML can bypass approval, reopen reasons, checklist and invoice coordination. Candidate for controlled transitions and invariant checks. |
| Admission, enrollment and transfer: `core.enrollment.StudentListCreateView`, academics collection POST, `academics.transfers.ClassTransferView` | `core_student`, `core_admissionsequence`, `academics_studentenrollment`, enrollment-subject join table, `core_auditevent` | Admission: `atomic`, year → class → tenant for number reservation. Transfer: `atomic`, term rows → student → source enrollment → target class; revalidates before writes. Other enrollment operations have their own row-lock paths | Direct writes can bypass sequence generation, transfer dates, tenant/campus checks and enrollment history. Controlled writes/constraints warrant evaluation; do not assume one lock order covers every enrollment writer. |
| Marks and reports: `academics.examinations` mark action, `academics.report_cards` review/publish/withdraw, batch review | `academics_assessmentresult`, `academics_reportcardversion`, `core_auditevent`; examination publication workflows also write `academics_examination` and `academics_examinationpublication` | Mark and report actions use `atomic` and lock term first; marks then examination/results; publish/withdraw then report and revalidate. Batch review uses nested atomic blocks | DML can bypass status, permissions, immutable snapshot/version and open-term safeguards. Candidate for controlled transitions, but preserve historical versions and the existing prohibition on transfer-aware issuance. |
| Provenance attest/revoke: `academics.provenance.ProvenanceAttestationView` / `ProvenanceRevocationView` | `academics_enrollmentprovenanceattestation`, `academics_enrollmentprovenancerevocation`, `core_auditevent` | `atomic`; term first, then student/enrollment for attestation or attestation for revocation | Direct DML can bypass fingerprint, history and revocation rules. Transactional provenance validation is not yet complete; do not enable provenance-linked publication as a consequence of database design. |
| Subscription changes: `core.subscriptions.activate_subscription`, `expire_subscriptions`, `core.views.start_trial_subscription`; admin renewal and management commands | `core_tenantsubscription`, `core_subscriptionplan` on trial get-or-create, `core_auditevent` for activation/renewal | Activation: `atomic`, Tenant → live subscription rows; expiry uses conditional bulk updates without its own encompassing transaction; onboarding calls trial creation within its outer transaction. Admin restricts raw subscription CRUD and existing plan code edits | DML can bypass status/plan policy, admin restrictions and audit, notwithstanding the one-live-subscription constraint. Candidate for controlled transitions, with separate handling for expiry and shared plans. |
| Number backfill: `audit_admission_numbers --assign-blank` | `core_student`, `core_admissionsequence`, `core_auditevent` | Per-candidate `atomic`, tenant → student; rechecks blank after waiting, then reserves and audits | DML can overwrite numbers or bypass the reservation/audit. Limit direct writers; preserve separately authorized repair workflow. |
| Audit events: `core.enrollment._audit`, `finance.views.FinanceView.audit`, subscription service and management command | `core_auditevent`; Django admin also writes `django_admin_log` | Report, transfer, subscription activation and backfill examples audit within their business transaction; invoice draft/void views call audit after the service's transaction. Atomic coupling is therefore **not universal** | A role with unrestricted audit-table DML can omit, forge, alter or delete events as permitted. Transactional audit and tamper resistance are justified design goals, not verified guarantees. |

These application paths use row locks as described, not a universal tenant advisory lock. No role separation alone ensures they are invoked, and database checks cannot substitute for actor authorization or campus isolation without a trusted identity design.

### Proposed identities (not created)

| Identity | Proposed access and boundary | Compatibility / operational limitation |
|---|---|---|
| Runtime application | Dedicated NLSchool non-owner login, scoped `CONNECT` and schema access, only the table/sequence/function privileges required by current Django API, admin and approved background commands; no DDL or role administration | Today Django uses direct ORM DML and one `default` connection, so runtime still needs substantial write rights. SQL using these credentials can bypass services, change state or forge audit if grants permit. TLS, network restrictions and secret access reduce exposure but do not enforce business invariants. |
| Migration/deployment | Separate tightly controlled NLSchool object-owning or appropriately privileged migration identity, usable only for approved deployment windows; supports DDL, migration history and data migrations | `manage.py migrate` currently uses the same configured default alias as the app; process-specific credentials/configuration and compatible object ownership/default grants must be designed and tested first. `RunPython` backfills write business tables. Do not grant the runtime migration privileges merely to preserve convenience. |
| Reporting | Dedicated non-owner read-only identity, limited to approved NLSchool views/tables and columns; no write, DDL, role escalation or unsafe function execution | Define tenant/privacy requirements separately: table-level SELECT alone is not tenant isolation. Django GET endpoints that perform subscription expiry or finance seeding cannot simply run under a read-only identity. |
| Administrative/emergency | Separate time-limited, strongly authenticated, monitored break-glass identity; normally disabled or inaccessible, approval and independent audit required | Scope to NLSchool objects where feasible. Privileged owners or superusers can bypass application controls; emergency operations and any correction must be reconciled and independently reviewed. |

Role names are proposals, not evidence of actual logins. Validate effective grants, `PUBLIC`, inherited memberships, schema/table/sequence/function ownership, default privileges and `SET ROLE` paths; audit access to all credentials. A shared login used by old and new processes cannot distinguish versions merely by changing grants. Separating identities is not tenant isolation and is not a substitute for application permissions.

### Enforcement options and trade-offs

All changes below require database/deployment approval and an isolated rehearsal. None is automatically safe for current Django, migrations, admin or other applications.

| Option | Security benefit | Application compatibility and existing-data risk | Other Nexlogs impact | Rollback |
|---|---|---|---|---|
| Per-database/schema privileges and distinct roles | Limits who can connect, read, write or perform DDL; reduces exposure of reporting and operator identities | Current runtime still needs broad ORM DML, `SELECT`/locking access and sequence usage as actually required. Restrictive grants can break admin, write-capable GETs, commands, migrations or future tables. Permission changes normally do not alter existing rows, but failed workflows and incomplete jobs are possible | PostgreSQL roles are cluster-wide; shared credentials, memberships, `PUBLIC` privileges or role deletion/rotation can affect unrelated databases. Use NLSchool-specific identities and inspect all dependencies | Under a write freeze, restore reviewed prior NLSchool-specific grants/credentials and drain sessions; avoid restoring broad shared grants without approval. |
| Controlled service/database write interfaces, potentially stored procedures | Revoke direct DML for covered tables and require a validated transition, consistent lock ordering and transactional audit in the same DB transaction | Existing Django ORM writes would fail until every caller, bulk path, admin action and migration is adapted; procedures require trusted actor/tenant context, safe `search_path`, narrowly owned `SECURITY DEFINER` code where needed and review against privilege escalation. Incorrect conversions can reject valid legacy data or corrupt state | Put interfaces and owners in NLSchool scope; cluster-wide identities and shared integrations still require review | Freeze writers, restore compatible code and prior scoped grants; preserve history and reconcile any writes performed through new interfaces. |
| Constraints and narrowly scoped triggers | Database checks can protect selected invariants even from direct DML, if privilege boundaries prevent bypass; unique/FK/check constraints can enforce suitable local rules | Cross-row balance, authorization and locking are nontrivial; triggers can add deadlocks, overhead and duplicate or incomplete audits, and may be bypassed by privileged roles. Existing historical rows, deferred constraints, bulk updates, backfills and Django migrations require validation. Adding constraints may fail on legacy data | Restrict objects to the NLSchool database/schema; changes to shared extensions/roles may affect other applications | Freeze writes, remove/disable only approved NLSchool-specific enforcement via a reviewed change; retain captured evidence and reconcile writes; rollback does not undo bad data. |
| Separate NLSchool schema and ownership | Narrows grants and reduces namespace collisions; separates object ownership from runtime | Moving existing tables changes Django `search_path`, dependencies, migrations, extensions and operations. Ownership changes/default grants can break current jobs; no existing data should be moved without validation and backup | Schemas are database-local, but shared databases, cross-schema dependencies and cluster-wide roles need assessment | Freeze writes and use a tested reverse migration/restore plan; schema moves and ownership reversal need explicit validation, not just a configuration revert. |
| Transactional audit and protected audit storage | Coupling business mutation and audit append prevents a successful covered transaction from committing without its audit record; separately administered append-only/exported records can improve tamper detection | Current invoice audit is outside the service transaction; forcing transactional audit requires application or controlled-interface changes and trusted actor attribution. Triggers alone may lack authenticated user context and can generate duplicate events. Retention/privacy and failure behavior need decisions | Use NLSchool-specific storage/permissions; shared logging pipelines, privileged database roles and capacity may affect other apps | Freeze affected writers, restore reviewed audit behavior and privileges; retain existing records and investigate gaps. External immutable export cannot be rolled back by deleting evidence. |

**Compatibility-first candidates:** designing the role inventory, a new reporting identity with narrowly scoped reads, and a distinct migration identity are candidates for staged testing without changing Django business logic. A runtime identity can be tested only after its full required DML, sequences, read/lock, admin, command and migration permissions are mapped; none is proven compatible. Revoking runtime table DML, enforcing triggers/procedures, moving schemas or mandating transactional audit are later, potentially breaking changes requiring application design and explicit approval.

### Rollout, verification and decisions

Use the Step 4 fail-closed freeze/drain procedure; do not assume old processes or external schedulers are absent. First obtain a complete writer/credential/role inventory, dependency owners, tested backup/restore and migration-built schema plan. Freeze all NLSchool writes including write-capable GETs, admin, scheduled jobs and direct clients; prevent old processes from reconnecting; drain requests, transactions and pooled sessions, and verify versions and session ownership. Keep the freeze if an unidentified or old writer remains. Then, only after approval, introduce NLSchool-scoped identities/grants and verified compatible application configuration; test effective identities, allowed and denied operations and maintenance jobs before restoring traffic. Monitor lock waits, deadlocks, audit gaps and permission failures. Rollback requires another write freeze, session draining, restoration of compatible code and scoped grants, and reconciliation; do not simply restart an old image while new writers are active.

Before implementation, obtain database-owner/security and deployment-owner approval, plus approval from owners of every potentially affected Nexlogs application and integration. Decide runtime privilege scope, ownership and migration execution, reporting privacy, emergency access, direct-writer policy, trusted actor attribution, audit retention and which invariants justify database enforcement. No change to a cluster-wide role, shared credential, session or another Nexlogs database is implied.

Required validation: rehearse grants and denied direct SQL in a **separate disposable PostgreSQL cluster or explicitly isolated test environment**, never by changing shared roles; run actual endpoints, admin actions, management commands, permitted report reads and migrations under intended roles. Test rollback, nested transactions, bulk writes, tenant separation, audit failure behavior, concurrent lock ordering and SQLSTATE `40P01`, plus old-process draining and backup restoration. The existing disposable fixture runner explicitly disables migrations; migration-built testing needs a separately reviewed disposable-only plan and authorization. Existing Step 3 test results are useful application evidence, not proof of database enforcement.

**Stage 2A remains NOT READY / incomplete.** Writer fencing and live inventory, the chosen database boundary, migration-built schema validation, rollout rehearsal, unresolved cross-module coordination and transactional provenance validation are outstanding. Transfer-aware report issuance remains disabled.

## Stage 2A step 6: isolated PostgreSQL identity rehearsal (partial validation; NOT READY)

### Environment and scope

- Created a new, dedicated PostgreSQL **18.6** cluster using `initdb` and `pg_ctl`, not the configured local database or the shared Nexlogs container. It listened only on a private, mode-700 Unix-socket directory, with TCP disabled and trust authentication restricted to that local socket; this test setup is **not** a production authentication recommendation. The harness verified the new cluster's data directory and the runtime connection's database, role and Unix-socket transport before testing.
- The database and all four roles existed only in that cluster. Existing Django migrations were applied by the migration identity to the empty disposable database, including `academics` 0018–0020; **none were applied to an existing, development or production database**. The server was stopped and its isolated data and socket directories removed. No Docker, other Nexlogs database, production credential, role or privilege was touched.
- Migration-built tests use a session-only test runner pointing at that one pre-created isolated database. The selected Django `TestCase` tests use transactional rollback; no production files, triggers, stored procedures or new application migrations were added. The empty database does not exercise production data or every data-backfill branch. PostgreSQL 18.6 is not proof of compatibility with the deployed PostgreSQL version or schema.

### Roles and grants exercised

| Test identity | Scope tested | Result / caveat |
|---|---|---|
| `rehearsal_migrator` | Login and owner of the disposable database; schema/object ownership adequate for Django `migrate` | All existing migrations completed on the empty isolated database. Future deployment migrations, historical data backfills and production ownership are not established. |
| `rehearsal_runtime` | Non-owner login; database `CONNECT`, public-schema `USAGE`, table `SELECT/INSERT/UPDATE/DELETE` and sequence `USAGE/SELECT`. In a second profile, `UPDATE/DELETE` on `core_auditevent` and `INSERT/UPDATE/DELETE` on `django_migrations` were revoked | Both profiles passed the 12 selected application tests. Schema creation, temporary-table creation, table alteration and `SET ROLE rehearsal_migrator` were denied with SQLSTATE `42501`. This is a **broad test grant**, not a demonstrated minimum or a complete safe permission model. |
| `rehearsal_reporter` | Login, database `CONNECT`, schema `USAGE` and `SELECT` on all existing public tables | A direct student read succeeded; student update, `SELECT ... FOR UPDATE`, audit deletion and schema creation were denied (`42501`). Broad SELECT includes sensitive and cross-tenant tables; this is **not** an approved reporting policy or tenant isolation. Django read endpoints that write were not run as this identity. |
| `rehearsal_emergency` | Login with `NOINHERIT` and membership in the migration role | Explicit `SET ROLE rehearsal_migrator` and a student update succeeded, then rolled back. This demonstrates escalation power, not a production-ready break-glass design; time limitation, approval, MFA and independent monitoring were not implemented or tested. |

Database `PUBLIC` privileges and public-schema privileges were revoked in this test before the scoped grants. Default privileges for future tables, functions and sequences were **not** designed or verified, nor were all built-in function `EXECUTE` privileges restricted. The temporary initial cluster owner was used only to provision the test roles and grants. Test identities used the same private socket and were destroyed with the cluster.

### Representative application operations

For **each** of the broad and narrowed runtime profiles, the same **12 existing Django tests passed**: balanced/idempotent journal posting; invoice calculation and create/void; advance payment allocation; two-stage term closure and reopening; admission and enrollment; student transfer; examination marks, report review/publication and portal reads; admission-number backfill command with audit; subscription admin renewal; and subscription-expiry command. These tests invoke real services, views, admin actions or commands and include ordinary application reads and writes. Their test setup itself also requires permissions, so the results do not isolate a minimum grant per operation. An initial run also passed 11 tests but its separate SQL probe failed due to a test-harness encoding mismatch; the final UTF-8 rehearsal superseded that probe.

No selected application workflow failed from insufficient privileges under the two tested profiles. Negative probes did demonstrate insufficient privilege for runtime DDL/temporary-table creation and migration-role escalation, and for reporter writes and locking reads. Untested paths may require additional permissions or conflict with the narrowed audit grants; notably no separate external scheduler/background-worker deployment, full suite, cleanup/deletion workflow, production integration or future migration was tested.

### Direct SQL and audit findings

- Using the runtime identity and rolling back, direct SQL **changed an actual student status** and inserted a fabricated audit event. The broad profile also deleted an actual audit event; rollback restored both the student and original audit. The narrowed profile denied audit `UPDATE` and `DELETE`, but still permitted a forged audit `INSERT`. Application permissions, audit semantics and business workflow can therefore be bypassed with runtime credentials.
- Under both profiles, no-op direct SQL `UPDATE` statements were allowed on invoice, payment allocation, journal entry, academic term, enrollment, assessment result, report version, tenant subscription and admission sequence tables. These prove table-level update permission, **not** that every arbitrary data change would satisfy constraints. Broad runtime also had update privilege on `django_migrations`; the narrowed profile denied it.
- The reporter could read a student but could not update that student, lock it with `FOR UPDATE`, delete the audit event, or create a table. The migration and emergency identities can modify protected data by design; permissions alone do not ensure correct lock ordering, authorisation or audit attribution.

### Candidate model, rollback and remaining approval

The narrower profile is a useful **compatibility candidate for the selected tests only**, not a production-ready or proven least-privilege model: runtime `SELECT/INSERT/UPDATE/DELETE` remains broad on other existing tables, and audit `INSERT` can be forged. Continue with per-table/column, sequence and function privilege inventory; consider denying audit modification and migration-history writes only after testing every legitimate retention, deletion, admin and deployment path. Design default privileges for future migration-created objects. A real reporting role needs approved views/columns and tenant/privacy controls, not blanket table reads. Direct SQL using runtime credentials still bypasses business rules; stronger controlled write interfaces, constraints or triggers need a separately approved architecture and verification.

Separate roles are compatible with the **tested** fresh-database migration and selected runtime/admin/command paths when different credentials are supplied to Django's single default connection. This does not prove full Django migration/admin/worker compatibility or safe identity rotation in deployment. No old process or scheduler was inspected or fenced.

Before any real change, obtain database/security, deployment and affected Nexlogs owners' approval; inventory cluster-wide roles, inherited grants, ownership, running sessions and integrations; validate on a migration-built isolated cluster matching the deployed version and representative historical data; and rehearse all workflows, future/default grants, external jobs, monitoring and restore. Follow Step 4's write freeze, old-process/session drain, identity verification and controlled traffic restoration. Roll back under a renewed freeze by draining affected connections, restoring reviewed compatible credentials/grants and application version, and reconciling audit and data; never blindly restore shared cluster-wide privileges.

**Stage 2A remains NOT READY.** A database-enforced business-write boundary, actual writer fencing, full role minimization, production-schema fidelity and transactional provenance validation remain unresolved. Transfer-aware report issuance remains disabled.

## Stage 2A step 7: transactional report provenance (incomplete; NOT READY)

No transactional provenance implementation has been made. The existing
`inspect_segment` validator is a read-only inspection of an attested enrollment
segment, and its own contract requires callers to coordinate all writers. It
is not called by report review or publication. Ordinary review has no
attestation input, and publication still explicitly rejects attestation-linked
reports. Neither that issuance gate nor historical report snapshots were changed.

Review and publication already lock the term, as do transfers affecting that
term, attestation and revocation, and examination actions. The term lock alone
does not cover all provenance inputs: ordinary enrollment and attendance
creation can lock a student without locking the term; class management can
change the class campus while locking only the class; teaching assignments
can be inserted without that term lock; and student editing validates before
its write transaction. The validator also reads enrollment history, subjects,
assignments, examination publications, marks and attendance. Direct SQL and
old application processes remain outside application locking.

A targeted PostgreSQL reproduction used real review, withdrawal, class
management and publication endpoints. After creating a reviewed report whose
enrollment and class shared a campus, publication was paused immediately
after taking its term lock. A class-management request successfully changed
the class to a different campus before publication resumed. Publication
returned 200, leaving a published report while the enrollment and class
campuses differed. This demonstrates an unfenced application writer and
missing publication revalidation; it is **not** a passing fix test.

A complete correction needs a common, verified lock hierarchy for every
relevant writer, including inserts and multi-student batches, with fresh
reads after waiting. Merely adding student or class row locks to report
transactions is not sufficient: admission-number backfill locks tenant then
student, admission locks academic year then class, and other paths have their
own locks and audit foreign keys. Their combined ordering must be assessed
against term-first report and closure paths to avoid new cycles. The existing
evidence fingerprint and attestation rules also need integration without
enabling transfer-aware issuance. The user selected **requiring a new review**
before publishing legacy reviewed-but-unpublished reports that lack stored
provenance evidence; that policy has not yet been implemented. Published
historical reports must remain unchanged.

Verification in the disposable, randomly named PostgreSQL fixture database
(`TEST.MIGRATE=False`, no `--keepdb`) included the one endpoint race
reproduction above and 38 existing report provenance, provenance concurrency,
report publication/lock-order, batch-review and transfer tests; all completed
successfully. The reproduction succeeds by demonstrating the existing defect.
`manage.py check` reported no issues and `makemigrations --check --dry-run`
reported no changes. No migrations were created or applied. The requested
transfer/review, transfer/publication, revocation/change, tenant-isolation and
rollback schedules for a corrected implementation remain untested.

Remaining work: design and implement the complete writer coordination and
authoritative transactional revalidation, apply the selected legacy-review
policy, and run all requested concurrency schedules and regression suites.
Stage 2A remains **NOT READY**, and transfer-aware report issuance remains
disabled.

## Stage 2A step 7A: provenance writer coordination (design only)

This is a proposed protocol, **not an implemented or verified global lock
order**. It builds on Step 7's reproduced class-campus race; no new test,
code, migration, database or production change was made in this step.

### Dependency map

| Dependency | Existing relationship and use |
|---|---|
| Tenant, campus and presentation | `ReportCardVersion`, term, enrollment, student, class and academic year have separate tenant references that must agree. `StudentEnrollment.campus` and `SchoolClass.campus` are nullable campus FKs; the student also has a nullable profile campus. Preview compares populated enrollment/class campus IDs and tenant ownership and renders the enrollment campus. `inspect_segment` checks attested-campus ownership and fingerprints campus names, school name and enrollment/class campuses; it does not itself enforce every equality required by ordinary preview. There is no separate class-group model: `SchoolClass` is the group, with optional `SchoolClassLevel`. |
| Student, year and term | A report FK points independently to student, enrollment and term; enrollment points to student and class; class and term point to `core.AcademicYear`. Review requires the class and term to have the same year and checks enrollment overlaps the term. Preview snapshots student name and admission number, year and term names. A term's dates and completion status are authoritative at review/publication. |
| Enrollment, history and transfer | `StudentEnrollment` has start/end, status, class, campus and subjects M2M. `_context` queries all of the student's year enrollments overlapping the term, including rows from another tenant to detect inconsistent ownership, and fingerprints their intervals, class, status and campus. Transfer closes the source interval, creates a destination and sets its subjects; there is no separate transfer-history table in this path. Ordinary review blocks multiple term enrollments; do not enable transfer-aware issuance. |
| Attestation | `EnrollmentProvenanceAttestation` belongs to term and enrollment and stores version, segment, campus, subject IDs, original context, fingerprint, reason, evidence and actor; `EnrollmentProvenanceRevocation` is a one-to-one revocation. `inspect_segment` checks the latest attestation, no revocation, source fingerprint and evidence, and segment dates. It is currently called by tests only, not report endpoints. Its `segment_not_ended` and attestation rules are not a drop-in replacement for ordinary reports. |
| Subjects, examinations and marks | Enrollment subjects or, if absent, `TeachingAssignment` determine required subjects. Preview reads selected `Examination` and current-version `ExaminationPublication` snapshots. `_context` additionally fingerprints all publications and assessment results for that enrollment, including non-exam results, with term/class/subject/date/status. `inspect_segment` further checks exam session, grading configuration, selected subjects, exam dates, all components, marks including score/remarks, and publication snapshots. |
| Attendance and snapshots | Preview counts `AttendanceRecord` by status over the enrollment/term interval; `inspect_segment` checks and fingerprints segment attendance. `ReportCardVersion` stores reviewed/published/withdrawn status, version, reviewer, timestamp and JSON snapshot; its optional attestation FK is not populated by ordinary review. Snapshot results reference publication IDs and versions. Historic published snapshots are rendered as stored; no reconstruction or mutation is proposed. |

`_context` and `inspect_segment` have different fingerprints and coverage;
neither fingerprint alone currently proves that all fields shown in an
ordinary report still match its review. A future validator must define and
version a canonical evidence envelope covering both the relevant provenance
relationships and the actual reviewed snapshot inputs, while reusing the
existing validators where their semantics apply. This is not permission to
create a second attestation system.

### Application writer inventory

The table covers repository application write entry points relevant to the
dependencies above, their current behavior, and the proposed fence. All can
run concurrently with review/publication unless a shared lock happens to
block them. “Fence” means an implementation must acquire the proposed
tenant transaction fence before provenance reads/row locks and reread and
revalidate inside it; none currently acquire it on these paths. Writes may
also take implicit FK, unique-index, or joined-row locks not enumerated by
`select_for_update`.

| Entry point / current transaction and locks | Writes and coordination implications |
|---|---|
| `ReportCardListView.post`, `ClassReportReviewView.post` → `_review_report` | Atomic; term lock first (batch roster lookup locks joined term/year); batch uses savepoints. Creates reviewed report snapshot/version and audit. Fence before term/roster and before batch savepoints; revalidate each student's sources and entire dated roster after waiting. |
| `ReportCardPublishView.post`, `ReportCardWithdrawView.post` → `_lock_report_and_term` | Atomic; unlocked term-ID hint, then term `FOR UPDATE OF self` → report `FOR UPDATE OF self`; checks after locks. Publish updates status, actor/time and audit; withdrawal status and audit. Fence before authoritative lookup; preserve term-before-report ordering and gate on provenance-linked publication. |
| `ClassTransferView.post` | Atomic after initial enrollment hint; sorted affected terms → student → source enrollment joined to class/year (joined rows can also be locked) → target class; updates source, creates destination/subjects, updates student and audit. Fence before fresh discovery; reconcile joined locks with proposed hierarchy. A transfer outside a term's affected set is not automatically fenced by that term. |
| `AcademicCollectionView.post` for enrollments | Serializer performs reads before atomic; then student → class, inserts enrollment and M2M subjects, may update student grade/campus and audit. No term lock; inserts create phantom history. Fence and re-run relationship, campus, subject and active-enrollment checks after waiting. |
| `StudentListView.post` in `core.enrollment` | Atomic: year → class → tenant via `next_admission_number`/sequence; inserts student, enrollment and audit. Normally creates a new student, but shares year/class/tenant locks with other writers. Fence and reconcile its existing year-first and tenant-row order before use with the proposed order. |
| `StudentDetailView.patch` | Reads and validates the student before atomic; implicit student UPDATE then audit, without explicit re-read/lock. Changes name, admission number and other writable profile fields. Fence and validate against a freshly loaded row; grade/campus are serializer method fields, not ordinary writable profile fields here. |
| `audit_admission_numbers --assign-blank` | Initial read-only scan; per student atomic tenant → student → number reservation/sequence → student UPDATE + audit. Changes admission number used in presentation. Fence per candidate before tenant lock and recheck; preserve its tenant-before-student reservation rule. |
| `PromotionView.post` | Atomic after year discovery; sorted selected students → sorted target classes; inserts target enrollments/M2M, updates graduate/student and possibly year current flags. Its final loop can update additional target-year students not in the initial locked selection. Fence before authoritative year/roster discovery; reconcile all affected students and batch lock order, not just selected IDs. |
| `ClassManagementView.patch` | Atomic class `FOR UPDATE`, then class teacher/active/campus UPDATE and audit; no term lock. The campus change is the reproduced bypass. Fence before class lock and reread. For a conflicting class/enrollment campus, define whether to reject the class change or allow it but block subsequent review/publication pending reconciliation. |
| `AcademicCollectionView.post` for teacher assignments | Atomic insert of `TeachingAssignment` and audit; no class/term lock. A new assignment changes `_context` and fallback subject requirements even when no existing row can be locked. Fence before subject/assignment validation and insert. |
| `AcademicCollectionView.post` for attendance and non-examination results | Serializer/teacher authorization reads before atomic; student `FOR UPDATE`, rechecks active enrollment, then inserts attendance or assessment result and audit. Generic assessments are inserted in atomic without a term lock. These records feed preview or `_context`; fence and rerun authorization and relevant dates/relationships after waiting. |
| `ClassAttendanceView.post` | Atomic; sorted terms covering date → sorted students → attendance inserts/audits; preliminary class/year/authorization reads precede transaction. Fence and refresh inputs after waiting. |
| `ExaminationListView`, `ExaminationSessionView` / `_create_exam` | Atomic; term lock → exam, session and component inserts and audit; related class, subject, configuration and assignment are plain reads. Fence and refresh. |
| `ExaminationDetailView.post`, `ExaminationActionView.post` / `_lock_for_action` | Atomic; initial plain exam/authorization read → term lock → exam `FOR UPDATE OF self`; mark upserts, state changes, publication inserts and audits as applicable. Fence before fresh authorization, roster, configuration and marks; preserve term-first. |
| `ProvenanceAttestationView.post`, `ProvenanceRevocationView.post` | Atomic: attestation term → student → enrollment `FOR UPDATE OF self` then attestation/audit; revocation term → attestation then revocation/audit. Both currently use term coordination, but `_context` has other unfenced writers. Fence first, refresh and recheck latest attestation and revocation. Ordinary report reviews do not currently depend on an attestation; revoking an unrelated attestation must not reject them. |
| `TermDetailView.patch`, generic academic term/year creation, finance `AcademicYearListView.post`, `PromotionView.post` year-current update | Term detail validates a plain-read instance before atomic then UPDATE + audit; create paths atomic, potentially updating existing current term/year rows without a documented order. Finance year creation audits after its atomic block. These can alter dates, year/term existence or current flags; fence where they affect the evidence or lock graph and revalidate after waiting. |
| Term-closure/reopen services in `closure.py` | Atomic `_locked` uses term `select_for_update().select_related('academic_year')` (joined year may be locked too) → closure, and final authorization may UPDATE a later term. These change term completion/current status. Include in fence rollout or prove compatible as a nonparticipant; explicitly order joined/year and multiple-term locks. Preserve term-before-report and invoice/closure behavior. |
| Generic class and subject creation; class-level assignment and grading-configuration create/revision | Atomic class creation may lock level; subject creation inserts only; class-level assignment locks class → level; configuration revision locks old configuration and inserts a new version, rather than editing the exam's existing configuration. Existing snapshots use class/subject/configuration references, but a new unrelated row alone does not change them. Fence mutations only when they affect evidence, roster membership or a required cross-workflow lock; any future edit/delete path for a referenced object must join the protocol. |
| Django admin, commands and jobs | `academics.admin` registers only the class-level catalogue; `core.admin` registers subscription plan/subscription with direct subscription mutations restricted. Catalogue fields are not report provenance inputs. The inspected management commands are admission-number audit, subscription activation and expiration; only the admission command writes an identified provenance input. No repository `tasks.py` or signal receiver was found. This is not evidence that deployed schedulers, custom scripts or other DB users are absent. |

Finance invoice generation is an important **lock-graph boundary**, not a
provenance writer: it holds FinanceSettings → term and writes invoice/lines.
It must not be changed into term → FinanceSettings. If it is not fenced, it
must never acquire the fence after holding either row and every implicit
FK/index lock it takes must be checked for cycles with fenced operations.
The same applies to shared-user invitation/identity and subscription paths
that touch tenant, student, user or audit FK rows. Direct ORM scripts, test
fixtures, data migrations, unsupported admin registrations, old binaries and
direct SQL currently bypass the fence; they cannot be claimed protected.
There is no repository endpoint identified for editing tenant/campus names or
existing subject/year names, but future services and privileged writers must
participate or be denied. Deletion/cascade and database-level writers require
separate controls, not assumptions about the application API.

### Proposed ordering and transaction boundary

Candidate baseline, subject to a full wait-for graph and PostgreSQL testing:

1. Authenticate without treating pre-transaction model instances as
   authoritative. Begin the **outermost READ COMMITTED atomic transaction**;
   acquire existing `acquire_academic_coordination(tenant_id)` before any
   provenance source read, savepoint or row lock, then verify tenant and
   reauthorize/reload state. Its transaction-scoped advisory key is per
   tenant and is held through outer commit/rollback. Do not acquire another
   tenant's key in the same transaction. The current helper explicitly rejects
   first acquisition in a savepoint and non-READ-COMMITTED transactions.
2. Discover all affected IDs under the fence. Where a row lock is required,
   use explicit `FOR UPDATE OF self` and deterministic PK ordering within
   each type; avoid accidental joined-row locks. Proposed order is affected
   **terms → academic years → tenant/sequence rows when needed → students →
   classes → enrollments → examinations → reports → attestations and
   subordinate records**. The report remains after its term, and withdrawal
   must recheck the term/report relationship. Read-only evidence rows need
   not all be row-locked if **every** writer, including inserts and M2M
   changes, is fenced; required business row locks still remain. This is a
   target, not a description of current code.
3. Reread all dependencies after the fence and required row locks, including
   rows absent before waiting. Validate ownership, campus, year/term, date
   coverage, history, subject set, latest/revoked attestation where applicable,
   current result versions and snapshot inputs. Do not reuse stale serializer
   instances, pre-lock roster or an unlocked term-ID hint without checking
   the relationship again. Persist report, evidence and audit atomically;
   release protections only at the outer commit or rollback.

One tenant fence serializes participating same-tenant writers, including
phantoms, and prevents competing participants in that tenant from each
holding locks while waiting for the other's fence; different tenant keys do
not intentionally block each other. It
does not prove the entire system deadlock-free: nonparticipants, shared users,
FK key-share locks, unique constraints, implicit joined locks, different
transaction nesting, existing tenant/year orders and multi-term closure
updates can still create cycles. For example, current admission is year →
class → tenant whereas the candidate order places term/year before tenant
and class later; backfill is tenant → student. Transfers currently lock a
joined source enrollment/class/year before their target class. These paths
must be reorganized or explicitly proven safe; simply adding the fence to
reports is unsafe. The hierarchy cannot be approved until closure's joined
year/multiple-term locks, FinanceSettings → term, identity/user and audit FK
interactions have been traced, with no row-lock → advisory-lock inversion.
Do not silently remove or weaken the invoice/closure or report/withdrawal
fixes. The candidate trades same-tenant throughput, particularly 500-student
batch reviews, for a simpler phantom-safe boundary; benchmark and consider
a finer-grained design only after a complete protocol is proven.

Report preview is read-only and non-authoritative; there is no separate draft
creation before review. Initial official creation **is review** and must
validate within the fenced transaction before snapshot/version/audit INSERT.
At publication, lock and re-read term and report, recompute and compare
versioned evidence with the reviewed snapshot, fail closed on missing,
malformed or mismatched evidence, and atomically update status/audit.
Withdrawal uses the same fence and term-before-report discipline, but does
not need provenance validation to let an authorized headteacher discard a
stale review. Already published reports are not rewritten or revalidated
retroactively. A later allowed source change must not mutate the issued
snapshot; whether an incompatible post-publication class-campus change or
transfer is rejected, allowed as a historical change, or requires a
correction workflow is an explicit product decision. The minimum guarantee
is a valid, consistent snapshot **at its publication commit**, not permanent
equality with mutable present-day class data.

### Legacy unpublished report policy

Current state is `ReportCardVersion.status`, `version`, `reviewed_by`,
`reviewed_at`, `snapshot` and a separate `academic.report_card.reviewed`
`AuditEvent` whose current metadata contains only version and reason. No
current persisted field reliably identifies a review as transactionally
provenance-validated. Timestamp or deployment time is not sufficient.

Design candidate without a new model or migration: when a *new* review is
created, atomically include a strictly versioned evidence fingerprint,
report/tenant/term/student/enrollment/version binding and evidence-scheme
identifier in its existing review audit event metadata. Publication requires
exactly one matching, well-formed supported event for that reviewed report
and a recomputed matching fingerprint; absent, duplicate, malformed, unknown
or mismatched evidence fails closed. Review and audit must roll back together.
Avoid exposing internal evidence in public report responses. Treat this as
application-level evidence, **not tamper-proof**: existing audit rows lack a
report FK/uniqueness constraint, and Step 6 showed runtime SQL can forge audit
INSERTs. Audit retention, indexing/performance, authoritative storage,
version upgrades and stronger integrity controls require owner review; if
these cannot be assured, a separately approved schema/controlled-write
design may be needed. No backfill may label a legacy review as newly validated.

For a legacy reviewed report, publication fails with an actionable message.
An authorized headteacher uses the existing withdrawal flow and reason,
then creates a new version through the normal review flow; the existing
`version > 1` reason requirement remains. New review must genuinely
recompute its own sources/evidence and pass all existing safeguards. Only
that new reviewed version can publish; the old withdrawn row and audit remain.
If the term is closed, follow authorized reopen/closure rules rather than
bypassing them. Already published versions and their snapshots remain intact.
This policy is selected but **not implemented**.

### Required disposable PostgreSQL test matrix

All rows below are proposed, not completed tests. Use real authorized API
or service calls, separate connections, barrier after the first transaction
has acquired the fence and before commit, `pg_blocking_pids` proving the
second backend is actually blocked by the first, bounded timeouts, explicit
release and both SQLSTATEs (especially no `40P01`). Assert tenant-scoped
records, versions, snapshots, audit history and absence of partial writes.
Tests must use a fresh disposable database, not `--keepdb`; run both
model-built and, separately where feasible, migration-built disposable
schemas. One cannot assert blocking for a request rejected by authorization
or the issuance gate before it reaches the fence.

| First transaction held before commit | Second operation while first is held | Expected after release / assertions |
|---|---|---|
| Class campus changed to a different campus | Publish previously reviewed ordinary report | Publication blocks; after class commit it re-reads and rejects mismatch or changed evidence, with no publish audit/status. If class change is rejected by the approved business policy instead, test its rollback and then successful publication against unchanged data. |
| Publication after successful validation | Change class campus | Change blocks; publication commits with consistent snapshot/audit. After waiting, change follows the approved post-publication policy; verify historical snapshot unchanged and no lost audit. |
| Transfer effective inside report term | Review source enrollment | Review blocks; after transfer commit it sees multi-enrollment history and rejects under the existing issuance gate; no report/review audit. |
| Review after validation | Transfer | Transfer blocks; review commits, then transfer rechecks and either succeeds if existing transfer rules permit or fails for a demonstrated business reason. Verify reviewed evidence will reject a later stale publication, and no partial transfer. |
| Transfer effective inside term | Publish existing review | Publish blocks, then rejects changed history/evidence; report remains reviewed, no publish audit. |
| Publication after validation | Transfer | Transfer blocks; publication commits; transfer revalidates and follows existing and approved post-publication rules. Issued version/snapshot and audit remain unchanged. |
| Enrollment creation/change first; separately a relevant revocation first | Review | Review waits and re-reads; conflicting enrollment causes rejection. For a future attestation-aware validation boundary, relevant revocation must reject that attestation; unrelated revocation must not invalidate an ordinary report. Exercise the validator/service without opening transfer-aware issuance. |
| Enrollment creation/change first; separately a relevant revocation first | Publish | Publish waits, then rejects changed enrollment or relevant revoked attestation. Keep the current provenance-linked publication gate; a gate-only 400 is not proof of revocation validation. |
| Review or publication after validation, held before commit | Enrollment change; separately a relevant revocation | Each writer waits, then rechecks its own state after report commit. Verify the review becomes unpublishable if a later enrollment change invalidates its evidence; a published historical snapshot remains unchanged. Test linked-attestation validation internally while keeping the issuance gate closed. |
| Legacy reviewed report with no supported evidence | Publish | Run actual publication, assert rejection, unchanged status/snapshot and no publish audit. This sequential negative case does not claim lock blocking. |
| Authorized withdrawal and genuine new review of legacy version | Publish new version | Old row/audits retained, new version/evidence present, publication succeeds only if still valid. Race a concurrent withdrawal/publication as an additional blocking check. |
| Tenant A review or provenance writer held | Tenant B analogous authorized writer | B commits while A remains held; verify separate tenant keys, no shared fixture rows and no unintended cross-tenant access. Shared user rows may still legitimately contend and need separate tests. |
| Writer creates an invalidating change, then rolls back | Waiting review/publication | Observe wait; after rollback the second rereads original state and follows the appropriate valid/invalid path, with no rolled-back source or audit records. Separately force validation or audit failure after partial batch work and assert no report/version/evidence/audit survives. |

Also run review/publication versus closure and reopen, existing
publication/withdrawal both orders, invoice/closure, invitation and
admission/finance regressions; test batch savepoint behavior, class
assignment/attendance/mark inserts, same-tenant serialization, mismatched
tenant IDs, stale hints and malformed evidence. Check each test's actual
commit outcome rather than inferring safety from HTTP status. No such new
corrected schedules have yet passed.

### Decisions and release blockers

- Approve the tenant-wide serialization/performance tradeoff and complete
  row/FK/index/shared-identity lock graph, including nonparticipating
  workflows; adjust the candidate order before implementation if needed.
- Decide post-publication class-campus and transfer behavior, exact ordinary
  snapshot evidence (including attendance changes), evidence storage and audit
  retention/integrity, fingerprint versioning, and failure UX.
- Define attestation-aware validation tests while retaining the current
  provenance-linked issuance block. Direct SQL/runtime credentials and old
  processes remain capable of bypassing application fencing; Steps 4–6's
  owner-approved database controls and drained rollout remain prerequisites.
- Implement every participating writer and revalidation together, then run
  the matrix and regression checks. Any needed migration, privilege or
  trigger requires separate approval. Stage 2A remains **NOT READY**.

## Stage 2A step 7B: lock-graph gate (blocked; no implementation)

**STOP:** the Step 7A fence-first proposal does not safely cover the existing
shared-user/tenant foreign-key lock graph. Phase 1 found and reproduced an
unsafe cycle, so no provenance writers were integrated, no transactional
report validation or legacy-review policy was implemented, and no application
code or deployment configuration was changed. The complete system lock graph
has not been verified; the concrete counterexample below is enough to reject
the proposed protocol in its present form. Stage 2A remains **NOT READY**.

### Verified counterexample and existing lock edges

The test ran real student-admission and account endpoints with a temporary
test harness, not a modified application implementation. The harness placed
the admission endpoint inside an outer `READ COMMITTED` transaction and took
the existing tenant advisory lock first, as the proposed rollout would; the
endpoint then used its existing nested atomic transaction. A single verified
school owner was the admission actor and the account being reset. In separate
experiments, email verification used an unverified owner. Test clients
force-authenticated admission; normal login requires email verification, so
the email-verification scenario establishes the database/endpoint cycle but
does not establish that a normal login could initiate that exact schedule.
Password reset was additionally reproduced with a verified owner; the test
still did not exercise JWT issuance or a deployed client.

1. Account verification or password reset locks its action token **and joined
   shared `core_user` row `FOR UPDATE`** via `get_email_action(..., lock=True)`.
   It updates the user and writes audit events for its active tenant
   memberships. It does not acquire the academic tenant advisory fence.
2. Admission obtains **tenant advisory fence → academic year `FOR UPDATE` →
   class `FOR UPDATE` → tenant `FOR UPDATE`** via
   `next_admission_number`, then inserts student, enrollment and audit events
   whose actor is that same user. The test pauses after the endpoint returns
   201 but **before the outer transaction commits**; that response is not yet
   a durable admission.
3. In the model-built disposable schema, `core_auditevent` foreign keys to
   both tenant and actor were verified `DEFERRABLE INITIALLY DEFERRED`.
   Account commit waits to acquire `FOR KEY SHARE` on admission's tenant.
   `pg_blocking_pids` confirmed the account backend was blocked by the
   admission backend at `COMMIT`.
4. Admission then commits and its deferred actor foreign-key check waits on
   the account's user lock. PostgreSQL detected the cycle and returned
   **`40P01`**, with the server diagnostic identifying the blocked tenant
   `FOR KEY SHARE`; the account transaction was rolled back and admission
   committed. Final-state assertions verified student/enrollment and audit
   outcomes. The email-verification experiment reproduced this result twice
   on separate fresh disposable databases; password reset reproduced it on
   another fresh disposable database.

This is an interaction with an **unfenced participant**, not evidence that the
advisory lock by itself creates the old row-lock cycle: the admission and
account operations already have these row/FK orders. The important Phase 1
result is that adding the proposed fence first while retaining these existing
orders does **not** make the protocol safe. The Step 2A invitation fix
already orders the shared user before the invitation/tenant row; it does not
repair admission or all other tenant-first operations. For example staff
invitation creation and subscription activation also acquire a tenant row
and can write actor-bearing audit records; their complete interactions were
not reproduced or fully verified here.

Other verified source-level edges relevant to completing the graph are:

- Report review: term lock → report INSERT and audit; publication/withdrawal:
  term `FOR UPDATE OF self` → report `FOR UPDATE OF self` → status/audit.
  Their proposed advisory fence is not currently integrated.
- Transfer: sorted term locks → student → source enrollment joined to class
  and year (which can acquire joined row locks) → destination class, then
  enrollment/M2M, student and audit writes. Generic enrollment takes student
  → class; class-campus management takes class alone. Attendance, examination,
  attestation and revocation ordering is inventoried in Step 7A.
- Closure: `FOR UPDATE` on term joined to year → closure; final authorization
  can also update a later term. Invoice generation: FinanceSettings → term
  `FOR UPDATE OF self` → invoice/lines, with FK checks. Do not reverse this
  FinanceSettings → term relationship or alter the completed invoice fix.
- The admission-number command takes tenant → student and writes a student
  and audit without an actor; promotion locks multiple students/classes.
  Same-tenant advisory locking only coordinates transactions that enter it
  **before** their other locks. FK checks, unique indexes, shared users,
  cross-tenant memberships, old processes and direct SQL remain outside that
  guarantee. No complete graph or deadlock-freedom proof is claimed.

### Corrective design candidate, not an approved fix

Keep the existing per-tenant advisory fence, term-before-report rule and
FinanceSettings-before-term invoice rule; do not introduce a new global lock.
The specific tenant/user cycle requires eliminating the tenant
`FOR UPDATE` versus deferred tenant `FOR KEY SHARE` conflict or establishing
a compatible shared-user ordering across **all** interacting writers.
One candidate is to use PostgreSQL `FOR NO KEY UPDATE` for tenant reservation
where callers do not delete the tenant or change its referenced key. Two such
reservation locks still conflict with each other, while a foreign-key
`FOR KEY SHARE` can coexist. This must be evaluated for admission, backfill
and every other tenant-locking path, including any lock upgrade, tenant
deletion/key change, constraints, shared-user and subscription interactions.
Changing only report operations or only one admission call would be incomplete.

A separate disposable experiment replaced only admission's tenant
`select_for_update` with `no_key=True` **in the test harness**, leaving the
application files unchanged. With admission still open and holding the
fence, the same verified user's password reset committed; admission then
committed (200/201, no `40P01`). This is evidence for that one edge only:
it does not prove admission-number serialization, the whole lock graph,
deletion behavior, multi-tenant identity safety or safety of the proposal.
An alternative user-before-tenant ordering would also need a complete
multi-user/multi-tenant analysis and must not regress the existing invitation
repair. Review and approve a complete corrected graph before any integration.

### Test, migration and implementation status

All four experiments used newly created and destroyed disposable PostgreSQL
fixture databases with `TEST.MIGRATE=False` and no `--keepdb`; they do not
establish migration-built schema or production behavior. The three
deadlock-characterization runs passed by **detecting the defect**, not by
proving correctness; the no-key hypothesis run passed only for its limited
schedule. No corrected provenance concurrency matrix or Phase 6 regression
suite was run after the stop gate. Django's test runner reported a clean
system check during these runs; a separate migration consistency check was
not run in this step.

No migration was created or applied. All Step 7A provenance writers remain
unintegrated and direct SQL, old processes and external schedulers remain
unfenced. The class-campus publication race and stale/legacy publication
risk remain. Existing historical published reports and snapshots were not
changed, and transfer-aware issuance remains disabled. Next: review the
corrected shared-user/tenant design, verify the complete graph including
nonparticipants and migration-built constraints, then implement the agreed
protocol and run all Step 7B schedules and regressions on fresh disposable
databases.

## Stage 2A step 7C: tenant/shared-user lock architecture (audit only; blocked)

**Result:** no complete protocol is recommended as safe yet. Step 7B must
not restart on the strength of the limited no-key result. This section
supplements the Step 7A inventory and Step 7B counterexample; it does not
replace those inventories with a claim of a complete wait-for graph. No
application code, migration, production database, privileges or deployment
configuration was changed, and transfer-aware issuance remains disabled.

### Reconstructed cycle

The model-built disposable schema exposed these exact `core_auditevent`
foreign-key constraint names and attributes: `core_auditevent_tenant_id_43ff9a90_fk_core_tenant_id`
and `core_auditevent_actor_id_3bd0c673_fk_core_user_id`, both
`DEFERRABLE INITIALLY DEFERRED`. These names and settings are verified for
the fixture schema only; production and migration-built constraint state is
not established here.

For the test schedule:

1. Password reset calls `get_email_action(..., lock=True)`, whose
   `EmailActionToken.objects.select_related("user").select_for_update()`
   produces a joined `SELECT ... FOR UPDATE`, locking the token and the
   shared `core_user` row. It updates that user, marks the token used,
   invalidates refresh tokens and inserts per-membership `AuditEvent` rows.
2. The test wraps the **actual** admission endpoint in an outer atomic
   transaction and acquires `pg_advisory_xact_lock` for that tenant first.
   The unchanged endpoint locks academic year, class, then tenant with
   `SELECT ... FOR UPDATE`, allocates the admission number, inserts student,
   enrollment and actor-bearing audit rows, then exits its inner savepoint.
   The returned 201 precedes the outer commit and is not yet a durable result.
3. If reset reaches `COMMIT` while admission holds the tenant row, PostgreSQL's
   deferred audit tenant-FK trigger runs
   `SELECT 1 FROM ONLY "public"."core_tenant" x WHERE "id" OPERATOR(pg_catalog.=) $1 FOR KEY SHARE OF x`
   and waits for admission. Admission's deferred actor FK likewise needs
   `FOR KEY SHARE` on `core_user`, which conflicts with reset's user
   `FOR UPDATE`. `pg_blocking_pids` observed the waiting backend at `COMMIT`;
   PostgreSQL's `40P01` diagnostic confirmed both transaction wait edges.
   Depending on starting order, the victim's diagnostic named either
   `core_tenant` or `core_user` and showed the corresponding FK check SQL.
4. PostgreSQL aborts a victim and the survivor commits. The test checked the
   student, enrollment, reset token and associated audit rows against the
   actual winner. The reproduced graph exists with the original row locks;
   the experimental fence does not create it or prevent it. The actor was
   force-authenticated in tests, not through JWT, but was verified for the
   password-reset case.

Email verification uses the same joined action/user lock and per-active-
membership audit pattern, but changes `email_verified` instead. Step 7B
observed its analogous cycle twice with force authentication of an unverified
owner; normal login refuses an unverified user, so that schedule is a
database/endpoint characterization, not proof of normal client reachability.
Existing invitation acceptance obtains the existing shared user first, then
the invitation joined to tenant, then (if applicable) student and writes
membership and audits. Its user-first repair prevents the previously
demonstrated invitation/verification ordering; it does not order all other
tenant-first writers. Password reset and verification may write events for
several tenants held by the same user, so per-tenant advisory fences alone
cannot provide a global shared-user order.

### PostgreSQL lock semantics and focused observations

PostgreSQL documents that `FOR UPDATE` conflicts with all four row-lock
modes, including `FOR KEY SHARE`. `FOR NO KEY UPDATE` conflicts with
`FOR UPDATE`, `FOR NO KEY UPDATE` and `FOR SHARE` but **not** `FOR KEY SHARE`.
`FOR KEY SHARE` also prevents deletion or updates of a referenced key;
PostgreSQL takes stronger update locks for an UPDATE affecting a column in
a unique index usable as an FK, and takes no-key update locks for ordinary
non-key updates. See the [PostgreSQL row-lock documentation](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS).
No-key locking does not replace uniqueness constraints, audit controls or
transactional validation, and an operation later taking a stronger lock
can reintroduce a wait or upgrade cycle.

Two new disposable experiments each held a real tenant row
`FOR NO KEY UPDATE`: a competing `FOR KEY SHARE NOWAIT` succeeded; competing
`FOR NO KEY UPDATE NOWAIT` and `FOR UPDATE NOWAIT` failed with `55P03`; a
DELETE timed out with `55P03`. An actual audit insert with
`SET CONSTRAINTS ALL IMMEDIATE` succeeded while the no-key lock was held,
and an explicit rollback left no audit event. This tested immediate checks
and atomic rollback in that setup, not the complete constraints graph,
referenced-key update or tenant deletion semantics in isolation. No-key
conflicting with another no-key lock is consistent with preserving
serialization **only when all reservers use a compatible row-lock protocol**;
actual admission-number uniqueness, sequence correctness, row deletion,
key changes and lock upgrades must be separately tested.

An initially deferred constraint is normally checked at outer transaction
commit; changing it to immediate is transaction-local and retroactively
checks pending changes at the `SET CONSTRAINTS` statement. It moves the
wait/check point; it does not itself impose a safe lock order or make audits
atomic across separate transactions. See [PostgreSQL SET CONSTRAINTS](https://www.postgresql.org/docs/current/sql-set-constraints.html).
The no-key candidate permitted the tenant FK's key-share check in the
observed reset/admission case, removing that particular wait edge; it did
not remove all possible shared-user, tenant, index or cross-tenant cycles.

### Existing lock graph and remaining unknown edges

The Step 7A writer table remains the detailed provenance inventory. The
following are verified source-level lock sequences, not claims that PostgreSQL
acquires joined table locks in the textual order shown:

| Workflow | Relevant locks, checks and boundaries |
|---|---|
| Verify/reset | Atomic joined token + user `FOR UPDATE`; user/token UPDATE; reset also locks outstanding refresh tokens; bulk per-membership audit INSERTs; deferred tenant/actor FK checks at outer commit. |
| Invitation acceptance | Atomic shared user `FOR UPDATE` → invitation joined with tenant `FOR UPDATE` → optional student `FOR UPDATE` → membership/guardian/student and actor-audit writes; tenant/user FK checks can occur at commit. Joined-row acquisition order is not asserted. |
| Staff invitation, guardian/portal invitation | Staff invite locks tenant `FOR UPDATE` then writes invitation and actor audit. Guardian/portal invite locks student joined to tenant and may insert guardian/invitation and audit referencing a user. These are possible inverse tenant/user edges; no new cycle for them is claimed reproduced. |
| Admission and number assignment | Admission atomic year → class → tenant `FOR UPDATE` in `next_admission_number` → sequence/student/enrollment/audit; backfill per student tenant → student → sequence/update and actorless audit. Generic enrollment locks student → class, with serializer reads before atomic; transfer locks affected terms → student → joined enrollment/class/year → target class; promotion locks selected students then target classes and later may update more students. |
| Review/publication/withdrawal; provenance | Review locks term before report INSERT and audit; publication and withdrawal lock term `OF self` → report `OF self`; attestation term → student → enrollment and revocation term → attestation. Existing audit/reviewer and attestor/revoker FKs can require deferred user/tenant checks. Current review/publication does not use a provenance fence or validated provenance evidence. |
| Closure and invoice | Closure locks term joined to year → closure and can update another term; invoice generation holds FinanceSettings → term → invoice/lines. Preserve this established order and term-closure exclusion. Both also have FK and audit/approval references. |
| Payment allocation | FinanceSettings → payment → ordered invoices → journal and allocation writes; void uses FinanceSettings → invoice. Other finance entry points and implicit FK/unique-index locks must be included in the eventual proof. |
| Subscription/admin/commands | Subscription activation locks tenant → subscription rows and writes actor audit; its admin action invokes the service. Admission backfill uses tenant/student locks above; subscription commands invoke subscription services or bulk expiry. `require_active_subscription` calls `expire_subscriptions`, which performs UPDATEs, so a presumed authorization-only call can take subscription locks. If performed within an outer transaction before fence acquisition, that violates the fence-first contract. |

Nonparticipating direct SQL, old application binaries, external schedulers,
unreviewed scripts, migration operations and unmanaged database identities
can still write or lock rows without the advisory fence. Even participating
writers can interact with nonparticipants through FK and unique-index locks.
Therefore neither tenant isolation nor deadlock-freedom follows solely from
the fence. Do not claim that a tenant advisory key fences SQL or that the
entire graph is verified until those boundaries, membership changes and
multiple-user/multiple-tenant transactions have been traced and tested.

### Architecture comparison

| Candidate | Integrity, audit and compatibility | Risk / migration / operations |
|---|---|---|
| A. Shared-user first | Lock all relevant existing shared users in deterministic order before tenant rows, then perform tenant work and atomic audit/FK checks. ORM can issue user `FOR UPDATE`; preserves audit and existing invitation ordering if fully applied. | Tenant writers often know only an actor; identity flows can span several tenants or create a new user, and dynamic sets need stable discovery/revalidation. User-first versus fence-first can itself invert. Can serialize unrelated tenants sharing a user. No migration necessarily, but broad workflow changes, deadlock tests and retries are required. Not proven. |
| B. Tenant fence plus explicit shared-user rules | Existing transaction-scoped helper serializes participating same-tenant writers and covers phantoms when obtained in the outer transaction before provenance reads/locks; ORM and PostgreSQL supported. Audit remains transactional if kept inside. | The observed unfenced user/tenant FK cycle remains. Shared identity work across tenants cannot safely take one arbitrary tenant key while holding a user; helper currently prohibits coordinating multiple tenant IDs. Before deciding between user → fence and fence → user, model all membership sets and nonparticipants. No migration necessarily; same-tenant throughput, rollout/fencing and mixed versions are operational risks. Not sufficient alone. |
| C. Targeted `FOR NO KEY UPDATE` | ORM `select_for_update(no_key=True)` and PostgreSQL support it; mutually excludes tenant reservers but allows FK `KEY SHARE`; constraints and atomic audit remain intact if unchanged. | Both-order admission/reset tests passed for a **test-only replacement of one tenant lock**, not all writers. Deletion, FK-key updates, lock upgrades, subscriptions, identity, authorization, other tenant row locks and untested constraint paths remain. No migration for the lock call itself; carefully inventory and review each use. Not established system-safe. |
| D. Split identity and tenant phases | Separate short transactions may reduce simultaneous shared-user/tenant holdings; ORM supports explicit phases. Tenant operations and audit can each remain atomic within their own phase. | Cross-phase consistency, user changes, authorization, retries, duplicate operations and audit ordering need explicit durable state/idempotency and recovery. Splitting a currently atomic membership or audit update can weaken guarantees. May require a migration/outbox depending on design, and adds operational complexity; cross-tenant behavior needs policy. No such split is approved or tested. |
| E. Change deferred audit handling | Existing `SET CONSTRAINTS ... IMMEDIATE` is transaction-local and no migration is needed; permanent FK/trigger changes would require a separately reviewed migration. Atomic audit is preserved only if it stays in the business transaction. | Moving checks earlier merely relocates waits and may expose another cycle; removing, disabling or weakening FKs loses referential integrity, and separate audit writes risk loss or forgery. Schema changes can affect other applications sharing a server and require impact, rollback and owner approval. No broad constraint change is proposed. |

### Fresh disposable PostgreSQL verification matrix

All tests below used randomly named databases created/destroyed by the
fixture runner on local PostgreSQL, model-built schemas (`TEST.MIGRATE=False`),
without `--keepdb`. Test-only monkeypatching changed no repository code.
Worker connections were independent, used barriers and bounded timeouts;
for expected waits `pg_blocking_pids` confirmed the first backend blocked the
second. A successful test that expects `40P01` **characterizes a failure**,
not an implementation success.

| Schedule and repetition | Observed outcome and limit |
|---|---|
| Password reset vs fence-first admission, original `FOR UPDATE`, each operation first; each schedule run twice on separate fresh databases | All four runs detected `40P01`; one transaction aborted, the other committed, with token, enrollment, student and audit state matching the winner. The observed `COMMIT` wait and SQL diagnostics established the two FK wait edges. |
| Same two orders with test-only admission tenant `FOR NO KEY UPDATE`, each run twice | All four runs completed reset 200 and admission 201 with audit and enrollment committed. Reset-first: admission waited for reset's user at commit, then completed. Admission-first: reset committed while admission remained open. These are **only** admission/reset tests, not candidate-wide certification. |
| Tenant no-key row compatibility and immediate audit rollback, twice | Key-share and immediate audit insert succeeded; conflicting nowait no-key/update and timed-out delete returned `55P03`; forced rollback removed the audit. No automatic application protection was changed. |
| Existing email verification first vs invitation acceptance, twice | Both runs observed the expected shared-user wait and produced 200/409 with membership/token assertions. The reverse order was **not run in Step 7C**. No no-key candidate was integrated into this path. |
| Existing two-tenant invitations for same shared user, twice | Both operations 201 after actual user-lock waiting; invitation and membership checks passed. This is a baseline same-user test, not a full cross-tenant candidate test in both orders. |
| Existing same-tenant admissions in different years/classes, twice | Both succeeded, waited on tenant reservation, and assigned distinct numbers. This baseline does not exercise different actors or the test-only no-key candidate. |
| Invoice first / closure first, twice each | Actual waits observed; 201/200 and 200/409 respectively. Existing no-partial-invoice checks passed. This validates the current invoice/closure implementation, not fence integration. |
| Report publication first / closure first, twice each | Actual waits observed; publication then closure succeeded; closure first rejected publication. Existing report/term assertions passed. This does not validate transactional provenance. |
| Payment allocation first / invoice void first, twice each | Existing tests observed FinanceSettings wait and expected final outcomes; current baseline only. |

The requested **complete** candidate matrix was not met: reverse
verification/acceptance, different-user same-tenant cases, all shared-user
update/tenant-write combinations, candidate-wide finance/report/closure
interactions and migration-built/deployed-schema constraints remain
unverified. No candidate is certified; no migration consistency check was
run in this documentation/audit-only step. Django test-runner system checks
were clean in the test databases.

### Release gate and next work

Do not treat the Step 7A row order or fence-first protocol as approved for
implementation. Before restarting Step 7B: obtain owner review of a
complete identity/tenant protocol, map explicit and implicit row/FK/index
and audit edges including authentication's subscription writes, preserve
invoice/closure and user/invitation repairs, and make lock-failure behavior
explicit (atomic rollback, no success response for failed commit; only
bounded idempotent retries where safe). Revalidate authorization and source
state after all waits, keep audit writes and intended FK checks in the
transaction, and define the permitted row modes and acquisition order for
each participant and cross-tenant identity transaction.

Rehearse both orders repeatedly with real endpoints/services, committed-state
and audit checks, and model-built **and** approved disposable migration-built
schemas matching the deployed PostgreSQL version. Establish how old
instances, schedulers and SQL writers are fenced; assess performance and
obtain database/security, deployment and affected application owner approval
before any schema, privilege or rollout changes. The class-campus race,
legacy-review enforcement, transactional provenance validation and broader
Stage 2A writer fencing remain outstanding. Stage 2A stays **NOT READY**.

## Stage 2A step 7D: implicit-lock verification (audit only; still blocked)

**Decision: no complete lock protocol is approved or recommended for
implementation. Step 7B must not restart. Stage 2A remains NOT READY.**
The migration-built and additional schedules below narrow the evidence gap;
they do not complete the cross-workflow wait-for graph or establish that a
test-only lock substitution is safe in the application.

### Schema and test boundary

The user explicitly approved applying **existing** migrations only to newly
created disposable databases. Each test invocation used the existing
`FixtureTestRunner` local identity check and a new random
`test_nlschool_fixture_*` database, with `TEST.MIGRATE=True` in a temporary
session-only settings module. The runner prohibited `--keepdb` and dropped
its own database. No migrations were created, no production database was
accessed, and no application code, shared roles, privileges, deployment
configuration or historical reports were changed. Tests used independent
connections and actual endpoints unless described as SQL lock experiments;
force-authenticated requests do not prove JWT/login reachability.

On two migrated databases the audit foreign keys to tenant and user were
both `DEFERRABLE INITIALLY DEFERRED`, matching Step 7C's model-built
observation; 20 academics migrations were recorded. On another migrated
database, the existing schema test verified deferred FKs for the sampled
admission/enrollment relationships and the expected admission/year/enrollment
unique indexes. This samples the local migrated schema; it is not an
inspection or certification of the deployed schema.

### Observed schedules

Each entry marked twice was run on two fresh migration-built databases,
unless explicitly identified as a prior Step 7C result. Waiting tests used
`pg_blocking_pids`; the campus tests instead deliberately confirmed that the
second endpoint completed while the first transaction was paused. Committed
business state was checked; audit checks are stated below where performed.

| Schedule | Result and scope |
|---|---|
| Invitation acceptance first, verification second | Twice: verification waited on the shared user in its joined token/user query; acceptance returned 201, verification 200, invitation accepted, token used, user verified, and both memberships existed. First probe initially failed because PostgreSQL truncated `pg_stat_activity.query` before `FOR UPDATE`; the corrected probe matched the visible query and independently checked the full captured statement. |
| Verification first, acceptance second | Once on a migrated database and twice previously on model-built databases: 200/409 with an observed user lock wait and membership/token checks. The first schedule uses an already existing membership, unlike the reverse schedule above, so this is not a symmetric same-fixture proof. |
| Two tenants, same existing user, invitation acceptance | Twice on migrated databases: both returned 201 after a user-lock wait, and membership and invitation state committed. The same fixture/order was repeated, not both labeled tenant orders or all cross-tenant account writes. |
| Different actors, same tenant, separate years/classes | Twice with captured waiting SQL on migrated databases: original tenant `FOR UPDATE` and test-only tenant `FOR NO KEY UPDATE` admissions each returned 201/201 with actual blocking on the tenant lock, distinct numbers, two enrollments, sequence value 2 and both student audit records. This tests that narrow admission-number case, not every user/tenant interaction. |
| Tenant no-key and audit checks | Twice: a competing no-key NOWAIT lock returned `55P03`; an actor/tenant audit insert with immediate constraints succeeded and its forced rollback removed the audit; a second audit with initially deferred checks committed while the no-key lock remained held. The two audit records were checked after transaction completion. This does not exercise a simultaneous actor `FOR UPDATE` or all audit FKs. |
| Key-share, key-changing updates and uniqueness | Twice: while tenant `FOR KEY SHARE` was held, an ordinary name UPDATE committed; changes to its unique slug or primary key timed out with `55P03`, leaving the keys unchanged. A duplicate-slug insert against a tenant held `FOR NO KEY UPDATE` returned `23505` and left one tenant. This does not prove all index-wait, key-upgrade or deletion schedules. |
| Transfer first, then report review / publication | Twice each: second operation waited for the term row; transfer returned 201 and review/publication returned 400 due to changed overlapping enrollment history. Two enrollments persisted and the stale report did not publish. |
| Review / publication first, then transfer | Twice each: the second operation waited for the term row; review then transfer returned 201/201, and publication then transfer returned 200/201. A later publication of the reviewed report was rejected after transfer; the already-published report remained published. These are existing safeguards for the tested term-overlapping transfer, not full transactional provenance validation. |
| Class-campus update first / publication first | Twice each: the other endpoint completed without waiting while the first transaction was paused; both returned 200 and a published report snapshot and enrollment retained the old campus while the class committed the new campus. These passing **defect-characterization tests reproduce an integrity failure**, not a safe result. |
| Invoice generation vs term closure, each starting order | Twice: actual term-row waits; invoice-first returned 201/200 and closure-first returned 200/409, with existing committed invoice/term assertions. The existing FinanceSettings-before-term order was not changed. |
| Report publication vs term closure, each starting order | Twice: actual term-row waits and existing status/audit assertions passed; publication-first committed before closure, while closure-first rejected publication. |
| Original and candidate admission/password reset, each starting order | Step 7C already repeated each schedule twice on fresh **model-built** databases: original `FOR UPDATE` produced `40P01`; the test-only tenant no-key variant completed both operations. Not repeated on a migration-built schema in this step. |
| Payment allocation vs invoice void | Step 7C repeated both orders twice on model-built databases. No additional migrated-schema allocation schedule was run here. |

### Lock graph, implicit dependencies and residual uncertainty

The Step 7C table and reconstructed `40P01` cycle remain part of this
audit. In particular, reset holds the token and shared user through a joined
`FOR UPDATE`; fence-wrapped admission holds year, class and tenant rows.
At outer commit the deferred audit tenant and actor foreign keys request
`KEY SHARE`: reset waits on the tenant while admission waits on the user.
The advisory tenant fence does not remove that cycle. PostgreSQL detects it,
aborts a transaction, and releases its locks; the victim's business and audit
writes roll back. An endpoint response produced inside an outer atomic block
is not a committed success until that outer block commits.

Additional source-level graph boundaries, without asserting execution order
inside joined PostgreSQL plans:

| Operation | Explicit and implicit dependencies |
|---|---|
| Admission / enrollment | Admission locks year and class, then the tenant for the number sequence, and inserts student, enrollment and actor audit. Other enrollment changes lock student then class. Unique indexes and deferred FK checks can add waits or abort at insert/commit. Reads and authorization outside an outer coordination boundary require revalidation. |
| Account / invitation | Reset and verification lock token plus user, then write user/token and per-membership audit; reset also locks refresh-token rows. Invitation acceptance locks an existing user, then a joined invitation/tenant row and optionally student before membership/audit writes. Tenant-first invitation or admission paths and deferred audit references remain potential inverse edges. |
| Transfer / promotion | Transfer locks affected terms in PK order, student, source enrollment joined to class/year and target class, then writes enrollment/student/audit. Promotion locks selected students and target classes and may update other students. Joined rows, FK checks and uniqueness can add dependencies beyond explicit locks. |
| Report / provenance | Review locks term before snapshot/report and audit; publication and withdrawal lock term `OF self` then report `OF self`. Attestation locks term, student and enrollment; revocation locks term then attestation. Audit and reviewer/attestor FKs may acquire user/tenant key-share locks at commit. Campus and other provenance writers are not fenced with these report operations. |
| Invoice / allocation / closure / reopening | Invoice generation takes FinanceSettings then term before invoice writes; allocation takes FinanceSettings, payment and ordered invoices. Closure/reopening lock term joined to year, then closure and other related records; final authorization may update another term. Deferred FK, uniqueness and audit checks can add waits. Preserve the observed invoice-versus-closure behavior and do not invert FinanceSettings/term ordering. |
| Subscription | Activation locks tenant then subscription rows and writes audit. `require_active_subscription` calls expiry UPDATEs, so authorization can acquire subscription locks before the visible workflow; its transaction boundary matters. |

An eligible non-partial unique index can both enforce uniqueness and affect
whether an UPDATE takes the stronger key-changing row lock. Index conflicts
can wait on an uncommitted transaction independently of row-lock ordering.
Deferred checks run at the outer commit unless made immediate, and making
them immediate can move, rather than remove, a wait. Multiple tenant
memberships, dynamic membership changes, joined rows, unique-index conflicts,
lock upgrades, refresh-token and subscription paths, and commands/admin
writes remain incompletely enumerated. Existing direct SQL, old application
instances, schedulers and other nonparticipants do not obey the advisory
fence, so it cannot guarantee their isolation or deadlock freedom.

### Candidate assessment and release gate

The PostgreSQL compatibility findings and alternatives A-E in Step 7C still
apply. A tenant no-key reservation is supported by evidence only for the
tested admission-number and password-reset scenarios; it permits key-share
FK checks and still excludes a competing no-key reserver. Key-changing
updates and deletion require stronger locks and can wait, while later lock
upgrades or uncoordinated writers can add other dependencies. It is not
approved for other `select_for_update()` uses or as a system-wide protocol.
The existing invoice/closure and invitation/user-first repairs must be
preserved. No audit FK has been weakened or moved to a separate transaction.

Before Step 7B resumes, finish the per-operation implicit-lock graph and
review all row-lock modes, key updates/deletes and unique-index waits; repeat
the missing cross-tenant, account-change, subscription, audit-commit and
candidate interactions in both orders on fresh migrated schemas; test
nonparticipating writer and rollout boundaries; and obtain database/security
and affected workflow owner approval for one coherent protocol. Define
outermost transaction and authorization revalidation rules, atomic audit and
constraint timing, failure/rollback behavior and bounded safe retries before
implementation. Also resolve the reproduced campus-publication race and
implement the selected fresh-review policy only in an approved subsequent
build step. Transfer-aware report issuance remains disabled.

## Stage 2A step 7E: class-campus and report provenance (design only)

**Status: NOT READY.** This section records a product decision and a
conditional design, not an implemented or PostgreSQL-verified locking
protocol. The shared-user/tenant deadlock remains a separate blocker. No
application code, migration, database, production data, permissions or
historical report was changed in this audit.

### Product decisions and what the current model actually means

The product owner selected these rules for this design:

1. **Freeze a class's campus once any enrollment exists**, including ended,
   withdrawn or transferred enrollments; do not silently relabel history.
   A real move requires a new class and a separately approved movement
   workflow. The current cross-campus transfer restriction remains in force;
   this decision does not imply that the application can presently move
   enrolled students to another campus.
2. Block new review and publication when campus evidence is missing or
   conflicting until an authorized person reconciles it. This design takes
   the conservative interpretation that a normal report needs both a
   non-null enrollment campus and a non-null class campus, with the same
   tenant-owned campus ID. An explicit no-campus reporting policy was not
   selected.
3. Keep cross-campus transfers blocked pending a separately approved
   workflow; do not enable transfer-aware report issuance.

These are intended rules for future work, **not claims about existing
enforcement**. In the current code:

- `SchoolClass.campus` is an optional, mutable class configuration;
  `StudentEnrollment.campus` is an optional separately stored FK, copied
  from class or student when an enrollment is created. Both use `PROTECT`
  on deletion. Neither has a dated campus history or immutable database
  constraint. `Student.campus` is a mutable profile/current-campus value,
  not proof of campus at a past term.
- Admission copies the class campus unless an allowed matching campus was
  supplied. Generic enrollment and promotion use class campus or student
  campus. The transfer endpoint ends the source enrollment the day before
  the effective date, creates a destination enrollment and updates the
  current student profile; it rejects a populated destination campus that
  differs from the populated student campus. A student with no profile
  campus may be assigned the destination campus, so that check is not a
  complete historical cross-campus policy.
- Class management presently changes only the class campus, with an audit
  event; it does not date the change, change enrollments or check for
  reports. `_preview` renders **enrollment.campus.name** and rejects
  class/enrollment mismatch only when both IDs are non-null. It therefore
  already treats the enrollment as its display source but permits missing
  values and does not prove that the copied value was historically correct.
  Class roster/export display or validate class campus separately.
- A report has separate term, student and enrollment FKs plus a JSON
  snapshot containing a campus **name**, not a campus ID, date interval or
  immutable source revision. It can preserve the text presented on a
  published report, but cannot independently establish campus identity,
  ownership or historical accuracy; names can change or collide. Existing
  reconciliation attestations contain campus IDs and evidence, but ordinary
  review does not use them, and provenance-linked publication is currently
  deliberately blocked. They must not be presented as an implemented fix.

For a **new ordinary report**, the intended authority is the verified
enrollment campus for the enrollment/term evidence, with the class campus
as a required consistency check under the freeze rule; the current student
profile and a later class location are not historical substitutes.
An enrollment FK alone is not reliable proof for legacy, manually changed
or already inconsistent data. Such cases require authorized reconciliation
with evidence rather than automatic copying or a guess. A separately
designed evidence/reconciliation workflow and its permissions remain an
implementation prerequisite; the current attestation path is not a license
to enable transfer-aware reports.

For an **already published report**, the existing stored snapshot is the
authoritative display of what was published, not necessarily proof that the
original campus was correct. Keep its JSON, report state and past versions
unchanged; any warranted correction requires a separately reviewed and
published new version and an audit trail. Individual PDF generation uses
the stored snapshot, though the PDF bytes themselves are not retained.
Current class-export validation can reject an inconsistent live roster even
when individual published reports exist; preserving historical snapshots
does not guarantee an export will remain available.

### Expected behavior by scenario

| Scenario | Intended rule; not yet implemented |
|---|---|
| Class campus change before any enrollment | A same-tenant active campus (or an unassigned value, subject to future validation) may be set through the authorized audited class-management workflow. Review is impossible without an enrollment; a missing campus blocks a later ordinary report. |
| Change after any enrollment, including historical ones | Reject a different campus, including clearing or assigning a previously null campus; leave class, all enrollment rows and audit trail unchanged on rejection. Same-value updates are not a relocation. Existing null/mismatched records need a separately approved, evidence-based reconciliation process, not an exception hidden in ordinary class editing. |
| Student changes class | Use the dated transfer workflow where its existing checks permit it: retain the source enrollment and create a destination enrollment; do not mutate old campus evidence. Multiple enrollments overlapping a term continue to block ordinary issuance. Reassignment by any other writer must not bypass these constraints. |
| Cross-campus transfer | Remains unsupported/blocked, even though today's null-profile edge shows the current guard is narrower. A future dated, authorized workflow needs separate business approval and provenance design. |
| Change after review | Under the freeze rule the attempted campus change is rejected. If existing or externally modified evidence is missing, conflicting or changed, publication must fail; withdraw the review with a reason and perform a fresh review after authorized reconciliation. Legacy unpublished reports without reliable evidence must also undergo the already selected withdrawal/fresh-review policy; it is not implemented. |
| Change after publication | Reject a campus reassignment for a class with any enrollment. Existing published snapshots and historical versions remain untouched, even if an old inconsistency is discovered; investigate and use a separately reviewed correction if warranted. |
| Change concurrent with review/publication | Coordinate the operations under an approved protocol, then re-read and validate. A class move that sees an enrollment is rejected; a report with missing, conflicting or changed evidence is rejected. No partial review, publication, campus update or success audit may commit on validation failure. |

The policy intentionally limits real school relocations until a future
dated class-location and movement design is approved. It does not silently
move a class or all of its students, or reassign invoice history.

### Reconstructed race and SQL evidence

Step 7D twice ran each order on separate migration-built disposable
databases using actual endpoints, independent connections and barriers.
The first operation was paused after executing its target statement and
before its atomic block committed; the second completed while it was
paused. Both returned 200; class campus became the new campus while the
enrollment and published snapshot kept the old campus. These tests establish
no waiting in those executions, not a universal absence of other locks.

The test probe and complete captured SQL were temporary and are no longer
available. The following is a **source-based normalized reconstruction** of
the statements, not a verbatim transcript or a claim that every implicit
FK query was captured:

| Campus management transaction | Report publication transaction |
|---|---|
| Authorization may issue subscription-expiry UPDATEs; inside `atomic`, `SELECT ... FROM academics_schoolclass WHERE tenant_id = ? AND id = ? FOR UPDATE`; validate new campus; `UPDATE academics_schoolclass SET class_teacher_id = ?, is_active = ?, campus_id = ? WHERE id = ?`; `INSERT INTO core_auditevent ...`; outer `COMMIT`. | Authorization; inside `atomic`, ordinary report term-ID lookup; `SELECT ... FROM academics_academicterm ... FOR UPDATE OF self`; `SELECT ... FROM academics_reportcardversion ... FOR UPDATE OF self`; ordinary overlap `SELECT/EXISTS` and publication `SELECT/COUNT` joining enrollment, examination and class; `UPDATE academics_reportcardversion SET status = ?, published_by_id = ?, published_at = ? WHERE id = ?`; audit INSERT; outer `COMMIT`. |

**A: campus change first.** The class `FOR UPDATE` and class UPDATE were
held open. Publication locked term and report, read other data using
ordinary READ COMMITTED queries, and committed with the old reviewed
snapshot; it neither locked the class row nor revalidated campus. The
campus transaction then committed the class update. Where a normal query
reads the uncommitted class version it sees the prior committed version,
not the pending update. Publication has no campus-equality check even if it
later reads the new committed class.

**B: publication first.** Publication was paused after taking its term
lock. Class management locked and updated the separate class row and
committed without waiting for term/report. Publication resumed, did not
validate campus and committed the old reviewed snapshot. In neither
schedule is the mere fact that a SELECT joins a class row equivalent to a
row lock; the two explicit row-lock sets do not intersect.

The class campus FK may require a key-share check on the **referenced
campus**; audit tenant/actor FKs were verified initially deferred on the
sampled migrated schema and can require key-share at commit. A class FK
check is not a lock of the report's term, report or enrollment, and the
report's unchanged FKs do not serialize the campus UPDATE. The exact
deferral and FK-trigger SQL for every class-campus constraint were not
independently captured in Step 7D; they remain to be verified rather than
assumed. Neither test establishes deadlock freedom or behavior of concurrent
subscription expiry, account updates or other writers.

### Required review/publication invariant

For a future **ordinary** report, after authorization and fresh reads inside
the eventually approved coordination/transaction boundary:

1. Report, term, academic year, student, enrollment, class and both campus
   references belong to the same tenant; report.student equals
   enrollment.student, class/year matches term/year, the enrollment
   overlaps the term, and its status/dates and examination records satisfy
   the existing rules. Reject another term-overlapping enrollment until
   transfer-aware issuance is separately designed.
2. Enrollment and class each have a non-null campus ID, both IDs match, and
   the referenced campus belongs to that tenant. The enrollment and its
   temporal history must be sufficiently reliable for the term; equal
   present-day FKs alone do not prove this for legacy or altered history.
3. At review, preserve a versioned evidence envelope identifying tenant,
   term/year, student, enrollment, class, campus ID, enrollment dates and
   relevant transfer history, plus the campus display name actually
   reviewed and other snapshot inputs. Protect all contributing writers
   and revalidate after waits. A name is presentation evidence, not an
   identity key.
4. At publication, compare fresh authoritative data and a validated
   evidence revision/fingerprint against what was reviewed **inside the
   same protected transaction**; reject stale, missing, revoked or
   inconsistent evidence rather than silently updating the snapshot.
   Compare reviewed display inputs, including a changed campus name, under
   the same policy: a change needs a fresh review. Snapshot-only or a
   check performed before acquiring protection is insufficient.
5. Previously published snapshots are not revalidated as a condition of
   preserving their historical content, rewritten, deleted or silently
   reclassified. Legacy unpublished reports lacking reliable review
   evidence must be withdrawn and genuinely reviewed again, using existing
   states and audits. Failure rolls back business and audit writes.

The exact envelope schema, handling of legacy records, authorized
reconciliation and review/withdrawal UX still require detailed design and
approval. Existing preview and publication do not satisfy these invariants.

### Protection options and conditional recommendation

| Strategy | Guarantees and limits; impact, locking and migration |
|---|---|
| Existing tenant advisory fence | Can serialize **participating** tenant writers and discovery/phantoms when acquired before reads and locks, with rereads and validation. It does not stop nonparticipants or preserve history by itself. Fence-first is not approved: Step 7B/7C demonstrated a shared-user/audit-FK deadlock, and subscriptions, finance and multi-tenant identity add edges. Existing helper needs no migration; broad integration, rollout controls and complete graph/tests are prerequisites. |
| Class-row locking with frozen-campus rule | A common class lock can serialize class changes against enrollment creation and report reads if all paths take it, check under lock and commit in one transaction. A bare lock does not detect stale review or protect other sources. Class-first can invert existing term→student→class, student→class, year→class→tenant and term→report orders; FK/unique waits and multi-class batches add edges. A specific mode/order is **not approved or demonstrated**. ORM locks need no migration; database-wide protection needs stronger enforcement and careful rollout. |
| Enrollment-row locking | Protects selected existing enrollment changes when every writer participates, but cannot alone prevent a class-campus update, another enrollment inserted as a phantom, cross-tenant errors or changes in exam and campus-name sources. Existing transfer and promotion orders make lock placement important. ORM locking needs no migration; it is complementary, not sufficient. |
| Immutable published snapshot / reviewed evidence | Preserves what was displayed and helps compare historical identity if an ID and revision are recorded; does not by itself prevent a concurrent update or prove old data was accurate. Historical reports remain unchanged. Versioned new evidence may use a documented JSON envelope or require a reviewed schema migration, depending on storage, constraints and legacy policy; existing name-only JSON cannot be treated as an ID. |
| Revision / fingerprint check | Detects a change between review and publication only if the revision covers every relevant source and is rechecked atomically under a proven concurrency mechanism; a check followed by an unprotected update is another race. All writers, revision increments and batch operations must participate; atomic compare-and-swap or locking still affects ordering. Durable revision columns or tables would require a migration; a JSON fingerprint alone does not ensure comprehensive writer coverage. |
| Database-enforced constraints / triggers | Properly designed database enforcement could cover SQL and old clients and prevent invalid cross-table writes or identity mismatch, but ordinary PostgreSQL CHECK constraints cannot safely assert other rows or temporal enrollment history. Composite tenant FKs, immutability triggers or exclusion/temporal designs have rollout, locking, backfill, legacy-data and operational costs; triggers also need a verified lock order and cannot guess historical truth. Likely migration(s), separate security/database review and cross-tenant tests; never weaken existing audit FKs. |

**Conditional recommendation:** adopt the owner-selected freeze and
enrollment-based historical evidence as the business model, combined with
transactional review/publication validation and immutable published
snapshots. Evaluate class/enrollment coordination and database enforcement
as complementary mechanisms within one verified system-wide protocol.
Do **not** implement a class-first lock, a fence-first rollout, a no-key
substitution or a trigger solely on the basis of these results. The exact
technical mechanism and any migration remain undecided until the
shared-user lock problem, complete writer graph, nonparticipant boundary and
real PostgreSQL matrix are resolved. Each access, lookup and any eventual
constraint must remain tenant-scoped; cross-tenant equality must never be
inferred from matching names.

### Required PostgreSQL acceptance matrix

These are **future tests**, not Step 7E results. Run repeated schedules in
both orders on fresh, migration-built disposable PostgreSQL databases using
independent transactions, deterministic barriers, bounded timeouts and
`pg_blocking_pids`/wait-event evidence where a wait is expected; assert
actual commit outcomes, SQLSTATEs, audit rows, snapshot bytes/JSON and
tenant-scoped final state. A report failure must leave no partial report,
status update, revision or success audit. Failure or `40P01` is not a pass.
Use a synchronized precondition/lock phase to prove intended conflicting
operations block; if policy rejects an invalid move before any conflict,
assert the rejection and separately test the actual shared-lock schedule
rather than manufacturing a wait.

| Concurrent schedule | Required result |
|---|---|
| Class-campus change first / publication first, with an enrollment | Both orders: conflicting phase waits as dictated by the approved protocol, then the campus change rejects; a publication with valid unchanged evidence may commit. No old/new campus mix, partial audit or 200 for a rejected change. |
| Campus change during individual and batch review, both orders | Freeze rejects class change after enrollment; valid review may commit only with a consistent evidence envelope. Missing/mismatched campus rejects review; an invalid batch rolls back every review and audit. |
| Campus change while publication is in progress, both orders | Verify waiting/re-read after commit, and reject changed, missing, foreign-tenant or stale evidence including name/revision changes; never rewrite a reviewed snapshot to make it pass. |
| Same-campus student transfer versus review and publication, both orders | Observe existing affected-term waits; overlapping enrollment histories block ordinary review/publication. A publication that commits before a transfer keeps its snapshot; a reviewed version made stale by a later transfer cannot publish. Cross-campus requests remain rejected. |
| Class reassignment / new enrollment versus review and publication, both orders | Exercise actual admission, generic enrollment, transfer and promotion paths, including a class with no enrollment concurrently receiving its first enrollment and a campus edit. Prove no phantom bypass and no mixed snapshot; do not rely on an enrollment lock for a row that did not exist. |
| Published history after later authorized changes and attempted relocation | Individual retrieval and PDF continue to render the originally stored campus; no published JSON/state or prior version changes. Test any separately approved correction as a new reviewed version. Explicitly test current class-export rejection or a separately approved export policy. |
| Two students in different tenants | Same-named campuses and shared actors cannot cross tenant boundaries; a coordination mechanism must not needlessly block distinct tenant operations, except for documented shared-user/FK contention. Test both actor arrangements and both orders; assert no foreign IDs or audit leakage. |
| Rollback / legacy / reconciliation | Fail campus validation after writes would otherwise occur, including batch review and publication; verify all business/audit writes roll back, no success response precedes outer commit, and legacy unpublished reports cannot publish until withdrawn and freshly reviewed. Test null, stale, revoked, foreign-tenant and fabricated evidence. |
| Lock-graph regressions | Re-run password reset/admission both orders, invitation/verification both orders, same-user cross-tenant writes, subscription operations, term closure/reopening, report/withdrawal, invoice/closure and payment allocation. Assert the existing FinanceSettings→term and term→report behavior and no `40P01`; include any new FK, trigger or revision locks. |

The campus design does not fix the separately reproduced shared-user/audit
cycle. No technical lock protocol may be approved until that graph, audit
atomicity, the finance/closure ordering, mixed-version deployment and
nonparticipating SQL writers are addressed. No new PostgreSQL experiments
or migration consistency checks were run in Step 7E.

## Stage 2 finalization: current attempt (incomplete; NOT READY)

**Both requested workstreams remain unresolved.** This attempt includes a
narrow account-token expiry fix described below, but no application
locking, campus or report fix. The candidate lock substitutions below do
not yet meet the requested integrated correctness and concurrency gate.
In particular, do not deploy those test-only substitutions or infer that a
passing subset is a complete protocol. Existing published snapshots and
transfer-aware issuance were not changed. Direct SQL, older application
instances and other nonparticipants remain unprotected.

### Persisted work and testing environment

- Added `backend/academics/migrated_fixture_test_settings.py`, which reuses
  the local-only fixture runner's identity check, random database names and
  no-reuse guard, but builds each newly created disposable database with
  the project's existing migrations.
- Added `backend/academics/test_identity_lock_regressions.py`, with real
  PostgreSQL concurrent endpoint tests for reset versus verification and a
  verification or reset token expiring while waiting for its row lock.
  These assert the **required safe behavior**, not a characterization that
  accepts `40P01` or an expired token. The deadlock test still exposes an
  unresolved failure; run them with:

  ```sh
  cd backend
  NLSCHOOL_DISPOSABLE_FIXTURE_TEST=local-only \
  DJANGO_SETTINGS_MODULE=academics.migrated_fixture_test_settings \
  .venv/bin/python manage.py test academics.test_identity_lock_regressions
  ```

  Before the expiry fix the initial two tests failed: reset versus
  verification returned `40P01` rather than the expected 400, and the
  expired verification token returned 200 rather than 400. On a separate
  migrated database those two tests passed with a temporary user-first,
  post-lock-expiry-check monkeypatch. That experiment is not an implemented
  lock change or system-wide approval.

- Updated `backend/core/auth_email.py` to check a locked action's expiry
  again after the query and any row-lock wait. If it has expired, the
  existing invalid/expired response path is used and the token is not
  consumed. The two expiry races and eight existing authentication tests
  passed together against a migrated disposable database. This does not
  alter the lock order or solve the deadlock.
- Added `backend/academics/test_campus_lock_regressions.py` to assert that
  a generic enrollment revalidates campus after waiting for a class update;
  its migrated-database run failed because the current application returned
  201 rather than 400 and allowed the inconsistent enrollment.
- This status file, that application file and the three test-support files
  are the only repository files changed in this attempt. Earlier worktree
  changes are not reverted.
  No migration was created. Existing migrations were applied only to new,
  randomly named disposable local test databases, which the runner destroys;
  no production database, roles, privileges or deployment configuration
  was changed.

### New PostgreSQL evidence

The probes used independent PostgreSQL transactions, actual DRF endpoints,
barriers, bounded timeouts and `pg_blocking_pids`; candidate substitutions
were confined to temporary test process monkeypatches. Admission was
force-authenticated as a verified owner and experimentally wrapped in the
existing tenant advisory fence; the endpoint's 201 was observed before the
outer commit, not assumed durable.

| Schedule | Observation |
|---|---|
| Original fenced admission versus password reset, both commit orders | Reproduced on two fresh migrated databases: each order produced `40P01`; the database aborted one transaction. Deferred audit tenant/user FK diagnostics identified the same opposing `KEY SHARE` waits as Step 7C. A first repeat had an instrumentation failure due to transient wait-state sampling; the corrected run reproduced both orders. |
| Test-only joined token/user `FOR NO KEY UPDATE`, admission/reset | Both orders completed 201/200 in repeated migrated tests. This was not a safe solution: reset first versus verification produced `40P01` when reset invalidated another token while verification had obtained its token and waited on the user. The original joined `FOR UPDATE` also produced the same reset/verification deadlock in that schedule. |
| Test-only shared-user-first token lookup | Acquiring user no-key first, then locking/revalidating the token, passed the sampled reset/admission, reset/verification and verification/invitation orders; repeated identity tests passed on fresh migrated databases. These samples do not certify every identity writer or lock edge. |
| Token expires while its lock is held | The original application and the initial user-first candidate returned 200 and consumed an expired verification token because the expiry cutoff was calculated before a token-lock wait. Test-only post-lock expiry checks returned 400 for verification and reset in separate migrated runs. The application now rechecks expiry after its locked query; the durable regressions exercise the actual endpoints. Other lock/revocation paths still need integrated verification. |
| Invitation acceptance versus admission | With an existing same-tenant membership, acceptance holds the user `FOR UPDATE` and waits for its invitation joined to tenant while admission holds tenant and waits for the audit actor FK. Original locks produced `40P01` in both starting orders; substituting tenant no-key for admission still produced `40P01` in the admission-first order. The acceptance would otherwise reject the existing membership (409); this is a real endpoint/database lock interaction, but not a test of successful invitation acceptance. |
| Test-only invitation user no-key lock | In both starting orders, the sampled conflicting-membership invitation returned 409 and admission committed 201. This was repeated on a second migrated database. A combined candidate also passed nine selected identity, cross-tenant invitation and token-security tests on one migrated database. This is not sufficient coverage of successful acceptance under every tenant/user/transaction ordering, lock upgrades or unrelated writers. |
| Class campus update first versus generic enrollment | Twice on separate migrated databases: campus update held the class row, generic enrollment validated the old class/student campus, then waited on that row. After the new campus committed, enrollment returned 201 using the new class campus and updated the student's campus without revalidating its earlier campus check. This demonstrates that a class freeze check alone cannot safely handle the first-enrollment race; it does not demonstrate a correct freeze implementation. |

The prior Step 7D class-campus/report races remain unresolved. There is no
approved technical order combining the proposed campus freeze, transactional
report provenance, user/tenant coordination and audit FK checks. Enrollment
creation, admission, transfer, promotion, report individual/batch review and
publication and other provenance writers still need a consistent protected
read/revalidation design. Preserve term-before-report and
FinanceSettings-before-term. Missing/contradictory campus evidence and
legacy unpublished reviews are not yet protected by the requested policy.

### Baseline regression and remaining gate

On one fresh migrated database, 26 existing tests passed covering report
review/publication/withdrawal and closure, shared-user and invitation
regressions, admission-number races, transfers, invoice/closure and
payment-allocation/void in both selected orders. This baseline does not
include a campus or identity fix and therefore is not the required
post-implementation integrated matrix. Django system checks passed and
`makemigrations --check --dry-run` reported no changes. No frontend code was
changed.

Before declaring READY, implement and validate a coherent solution that
also addresses the newly observed reset/verification, invitation/admission
and enrollment/campus races and preserves the expiry fix; test both orders
and security checks for the final actual implementation on fresh migrated
databases; then run the requested complete integrated regression suite.
Document migration and rollout controls if any are needed. Do not remove
or rewrite historical reports, and do not enable transfer-aware issuance.

## Focused PostgreSQL failure corrections (Stage 2A remains NOT READY)

This milestone addresses the reproduced reset/verification deadlock and
the application-managed class-campus/first-enrollment race. It does not
approve a complete shared-user/tenant protocol or transactional report
provenance, and does not close Stage 2A.

### Reset versus verification

On a new disposable database built from the existing migrations, reset
first again returned 200 while verification failed with PostgreSQL
`40P01`. Verification first returned 200, followed by reset 200. The
captured reset and verification lock query was, with parameters omitted:

```sql
SELECT "core_emailactiontoken"."id", "core_emailactiontoken"."user_id", "core_emailactiontoken"."purpose", "core_emailactiontoken"."token_hash", "core_emailactiontoken"."expires_at", "core_emailactiontoken"."used_at", "core_emailactiontoken"."created_at", "core_user"."id", "core_user"."password", "core_user"."last_login", "core_user"."is_superuser", "core_user"."first_name", "core_user"."last_name", "core_user"."is_staff", "core_user"."is_active", "core_user"."date_joined", "core_user"."email", "core_user"."email_verified", "core_user"."full_name" FROM "core_emailactiontoken" INNER JOIN "core_user" ON ("core_emailactiontoken"."user_id" = "core_user"."id") WHERE ("core_emailactiontoken"."expires_at" > %s AND "core_emailactiontoken"."purpose" = %s AND "core_emailactiontoken"."token_hash" = %s AND "core_emailactiontoken"."used_at" IS NULL) ORDER BY "core_emailactiontoken"."id" ASC LIMIT 1 FOR UPDATE
```

In the observed plan, reset held its token and shared user; verification
held its different token while waiting for the user. Reset then executed:

```sql
UPDATE "core_user" SET "password" = %s WHERE "core_user"."id" = %s
UPDATE "core_emailactiontoken" SET "used_at" = %s WHERE "core_emailactiontoken"."id" = %s
UPDATE "core_emailactiontoken" SET "used_at" = %s WHERE ("core_emailactiontoken"."used_at" IS NULL AND "core_emailactiontoken"."user_id" = %s)
```

The last statement waited for verification's token while verification's
joined lock waited for reset's user. PostgreSQL reported each process
waiting for a `ShareLock` on the other transaction ID and aborted
verification. The smallest observed cycle therefore needs the joined
token/user lock, a second token/user lock, and reset's bulk invalidation;
the audit write was not required for this particular cycle.

`core/auth_email.py` now finds the token's user without a lock, locks that
user `FOR NO KEY UPDATE`, then locks and revalidates the token
`FOR NO KEY UPDATE`. It verifies the user ID, token hash, purpose,
unused state and expiry after locking, and retains the post-wait expiry
recheck. Reset and verification serialize on the user before either locks
an action; these operations update non-key fields. This is not a general
approval of no-key locks for referenced-key updates or other writers.
Invitation acceptance's existing user-then-invitation order is unchanged.

The test-only candidate passed reset/verification in both orders twice
before the application change. It also passed the expanded reset request
versus confirmation, successful and conflicting invitation versus
verification, and successful invitation versus reset schedules in both
orders on two fresh migrated databases. One earlier expanded candidate run
had four barrier failures after multiple anonymous requests shared the
authentication throttle; the barriers did not capture a response proving
the cause. The tests now clear the local test cache per case, and the
expanded candidate passed on both subsequent runs. A still earlier
invitation test had a barrier hard-coded to the old joined `FOR UPDATE`
query; its synchronization was updated to recognize both tested lock
forms without relaxing the invitation lock-order assertions.

After implementation, the combined 50-test suite described below passed
twice. It checks expiry during a token lock wait, token replay, reset
request invalidation, refresh-token revocation, rollback on forced audit
failure, tenant-scoped account audit events, invitation expiry and
revocation, successful and rejected invitation acceptance, and the
relevant two-order races. Ten additional existing invitation and
verification diagnostics passed, including repeated verification versus
invitation and revocation/expiry after waits.

### Class campus and enrollment

The original generic-enrollment schedules were reproduced on a fresh
migration-built database. With campus change first, the class update
returned 200 and enrollment returned 201 using a newly read class campus
after its pre-transaction validation had checked the old campus. With
enrollment first, enrollment returned 201 and the class change returned
200 after waiting, leaving class and enrollment campuses different. Both
are incorrect successes.

Class management already locked the class `FOR UPDATE`; it now checks for
**any** enrollment, including withdrawn and transferred records, before
changing that class's campus in the same transaction. Generic enrollment
already locked student then class; it now revalidates their campus after
the class wait and uses those locked, fresh objects when saving. An
unenrolled class can still change campus; a same-campus update is allowed.
The existing admission, transfer and promotion application paths also lock
the destination class before inserting an enrollment.

A separate regression reproduced a 201 cross-campus transfer when the
student profile campus was null but the locked source enrollment and class
identified the old campus. Transfer now rejects a known destination-campus
conflict with either source record, in addition to the existing student
profile check. Missing source or destination campus evidence remains an
unresolved policy and integrity boundary; this change does not establish
that every legacy cross-campus move with unknown evidence is blocked.

The two generic-enrollment race orders passed in each of two final
migration-built runs, with PostgreSQL blocking observed and expected
400/201 outcomes and committed data. Campus checks also cover changing a
class before and after enrollment, all enrollment statuses, tenant
isolation, the null-profile transfer case, and rollback of class and
enrollment writes on forced audit failure. The selected transfer and
enrollment concurrency regressions passed in both final runs.

This is an **application-path** guard, not a database-enforced permanent
freeze. Direct ORM/SQL writes, older application instances and other
nonparticipants can bypass it; deleting all enrollment evidence would
also remove the `exists()` basis for the guard. No new trigger, immutable
history marker or migration was added. A class with a null campus and an
enrollment is frozen too; any correction policy needs separate design.

### Verification and remaining gate

Each final run created a fresh local disposable PostgreSQL database using
the project's existing migrations. Both combined runs passed **50/50**
tests covering the identity, invitation, campus, transfer and enrollment
regressions plus existing account and staff-invitation security tests.
A separate migrated run passed **10/10** identity diagnostics; another
passed **14/14** selected admission-number, report publication/withdrawal
and invoice-versus-closure tests. Django system checks passed and
`makemigrations --check --dry-run` reported no changes. No migration was
created, and existing migrations were applied only to disposable local
test databases, which were destroyed. No production database, privileges,
deployment or historical published report was changed.

Report-provenance validation is **not implemented**. The tested
application guard does not protect nonparticipating writers or permanently
record that an enrollment once existed; existing reviewed snapshots also
store a campus name rather than a trustworthy campus identity. Missing or
conflicting campus evidence can therefore still reach review/publication.
Do not treat matching names or a legacy enrollment FK as proof of
historical provenance, and do not rewrite published snapshots. A protected
provenance protocol and its review/publication, rollback, legacy and
cross-writer PostgreSQL tests remain necessary before enabling this rule.

The previously demonstrated invitation/admission tenant/user audit-FK
deadlock is separate and remains unresolved. The new identity lock order
does not certify every shared-user writer, cross-tenant interaction or
mixed-version rollout. The complete Stage 2A integrated matrix remains
outstanding. Stage 2A is **NOT READY**; Step 7B must not restart until
these blockers and the previously recorded outstanding gates are resolved.

## Invitation/admission deadlock correction (Stage 2A remains NOT READY)

The previously observed invitation/admission `40P01` was reproduced in
both starting orders on a fresh PostgreSQL database built with the project's
existing migrations. The test used the real DRF views, independent
connections, an outer admission transaction, bounded waits and
`pg_blocking_pids`. Admission was force-authenticated as the same verified
owner targeted by the same-tenant staff invitation. No middleware or
experimental advisory fence was included. The 201 observed inside the
outer admission transaction was not treated as durable until commit.

Admission locked the academic year and class, then reserved its admission
number under this tenant-row lock:

```sql
SELECT "core_tenant"."id", "core_tenant"."name", "core_tenant"."slug", "core_tenant"."email", "core_tenant"."phone", "core_tenant"."country", "core_tenant"."region", "core_tenant"."address", "core_tenant"."school_type", "core_tenant"."status", "core_tenant"."created_at", "core_tenant"."updated_at" FROM "core_tenant" WHERE "core_tenant"."id" = %s LIMIT 21 FOR UPDATE
```

It inserted a student, enrollment and two audit events, including:

```sql
INSERT INTO "core_auditevent" ("id", "tenant_id", "actor_id", "action", "entity_type", "entity_id", "timestamp", "request_id", "metadata", "ip_address", "user_agent") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

The original invitation acceptance locked the actor/user first and then
attempted to lock the invitation joined to its tenant:

```sql
SELECT "core_user"."id", "core_user"."password", "core_user"."last_login", "core_user"."is_superuser", "core_user"."first_name", "core_user"."last_name", "core_user"."is_staff", "core_user"."is_active", "core_user"."date_joined", "core_user"."email", "core_user"."email_verified", "core_user"."full_name" FROM "core_user" WHERE UPPER("core_user"."email"::text) = UPPER(%s) ORDER BY "core_user"."id" ASC LIMIT 1 FOR UPDATE
SELECT "core_membershipinvitation"."id", "core_membershipinvitation"."tenant_id", "core_membershipinvitation"."email", "core_membershipinvitation"."role", "core_membershipinvitation"."token_hash", "core_membershipinvitation"."invited_by_id", "core_membershipinvitation"."student_id", "core_membershipinvitation"."guardian_relationship", "core_membershipinvitation"."guardian_is_primary", "core_membershipinvitation"."expires_at", "core_membershipinvitation"."accepted_at", "core_membershipinvitation"."revoked_at", "core_membershipinvitation"."created_at", "core_tenant"."id", "core_tenant"."name", "core_tenant"."slug", "core_tenant"."email", "core_tenant"."phone", "core_tenant"."country", "core_tenant"."region", "core_tenant"."address", "core_tenant"."school_type", "core_tenant"."status", "core_tenant"."created_at", "core_tenant"."updated_at" FROM "core_membershipinvitation" INNER JOIN "core_tenant" ON ("core_membershipinvitation"."tenant_id" = "core_tenant"."id") WHERE "core_membershipinvitation"."token_hash" = %s ORDER BY "core_membershipinvitation"."id" ASC LIMIT 1 FOR UPDATE
```

Both orders produced `40P01`: the invitation process waited for a
transaction `ShareLock` while locking the `core_tenant` tuple, and the
admission transaction waited for the invitation transaction. On the
migration-built schema, audit foreign keys to both tenant and actor/user
are `DEFERRABLE INITIALLY DEFERRED`; the admission commit's actor FK
`KEY SHARE` check is incompatible with the invitation's user `FOR UPDATE`.
The invitation was waiting for admission's tenant `FOR UPDATE`. The
invitation row and admission-number reservation participate in the route
to the cycle, but the reproduced conflicting rows were the tenant and
shared user; no existing student row was involved. The same-tenant
invitation ultimately should be rejected as an existing membership (409),
not accepted.

The narrowly scoped change in `core/views.py` retains user-first ordering
and changes only the existing user's invitation lock to
`FOR NO KEY UPDATE`. Its `KEY SHARE` compatibility permits the deferred
audit FK check while still conflicting with competing no-key or stronger
user locks. Invitation processing updates password, full name and
verification state, not the user's referenced primary key. The joined
invitation/tenant `FOR UPDATE`, fresh expiry/revocation/tenant checks and
the existing user-before-token account-action correction are unchanged.
No audit constraint or validation was weakened. This is not a blanket
approval of no-key locks for unrelated writers or referenced-key changes.

Before implementation the temporary candidate passed the original
same-user conflicting-membership schedule twice per starting order, and
then an expanded seven-test focused suite twice. The latter also included
successful same-tenant acceptance for a different admission actor,
successful same-user cross-tenant acceptance, both starting orders, and
forced audit rollback. A candidate matrix of 51 and 52 identity,
invitation and admission tests passed on separate migrated databases; the
test count changed because the rollback test was added between runs.

After implementation, full identity/invitation/admission suites passed
**54/54 and 55/55** tests on separate migrated databases. The second added
the reverse same-user/two-tenant invitation test. The final focused suite
then passed **18/18 twice** on two further migrated databases. Each of the
following was exercised twice in that final focused suite:

- admission first and invitation first for the original shared-user cycle,
  with actual tenant blocking observed and outcomes 201/409;
- successful same-tenant acceptance for a different admission actor, in
  both starting orders, with tenant blocking and committed 201/201;
- successful same-user acceptance in another tenant versus admission, in
  both starting orders, with admission committing while the invitation
  user lock remained held, followed by 201 acceptance;
- same-user invitations in both tenant orders, verification/invitation and
  reset/invitation orders, invitation replay and audit rollback.

The larger suites also covered expiry and revocation while waiting, token
validation and one-time use, account expiry checks, reset revocation,
tenant-scoped audit writes, and admission-number concurrency/uniqueness.
Preserved campus, report publication/withdrawal and invoice/closure
regressions passed **15/15** in a separate migrated run. Django system
checks passed, migration consistency reported no changes, and no new
migration was created. Existing migrations were applied only to fresh,
randomly named local disposable test databases, which were destroyed.

Changed files for this milestone: `backend/core/views.py`,
`backend/academics/test_invitation_admission_races.py`,
`backend/academics/test_shared_user_races.py`, and
`backend/academics/test_identity_deadlock_diagnostics.py`, plus this status
document. Existing test lock matchers were updated to recognize the
no-key mode while retaining user-before-invitation order assertions.

This resolves the **observed** invitation/admission schedule, not the
complete system lock graph. The original shared-user case necessarily
rejects an invitation to an existing owner; successful same-tenant
acceptance was separately tested with a different admission actor, and
successful shared-user acceptance with a different tenant. Arbitrary
tenant-first writers that update the same user, key-changing operations,
old application instances, raw SQL and all mixed-version rollout orders
are not certified by these tests. Transactional campus/report provenance
and the complete Stage 2A integrated matrix remain outstanding.
Stage 2A is **NOT READY**.

## Campus integrity enforcement milestone

Stage 2A remains **NOT READY**. Report-provenance validation and the final
integrated regression matrix are not part of this milestone.

The migration-built PostgreSQL schema had deferred enrollment foreign keys
and no application triggers on the class or enrollment tables. Application
locks alone cannot enforce the freeze against ORM bulk updates, direct SQL,
or older application writers. Migration
`academics/0021_class_campus_freeze.py` therefore adds a database-only,
durable class marker and two PostgreSQL triggers. It backfills the marker
from existing enrollment rows without changing enrollment or report data.
On enrollment insertion or changes to its class, student, tenant or campus,
the trigger updates and locks the destination class, marks it frozen and
checks tenant ownership and known non-null campus conflicts. A class trigger
rejects changing the campus or tenant of a frozen class or clearing its
marker. The marker is retained if enrollments are deleted or reassigned,
and is rolled back with a failed transaction. Null campus evidence is still
allowed for ordinary enrollment; it is not treated as verified provenance.

The competing first-enrollment and class-update transactions serialize on
the class row. In the tested enrollment-first order, the campus update
waited and failed with PostgreSQL `23514`; in the campus-first order, the
enrollment with stale known campus evidence waited and failed with `23514`.
The existing application-level checks remain in place, and the class API
translates the specific database freeze violation to its existing 400
validation response.

Transfers and promotions/repetitions into another class require matching,
non-null campus evidence for the student, source enrollment, source class
and destination class, and the campus must belong to the tenant. A valid
same-campus move is supported; missing or conflicting evidence and
cross-campus moves are rejected without inferring or rewriting historical
values. This intentionally rejects moves at schools with incomplete campus
data until their evidence is reconciled. Graduation and leaving do not
create a destination enrollment and are not subject to this move check.

Validation used fresh randomly named, migration-built local disposable
PostgreSQL databases, destroyed after each run; no production database or
permissions were changed:

- The final database-freeze, migration-backfill and same-student transfer
  suite passed **10/10 twice**, on two separately created databases. Each
  run observed actual PostgreSQL blocking for both first-enrollment versus
  campus-change orders and both transfer versus another student's enrollment
  orders. The former produced one commit and one `23514`; the latter
  committed both valid same-campus operations. Each run also verified
  same-student transfer serialization, direct ORM and SQL changes before
  and after enrollment, every enrollment status, deleted-enrollment
  history, reassignment, rollback, tenant checks and legacy backfill.
- Campus, transfer, enrollment and promotion suites passed **32/32** and
  **35/35** in successive runs; the latter included additional database
  test coverage. A separate integrated selection passed **29/29**, covering
  migration backfill, invitation/admission and reset/verification races,
  admission-number concurrency, invoice/term closure and report
  publication/closure. Important race orders were exercised by those tests,
  but this selection is not the final integrated matrix.
- An earlier 21-test run had three failures from transfer fixtures lacking
  the now-required campus evidence; those fixtures were corrected. A later
  compatibility selection had **37 passes and one failure** for the same
  reason in the two-transfer race fixture. After correcting that fixture,
  the previously failing race passed in both final runs above. These
  failures are recorded rather than interpreted as successful runs.
- Django system checks and migration consistency checks passed. Pylance
  reported no syntax errors in the changed Python files. The new migration
  and existing migrations were applied only to the disposable test
  databases; there was no production migration.

Files changed in this milestone: `backend/academics/campus.py`,
`backend/academics/migrations/0021_class_campus_freeze.py`,
`backend/academics/transfers.py`, `backend/academics/promotion.py`,
`backend/academics/views.py`, `backend/academics/tests.py`,
`backend/academics/test_transfers.py`,
`backend/academics/test_enrollment_concurrency.py`,
`backend/academics/test_cross_module_workflow_races.py`,
`backend/academics/test_campus_database_freeze.py`,
`backend/academics/test_campus_freeze_migration.py`, and this document.

Limits: the backfill cannot discover enrollments deleted before the
migration; existing missing or conflicting evidence is retained, not
certified. The triggers do not protect arbitrary later changes to student
campus or campus ownership, privileged trigger disabling, all possible
direct SQL changes, or every multi-row lock order. Older writers are
protected by the active PostgreSQL triggers for the tested class/enrollment
operations, not certified for all cross-module deadlocks. Non-PostgreSQL
databases do not receive this trigger protection. Reversing migration 0021
removes the triggers and marker: freeze history for classes whose
enrollments have been deleted can be lost even after reapplying it.
Rollback therefore requires an explicit preservation/reconciliation plan
and must not be treated as a safe automatic reversal. Report review and
publication still need transactional provenance validation and their full
PostgreSQL race matrix before Stage 2A can be reconsidered.

## Transactional report-campus provenance

The individual and batch review paths now require exactly one enrollment
for the student in the report's academic year whose dates intersect the
term. It must be the requested enrollment; its student, enrollment, class,
term, year and campus must belong to the tenant, and the enrollment and
class campus must be present and equal. Multiple term enrollments remain
unsupported even when their dates do not overlap each other. Missing,
foreign or conflicting evidence is rejected, not inferred or repaired.
The existing examination and subject checks still establish that the
selected results belong to the same class and term.

New reviewed snapshots contain `_provenance` identifiers for the term,
year, student, enrollment, class and campus. Publication locks and
revalidates the evidence and requires the identifiers to match the review;
it still checks the current examination publications. A legacy reviewed
snapshot without these identifiers cannot be published: it must be
withdrawn and a new version reviewed. Published snapshots and their audit
history are not rewritten. A snapshot records what was displayed and is not
proof that legacy data was historically correct. Provenance-attestation
linked and transfer-aware issuance remain blocked as before.

The established term-before-report order is unchanged. Within the atomic
review/publication transaction, PostgreSQL audit tenant and actor references
are first acquired `FOR KEY SHARE NOWAIT`; the student, the student's
existing enrollments and their classes, the academic year and the selected
campus are acquired `FOR UPDATE NOWAIT`. The student lock fences
commit-time enrollment foreign-key checks, while the existing class trigger
continues to enforce the campus freeze. Enrollment and class rows are
locked in primary-key order. An explicit `55P03` from these provenance
locks aborts the transaction and retries it at most three times, with
50 ms and 100 ms delays; after exhaustion the API returns a clear 409.
Other database errors are not converted to successes or retried. Batch
review retries the entire batch, preserving its all-or-nothing behavior.

The choice was informed by migration-built PostgreSQL experiments. Before
the change, a direct enrollment insert into another class committed after
the publication overlap query but before publication, and publication
still succeeded. A test-only blocking student-then-enrollment candidate
fenced the insert but produced `40P01` against an enrollment update whose
deferred student FK check waited at commit; the report waited on the
enrollment row. Reversing those locks produced `40P01` against a transfer
in a later term: the transfer held the student and waited for the
enrollment, while the report held the enrollment and waited for the
student. Both cycles were observed with `pg_blocking_pids` and SQL on
repeated disposable databases. A test-only NOWAIT candidate avoided the
first cycle; it was not adopted until the actual endpoint and related
regressions were exercised. Audit tenant and user key-share locks were
included to avoid deferring those reference waits until commit.

PostgreSQL validation used fresh randomly named databases built by the
project migrations, destroyed afterward:

- The final report, legacy migration and report-lock-order selection passed
  **38/38 twice** on separate databases. Its deterministic concurrency
  tests exercised review and publication versus enrollment changes,
  class-campus changes and transfers in both starting orders; report
  review versus publication; publication versus closure; tenant and user
  audit-reference locks in both orders; deferred-FK inversion; later-term
  transfer; direct enrollment insertion; retry exhaustion and successful
  retry. These tests assert actual blocking or the specified 409 and
  inspect final data and audit outcomes. Each of those selected schedules
  ran once per database.
- A broader integrated selection passed **280/280** on a separate migrated
  database. It included the report, provenance, campus, transfer,
  enrollment, invitation/admission, reset/verification, admission number,
  invoice/closure, payment/void, subscription, audit, examination and term
  closure regressions. A test-only MD5 password hasher reduced runtime;
  production settings were not changed. The new legacy withdrawal and
  re-review assertion was additionally run and passed separately.
- An earlier 111-test combined run had one test-harness `TimeoutError` in
  the marks-first/review race while several large test processes were
  running concurrently. That schedule passed twice in isolation with real
  term-row blocking, and also passed in the later 280-test selection. An
  earlier 27-test report run had three fixture/message expectation
  failures; after correcting the same-campus transfer fixture and
  retaining clear reconciliation wording, those three passed, and the
  later report and integrated selections passed.
- Django system checks passed, migration consistency reported no changes,
  and Pylance syntax checks passed for the changed Python files. No
  migration was created for this milestone. Existing migrations, including
  campus freeze migration 0021, were applied only to disposable local test
  databases; none was applied to production.

Changed files: `backend/academics/report_campus.py`,
`backend/academics/report_cards.py`, `backend/academics/report_card_batch.py`,
`backend/academics/test_report_campus.py`,
`backend/academics/test_campus_freeze_migration.py`,
`backend/academics/test_report_publication_concurrency.py`,
`backend/academics/test_report_card_lock_order.py`,
`backend/academics/test_report_card_batch.py`,
`backend/academics/test_examinations.py`,
`backend/academics/test_cross_module_workflow_races.py`, and this document.

Limitations remain. The PostgreSQL behavior depends on migrations, deferred
foreign keys and participating report paths; SQLite does not provide the
same protection. Raw SQL or older application instances can bypass report
validation and snapshot/version controls, and privileged clients can
disable database triggers. Direct writes after publication can change
source data without rewriting the historical snapshot. Legacy campus
evidence and classes whose enrollments were deleted before migration
cannot be certified from these checks. Locking all existing enrollments
and their classes can cause legitimate concurrent work to receive 409;
the sampled schedules do not establish the entire cross-module lock
graph. Coordinated writer fencing, rollout and production-schema review
remain necessary. Stage 2A remains **NOT READY** pending those controls and
the formal readiness decision; this selection is not a certification of
all writers or every possible transaction order.
