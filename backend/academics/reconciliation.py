from collections import defaultdict

from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _membership
from core.models import AcademicYear, Student

from .models import SchoolClass, SchoolClassLevel, StudentEnrollment
from .views import MANAGE_ROLES


def _normalise(value):
    return " ".join(value.casefold().split())


def _level_names(level):
    names = {level.display_name}
    if level.catalogue_level:
        names.add(level.catalogue_level.name)
        code = level.catalogue_level.code
        if code.startswith("gh-basic-") and code.removeprefix("gh-basic-") in {"1", "2", "3", "4", "5", "6"}:
            names.add("Grade " + code.removeprefix("gh-basic-"))
    return {_normalise(name) for name in names}


class ClassReconciliationView(APIView):
    """Report legacy mappings and current enrollment conflicts without modifying records."""

    def get(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        levels = list(
            SchoolClassLevel.objects.filter(tenant_id=tenant_id, is_active=True)
            .select_related("catalogue_level")
        )
        candidates_by_name = defaultdict(list)
        for level in levels:
            for name in _level_names(level):
                candidates_by_name[name].append({"id": str(level.pk), "name": level.display_name})
        classes = SchoolClass.objects.filter(
            tenant_id=tenant_id, class_level__isnull=True
        ).select_related("academic_year")
        unmapped = [{
            "id": str(item.pk),
            "name": item.name,
            "section": item.section,
            "academic_year": item.academic_year.name,
            "candidates": candidates_by_name.get(_normalise(item.name), []),
        } for item in classes]

        year = AcademicYear.objects.filter(tenant_id=tenant_id, is_current=True).first()
        student_issues = []
        if year:
            enrollments = defaultdict(list)
            for enrollment in StudentEnrollment.objects.filter(
                tenant_id=tenant_id,
                school_class__academic_year=year,
                status=StudentEnrollment.Status.ACTIVE,
            ).select_related("school_class"):
                enrollments[enrollment.student_id].append(enrollment)
            for student in Student.objects.filter(tenant_id=tenant_id, status=Student.Status.ACTIVE):
                matches = enrollments[student.pk]
                if not matches:
                    issue = "no_current_enrollment"
                elif len(matches) > 1:
                    issue = "multiple_current_enrollments"
                elif _normalise(student.grade_level) != _normalise(matches[0].school_class.name):
                    issue = "grade_mismatch"
                else:
                    continue
                student_issues.append({
                    "id": str(student.pk),
                    "name": f"{student.first_name} {student.last_name}".strip(),
                    "admission_number": student.admission_number,
                    "grade_level": student.grade_level,
                    "issue": issue,
                    "classes": [
                        {"id": str(item.school_class_id), "name": item.school_class.name,
                         "section": item.school_class.section}
                        for item in matches
                    ],
                })
        return Response({
            "current_year": {"id": str(year.pk), "name": year.name} if year else None,
            "unmapped_classes": unmapped,
            "student_issues": student_issues,
        })
