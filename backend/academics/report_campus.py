from functools import wraps
from time import sleep

from django.db import DatabaseError, connection
from rest_framework.exceptions import APIException, ValidationError

from core.models import AcademicYear, Campus, Student, Tenant, User
from .models import SchoolClass, StudentEnrollment


class ProvenanceBusy(APIException):
    status_code = 409
    default_detail = "Report provenance is being changed. Please retry."
    default_code = "provenance_busy"


class _ProvenanceLockConflict(Exception):
    pass


def retry_provenance_conflicts(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        for attempt in range(3):
            try:
                return view(*args, **kwargs)
            except _ProvenanceLockConflict as exc:
                if attempt == 2:
                    raise ProvenanceBusy() from exc
                sleep(0.05 * (attempt + 1))
    return wrapped


def _lock_audit_references(tenant_id, user_id):
    if connection.vendor != "postgresql":
        return
    # Take deferred audit FK locks before provenance rows to avoid a commit-time inversion.
    with connection.cursor() as cursor:
        for model, pk in ((Tenant, tenant_id), (User, user_id)):
            cursor.execute(
                f"SELECT id FROM {connection.ops.quote_name(model._meta.db_table)} "
                "WHERE id = %s FOR KEY SHARE NOWAIT",
                [pk],
            )
            if cursor.fetchone() is None:
                raise ValidationError({"detail": "The school or reviewer no longer exists."})


def lock_report_campus(tenant_id, term, enrollment_id, user_id, student_id=None):
    try:
        return _lock_report_campus(tenant_id, term, enrollment_id, user_id, student_id)
    except DatabaseError as exc:
        cause = exc.__cause__
        if getattr(cause, "sqlstate", getattr(cause, "pgcode", None)) == "55P03":
            raise _ProvenanceLockConflict() from exc
        raise


def _lock_report_campus(tenant_id, term, enrollment_id, user_id, student_id):
    _lock_audit_references(tenant_id, user_id)
    initial = StudentEnrollment.objects.filter(pk=enrollment_id, tenant_id=tenant_id).values("student_id").first()
    if initial is None or (student_id is not None and initial["student_id"] != student_id):
        raise ValidationError({"enrollment": "The report enrollment is invalid."})
    # FOR UPDATE fences concurrent enrollment FK checks; NOWAIT avoids row-lock inversions.
    student = Student.objects.select_for_update(nowait=True).filter(
        pk=initial["student_id"], tenant_id=tenant_id,
    ).first()
    if student is None:
        raise ValidationError({"enrollment": "The report student does not belong to this school."})
    enrollments = list(StudentEnrollment.objects.select_for_update(nowait=True).filter(
        student_id=student.pk,
    ).order_by("pk"))
    classes = {
        row.pk: row for row in SchoolClass.objects.select_for_update(nowait=True).filter(
            pk__in=[enrollment.school_class_id for enrollment in enrollments],
        ).order_by("pk")
    }
    year = AcademicYear.objects.select_for_update(nowait=True).filter(
        pk=term.academic_year_id, tenant_id=tenant_id,
    ).first()
    if year is None:
        raise ValidationError({"term": "The term has an invalid academic year."})
    relevant = [
        row for row in enrollments
        if row.school_class_id in classes
        and classes[row.school_class_id].academic_year_id == year.pk
        and row.enrolled_on <= term.end_date
        and (row.ended_on is None or row.ended_on >= term.start_date)
    ]
    if len(relevant) != 1 or relevant[0].pk != enrollment_id:
        raise ValidationError({"enrollment": "Enrollment history is missing or has multiple term enrollments; manual reconciliation is required."})
    enrollment = relevant[0]
    school_class = classes[enrollment.school_class_id]
    if enrollment.tenant_id != tenant_id or school_class.tenant_id != tenant_id:
        raise ValidationError({"enrollment": "The enrollment or class belongs to another school."})
    if not enrollment.campus_id or not school_class.campus_id or enrollment.campus_id != school_class.campus_id:
        raise ValidationError({"enrollment": "Enrollment and class campus evidence is missing or conflicting."})
    campus = Campus.objects.select_for_update(nowait=True).filter(
        pk=enrollment.campus_id, tenant_id=tenant_id,
    ).first()
    if campus is None:
        raise ValidationError({"enrollment": "The campus does not belong to this school."})
    return {
        "term": str(term.pk), "year": str(year.pk), "student": str(student.pk),
        "enrollment": str(enrollment.pk), "class": str(school_class.pk), "campus": str(campus.pk),
    }
