from datetime import date
from io import BytesIO
from zipfile import ZipFile
from unittest.mock import patch

from django.test import TestCase

from core.models import AuditEvent, Membership, Student
from .models import ReportCardVersion, SchoolClass, StudentEnrollment
from .tests import AcademicFixture


class ClassReportExportTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.head = self.make_member(self.tenant, "head-export@northstar.example", Membership.Role.HEADTEACHER)
        self.enrollment_obj = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        self.url = self.base + "report-cards/class-export/"
        self.payload = {"term": str(self.term.pk), "school_class": str(self.school_class.pk),
                        "roster_date": "2026-03-10"}

    def report(self, enrollment, version=1, status=ReportCardVersion.Status.PUBLISHED):
        return ReportCardVersion.objects.create(
            tenant=self.tenant, term=self.term, enrollment=enrollment, student=enrollment.student,
            version=version, status=status,
            snapshot={"school": self.tenant.name, "student": enrollment.student.first_name,
                      "admission_number": enrollment.student.admission_number,
                      "class": enrollment.school_class.name, "section": "", "campus": None,
                      "year": self.year.name, "term": self.term.name, "attendance": {"present": 1},
                      "results": [{"result": {"subject": "Math", "examination": "Exam",
                                              "score": "90", "grade": "A", "description": "Good"}}],
                      "overall_average": "90.00", "overall_method": "Average",
                      "class_teacher_remark": "Good", "headteacher_remark": "Well done"},
        )

    def test_exports_latest_published_for_entire_dated_roster_and_audits(self):
        old = self.report(self.enrollment_obj)
        latest = self.report(self.enrollment_obj, version=2)
        self.report(self.enrollment_obj, version=3, status=ReportCardVersion.Status.REVIEWED)
        student = Student.objects.create(tenant=self.tenant, admission_number="NSA-002", first_name="Kojo", last_name="Adjei")
        enrollment = StudentEnrollment.objects.create(tenant=self.tenant, student=student, school_class=self.school_class,
                                                       enrolled_on=date(2026, 2, 1))
        second = self.report(enrollment)
        self.client.force_authenticate(user=self.head)
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {f"{self.enrollment_obj.pk}-v2.pdf", f"{enrollment.pk}-v1.pdf"})
            self.assertTrue(all(archive.read(name).startswith(b"%PDF-") for name in archive.namelist()))
        audit = AuditEvent.objects.get(action="academic.report_card.class_exported")
        self.assertEqual(set(audit.metadata["reports"]), {str(latest.pk), str(second.pk)})
        self.assertNotIn(str(old.pk), audit.metadata["reports"])

    def test_missing_conflicting_and_unauthorized_exports_fail(self):
        self.client.force_authenticate(user=self.head)
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("NSA-001", str(response.data))
        self.report(self.enrollment_obj)
        other_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Grade 2")
        other = StudentEnrollment.objects.create(tenant=self.tenant, student=self.student, school_class=other_class,
                                                  enrolled_on=date(2026, 3, 1))
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 400)
        other.enrolled_on = date(2026, 3, 11)
        other.save(update_fields=("enrolled_on",))
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 200)
        self.assertEqual(self.client.post(self.url, {**self.payload, "roster_date": "2026-05-01"}, format="json").status_code, 400)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 404)

    def test_partial_roster_and_audit_failure_never_return_archive(self):
        self.report(self.enrollment_obj)
        student = Student.objects.create(tenant=self.tenant, admission_number="NSA-002", first_name="Kojo", last_name="Adjei")
        enrollment = StudentEnrollment.objects.create(tenant=self.tenant, student=student, school_class=self.school_class,
                                                       enrolled_on=date(2026, 2, 1))
        self.client.force_authenticate(user=self.head)
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("NSA-002", str(response.data))
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.class_exported").exists())
        self.report(enrollment)
        with patch("academics.report_card_class_export._audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.client.post(self.url, self.payload, format="json")
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.class_exported").exists())
