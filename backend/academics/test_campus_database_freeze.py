"""Migration-built PostgreSQL checks for class-campus database enforcement."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from core.models import AcademicYear, AuditEvent, Campus, Membership, Student, User
from core.tests import create_tenant
from rest_framework.test import APIClient
from .models import SchoolClass, StudentEnrollment


class CampusDatabaseFreezeTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or connection.vendor != "postgresql"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise SkipTest("Requires the disposable PostgreSQL fixture runner.")
        super().setUpClass()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                "AND tgname IN ('academics_guard_class_campus', 'academics_freeze_enrolled_class')"
            )
            if cursor.fetchone()[0] != 2:
                raise AssertionError("Campus freeze migration has not been applied.")

    def setUp(self):
        self.tenant = create_tenant(name="Test", slug="db-freeze", email="test@example.com", phone="1")
        self.old = Campus.objects.create(tenant=self.tenant, name="Old", code="OLD")
        self.new = Campus.objects.create(tenant=self.tenant, name="New", code="NEW")
        year = AcademicYear.objects.create(
            tenant=self.tenant, name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        self.school_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=year, campus=self.old, name="A")
        self.student = Student.objects.create(
            tenant=self.tenant, first_name="A", last_name="B", admission_number="A1", campus=self.old
        )

    def test_direct_orm_and_sql_updates_and_history(self):
        self.assertEqual(SchoolClass.objects.filter(pk=self.school_class.pk).update(campus=self.new), 1)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE academics_schoolclass SET campus_id = %s WHERE id = %s", [self.old.pk, self.school_class.pk]
            )
        enrollment = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=self.school_class, campus=self.old
        )
        for status in StudentEnrollment.Status.values:
            enrollment.status = status
            enrollment.save(update_fields=("status",))
            with self.assertRaises(IntegrityError):
                SchoolClass.objects.filter(pk=self.school_class.pk).update(campus=self.new)
            with self.assertRaises(IntegrityError):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE academics_schoolclass SET campus_id = %s WHERE id = %s",
                        [self.new.pk, self.school_class.pk],
                    )
        enrollment.delete()
        with self.assertRaises(IntegrityError):
            SchoolClass.objects.filter(pk=self.school_class.pk).update(campus=self.new)
        with self.assertRaises(IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE academics_schoolclass SET campus_frozen_by_enrollment = false WHERE id = %s",
                    [self.school_class.pk],
                )
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=self.tenant, user=owner, role=Membership.Role.OWNER)
        client = APIClient()
        client.force_authenticate(user=owner)
        response = client.patch(
            f"/api/tenants/{self.tenant.pk}/academics/classes/{self.school_class.pk}/management/",
            {"campus": str(self.new.pk)}, format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.school_class.refresh_from_db()
        self.assertEqual(self.school_class.campus_id, self.old.pk)

    def test_rollback_does_not_freeze_class(self):
        with self.assertRaisesRegex(RuntimeError, "forced rollback"):
            with transaction.atomic():
                StudentEnrollment.objects.create(
                    tenant=self.tenant, student=self.student, school_class=self.school_class, campus=self.old
                )
                raise RuntimeError("forced rollback")
        self.assertEqual(SchoolClass.objects.filter(pk=self.school_class.pk).update(campus=self.new), 1)

    def test_tenant_isolation_and_mismatched_campus(self):
        foreign = create_tenant(name="Other", slug="db-other", email="other@example.com", phone="2")
        foreign_campus = Campus.objects.create(tenant=foreign, name="Foreign", code="FOREIGN")
        with self.assertRaises(IntegrityError):
            SchoolClass.objects.filter(pk=self.school_class.pk).update(campus=foreign_campus)
        with self.assertRaises(IntegrityError):
            StudentEnrollment.objects.create(
                tenant=foreign, student=self.student, school_class=self.school_class, campus=self.old
            )
        with self.assertRaises(IntegrityError):
            StudentEnrollment.objects.create(
                tenant=self.tenant, student=self.student, school_class=self.school_class, campus=self.new
            )
        self.assertFalse(StudentEnrollment.objects.filter(student=self.student).exists())
        self.assertEqual(SchoolClass.objects.filter(pk=self.school_class.pk).update(campus=self.new), 1)
        with self.assertRaises(IntegrityError):
            StudentEnrollment.objects.create(
                tenant=self.tenant, student=self.student, school_class=self.school_class, campus=self.new
            )
        self.assertFalse(StudentEnrollment.objects.filter(student=self.student).exists())

    def test_reassignment_freezes_both_classes(self):
        destination = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.school_class.academic_year, campus=self.old, name="B"
        )
        enrollment = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=self.school_class, campus=self.old
        )
        StudentEnrollment.objects.filter(pk=enrollment.pk).update(school_class=destination)
        for school_class in (self.school_class, destination):
            with self.assertRaises(IntegrityError):
                SchoolClass.objects.filter(pk=school_class.pk).update(campus=self.new)

    def run_race(self, enrollment_first):
        held, release = Event(), Event()
        pids = Queue()

        def worker(kind, hold):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((kind, cursor.fetchone()[0]))
                try:
                    with transaction.atomic():
                        if kind == "enrollment":
                            StudentEnrollment.objects.create(
                                tenant_id=self.tenant.pk, student_id=self.student.pk,
                                school_class_id=self.school_class.pk, campus_id=self.old.pk,
                            )
                        else:
                            SchoolClass.objects.filter(pk=self.school_class.pk).update(campus_id=self.new.pk)
                        if hold:
                            held.set()
                            if not release.wait(12):
                                raise AssertionError("First transaction was not released.")
                    return "committed"
                except DatabaseError as exc:
                    return getattr(exc.__cause__, "sqlstate", None)
            finally:
                db.close()

        first_kind = "enrollment" if enrollment_first else "campus"
        second_kind = "campus" if enrollment_first else "enrollment"
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(worker, first_kind, True)
            label, first_pid = pids.get(timeout=5)
            self.assertEqual(label, first_kind)
            try:
                self.assertTrue(held.wait(8))
                b = pool.submit(worker, second_kind, False)
                label, second_pid = pids.get(timeout=5)
                self.assertEqual(label, second_kind)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [second_pid])
                        if first_pid in cursor.fetchone()[0]:
                            break
                    sleep(0.02)
                else:
                    self.fail("Expected database lock wait.")
            finally:
                release.set()
            self.assertEqual((a.result(timeout=15), b.result(timeout=15)), ("committed", "23514"))
        self.school_class.refresh_from_db()
        self.assertEqual(self.school_class.campus_id, self.old.pk if enrollment_first else self.new.pk)
        self.assertEqual(StudentEnrollment.objects.filter(student=self.student).count(), int(enrollment_first))

    def test_enrollment_first(self):
        self.run_race(True)

    def test_campus_first(self):
        self.run_race(False)

    @patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1))
    def assert_transfer_enrollment_race(self, transfer_first, _today):
        year = self.school_class.academic_year
        year.is_current = True
        year.save(update_fields=("is_current",))
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=self.tenant, user=owner, role=Membership.Role.OWNER)
        target = SchoolClass.objects.create(tenant=self.tenant, academic_year=year, campus=self.old, name="B")
        source = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=self.school_class,
            campus=self.old, enrolled_on=date(2026, 1, 1),
        )
        other = Student.objects.create(
            tenant=self.tenant, first_name="C", last_name="D", admission_number="A2", campus=self.old
        )
        entered, release = Event(), Event()
        pids = Queue()
        base = f"/api/tenants/{self.tenant.pk}/academics/"
        first_kind = "transfer" if transfer_first else "enroll"

        def worker(kind):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((kind, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if kind == first_kind and sql.upper().startswith('INSERT INTO "ACADEMICS_STUDENTENROLLMENT"'):
                        entered.set()
                        if not release.wait(12):
                            raise AssertionError("First enrollment was not released.")
                    return result

                client = APIClient()
                client.force_authenticate(user=owner)
                with db.execute_wrapper(trace):
                    if kind == "transfer":
                        response = client.post(base + "transfers/", {
                            "enrollment": str(source.pk), "target_class": str(target.pk),
                            "effective_date": "2026-02-01", "reason": "Class change",
                        }, format="json")
                    else:
                        response = client.post(base + "enrollments/", {
                            "student": str(other.pk), "school_class": str(target.pk), "enrolled_on": "2026-02-01",
                        }, format="json")
                    return response.status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, first_kind)
            label, first_pid = pids.get(timeout=5)
            self.assertEqual(label, first_kind)
            try:
                self.assertTrue(entered.wait(8))
                second_kind = "enroll" if transfer_first else "transfer"
                second = pool.submit(worker, second_kind)
                label, second_pid = pids.get(timeout=5)
                self.assertEqual(label, second_kind)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [second_pid])
                        if first_pid in cursor.fetchone()[0]:
                            break
                    sleep(0.02)
                else:
                    self.fail("Transfer and enrollment did not wait for the destination class.")
            finally:
                release.set()
            self.assertEqual((first.result(timeout=15), second.result(timeout=15)), (201, 201))
        source.refresh_from_db()
        self.assertEqual(source.status, StudentEnrollment.Status.TRANSFERRED)
        self.assertEqual(StudentEnrollment.objects.filter(school_class=target, campus=self.old).count(), 2)
        self.assertEqual(AuditEvent.objects.filter(
            tenant=self.tenant, action="academic.enrollment.transferred"
        ).count(), 1)

    def test_transfer_first_then_enrollment(self):
        self.assert_transfer_enrollment_race(True)

    def test_enrollment_first_then_transfer(self):
        self.assert_transfer_enrollment_race(False)
