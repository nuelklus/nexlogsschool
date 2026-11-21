from io import BytesIO
from xml.sax.saxutils import escape

from django.db.models import F
from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .class_subjects import class_subject_ids
from .examinations import LEADERS, _member
from .models import AcademicTerm, AttendanceRecord, Examination, ExaminationPublication, StudentEnrollment


class ReportCardPreviewSerializer(serializers.Serializer):
    term = serializers.UUIDField()
    enrollment = serializers.UUIDField()
    examinations = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=100)
    format = serializers.ChoiceField(choices=("json", "pdf"), default="json")

    def validate_examinations(self, values):
        if len(set(values)) != len(values):
            raise serializers.ValidationError("Each examination may be selected only once.")
        return values


def _preview(tenant_id, data):
    term = AcademicTerm.objects.filter(
        tenant_id=tenant_id, academic_year__tenant_id=tenant_id, pk=data["term"]
    ).select_related("academic_year").first()
    enrollment = StudentEnrollment.objects.filter(
        pk=data["enrollment"], tenant_id=tenant_id, student__tenant_id=tenant_id,
        school_class__tenant_id=tenant_id, school_class__academic_year__tenant_id=tenant_id,
    ).select_related("student", "school_class", "campus", "tenant").first()
    if not term or not enrollment or enrollment.school_class.academic_year_id != term.academic_year_id:
        raise ValidationError({"detail": "Select an enrollment and term from the same school and academic year."})
    if (enrollment.enrolled_on > term.end_date or (enrollment.ended_on and enrollment.ended_on < term.start_date)
            or (enrollment.campus_id and enrollment.campus.tenant_id != tenant_id)
            or (enrollment.school_class.campus_id and enrollment.school_class.campus.tenant_id != tenant_id)
            or (enrollment.campus_id and enrollment.school_class.campus_id
                and enrollment.campus_id != enrollment.school_class.campus_id)):
        raise ValidationError({"detail": "The enrollment does not cover this term or has inconsistent campus data."})
    exams = list(Examination.objects.filter(
        tenant_id=tenant_id, pk__in=data["examinations"], term=term, school_class=enrollment.school_class,
        subject__tenant_id=tenant_id, published_version__gt=0,
    ).order_by("subject__name", "id"))
    if len(exams) != len(data["examinations"]):
        raise ValidationError({"examinations": "All selected examinations must be published for this class and term."})
    if len({exam.subject_id for exam in exams}) != len(exams):
        raise ValidationError({"examinations": "Select at most one examination per subject."})
    if not {exam.subject_id for exam in exams}.issubset(set(class_subject_ids(tenant_id, enrollment.school_class_id))):
        raise ValidationError({"examinations": "Every selected subject must be assigned to this class."})
    if any(
        exam.start_date < enrollment.enrolled_on
        or (enrollment.ended_on is not None and exam.start_date > enrollment.ended_on)
        or (enrollment.ended_on is None and enrollment.status != StudentEnrollment.Status.ACTIVE)
        for exam in exams
    ):
        raise ValidationError({"examinations": "The enrollment must cover each examination start date; reconcile the historical record."})
    publications = ExaminationPublication.objects.filter(
        tenant_id=tenant_id, enrollment=enrollment, examination__in=exams,
        examination__tenant_id=tenant_id, version=F("examination__published_version"),
    ).select_related("examination")
    by_exam = {row.examination_id: row for row in publications}
    if len(by_exam) != len(exams):
        raise ValidationError({"examinations": "The student does not have a published result for every selected examination."})
    attendance = {state: 0 for state in AttendanceRecord.Status.values}
    for row in AttendanceRecord.objects.filter(
        tenant_id=tenant_id, enrollment=enrollment,
        date__range=(max(term.start_date, enrollment.enrolled_on), min(term.end_date, enrollment.ended_on or term.end_date)),
    ).values_list("status", flat=True):
        attendance[row] += 1
    student = enrollment.student
    return {
        "school": enrollment.tenant.name,
        "campus": enrollment.campus.name if enrollment.campus_id else None,
        "student": f"{student.first_name} {student.last_name}",
        "admission_number": student.admission_number,
        "class": enrollment.school_class.name,
        "section": enrollment.school_class.section,
        "year": term.academic_year.name,
        "term": term.name,
        "attendance": attendance,
        "results": [
            {"publication_id": str(by_exam[exam.pk].pk), "version": by_exam[exam.pk].version,
             "result": by_exam[exam.pk].snapshot}
            for exam in exams
        ],
    }


def _pdf(data, official_version=None):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    def text(value):
        return escape(str(value))
    story = [
        Paragraph(text(data["school"]), styles["Title"]),
        Paragraph(f"OFFICIAL REPORT CARD — VERSION {official_version}" if official_version is not None
                  else "UNPUBLISHED PREVIEW — NOT AN OFFICIAL REPORT CARD", styles["Heading2"]),
        Paragraph(f"{text(data['student'])} · {text(data['admission_number'])}", styles["Normal"]),
        Paragraph(f"{text(data['class'])} {text(data['section'])} · {text(data['year'])} · {text(data['term'])}", styles["Normal"]),
        Paragraph(f"Campus: {text(data['campus'] or 'Not assigned')}", styles["Normal"]),
        Spacer(1, 12),
    ]
    rows = [[Paragraph(value, styles["BodyText"]) for value in ("Subject", "Examination", "Score", "Grade", "Remark")]]
    for item in data["results"]:
        result = item["result"]
        rows.append([Paragraph(text(result[key]), styles["BodyText"])
                     for key in ("subject", "examination", "score", "grade", "description")])
    table = Table(rows, repeatRows=1, colWidths=(90, 100, 48, 48, 150))
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.extend([table, Spacer(1, 12), Paragraph(
        "Recorded attendance: " + " · ".join(f"{text(k.title())}: {v}" for k, v in data["attendance"].items()),
        styles["Normal"],
    )])
    if official_version is None:
        story.append(Paragraph("Attendance covers this enrollment only. Subject selection may be incomplete.", styles["Normal"]))
    else:
        story.extend([
            Paragraph(f"Overall average: {text(data['overall_average'])}% — {text(data['overall_method'])}", styles["Normal"]),
            Paragraph(f"Class teacher remark: {text(data['class_teacher_remark'])}", styles["Normal"]),
            Paragraph(f"Headteacher remark: {text(data['headteacher_remark'])}", styles["Normal"]),
        ])
    SimpleDocTemplate(buffer).build(story)
    return buffer.getvalue()


class ReportCardPreviewView(APIView):
    def post(self, request, tenant_id):
        _member(request, tenant_id, LEADERS)
        serializer = ReportCardPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = _preview(tenant_id, serializer.validated_data)
        if serializer.validated_data["format"] == "pdf":
            response = HttpResponse(_pdf(data), content_type="application/pdf")
            response["Content-Disposition"] = 'attachment; filename="report-card-preview.pdf"'
            response["Cache-Control"] = "private, no-store"
            return response
        response = Response(data)
        response["Cache-Control"] = "private, no-store"
        return response
