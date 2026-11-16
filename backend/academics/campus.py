from rest_framework.exceptions import ValidationError

from core.models import Campus


def validate_same_campus(tenant_id, student, source, target, field):
    campus_ids = (student.campus_id, source.campus_id, source.school_class.campus_id, target.campus_id)
    if any(campus_id is None for campus_id in campus_ids):
        raise ValidationError({
            field: "Campus evidence is missing; reconcile the student, enrollment and both classes before moving."
        })
    if len(set(campus_ids)) != 1:
        raise ValidationError({field: "Campus evidence conflicts; cross-campus moves are not supported."})
    if not Campus.objects.filter(pk=target.campus_id, tenant_id=tenant_id).exists():
        raise ValidationError({field: "The campus does not belong to this school."})
