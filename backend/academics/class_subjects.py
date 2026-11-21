from .models import TeachingAssignment


def class_subject_ids(tenant_id, school_class_id):
    return TeachingAssignment.objects.filter(
        tenant_id=tenant_id,
        school_class_id=school_class_id,
        school_class__tenant_id=tenant_id,
        subject__tenant_id=tenant_id,
    ).values_list("subject_id", flat=True).distinct()
