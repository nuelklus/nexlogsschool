# NLSchool recovery materials

This is a partial recovery bundle made from material preserved in ChatGPT conversation/library history. It is NOT the original full repository.

## Included
- `backend/core/models.py`: model source recovered from a terminal transcript dated 2026-10-19. It includes User, Tenant, Membership, Campus, AcademicYear, SubscriptionPlan, TenantSubscription, MembershipInvitation, EmailActionToken, and AuditEvent.
- `Pasted text(4).txt`: original transcript, retained for provenance and additional clues.
- `NLSchool_Registration_Key_Points.docx`: saved requirements for staff, parent, and student onboarding.

## Important limitations
- No complete Django project, migration files, settings, serializers, views, URLs, tests, or Next.js source were recovered in this bundle.
- The recovered `models.py` must be reviewed against the rest of the project before use. Do not assume it will migrate cleanly by itself.
- The transcript records model definitions and a shell inspection, not a verified runnable project backup.

## Recommended next steps
1. Check Microsoft OneDrive, Google Drive, Dropbox, Windows backup, and any external drives for the original `NLSchool` folder.
2. Check the coding assistant's chat/workspace history for more code outputs.
3. If no full copy exists, use this file as a reference while reconstructing a clean repository.
4. Create a private GitHub repository immediately once the rebuilt project exists; add `.env`, secrets, local databases, and `.venv` to `.gitignore`.

## Local recovery check (2026-10-25)

The available local Desktop, Documents, Downloads, OneDrive, Google Drive, iCloud Drive, and CloudStorage folders were checked for NLSchool-named folders and likely source/backup files. This found:

- `Desktop/NLSchool_Partial_Recovery.zip`, containing the registration requirements document, this recovery README, the original terminal transcript, and `backend/core/models.py`.
- `Desktop/nlschool_recovery/`, a duplicate extraction of those recovery files.
- `Desktop/nlschool/`, the current reconstruction described below.

The duplicate requirements document, recovery README, and transcript match the copies in this workspace. No additional full project backup was found in the checked locations. This does not check the stolen computer, external drives not connected to this Mac, or cloud accounts not synchronized locally.

The transcript also lists `models_fixed.py`, `permissions.py`, `serializers.py`, `urls.py`, and `views.py` in a former Windows project, but it does not preserve their source contents. Do not treat those files or their behavior as recovered.

## Current reconstruction inventory

- `backend/core/models.py` and migrations `0001_initial.py` and `0002_platformsetupstate.py` provide the user, tenant, membership, campus, academic-year, subscription, invitation, email-action-token, audit-event, and one-time setup-state models.
- The backend contains initial-school setup, health, staff invitation create/accept/revoke, shared email/password login, JWT refresh/logout, email verification, and password reset endpoints. Owner setup sends a verification email; staff invitation acceptance verifies the invited email. Parent self-registration/child linking and student enrollment/portal activation remain unimplemented.
- `frontend/src/app/` contains a reconstructed overview, initial-school-setup page, staff invitation acceptance page, login page, email verification flows, and password reset flows. The recovered original Next.js application was not present.
- `backend/config/settings.py` loads `backend/.env`, maps standard `EMAIL_*` settings, and configures PostgreSQL. The PostgreSQL database connection was verified, its existing migrations were applied, and the JWT token-blacklist migrations were subsequently applied. Gmail SMTP connection and authentication were verified without sending a test email.
- The workspace root has no Git repository. `frontend/` has only the Create Next App initial commit; its current UI changes are uncommitted. No remote was configured.

The 23 backend tests pass against in-memory SQLite, and Django reports no model/migration drift. The frontend lint, TypeScript check, and production build pass with Node.js 22.13.0. PostgreSQL test-database creation remains unavailable to the configured database role, so the test suite was not run against PostgreSQL.
