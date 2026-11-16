from django.db import transaction
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit
from core.models import Membership
from .examinations import _member
from .models import AcademicTerm, SchoolClass, StudentEnrollment
from .report_cards import PrepareReportSerializer, _record_data, _review_report
from .report_campus import retry_provenance_conflicts


class RosterSerializer(serializers.Serializer):
    term = serializers.UUIDField()
    school_class = serializers.UUIDField()
    roster_date = serializers.DateField()


class BatchEntrySerializer(serializers.Serializer):
    enrollment = serializers.UUIDField()
    examinations = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=100)
    class_teacher_remark = serializers.CharField(max_length=1000)
    headteacher_remark = serializers.CharField(max_length=1000)
    reason = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")


class BatchSerializer(RosterSerializer):
    entries = BatchEntrySerializer(many=True, allow_empty=False, max_length=500)


def _roster(tenant_id, data, lock=False):
    terms = AcademicTerm.objects.filter(tenant_id=tenant_id, academic_year__tenant_id=tenant_id, pk=data["term"])
    if lock:
        terms = terms.select_for_update()
    term = terms.first()
    school_class = SchoolClass.objects.filter(
        pk=data["school_class"], tenant_id=tenant_id, academic_year__tenant_id=tenant_id,
    ).select_related("campus").first()
    if not term or not school_class or school_class.academic_year_id != term.academic_year_id or term.is_completed:
        raise ValidationError({"detail": "Select an open term and class from the same school and academic year."})
    if not term.start_date <= data["roster_date"] <= term.end_date:
        raise ValidationError({"roster_date": "The roster date must fall within the term."})
    if school_class.campus_id and school_class.campus.tenant_id != tenant_id:
        raise ValidationError({"detail": "The class has inconsistent campus ownership."})
    roster = list(StudentEnrollment.objects.filter(
        tenant_id=tenant_id, school_class=school_class, enrolled_on__lte=data["roster_date"],
    ).filter(Q(ended_on__gte=data["roster_date"]) |
             Q(ended_on__isnull=True, status=StudentEnrollment.Status.ACTIVE))
        .select_related("student", "campus").order_by("student__last_name", "student__first_name", "id"))
    if not roster or len(roster) > 500:
        raise ValidationError({"detail": "The roster must contain between 1 and 500 students."})
    if any(row.student.tenant_id != tenant_id or (row.campus_id and row.campus.tenant_id != tenant_id)
           or (row.campus_id and school_class.campus_id and row.campus_id != school_class.campus_id)
           for row in roster):
        raise ValidationError({"detail": "The roster contains inconsistent tenant or campus data."})
    student_ids = [row.student_id for row in roster]
    if len(set(student_ids)) != len(student_ids) or StudentEnrollment.objects.filter(
        tenant_id=tenant_id, student_id__in=student_ids, school_class__academic_year=term.academic_year,
        enrolled_on__lte=data["roster_date"],
    ).filter(Q(ended_on__gte=data["roster_date"]) |
             Q(ended_on__isnull=True, status=StudentEnrollment.Status.ACTIVE)).exclude(
                 school_class=school_class
             ).exists():
        raise ValidationError({"detail": "The roster contains conflicting enrollments."})
    return term, school_class, roster


class ClassReportRosterView(APIView):
    def post(self, request, tenant_id):
        _member(request, tenant_id, (Membership.Role.HEADTEACHER,))
        serializer = RosterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        term, school_class, roster = _roster(tenant_id, serializer.validated_data)
        response = Response({
            "year": term.academic_year.name, "term": term.name, "class_name": school_class.name,
            "campus": school_class.campus.name if school_class.campus_id else None,
            "students": [{"enrollment": str(row.pk), "name": f"{row.student.first_name} {row.student.last_name}",
                          "admission_number": row.student.admission_number} for row in roster],
        })
        response["Cache-Control"] = "private, no-store"
        return response


class ClassReportReviewView(APIView):
    @retry_provenance_conflicts
    def post(self, request, tenant_id):
        _member(request, tenant_id, (Membership.Role.HEADTEACHER,))
        serializer = BatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            term, school_class, roster = _roster(tenant_id, data, lock=True)
            entries = data["entries"]
            ids = [row["enrollment"] for row in entries]
            if len(set(ids)) != len(ids) or set(ids) != {row.pk for row in roster}:
                raise ValidationError({"entries": "Supply exactly one entry for every student in the dated roster. Reload it and retry."})
            by_id = {row.pk: row for row in roster}
            reports, errors = [], []
            for entry in entries:
                student = by_id[entry["enrollment"]]
                individual = PrepareReportSerializer(data={**entry, "term": term.pk})
                if not individual.is_valid():
                    errors.append({"admission_number": student.student.admission_number, "errors": individual.errors})
                    continue
                try:
                    with transaction.atomic():
                        reports.append(_review_report(request, tenant_id, term, individual.validated_data))
                except ValidationError as exc:
                    errors.append({"admission_number": student.student.admission_number, "errors": exc.detail})
            if errors:
                raise ValidationError({"detail": "No reports were saved. Resolve each student error and retry.", "students": errors})
            _audit(request, tenant_id, "academic.report_card.class_reviewed", "SchoolClass", school_class.pk, {
                "term": str(term.pk), "roster_date": data["roster_date"].isoformat(),
                "reports": [str(report.pk) for report in reports],
            })
        response = Response({"count": len(reports), "reports": [_record_data(report) for report in reports]},
                            status=status.HTTP_201_CREATED)
        response["Cache-Control"] = "private, no-store"
        return response
