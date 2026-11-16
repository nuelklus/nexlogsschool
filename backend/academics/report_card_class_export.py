from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from core.enrollment import _audit
from .examinations import LEADERS, _member
from .models import AcademicTerm, ReportCardVersion, SchoolClass, StudentEnrollment
from .report_card_preview import _pdf


class ClassReportExportSerializer(serializers.Serializer):
    term = serializers.UUIDField()
    school_class = serializers.UUIDField()
    roster_date = serializers.DateField()


class ClassReportExportView(APIView):
    def post(self, request, tenant_id):
        _member(request, tenant_id, LEADERS)
        serializer = ClassReportExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        term = AcademicTerm.objects.filter(
            pk=data["term"], tenant_id=tenant_id, academic_year__tenant_id=tenant_id,
        ).first()
        school_class = SchoolClass.objects.filter(
            pk=data["school_class"], tenant_id=tenant_id, academic_year__tenant_id=tenant_id,
        ).select_related("campus").first()
        if not term or not school_class or school_class.academic_year_id != term.academic_year_id:
            raise ValidationError({"detail": "Select a class and term from the same school and academic year."})
        if not term.start_date <= data["roster_date"] <= term.end_date:
            raise ValidationError({"roster_date": "The roster date must fall within the term."})
        if school_class.campus_id and school_class.campus.tenant_id != tenant_id:
            raise ValidationError({"detail": "The class has inconsistent campus ownership."})
        roster = list(StudentEnrollment.objects.filter(
            tenant_id=tenant_id, school_class=school_class, enrolled_on__lte=data["roster_date"],
        ).filter(Q(ended_on__gte=data["roster_date"]) |
                 Q(ended_on__isnull=True, status=StudentEnrollment.Status.ACTIVE))
            .select_related("student", "campus").order_by("student__last_name", "student__first_name", "id"))
        if not roster:
            raise ValidationError({"detail": "There are no enrollments in this class on the roster date."})
        if len(roster) > 500:
            raise ValidationError({"detail": "The class exceeds the 500-student export limit."})
        if any(item.student.tenant_id != tenant_id or
               (item.campus_id and item.campus.tenant_id != tenant_id) or
               (item.campus_id and school_class.campus_id and item.campus_id != school_class.campus_id)
               for item in roster):
            raise ValidationError({"detail": "The roster contains inconsistent tenant or campus data."})
        students = [item.student_id for item in roster]
        if len(students) != len(set(students)) or StudentEnrollment.objects.filter(
            tenant_id=tenant_id, student_id__in=students, school_class__academic_year=term.academic_year,
            enrolled_on__lte=data["roster_date"],
        ).filter(Q(ended_on__gte=data["roster_date"]) |
                 Q(ended_on__isnull=True, status=StudentEnrollment.Status.ACTIVE)).exclude(
                     school_class=school_class
                 ).exists():
            raise ValidationError({"detail": "The roster contains students with conflicting enrollments on this date."})
        records = ReportCardVersion.objects.filter(
            tenant_id=tenant_id, term=term, enrollment_id__in=[item.pk for item in roster],
            student__tenant_id=tenant_id, status=ReportCardVersion.Status.PUBLISHED,
        ).order_by("enrollment_id", "-version")
        latest = {}
        for report in records:
            latest.setdefault(report.enrollment_id, report)
        missing = [item.student.admission_number for item in roster if item.pk not in latest]
        if missing:
            raise ValidationError({"detail": "Published reports are missing for: " + ", ".join(missing)})
        if any(latest[item.pk].student_id != item.student_id for item in roster):
            raise ValidationError({"detail": "A report has inconsistent enrollment ownership."})
        buffer = BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            for enrollment in roster:
                report = latest[enrollment.pk]
                archive.writestr(f"{enrollment.pk}-v{report.version}.pdf", _pdf(report.snapshot, official_version=report.version))
        with transaction.atomic():
            _audit(request, tenant_id, "academic.report_card.class_exported", "SchoolClass", school_class.pk, {
                "term": str(term.pk), "roster_date": data["roster_date"].isoformat(),
                "reports": [str(latest[item.pk].pk) for item in roster],
            })
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="class-report-cards.zip"'
        response["Cache-Control"] = "private, no-store"
        return response
