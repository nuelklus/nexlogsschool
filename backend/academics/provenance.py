import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit
from core.models import Campus, Student
from .class_subjects import class_subject_ids
from .examinations import _member
from .models import (
    AcademicTerm, AssessmentResult, EnrollmentProvenanceAttestation, EnrollmentProvenanceRevocation, ExaminationPublication,
    SchoolClass, StudentEnrollment, Subject, TeachingAssignment,
)
from .views import MANAGE_ROLES


class ProvenanceScopeSerializer(serializers.Serializer):
    term = serializers.UUIDField()
    school_class = serializers.UUIDField()


class RevocationSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class AttestationSerializer(serializers.Serializer):
    term = serializers.UUIDField()
    enrollment = serializers.UUIDField()
    previous_attestation = serializers.UUIDField(allow_null=True)
    fingerprint = serializers.CharField(min_length=64, max_length=64)
    segment_start = serializers.DateField()
    segment_end = serializers.DateField()
    campus = serializers.UUIDField(allow_null=True)
    no_campus = serializers.BooleanField()
    subjects = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=100)
    reason = serializers.CharField(max_length=500)
    evidence = serializers.CharField(max_length=1000)

    def validate_subjects(self, values):
        if len(set(values)) != len(values):
            raise serializers.ValidationError("Do not select a subject more than once.")
        return values


def _context(tenant_id, term, enrollment):
    rows = list(StudentEnrollment.objects.filter(
        student_id=enrollment.student_id, school_class__academic_year=term.academic_year,
        enrolled_on__lte=term.end_date,
    ).filter(Q(ended_on__isnull=True) | Q(ended_on__gte=term.start_date))
        .select_related("school_class", "campus").order_by("enrolled_on", "pk"))
    history = [{
        "id": str(row.pk), "tenant": str(row.tenant_id), "class": str(row.school_class_id),
        "class_tenant": str(row.school_class.tenant_id),
        "campus": str(row.campus_id) if row.campus_id else None,
        "start": row.enrolled_on.isoformat(), "end": row.ended_on.isoformat() if row.ended_on else None,
        "status": row.status,
    } for row in rows]
    original = {
        "term_start": term.start_date.isoformat(), "term_end": term.end_date.isoformat(),
        "student": str(enrollment.student_id), "student_tenant": str(enrollment.student.tenant_id),
        "enrollment": str(enrollment.pk), "class": str(enrollment.school_class_id),
        "class_tenant": str(enrollment.school_class.tenant_id),
        "campus": str(enrollment.campus_id) if enrollment.campus_id else None,
        "class_campus": str(enrollment.school_class.campus_id) if enrollment.school_class.campus_id else None,
        "enrolled_on": enrollment.enrolled_on.isoformat(),
        "ended_on": enrollment.ended_on.isoformat() if enrollment.ended_on else None,
        "status": enrollment.status,
        "subjects": sorted(str(pk) for pk in enrollment.subjects.values_list("pk", flat=True)),
        "class_subjects": sorted(str(pk) for pk in TeachingAssignment.objects.filter(
            tenant_id=tenant_id, school_class=enrollment.school_class,
        ).values_list("subject_id", flat=True)),
        "history": history,
    }
    publications = list(ExaminationPublication.objects.filter(enrollment=enrollment).select_related(
        "examination"
    ).order_by("pk"))
    marks = list(AssessmentResult.objects.filter(enrollment=enrollment).select_related(
        "assessment"
    ).order_by("pk"))
    original["publications"] = [{
        "id": str(row.pk), "tenant": str(row.tenant_id), "examination": str(row.examination_id),
        "exam_tenant": str(row.examination.tenant_id), "term": str(row.examination.term_id),
        "class": str(row.examination.school_class_id), "subject": str(row.examination.subject_id),
        "date": row.examination.start_date.isoformat(), "version": row.version,
    } for row in publications]
    original["marks"] = [{
        "id": str(row.pk), "tenant": str(row.tenant_id), "assessment": str(row.assessment_id),
        "assessment_tenant": str(row.assessment.tenant_id), "term": str(row.assessment.term_id),
        "class": str(row.assessment.school_class_id), "subject": str(row.assessment.subject_id),
        "date": row.assessment.assessment_date.isoformat(), "status": row.mark_status,
    } for row in marks]
    fingerprint = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    issues = ["subjects_require_attestation", "campus_requires_attestation"]
    blocking = []
    if min(term.end_date, enrollment.ended_on or term.end_date) > timezone.localdate():
        blocking.append("segment_not_ended")
    if enrollment.student.tenant_id != tenant_id or (
        enrollment.school_class.campus_id and enrollment.school_class.campus.tenant_id != tenant_id
    ) or enrollment.subjects.exclude(tenant_id=tenant_id).exists() or any(
        row.tenant_id != tenant_id or row.school_class.tenant_id != tenant_id
        or (row.campus_id and row.campus.tenant_id != tenant_id) for row in rows
    ):
        blocking.append("inconsistent_tenant")
    if enrollment.status != StudentEnrollment.Status.ACTIVE and enrollment.ended_on is None:
        blocking.append("missing_end_date")
    for index, row in enumerate(rows):
        if row.status == StudentEnrollment.Status.ACTIVE and row.ended_on is not None:
            blocking.append("inconsistent_enrollment_status")
        if row.status != StudentEnrollment.Status.ACTIVE and row.ended_on is None:
            blocking.append("missing_end_date")
        if index:
            previous = rows[index - 1]
            if previous.ended_on is None or row.enrolled_on <= previous.ended_on:
                blocking.append("overlapping_enrollments")
            elif row.enrolled_on != previous.ended_on + timedelta(days=1):
                blocking.append("gap_after_transfer" if previous.status == StudentEnrollment.Status.TRANSFERRED
                                else "gap_between_enrollments")
        if row.status == StudentEnrollment.Status.TRANSFERRED and row.ended_on and row.ended_on < term.end_date and index == len(rows) - 1:
            blocking.append("missing_transfer_destination")
    for item in original["publications"] + original["marks"]:
        if item["term"] != str(term.pk):
            continue
        if (item["tenant"] != str(tenant_id) or item.get("exam_tenant", item.get("assessment_tenant")) != str(tenant_id)
                or item["class"] != str(enrollment.school_class_id)
                or item["date"] < enrollment.enrolled_on.isoformat()
                or (enrollment.ended_on and item["date"] > enrollment.ended_on.isoformat())):
            blocking.append("conflicting_academic_record")
    return original, fingerprint, sorted(set(issues)), sorted(set(blocking))


def _scope(tenant_id, data):
    term = AcademicTerm.objects.filter(pk=data["term"], tenant_id=tenant_id, academic_year__tenant_id=tenant_id).first()
    school_class = SchoolClass.objects.filter(pk=data["school_class"], tenant_id=tenant_id).first()
    if not term or not school_class or school_class.academic_year_id != term.academic_year_id:
        raise ValidationError({"detail": "Select a term and class from the same school and year."})
    return term, school_class


def _revocation_data(attestation):
    revocation = getattr(attestation, "revocation", None)
    if revocation is None:
        return None
    return {
        "reason": revocation.reason, "actor": str(revocation.revoked_by_id),
        "timestamp": revocation.revoked_at.isoformat(),
    }


class ProvenanceReportView(APIView):
    def post(self, request, tenant_id):
        _member(request, tenant_id, MANAGE_ROLES)
        serializer = ProvenanceScopeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        term, school_class = _scope(tenant_id, serializer.validated_data)
        rows = list(StudentEnrollment.objects.filter(
            tenant_id=tenant_id, school_class=school_class, enrolled_on__lte=term.end_date,
        ).filter(Q(ended_on__isnull=True) | Q(ended_on__gte=term.start_date))
            .select_related("student", "school_class", "campus").order_by("student__last_name", "pk")[:501])
        if len(rows) > 500:
            raise ValidationError({"detail": "More than 500 enrollments require a narrower report."})
        result = []
        for row in rows:
            original, fingerprint, issues, blocking = _context(tenant_id, term, row)
            attestations = list(EnrollmentProvenanceAttestation.objects.filter(
                tenant_id=tenant_id, term=term, enrollment=row,
            ).select_related("revocation").order_by("-version"))
            latest = attestations[0] if attestations else None
            result.append({
                "enrollment": str(row.pk),
                "student": f"{row.student.first_name} {row.student.last_name}" if row.student.tenant_id == tenant_id else "Inconsistent tenant",
                "admission_number": row.student.admission_number if row.student.tenant_id == tenant_id else "",
                "original": original,
                "segment_start": max(term.start_date, row.enrolled_on).isoformat(),
                "segment_end": min(term.end_date, row.ended_on or term.end_date).isoformat(),
                "fingerprint": fingerprint, "issues": issues, "blocking": blocking,
                "attestation": None if latest is None else {
                    "id": str(latest.pk), "version": latest.version,
                    "current": latest.source_fingerprint == fingerprint and _revocation_data(latest) is None,
                    "campus": str(latest.campus_id) if latest.campus_id else None,
                    "subjects": latest.subject_ids, "reason": latest.reason, "evidence": latest.evidence,
                    "actor": str(latest.attested_by_id), "timestamp": latest.attested_at.isoformat(),
                    "revocation": _revocation_data(latest),
                },
                "attestation_history": [{
                    "id": str(item.pk), "version": item.version, "campus": str(item.campus_id) if item.campus_id else None,
                    "subjects": item.subject_ids, "segment_start": item.segment_start.isoformat(),
                    "segment_end": item.segment_end.isoformat(), "original": item.original,
                    "source_fingerprint": item.source_fingerprint, "reason": item.reason, "evidence": item.evidence,
                    "actor": str(item.attested_by_id), "timestamp": item.attested_at.isoformat(),
                    "revocation": _revocation_data(item),
                } for item in attestations],
            })
        response = Response({
            "students": result,
            "campuses": [{"id": str(c.pk), "name": c.name} for c in Campus.objects.filter(tenant_id=tenant_id).order_by("name")],
        })
        response["Cache-Control"] = "private, no-store"
        return response


class ProvenanceAttestationView(APIView):
    def post(self, request, tenant_id):
        _member(request, tenant_id, MANAGE_ROLES)
        serializer = AttestationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            term = AcademicTerm.objects.select_for_update().filter(
                pk=data["term"], tenant_id=tenant_id, academic_year__tenant_id=tenant_id,
            ).first()
            initial = StudentEnrollment.objects.filter(
                pk=data["enrollment"], tenant_id=tenant_id, student__tenant_id=tenant_id,
                school_class__tenant_id=tenant_id,
            ).values("student_id").first()
            if not term or not initial:
                raise NotFound()
            Student.objects.select_for_update().get(pk=initial["student_id"], tenant_id=tenant_id)
            enrollment = StudentEnrollment.objects.select_for_update(of=("self",)).select_related("student", "school_class", "campus").get(
                pk=data["enrollment"], tenant_id=tenant_id,
            )
            if not SchoolClass.objects.select_for_update().filter(
                pk=enrollment.school_class_id, tenant_id=tenant_id
            ).exists():
                raise ValidationError({"enrollment": "The enrollment class does not belong to this school."})
            if enrollment.school_class.academic_year_id != term.academic_year_id or enrollment.enrolled_on > term.end_date or (
                enrollment.ended_on and enrollment.ended_on < term.start_date
            ):
                raise ValidationError({"enrollment": "Enrollment and term must overlap in the same academic year."})
            original, fingerprint, _, blocking = _context(tenant_id, term, enrollment)
            if fingerprint != data["fingerprint"]:
                raise ValidationError({"detail": "Source records changed. Reload the reconciliation report."})
            if blocking:
                raise ValidationError({"detail": "Resolve conflicting or incomplete history before attestation.", "issues": blocking})
            if data["segment_start"] != max(term.start_date, enrollment.enrolled_on) or data["segment_end"] != min(
                term.end_date, enrollment.ended_on or term.end_date
            ):
                raise ValidationError({"detail": "Confirmed segment dates must match the recorded enrollment and term."})
            if data["no_campus"] != (data["campus"] is None):
                raise ValidationError({"campus": "Select a campus or explicitly confirm no campus, but not both."})
            campus = None
            if data["campus"] is not None:
                campus = Campus.objects.filter(pk=data["campus"], tenant_id=tenant_id).first()
                if campus is None:
                    raise ValidationError({"campus": "Select a campus from this school."})
            subject_ids = sorted(str(pk) for pk in data["subjects"])
            if Subject.objects.filter(tenant_id=tenant_id, pk__in=data["subjects"]).count() != len(subject_ids):
                raise ValidationError({"subjects": "Select subjects from this school."})
            recorded_subjects = {
                item["subject"] for item in original["publications"] + original["marks"] if item["term"] == str(term.pk)
            }
            if not recorded_subjects.issubset(set(subject_ids)):
                raise ValidationError({"subjects": "Recorded term results conflict with the confirmed subject list; reconcile the records first."})
            if set(data["subjects"]) != set(class_subject_ids(tenant_id, enrollment.school_class_id)):
                raise ValidationError({"subjects": "Confirm all subjects currently assigned to the class."})
            versions = EnrollmentProvenanceAttestation.objects.filter(tenant_id=tenant_id, term=term, enrollment=enrollment)
            latest = versions.order_by("-version").first()
            if (latest.pk if latest else None) != data["previous_attestation"]:
                raise ValidationError({"detail": "Another attestation was recorded. Reload the reconciliation report."})
            if latest and not EnrollmentProvenanceRevocation.objects.filter(attestation=latest).exists() \
                    and latest.source_fingerprint == fingerprint and latest.campus_id == (campus.pk if campus else None) \
                    and latest.subject_ids == subject_ids:
                raise ValidationError({"detail": "These values are already attested. Reload the report."})
            version = (versions.aggregate(value=Max("version"))["value"] or 0) + 1
            record = EnrollmentProvenanceAttestation.objects.create(
                tenant_id=tenant_id, term=term, enrollment=enrollment, version=version,
                segment_start=data["segment_start"], segment_end=data["segment_end"], campus=campus,
                subject_ids=subject_ids, original=original, source_fingerprint=fingerprint,
                reason=data["reason"], evidence=data["evidence"], attested_by=request.user,
            )
            _audit(request, tenant_id, "academic.enrollment.provenance_attested", "EnrollmentProvenanceAttestation", record.pk, {
                "term": str(term.pk), "enrollment": str(enrollment.pk), "version": version,
                "campus": str(campus.pk) if campus else None, "subjects": subject_ids,
                "reason": data["reason"], "evidence": data["evidence"],
            })
        response = Response({"id": str(record.pk), "version": record.version}, status=status.HTTP_201_CREATED)
        response["Cache-Control"] = "private, no-store"
        return response


class ProvenanceRevocationView(APIView):
    def post(self, request, tenant_id, attestation_id):
        _member(request, tenant_id, MANAGE_ROLES)
        serializer = RevocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            initial = EnrollmentProvenanceAttestation.objects.filter(
                pk=attestation_id, tenant_id=tenant_id, term__tenant_id=tenant_id,
                enrollment__tenant_id=tenant_id,
            ).values("term_id").first()
            if not initial:
                raise NotFound()
            term = AcademicTerm.objects.select_for_update().get(pk=initial["term_id"], tenant_id=tenant_id)
            attestation = EnrollmentProvenanceAttestation.objects.select_for_update().filter(
                pk=attestation_id, tenant_id=tenant_id, term=term, enrollment__tenant_id=tenant_id,
            ).first()
            if not attestation:
                raise NotFound()
            if EnrollmentProvenanceRevocation.objects.filter(attestation=attestation).exists():
                raise ValidationError({"detail": "This attestation has already been revoked."})
            revocation = EnrollmentProvenanceRevocation.objects.create(
                attestation=attestation, reason=serializer.validated_data["reason"], revoked_by=request.user,
            )
            _audit(request, tenant_id, "academic.enrollment.provenance_revoked",
                   "EnrollmentProvenanceAttestation", attestation.pk, {
                       "term": str(term.pk), "enrollment": str(attestation.enrollment_id),
                       "version": attestation.version, "reason": revocation.reason,
                       "revocation": str(revocation.pk),
                   })
        response = Response({"id": str(revocation.pk), "attestation": str(attestation.pk),
                             "reason": revocation.reason, "actor": str(revocation.revoked_by_id),
                             "timestamp": revocation.revoked_at.isoformat()}, status=status.HTTP_201_CREATED)
        response["Cache-Control"] = "private, no-store"
        return response
