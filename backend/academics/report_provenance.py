import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db.models import F
from rest_framework.exceptions import ValidationError

from .models import (
    AcademicTerm, Assessment, AssessmentResult, AttendanceRecord, EnrollmentProvenanceAttestation, Examination,
    ExaminationPublication, StudentEnrollment, Subject,
)
from .provenance import _context
from .class_subjects import class_subject_ids


@dataclass(frozen=True)
class SegmentEvidence:
    attestation: EnrollmentProvenanceAttestation
    enrollment: StudentEnrollment
    publications: tuple
    fingerprint: str


def inspect_segment(tenant_id, term_id, enrollment_id, attestation_id, examination_ids):
    """Inspect candidate sources; callers must separately coordinate every writer."""
    term = AcademicTerm.objects.filter(
        pk=term_id, tenant_id=tenant_id, academic_year__tenant_id=tenant_id,
    ).first()
    enrollment = StudentEnrollment.objects.filter(
        pk=enrollment_id, tenant_id=tenant_id, student__tenant_id=tenant_id,
        school_class__tenant_id=tenant_id, school_class__academic_year__tenant_id=tenant_id,
    ).select_related("student", "tenant", "school_class", "school_class__campus", "campus").first()
    if not term or not enrollment or enrollment.school_class.academic_year_id != term.academic_year_id:
        raise ValidationError({"enrollment": "Select an enrollment and term from the same school and year."})
    if enrollment.enrolled_on > term.end_date or (enrollment.ended_on and enrollment.ended_on < term.start_date):
        raise ValidationError({"enrollment": "The enrollment does not overlap this term."})

    attestation = EnrollmentProvenanceAttestation.objects.filter(
        pk=attestation_id, tenant_id=tenant_id, term=term, enrollment=enrollment,
    ).select_related("campus").first()
    latest = EnrollmentProvenanceAttestation.objects.filter(
        term=term, enrollment=enrollment,
    ).order_by("-version").values_list("pk", flat=True).first()
    if not attestation or latest != attestation.pk:
        raise ValidationError({"attestation": "Select the latest attestation for this enrollment and term."})
    if hasattr(attestation, "revocation"):
        raise ValidationError({"attestation": "The attestation has been revoked."})
    original, source_fingerprint, _, blocking = _context(tenant_id, term, enrollment)
    if (blocking or source_fingerprint != attestation.source_fingerprint or original != attestation.original
            or not attestation.reason.strip() or not attestation.evidence.strip()):
        raise ValidationError({"attestation": "Enrollment history or academic sources require reconciliation.", "issues": blocking})
    if (attestation.segment_start != max(term.start_date, enrollment.enrolled_on)
            or attestation.segment_end != min(term.end_date, enrollment.ended_on or term.end_date)
            or (attestation.campus_id and attestation.campus.tenant_id != tenant_id)):
        raise ValidationError({"attestation": "The attested segment dates or campus are inconsistent."})
    subjects = attestation.subject_ids
    if not isinstance(subjects, list) or not subjects or any(not isinstance(item, str) for item in subjects):
        raise ValidationError({"attestation": "The attested subject list is invalid."})
    try:
        subject_ids = [UUID(item) for item in subjects]
    except ValueError as exc:
        raise ValidationError({"attestation": "The attested subject list is invalid."}) from exc
    if len(set(subject_ids)) != len(subject_ids) or Subject.objects.filter(
        tenant_id=tenant_id, pk__in=subject_ids,
    ).count() != len(subject_ids):
        raise ValidationError({"attestation": "The attested subjects are inconsistent with this school."})
    if set(subject_ids) != set(class_subject_ids(tenant_id, enrollment.school_class_id)):
        raise ValidationError({"attestation": "The attested subjects must match all subjects assigned to this class."})

    if not examination_ids or len(set(examination_ids)) != len(examination_ids):
        raise ValidationError({"examinations": "Select each required subject examination exactly once."})
    examinations = list(Examination.objects.filter(
        tenant_id=tenant_id, term=term, school_class=enrollment.school_class,
        subject__tenant_id=tenant_id, configuration__tenant_id=tenant_id,
        pk__in=examination_ids, published_version__gt=0,
    ).select_related("session", "subject", "configuration").order_by("pk"))
    if len(examinations) != len(examination_ids) or {exam.subject_id for exam in examinations} != set(subject_ids) \
            or len(examinations) != len(subject_ids):
        raise ValidationError({"examinations": "Select one published result for each attested subject in this segment."})
    for exam in examinations:
        if not (attestation.segment_start <= exam.start_date <= exam.end_date <= attestation.segment_end):
            raise ValidationError({"examinations": "An examination falls outside the attested segment."})
        if exam.session_id and (exam.session.tenant_id != tenant_id or exam.session.term_id != term.pk):
            raise ValidationError({"examinations": "An examination session has inconsistent school or term."})

    publications = list(ExaminationPublication.objects.filter(
        tenant_id=tenant_id, enrollment=enrollment, examination__in=examinations,
        version=F("examination__published_version"),
    ).select_related("examination").order_by("examination_id"))
    if len(publications) != len(examinations) or len({p.examination_id for p in publications}) != len(examinations):
        raise ValidationError({"examinations": "Published results are missing or inconsistent for this enrollment."})
    components = list(Assessment.objects.filter(
        examination__in=examinations,
    ).order_by("pk"))
    marks = list(AssessmentResult.objects.filter(
        enrollment=enrollment, assessment__examination__in=examinations,
    ).select_related("assessment", "assessment__examination").order_by("pk"))
    for mark in marks:
        assessment, exam = mark.assessment, mark.assessment.examination
        if (mark.tenant_id != tenant_id or assessment.tenant_id != tenant_id
                or assessment.term_id != term.pk or assessment.school_class_id != enrollment.school_class_id
                or assessment.subject_id != exam.subject_id
                or not attestation.segment_start <= assessment.assessment_date <= attestation.segment_end):
            raise ValidationError({"examinations": "A mark has inconsistent segment provenance."})
        if (mark.mark_status not in AssessmentResult.Status.values
                or (mark.mark_status == AssessmentResult.Status.SCORED) != (mark.score is not None)):
            raise ValidationError({"examinations": "A mark status and score are inconsistent."})
    if not components or {mark.assessment_id for mark in marks} != {item.pk for item in components}:
        raise ValidationError({"examinations": "A mark is missing for an examination component."})
    for publication in publications:
        published_marks = publication.snapshot.get("marks") if isinstance(publication.snapshot, dict) else None
        expected = [item.component_key for item in components if item.examination_id == publication.examination_id]
        if not isinstance(published_marks, list) or not expected or len(set(expected)) != len(expected) or any(
            not isinstance(mark, dict) or not isinstance(mark.get("component"), str)
            or not isinstance(mark.get("remarks"), str)
            or mark.get("status") not in (AssessmentResult.Status.SCORED, AssessmentResult.Status.EXEMPT,
                                          AssessmentResult.Status.NOT_APPLICABLE)
            or (mark.get("status") == AssessmentResult.Status.SCORED) != (mark.get("score") is not None)
            for mark in published_marks
        ) or not any(mark["status"] == AssessmentResult.Status.SCORED for mark in published_marks) \
                or sorted(mark.get("component", "") for mark in published_marks) != sorted(expected):
            raise ValidationError({"examinations": "Published component marks are incomplete or inconsistent."})
        source_marks = {mark.assessment.component_key: mark for mark in marks
                        if mark.assessment.examination_id == publication.examination_id}
        for item in published_marks:
            source = source_marks[item["component"]]
            try:
                score = Decimal(item["score"]) if isinstance(item["score"], str) else None
            except InvalidOperation as exc:
                raise ValidationError({"examinations": "A published mark has an invalid score."}) from exc
            if (source.mark_status != item["status"] or source.remarks != item["remarks"]
                    or (source.score is None and item["score"] is not None)
                    or (source.score is not None and (score is None or not score.is_finite() or score != source.score))):
                raise ValidationError({"examinations": "Current component marks differ from the published result; reconcile before review."})
    attendance = list(AttendanceRecord.objects.filter(
        enrollment=enrollment, date__range=(attestation.segment_start, attestation.segment_end),
    ).order_by("pk"))
    if any(row.tenant_id != tenant_id or row.status not in AttendanceRecord.Status.values for row in attendance):
        raise ValidationError({"enrollment": "Attendance has inconsistent school or status."})
    evidence = {
        "attestation": str(attestation.pk), "attestation_version": attestation.version,
        "attested_by": str(attestation.attested_by_id), "attested_at": attestation.attested_at.isoformat(),
        "attestation_reason": attestation.reason, "attestation_evidence": attestation.evidence,
        "source_fingerprint": source_fingerprint, "original": original,
        "segment_start": attestation.segment_start.isoformat(), "segment_end": attestation.segment_end.isoformat(),
        "campus": str(attestation.campus_id) if attestation.campus_id else None,
        "campus_name": attestation.campus.name if attestation.campus_id else None,
        "presentation": {
            "school": enrollment.tenant.name, "student_first_name": enrollment.student.first_name,
            "student_last_name": enrollment.student.last_name, "admission_number": enrollment.student.admission_number,
            "class_name": enrollment.school_class.name, "section": enrollment.school_class.section,
            "enrollment_campus_name": enrollment.campus.name if enrollment.campus_id else None,
            "class_campus_name": enrollment.school_class.campus.name if enrollment.school_class.campus_id else None,
            "year_name": term.academic_year.name, "term_name": term.name,
        },
        "subjects": sorted(subjects),
        "examinations": [{
            "id": str(exam.pk), "subject": str(exam.subject_id), "subject_name": exam.subject.name,
            "name": exam.name,
            "start": exam.start_date.isoformat(), "end": exam.end_date.isoformat(),
            "session": str(exam.session_id) if exam.session_id else None,
            "session_term": str(exam.session.term_id) if exam.session_id else None,
            "session_name": exam.session.name if exam.session_id else None,
            "status": exam.status, "version": exam.version, "published_version": exam.published_version,
            "configuration_id": str(exam.configuration_id), "configuration": exam.configuration.configuration,
        } for exam in examinations],
        "publications": [{"id": str(row.pk), "examination": str(row.examination_id),
                          "enrollment": str(row.enrollment_id), "snapshot": row.snapshot, "version": row.version}
                         for row in publications],
        "components": [{"id": str(row.pk), "examination": str(row.examination_id), "key": row.component_key,
                        "tenant": str(row.tenant_id), "term": str(row.term_id),
                        "class": str(row.school_class_id), "subject": str(row.subject_id),
                        "date": row.assessment_date.isoformat(), "maximum": str(row.max_score)}
                       for row in components],
        "marks": [{"id": str(row.pk), "assessment": str(row.assessment_id),
                   "enrollment": str(row.enrollment_id), "status": row.mark_status,
                   "score": str(row.score) if row.score is not None else None, "remarks": row.remarks}
                  for row in marks],
        "attendance": [{"id": str(row.pk), "date": row.date.isoformat(), "status": row.status}
                       for row in attendance],
    }
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return SegmentEvidence(attestation, enrollment, tuple(publications), fingerprint)
