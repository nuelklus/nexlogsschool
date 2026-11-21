from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership
from core.models import Student

from .campus import validate_same_campus
from .class_subjects import class_subject_ids
from .models import AcademicTerm, AssessmentResult, AttendanceRecord, SchoolClass, StudentEnrollment, TeachingAssignment
from .views import MANAGE_ROLES, StudentEnrollmentSerializer


class TransferRequestSerializer(serializers.Serializer):
    enrollment = serializers.UUIDField()
    target_class = serializers.UUIDField()
    effective_date = serializers.DateField()
    reason = serializers.CharField(max_length=240, trim_whitespace=True)
    subjects = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)


class ClassTransferView(APIView):
    def post(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        data = TransferRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        initial = StudentEnrollment.objects.filter(
            pk=values["enrollment"], tenant_id=tenant_id
        ).values("student_id", "school_class__academic_year_id").first()
        if initial is None:
            raise NotFound()

        try:
            with transaction.atomic():
                # Term closure locks the term as well, so it cannot complete between
                # the closed-term check and the enrollment change.
                terms = list(AcademicTerm.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    academic_year_id=initial["school_class__academic_year_id"],
                    end_date__gte=values["effective_date"],
                ).order_by("pk"))
                student = Student.objects.select_for_update().get(pk=initial["student_id"], tenant_id=tenant_id)
                source = StudentEnrollment.objects.select_for_update().select_related(
                    "school_class__academic_year"
                ).filter(pk=values["enrollment"], tenant_id=tenant_id, student=student).first()
                target = SchoolClass.objects.select_for_update().filter(pk=values["target_class"], tenant_id=tenant_id).first()
                if source is None or target is None:
                    raise NotFound()
                year = source.school_class.academic_year
                day = values["effective_date"]
                if student.status != Student.Status.ACTIVE or source.status != StudentEnrollment.Status.ACTIVE:
                    raise ValidationError({"enrollment": "Choose an active student and active enrollment."})
                if not year.is_current:
                    raise ValidationError({"enrollment": "Transfers are only supported in the current academic year."})
                if target.academic_year_id != year.pk or target.pk == source.school_class_id:
                    raise ValidationError({"target_class": "Choose a different class in the same academic year."})
                if not target.is_active:
                    raise ValidationError({"target_class": "The destination class is archived."})
                validate_same_campus(tenant_id, student, source, target, "target_class")
                if not year.start_date <= day <= year.end_date or day <= source.enrolled_on:
                    raise ValidationError({"effective_date": "The transfer must be within the year and after the enrollment date."})
                if day > timezone.localdate():
                    raise ValidationError({"effective_date": "Future transfers are not supported."})
                if any(term.is_completed for term in terms):
                    raise ValidationError({"effective_date": "This date affects a closed term. Reopen it before transferring."})
                if StudentEnrollment.objects.filter(
                    tenant_id=tenant_id, student=student, school_class__academic_year=year,
                    status=StudentEnrollment.Status.ACTIVE,
                ).exclude(pk=source.pk).exists():
                    raise ValidationError({"enrollment": "Resolve multiple active enrollments before transferring."})
                if StudentEnrollment.objects.filter(student=student, school_class=target).exists():
                    raise ValidationError({"target_class": "A prior enrollment exists in this class; returning to it is not supported yet."})
                subjects = set(values["subjects"])
                assigned = set(TeachingAssignment.objects.filter(
                    tenant_id=tenant_id, school_class=target, subject_id__in=subjects
                ).values_list("subject_id", flat=True))
                if assigned != subjects:
                    raise ValidationError({"subjects": "Select only subjects assigned to the destination class."})
                if subjects and subjects != set(class_subject_ids(tenant_id, target.pk)):
                    raise ValidationError({"subjects": "Students follow all subjects assigned to the destination class; do not select a subset."})
                if AttendanceRecord.objects.filter(enrollment=source, date__gte=day).exists():
                    raise ValidationError({"effective_date": "Attendance exists on or after this date; review it before transferring."})
                if AssessmentResult.objects.filter(enrollment=source, assessment__assessment_date__gte=day).exists():
                    raise ValidationError({"effective_date": "Results exist on or after this date; review them before transferring."})
                source.status = StudentEnrollment.Status.TRANSFERRED
                source.ended_on = day - timedelta(days=1)
                source.save(update_fields=("status", "ended_on"))
                destination = StudentEnrollment.objects.create(
                    tenant_id=tenant_id, student=student,
                    campus=target.campus or student.campus,
                    school_class=target, enrolled_on=day
                )
                if year.is_current:
                    student.grade_level = target.name
                    changed_fields = ["grade_level", "updated_at"]
                    if destination.campus_id:
                        student.campus_id = destination.campus_id
                        changed_fields.append("campus")
                    student.save(update_fields=changed_fields)
                _audit(request, tenant_id, "academic.enrollment.transferred", "StudentEnrollment", destination.pk, {
                    "student": str(student.pk), "previous_enrollment": str(source.pk),
                    "from_class": str(source.school_class_id), "to_class": str(target.pk),
                    "effective_date": day.isoformat(), "reason": values["reason"],
                })
        except IntegrityError:
            raise ValidationError({"target_class": "The destination enrollment already exists."})
        return Response({
            "previous": StudentEnrollmentSerializer(source, context={"tenant_id": tenant_id}).data,
            "enrollment": StudentEnrollmentSerializer(destination, context={"tenant_id": tenant_id}).data,
        }, status=status.HTTP_201_CREATED)
