from django.db.models.deletion import ProtectedError
from django.test import TestCase

from core.models import AuditEvent, Campus

from .models import AttendanceRecord, SchoolClass, SchoolClassLevel, StudentEnrollment
from .tests import AcademicFixture


class ClassLevelAssignmentTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.level = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Basic 1")
        self.foreign = SchoolClassLevel.objects.create(tenant=self.other_tenant, display_name="Basic 1")
        self.url = self.base + f"classes/{self.school_class.pk}/level/"

    def test_existing_class_mapping_preserves_enrollment_and_history(self):
        attendance = AttendanceRecord.objects.create(
            tenant=self.tenant,
            enrollment_id=self.enrollment["id"],
            date="2026-02-02",
            status=AttendanceRecord.Status.PRESENT,
        )
        response = self.client.patch(self.url, {"class_level": str(self.level.pk)}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(self.school_class.pk))
        self.assertEqual(str(response.data["class_level"]), str(self.level.pk))
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        self.assertEqual(enrollment.school_class_id, self.school_class.pk)
        self.assertEqual(AttendanceRecord.objects.get(pk=attendance.pk).enrollment_id, enrollment.pk)
        self.school_class.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(self.school_class.name, "Grade 1")
        self.assertEqual(self.student.grade_level, "Grade 1")
        event = AuditEvent.objects.get(action="academic.class_level_assigned", entity_id=str(self.school_class.pk))
        self.assertEqual(event.metadata, {"previous_level": None, "new_level": str(self.level.pk)})
        with self.assertRaises(ProtectedError):
            self.level.delete()

    def test_create_class_optional_level_and_existing_api_compatibility(self):
        campus = Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
        response = self.client.post(self.base + "classes/", {
            "academic_year": str(self.year.pk), "name": "Grade 1", "section": "A",
            "class_level": str(self.level.pk), "campus": str(campus.pk),
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["class_level"]), str(self.level.pk))
        legacy = self.client.post(self.base + "classes/", {
            "academic_year": str(self.year.pk), "name": "Grade 2", "section": "", "campus": str(campus.pk),
        }, format="json")
        self.assertEqual(legacy.status_code, 201)
        self.assertIsNone(legacy.data["class_level"])
        duplicate = self.client.post(self.base + "classes/", {
            "academic_year": str(self.year.pk), "name": "Grade 1", "section": "A", "campus": str(campus.pk),
        }, format="json")
        self.assertIn(duplicate.status_code, (400, 409))

    def test_tenant_role_and_archived_level_validation(self):
        self.assertEqual(self.client.patch(
            self.url, {"class_level": str(self.foreign.pk)}, format="json"
        ).status_code, 400)
        self.assertEqual(self.client.post(self.base + "classes/", {
            "academic_year": str(self.year.pk), "name": "Foreign",
            "class_level": str(self.foreign.pk),
        }, format="json").status_code, 400)
        self.level.is_active = False
        self.level.save(update_fields=("is_active",))
        self.assertEqual(self.client.patch(
            self.url, {"class_level": str(self.level.pk)}, format="json"
        ).status_code, 400)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.patch(self.url, {"class_level": None}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.patch(self.url, {"class_level": None}, format="json").status_code, 404)

    def test_reassignment_and_clear_are_audited(self):
        self.assertEqual(self.client.patch(self.url, {"class_level": str(self.level.pk)}, format="json").status_code, 200)
        other = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Other")
        self.assertEqual(self.client.patch(self.url, {"class_level": str(other.pk)}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(self.url, {"class_level": None}, format="json").status_code, 200)
        self.assertEqual(
            AuditEvent.objects.filter(action="academic.class_level_assigned", entity_id=str(self.school_class.pk)).count(),
            3,
        )
        self.assertEqual(self.client.patch(self.url, {"name": "Changed"}, format="json").status_code, 400)
        self.school_class.refresh_from_db()
        self.assertIsNone(self.school_class.class_level_id)
        self.assertEqual(self.school_class.name, "Grade 1")

    def test_archiving_level_preserves_existing_link(self):
        SchoolClass.objects.filter(pk=self.school_class.pk).update(class_level=self.level)
        response = self.client.patch(
            self.base + f"class-levels/{self.level.pk}/", {"is_active": False}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.school_class.refresh_from_db()
        self.assertEqual(self.school_class.class_level_id, self.level.pk)
        listed = self.client.get(self.base + "classes/").data
        self.assertEqual(str(next(row for row in listed if row["id"] == str(self.school_class.pk))["class_level"]), str(self.level.pk))
