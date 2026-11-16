"""Disposable PostgreSQL tests of real shared-user and trial-subscription workflows."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from core.auth_views import PasswordResetConfirmView, VerifyEmailView
from core.models import (
    AuditEvent, EmailActionToken, Membership, MembershipInvitation, Student, SubscriptionPlan, Tenant, TenantSubscription, User,
)
from core.views import AcceptStaffInvitationView, start_trial_subscription


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SharedUserRaceTests(TransactionTestCase):
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
        self.tenants = [
            Tenant.objects.create(name=name, slug=name.lower(), email="school@example.com", phone="1")
            for name in ("First", "Second")
        ]
        self.owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        self.shared = User.objects.create_user(
            email="shared@example.com", password="Example-test-passphrase-123", full_name="Shared"
        )
        for tenant in self.tenants:
            Membership.objects.create(tenant=tenant, user=self.owner, role=Membership.Role.OWNER)

    def invite(self, tenant, token):
        return MembershipInvitation.objects.create(
            tenant=tenant, email=self.shared.email, role=MembershipInvitation.Role.TEACHER,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            invited_by=self.owner, expires_at=timezone.now() + timedelta(days=1),
        )

    def run_race(self, first, second, first_match, second_match):
        entered, release = Event(), Event()
        pids = Queue()
        traces = {"first": [], "second": []}

        def worker(label, operation, hold):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((label, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    traces[label].append(sql)
                    result = execute(sql, params, many, context)
                    if hold and first_match(sql) and not entered.is_set():
                        entered.set()
                        if not release.wait(10):
                            raise AssertionError("First transaction was not released")
                    return result

                try:
                    with db.execute_wrapper(trace):
                        return operation()
                except DatabaseError as exc:
                    cause = exc.__cause__
                    diag = getattr(cause, "diag", None)
                    return None, getattr(cause, "sqlstate", None), (
                        getattr(diag, "message_detail", None), getattr(diag, "context", None)
                    )
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(worker, "first", first, True)
            try:
                label, first_pid = pids.get(timeout=5)
                self.assertEqual(label, "first")
                self.assertTrue(entered.wait(8), "First operation did not reach synchronization point")
                b = pool.submit(worker, "second", second, False)
                label, second_pid = pids.get(timeout=5)
                self.assertEqual(label, "second")
                self.assertNotEqual(first_pid, second_pid)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_blocking_pids(%s), wait_event_type, query "
                            "FROM pg_stat_activity WHERE pid=%s", [second_pid, second_pid]
                        )
                        state = cursor.fetchone()
                    if state and first_pid in state[0]:
                        break
                    sleep(0.02)
                else:
                    self.fail("Expected PostgreSQL blocking relationship was not observed")
                self.assertEqual(state[1], "Lock")
                self.assertTrue(second_match(state[2]), state[2])
            finally:
                release.set()
            return (a.result(timeout=15), b.result(timeout=15)), traces

    @staticmethod
    def user_lock(sql):
        text = sql.upper()
        return 'FROM "CORE_USER"' in text and (
            "FOR UPDATE" in text or "FOR NO KEY UPDATE" in text
        )

    @staticmethod
    def invitation_lock(sql):
        text = sql.upper()
        return 'FROM "CORE_MEMBERSHIPINVITATION"' in text and "FOR UPDATE" in text

    def accept(self, token):
        request = APIRequestFactory().post(
            "/api/invitations/accept/",
            {"token": token, "password": "Example-test-passphrase-123"}, format="json"
        )
        response = AcceptStaffInvitationView.as_view()(request)
        return response.status_code, None, dict(response.data)

    def test_two_tenants_accept_invitations_for_same_existing_user(self):
        first_token, second_token = "A" * 40, "B" * 40
        invitations = [
            self.invite(self.tenants[0], first_token), self.invite(self.tenants[1], second_token)
        ]
        outcomes, traces = self.run_race(
            lambda: self.accept(first_token), lambda: self.accept(second_token),
            self.user_lock, self.user_lock,
        )
        self.assertEqual([o[:2] for o in outcomes], [(201, None), (201, None)], outcomes)
        for trace in traces.values():
            locks = [sql for sql in trace if self.invitation_lock(sql) or self.user_lock(sql)]
            self.assertTrue(self.user_lock(locks[0]))
            self.assertTrue(self.invitation_lock(locks[1]))
        self.assertEqual(Membership.objects.filter(user=self.shared).count(), 2)
        self.assertEqual(MembershipInvitation.objects.filter(
            pk__in=[i.pk for i in invitations], accepted_at__isnull=False
        ).count(), 2)
        self.shared.refresh_from_db()
        self.assertTrue(self.shared.email_verified)

    def test_two_tenants_accept_same_user_invitations_in_reverse_order(self):
        first_token, second_token = "A" * 40, "B" * 40
        invitations = [
            self.invite(self.tenants[0], first_token), self.invite(self.tenants[1], second_token)
        ]
        outcomes, traces = self.run_race(
            lambda: self.accept(second_token), lambda: self.accept(first_token),
            self.user_lock, self.user_lock,
        )
        self.assertEqual([o[:2] for o in outcomes], [(201, None), (201, None)], outcomes)
        for trace in traces.values():
            locks = [sql for sql in trace if self.invitation_lock(sql) or self.user_lock(sql)]
            self.assertTrue(self.user_lock(locks[0]))
            self.assertTrue(self.invitation_lock(locks[1]))
        self.assertEqual(Membership.objects.filter(user=self.shared).count(), 2)
        self.assertEqual(MembershipInvitation.objects.filter(
            pk__in=[i.pk for i in invitations], accepted_at__isnull=False
        ).count(), 2)
        self.assertEqual(AuditEvent.objects.filter(
            tenant__in=self.tenants, actor=self.shared, action="membership.invitation_accepted"
        ).count(), 2)

    def test_acceptance_vs_second_membership_creation_same_tenant(self):
        first_token, second_token = "C" * 40, "D" * 40
        first = self.invite(self.tenants[0], first_token)
        second = self.invite(self.tenants[0], second_token)
        student = Student.objects.create(
            tenant=self.tenants[0], first_name="Student", last_name="Example", admission_number="T-1"
        )
        second.student = student
        second.role = MembershipInvitation.Role.STUDENT
        second.save(update_fields=("student", "role"))
        outcomes, traces = self.run_race(
            lambda: self.accept(first_token), lambda: self.accept(second_token),
            self.user_lock, self.user_lock,
        )
        self.assertEqual([o[:2] for o in outcomes], [(201, None), (409, None)], outcomes)
        self.assertIn('JOIN "CORE_TENANT"', next(sql.upper() for sql in traces["second"] if self.invitation_lock(sql)))
        self.assertEqual(Membership.objects.filter(tenant=self.tenants[0], user=self.shared).count(), 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.accepted_at)
        self.assertIsNone(second.accepted_at)

    def test_trial_plan_creation_for_two_tenants(self):
        def create_subscription(tenant):
            with transaction.atomic():
                subscription = start_trial_subscription(tenant)
                return subscription.status, None, str(subscription.plan_id)

        def plan_insert(sql):
            return sql.upper().startswith('INSERT INTO "CORE_SUBSCRIPTIONPLAN"')

        outcomes, traces = self.run_race(
            lambda: create_subscription(self.tenants[0]), lambda: create_subscription(self.tenants[1]),
            plan_insert, plan_insert,
        )
        self.assertEqual([o[:2] for o in outcomes], [("trialing", None), ("trialing", None)], outcomes)
        self.assertEqual(outcomes[0][2], outcomes[1][2])
        self.assertEqual(SubscriptionPlan.objects.filter(code="trial").count(), 1)
        self.assertEqual(TenantSubscription.objects.filter(tenant__in=self.tenants).count(), 2)
        self.assertTrue(all(any(plan_insert(sql) for sql in trace) for trace in traces.values()))

    def test_verification_vs_existing_membership_invitation_acceptance(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT k.condeferrable, k.condeferred FROM pg_constraint k "
                "JOIN pg_class c ON c.oid=k.conrelid JOIN pg_class p ON p.oid=k.confrelid "
                "WHERE k.contype='f' AND c.relname='core_auditevent' AND p.relname='core_tenant'"
            )
            self.assertEqual(cursor.fetchone(), (True, True))
        Membership.objects.create(tenant=self.tenants[0], user=self.shared, role=Membership.Role.TEACHER)
        Membership.objects.create(tenant=self.tenants[1], user=self.shared, role=Membership.Role.TEACHER)
        verify_token, invitation_token = "E" * 40, "F" * 40
        token = EmailActionToken.objects.create(
            user=self.shared, purpose=EmailActionToken.Purpose.VERIFY_EMAIL,
            token_hash=hashlib.sha256(verify_token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=1),
        )
        invitation = self.invite(self.tenants[0], invitation_token)

        def verify():
            request = APIRequestFactory().post("/api/auth/email/verify/", {"token": verify_token}, format="json")
            response = VerifyEmailView.as_view()(request)
            return response.status_code, None, dict(response.data)

        def token_lock(sql):
            text = sql.upper()
            return 'FROM "CORE_EMAILACTIONTOKEN"' in text and (
                "FOR UPDATE" in text or "FOR NO KEY UPDATE" in text
            )

        outcomes, traces = self.run_race(
            verify, lambda: self.accept(invitation_token), token_lock, self.user_lock,
        )
        # Acceptance locks the shared user before the invitation/tenant join, so
        # the former verification/acceptance cycle no longer occurs.
        self.assertEqual([o[:2] for o in outcomes], [(200, None), (409, None)], outcomes)
        token_index = next(i for i, sql in enumerate(traces["first"]) if token_lock(sql))
        token_sql = traces["first"][token_index].upper()
        self.assertTrue(
            'JOIN "CORE_USER"' in token_sql or any(
                'FROM "CORE_USER"' in sql.upper() and "FOR NO KEY UPDATE" in sql.upper()
                for sql in traces["first"][:token_index]
            )
        )
        self.assertTrue(any(sql.upper().startswith('INSERT INTO "CORE_AUDITEVENT"') for sql in traces["first"]))
        self.assertTrue(any(self.invitation_lock(sql) and 'JOIN "CORE_TENANT"' in sql.upper()
                            for sql in traces["second"]))
        self.assertEqual(Membership.objects.filter(tenant=self.tenants[0], user=self.shared).count(), 1)
        self.assertEqual(Membership.objects.filter(user=self.shared).count(), 2)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        token.refresh_from_db()
        self.shared.refresh_from_db()
        self.assertEqual(token.used_at is not None, self.shared.email_verified)

    def test_existing_membership_invitation_acceptance_before_verification(self):
        Membership.objects.create(tenant=self.tenants[0], user=self.shared, role=Membership.Role.TEACHER)
        verify_token, invitation_token = "G" * 40, "H" * 40
        token = EmailActionToken.objects.create(
            user=self.shared, purpose=EmailActionToken.Purpose.VERIFY_EMAIL,
            token_hash=hashlib.sha256(verify_token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=1),
        )
        invitation = self.invite(self.tenants[0], invitation_token)

        def verify():
            request = APIRequestFactory().post(
                "/api/auth/email/verify/", {"token": verify_token}, format="json"
            )
            response = VerifyEmailView.as_view()(request)
            return response.status_code, None, dict(response.data)

        def verify_wait(sql):
            text = sql.upper()
            return (
                ('FROM "CORE_EMAILACTIONTOKEN"' in text and 'JOIN "CORE_USER"' in text)
                or ('FROM "CORE_USER"' in text and "FOR NO KEY UPDATE" in text)
            )

        outcomes, _ = self.run_race(
            lambda: self.accept(invitation_token), verify, self.user_lock, verify_wait,
        )
        self.assertEqual([o[:2] for o in outcomes], [(409, None), (200, None)], outcomes)
        invitation.refresh_from_db()
        token.refresh_from_db()
        self.shared.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertIsNotNone(token.used_at)
        self.assertTrue(self.shared.email_verified)
        self.assertEqual(Membership.objects.filter(tenant=self.tenants[0], user=self.shared).count(), 1)

    def assert_verification_and_successful_invitation(self, invitation_first):
        verify_token, invitation_token = "I" * 40, "J" * 40
        token = EmailActionToken.objects.create(
            user=self.shared, purpose=EmailActionToken.Purpose.VERIFY_EMAIL,
            token_hash=hashlib.sha256(verify_token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=1),
        )
        invitation = self.invite(self.tenants[0], invitation_token)

        def verify():
            request = APIRequestFactory().post(
                "/api/auth/email/verify/", {"token": verify_token}, format="json"
            )
            response = VerifyEmailView.as_view()(request)
            return response.status_code, None, dict(response.data)

        def token_lock(sql):
            text = sql.upper()
            return 'FROM "CORE_EMAILACTIONTOKEN"' in text and (
                "FOR UPDATE" in text or "FOR NO KEY UPDATE" in text
            )

        def verification_wait(sql):
            text = sql.upper()
            return (
                ('FROM "CORE_EMAILACTIONTOKEN"' in text and 'JOIN "CORE_USER"' in text)
                or ('FROM "CORE_USER"' in text and "FOR NO KEY UPDATE" in text)
            )

        if invitation_first:
            outcomes, _ = self.run_race(
                lambda: self.accept(invitation_token), verify, self.user_lock, verification_wait,
            )
            expected = [(201, None), (200, None)]
        else:
            outcomes, _ = self.run_race(
                verify, lambda: self.accept(invitation_token), token_lock, self.user_lock,
            )
            expected = [(200, None), (201, None)]
        self.assertEqual([o[:2] for o in outcomes], expected, outcomes)
        invitation.refresh_from_db()
        token.refresh_from_db()
        self.shared.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertIsNotNone(token.used_at)
        self.assertTrue(self.shared.email_verified)
        self.assertEqual(Membership.objects.filter(tenant=self.tenants[0], user=self.shared).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            tenant=self.tenants[0], action="membership.invitation_accepted"
        ).count(), 1)

    def test_verification_before_successful_invitation(self):
        self.assert_verification_and_successful_invitation(invitation_first=False)

    def test_successful_invitation_before_verification(self):
        self.assert_verification_and_successful_invitation(invitation_first=True)

    def assert_reset_and_invitation(self, invitation_first):
        reset_token, invitation_token = "K" * 40, "L" * 40
        token = EmailActionToken.objects.create(
            user=self.shared, purpose=EmailActionToken.Purpose.RESET_PASSWORD,
            token_hash=hashlib.sha256(reset_token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=1),
        )
        invitation = self.invite(self.tenants[0], invitation_token)

        def reset():
            request = APIRequestFactory().post(
                "/api/auth/password-reset/confirm/",
                {"token": reset_token, "new_password": "New-example-passphrase-456"}, format="json",
            )
            response = PasswordResetConfirmView.as_view()(request)
            return response.status_code, None, dict(response.data)

        def token_lock(sql):
            text = sql.upper()
            return 'FROM "CORE_EMAILACTIONTOKEN"' in text and (
                "FOR UPDATE" in text or "FOR NO KEY UPDATE" in text
            )

        def reset_wait(sql):
            text = sql.upper()
            return (
                ('FROM "CORE_EMAILACTIONTOKEN"' in text and 'JOIN "CORE_USER"' in text)
                or ('FROM "CORE_USER"' in text and "FOR NO KEY UPDATE" in text)
            )

        if invitation_first:
            outcomes, _ = self.run_race(
                lambda: self.accept(invitation_token), reset, self.user_lock, reset_wait,
            )
            expected = [(201, None), (200, None)]
        else:
            outcomes, _ = self.run_race(
                reset, lambda: self.accept(invitation_token), token_lock, self.user_lock,
            )
            expected = [(200, None), (400, None)]
        self.assertEqual([o[:2] for o in outcomes], expected, outcomes)
        invitation.refresh_from_db()
        token.refresh_from_db()
        self.shared.refresh_from_db()
        self.assertIsNotNone(token.used_at)
        self.assertTrue(self.shared.check_password("New-example-passphrase-456"))
        self.assertEqual(invitation.accepted_at is not None, invitation_first)
        self.assertEqual(
            Membership.objects.filter(tenant=self.tenants[0], user=self.shared).count(),
            int(invitation_first),
        )

    def test_password_reset_before_invitation(self):
        self.assert_reset_and_invitation(invitation_first=False)

    def test_invitation_before_password_reset(self):
        self.assert_reset_and_invitation(invitation_first=True)
