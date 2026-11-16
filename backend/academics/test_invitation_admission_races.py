"""PostgreSQL regressions for invitation acceptance and admission audit locks."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from core.enrollment import StudentListCreateView
from core.models import AcademicYear, AuditEvent, Membership, MembershipInvitation, Student, User
from core.tests import create_tenant
from core.views import AcceptStaffInvitationView
from .models import SchoolClass, StudentEnrollment


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class InvitationAdmissionRaceTests(TransactionTestCase):
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
        self.tenant = create_tenant(name="Race", slug="race-admission", email="race@example.com", phone="1")
        self.user = User.objects.create_user(
            email="owner@example.com", password="Example-test-passphrase-123",
            full_name="Owner", email_verified=True,
        )
        Membership.objects.create(tenant=self.tenant, user=self.user, role=Membership.Role.OWNER)
        self.year = AcademicYear.objects.create(
            tenant=self.tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        self.school_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Class")
        self.raw = "I" * 40
        self.invitation = MembershipInvitation.objects.create(
            tenant=self.tenant, email=self.user.email, role=MembershipInvitation.Role.TEACHER,
            token_hash=hashlib.sha256(self.raw.encode()).hexdigest(),
            invited_by=self.user, expires_at=timezone.now() + timedelta(days=1),
        )

    def run_order(self, invitation_first, successful=False):
        invited_user = self.user
        if successful:
            invited_user = User.objects.create_user(
                email="teacher@example.com", password="Example-test-passphrase-123",
                full_name="Teacher",
            )
            self.invitation.email = invited_user.email
            self.invitation.save(update_fields=("email",))
        user_locked, admission_ready = Event(), Event()
        release_user, release_commit = Event(), Event()
        pids = Queue()
        statements = {"admission": [], "invitation": []}

        def worker(kind):
            close_old_connections()
            db = connections["default"]
            status = None
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '12s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((kind, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    statements[kind].append(sql)
                    result = execute(sql, params, many, context)
                    upper = sql.upper()
                    if (
                        kind == "invitation" and 'FROM "CORE_USER"' in upper
                        and ("FOR UPDATE" in upper or "FOR NO KEY UPDATE" in upper)
                        and not user_locked.is_set()
                    ):
                        user_locked.set()
                        if not release_user.wait(15):
                            raise AssertionError("Invitation user lock was not released.")
                    return result

                try:
                    with db.execute_wrapper(trace):
                        if kind == "admission":
                            with transaction.atomic():
                                request = APIRequestFactory().post("/students/", {
                                    "first_name": "Student", "last_name": "Example",
                                    "initial_academic_year": str(self.year.pk),
                                    "initial_school_class": str(self.school_class.pk),
                                    "enrolled_on": "2026-06-01",
                                }, format="json")
                                force_authenticate(request, user=self.user)
                                response = StudentListCreateView.as_view()(request, tenant_id=self.tenant.pk)
                                status = response.status_code
                                admission_ready.set()
                                if not release_commit.wait(15):
                                    raise AssertionError("Admission commit was not released.")
                        else:
                            request = APIRequestFactory().post("/invitations/accept/", {
                                "token": self.raw, "password": "Example-test-passphrase-123",
                            }, format="json")
                            status = AcceptStaffInvitationView.as_view()(request).status_code
                    return status, None, None
                except DatabaseError as exc:
                    cause = exc.__cause__
                    diag = getattr(cause, "diag", None)
                    return status, getattr(cause, "sqlstate", None), (
                        getattr(diag, "message_detail", None), getattr(diag, "context", None)
                    )
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                if invitation_first:
                    invite = pool.submit(worker, "invitation")
                    self.assertTrue(user_locked.wait(10), "Invitation did not lock the user.")
                    admission = pool.submit(worker, "admission")
                else:
                    admission = pool.submit(worker, "admission")
                    self.assertTrue(admission_ready.wait(10), "Admission did not reach commit.")
                    invite = pool.submit(worker, "invitation")
                    self.assertTrue(user_locked.wait(10), "Invitation did not lock the user.")
                self.assertTrue(admission_ready.wait(10), "Admission did not reach commit.")
                pid_map = dict(pids.get(timeout=5) for _ in range(2))
                release_user.set()
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_blocking_pids(%s), query FROM pg_stat_activity WHERE pid=%s",
                            [pid_map["invitation"], pid_map["invitation"]],
                        )
                        state = cursor.fetchone()
                    if state and pid_map["admission"] in state[0]:
                        self.assertIn('core_membershipinvitation', state[1])
                        self.assertIn('JOIN "core_tenant"', state[1])
                        break
                    sleep(0.02)
                else:
                    self.fail("Invitation did not wait for the admission tenant lock.")
            finally:
                release_user.set()
                release_commit.set()
            outcomes = admission.result(timeout=20), invite.result(timeout=20)

        self.assertEqual(
            [(status, sqlstate) for status, sqlstate, _ in outcomes],
            [(201, None), (201 if successful else 409, None)],
            f"outcomes={outcomes!r}; sql={statements!r}",
        )
        self.assertTrue(any(
            'FROM "core_user"' in sql and "FOR NO KEY UPDATE" in sql
            for sql in statements["invitation"]
        ))
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(StudentEnrollment.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            tenant=self.tenant, actor=self.user, action="student.enrolled",
        ).count(), 1)
        self.invitation.refresh_from_db()
        if successful:
            self.assertIsNotNone(self.invitation.accepted_at)
            self.assertTrue(Membership.objects.filter(
                tenant=self.tenant, user=invited_user, role=Membership.Role.TEACHER,
            ).exists())
            self.assertEqual(AuditEvent.objects.filter(
                tenant=self.tenant, actor=invited_user, action="membership.invitation_accepted",
            ).count(), 1)
        else:
            self.assertIsNone(self.invitation.accepted_at)

    def test_admission_first(self):
        self.run_order(invitation_first=False)

    def test_invitation_first(self):
        self.run_order(invitation_first=True)

    def test_successful_invitation_first(self):
        self.run_order(invitation_first=True, successful=True)

    def test_successful_invitation_admission_first(self):
        self.run_order(invitation_first=False, successful=True)

    def run_cross_tenant(self, invitation_first):
        other = create_tenant(name="Other", slug="other-admission", email="other@example.com", phone="2")
        self.invitation.tenant = other
        self.invitation.save(update_fields=("tenant",))
        user_locked, admission_ready = Event(), Event()
        release_user, release_commit = Event(), Event()

        def worker(kind):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")

                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    upper = sql.upper()
                    if (
                        kind == "invitation" and 'FROM "CORE_USER"' in upper
                        and ("FOR UPDATE" in upper or "FOR NO KEY UPDATE" in upper)
                        and not user_locked.is_set()
                    ):
                        user_locked.set()
                        if not release_user.wait(15):
                            raise AssertionError("Invitation was not released.")
                    return result

                with db.execute_wrapper(trace):
                    if kind == "admission":
                        with transaction.atomic():
                            request = APIRequestFactory().post("/students/", {
                                "first_name": "Student", "last_name": "Example",
                                "initial_academic_year": str(self.year.pk),
                                "initial_school_class": str(self.school_class.pk),
                                "enrolled_on": "2026-06-01",
                            }, format="json")
                            force_authenticate(request, user=self.user)
                            response = StudentListCreateView.as_view()(request, tenant_id=self.tenant.pk)
                            admission_ready.set()
                            if not release_commit.wait(15):
                                raise AssertionError("Admission was not released.")
                        return response.status_code
                    request = APIRequestFactory().post("/invitations/accept/", {
                        "token": self.raw, "password": "Example-test-passphrase-123",
                    }, format="json")
                    return AcceptStaffInvitationView.as_view()(request).status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                if invitation_first:
                    invite = pool.submit(worker, "invitation")
                    self.assertTrue(user_locked.wait(10))
                    admission = pool.submit(worker, "admission")
                else:
                    admission = pool.submit(worker, "admission")
                    self.assertTrue(admission_ready.wait(10))
                    invite = pool.submit(worker, "invitation")
                self.assertTrue(user_locked.wait(10))
                self.assertTrue(admission_ready.wait(10))
                release_commit.set()
                self.assertEqual(admission.result(timeout=8), 201)
            finally:
                release_user.set()
                release_commit.set()
            self.assertEqual(invite.result(timeout=20), 201)

        self.invitation.refresh_from_db()
        self.assertIsNotNone(self.invitation.accepted_at)
        self.assertTrue(Membership.objects.filter(
            tenant=other, user=self.user, role=Membership.Role.TEACHER,
        ).exists())
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            tenant=self.tenant, actor=self.user, action="student.enrolled",
        ).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            tenant=other, actor=self.user, action="membership.invitation_accepted",
        ).count(), 1)
        self.assertFalse(AuditEvent.objects.filter(
            tenant=other, action="student.enrolled",
        ).exists())

    def test_cross_tenant_invitation_first(self):
        self.run_cross_tenant(invitation_first=True)

    def test_cross_tenant_admission_first(self):
        self.run_cross_tenant(invitation_first=False)

    def test_acceptance_rolls_back_if_audit_fails(self):
        other = create_tenant(name="Other", slug="other-admission", email="other@example.com", phone="2")
        self.invitation.tenant = other
        self.invitation.save(update_fields=("tenant",))
        self.user.email_verified = False
        self.user.save(update_fields=("email_verified",))
        request = APIRequestFactory().post("/invitations/accept/", {
            "token": self.raw, "password": "Example-test-passphrase-123",
        }, format="json")
        with patch.object(AuditEvent.objects, "create", side_effect=RuntimeError("forced audit failure")):
            with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
                AcceptStaffInvitationView.as_view()(request)
        self.user.refresh_from_db()
        self.invitation.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        self.assertIsNone(self.invitation.accepted_at)
        self.assertFalse(Membership.objects.filter(tenant=other, user=self.user).exists())
        self.assertFalse(AuditEvent.objects.filter(
            tenant=other, action="membership.invitation_accepted",
        ).exists())

    def test_audit_foreign_keys_are_initially_deferred(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.relname, c.condeferrable, c.condeferred "
                "FROM pg_constraint c "
                "JOIN pg_class a ON a.oid = c.conrelid "
                "JOIN pg_class p ON p.oid = c.confrelid "
                "WHERE c.contype = 'f' AND a.relname = 'core_auditevent' "
                "AND p.relname IN ('core_tenant', 'core_user')"
            )
            constraints = set(cursor.fetchall())
        self.assertEqual(constraints, {("core_tenant", True, True), ("core_user", True, True)})
