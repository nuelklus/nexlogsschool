from datetime import date
from unittest.mock import patch

from django.test import TestCase

from core.models import AcademicYear, AuditEvent, Membership, Student
from .models import SchoolClass, StudentEnrollment
from .tests import AcademicFixture


class ClassManagementTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.url = self.base + f"classes/{self.school_class.pk}/management/"

    def test_teacher_and_archiving_preserve_existing_records(self):
        response = self.client.patch(self.url, {
            "class_teacher": str(self.teacher.pk), "is_active": False,
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["class_teacher"], str(self.teacher.pk))
        self.assertFalse(response.data["is_active"])
        listed = self.client.get(self.base + "classes/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [row["class_teacher"] for row in listed.data if row["id"] == str(self.school_class.pk)],
            [str(self.teacher.pk)],
        )
        self.assertTrue(StudentEnrollment.objects.filter(pk=self.enrollment["id"], school_class=self.school_class).exists())
        self.assertTrue(AuditEvent.objects.filter(action="academic.class_group.updated", entity_id=str(self.school_class.pk)).exists())
        student = Student.objects.create(tenant=self.tenant, admission_number="OTHER", first_name="A", last_name="B")
        response = self.client.post(self.base + "enrollments/", {
            "student": str(student.pk), "school_class": str(self.school_class.pk),
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.patch(self.url, {"is_active": True, "class_teacher": None}, format="json").status_code, 200)
        self.school_class.refresh_from_db()
        self.assertTrue(self.school_class.is_active)
        self.assertIsNone(self.school_class.class_teacher)

    def test_tenant_role_and_fields_are_validated(self):
        foreign = self.make_member(self.other_tenant, "foreign-teacher@example.com", Membership.Role.TEACHER)
        for value in (str(foreign.pk), str(self.owner.pk)):
            self.assertEqual(self.client.patch(self.url, {"class_teacher": value}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(self.url, {"name": "Changed"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(self.url, {"is_active": "not a boolean"}, format="json").status_code, 400)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.patch(self.url, {"is_active": False}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.patch(self.url, {"is_active": False}, format="json").status_code, 404)

    @patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1))
    def test_archived_class_cannot_be_a_transfer_destination(self, _today):
        target = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Other", is_active=False)
        response = self.client.post(self.base + "transfers/", {
            "enrollment": self.enrollment["id"], "target_class": str(target.pk),
            "effective_date": "2026-04-01", "reason": "Class change",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(StudentEnrollment.objects.filter(student=self.student).count(), 1)

    def test_archived_class_is_not_a_promotion_destination(self):
        target_year = AcademicYear.objects.create(
            tenant=self.tenant, name="Next", start_date=date(2027, 1, 1), end_date=date(2027, 12, 31)
        )
        target = SchoolClass.objects.create(tenant=self.tenant, academic_year=target_year, name="Grade 2", is_active=False)
        url = self.base + "promotion/"
        params = {"from_year": str(self.year.pk), "to_year": str(target_year.pk)}
        preview = self.client.get(url, params)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data["target_classes"], [])
        result = self.client.post(url, {
            **params, "decisions": [{"enrollment": self.enrollment["id"], "action": "promote", "target_class": str(target.pk)}],
        }, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(StudentEnrollment.objects.filter(student=self.student, school_class=target).exists())
