from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.models import AcademicYear, Membership, Student
from .models import AttendanceRecord, SchoolClass, StudentEnrollment
from .tests import AcademicFixture


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AttendanceRegisterTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.second = Student.objects.create(
            tenant=self.tenant, admission_number="NSA-002", first_name="Kojo", last_name="Asare"
        )
        self.second_enrollment = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.second, school_class=self.school_class,
            enrolled_on=date(2026, 1, 5),
        )
        self.url = self.base + "attendance/register/"

    def payload(self, **overrides):
        return {
            "school_class": str(self.school_class.pk),
            "date": "2026-02-02",
            "entries": [
                {"enrollment": self.enrollment["id"], "status": "present", "notes": ""},
                {"enrollment": str(self.second_enrollment.pk), "status": "late", "notes": "Bus delay"},
            ],
            **overrides,
        }

    def test_saves_all_entries_and_records_the_actor(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["created"], 2)
        records = AttendanceRecord.objects.order_by("status")
        self.assertEqual(records.count(), 2)
        self.assertEqual(set(records.values_list("status", flat=True)), {"present", "late"})
        self.assertEqual(set(records.values_list("recorded_by_id", flat=True)), {self.teacher.pk})
        self.assertEqual(records.get(status="late").notes, "Bus delay")

    def test_existing_record_is_never_overwritten_and_conflict_is_atomic(self):
        original = AttendanceRecord.objects.create(
            tenant=self.tenant, enrollment_id=self.enrollment["id"],
            date=date(2026, 2, 2), status="absent", notes="Original",
        )
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 409)
        original.refresh_from_db()
        self.assertEqual((original.status, original.notes), ("absent", "Original"))
        self.assertEqual(AttendanceRecord.objects.count(), 1)
        response = self.client.post(self.url, self.payload(entries=self.payload()["entries"][1:]), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(AttendanceRecord.objects.count(), 2)

    def test_invalid_entries_and_dates_leave_no_records(self):
        for payload in [
            self.payload(date="2027-01-01"),
            self.payload(date="2026-01-01"),
            self.payload(entries=[]),
            self.payload(entries=[self.payload()["entries"][0]] * 2),
            self.payload(entries=[{"enrollment": self.enrollment["id"], "status": ""}]),
        ]:
            response = self.client.post(self.url, payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_transferred_enrollment_is_valid_only_during_its_historical_dates(self):
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        enrollment.status = StudentEnrollment.Status.TRANSFERRED
        enrollment.ended_on = date(2026, 2, 10)
        enrollment.save(update_fields=("status", "ended_on"))
        entry = self.payload()["entries"][:1]
        response = self.client.post(self.url, self.payload(entries=entry), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(self.url, self.payload(date="2026-02-11", entries=entry), format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_teacher_assignment_and_tenant_are_enforced(self):
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 404)
        self.client.force_authenticate(user=self.teacher)
        other_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Other")
        self.assertEqual(self.client.post(self.url, self.payload(school_class=str(other_class.pk)), format="json").status_code, 404)
        foreign_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="Other", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        foreign = SchoolClass.objects.create(tenant=self.other_tenant, academic_year=foreign_year, name="Foreign")
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.post(self.url, self.payload(school_class=str(foreign.pk)), format="json").status_code, 404)
        reader = self.make_member(self.tenant, "accountant@northstar.example", Membership.Role.ACCOUNTANT)
        self.client.force_authenticate(user=reader)
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 404)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_enrollment_from_another_class_or_tenant_is_rejected(self):
        other_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Other")
        other_enrollment = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.second, school_class=other_class, enrolled_on=date(2026, 1, 5)
        )
        foreign_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="Foreign", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        foreign_class = SchoolClass.objects.create(tenant=self.other_tenant, academic_year=foreign_year, name="Foreign")
        foreign_student = Student.objects.create(
            tenant=self.other_tenant, admission_number="OTHER", first_name="Other", last_name="Student"
        )
        foreign_enrollment = StudentEnrollment.objects.create(
            tenant=self.other_tenant, student=foreign_student, school_class=foreign_class, enrolled_on=date(2026, 1, 5)
        )
        for enrollment in (other_enrollment, foreign_enrollment):
            entries = self.payload()["entries"][:1] + [{"enrollment": str(enrollment.pk), "status": "present"}]
            response = self.client.post(self.url, self.payload(entries=entries), format="json")
            self.assertEqual(response.status_code, 400, response.data)
            self.assertFalse(AttendanceRecord.objects.exists())

    def test_closed_term_and_audit_failure_leave_no_records(self):
        self.term.is_completed = True
        self.term.save(update_fields=("is_completed",))
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 400)
        self.term.is_completed = False
        self.term.save(update_fields=("is_completed",))
        with patch("academics.attendance._audit", side_effect=[None, RuntimeError("audit failed")]):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.payload(), format="json")
        self.assertFalse(AttendanceRecord.objects.exists())
