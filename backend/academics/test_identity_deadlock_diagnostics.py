"""Regression tests for real email-verification / invitation-acceptance concurrency."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from core.auth_views import VerifyEmailView
from core.models import AuditEvent, EmailActionToken, Membership, MembershipInvitation, Tenant, User
from core.views import AcceptStaffInvitationView


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class IdentityConcurrencyRegression(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or connection.vendor != "postgresql"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise SkipTest("Requires the disposable PostgreSQL fixture runner.")
        super().setUpClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(name="School", slug="school", email="test@example.com", phone="1")
        self.owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        self.user = User.objects.create_user(
            email="shared@example.com", password="Example-test-passphrase-123", full_name="Shared"
        )
        Membership.objects.create(tenant=self.tenant, user=self.user, role=Membership.Role.TEACHER)
        self.invitation_token = "I" * 40
        self.verify_token = "V" * 40
        self.invitation = MembershipInvitation.objects.create(
            tenant=self.tenant, email=self.user.email, role=MembershipInvitation.Role.TEACHER,
            token_hash=hashlib.sha256(self.invitation_token.encode()).hexdigest(),
            invited_by=self.owner, expires_at=timezone.now() + timedelta(days=1),
        )
        self.action = EmailActionToken.objects.create(
            user=self.user, purpose=EmailActionToken.Purpose.VERIFY_EMAIL,
            token_hash=hashlib.sha256(self.verify_token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=1),
        )

    def accept(self):
        request = APIRequestFactory().post(
            "/api/invitations/accept/",
            {"token": self.invitation_token, "password": "Example-test-passphrase-123"}, format="json",
        )
        return AcceptStaffInvitationView.as_view()(request).status_code

    def verify(self):
        request = APIRequestFactory().post(
            "/api/auth/email/verify/", {"token": self.verify_token}, format="json",
        )
        return VerifyEmailView.as_view()(request).status_code

    def wait_blocked(self, waiting_pid, blocker_pid, query_fragment):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_blocking_pids(%s), query FROM pg_stat_activity WHERE pid = %s",
                    [waiting_pid, waiting_pid],
                )
                state = cursor.fetchone()
            if state and blocker_pid in state[0] and query_fragment in state[1]:
                return
            sleep(0.02)
        self.fail(f"Expected PID {waiting_pid} to wait behind PID {blocker_pid} in {query_fragment}")

    def test_verification_vs_obsolete_invitation_no_deadlock(self):
        token_locked, release_token = Event(), Event()
        pids = Queue()
        traces = {"verification": [], "acceptance": []}

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT k.condeferrable, k.condeferred FROM pg_constraint k "
                "JOIN pg_class c ON c.oid=k.conrelid JOIN pg_class p ON p.oid=k.confrelid "
                "WHERE k.contype='f' AND c.relname='core_auditevent' AND p.relname='core_tenant'"
            )
            self.assertEqual(cursor.fetchone(), (True, True))

        def worker(label, operation):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SET application_name = %s", [f"nlschool-deadlock-{label}"])
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((label, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    traces[label].append(sql)
                    result = execute(sql, params, many, context)
                    if (
                        label == "verification"
                        and 'FROM "core_emailactiontoken"' in sql
                        and ("FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql)
                    ):
                        token_locked.set()
                        if not release_token.wait(10):
                            raise AssertionError("Verification was not released")
                    return result

                try:
                    with db.execute_wrapper(trace):
                        return operation(), None, None
                except DatabaseError as exc:
                    cause = exc.__cause__
                    diag = getattr(cause, "diag", None)
                    return None, getattr(cause, "sqlstate", None), (
                        getattr(diag, "message_detail", None), getattr(diag, "context", None)
                    )
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(worker, "verification", self.verify)
            try:
                label, verification_pid = pids.get(timeout=5)
                self.assertEqual(label, "verification")
                self.assertTrue(token_locked.wait(8))
                b = pool.submit(worker, "acceptance", self.accept)
                label, acceptance_pid = pids.get(timeout=5)
                self.assertEqual(label, "acceptance")
                self.wait_blocked(acceptance_pid, verification_pid, 'FROM "core_user"')
            finally:
                release_token.set()
            outcomes = a.result(timeout=15), b.result(timeout=15)

        # Acceptance now takes the user lock before the invitation/tenant lock, so
        # it holds nothing verification needs while it waits: no 40P01.
        self.assertEqual([o[:2] for o in outcomes], [(200, None), (409, None)], outcomes)
        self.assertTrue(any('INSERT INTO "core_auditevent"' in sql for sql in traces["verification"]))
        user_index = next(i for i, sql in enumerate(traces["acceptance"])
                          if 'FROM "core_user"' in sql and
                          ("FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql))
        invitation_index = next(i for i, sql in enumerate(traces["acceptance"])
                                if 'JOIN "core_tenant"' in sql and "FOR UPDATE" in sql)
        self.assertLess(user_index, invitation_index)
        self.invitation.refresh_from_db()
        self.action.refresh_from_db()
        self.user.refresh_from_db()
        self.assertIsNone(self.invitation.accepted_at)
        self.assertTrue(self.user.email_verified)
        self.assertIsNotNone(self.action.used_at)
        self.assertEqual(AuditEvent.objects.filter(action="account.email_verified").count(), 1)
        self.assertEqual(Membership.objects.filter(tenant=self.tenant, user=self.user).count(), 1)

    def test_verification_commit_waits_for_locked_audit_tenant(self):
        from django.db import transaction

        locked, release = Event(), Event()
        pids = Queue()
        statements = []

        def holder():
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(("holder", cursor.fetchone()[0]))
                with transaction.atomic():
                    Tenant.objects.select_for_update().get(pk=self.tenant.pk)
                    locked.set()
                    if not release.wait(10):
                        raise AssertionError("Tenant holder was not released")
            finally:
                db.close()

        def verify():
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(("verification", cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    statements.append(sql)
                    return execute(sql, params, many, context)

                with db.execute_wrapper(trace):
                    return self.verify()
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(holder)
            try:
                label, holder_pid = pids.get(timeout=5)
                self.assertEqual(label, "holder")
                self.assertTrue(locked.wait(8))
                second = pool.submit(verify)
                label, verification_pid = pids.get(timeout=5)
                self.assertEqual(label, "verification")
                self.wait_blocked(verification_pid, holder_pid, "COMMIT")
                self.assertTrue(any('INSERT INTO "core_auditevent"' in sql for sql in statements))
            finally:
                release.set()
            first.result(timeout=10)
            self.assertEqual(second.result(timeout=10), 200)

    @patch("core.views.timezone.now")
    def test_invitation_expiring_while_waiting_is_rejected(self, mocked_now):
        fixed_time = datetime.now(datetime_timezone.utc)
        mocked_now.return_value = fixed_time
        self.invitation.expires_at = fixed_time + timedelta(seconds=1)
        self.invitation.save(update_fields=("expires_at",))
        locked, release = Event(), Event()
        pids = Queue()

        def lock_invitation():
            from django.db import transaction

            close_old_connections()
            try:
                with transaction.atomic():
                    MembershipInvitation.objects.select_for_update().get(pk=self.invitation.pk)
                    locked.set()
                    if not release.wait(10):
                        raise AssertionError("Invitation holder was not released")
            finally:
                connections.close_all()

        def accept():
            close_old_connections()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                return self.accept()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(lock_invitation)
            try:
                self.assertTrue(locked.wait(5))
                acceptance = pool.submit(accept)
                pid = pids.get(timeout=5)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [pid])
                        if cursor.fetchone()[0]:
                            break
                    sleep(0.02)
                else:
                    self.fail("Acceptance did not wait for invitation")
                mocked_now.return_value = fixed_time + timedelta(seconds=2)
            finally:
                release.set()
            holder.result(timeout=10)
            self.assertEqual(acceptance.result(timeout=10), 400)
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.accepted_at)

    def test_invitation_expired_before_acceptance_makes_no_writes(self):
        self.invitation.expires_at = timezone.now() - timedelta(seconds=1)
        self.invitation.save(update_fields=("expires_at",))
        statements = []

        def trace(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(trace):
            self.assertEqual(self.accept(), 400)
        self.assertTrue(any('FROM "core_membershipinvitation"' in sql and 'JOIN "core_tenant"' in sql
                            and "FOR UPDATE" in sql for sql in statements))
        self.assertFalse(any(sql.upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for sql in statements))
        self.assertEqual(Membership.objects.filter(tenant=self.tenant, user=self.user).count(), 1)

    def test_same_invitation_concurrent_acceptance_rechecks_after_wait(self):
        Membership.objects.filter(tenant=self.tenant, user=self.user).delete()
        self.user.set_password("Example-test-passphrase-123")
        self.user.save(update_fields=("password",))
        locked, release = Event(), Event()
        pids = Queue()

        def worker(label):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((label, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if label == "first" and 'FROM "core_membershipinvitation"' in sql and "FOR UPDATE" in sql:
                        locked.set()
                        if not release.wait(10):
                            raise AssertionError("First acceptance was not released")
                    return result

                with db.execute_wrapper(trace):
                    return self.accept()
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, "first")
            try:
                label, first_pid = pids.get(timeout=5)
                self.assertEqual(label, "first")
                self.assertTrue(locked.wait(8))
                second = pool.submit(worker, "second")
                label, second_pid = pids.get(timeout=5)
                self.assertEqual(label, "second")
                self.assertNotEqual(first_pid, second_pid)
                self.wait_blocked(second_pid, first_pid, 'FROM "core_user"')
            finally:
                release.set()
            self.assertEqual((first.result(timeout=15), second.result(timeout=15)), (201, 400))

        self.invitation.refresh_from_db()
        self.assertIsNotNone(self.invitation.accepted_at)
        self.assertEqual(Membership.objects.filter(tenant=self.tenant, user=self.user).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="membership.invitation_accepted").count(), 1)

    def fresh_user_invitation(self, tenant, email, token):
        return MembershipInvitation.objects.create(
            tenant=tenant, email=email, role=MembershipInvitation.Role.TEACHER,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            invited_by=self.owner, expires_at=timezone.now() + timedelta(days=1),
        )

    def accept_payload(self, token, **extra):
        request = APIRequestFactory().post(
            "/api/invitations/accept/", {"token": token, **extra}, format="json",
        )
        return AcceptStaffInvitationView.as_view()(request).status_code

    def test_verification_deadlock_schedule_repeats_without_40p01(self):
        for _ in range(5):
            self.invitation.accepted_at = None
            self.invitation.save(update_fields=("accepted_at",))
            self.user.email_verified = False
            self.user.save(update_fields=("email_verified",))
            self.action.used_at = None
            self.action.save(update_fields=("used_at",))
            AuditEvent.objects.all().delete()
            self.test_verification_vs_obsolete_invitation_no_deadlock()

    def test_revoked_invitation_is_rejected_after_waiting(self):
        Membership.objects.filter(tenant=self.tenant, user=self.user).delete()
        locked, release = Event(), Event()
        pids, holder_pids = Queue(), Queue()

        def revoker():
            from django.db import transaction

            close_old_connections()
            try:
                with transaction.atomic():
                    MembershipInvitation.objects.select_for_update().get(pk=self.invitation.pk)
                    with connections["default"].cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        holder_pids.put(cursor.fetchone()[0])
                    locked.set()
                    if not release.wait(10):
                        raise AssertionError("not released")
                    MembershipInvitation.objects.filter(pk=self.invitation.pk).update(revoked_at=timezone.now())
            finally:
                connections.close_all()

        def accept():
            close_old_connections()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                return self.accept()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(revoker)
            try:
                self.assertTrue(locked.wait(5))
                acceptance = pool.submit(accept)
                self.wait_blocked(pids.get(timeout=5), holder_pids.get(timeout=5), "core_membershipinvitation")
            finally:
                release.set()
            holder.result(timeout=10)
            self.assertEqual(acceptance.result(timeout=10), 400)
        self.assertFalse(Membership.objects.filter(tenant=self.tenant, user=self.user).exists())

    def test_verification_without_invitation_and_reuse_rejected(self):
        MembershipInvitation.objects.all().delete()
        self.assertEqual(self.verify(), 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.verify(), 400)
        self.assertEqual(AuditEvent.objects.filter(action="account.email_verified").count(), 1)

    def test_valid_acceptance_after_verification_and_cross_tenant_isolation(self):
        other = Tenant.objects.create(name="Other", slug="other", email="o@example.com", phone="1")
        Membership.objects.filter(tenant=self.tenant, user=self.user).delete()
        self.assertEqual(self.verify(), 200)
        self.assertEqual(self.accept(), 201)
        self.assertEqual(Membership.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Membership.objects.get(user=self.user).tenant_id, self.tenant.pk)
        self.assertFalse(Membership.objects.filter(tenant=other, user=self.user).exists())
        self.assertEqual(self.accept(), 400)
        self.assertEqual(Membership.objects.filter(user=self.user).count(), 1)

    def test_other_tenant_invitation_for_other_email_unaffected(self):
        other = Tenant.objects.create(name="Other", slug="other", email="o@example.com", phone="1")
        self.fresh_user_invitation(other, "new@example.com", "N" * 40)
        self.assertEqual(self.accept_payload("N" * 40, full_name="New", password="Example-test-passphrase-123"), 201)
        self.assertEqual(Membership.objects.filter(tenant=other, user__email="new@example.com").count(), 1)
        self.assertEqual(Membership.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(self.accept_payload("missing" * 8, password="x"), 400)
