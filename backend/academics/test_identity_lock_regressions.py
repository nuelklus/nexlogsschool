"""PostgreSQL regressions for shared-user account-action races."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from core.auth_views import PasswordResetConfirmView, PasswordResetRequestView, VerifyEmailView
from core.models import AuditEvent, EmailActionToken, Membership, User
from core.tests import create_tenant


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class IdentityLockRegressionTests(TransactionTestCase):
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
        cache.clear()
        self.user = User.objects.create_user(
            email="identity-race@example.com",
            password="Original-example-passphrase-123",
            full_name="Identity race",
        )

    def create_token(self, raw, purpose, expires_at):
        return EmailActionToken.objects.create(
            user=self.user, purpose=purpose,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=expires_at,
        )

    @staticmethod
    def worker_connection(pids):
        close_old_connections()
        db = connections["default"]
        with db.cursor() as cursor:
            cursor.execute("SET lock_timeout = '12s'")
            cursor.execute("SET statement_timeout = '20s'")
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])
        return db

    def wait_for_blocker(self, waiting_pid, blocker_pid):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_blocking_pids(%s)", [waiting_pid])
                if blocker_pid in cursor.fetchone()[0]:
                    return
            sleep(0.02)
        self.fail("PostgreSQL did not report the expected blocking relationship.")

    def test_password_reset_invalidates_verification_without_deadlock(self):
        reset_raw, verify_raw = "R" * 40, "V" * 40
        expires = timezone.now() + timedelta(days=1)
        reset_token = self.create_token(reset_raw, EmailActionToken.Purpose.RESET_PASSWORD, expires)
        verify_token = self.create_token(verify_raw, EmailActionToken.Purpose.VERIFY_EMAIL, expires)
        entered, release = Event(), Event()
        pids = Queue()
        statements = Queue()

        def reset():
            db = self.worker_connection(pids)
            try:
                def trace(execute, sql, params, many, context):
                    statements.put(("reset", sql))
                    result = execute(sql, params, many, context)
                    upper = sql.upper()
                    if (
                        'FROM "CORE_EMAILACTIONTOKEN"' in upper
                        and ("FOR UPDATE" in upper or "FOR NO KEY UPDATE" in upper)
                        and not entered.is_set()
                    ):
                        entered.set()
                        if not release.wait(15):
                            raise AssertionError("Reset transaction was not released.")
                    return result
                try:
                    with db.execute_wrapper(trace):
                        response = PasswordResetConfirmView.as_view()(APIRequestFactory().post(
                            "/api/auth/password-reset/confirm/",
                            {"token": reset_raw, "new_password": "New-example-passphrase-456"}, format="json",
                        ))
                    return response.status_code, None
                except DatabaseError as exc:
                    cause = exc.__cause__
                    return None, (
                        getattr(cause, "sqlstate", None),
                        getattr(getattr(cause, "diag", None), "message_detail", None),
                    )
            finally:
                db.close()

        def verify():
            db = self.worker_connection(pids)
            try:
                try:
                    def trace(execute, sql, params, many, context):
                        statements.put(("verify", sql))
                        return execute(sql, params, many, context)
                    with db.execute_wrapper(trace):
                        response = VerifyEmailView.as_view()(APIRequestFactory().post(
                            "/api/auth/email/verify/", {"token": verify_raw}, format="json",
                        ))
                    return response.status_code, None
                except DatabaseError as exc:
                    cause = exc.__cause__
                    return None, (
                        getattr(cause, "sqlstate", None),
                        getattr(getattr(cause, "diag", None), "message_detail", None),
                    )
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(reset)
            first_pid = pids.get(timeout=5)
            try:
                self.assertTrue(entered.wait(10), "Reset did not lock its action token.")
                second = pool.submit(verify)
                second_pid = pids.get(timeout=5)
                self.wait_for_blocker(second_pid, first_pid)
            finally:
                release.set()
            results = first.result(timeout=20), second.result(timeout=20)

        self.assertEqual(results, ((200, None), (400, None)), list(statements.queue))
        reset_token.refresh_from_db()
        verify_token.refresh_from_db()
        self.user.refresh_from_db()
        self.assertIsNotNone(reset_token.used_at)
        self.assertIsNotNone(verify_token.used_at)
        self.assertFalse(self.user.email_verified)

    def test_verification_before_password_reset(self):
        reset_raw, verify_raw = "R" * 40, "V" * 40
        expires = timezone.now() + timedelta(days=1)
        reset_token = self.create_token(reset_raw, EmailActionToken.Purpose.RESET_PASSWORD, expires)
        verify_token = self.create_token(verify_raw, EmailActionToken.Purpose.VERIFY_EMAIL, expires)
        entered, release = Event(), Event()
        pids = Queue()

        def worker(kind):
            db = self.worker_connection(pids)
            try:
                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if (
                        kind == "verify"
                        and 'FROM "CORE_EMAILACTIONTOKEN"' in sql.upper()
                        and ("FOR UPDATE" in sql.upper() or "FOR NO KEY UPDATE" in sql.upper())
                        and not entered.is_set()
                    ):
                        entered.set()
                        if not release.wait(15):
                            raise AssertionError("Verification transaction was not released.")
                    return result

                with db.execute_wrapper(trace):
                    if kind == "verify":
                        response = VerifyEmailView.as_view()(APIRequestFactory().post(
                            "/api/auth/email/verify/", {"token": verify_raw}, format="json",
                        ))
                    else:
                        response = PasswordResetConfirmView.as_view()(APIRequestFactory().post(
                            "/api/auth/password-reset/confirm/",
                            {"token": reset_raw, "new_password": "New-example-passphrase-456"}, format="json",
                        ))
                return response.status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, "verify")
            first_pid = pids.get(timeout=5)
            try:
                self.assertTrue(entered.wait(10), "Verification did not lock its action token.")
                second = pool.submit(worker, "reset")
                second_pid = pids.get(timeout=5)
                self.wait_for_blocker(second_pid, first_pid)
            finally:
                release.set()
            results = first.result(timeout=20), second.result(timeout=20)

        self.assertEqual(results, (200, 200))
        reset_token.refresh_from_db()
        verify_token.refresh_from_db()
        self.user.refresh_from_db()
        self.assertIsNotNone(reset_token.used_at)
        self.assertIsNotNone(verify_token.used_at)
        self.assertTrue(self.user.email_verified)

    def assert_reset_request_and_confirmation(self, request_first):
        reset_raw = "Q" * 40
        token = self.create_token(
            reset_raw, EmailActionToken.Purpose.RESET_PASSWORD, timezone.now() + timedelta(days=1)
        )
        entered, release = Event(), Event()
        pids = Queue()
        first_kind = "request" if request_first else "confirm"

        def worker(kind):
            db = self.worker_connection(pids)
            try:
                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    upper = sql.upper()
                    match = (
                        upper.startswith('UPDATE "CORE_EMAILACTIONTOKEN"')
                        if kind == "request"
                        else 'FROM "CORE_EMAILACTIONTOKEN"' in upper
                        and ("FOR UPDATE" in upper or "FOR NO KEY UPDATE" in upper)
                    )
                    if kind == first_kind and match and not entered.is_set():
                        entered.set()
                        if not release.wait(15):
                            raise AssertionError("First reset operation was not released.")
                    return result

                with db.execute_wrapper(trace):
                    if kind == "request":
                        response = PasswordResetRequestView.as_view()(APIRequestFactory().post(
                            "/api/auth/password-reset/", {"email": self.user.email}, format="json",
                        ))
                    else:
                        response = PasswordResetConfirmView.as_view()(APIRequestFactory().post(
                            "/api/auth/password-reset/confirm/",
                            {"token": reset_raw, "new_password": "New-example-passphrase-456"}, format="json",
                        ))
                return response.status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, first_kind)
            first_pid = pids.get(timeout=5)
            try:
                self.assertTrue(entered.wait(10), "First reset operation did not reach its lock.")
                second = pool.submit(worker, "confirm" if request_first else "request")
                second_pid = pids.get(timeout=5)
                self.wait_for_blocker(second_pid, first_pid)
            finally:
                release.set()
            results = first.result(timeout=20), second.result(timeout=20)

        self.assertEqual(results, (200, 400) if request_first else (200, 200))
        token.refresh_from_db()
        self.user.refresh_from_db()
        self.assertIsNotNone(token.used_at)
        self.assertEqual(self.user.check_password("New-example-passphrase-456"), not request_first)
        self.assertEqual(EmailActionToken.objects.filter(
            user=self.user, purpose=EmailActionToken.Purpose.RESET_PASSWORD, used_at__isnull=True,
        ).count(), 1)

    def test_reset_request_before_confirmation(self):
        self.assert_reset_request_and_confirmation(request_first=True)

    def test_reset_confirmation_before_request(self):
        self.assert_reset_request_and_confirmation(request_first=False)

    def test_account_actions_roll_back_when_audit_fails(self):
        for purpose in (EmailActionToken.Purpose.RESET_PASSWORD, EmailActionToken.Purpose.VERIFY_EMAIL):
            with self.subTest(purpose=purpose):
                raw = ("S" if purpose == EmailActionToken.Purpose.RESET_PASSWORD else "T") * 40
                token = self.create_token(raw, purpose, timezone.now() + timedelta(days=1))
                original_password = self.user.password
                if purpose == EmailActionToken.Purpose.RESET_PASSWORD:
                    view = PasswordResetConfirmView
                    payload = {"token": raw, "new_password": "New-example-passphrase-456"}
                else:
                    view = VerifyEmailView
                    payload = {"token": raw}
                with patch("core.auth_views.record_user_audit", side_effect=RuntimeError("forced audit failure")):
                    with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
                        view.as_view()(APIRequestFactory().post("/api/auth/action/", payload, format="json"))
                token.refresh_from_db()
                self.user.refresh_from_db()
                self.assertIsNone(token.used_at)
                self.assertFalse(self.user.email_verified)
                self.assertEqual(self.user.password, original_password)

    def test_account_action_audit_is_tenant_scoped_and_single_use(self):
        first = create_tenant(name="First", slug="first-audit", email="first@example.com", phone="1")
        second = create_tenant(name="Second", slug="second-audit", email="second@example.com", phone="2")
        for tenant in (first, second):
            Membership.objects.create(tenant=tenant, user=self.user, role=Membership.Role.TEACHER)
        for purpose, raw, view, payload, action_name in (
            (EmailActionToken.Purpose.VERIFY_EMAIL, "U" * 40, VerifyEmailView,
             {"token": "U" * 40}, "account.email_verified"),
            (EmailActionToken.Purpose.RESET_PASSWORD, "X" * 40, PasswordResetConfirmView,
             {"token": "X" * 40, "new_password": "New-example-passphrase-456"}, "account.password_reset"),
        ):
            self.create_token(raw, purpose, timezone.now() + timedelta(days=1))
            for expected in (200, 400):
                response = view.as_view()(APIRequestFactory().post("/api/auth/action/", payload, format="json"))
                self.assertEqual(response.status_code, expected)
            for tenant in (first, second):
                self.assertEqual(AuditEvent.objects.filter(
                    tenant=tenant, actor=self.user, action=action_name,
                ).count(), 1)

    def assert_rejects_token_that_expires_while_waiting(self, purpose):
        raw = "W" * 40
        token = self.create_token(raw, purpose, timezone.now() + timedelta(seconds=5))
        original_password = self.user.password
        held, release = Event(), Event()
        pids = Queue()

        def hold_token():
            db = self.worker_connection(pids)
            try:
                with transaction.atomic():
                    EmailActionToken.objects.select_for_update().get(pk=token.pk)
                    held.set()
                    if not release.wait(15):
                        raise AssertionError("Token lock was not released.")
            finally:
                db.close()

        def verify():
            db = self.worker_connection(pids)
            try:
                if purpose == EmailActionToken.Purpose.VERIFY_EMAIL:
                    response = VerifyEmailView.as_view()(APIRequestFactory().post(
                        "/api/auth/email/verify/", {"token": raw}, format="json",
                    ))
                else:
                    response = PasswordResetConfirmView.as_view()(APIRequestFactory().post(
                        "/api/auth/password-reset/confirm/",
                        {"token": raw, "new_password": "New-example-passphrase-456"}, format="json",
                    ))
                return response.status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(hold_token)
            first_pid = pids.get(timeout=5)
            try:
                self.assertTrue(held.wait(5), "Token lock was not acquired.")
                second = pool.submit(verify)
                second_pid = pids.get(timeout=5)
                self.wait_for_blocker(second_pid, first_pid)
                while timezone.now() <= token.expires_at:
                    sleep(0.05)
            finally:
                release.set()
            first.result(timeout=20)
            status = second.result(timeout=20)

        token.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(status, 400)
        self.assertIsNone(token.used_at)
        self.assertFalse(self.user.email_verified)
        self.assertEqual(self.user.password, original_password)

    def test_verification_rejects_token_that_expires_while_waiting(self):
        self.assert_rejects_token_that_expires_while_waiting(EmailActionToken.Purpose.VERIFY_EMAIL)

    def test_reset_rejects_token_that_expires_while_waiting(self):
        self.assert_rejects_token_that_expires_while_waiting(EmailActionToken.Purpose.RESET_PASSWORD)
