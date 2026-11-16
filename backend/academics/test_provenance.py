from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.db.models.deletion import ProtectedError

from core.models import AcademicYear, AuditEvent, Campus, Membership, StudentGuardian
from .models import (
    AssessmentResult, EnrollmentProvenanceAttestation, EnrollmentProvenanceRevocation, Examination, ExaminationPublication,
    GradingConfiguration, ReportCardVersion, SchoolClass, StudentEnrollment, Subject,
)
from .tests import AcademicFixture


class EnrollmentProvenanceTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.source = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        self.source.status = StudentEnrollment.Status.TRANSFERRED
        self.source.ended_on = date(2026, 3, 19)
        self.source.save(update_fields=("status", "ended_on"))
        self.destination_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Grade 2")
        self.destination = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=self.destination_class, enrolled_on=date(2026, 3, 20),
        )
        self.campus = Campus.objects.create(tenant=self.tenant, name="North", code="N")
        self.url = self.base + "enrollment-provenance/"

    def report(self, school_class=None):
        return self.client.post(self.url + "report/", {
            "term": str(self.term.pk), "school_class": str((school_class or self.school_class).pk),
        }, format="json")

    def payload(self, row):
        return {
            "term": str(self.term.pk), "enrollment": row["enrollment"], "fingerprint": row["fingerprint"],
            "previous_attestation": row["attestation"]["id"] if row["attestation"] else None,
            "segment_start": row["segment_start"], "segment_end": row["segment_end"],
            "campus": str(self.campus.pk), "no_campus": False, "subjects": [str(self.subject.pk)],
            "reason": "Reconcile transfer", "evidence": "Register page 12",
        }

    def test_attestation_preserves_original_and_creates_audited_versions(self):
        row = self.report().data["students"][0]
        self.assertIn("campus_requires_attestation", row["issues"])
        self.assertEqual(row["blocking"], [])
        self.assertIsNone(row["attestation"])
        payload = self.payload(row)
        created = self.client.post(self.url + "attest/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        record = EnrollmentProvenanceAttestation.objects.get(pk=created.data["id"])
        self.assertIsNone(record.original["campus"])
        self.assertEqual(record.subject_ids, [str(self.subject.pk)])
        self.assertEqual(record.attested_by, self.owner)
        self.assertEqual(record.evidence, "Register page 12")
        self.source.refresh_from_db()
        self.assertIsNone(self.source.campus_id)
        self.assertEqual(self.source.status, StudentEnrollment.Status.TRANSFERRED)
        self.assertEqual(AuditEvent.objects.filter(action="academic.enrollment.provenance_attested").count(), 1)
        self.assertEqual(self.client.post(self.url + "attest/", payload, format="json").status_code, 400)
        revised = self.client.post(self.url + "attest/", {
            **payload, "campus": None, "no_campus": True, "previous_attestation": created.data["id"],
        }, format="json")
        self.assertEqual(revised.status_code, 201, revised.data)
        self.assertEqual(revised.data["version"], 2)
        self.assertEqual(EnrollmentProvenanceAttestation.objects.count(), 2)
        refreshed = self.report().data["students"][0]
        self.assertTrue(refreshed["attestation"]["current"])
        self.assertEqual([item["version"] for item in refreshed["attestation_history"]], [2, 1])
        self.assertEqual(refreshed["attestation_history"][1]["original"]["campus"], None)
        self.assertFalse(ReportCardVersion.objects.exists())

    def test_stale_overlap_gap_and_missing_end_fail_closed(self):
        row = self.report().data["students"][0]
        payload = self.payload(row)
        self.destination.enrolled_on = date(2026, 3, 19)
        self.destination.save(update_fields=("enrolled_on",))
        self.assertEqual(self.client.post(self.url + "attest/", payload, format="json").status_code, 400)
        row = self.report().data["students"][0]
        self.assertIn("overlapping_enrollments", row["blocking"])
        self.assertEqual(self.client.post(self.url + "attest/", self.payload(row), format="json").status_code, 400)
        self.destination.enrolled_on = date(2026, 3, 22)
        self.destination.save(update_fields=("enrolled_on",))
        self.assertIn("gap_after_transfer", self.report().data["students"][0]["blocking"])
        self.source.ended_on = None
        self.source.save(update_fields=("ended_on",))
        self.assertIn("missing_end_date", self.report().data["students"][0]["blocking"])
        self.assertFalse(EnrollmentProvenanceAttestation.objects.exists())

    def test_subject_campus_and_permissions_are_tenant_scoped(self):
        row = self.report().data["students"][0]
        payload = self.payload(row)
        foreign_campus = Campus.objects.create(tenant=self.other_tenant, name="Other", code="O")
        foreign_subject = Subject.objects.create(tenant=self.other_tenant, name="Other", code="O")
        self.assertEqual(self.client.post(self.url + "attest/", {**payload, "campus": str(foreign_campus.pk)}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url + "attest/", {**payload, "subjects": [str(foreign_subject.pk)]}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url + "attest/", {**payload, "campus": None}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url + "attest/", {**payload, "evidence": "  "}, format="json").status_code, 400)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.report().status_code, 404)
        self.assertEqual(self.client.post(self.url + "attest/", payload, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.report().status_code, 404)
        self.assertFalse(EnrollmentProvenanceAttestation.objects.exists())

    def test_conflicting_mark_and_publication_are_flagged_without_modification(self):
        config = GradingConfiguration.objects.create(tenant=self.tenant, name="Policy", examination_type="term", configuration={})
        exam = Examination.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.destination_class, subject=self.subject,
            configuration=config, name="Exam", start_date=date(2026, 3, 22), end_date=date(2026, 3, 23),
            marks_deadline=date(2026, 3, 24), publication_date=date(2026, 3, 25), published_version=1,
        )
        publication = ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=self.source, version=1, snapshot={"score": "0.00"},
        )
        mark = AssessmentResult.objects.create(
            tenant=self.tenant, enrollment=self.source, assessment_id=self.assessment["id"],
            mark_status=AssessmentResult.Status.ABSENT, score=None,
        )
        row = self.report().data["students"][0]
        self.assertIn("conflicting_academic_record", row["blocking"])
        self.assertEqual(self.client.post(self.url + "attest/", self.payload(row), format="json").status_code, 400)
        publication.refresh_from_db()
        mark.refresh_from_db()
        self.assertEqual(publication.snapshot["score"], "0.00")
        self.assertEqual(mark.mark_status, "absent")
        self.assertIsNone(mark.score)

    def test_attestation_does_not_unblock_transfer_report_or_partial_batch(self):
        row = self.report().data["students"][0]
        self.assertEqual(self.client.post(self.url + "attest/", self.payload(row), format="json").status_code, 201)
        head = self.make_member(self.tenant, "provenance-head@northstar.example", Membership.Role.HEADTEACHER)
        config = GradingConfiguration.objects.create(tenant=self.tenant, name="Policy", examination_type="term", configuration={})
        exam = Examination.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            configuration=config, name="Exam", start_date=date(2026, 3, 10), end_date=date(2026, 3, 11),
            marks_deadline=date(2026, 3, 12), publication_date=date(2026, 3, 13), published_version=1,
        )
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=self.source, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "78.00", "grade": "P", "description": "Pass"},
        )
        self.client.force_authenticate(user=head)
        entry = {"enrollment": str(self.source.pk), "examinations": [str(exam.pk)],
                 "class_teacher_remark": "Good", "headteacher_remark": "Good"}
        response = self.client.post(self.base + "report-cards/", {**entry, "term": str(self.term.pk)}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.base + "report-cards/class-review/", {
            "term": str(self.term.pk), "school_class": str(self.school_class.pk),
            "roster_date": "2026-03-10", "entries": [entry],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ReportCardVersion.objects.exists())

    def test_cross_campus_segments_are_attested_separately_and_status_becomes_stale(self):
        destination_campus = Campus.objects.create(tenant=self.tenant, name="South", code="S")
        self.destination.campus = destination_campus
        self.destination.save(update_fields=("campus",))
        first = self.report().data["students"][0]
        second = self.report(self.destination_class).data["students"][0]
        self.assertEqual(self.client.post(self.url + "attest/", self.payload(first), format="json").status_code, 201)
        destination_payload = {**self.payload(second), "campus": str(destination_campus.pk)}
        self.assertEqual(self.client.post(self.url + "attest/", destination_payload, format="json").status_code, 201)
        self.assertEqual(EnrollmentProvenanceAttestation.objects.count(), 2)
        self.assertEqual(EnrollmentProvenanceAttestation.objects.get(enrollment=self.source).campus, self.campus)
        self.assertEqual(EnrollmentProvenanceAttestation.objects.get(enrollment=self.destination).campus, destination_campus)
        other = Subject.objects.create(tenant=self.tenant, name="English", code="ENG")
        self.destination.subjects.add(other)
        self.assertFalse(self.report(self.destination_class).data["students"][0]["attestation"]["current"])
        self.assertEqual(self.client.post(self.url + "attest/", destination_payload, format="json").status_code, 400)

    def test_historical_other_year_is_not_combined(self):
        year = AcademicYear.objects.create(
            tenant=self.tenant, name="2027", start_date=date(2027, 1, 1), end_date=date(2027, 12, 31),
        )
        school_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=year, name="Next")
        StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=school_class, enrolled_on=date(2027, 1, 1),
        )
        row = self.report().data["students"][0]
        self.assertEqual(len(row["original"]["history"]), 2)
        self.assertEqual(row["blocking"], [])
        self.assertEqual(self.client.post(self.url + "attest/", self.payload(row), format="json").status_code, 201)

    def test_attestation_rejects_excluding_recorded_subject_without_changing_marks(self):
        other = Subject.objects.create(tenant=self.tenant, name="English", code="ENG")
        mark = AssessmentResult.objects.create(
            tenant=self.tenant, enrollment=self.source, assessment_id=self.assessment["id"],
            mark_status=AssessmentResult.Status.EXEMPT, score=None,
        )
        row = self.report().data["students"][0]
        response = self.client.post(self.url + "attest/", {**self.payload(row), "subjects": [str(other.pk)]}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("conflict", str(response.data))
        mark.refresh_from_db()
        self.assertEqual(mark.mark_status, "exempt")
        self.assertIsNone(mark.score)
        self.assertFalse(EnrollmentProvenanceAttestation.objects.exists())

    def test_open_segment_is_not_attested_as_complete(self):
        with patch("academics.provenance.timezone.localdate", return_value=date(2026, 3, 1)):
            row = self.report().data["students"][0]
            self.assertIn("segment_not_ended", row["blocking"])
            self.assertEqual(self.client.post(self.url + "attest/", self.payload(row), format="json").status_code, 400)
        self.assertFalse(EnrollmentProvenanceAttestation.objects.exists())

    def test_audit_failure_rolls_back_attestation(self):
        row = self.report().data["students"][0]
        with patch("academics.provenance._audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.client.post(self.url + "attest/", self.payload(row), format="json")
        self.assertFalse(EnrollmentProvenanceAttestation.objects.exists())

    def test_report_provenance_reference_is_protected_and_visible_only_to_staff(self):
        row = self.report().data["students"][0]
        created = self.client.post(self.url + "attest/", self.payload(row), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        attestation = EnrollmentProvenanceAttestation.objects.get(pk=created.data["id"])
        report = ReportCardVersion.objects.create(
            tenant=self.tenant, term=self.term, enrollment=self.source, student=self.student,
            version=1, provenance_attestation=attestation, snapshot={},
        )
        self.assertEqual(report.provenance_attestation_id, attestation.pk)
        response = self.client.get(self.base + "report-cards/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["provenance_attestation"], str(attestation.pk))
        report.status = ReportCardVersion.Status.PUBLISHED
        report.save(update_fields=("status",))
        parent = self.make_member(self.tenant, "provenance-parent@northstar.example", Membership.Role.PARENT)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, user=parent)
        self.client.force_authenticate(user=parent)
        portal = self.client.get(self.base + "report-cards/")
        self.assertEqual(portal.status_code, 200)
        self.assertNotIn("provenance_attestation", portal.data[0])
        with self.assertRaises(ProtectedError):
            attestation.delete()

    def test_revocation_is_authorized_audited_and_preserves_published_report(self):
        row = self.report().data["students"][0]
        payload = self.payload(row)
        created = self.client.post(self.url + "attest/", payload, format="json")
        attestation = EnrollmentProvenanceAttestation.objects.get(pk=created.data["id"])
        report = ReportCardVersion.objects.create(
            tenant=self.tenant, term=self.term, enrollment=self.source, student=self.student,
            version=1, provenance_attestation=attestation, status=ReportCardVersion.Status.PUBLISHED,
            snapshot={"school": "North Star", "student": "Ama Mensah", "admission_number": "A1",
                      "class": "Grade 1", "section": "", "year": "2026", "term": "Term 1", "campus": None,
                      "attendance": {}, "results": [], "overall_average": "78.00",
                      "overall_method": "Equal weight", "class_teacher_remark": "Good", "headteacher_remark": "Good"},
        )
        original_snapshot = report.snapshot
        parent = self.make_member(self.tenant, "revocation-parent@northstar.example", Membership.Role.PARENT)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, user=parent)
        self.client.force_authenticate(user=parent)
        original_pdf = self.client.get(self.base + f"report-cards/{report.pk}/pdf/")
        self.assertEqual(original_pdf.status_code, 200)
        self.assertEqual(self.client.get(self.base + "report-cards/").data[0]["snapshot"], original_snapshot)
        revoke_url = self.url + f"{attestation.pk}/revoke/"
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.post(revoke_url, {"reason": "Incorrect register"}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.post(revoke_url, {"reason": "Incorrect register"}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.post(revoke_url, {"reason": "  "}, format="json").status_code, 400)
        response = self.client.post(revoke_url, {"reason": "Incorrect register"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.post(revoke_url, {"reason": "Again"}, format="json").status_code, 400)
        revocation = EnrollmentProvenanceRevocation.objects.get(attestation=attestation)
        self.assertEqual(revocation.revoked_by, self.owner)
        self.assertEqual(revocation.reason, "Incorrect register")
        self.assertEqual(AuditEvent.objects.filter(action="academic.enrollment.provenance_revoked").count(), 1)
        report.refresh_from_db()
        self.assertEqual(report.status, ReportCardVersion.Status.PUBLISHED)
        self.assertEqual(report.snapshot, original_snapshot)
        self.client.force_authenticate(user=parent)
        retrieved = self.client.get(self.base + f"report-cards/{report.pk}/pdf/")
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(self.client.get(self.base + "report-cards/").data[0]["snapshot"], original_snapshot)
        self.client.force_authenticate(user=self.owner)
        refreshed = self.report().data["students"][0]
        self.assertFalse(refreshed["attestation"]["current"])
        self.assertEqual(refreshed["attestation_history"][0]["revocation"]["reason"], "Incorrect register")
        replacement = self.client.post(self.url + "attest/", {
            **payload, "previous_attestation": str(attestation.pk),
            "reason": "Reverified", "evidence": "Register page 13",
        }, format="json")
        self.assertEqual(replacement.status_code, 201, replacement.data)
        self.assertEqual(replacement.data["version"], 2)
        with self.assertRaises(ProtectedError):
            attestation.delete()

    def test_revocation_audit_failure_rolls_back(self):
        row = self.report().data["students"][0]
        attestation = self.client.post(self.url + "attest/", self.payload(row), format="json").data["id"]
        with patch("academics.provenance._audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.client.post(self.url + f"{attestation}/revoke/", {"reason": "Correction"}, format="json")
        self.assertFalse(EnrollmentProvenanceRevocation.objects.exists())

    def test_linked_review_cannot_publish_without_transactional_validation(self):
        row = self.report().data["students"][0]
        attestation = self.client.post(self.url + "attest/", self.payload(row), format="json").data["id"]
        report = ReportCardVersion.objects.create(
            tenant=self.tenant, term=self.term, enrollment=self.source, student=self.student,
            version=1, provenance_attestation_id=attestation, snapshot={},
        )
        url = self.base + f"report-cards/{report.pk}/publish/"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        self.client.post(self.url + f"{attestation}/revoke/", {"reason": "Correction"}, format="json")
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        report.refresh_from_db()
        self.assertEqual(report.status, ReportCardVersion.Status.REVIEWED)
