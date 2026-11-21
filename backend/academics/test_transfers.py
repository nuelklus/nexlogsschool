from datetime import date
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from core.models import AcademicYear, AuditEvent, Campus, Student
from .models import (
    AssessmentResult, AttendanceRecord, SchoolClass, StudentEnrollment, Subject, TeachingAssignment,
)
from .tests import AcademicFixture


@patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1))
class ClassTransferTests(AcademicFixture, TestCase):
    require_fixture_campus = True

    def setUp(self):
        super().setUp()
        self.target = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Grade 2", campus=self.fixture_campus
        )
        self.url = self.base + "transfers/"

    def payload(self, **overrides):
        return {
            "enrollment": self.enrollment["id"], "target_class": str(self.target.pk),
            "effective_date": "2026-04-01", "reason": "Class change", **overrides,
        }

    def test_transfer_preserves_history_and_updates_current_grade(self, _today):
        source = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        attendance = AttendanceRecord.objects.create(
            tenant=self.tenant, enrollment=source, date=date(2026, 3, 1), status="present"
        )
        result = AssessmentResult.objects.create(
            tenant=self.tenant, assessment_id=self.assessment["id"], enrollment=source, score="50"
        )
        TeachingAssignment.objects.create(
            tenant=self.tenant, school_class=self.target, subject=self.subject, teacher=self.teacher
        )
        response = self.client.post(self.url, self.payload(subjects=[str(self.subject.pk)]), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        source.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(source.status, StudentEnrollment.Status.TRANSFERRED)
        self.assertEqual(source.ended_on, date(2026, 3, 31))
        self.assertEqual(AttendanceRecord.objects.get(pk=attendance.pk).enrollment_id, source.pk)
        self.assertEqual(AssessmentResult.objects.get(pk=result.pk).enrollment_id, source.pk)
        new = StudentEnrollment.objects.get(pk=response.data["enrollment"]["id"])
        self.assertEqual(new.enrolled_on, date(2026, 4, 1))
        self.assertEqual(response.data["enrollment"]["subjects"], [self.subject.pk])
        self.assertEqual(self.student.grade_level, "Grade 2")
        self.assertTrue(AuditEvent.objects.filter(
            tenant=self.tenant, action="academic.enrollment.transferred", entity_id=str(new.pk)
        ).exists())

    def test_rejects_foreign_roles_and_invalid_inputs(self, _today):
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 404)
        self.client.force_authenticate(user=self.owner)
        foreign_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="Other", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        foreign = SchoolClass.objects.create(tenant=self.other_tenant, academic_year=foreign_year, name="Foreign")
        self.assertEqual(self.client.post(self.url, self.payload(target_class=str(foreign.pk)), format="json").status_code, 404)
        for value in ("2026-01-05", "2026-07-01", "2027-01-01"):
            self.assertEqual(self.client.post(self.url, self.payload(effective_date=value), format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, self.payload(reason=" "), format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, self.payload(subjects=[str(self.subject.pk)]), format="json").status_code, 400)
        self.assertEqual(StudentEnrollment.objects.filter(student=self.student).count(), 1)

    def test_rejects_history_after_date_and_closed_terms(self, _today):
        source = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        attendance = AttendanceRecord.objects.create(
            tenant=self.tenant, enrollment=source, date=date(2026, 4, 1), status="present"
        )
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 400)
        attendance.delete()
        AssessmentResult.objects.create(
            tenant=self.tenant, assessment_id=self.assessment["id"], enrollment=source, score="50"
        )
        self.assertEqual(self.client.post(
            self.url, self.payload(effective_date="2026-03-10"), format="json"
        ).status_code, 400)
        self.term.is_completed = True
        self.term.save(update_fields=("is_completed",))
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 400)
        source.refresh_from_db()
        self.assertEqual(source.status, StudentEnrollment.Status.ACTIVE)

    def test_cannot_return_to_a_previous_class_or_transfer_twice(self, _today):
        first = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 400)
        back = self.client.post(self.url, self.payload(
            enrollment=first.data["enrollment"]["id"], target_class=str(self.school_class.pk),
            effective_date="2026-05-01",
        ), format="json")
        self.assertEqual(back.status_code, 400)

    def test_only_current_year_and_conflicting_active_enrollment(self, _today):
        self.year.is_current = False
        self.year.save(update_fields=("is_current",))
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 400)
        self.year.is_current = True
        self.year.save(update_fields=("is_current",))
        other = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Other")
        StudentEnrollment.objects.create(tenant=self.tenant, student=self.student, school_class=other)
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 400)

    def test_no_direct_creation_of_transferred_status(self, _today):
        other = Student.objects.create(tenant=self.tenant, first_name="Other", last_name="Student", admission_number="OTHER")
        response = self.client.post(self.base + "enrollments/", {
            "student": str(other.pk), "school_class": str(self.target.pk), "status": "transferred",
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_missing_and_conflicting_campus_evidence_is_rejected(self, _today):
        source = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        other = Campus.objects.create(tenant=self.tenant, name="Other", code="OTHER")
        for case in ("student_missing", "enrollment_missing", "target_missing", "student_conflict", "target_conflict"):
            with self.subTest(case=case), transaction.atomic():
                if case.startswith("student_"):
                    self.student.campus = None if case == "student_missing" else other
                    self.student.save(update_fields=("campus",))
                elif case == "enrollment_missing":
                    source.campus = None
                    source.save(update_fields=("campus",))
                else:
                    self.target.campus = None if case == "target_missing" else other
                    self.target.save(update_fields=("campus",))
                response = self.client.post(self.url, self.payload(), format="json")
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("campus", str(response.data).lower())
                source.refresh_from_db()
                self.assertEqual(source.status, StudentEnrollment.Status.ACTIVE)
                self.assertIsNone(source.ended_on)
                self.assertEqual(StudentEnrollment.objects.filter(student=self.student).count(), 1)
                self.assertFalse(AuditEvent.objects.filter(
                    tenant=self.tenant, action="academic.enrollment.transferred"
                ).exists())
                transaction.set_rollback(True)
