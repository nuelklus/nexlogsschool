from unittest.mock import patch

from django.test import TestCase

from core.models import AuditEvent, Campus, Membership, Student
from .models import SchoolClass, StudentEnrollment
from .tests import AcademicFixture


class CampusSetupTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.campuses_url = f"/api/tenants/{self.tenant.pk}/campuses/"

    def test_create_update_and_tenant_isolation(self):
        response = self.client.post(self.campuses_url, {"name": "Main Campus", "code": "MAIN"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        campus_id = response.data["id"]
        self.assertEqual(response.data["status"], "active")
        self.assertEqual(len(self.client.get(self.campuses_url).data), 1)
        self.assertEqual(self.client.post(self.campuses_url, {"name": "Duplicate", "code": "MAIN"}, format="json").status_code, 400)
        response = self.client.patch(self.campuses_url + campus_id + "/", {"status": "inactive", "address": "New address"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "inactive")
        self.assertEqual(AuditEvent.objects.filter(action__in=("campus.created", "campus.updated")).count(), 2)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.get(self.campuses_url).status_code, 404)
        self.assertEqual(self.client.post(self.campuses_url, {"name": "X", "code": "X"}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        other_url = f"/api/tenants/{self.other_tenant.pk}/campuses/"
        self.assertEqual(self.client.get(other_url).data, [])
        self.assertEqual(self.client.patch(other_url + campus_id + "/", {"name": "Other"}, format="json").status_code, 404)

    def test_audit_failure_rolls_back_campus_write(self):
        with patch("core.campuses._audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                self.client.post(self.campuses_url, {"name": "Main", "code": "MAIN"}, format="json")
        self.assertFalse(Campus.objects.filter(tenant=self.tenant, code="MAIN").exists())

    def test_new_classes_require_active_school_campus(self):
        url = self.base + "classes/"
        payload = {"academic_year": str(self.year.pk), "name": "New class"}
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, "campus": None}, format="json").status_code, 400)
        foreign = Campus.objects.create(tenant=self.other_tenant, name="Foreign", code="F")
        inactive = Campus.objects.create(tenant=self.tenant, name="Inactive", code="I", status="inactive")
        self.assertEqual(self.client.post(url, {**payload, "campus": str(foreign.pk)}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, "campus": str(inactive.pk)}, format="json").status_code, 400)
        active = Campus.objects.create(tenant=self.tenant, name="Main", code="M")
        result = self.client.post(url, {**payload, "campus": str(active.pk)}, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(str(result.data["campus"]), str(active.pk))
        self.assertEqual(self.client.patch(url + result.data["id"] + "/management/", {"campus": None}, format="json").status_code, 400)
        self.school_class.refresh_from_db()
        self.assertIsNone(self.school_class.campus_id)
        self.assertEqual(SchoolClass.objects.get(pk=result.data["id"]).campus_id, active.pk)

    def test_school_admin_can_manage_campus(self):
        admin = self.make_member(self.tenant, "campus-admin@example.test", Membership.Role.SCHOOL_ADMIN)
        self.client.force_authenticate(user=admin)
        response = self.client.post(self.campuses_url, {"name": "Main", "code": "M"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)

    def test_setup_class_and_admission_record_the_same_campus(self):
        campus = self.client.post(self.campuses_url, {"name": "Main", "code": "MAIN"}, format="json")
        self.assertEqual(campus.status_code, 201, campus.data)
        school_class = self.client.post(self.base + "classes/", {
            "academic_year": str(self.year.pk), "name": "New class", "campus": campus.data["id"],
        }, format="json")
        self.assertEqual(school_class.status_code, 201, school_class.data)
        student = self.client.post(f"/api/tenants/{self.tenant.pk}/students/", {
            "first_name": "Test", "last_name": "Student", "initial_academic_year": str(self.year.pk),
            "initial_school_class": school_class.data["id"], "enrolled_on": "2026-02-01",
        }, format="json")
        self.assertEqual(student.status_code, 201, student.data)
        saved = Student.objects.get(pk=student.data["id"])
        enrollment = StudentEnrollment.objects.get(student=saved)
        self.assertEqual(str(saved.campus_id), campus.data["id"])
        self.assertEqual(str(enrollment.campus_id), campus.data["id"])
