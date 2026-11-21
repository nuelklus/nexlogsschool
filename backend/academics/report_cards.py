from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F, Max, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit
from core.models import Membership, Student, StudentGuardian
from .examinations import LEADERS, _member
from .models import (
    AcademicTerm, Examination, ExaminationPublication, ReportCardVersion, StudentEnrollment,
)
from .class_subjects import class_subject_ids
from .report_card_preview import ReportCardPreviewSerializer, _pdf, _preview
from .report_campus import lock_report_campus, retry_provenance_conflicts
from .views import MANAGE_ROLES


class PrepareReportSerializer(ReportCardPreviewSerializer):
    class_teacher_remark = serializers.CharField(max_length=1000, allow_blank=False)
    headteacher_remark = serializers.CharField(max_length=1000, allow_blank=False)
    reason = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")


class WithdrawReportSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


def _record_data(report, include_internal=True):
    data = {
        "id": str(report.pk), "term": str(report.term_id), "enrollment": str(report.enrollment_id),
        "student": str(report.student_id), "version": report.version, "status": report.status,
        "snapshot": report.snapshot,
        "reviewed_at": report.reviewed_at.isoformat(),
        "published_at": report.published_at.isoformat() if report.published_at else None,
    }
    if include_internal:
        data["reason"] = report.reason
        data["provenance_attestation"] = str(report.provenance_attestation_id) if report.provenance_attestation_id else None
    return data


def _student_scope(request, tenant_id, member):
    if member.role == Membership.Role.PARENT:
        return StudentGuardian.objects.filter(tenant_id=tenant_id, user=request.user,
                                              student__tenant_id=tenant_id).values_list("student_id", flat=True)
    return Student.objects.filter(tenant_id=tenant_id, user=request.user).values_list("pk", flat=True)


def _accessible_reports(request, tenant_id):
    member = _member(request, tenant_id, (*LEADERS, Membership.Role.PARENT, Membership.Role.STUDENT))
    records = ReportCardVersion.objects.filter(
        tenant_id=tenant_id, student__tenant_id=tenant_id, enrollment__tenant_id=tenant_id,
        term__tenant_id=tenant_id, enrollment__student_id=F("student_id"),
    )
    if member.role in (Membership.Role.PARENT, Membership.Role.STUDENT):
        records = records.filter(status=ReportCardVersion.Status.PUBLISHED,
                                 student_id__in=_student_scope(request, tenant_id, member))
    return records


def _review_report(request, tenant_id, term, data):
    provenance = lock_report_campus(tenant_id, term, data["enrollment"], request.user.pk)
    snapshot = _preview(tenant_id, data)
    enrollment = StudentEnrollment.objects.select_related("school_class").get(pk=data["enrollment"], tenant_id=tenant_id)
    required = set(class_subject_ids(tenant_id, enrollment.school_class_id))
    selected = set(Examination.objects.filter(
        tenant_id=tenant_id, pk__in=data["examinations"],
    ).values_list("subject_id", flat=True))
    if not required or selected != required:
        raise ValidationError({"examinations": "Select one published examination for every subject assigned to the class."})
    versions = ReportCardVersion.objects.filter(tenant_id=tenant_id, term=term, student=enrollment.student)
    if versions.filter(status=ReportCardVersion.Status.REVIEWED).exists():
        raise ValidationError({"detail": "A reviewed version already awaits publication."})
    version = (versions.aggregate(maximum=Max("version"))["maximum"] or 0) + 1
    reason = data["reason"].strip()
    if version > 1 and not reason:
        raise ValidationError({"reason": "A reason is required for a revised report."})
    scores = [Decimal(row["result"]["score"]) for row in snapshot["results"]]
    snapshot.update({
        "_provenance": provenance,
        "overall_average": str((sum(scores) / len(scores)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "overall_method": "Equal-weight average of selected subject percentages, rounded half up to two decimals.",
        "class_teacher_remark": data["class_teacher_remark"],
        "headteacher_remark": data["headteacher_remark"],
    })
    report = ReportCardVersion.objects.create(
        tenant_id=tenant_id, term=term, enrollment=enrollment, student=enrollment.student,
        version=version, snapshot=snapshot, reason=reason, reviewed_by=request.user,
    )
    _audit(request, tenant_id, "academic.report_card.reviewed", "ReportCardVersion", report.pk,
           {"version": version, "reason": reason})
    return report


def _lock_report_and_term(tenant_id, report_id):
    report_term = ReportCardVersion.objects.filter(
        tenant_id=tenant_id, pk=report_id,
    ).values_list("term_id", flat=True).first()
    if report_term is None:
        raise NotFound()
    term = AcademicTerm.objects.select_for_update(of=("self",)).filter(
        tenant_id=tenant_id, pk=report_term,
    ).first()
    if term is None:
        raise NotFound()
    report = ReportCardVersion.objects.select_for_update(of=("self",)).filter(
        tenant_id=tenant_id, pk=report_id,
    ).first()
    if report is None:
        raise NotFound()
    if report.term_id != term.pk:
        raise ValidationError({"detail": "The report's term changed; reload the report before continuing."})
    return report, term


class ReportCardListView(APIView):
    def get(self, request, tenant_id):
        reports = _accessible_reports(request, tenant_id)
        member = _member(request, tenant_id, (*LEADERS, Membership.Role.PARENT, Membership.Role.STUDENT))
        response = Response([_record_data(report, include_internal=member.role in LEADERS)
                             for report in reports.order_by("-reviewed_at")])
        response["Cache-Control"] = "private, no-store"
        return response

    @retry_provenance_conflicts
    def post(self, request, tenant_id):
        _member(request, tenant_id, (Membership.Role.HEADTEACHER,))
        serializer = PrepareReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            term = AcademicTerm.objects.select_for_update().filter(tenant_id=tenant_id, pk=data["term"]).first()
            if not term or term.is_completed:
                raise ValidationError({"term": "Select an open term from this school."})
            report = _review_report(request, tenant_id, term, data)
        return Response(_record_data(report), status=status.HTTP_201_CREATED)


class ReportCardPublishView(APIView):
    @retry_provenance_conflicts
    def post(self, request, tenant_id, report_id):
        _member(request, tenant_id, MANAGE_ROLES)
        with transaction.atomic():
            report, term = _lock_report_and_term(tenant_id, report_id)
            if term.is_completed or report.status != ReportCardVersion.Status.REVIEWED:
                raise ValidationError({"detail": "Only reviewed reports in an open term can be published."})
            if report.provenance_attestation_id:
                raise ValidationError({"detail": "Provenance-linked reports cannot be published until transactional provenance validation is available."})
            provenance = lock_report_campus(
                tenant_id, term, report.enrollment_id, request.user.pk, student_id=report.student_id,
            )
            if not isinstance(report.snapshot, dict) or report.snapshot.get("_provenance") != provenance:
                raise ValidationError({"enrollment": "Report provenance changed or was not verified; withdraw this review and prepare a new one."})
            ids = [row["publication_id"] for row in report.snapshot["results"]]
            publications = ExaminationPublication.objects.filter(
                tenant_id=tenant_id, pk__in=ids, enrollment_id=report.enrollment_id,
                examination__tenant_id=tenant_id, examination__term=term,
                examination__school_class_id=F("enrollment__school_class_id"),
                examination__start_date__gte=F("enrollment__enrolled_on"),
                version=F("examination__published_version"),
            ).filter(
                Q(enrollment__ended_on__gte=F("examination__start_date"))
                | Q(enrollment__ended_on__isnull=True, enrollment__status=StudentEnrollment.Status.ACTIVE)
            )
            current_subjects = set(publications.values_list("examination__subject_id", flat=True))
            if publications.count() != len(ids):
                raise ValidationError({"detail": "Published subject results or enrollment dates have changed since review; prepare a new review."})
            if current_subjects != set(class_subject_ids(tenant_id, report.enrollment.school_class_id)):
                raise ValidationError({"detail": "Class subjects have changed since review; withdraw this review and prepare a new one."})
            report.status = ReportCardVersion.Status.PUBLISHED
            report.published_by = request.user
            report.published_at = timezone.now()
            report.save(update_fields=("status", "published_by", "published_at"))
            _audit(request, tenant_id, "academic.report_card.published", "ReportCardVersion", report.pk,
                   {"version": report.version})
        return Response(_record_data(report))


class ReportCardWithdrawView(APIView):
    def post(self, request, tenant_id, report_id):
        _member(request, tenant_id, (Membership.Role.HEADTEACHER,))
        serializer = WithdrawReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            report, term = _lock_report_and_term(tenant_id, report_id)
            if term.is_completed or report.status != ReportCardVersion.Status.REVIEWED:
                raise ValidationError({"detail": "Only reviewed reports in an open term can be withdrawn."})
            report.status = ReportCardVersion.Status.WITHDRAWN
            report.save(update_fields=("status",))
            _audit(request, tenant_id, "academic.report_card.withdrawn", "ReportCardVersion", report.pk,
                   {"version": report.version, "reason": serializer.validated_data["reason"]})
        return Response(_record_data(report))


class ReportCardPdfView(APIView):
    def get(self, request, tenant_id, report_id):
        report = _accessible_reports(request, tenant_id).filter(pk=report_id, status=ReportCardVersion.Status.PUBLISHED).first()
        if not report:
            raise NotFound()
        response = HttpResponse(_pdf(report.snapshot, official_version=report.version), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="report-card-v{report.version}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response
