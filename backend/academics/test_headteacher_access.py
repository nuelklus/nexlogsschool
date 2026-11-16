from django.test import TestCase, override_settings

from core.models import Membership, Student
from .models import AttendanceRecord, TimetableEntry
from .tests import AcademicFixture


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class HeadteacherAcademicAccessTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.headteacher = self.make_member(
            self.tenant, "head@northstar.example", Membership.Role.HEADTEACHER
        )
        self.client.force_authenticate(user=self.headteacher)

    def test_workspace_read_dependencies_are_available_for_own_school(self):
        for collection in (
            "years", "terms", "classes", "subjects", "teachers", "teacher-assignments",
            "enrollments", "attendance", "timetable", "assessments", "results",
            "class-level-catalogue", "class-levels",
        ):
            response = self.client.get(self.base + collection + "/")
            self.assertEqual(response.status_code, 200, (collection, response.data))
        students = self.client.get(f"/api/tenants/{self.tenant.pk}/students/")
        self.assertEqual(students.status_code, 200, students.data)
        self.assertEqual([row["id"] for row in students.data], [str(self.student.pk)])
        detail = self.client.get(f"/api/tenants/{self.tenant.pk}/students/{self.student.pk}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["grade_level"], "Grade 1")
        self.assertEqual(self.client.get(self.base + f"terms/{self.term.pk}/closure/").status_code, 200)

    def test_headteacher_cannot_access_another_school_or_modify_records(self):
        foreign_student = Student.objects.create(
            tenant=self.other_tenant, admission_number="OTHER", first_name="Other", last_name="Student"
        )
        self.assertEqual(self.client.get(f"/api/tenants/{self.other_tenant.pk}/students/").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/tenants/{self.other_tenant.pk}/students/{foreign_student.pk}/").status_code, 404
        )
        self.assertEqual(self.client.get(f"/api/tenants/{self.other_tenant.pk}/academics/timetable/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/tenants/{self.tenant.pk}/students/", {}, format="json").status_code, 404)
        self.assertEqual(
            self.client.patch(f"/api/tenants/{self.tenant.pk}/students/{self.student.pk}/",
                              {"first_name": "Changed"}, format="json").status_code, 404
        )
        self.assertEqual(self.client.post(self.base + "timetable/", {}, format="json").status_code, 404)
        self.assertEqual(self.client.post(self.base + "attendance/", {}, format="json").status_code, 404)
        self.assertEqual(self.client.post(self.base + "attendance/register/", {}, format="json").status_code, 404)
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, "Ama")
        self.assertFalse(TimetableEntry.objects.exists())
        self.assertFalse(AttendanceRecord.objects.exists())
