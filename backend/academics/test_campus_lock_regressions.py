"""Disposable PostgreSQL regression for class-campus and enrollment races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from core.models import AcademicYear, AuditEvent, Campus, Membership, Student, User
from core.tests import create_tenant
from .models import SchoolClass, StudentEnrollment


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CampusLockRegressionTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or connection.vendor != "postgresql"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise SkipTest("Requires the disposable PostgreSQL fixture runner.")
        super().setUpClass()

    def test_freeze_includes_historical_enrollments_and_tenant_isolation(self):
        tenant = create_tenant(name="Race School", slug="race-campus", email="race@example.com", phone="1")
        other = create_tenant(name="Other School", slug="other-campus", email="other@example.com", phone="2")
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        outsider = User.objects.create_user(email="outsider@example.com", full_name="Outsider")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        Membership.objects.create(tenant=other, user=outsider, role=Membership.Role.OWNER)
        old = Campus.objects.create(tenant=tenant, name="Old", code="OLD")
        new = Campus.objects.create(tenant=tenant, name="New", code="NEW")
        foreign = Campus.objects.create(tenant=other, name="Foreign", code="FOREIGN")
        year = AcademicYear.objects.create(
            tenant=tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class", campus=old)
        student = Student.objects.create(
            tenant=tenant, first_name="Student", last_name="Example",
            admission_number="T-1", campus=new,
        )
        path = f"/api/tenants/{tenant.pk}/academics/classes/{school_class.pk}/management/"
        client = APIClient()
        client.force_authenticate(user=owner)
        self.assertEqual(client.patch(path, {"campus": str(new.pk)}, format="json").status_code, 200)
        enrollment = StudentEnrollment.objects.create(
            tenant=tenant, student=student, school_class=school_class, campus=new,
            enrolled_on=date(2026, 1, 1),
        )
        audit_count = AuditEvent.objects.filter(tenant=tenant, action="academic.class_group.updated").count()
        for state in StudentEnrollment.Status.values:
            enrollment.status = state
            enrollment.save(update_fields=("status",))
            self.assertEqual(client.patch(path, {"campus": str(old.pk)}, format="json").status_code, 400)
        self.assertEqual(client.patch(path, {"campus": None}, format="json").status_code, 400)
        self.assertEqual(client.patch(path, {"campus": str(foreign.pk)}, format="json").status_code, 400)
        self.assertEqual(client.patch(path, {"campus": str(new.pk)}, format="json").status_code, 200)
        client.force_authenticate(user=outsider)
        self.assertEqual(client.patch(path, {"campus": str(old.pk)}, format="json").status_code, 404)
        school_class.refresh_from_db()
        self.assertEqual(school_class.campus_id, new.pk)
        self.assertEqual(
            AuditEvent.objects.filter(tenant=tenant, action="academic.class_group.updated").count(),
            audit_count,
        )

    def test_campus_change_rolls_back_if_audit_fails(self):
        tenant = create_tenant(name="Race School", slug="race-campus", email="race@example.com", phone="1")
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        old = Campus.objects.create(tenant=tenant, name="Old", code="OLD")
        new = Campus.objects.create(tenant=tenant, name="New", code="NEW")
        year = AcademicYear.objects.create(
            tenant=tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class", campus=old)
        client = APIClient()
        client.force_authenticate(user=owner)
        with patch("academics.views._audit", side_effect=RuntimeError("forced audit failure")):
            with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
                client.patch(
                    f"/api/tenants/{tenant.pk}/academics/classes/{school_class.pk}/management/",
                    {"campus": str(new.pk)}, format="json",
                )
        school_class.refresh_from_db()
        self.assertEqual(school_class.campus_id, old.pk)
        self.assertFalse(AuditEvent.objects.filter(tenant=tenant, action="academic.class_group.updated").exists())

    def test_enrollment_rolls_back_if_audit_fails(self):
        tenant = create_tenant(name="Race School", slug="race-campus", email="race@example.com", phone="1")
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        campus = Campus.objects.create(tenant=tenant, name="Old", code="OLD")
        year = AcademicYear.objects.create(
            tenant=tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class", campus=campus)
        student = Student.objects.create(
            tenant=tenant, first_name="Student", last_name="Example",
            admission_number="T-1", campus=campus, grade_level="Previous",
        )
        client = APIClient()
        client.force_authenticate(user=owner)
        with patch("academics.views._audit", side_effect=RuntimeError("forced audit failure")):
            with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
                client.post(f"/api/tenants/{tenant.pk}/academics/enrollments/", {
                    "student": str(student.pk), "school_class": str(school_class.pk),
                    "enrolled_on": "2026-06-01",
                }, format="json")
        student.refresh_from_db()
        self.assertEqual(student.grade_level, "Previous")
        self.assertEqual(student.campus_id, campus.pk)
        self.assertFalse(StudentEnrollment.objects.filter(student=student).exists())
        self.assertFalse(AuditEvent.objects.filter(
            tenant=tenant, action__in=("academic.enrollments.created", "student.current_class_assigned")
        ).exists())

    def test_cross_campus_transfer_rejects_missing_student_campus(self):
        tenant = create_tenant(name="Race School", slug="race-campus", email="race@example.com", phone="1")
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        old = Campus.objects.create(tenant=tenant, name="Old", code="OLD")
        new = Campus.objects.create(tenant=tenant, name="New", code="NEW")
        year = AcademicYear.objects.create(
            tenant=tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        source_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Source", campus=old)
        target_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Target", campus=new)
        student = Student.objects.create(
            tenant=tenant, first_name="Student", last_name="Example", admission_number="T-1", campus=None,
        )
        source = StudentEnrollment.objects.create(
            tenant=tenant, student=student, school_class=source_class, campus=old,
            enrolled_on=date(2026, 1, 1),
        )
        client = APIClient()
        client.force_authenticate(user=owner)
        response = client.post(
            f"/api/tenants/{tenant.pk}/academics/transfers/",
            {"enrollment": str(source.pk), "target_class": str(target_class.pk),
             "effective_date": "2026-02-01", "reason": "Transfer"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        source.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(source.status, StudentEnrollment.Status.ACTIVE)
        self.assertIsNone(source.ended_on)
        self.assertIsNone(student.campus_id)
        self.assertFalse(StudentEnrollment.objects.filter(student=student, school_class=target_class).exists())
        self.assertFalse(AuditEvent.objects.filter(tenant=tenant, action="academic.enrollment.transferred").exists())

    def test_enrollment_revalidates_campus_after_class_lock_wait(self):
        tenant = create_tenant(name="Race School", slug="race-campus", email="race@example.com", phone="1")
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        old = Campus.objects.create(tenant=tenant, name="Old", code="OLD")
        new = Campus.objects.create(tenant=tenant, name="New", code="NEW")
        year = AcademicYear.objects.create(
            tenant=tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class", campus=old)
        student = Student.objects.create(
            tenant=tenant, first_name="Student", last_name="Example",
            admission_number="T-1", campus=old,
        )
        entered, release = Event(), Event()
        pids = Queue()
        base = f"/api/tenants/{tenant.pk}/academics/"

        def worker(kind):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '12s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((kind, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if kind == "campus" and sql.upper().startswith('UPDATE "ACADEMICS_SCHOOLCLASS"'):
                        entered.set()
                        if not release.wait(15):
                            raise AssertionError("Campus transaction was not released.")
                    return result

                client = APIClient()
                client.force_authenticate(user=owner)
                with db.execute_wrapper(trace):
                    if kind == "campus":
                        response = client.patch(
                            f"{base}classes/{school_class.pk}/management/", {"campus": str(new.pk)}, format="json"
                        )
                    else:
                        response = client.post(base + "enrollments/", {
                            "student": str(student.pk), "school_class": str(school_class.pk),
                            "enrolled_on": "2026-06-01",
                        }, format="json")
                return response.status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, "campus")
            label, first_pid = pids.get(timeout=5)
            self.assertEqual(label, "campus")
            try:
                self.assertTrue(entered.wait(10), "Campus update did not reach the barrier.")
                second = pool.submit(worker, "enrollment")
                label, second_pid = pids.get(timeout=5)
                self.assertEqual(label, "enrollment")
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [second_pid])
                        if first_pid in cursor.fetchone()[0]:
                            break
                    sleep(0.02)
                else:
                    self.fail("Enrollment did not wait for the class update.")
            finally:
                release.set()
            outcomes = first.result(timeout=20), second.result(timeout=20)

        self.assertEqual(outcomes, (200, 400))
        school_class.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(school_class.campus_id, new.pk)
        self.assertEqual(student.campus_id, old.pk)
        self.assertFalse(StudentEnrollment.objects.filter(student=student).exists())
        self.assertFalse(AuditEvent.objects.filter(
            tenant=tenant, action__in=("academic.enrollments.created", "student.current_class_assigned")
        ).exists())

    def test_class_campus_change_waits_for_first_enrollment(self):
        tenant = create_tenant(name="Race School", slug="race-campus", email="race@example.com", phone="1")
        owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        old = Campus.objects.create(tenant=tenant, name="Old", code="OLD")
        new = Campus.objects.create(tenant=tenant, name="New", code="NEW")
        year = AcademicYear.objects.create(
            tenant=tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class", campus=old)
        student = Student.objects.create(
            tenant=tenant, first_name="Student", last_name="Example",
            admission_number="T-1", campus=old,
        )
        entered, release = Event(), Event()
        pids = Queue()
        base = f"/api/tenants/{tenant.pk}/academics/"

        def worker(kind):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '12s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((kind, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if kind == "enrollment" and sql.upper().startswith('INSERT INTO "ACADEMICS_STUDENTENROLLMENT"'):
                        entered.set()
                        if not release.wait(15):
                            raise AssertionError("Enrollment transaction was not released.")
                    return result

                client = APIClient()
                client.force_authenticate(user=owner)
                with db.execute_wrapper(trace):
                    if kind == "campus":
                        response = client.patch(
                            f"{base}classes/{school_class.pk}/management/", {"campus": str(new.pk)}, format="json"
                        )
                    else:
                        response = client.post(base + "enrollments/", {
                            "student": str(student.pk), "school_class": str(school_class.pk),
                            "enrolled_on": "2026-06-01",
                        }, format="json")
                return response.status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, "enrollment")
            label, first_pid = pids.get(timeout=5)
            self.assertEqual(label, "enrollment")
            try:
                self.assertTrue(entered.wait(10), "Enrollment did not reach the barrier.")
                second = pool.submit(worker, "campus")
                label, second_pid = pids.get(timeout=5)
                self.assertEqual(label, "campus")
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [second_pid])
                        if first_pid in cursor.fetchone()[0]:
                            break
                    sleep(0.02)
                else:
                    self.fail("Campus change did not wait for the enrollment.")
            finally:
                release.set()
            outcomes = first.result(timeout=20), second.result(timeout=20)

        self.assertEqual(outcomes, (201, 400))
        school_class.refresh_from_db()
        student.refresh_from_db()
        enrollment = StudentEnrollment.objects.get(student=student)
        self.assertEqual(school_class.campus_id, old.pk)
        self.assertEqual(student.campus_id, old.pk)
        self.assertEqual(enrollment.campus_id, old.pk)
        self.assertFalse(AuditEvent.objects.filter(
            tenant=tenant, action="academic.class_group.updated"
        ).exists())
