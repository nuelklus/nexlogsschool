from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership
from core.models import Membership, Student

from .models import AcademicTerm, AttendanceRecord, SchoolClass, StudentEnrollment, TeachingAssignment
from .views import MANAGE_ROLES, READ_ROLES


class RegisterEntrySerializer(serializers.Serializer):
    enrollment = serializers.UUIDField()
    status = serializers.ChoiceField(choices=AttendanceRecord.Status.choices)
    notes = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class RegisterSerializer(serializers.Serializer):
    school_class = serializers.UUIDField()
    date = serializers.DateField()
    entries = RegisterEntrySerializer(many=True, allow_empty=False)

    def validate_entries(self, entries):
        ids = [entry["enrollment"] for entry in entries]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each enrollment can appear only once.")
        return entries


class ClassAttendanceView(APIView):
    def post(self, request, tenant_id):
        membership = _membership(request.user, tenant_id, READ_ROLES)
        if membership is None or membership.role not in (*MANAGE_ROLES, Membership.Role.TEACHER):
            raise NotFound()
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        school_class = SchoolClass.objects.filter(
            pk=values["school_class"], tenant_id=tenant_id
        ).select_related("academic_year").first()
        if school_class is None:
            raise NotFound()
        if membership.role == Membership.Role.TEACHER and not TeachingAssignment.objects.filter(
            tenant_id=tenant_id, school_class=school_class, teacher=request.user
        ).exists():
            raise NotFound()
        day = values["date"]
        if not school_class.academic_year.start_date <= day <= school_class.academic_year.end_date:
            raise ValidationError({"date": "The date must be within the class's academic year."})
        ids = [entry["enrollment"] for entry in values["entries"]]

        try:
            with transaction.atomic():
                terms = list(AcademicTerm.objects.select_for_update().filter(
                    tenant_id=tenant_id, academic_year=school_class.academic_year,
                    start_date__lte=day, end_date__gte=day,
                ).order_by("pk"))
                if any(term.is_completed for term in terms):
                    raise ValidationError({"date": "This term is closed. Reopen it before recording attendance."})
                student_ids = list(StudentEnrollment.objects.filter(
                    tenant_id=tenant_id, pk__in=ids
                ).values_list("student_id", flat=True))
                list(Student.objects.select_for_update().filter(
                    tenant_id=tenant_id, pk__in=student_ids
                ).order_by("pk"))
                enrollments = {
                    item.pk: item for item in StudentEnrollment.objects.filter(
                        tenant_id=tenant_id, pk__in=ids, school_class=school_class,
                        status__in=(StudentEnrollment.Status.ACTIVE, StudentEnrollment.Status.TRANSFERRED),
                        enrolled_on__lte=day,
                    ).filter(Q(ended_on__isnull=True) | Q(ended_on__gte=day))
                }
                if len(enrollments) != len(ids):
                    raise ValidationError({"entries": "Every student must belong to this class on the selected date."})
                if AttendanceRecord.objects.filter(
                    tenant_id=tenant_id, enrollment_id__in=ids, date=day
                ).exists():
                    return Response(
                        {"detail": "Attendance was already recorded for one or more students. Reload the register; existing records were not changed."},
                        status=status.HTTP_409_CONFLICT,
                    )
                for entry in values["entries"]:
                    record = AttendanceRecord.objects.create(
                        tenant_id=tenant_id, enrollment=enrollments[entry["enrollment"]],
                        date=day, status=entry["status"], notes=entry["notes"], recorded_by=request.user,
                    )
                    _audit(request, tenant_id, "academic.attendance.created", "AttendanceRecord", record.pk)
        except IntegrityError:
            return Response(
                {"detail": "Attendance was recorded concurrently. Reload the register; no changes were saved."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"created": len(ids)}, status=status.HTTP_201_CREATED)
