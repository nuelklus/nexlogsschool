from datetime import date
from django.test import TestCase

from core.models import AuditEvent, Membership, Student
from .models import Examination, ExaminationPublication, GradingConfiguration, ReportCardVersion, SchoolClass, StudentEnrollment
from .tests import AcademicFixture


class ReportCardBatchTests(AcademicFixture, TestCase):
    require_fixture_campus = True
    def setUp(self):
        super().setUp()
        self.head = self.make_member(self.tenant, "batch-head@northstar.example", Membership.Role.HEADTEACHER)
        config = GradingConfiguration.objects.create(
            tenant=self.tenant, name="Policy", examination_type="term", configuration={},
        )
        self.exam = Examination.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            configuration=config, name="Exam", start_date=date(2026, 3, 10), end_date=date(2026, 3, 11),
            marks_deadline=date(2026, 3, 15), publication_date=date(2026, 3, 16), published_version=1,
        )
        self.first = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        student = Student.objects.create(tenant=self.tenant, admission_number="NSA-002", first_name="Kojo", last_name="Adjei",
                                         campus=self.fixture_campus)
        self.second = StudentEnrollment.objects.create(tenant=self.tenant, student=student, school_class=self.school_class,
                                                        campus=self.fixture_campus, enrolled_on=date(2026, 1, 5))
        self.url = self.base + "report-cards/"
        self.base_payload = {"term": str(self.term.pk), "school_class": str(self.school_class.pk),
                             "roster_date": "2026-03-10"}

    def publish(self, enrollment):
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=self.exam, enrollment=enrollment, version=1, published_by=self.owner,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "78.00",
                      "grade": "P", "description": "Pass"},
        )

    def entry(self, enrollment):
        return {"enrollment": str(enrollment.pk), "examinations": [str(self.exam.pk)],
                "class_teacher_remark": "Good", "headteacher_remark": "Keep going", "reason": ""}

    def test_batch_is_atomic_and_uses_individual_workflow(self):
        self.publish(self.first)
        self.client.force_authenticate(user=self.head)
        roster = self.client.post(self.url + "class-roster/", self.base_payload, format="json")
        self.assertEqual(roster.status_code, 200, roster.data)
        self.assertEqual(len(roster.data["students"]), 2)
        payload = {**self.base_payload, "entries": [self.entry(self.first), self.entry(self.second)]}
        failed = self.client.post(self.url + "class-review/", payload, format="json")
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertIn("NSA-002", str(failed.data))
        self.assertFalse(ReportCardVersion.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.reviewed").exists())
        self.publish(self.second)
        created = self.client.post(self.url + "class-review/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["count"], 2)
        self.assertEqual(set(ReportCardVersion.objects.values_list("status", flat=True)), {"reviewed"})
        self.assertEqual(AuditEvent.objects.filter(action="academic.report_card.reviewed").count(), 2)
        self.assertEqual(AuditEvent.objects.filter(action="academic.report_card.class_reviewed").count(), 1)
        self.assertEqual(self.client.post(self.url + "class-review/", payload, format="json").status_code, 400)
        self.assertEqual(ReportCardVersion.objects.count(), 2)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.post(self.url + created.data["reports"][0]["id"] + "/publish/", {}, format="json").status_code, 200)

    def test_roster_and_permissions_and_transfer_validation(self):
        self.publish(self.first)
        self.publish(self.second)
        payload = {**self.base_payload, "entries": [self.entry(self.first), self.entry(self.second)]}
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.post(self.url + "class-review/", payload, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.post(self.url + "class-roster/", self.base_payload, format="json").status_code, 404)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(self.url + "class-review/", {**payload, "entries": [self.entry(self.first)]}, format="json").status_code, 400)
        prior_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Earlier class")
        StudentEnrollment.objects.create(tenant=self.tenant, student=self.first.student, school_class=prior_class,
                                         enrolled_on=date(2026, 1, 1), ended_on=date(2026, 1, 4), status="transferred")
        response = self.client.post(self.url + "class-review/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("multiple term enrollments", str(response.data))
        self.assertFalse(ReportCardVersion.objects.exists())

    def test_invalid_dates_roster_entries_and_duplicate_examinations(self):
        self.publish(self.first)
        self.publish(self.second)
        self.client.force_authenticate(user=self.head)
        invalid_date = {**self.base_payload, "roster_date": "2027-01-01"}
        self.assertEqual(self.client.post(self.url + "class-roster/", invalid_date, format="json").status_code, 400)
        entries = [self.entry(self.first), self.entry(self.first)]
        payload = {**self.base_payload, "entries": entries}
        self.assertEqual(self.client.post(self.url + "class-review/", payload, format="json").status_code, 400)
        duplicate_exam = self.entry(self.second)
        duplicate_exam["examinations"].append(str(self.exam.pk))
        payload["entries"] = [self.entry(self.first), duplicate_exam]
        response = self.client.post(self.url + "class-review/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("NSA-002", str(response.data))
        self.assertFalse(ReportCardVersion.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.reviewed").exists())
