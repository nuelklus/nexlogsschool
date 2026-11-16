"""PostgreSQL experiments for proposed, not integrated, cross-module locking."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from queue import Queue
from threading import Barrier, Event
from time import monotonic, sleep
from unittest import SkipTest
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from core.admission_numbers import next_admission_number
from core.enrollment import MANAGE_ROLES, _membership
from core.models import AcademicYear, AdmissionSequence, Membership, Student, Tenant, User
from core.subscriptions import SubscriptionRequired
from core.tests import create_tenant
from finance.ledger import lock_finance
from finance.models import Invoice

from .closure import _locked
from .coordination import acquire_academic_coordination
from .models import AcademicTerm, SchoolClass, StudentEnrollment


class CrossModuleCoordinationTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise SkipTest("PostgreSQL is required.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database = cursor.fetchone()[0]
        if not database.startswith("test_") or os.environ.get("NLSCHOOL_LOCK_AUDIT_DB") != database:
            raise SkipTest(
                "These tests flush their database. Set NLSCHOOL_LOCK_AUDIT_DB to the "
                "exact name of an isolated, disposable test_ database."
            )
        super().setUpClass()

    def setUp(self):
        self.tenant = create_tenant(name="Test School", slug="lock-audit", email="test@example.com", phone="1")
        self.owner = User.objects.create_user(email="lock-audit@example.com", full_name="Owner")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER)
        self.year = AcademicYear.objects.create(
            tenant=self.tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        self.term = AcademicTerm.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Term",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        self.school_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="First")
        self.student = Student.objects.create(
            tenant=self.tenant, first_name="Test", last_name="Student", admission_number="ORIGINAL"
        )

    def wait_blocked(self, pid, blocker):
        end = monotonic() + 8
        while monotonic() < end:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_blocking_pids(%s)", [pid])
                if blocker in cursor.fetchone()[0]:
                    return
            sleep(0.02)
        self.fail(f"PostgreSQL did not report {blocker} blocking {pid}")

    def run_serialized(self, first, second, *, coordinated=True):
        entered, release = Event(), Event()
        pids = Queue()

        def execute(operation, hold):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connections["default"].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '10s'")
                        cursor.execute("SET LOCAL statement_timeout = '15s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        pids.put(cursor.fetchone()[0])
                    if coordinated:
                        acquire_academic_coordination(self.tenant.pk)
                    result = operation()
                    if hold:
                        entered.set()
                        if not release.wait(10):
                            raise AssertionError("First transaction was not released")
                    return result
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(execute, first, True)
            try:
                first_pid = pids.get(timeout=5)
                self.assertTrue(entered.wait(8))
                b = pool.submit(execute, second, False)
                second_pid = pids.get(timeout=5)
                self.wait_blocked(second_pid, first_pid)
            finally:
                release.set()
            return a.result(timeout=15), b.result(timeout=15)

    def admit(self, name):
        Tenant.objects.select_for_update().get(pk=self.tenant.pk)
        AcademicYear.objects.select_for_update().get(pk=self.year.pk)
        SchoolClass.objects.select_for_update().get(pk=self.school_class.pk)
        number = next_admission_number(self.tenant.pk, date(2026, 6, 1))
        student = Student.objects.create(
            tenant_id=self.tenant.pk, first_name=name, last_name="Student", admission_number=number
        )
        StudentEnrollment.objects.create(
            tenant_id=self.tenant.pk, student=student, school_class_id=self.school_class.pk,
            enrolled_on=date(2026, 6, 1),
        )
        return number

    def create_finance_year(self):
        AcademicYear.objects.filter(pk=self.year.pk).update(is_current=False)
        year = AcademicYear.objects.create(
            tenant_id=self.tenant.pk, name="Next", start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31), is_current=True,
        )
        return year.pk

    def close_term(self):
        term, _ = _locked(self.tenant.pk, self.term.pk)
        term.is_completed = True
        term.save(update_fields=("is_completed",))
        return term.pk

    def test_admission_and_finance_year_creation(self):
        number, year_id = self.run_serialized(lambda: self.admit("One"), self.create_finance_year)
        self.assertTrue(Student.objects.filter(tenant=self.tenant, admission_number=number).exists())
        self.assertTrue(AcademicYear.objects.get(pk=year_id).is_current)
        self.assertFalse(AcademicYear.objects.get(pk=self.year.pk).is_current)

    def test_admission_and_year_edit(self):
        def edit():
            return AcademicYear.objects.filter(pk=self.year.pk).update(name="Updated")

        number, changed = self.run_serialized(lambda: self.admit("One"), edit)
        self.assertTrue(number)
        self.assertEqual(changed, 1)
        self.assertEqual(AcademicYear.objects.get(pk=self.year.pk).name, "Updated")

    def test_admission_and_closure_locking(self):
        number, term_id = self.run_serialized(lambda: self.admit("One"), self.close_term)
        self.assertTrue(number)
        self.assertTrue(AcademicTerm.objects.get(pk=term_id).is_completed)

    def test_finance_year_and_closure_locking(self):
        year_id, term_id = self.run_serialized(self.create_finance_year, self.close_term)
        self.assertTrue(AcademicYear.objects.get(pk=year_id).is_current)
        self.assertTrue(AcademicTerm.objects.get(pk=term_id).is_completed)

    def test_subscription_change_is_seen_by_later_authorization(self):
        def expire():
            return self.tenant.subscriptions.update(status="expired")

        def authorize():
            try:
                _membership(self.owner, self.tenant.pk, MANAGE_ROLES)
            except SubscriptionRequired:
                return "subscription-required"
            return "allowed"

        changed, outcome = self.run_serialized(expire, authorize)
        self.assertEqual(changed, 1)
        self.assertEqual(outcome, "subscription-required")

    def test_finance_settings_and_invoice_insert(self):
        def settings_change():
            settings = lock_finance(self.tenant.pk)
            settings.invoice_prefix = "TEST"
            settings.save(update_fields=("invoice_prefix",))
            return settings.invoice_prefix

        def create_invoice():
            settings = lock_finance(self.tenant.pk)
            invoice = Invoice.objects.create(
                tenant_id=self.tenant.pk, academic_year_id=self.year.pk, student_id=self.student.pk,
                due_date=date(2026, 12, 1), period_label="Term",
            )
            return settings.invoice_prefix, invoice.pk

        prefix, (seen, invoice_id) = self.run_serialized(settings_change, create_invoice)
        self.assertEqual((prefix, seen), ("TEST", "TEST"))
        self.assertTrue(Invoice.objects.filter(pk=invoice_id, tenant=self.tenant).exists())

    def test_concurrent_admissions_reserve_distinct_numbers(self):
        first, second = self.run_serialized(lambda: self.admit("One"), lambda: self.admit("Two"))
        self.assertNotEqual(first, second)
        self.assertEqual(AdmissionSequence.objects.get(tenant=self.tenant).last_number, 2)
        self.assertEqual(StudentEnrollment.objects.filter(tenant=self.tenant).count(), 2)

    def admit_api(self, name):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.post(
            f"/api/tenants/{self.tenant.pk}/students/",
            {"first_name": name, "last_name": "Student", "enrolled_on": "2026-06-01",
             "initial_academic_year": str(self.year.pk),
             "initial_school_class": str(self.school_class.pk)},
            format="json",
        )
        return response.status_code, response.data

    def test_existing_uncoordinated_admission_endpoint(self):
        first, second = self.run_serialized(
            lambda: self.admit_api("First"), lambda: self.admit_api("Second"), coordinated=False
        )
        self.assertEqual((first[0], second[0]), (201, 201), (first, second))
        self.assertNotEqual(first[1]["admission_number"], second[1]["admission_number"])
        self.assertEqual(AdmissionSequence.objects.get(tenant=self.tenant).last_number, 2)

    def test_existing_finance_year_then_uncoordinated_admission(self):
        def finance_api():
            client = APIClient()
            client.force_authenticate(user=self.owner)
            response = client.post(
                f"/api/tenants/{self.tenant.pk}/finance/academic-years/",
                {"name": "Next", "start_date": "2027-01-01", "end_date": "2027-12-31",
                 "is_current": True},
                format="json",
            )
            return response.status_code, response.data

        year, admission = self.run_serialized(finance_api, lambda: self.admit_api("One"), coordinated=False)
        self.assertEqual((year[0], admission[0]), (201, 201), (year, admission))
        self.assertTrue(AcademicYear.objects.get(pk=year[1]["id"]).is_current)
        self.assertTrue(Student.objects.filter(admission_number=admission[1]["admission_number"]).exists())

    @patch("academics.transfers.timezone.localdate", return_value=date(2026, 8, 1))
    def test_concurrent_transfers_revalidate_after_barrier(self, _today):
        self.check_concurrent_transfers(coordinated=True)

    @patch("academics.transfers.timezone.localdate", return_value=date(2026, 8, 1))
    def test_existing_uncoordinated_transfers(self, _today):
        self.check_concurrent_transfers(coordinated=False)

    def check_concurrent_transfers(self, *, coordinated):
        source = StudentEnrollment.objects.create(
            tenant=self.tenant, school_class=self.school_class, student=self.student,
            enrolled_on=date(2026, 1, 1),
        )
        classes = [
            SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name=name)
            for name in ("Second", "Third")
        ]

        def transfer(target):
            client = APIClient()
            client.force_authenticate(user=self.owner)
            return client.post(
                f"/api/tenants/{self.tenant.pk}/academics/transfers/",
                {"enrollment": str(source.pk), "target_class": str(target.pk),
                 "effective_date": "2026-06-01", "reason": "Move"},
                format="json",
            ).status_code

        outcomes = self.run_serialized(
            lambda: transfer(classes[0]), lambda: transfer(classes[1]), coordinated=coordinated
        )
        self.assertEqual(outcomes, (201, 400))
        self.assertEqual(StudentEnrollment.objects.filter(
            student=self.student, status=StudentEnrollment.Status.ACTIVE
        ).count(), 1)

    def test_uncoordinated_global_user_update_bypasses_tenant_barrier(self):
        entered, release = Event(), Event()

        def holder():
            close_old_connections()
            try:
                with transaction.atomic():
                    acquire_academic_coordination(self.tenant.pk)
                    entered.set()
                    if not release.wait(10):
                        raise AssertionError("Holder not released")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(holder)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(User.objects.filter(pk=self.owner.pk).update(full_name="Changed"), 1)
            finally:
                release.set()
            future.result(timeout=10)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.full_name, "Changed")

    def test_different_tenant_barriers_cannot_prevent_shared_user_deadlock(self):
        other = create_tenant(name="Other", slug="other-lock-audit", email="other@example.com", phone="2")
        second_user = User.objects.create_user(email="other-user@example.com", full_name="Other")
        barrier = Barrier(2, timeout=8)

        def worker(tenant_id, first, second):
            close_old_connections()
            try:
                try:
                    with transaction.atomic():
                        with connections["default"].cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '10s'")
                        acquire_academic_coordination(tenant_id)
                        User.objects.select_for_update().get(pk=first)
                        barrier.wait()
                        User.objects.select_for_update().get(pk=second)
                    return "committed"
                except DatabaseError as exc:
                    return getattr(exc.__cause__, "sqlstate", None)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, self.tenant.pk, self.owner.pk, second_user.pk)
            second = pool.submit(worker, other.pk, second_user.pk, self.owner.pk)
            self.assertCountEqual([first.result(timeout=15), second.result(timeout=15)], ["committed", "40P01"])
