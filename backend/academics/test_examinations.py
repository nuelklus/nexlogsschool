from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from core.models import AuditEvent, Membership, StudentGuardian
from .models import (
    Assessment, AssessmentResult, AttendanceRecord, Examination, ExaminationPublication, ExaminationSession,
    GradingConfiguration, ReportCardVersion, SchoolClass, StudentEnrollment, Subject, TeachingAssignment,
)
from .tests import AcademicFixture


class ExaminationTests(AcademicFixture, TestCase):
    require_fixture_campus = True
    def setUp(self):
        super().setUp()
        self.head = self.make_member(self.tenant, "head@northstar.example", Membership.Role.HEADTEACHER)
        self.parent = self.make_member(self.tenant, "parent@northstar.example", Membership.Role.PARENT)
        self.other_parent = self.make_member(self.tenant, "otherparent@northstar.example", Membership.Role.PARENT)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, user=self.parent)
        self.config = GradingConfiguration.objects.create(
            tenant=self.tenant, name="Test policy", examination_type="end of term",
            configuration={
                "components": [
                    {"key": "work", "maximum": "20.00", "weight": "40.00"},
                    {"key": "exam", "maximum": "100.00", "weight": "60.00"},
                ],
                "bands": [
                    {"minimum": "0.00", "grade": "F", "description": "Fail", "passed": False},
                    {"minimum": "50.00", "grade": "P", "description": "Pass", "passed": True},
                ],
                "decimal_places": 2, "rounding": "half_up", "allow_decimal_scores": True,
                "exclude_not_applicable": False,
            },
        )
        self.url = self.base + "examinations/"
        self.payload = {
            "name": "Term exam", "term": str(self.term.pk), "school_class": str(self.school_class.pk),
            "subject": str(self.subject.pk), "configuration": str(self.config.pk),
            "start_date": "2026-03-10", "end_date": "2026-03-11",
            "marks_deadline": "2026-03-15", "publication_date": "2026-03-16",
        }

    def create_exam(self):
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def entries(self, status="scored"):
        return {"entries": [
            {"enrollment": self.enrollment["id"], "component": "work", "status": status,
             "score": "15" if status == "scored" else None, "remarks": ""},
            {"enrollment": self.enrollment["id"], "component": "exam", "status": "scored", "score": "80", "remarks": "Good"},
        ]}

    def test_class_subject_includes_students_with_old_restrictions_for_mark_entry(self):
        science = Subject.objects.create(tenant=self.tenant, name="Science", code="SCI")
        TeachingAssignment.objects.create(
            tenant=self.tenant, school_class=self.school_class, subject=science, teacher=self.teacher
        )
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        enrollment.subjects.set([self.subject])
        response = self.client.post(self.url, {**self.payload, "subject": str(science.pk)}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        path = self.url + response.data["id"] + "/"
        self.client.force_authenticate(user=self.teacher)
        detail = self.client.get(path)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual([row["enrollment"] for row in detail.data["students"]], [str(enrollment.pk)])
        saved = self.client.post(path, self.entries(), format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(AssessmentResult.objects.filter(enrollment=enrollment, assessment__examination_id=response.data["id"]).count(), 2)

    def test_grouped_exams_have_independent_schedules_and_teacher_scope(self):
        other_teacher = self.make_member(self.tenant, "groupteacher@northstar.example", Membership.Role.TEACHER)
        other_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Grade 2")
        other_subject = Subject.objects.create(tenant=self.tenant, name="English", code="ENG")
        TeachingAssignment.objects.create(
            tenant=self.tenant, school_class=other_class, subject=other_subject, teacher=other_teacher,
        )
        other_config = GradingConfiguration.objects.create(
            tenant=self.tenant, name="Other policy", examination_type="end of term",
            configuration=deepcopy(self.config.configuration),
        )
        first = {key: value for key, value in self.payload.items() if key not in ("name", "term")}
        second = {**first, "school_class": str(other_class.pk), "subject": str(other_subject.pk),
                  "configuration": str(other_config.pk), "start_date": "2026-03-20", "end_date": "2026-03-21",
                  "marks_deadline": "2026-03-22", "publication_date": "2026-03-23"}
        session_url = self.base + "examination-sessions/"
        response = self.client.post(session_url, {
            "name": "Term exams", "term": str(self.term.pk), "entries": [first, second],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["entries"]), 2)
        exam1, exam2 = response.data["entries"]
        self.assertEqual(exam2["configuration"], str(other_config.pk))
        self.assertEqual(exam2["start_date"], "2026-03-20")
        self.assertEqual(Assessment.objects.filter(examination__session_id=response.data["id"]).count(), 4)
        Examination.objects.filter(pk=exam2["id"]).update(status=Examination.Status.SUBMITTED)
        manager_view = self.client.get(session_url).data[0]
        self.assertEqual(manager_view["counts"]["draft"], 1)
        self.assertEqual(manager_view["counts"]["submitted"], 1)
        self.client.force_authenticate(user=self.teacher)
        teacher_view = self.client.get(session_url).data[0]
        self.assertEqual([entry["id"] for entry in teacher_view["entries"]], [exam1["id"]])
        self.assertEqual(teacher_view["counts"]["submitted"], 0)
        self.assertEqual(self.client.get(self.url + exam2["id"] + "/").status_code, 404)
        self.assertEqual(self.client.post(session_url, {}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.get(session_url).status_code, 404)

    def test_grouped_creation_rejects_duplicates_and_rolls_back_invalid_entries(self):
        session_url = self.base + "examination-sessions/"
        entry = {key: value for key, value in self.payload.items() if key not in ("name", "term")}
        payload = {"name": "Term exams", "term": str(self.term.pk), "entries": [entry, entry]}
        self.assertEqual(self.client.post(session_url, payload, format="json").status_code, 400)
        foreign_subject = Subject.objects.create(tenant=self.other_tenant, name="Foreign", code="FOR")
        payload["entries"] = [entry, {**entry, "subject": str(foreign_subject.pk)}]
        response = self.client.post(session_url, payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ExaminationSession.objects.exists())
        self.assertFalse(Examination.objects.exists())
        self.assertFalse(Assessment.objects.filter(examination__isnull=False).exists())
        self.assertFalse(AuditEvent.objects.filter(action="academic.examination.created").exists())

    def test_complete_workflow_and_legacy_bypass(self):
        exam_id = self.create_exam()
        detail = self.url + exam_id + "/"
        component = Assessment.objects.filter(examination_id=exam_id).first()
        self.assertEqual(len(self.client.get(self.base + "assessments/").data), 1)
        attempt = self.client.post(self.base + "results/", {
            "assessment": str(component.pk), "enrollment": self.enrollment["id"], "score": "10",
        }, format="json")
        self.assertEqual(attempt.status_code, 400)
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(self.client.get(self.base + "published-results/").data, [])
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(len(self.client.get(self.url).data), 1)
        self.assertEqual(self.client.post(detail, self.entries("absent"), format="json").status_code, 200)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(detail, self.entries(), format="json").status_code, 200)
        self.assertEqual(self.client.get(detail).data["calculations"][0]["score"], "78.00")
        self.assertEqual(self.client.post(detail + "approve/", {}, format="json").status_code, 404)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 10)):
            self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 400)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(detail, self.entries(), format="json").status_code, 400)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.post(detail + "publish/", {}, format="json").status_code, 400)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(detail + "approve/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.owner)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 15)):
            self.assertEqual(self.client.post(detail + "publish/", {}, format="json").status_code, 400)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "publish/", {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(detail + "publish/", {}, format="json").status_code, 400)
        self.assertEqual(ExaminationPublication.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="academic.examination.mark_saved").count(), 4)
        self.client.force_authenticate(user=self.parent)
        results = self.client.get(self.base + "published-results/")
        self.assertEqual(results.data[0]["result"]["score"], "78.00")
        self.assertEqual(results.data[0]["result"]["grade"], "P")
        self.client.force_authenticate(user=self.other_parent)
        self.assertEqual(self.client.get(self.base + "published-results/").data, [])
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(detail + "correct/", {"reason": "Correction"}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.post(detail + "correct/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(detail + "correct/", {"reason": "Correct entry"}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(self.client.get(self.base + "published-results/").data[0]["result"]["score"], "78.00")
        self.client.force_authenticate(user=self.teacher)
        corrected = self.entries()
        corrected["entries"][1]["score"] = "90"
        self.assertEqual(self.client.post(detail, corrected, format="json").status_code, 200)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(detail + "approve/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.owner)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "publish/", {}, format="json").status_code, 200)
        history = self.client.get(detail).data["publication_history"]
        self.assertEqual([(row["version"], row["result"]["score"]) for row in history], [(2, "84.00"), (1, "78.00")])
        self.client.force_authenticate(user=self.parent)
        published = self.client.get(self.base + "published-results/").data
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["result"]["score"], "84.00")
        student_user = self.make_member(self.tenant, "student@northstar.example", Membership.Role.STUDENT)
        self.student.user = student_user
        self.student.save(update_fields=("user",))
        self.client.force_authenticate(user=student_user)
        self.assertEqual(len(self.client.get(self.base + "published-results/").data), 1)

    def test_atomic_validation_and_scoped_access(self):
        foreign = GradingConfiguration.objects.create(
            tenant=self.other_tenant, name="Other", examination_type="other", configuration=self.config.configuration
        )
        payload = {**self.payload, "configuration": str(foreign.pk)}
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 400)
        exam_id = self.create_exam()
        detail = self.url + exam_id + "/"
        unassigned = self.make_member(self.tenant, "unassigned@northstar.example", Membership.Role.TEACHER)
        self.client.force_authenticate(user=unassigned)
        self.assertEqual(self.client.get(self.url).data, [])
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.client.force_authenticate(user=self.teacher)
        invalid = deepcopy(self.entries())
        invalid["entries"][1]["score"] = "101"
        self.assertEqual(self.client.post(detail, invalid, format="json").status_code, 400)
        self.assertFalse(AssessmentResult.objects.filter(assessment__examination_id=exam_id).exists())
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_changed_roster_or_marks_requires_return_before_approval(self):
        exam_id = self.create_exam()
        detail = self.url + exam_id + "/"
        self.client.force_authenticate(user=self.teacher)
        self.client.post(detail, self.entries(), format="json")
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 200)
        mark = AssessmentResult.objects.filter(assessment__examination_id=exam_id).first()
        mark.remarks = "Changed outside workflow"
        mark.save(update_fields=("remarks",))
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(detail + "approve/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(detail + "return/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(detail + "return/", {"reason": "Correct marks"}, format="json").status_code, 200)
        self.assertEqual(Examination.objects.get(pk=exam_id).status, "draft")

    def test_closed_term_blocks_draft_changes_and_actions(self):
        exam_id = self.create_exam()
        self.term.is_completed = True
        self.term.save(update_fields=("is_completed",))
        self.client.force_authenticate(user=self.teacher)
        detail = self.url + exam_id + "/"
        self.assertEqual(self.client.post(detail, self.entries(), format="json").status_code, 400)
        self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 400)
        self.assertFalse(AssessmentResult.objects.filter(assessment__examination_id=exam_id).exists())

    def test_audit_failure_rolls_back_marks(self):
        exam_id = self.create_exam()
        self.client.force_authenticate(user=self.teacher)
        with patch("academics.examinations._audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.client.post(self.url + exam_id + "/", self.entries(), format="json")
        self.assertFalse(AssessmentResult.objects.filter(assessment__examination_id=exam_id).exists())

    def test_staff_report_preview_uses_only_published_snapshots_and_is_scoped(self):
        exam_id = self.create_exam()
        detail = self.url + exam_id + "/"
        preview_url = self.base + "report-card-preview/"
        payload = {"term": str(self.term.pk), "enrollment": self.enrollment["id"], "examinations": [exam_id]}
        self.assertEqual(self.client.post(preview_url, payload, format="json").status_code, 400)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.post(preview_url, payload, format="json").status_code, 404)
        self.assertEqual(self.client.post(detail, self.entries(), format="json").status_code, 200)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(detail + "approve/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.owner)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "publish/", {}, format="json").status_code, 200)
        AttendanceRecord.objects.create(
            tenant=self.tenant, enrollment_id=self.enrollment["id"], date=date(2026, 3, 3), status="absent",
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant, enrollment_id=self.enrollment["id"], date=date(2026, 1, 1), status="present",
        )
        response = self.client.post(preview_url, payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["attendance"]["absent"], 1)
        self.assertEqual(response.data["attendance"]["present"], 0)
        self.assertEqual(response.data["results"][0]["result"]["score"], "78.00")
        self.assertEqual(response.data["results"][0]["version"], 1)
        pdf = self.client.post(preview_url, {**payload, "format": "pdf"}, format="json")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertEqual(self.client.post(preview_url, {**payload, "examinations": [exam_id, exam_id]}, format="json").status_code, 400)
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(self.client.post(preview_url, payload, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.post(preview_url, payload, format="json").status_code, 404)

    def test_report_rejects_publication_outside_enrollment_dates(self):
        exam_id = self.create_exam()
        exam = Examination.objects.get(pk=exam_id)
        exam.published_version = 1
        exam.save(update_fields=("published_version",))
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=enrollment, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "0.00",
                      "grade": "F", "description": "Fail"},
        )
        self.client.force_authenticate(user=self.head)
        payload = {"term": str(self.term.pk), "enrollment": str(enrollment.pk), "examinations": [exam_id],
                   "class_teacher_remark": "Good work", "headteacher_remark": "Keep it up"}
        preview = self.base + "report-card-preview/"
        reports = self.base + "report-cards/"
        self.assertEqual(self.client.post(preview, payload, format="json").status_code, 200)

        enrollment.enrolled_on = date(2026, 3, 11)
        enrollment.save(update_fields=("enrolled_on",))
        response = self.client.post(preview, payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.client.post(reports, payload, format="json").status_code, 400)
        enrollment.enrolled_on = date(2026, 1, 5)
        enrollment.ended_on = date(2026, 3, 9)
        enrollment.status = StudentEnrollment.Status.TRANSFERRED
        enrollment.save(update_fields=("enrolled_on", "ended_on", "status"))
        self.assertEqual(self.client.post(preview, payload, format="json").status_code, 400)
        self.assertEqual(self.client.post(reports, payload, format="json").status_code, 400)
        self.assertFalse(ReportCardVersion.objects.exists())

    def test_report_publication_rechecks_exam_enrollment_date(self):
        exam_id = self.create_exam()
        exam = Examination.objects.get(pk=exam_id)
        exam.published_version = 1
        exam.save(update_fields=("published_version",))
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=enrollment, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "0.00",
                      "grade": "F", "description": "Fail"},
        )
        self.client.force_authenticate(user=self.head)
        reports = self.base + "report-cards/"
        payload = {"term": str(self.term.pk), "enrollment": str(enrollment.pk), "examinations": [exam_id],
                   "class_teacher_remark": "Good work", "headteacher_remark": "Keep it up"}
        reviewed = self.client.post(reports, payload, format="json")
        self.assertEqual(reviewed.status_code, 201, reviewed.data)
        self.assertEqual(reviewed.data["snapshot"]["overall_average"], "0.00")
        enrollment.ended_on = date(2026, 3, 9)
        enrollment.status = StudentEnrollment.Status.TRANSFERRED
        enrollment.save(update_fields=("ended_on", "status"))
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(reports + reviewed.data["id"] + "/publish/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(ReportCardVersion.objects.get(pk=reviewed.data["id"]).status, "reviewed")

    def test_report_publication_rechecks_transfer_after_review(self):
        exam_id = self.create_exam()
        exam = Examination.objects.get(pk=exam_id)
        exam.published_version = 1
        exam.save(update_fields=("published_version",))
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=enrollment, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "78.00",
                      "grade": "P", "description": "Pass"},
        )
        self.client.force_authenticate(user=self.head)
        reports = self.base + "report-cards/"
        payload = {"term": str(self.term.pk), "enrollment": str(enrollment.pk), "examinations": [exam_id],
                   "class_teacher_remark": "Good work", "headteacher_remark": "Keep it up"}
        reviewed = self.client.post(reports, payload, format="json")
        self.assertEqual(reviewed.status_code, 201, reviewed.data)
        enrollment.ended_on = date(2026, 3, 19)
        enrollment.status = StudentEnrollment.Status.TRANSFERRED
        enrollment.save(update_fields=("ended_on", "status"))
        other_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Grade 2")
        StudentEnrollment.objects.create(tenant=self.tenant, student=self.student, school_class=other_class,
                                         enrolled_on=date(2026, 3, 20))
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(reports + reviewed.data["id"] + "/publish/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("reconciliation", str(response.data))
        self.assertEqual(ReportCardVersion.objects.get(pk=reviewed.data["id"]).status, "reviewed")
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.published").exists())

    def test_report_versions_review_publication_and_portal_scope(self):
        exam_id = self.create_exam()
        detail = self.url + exam_id + "/"
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.post(detail, self.entries(), format="json").status_code, 200)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "submit/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(detail + "approve/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=self.owner)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.assertEqual(self.client.post(detail + "publish/", {}, format="json").status_code, 200)
        reports = self.base + "report-cards/"
        payload = {"term": str(self.term.pk), "enrollment": self.enrollment["id"], "examinations": [exam_id],
                   "class_teacher_remark": "Good work", "headteacher_remark": "Keep it up"}
        self.assertEqual(self.client.post(reports, payload, format="json").status_code, 404)
        self.client.force_authenticate(user=self.head)
        response = self.client.post(reports, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        report_id = response.data["id"]
        self.assertEqual(response.data["snapshot"]["overall_average"], "78.00")
        self.assertEqual(self.client.post(reports, payload, format="json").status_code, 400)
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(self.client.get(reports).data, [])
        self.assertEqual(self.client.get(reports + report_id + "/pdf/").status_code, 404)
        self.client.force_authenticate(user=self.owner)
        new_subject = Subject.objects.create(tenant=self.tenant, name="Science", code="SCI")
        new_assignment = TeachingAssignment.objects.create(
            tenant=self.tenant, school_class=self.school_class, subject=new_subject, teacher=self.teacher
        )
        stale = self.client.post(reports + report_id + "/publish/", {}, format="json")
        self.assertEqual(stale.status_code, 400, stale.data)
        self.assertIn("Class subjects have changed", str(stale.data))
        new_assignment.delete()
        self.assertEqual(self.client.post(reports + report_id + "/publish/", {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(reports + report_id + "/publish/", {}, format="json").status_code, 400)
        self.client.force_authenticate(user=self.parent)
        portal = self.client.get(reports)
        self.assertEqual(portal.data[0]["version"], 1)
        self.assertEqual(portal["Cache-Control"], "private, no-store")
        self.assertTrue(self.client.get(reports + report_id + "/pdf/").content.startswith(b"%PDF-"))
        self.client.force_authenticate(user=self.other_parent)
        self.assertEqual(self.client.get(reports).data, [])
        self.assertEqual(self.client.get(reports + report_id + "/pdf/").status_code, 404)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(reports, payload, format="json").status_code, 400)
        revision = self.client.post(reports, {**payload, "reason": "Correct remarks",
                                             "headteacher_remark": "Revised"}, format="json")
        self.assertEqual(revision.status_code, 201, revision.data)
        self.assertEqual(revision.data["version"], 2)
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(len(self.client.get(reports).data), 1)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.post(reports + revision.data["id"] + "/publish/", {}, format="json").status_code, 200)
        self.assertEqual(ReportCardVersion.objects.get(pk=report_id).snapshot["headteacher_remark"], "Keep it up")
        self.assertEqual(AuditEvent.objects.filter(action="academic.report_card.published").count(), 2)
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(len(self.client.get(reports).data), 2)
        self.assertNotIn("reason", self.client.get(reports).data[0])
        student_user = self.make_member(self.tenant, "reportstudent@northstar.example", Membership.Role.STUDENT)
        self.student.user = student_user
        self.student.save(update_fields=("user",))
        self.client.force_authenticate(user=student_user)
        self.assertEqual(len(self.client.get(reports).data), 2)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.get(reports).status_code, 404)

    def test_report_review_rejects_stale_results_and_can_be_withdrawn(self):
        exam_id = self.create_exam()
        detail = self.url + exam_id + "/"
        self.client.force_authenticate(user=self.teacher)
        self.client.post(detail, self.entries(), format="json")
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.client.post(detail + "submit/", {}, format="json")
        self.client.force_authenticate(user=self.head)
        self.client.post(detail + "approve/", {}, format="json")
        self.client.force_authenticate(user=self.owner)
        with patch("academics.examinations.timezone.localdate", return_value=date(2026, 3, 16)):
            self.client.post(detail + "publish/", {}, format="json")
        reports = self.base + "report-cards/"
        payload = {"term": str(self.term.pk), "enrollment": self.enrollment["id"], "examinations": [exam_id],
                   "class_teacher_remark": "Good work", "headteacher_remark": "Keep it up"}
        self.client.force_authenticate(user=self.head)
        response = self.client.post(reports, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        report_id = response.data["id"]
        Examination.objects.filter(pk=exam_id).update(published_version=2)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.post(reports + report_id + "/publish/", {}, format="json").status_code, 400)
        self.client.force_authenticate(user=self.head)
        self.assertEqual(self.client.post(reports + report_id + "/withdraw/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(reports + report_id + "/withdraw/", {"reason": "Results revised"}, format="json").status_code, 200)
        self.assertEqual(ReportCardVersion.objects.get(pk=report_id).status, "withdrawn")
        self.assertTrue(AuditEvent.objects.filter(action="academic.report_card.withdrawn").exists())

    def test_report_review_requires_complete_subjects_and_rejects_multiple_enrollments(self):
        exam_id = self.create_exam()
        exam = Examination.objects.get(pk=exam_id)
        exam.published_version = 1
        exam.save(update_fields=("published_version",))
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=enrollment, version=1, published_by=self.owner,
            snapshot={"subject": "Mathematics", "examination": "Term exam", "score": "78.00",
                      "grade": "P", "description": "Pass"},
        )
        reports = self.base + "report-cards/"
        payload = {"term": str(self.term.pk), "enrollment": self.enrollment["id"], "examinations": [exam_id],
                   "class_teacher_remark": "Good work", "headteacher_remark": "Keep it up"}
        self.client.force_authenticate(user=self.head)
        another_subject = Subject.objects.create(tenant=self.tenant, name="Science", code="SCI")
        assignment = TeachingAssignment.objects.create(
            tenant=self.tenant, school_class=self.school_class, subject=another_subject, teacher=self.teacher
        )
        self.assertEqual(self.client.post(reports, payload, format="json").status_code, 400)
        assignment.delete()
        another_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Grade 2")
        overlap = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=another_class, enrolled_on=date(2026, 2, 1),
        )
        self.assertEqual(self.client.post(reports, payload, format="json").status_code, 400)
        overlap.enrolled_on = date(2025, 12, 31)
        overlap.ended_on = date(2025, 12, 31)
        overlap.save(update_fields=("enrolled_on", "ended_on"))
        with patch("academics.report_cards._audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.client.post(reports, payload, format="json")
        self.assertFalse(ReportCardVersion.objects.exists())
