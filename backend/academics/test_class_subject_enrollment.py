from io import StringIO
from datetime import date
from types import SimpleNamespace

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from core.models import AcademicYear, Student
from .class_subjects import class_subject_ids
from .examinations import _roster
from .models import AssessmentResult, SchoolClass, StudentEnrollment, Subject, TeachingAssignment
from .tests import AcademicFixture


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ClassSubjectEnrollmentTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.enrollment_row = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        self.science = Subject.objects.create(tenant=self.tenant, name="Science", code="SCI")
        self.assignment = TeachingAssignment.objects.create(
            tenant=self.tenant, school_class=self.school_class, subject=self.science, teacher=self.teacher
        )

    def test_existing_restrictions_do_not_exclude_class_subjects_from_api_or_roster(self):
        self.enrollment_row.subjects.set([self.subject])
        rows = self.client.get(self.base + "enrollments/").data
        self.assertEqual(set(rows[0]["subjects"]), {self.subject.pk, self.science.pk})
        exam = SimpleNamespace(
            tenant_id=self.tenant.pk, school_class=self.school_class,
            school_class_id=self.school_class.pk, subject_id=self.science.pk,
            start_date=self.enrollment_row.enrolled_on,
        )
        self.assertEqual(list(_roster(exam)), [self.enrollment_row])
        self.assignment.delete()
        with self.assertRaises(ValidationError):
            _roster(exam)
        self.assertEqual(set(class_subject_ids(self.tenant.pk, self.school_class.pk)), {self.subject.pk})
        self.assertEqual(self.client.get(self.base + "enrollments/").data[0]["subjects"], [self.subject.pk])

    def test_new_enrollment_follows_class_and_rejects_restrictions(self):
        student = Student.objects.create(
            tenant=self.tenant, first_name="New", last_name="Student", admission_number="NEW-001"
        )
        payload = {"student": str(student.pk), "school_class": str(self.school_class.pk),
                   "enrolled_on": "2026-01-05"}
        response = self.client.post(self.base + "enrollments/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(set(response.data["subjects"]), {self.subject.pk, self.science.pk})
        self.assertFalse(StudentEnrollment.objects.get(pk=response.data["id"]).subjects.exists())
        self.assertNotEqual(self.client.post(self.base + "enrollments/", payload, format="json").status_code, 201)
        other = Student.objects.create(
            tenant=self.tenant, first_name="Other", last_name="Student", admission_number="NEW-002"
        )
        response = self.client.post(self.base + "enrollments/", {
            **payload, "student": str(other.pk), "subjects": [str(self.subject.pk)],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("subset", str(response.data))

    def test_conversion_is_scoped_idempotent_and_preserves_students_and_results(self):
        result = AssessmentResult.objects.create(
            tenant=self.tenant, assessment_id=self.assessment["id"], enrollment=self.enrollment_row, score="50"
        )
        self.enrollment_row.subjects.add(self.subject, self.subject)
        self.assertEqual(self.enrollment_row.subjects.count(), 1)
        foreign_subject = Subject.objects.create(tenant=self.other_tenant, name="Foreign", code="FOREIGN")
        self.enrollment_row.subjects.add(foreign_subject)
        foreign_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="Foreign year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        foreign_class = SchoolClass.objects.create(tenant=self.other_tenant, academic_year=foreign_year, name="Foreign")
        foreign_student = Student.objects.create(
            tenant=self.other_tenant, first_name="Foreign", last_name="Student", admission_number="OTHER"
        )
        foreign_enrollment = StudentEnrollment.objects.create(
            tenant=self.other_tenant, student=foreign_student, school_class=foreign_class
        )
        foreign_enrollment.subjects.add(foreign_subject)
        output = StringIO()
        call_command("standardize_class_subjects", self.tenant.slug, stdout=output)
        self.assertIn("Effective subject memberships added: 1", output.getvalue())
        self.assertIn("Legacy restriction rows to remove: 2", output.getvalue())
        self.assertEqual(self.enrollment_row.subjects.count(), 2)
        call_command("standardize_class_subjects", self.tenant.slug, "--apply", stdout=StringIO())
        self.assertFalse(self.enrollment_row.subjects.exists())
        self.assertEqual(list(foreign_enrollment.subjects.all()), [foreign_subject])
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(AssessmentResult.objects.filter(pk=result.pk).exists())
        self.assertEqual(set(self.client.get(self.base + "enrollments/").data[0]["subjects"]),
                         {self.subject.pk, self.science.pk})
        output = StringIO()
        call_command("standardize_class_subjects", self.tenant.slug, "--apply", stdout=output)
        self.assertIn("Legacy restriction rows to remove: 0", output.getvalue())

    def test_foreign_assignment_is_not_exposed_and_conversion_refuses_corruption(self):
        foreign = Subject.objects.create(tenant=self.other_tenant, name="Foreign", code="FOREIGN")
        TeachingAssignment.objects.create(
            tenant=self.tenant, school_class=self.school_class, subject=foreign, teacher=self.teacher
        )
        self.assertNotIn(foreign.pk, class_subject_ids(self.tenant.pk, self.school_class.pk))
        with self.assertRaises(CommandError):
            call_command("standardize_class_subjects", self.tenant.slug, "--apply", stdout=StringIO())

    def test_admission_inherits_class_subjects(self):
        response = self.client.post(f"/api/tenants/{self.tenant.pk}/students/", {
            "first_name": "Admitted", "last_name": "Student", "initial_academic_year": str(self.year.pk),
            "initial_school_class": str(self.school_class.pk), "enrolled_on": "2026-01-05",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        enrollment = StudentEnrollment.objects.get(student_id=response.data["id"])
        self.assertFalse(enrollment.subjects.exists())
        records = self.client.get(self.base + "enrollments/").data
        admitted = next(row for row in records if str(row["id"]) == str(enrollment.pk))
        self.assertEqual(set(admitted["subjects"]), {self.subject.pk, self.science.pk})
