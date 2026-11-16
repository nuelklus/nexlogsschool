from datetime import date
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from core.models import AcademicYear, Campus, Membership, Student, User
from core.tests import create_tenant

from .models import AcademicTerm, Assessment, AssessmentResult, AttendanceRecord, SchoolClass, StudentEnrollment, Subject


@skipUnless(connection.vendor == "postgresql", "Row-level locking needs PostgreSQL")
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EnrollmentConcurrencyTests(TransactionTestCase):
    def test_archiving_racing_enrollment_does_not_create_after_archive(self):
        tenant = create_tenant(name="Test school", slug="archive-race", email="test@example.com", phone="1")
        owner = User.objects.create_user(email="archive-race@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        year = AcademicYear.objects.create(
            tenant=tenant, name="Year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        student = Student.objects.create(tenant=tenant, admission_number="T1", first_name="A", last_name="B")
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class")
        barrier = Barrier(2, timeout=15)
        results, errors = [], []
        base = f"/api/tenants/{tenant.pk}/academics/"

        def execute(archive):
            try:
                client = APIClient()
                client.force_authenticate(user=owner)
                barrier.wait()
                if archive:
                    response = client.patch(base + f"classes/{school_class.pk}/management/", {"is_active": False}, format="json")
                else:
                    response = client.post(base + "enrollments/", {
                        "student": str(student.pk), "school_class": str(school_class.pk),
                    }, format="json")
                results.append((archive, response.status_code))
            except Exception as error:
                errors.append(error)
            finally:
                connections.close_all()

        threads = [Thread(target=execute, args=(value,)) for value in (True, False)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertIn((True, 200), results)
        enrollment_status = next(status for archive, status in results if not archive)
        self.assertIn(enrollment_status, (201, 400))
        self.assertEqual(StudentEnrollment.objects.filter(student=student, school_class=school_class).count(), int(enrollment_status == 201))
        school_class.refresh_from_db()
        self.assertFalse(school_class.is_active)

    def run_race(self, owner, base, requests):
        barrier = Barrier(2, timeout=15)
        results, errors = [], []

        def execute(endpoint, data):
            try:
                client = APIClient()
                client.force_authenticate(user=owner)
                barrier.wait()
                response = client.post(base + endpoint, data, format="json")
                results.append((endpoint, response.status_code, response.data))
            except Exception as error:
                errors.append(error)
            finally:
                connections.close_all()

        threads = [Thread(target=execute, args=request) for request in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        return results

    @patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1))
    def test_transfer_races_preserve_a_single_consistent_history(self, _today):
        tenant = create_tenant(name="Test school", slug="transfer-race", email="test@example.com", phone="1")
        owner = User.objects.create_user(email="transfer-race@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        year = AcademicYear.objects.create(
            tenant=tenant, name="Year", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        term = AcademicTerm.objects.create(
            tenant=tenant, academic_year=year, name="Term", start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
        )
        campus = Campus.objects.create(tenant=tenant, name="Main", code="MAIN")
        classes = [
            SchoolClass.objects.create(tenant=tenant, academic_year=year, name=name, campus=campus)
            for name in ("Class A", "Class B", "Class C")
        ]
        subject = Subject.objects.create(tenant=tenant, name="Math", code="MATH")
        assessment = Assessment.objects.create(
            tenant=tenant, term=term, school_class=classes[0], subject=subject,
            name="Exam", assessment_date=date(2026, 5, 1), max_score=100,
        )
        base = f"/api/tenants/{tenant.pk}/academics/"
        for case in ("transfer", "attendance", "results"):
            with self.subTest(case=case):
                student = Student.objects.create(
                    tenant=tenant, admission_number=case, first_name="A", last_name="B", campus=campus
                )
                source = StudentEnrollment.objects.create(
                    tenant=tenant, student=student, school_class=classes[0], campus=campus,
                    enrolled_on=date(2026, 1, 1)
                )
                transfer = {
                    "enrollment": str(source.pk), "target_class": str(classes[1].pk),
                    "effective_date": "2026-04-01", "reason": "Class change",
                }
                if case == "transfer":
                    other_request = ("transfers/", {**transfer, "target_class": str(classes[2].pk)})
                elif case == "attendance":
                    other_request = ("attendance/", {
                        "enrollment": str(source.pk), "date": "2026-05-01", "status": "present",
                    })
                else:
                    other_request = ("results/", {
                        "enrollment": str(source.pk), "assessment": str(assessment.pk), "score": "50",
                    })
                results = self.run_race(owner, base, [("transfers/", transfer), other_request])
                self.assertCountEqual([result[1] for result in results], [201, 400], results)
                source.refresh_from_db()
                self.assertEqual(StudentEnrollment.objects.filter(
                    student=student, school_class__academic_year=year,
                    status=StudentEnrollment.Status.ACTIVE,
                ).count(), 1)
                if source.status == StudentEnrollment.Status.TRANSFERRED:
                    self.assertFalse(AttendanceRecord.objects.filter(enrollment=source, date__gte=date(2026, 4, 1)).exists())
                    self.assertFalse(AssessmentResult.objects.filter(
                        enrollment=source, assessment__assessment_date__gte=date(2026, 4, 1)
                    ).exists())

    def test_parallel_class_enrollments_allow_only_one_active_record(self):
        tenant = create_tenant(name="Test school", slug="concurrent-academic", email="test@example.com", phone="1")
        owner = User.objects.create_user(email="concurrent@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        year = AcademicYear.objects.create(
            tenant=tenant, name="Year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        student = Student.objects.create(tenant=tenant, admission_number="T1", first_name="A", last_name="B")
        classes = [
            SchoolClass.objects.create(tenant=tenant, academic_year=year, name=name)
            for name in ("Class A", "Class B")
        ]
        barrier = Barrier(2, timeout=15)
        responses = []
        errors = []

        def enroll(school_class):
            try:
                client = APIClient()
                client.force_authenticate(user=owner)
                barrier.wait()
                result = client.post(
                    f"/api/tenants/{tenant.pk}/academics/enrollments/",
                    {"student": str(student.pk), "school_class": str(school_class.pk)},
                    format="json",
                )
                responses.append(result.status_code)
            except Exception as error:
                errors.append(error)
            finally:
                connections.close_all()

        threads = [Thread(target=enroll, args=(school_class,)) for school_class in classes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual(responses, [201, 400])
        self.assertEqual(StudentEnrollment.objects.filter(
            student=student, school_class__academic_year=year, status=StudentEnrollment.Status.ACTIVE,
        ).count(), 1)

    def test_promotion_racing_manual_enrollment_keeps_one_active_target(self):
        tenant = create_tenant(name="Test school", slug="promotion-race", email="test@example.com", phone="1")
        owner = User.objects.create_user(email="promotion-race@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        source = AcademicYear.objects.create(
            tenant=tenant, name="Source", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )
        target = AcademicYear.objects.create(
            tenant=tenant, name="Target", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        campus = Campus.objects.create(tenant=tenant, name="Main", code="MAIN")
        student = Student.objects.create(tenant=tenant, admission_number="T1", first_name="A", last_name="B", campus=campus)
        source_class = SchoolClass.objects.create(tenant=tenant, academic_year=source, name="Grade 1", campus=campus)
        target_class = SchoolClass.objects.create(tenant=tenant, academic_year=target, name="Grade 2", campus=campus)
        source_enrollment = StudentEnrollment.objects.create(
            tenant=tenant, student=student, school_class=source_class, campus=campus
        )
        barrier = Barrier(2, timeout=15)
        results = []
        errors = []
        base = f"/api/tenants/{tenant.pk}/academics/"

        def execute(endpoint, data):
            try:
                client = APIClient()
                client.force_authenticate(user=owner)
                barrier.wait()
                response = client.post(base + endpoint, data, format="json")
                results.append((endpoint, response.status_code, response.data))
            except Exception as error:
                errors.append(error)
            finally:
                connections.close_all()

        threads = [
            Thread(target=execute, args=("enrollments/", {
                "student": str(student.pk), "school_class": str(target_class.pk),
            })),
            Thread(target=execute, args=("promotion/", {
                "from_year": str(source.pk), "to_year": str(target.pk),
                "decisions": [{
                    "enrollment": str(source_enrollment.pk), "action": "promote",
                    "target_class": str(target_class.pk),
                }],
            })),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        promotion = next(result for result in results if result[0] == "promotion/")
        enrollment = next(result for result in results if result[0] == "enrollments/")
        self.assertEqual(promotion[1], 200, results)
        self.assertIn(enrollment[1], (201, 400), results)
        self.assertEqual(promotion[2]["promote"] + promotion[2]["skipped"], 1)
        self.assertEqual(StudentEnrollment.objects.filter(
            student=student, school_class__academic_year=target, status=StudentEnrollment.Status.ACTIVE,
        ).count(), 1)
