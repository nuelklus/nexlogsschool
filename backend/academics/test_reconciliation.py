from django.test import TestCase

from core.models import AcademicYear, Student

from .models import ClassLevelCatalogue, SchoolClass, SchoolClassLevel, StudentEnrollment
from .tests import AcademicFixture


class ReconciliationTests(AcademicFixture, TestCase):
    def test_suggests_only_matching_levels_and_reports_student_conflicts(self):
        basic = SchoolClassLevel.objects.create(
            tenant=self.tenant, catalogue_level=ClassLevelCatalogue.objects.get(code="gh-basic-1"),
            display_name="Primary One",
        )
        ambiguous = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Grade 1")
        SchoolClassLevel.objects.create(tenant=self.other_tenant, display_name="Grade 1")
        missing = Student.objects.create(
            tenant=self.tenant, admission_number="OTHER", first_name="Other", last_name="Student",
            grade_level="Custom",
        )
        self.student.grade_level = "Different"
        self.student.save(update_fields=("grade_level",))
        before = (SchoolClass.objects.count(), SchoolClassLevel.objects.count(), StudentEnrollment.objects.count())
        response = self.client.get(self.base + "class-reconciliation/")
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data["unmapped_classes"] if item["id"] == str(self.school_class.pk))
        self.assertEqual({candidate["id"] for candidate in row["candidates"]}, {str(basic.pk), str(ambiguous.pk)})
        issues = {item["id"]: item["issue"] for item in response.data["student_issues"]}
        self.assertEqual(issues[str(self.student.pk)], "grade_mismatch")
        self.assertEqual(issues[str(missing.pk)], "no_current_enrollment")
        self.assertEqual(before, (SchoolClass.objects.count(), SchoolClassLevel.objects.count(), StudentEnrollment.objects.count()))
        self.school_class.refresh_from_db()
        self.assertIsNone(self.school_class.class_level_id)

    def test_no_current_year_never_misreports_students_as_unenrolled(self):
        AcademicYear.objects.filter(pk=self.year.pk).update(is_current=False)
        data = self.client.get(self.base + "class-reconciliation/").data
        self.assertIsNone(data["current_year"])
        self.assertEqual(data["student_issues"], [])

    def test_multiple_active_enrollments_and_archived_levels(self):
        other_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Grade 2")
        StudentEnrollment.objects.create(tenant=self.tenant, student=self.student, school_class=other_class)
        SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Grade 1", is_active=False)
        data = self.client.get(self.base + "class-reconciliation/").data
        self.assertEqual(data["student_issues"][0]["issue"], "multiple_current_enrollments")
        row = next(item for item in data["unmapped_classes"] if item["id"] == str(self.school_class.pk))
        self.assertEqual(row["candidates"], [])

    def test_owner_only_and_tenant_isolation(self):
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.get(self.base + "class-reconciliation/").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.get(self.base + "class-reconciliation/").status_code, 404)
