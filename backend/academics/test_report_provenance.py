from datetime import date
from unittest.mock import patch

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from .models import (
    Assessment, AssessmentResult, AttendanceRecord, EnrollmentProvenanceAttestation, Examination, ExaminationPublication,
    ExaminationSession, GradingConfiguration, SchoolClass, StudentEnrollment, Subject,
)
from .report_provenance import inspect_segment
from .tests import AcademicFixture


class ReportProvenanceInspectionTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        clock = patch("academics.provenance.timezone.localdate", return_value=date(2026, 5, 1))
        clock.start()
        self.addCleanup(clock.stop)
        self.source = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        self.source.ended_on = date(2026, 3, 19)
        self.source.status = StudentEnrollment.Status.TRANSFERRED
        self.source.save(update_fields=("ended_on", "status"))
        other = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Grade 2")
        self.destination = StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=other, enrolled_on=date(2026, 3, 20),
        )
        self.config = GradingConfiguration.objects.create(
            tenant=self.tenant, name="Policy", examination_type="term", configuration={},
        )
        self.session = ExaminationSession.objects.create(tenant=self.tenant, term=self.term, name="Session")
        self.exam = Examination.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            configuration=self.config, session=self.session, name="Exam", start_date=date(2026, 3, 10),
            end_date=date(2026, 3, 11), marks_deadline=date(2026, 3, 12),
            publication_date=date(2026, 3, 13), published_version=1,
        )
        component = Assessment.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            name="Exam component", assessment_date=date(2026, 3, 10), max_score="100",
            examination=self.exam, component_key="exam",
        )
        self.mark = AssessmentResult.objects.create(
            tenant=self.tenant, assessment=component, enrollment=self.source, mark_status="scored", score="0",
        )
        exempt_component = Assessment.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            name="Exempt component", assessment_date=date(2026, 3, 10), max_score="100",
            examination=self.exam, component_key="work",
        )
        AssessmentResult.objects.create(
            tenant=self.tenant, assessment=exempt_component, enrollment=self.source, mark_status="exempt", score=None,
        )
        self.publication = ExaminationPublication.objects.create(
            tenant=self.tenant, examination=self.exam, enrollment=self.source, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "0.00", "grade": "F",
                      "description": "Fail", "marks": [{"component": "exam", "status": "scored", "score": "0.00", "remarks": ""},
                                                      {"component": "work", "status": "exempt", "score": None, "remarks": ""}]},
        )
        url = self.base + "enrollment-provenance/"
        row = self.client.post(url + "report/", {
            "term": str(self.term.pk), "school_class": str(self.school_class.pk),
        }, format="json").data["students"][0]
        self.attestation_payload = {
            "term": str(self.term.pk), "enrollment": str(self.source.pk), "previous_attestation": None,
            "fingerprint": row["fingerprint"], "segment_start": row["segment_start"], "segment_end": row["segment_end"],
            "campus": None, "no_campus": True, "subjects": [str(self.subject.pk)],
            "reason": "Verified", "evidence": "Register",
        }
        response = self.client.post(url + "attest/", self.attestation_payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.attestation = response.data["id"]

    def inspect(self):
        return inspect_segment(self.tenant.pk, self.term.pk, self.source.pk, self.attestation, [self.exam.pk])

    def test_valid_segment_and_mark_score_change_is_rejected(self):
        first = self.inspect()
        self.assertEqual(first.enrollment.pk, self.source.pk)
        self.assertEqual(first.publications[0].pk, self.publication.pk)
        self.assertEqual(first.publications[0].snapshot["score"], "0.00")
        self.mark.score = "42"
        self.mark.save(update_fields=("score",))
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_published_mark_remarks_must_match_source(self):
        self.mark.remarks = "Corrected"
        self.mark.save(update_fields=("remarks",))
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_published_score_must_be_a_finite_matching_decimal_string(self):
        for value in ("NaN", "broken", "42", 0):
            with self.subTest(value=value):
                self.publication.snapshot["marks"][0]["score"] = value
                self.publication.save(update_fields=("snapshot",))
                with self.assertRaises(ValidationError):
                    self.inspect()
        self.publication.snapshot["marks"][0]["score"] = "0.0"
        self.publication.save(update_fields=("snapshot",))
        self.inspect()

    def test_duplicate_component_keys_are_rejected(self):
        second = Assessment.objects.get(examination=self.exam, component_key="work")
        second.component_key = "exam"
        second.save(update_fields=("component_key",))
        self.publication.snapshot["marks"][1]["component"] = "exam"
        self.publication.save(update_fields=("snapshot",))
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_evidence_tracks_attendance_and_presentation_identity(self):
        first = self.inspect().fingerprint
        AttendanceRecord.objects.create(
            tenant=self.tenant, enrollment=self.source, date=date(2026, 3, 10), status=AttendanceRecord.Status.PRESENT,
        )
        second = self.inspect().fingerprint
        self.assertNotEqual(first, second)
        self.student.first_name = "Changed"
        self.student.save(update_fields=("first_name",))
        self.assertNotEqual(second, self.inspect().fingerprint)

    def test_evidence_tracks_examination_correction_state(self):
        first = self.inspect().fingerprint
        self.exam.version = 2
        self.exam.save(update_fields=("version",))
        self.assertNotEqual(first, self.inspect().fingerprint)

    def test_superseded_attestation_is_rejected(self):
        payload = {**self.attestation_payload, "previous_attestation": self.attestation,
                   "subjects": [str(self.subject.pk), str(Subject.objects.create(
                       tenant=self.tenant, name="English", code="ENG").pk)]}
        response = self.client.post(self.base + "enrollment-provenance/attest/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_enrollment_overlap_and_foreign_tenant_rejected(self):
        self.destination.enrolled_on = date(2026, 3, 19)
        self.destination.save(update_fields=("enrolled_on",))
        with self.assertRaises(ValidationError):
            self.inspect()
        with self.assertRaises(ValidationError):
            inspect_segment(self.other_tenant.pk, self.term.pk, self.source.pk, self.attestation, [self.exam.pk])

    def test_exam_end_date_and_session_term_are_rechecked(self):
        self.exam.end_date = date(2026, 3, 20)
        self.exam.save(update_fields=("end_date",))
        with self.assertRaises(ValidationError):
            self.inspect()
        self.exam.end_date = date(2026, 3, 11)
        self.exam.save(update_fields=("end_date",))
        self.session.tenant = self.other_tenant
        self.session.save(update_fields=("tenant",))
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_foreign_grading_configuration_is_rejected(self):
        foreign = GradingConfiguration.objects.create(
            tenant=self.other_tenant, name="Other", examination_type="term", configuration={},
        )
        self.exam.configuration = foreign
        self.exam.save(update_fields=("configuration",))
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_publication_for_another_enrollment_is_rejected(self):
        self.publication.enrollment = self.destination
        self.publication.save(update_fields=("enrollment",))
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_source_subjects_and_campus_changes_invalidate_attestation(self):
        self.source.subjects.clear()
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_incomplete_or_absent_published_marks_are_not_converted_to_zero(self):
        for status, score in (("absent", None), ("missing", None), ("exempt", "0")):
            with self.subTest(status=status):
                self.publication.snapshot["marks"] = [
                    {"component": "exam", "status": status, "score": score},
                    {"component": "work", "status": "scored", "score": "42"},
                ]
                self.publication.save(update_fields=("snapshot",))
                with self.assertRaises(ValidationError):
                    self.inspect()

    def test_exemption_with_null_score_is_preserved(self):
        self.assertIsNone(self.inspect().publications[0].snapshot["marks"][1]["score"])

    def test_missing_component_mark_is_rejected(self):
        self.mark.delete()
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_tampered_attestation_original_is_rejected(self):
        EnrollmentProvenanceAttestation.objects.filter(pk=self.attestation).update(original={})
        with self.assertRaises(ValidationError):
            self.inspect()

    def test_revoked_attestation_is_rejected(self):
        response = self.client.post(
            self.base + f"enrollment-provenance/{self.attestation}/revoke/",
            {"reason": "Register corrected"}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        with self.assertRaises(ValidationError):
            self.inspect()
