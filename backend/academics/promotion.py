"""Year-end promotion: move students from one academic year's classes into the next year's."""
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership
from core.models import AcademicYear, Student

from .campus import validate_same_campus
from .models import SchoolClass, SchoolClassLevel, StudentEnrollment
from .views import MANAGE_ROLES

# Class order used to suggest where each class moves next year; the last class graduates.
PROGRESSION = [
    "Creche", "Nursery 1", "Nursery 2", "KG 1", "KG 2",
    "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6",
    "JHS 1", "JHS 2", "JHS 3", "SHS 1", "SHS 2", "SHS 3",
]
ACTIONS = ("promote", "repeat", "graduate", "leave")


def _norm(value):
    return " ".join(value.lower().split())


def _years(tenant_id, data):
    try:
        source = AcademicYear.objects.get(pk=data.get("from_year"), tenant_id=tenant_id)
        target = AcademicYear.objects.get(pk=data.get("to_year"), tenant_id=tenant_id)
    except (AcademicYear.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        raise ValidationError({"detail": "Choose the current and the next academic year."})
    if source.pk == target.pk:
        raise ValidationError({"to_year": "Choose a different academic year to promote into."})
    if target.start_date <= source.end_date:
        raise ValidationError({"to_year": "Choose a later academic year that starts after the source year ends."})
    return source, target


def _match(target_classes, name, section):
    """The class in the new year called ``name``, preferring the same section."""
    candidates = [c for c in target_classes if _norm(c.name) == _norm(name)]
    same_section = [c for c in candidates if c.section == section]
    matches = same_section if same_section else candidates
    return matches[0] if len(matches) == 1 else None


def _suggest(school_class, target_classes, levels):
    if school_class.class_level_id:
        level = school_class.class_level
        if level.is_terminal and level.is_active:
            return "graduate", None
        if not level.is_active:
            return "", None
        next_levels = [item for item in levels if item.sort_order > level.sort_order]
        if not next_levels:
            return "", None
        next_order = min(item.sort_order for item in next_levels)
        next_levels = [item for item in next_levels if item.sort_order == next_order]
        if len(next_levels) != 1:
            return "", None
        candidates = [item for item in target_classes if item.class_level_id == next_levels[0].pk]
        same_section = [item for item in candidates if item.section == school_class.section]
        matches = same_section if same_section else candidates
        if len(matches) == 1:
            return "promote", matches[0]
        return "", None
    name = _norm(school_class.name)
    order = [_norm(item) for item in PROGRESSION]
    if name in order and order.index(name) + 1 < len(order):
        nxt = _match(target_classes, PROGRESSION[order.index(name) + 1], school_class.section)
        if nxt:
            return "promote", nxt
    return "", None


class PromotionView(APIView):
    def _manager(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()

    def get(self, request, tenant_id):
        self._manager(request, tenant_id)
        source, target = _years(tenant_id, request.query_params)
        target_classes = list(SchoolClass.objects.filter(tenant_id=tenant_id, academic_year=target, is_active=True))
        levels = list(SchoolClassLevel.objects.filter(tenant_id=tenant_id, is_active=True))
        already = set(
            StudentEnrollment.objects.filter(
                tenant_id=tenant_id, school_class__academic_year=target, status=StudentEnrollment.Status.ACTIVE
            ).values_list("student_id", flat=True)
        )
        enrollments = StudentEnrollment.objects.filter(
            tenant_id=tenant_id, school_class__academic_year=source, status=StudentEnrollment.Status.ACTIVE,
            student__status=Student.Status.ACTIVE,
        ).select_related("student", "school_class__class_level")
        source_class = request.query_params.get("source_class")
        if source_class is not None:
            try:
                selected_class = SchoolClass.objects.get(pk=source_class, tenant_id=tenant_id, academic_year=source)
            except (SchoolClass.DoesNotExist, DjangoValidationError, ValueError, TypeError):
                raise ValidationError({"source_class": "Choose a class in the source academic year."})
            enrollments = enrollments.filter(school_class=selected_class)
        rows = []
        for item in enrollments:
            action, nxt = _suggest(item.school_class, target_classes, levels)
            rows.append({
                "enrollment": str(item.pk), "student": str(item.student_id),
                "student_name": f"{item.student.first_name} {item.student.last_name}",
                "admission_number": item.student.admission_number,
                "from_class": item.school_class.name + (f" {item.school_class.section}" if item.school_class.section else ""),
                "suggested_action": "" if item.student_id in already else action,
                "suggested_class": str(nxt.pk) if nxt else None,
                "already_enrolled": item.student_id in already,
            })
        rows.sort(key=lambda row: (row["from_class"], row["student_name"]))
        return Response({
            "from_year": str(source.pk), "to_year": str(target.pk),
            "target_classes": [
                {"id": str(c.pk), "name": c.name + (f" {c.section}" if c.section else "")} for c in target_classes
            ],
            "rows": rows,
        })

    def post(self, request, tenant_id):
        self._manager(request, tenant_id)
        data = request.data
        source, target = _years(tenant_id, data)
        decisions = data.get("decisions")
        if not isinstance(decisions, list) or not decisions:
            raise ValidationError({"decisions": "Choose what happens to at least one student."})
        counts = {action: 0 for action in ACTIONS}
        counts["skipped"] = 0

        with transaction.atomic():
            enrollment_ids = []
            for decision in decisions:
                if not isinstance(decision, dict) or not decision.get("enrollment"):
                    raise ValidationError({"decisions": "Each decision must identify an enrollment."})
                try:
                    enrollment_ids.append(UUID(str(decision["enrollment"])))
                except (ValueError, TypeError, AttributeError):
                    raise ValidationError({"decisions": "Each decision must identify a valid enrollment."})
            if len(enrollment_ids) != len(set(enrollment_ids)):
                raise ValidationError({"decisions": "A student enrollment cannot appear more than once."})
            selected = list(
                StudentEnrollment.objects.filter(
                    pk__in=enrollment_ids, tenant_id=tenant_id, school_class__academic_year=source,
                    status=StudentEnrollment.Status.ACTIVE,
                ).values_list("student_id", flat=True)
            )
            if len(selected) != len(enrollment_ids) or len(set(selected)) != len(selected):
                raise ValidationError({"decisions": "Each student needs exactly one active enrollment in the chosen year."})
            list(Student.objects.select_for_update().filter(
                tenant_id=tenant_id, pk__in=selected
            ).order_by("pk"))
            target_classes = SchoolClass.objects.select_for_update().filter(
                tenant_id=tenant_id, academic_year=target, is_active=True
            ).order_by("pk")
            by_id = {str(c.pk): c for c in target_classes}
            for decision in decisions:
                action = decision.get("action") if isinstance(decision, dict) else None
                if action not in ACTIONS:
                    raise ValidationError({"decisions": "Each student needs promote, repeat, graduate or leave."})
                enrollment = (
                    StudentEnrollment.objects.select_related("student", "school_class")
                    .prefetch_related("subjects")
                    .filter(pk=decision.get("enrollment"), tenant_id=tenant_id, school_class__academic_year=source,
                            status=StudentEnrollment.Status.ACTIVE)
                    .first()
                )
                if enrollment is None:
                    raise ValidationError({"decisions": "A student in this list is not enrolled in the chosen year."})
                student = enrollment.student
                if action == "leave":
                    counts["leave"] += 1
                    continue
                if student.status != Student.Status.ACTIVE:
                    raise ValidationError({"decisions": "Only active students can be promoted or graduated."})
                if StudentEnrollment.objects.filter(
                    student=student, school_class__academic_year=target, status=StudentEnrollment.Status.ACTIVE
                ).exists():
                    counts["skipped"] += 1
                    continue
                if action == "graduate":
                    student.status = Student.Status.GRADUATED
                    student.save(update_fields=("status", "updated_at"))
                    counts["graduate"] += 1
                    continue
                if action == "repeat" and not decision.get("target_class"):
                    destination = _match(target_classes, enrollment.school_class.name, enrollment.school_class.section)
                else:
                    destination = by_id.get(str(decision.get("target_class")))
                if destination is None:
                    raise ValidationError(
                        {"decisions": f"Choose a class in the new year for {student.first_name} {student.last_name}."}
                    )
                validate_same_campus(tenant_id, student, enrollment, destination, "decisions")
                created = StudentEnrollment.objects.create(
                    tenant_id=tenant_id, student=student,
                    campus=destination.campus or student.campus, school_class=destination,
                    enrolled_on=target.start_date,
                )
                created.subjects.set(enrollment.subjects.all())
                counts[action] += 1

            if data.get("make_current") is True:
                AcademicYear.objects.filter(tenant_id=tenant_id, is_current=True).exclude(pk=target.pk).update(is_current=False)
                target.is_current = True
                target.save(update_fields=("is_current",))
            if target.is_current:
                for item in StudentEnrollment.objects.filter(
                    tenant_id=tenant_id, school_class__academic_year=target, status=StudentEnrollment.Status.ACTIVE,
                    student__status=Student.Status.ACTIVE,
                ).select_related("student", "school_class", "campus"):
                    changed_fields = []
                    if item.student.grade_level != item.school_class.name:
                        item.student.grade_level = item.school_class.name
                        changed_fields.append("grade_level")
                    if item.campus_id and item.student.campus_id != item.campus_id:
                        item.student.campus_id = item.campus_id
                        changed_fields.append("campus")
                    if changed_fields:
                        item.student.save(update_fields=(*changed_fields, "updated_at"))
            _audit(request, tenant_id, "academic.promotion.applied", "AcademicYear", target.pk,
                   {"from_year": str(source.pk), **counts})
        return Response(counts, status=status.HTTP_200_OK)
