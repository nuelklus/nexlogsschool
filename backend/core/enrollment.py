import hashlib
import secrets
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .admission_numbers import get_settings, next_admission_number, validate_config
from .models import (
    AcademicYear,
    AdmissionSettings,
    AuditEvent,
    Campus,
    Membership,
    MembershipInvitation,
    Student,
    StudentGuardian,
    Tenant,
    User,
)
from .subscriptions import require_active_subscription

MANAGE_ROLES = (Membership.Role.OWNER, Membership.Role.SCHOOL_ADMIN)
READ_ROLES = MANAGE_ROLES + (Membership.Role.ACCOUNTANT, Membership.Role.TEACHER, Membership.Role.HEADTEACHER)


class StudentSerializer(serializers.ModelSerializer):
    portal_account = serializers.SerializerMethodField()
    grade_level = serializers.SerializerMethodField()
    campus = serializers.SerializerMethodField()
    current_class = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = (
            "id",
            "admission_number",
            "first_name",
            "last_name",
            "date_of_birth",
            "gender",
            "grade_level",
            "current_class",
            "campus",
            "status",
            "enrolled_on",
            "portal_account",
        )
        read_only_fields = ("id", "portal_account")

    def get_current_class(self, student):
        current_year_id = self.context.get("current_year_id")
        if "current_year_id" not in self.context:
            current_year_id = AcademicYear.objects.filter(
                tenant_id=student.tenant_id, is_current=True
            ).values_list("pk", flat=True).first()
        if current_year_id is None:
            return None
        enrollments = getattr(student, "current_year_enrollments", None)
        if enrollments is None:
            from academics.models import StudentEnrollment
            enrollments = list(StudentEnrollment.objects.filter(
                tenant_id=student.tenant_id, student=student,
                school_class__academic_year_id=current_year_id, status=StudentEnrollment.Status.ACTIVE,
            ))
        return str(enrollments[0].school_class_id) if len(enrollments) == 1 else None

    def get_grade_level(self, student):
        current_year_id = self.context.get("current_year_id")
        if current_year_id is None:
            current_year_id = AcademicYear.objects.filter(
                tenant_id=student.tenant_id, is_current=True
            ).values_list("pk", flat=True).first()
        if current_year_id is not None:
            enrollments = getattr(student, "current_year_enrollments", None)
            if enrollments is None:
                from academics.models import StudentEnrollment

                enrollments = list(
                    StudentEnrollment.objects.filter(
                        tenant_id=student.tenant_id,
                        student=student,
                        school_class__academic_year_id=current_year_id,
                        status=StudentEnrollment.Status.ACTIVE,
                    ).select_related("school_class")
                )
            if len(enrollments) == 1:
                return enrollments[0].school_class.name
        return student.grade_level

    def get_campus(self, student):
        current_year_id = self.context.get("current_year_id")
        if current_year_id is None:
            current_year_id = AcademicYear.objects.filter(
                tenant_id=student.tenant_id, is_current=True
            ).values_list("pk", flat=True).first()
        if current_year_id is not None:
            enrollments = getattr(student, "current_year_enrollments", None)
            if enrollments is None:
                from academics.models import StudentEnrollment

                enrollments = list(
                    StudentEnrollment.objects.filter(
                        tenant_id=student.tenant_id,
                        student=student,
                        school_class__academic_year_id=current_year_id,
                        status=StudentEnrollment.Status.ACTIVE,
                    ).select_related("campus", "school_class__campus")
                )
            if len(enrollments) == 1:
                enrollment = enrollments[0]
                campus_id = enrollment.campus_id or enrollment.school_class.campus_id or student.campus_id
                return str(campus_id) if campus_id else None
        return str(student.campus_id) if student.campus_id else None

    def get_portal_account(self, student):
        return student.user_id is not None


class StudentCreateSerializer(StudentSerializer):
    initial_academic_year = serializers.UUIDField(write_only=True)
    initial_school_class = serializers.UUIDField(write_only=True)
    campus = serializers.PrimaryKeyRelatedField(
        queryset=Campus.objects.none(), allow_null=True, required=False
    )

    class Meta(StudentSerializer.Meta):
        # The admission number is always generated by the server.
        read_only_fields = ("id", "admission_number", "portal_account", "status", "grade_level")
        fields = StudentSerializer.Meta.fields + ("initial_academic_year", "initial_school_class")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant_id = self.context["tenant_id"]
        self.fields["campus"].queryset = Campus.objects.filter(
            tenant_id=tenant_id, status=Campus.Status.ACTIVE
        )

    def create(self, validated_data):
        validated_data.pop("initial_academic_year")
        validated_data.pop("initial_school_class")
        return super().create(validated_data)


class StudentUpdateSerializer(StudentSerializer):
    def validate_admission_number(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter the admission number.")
        return value


class AdmissionSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdmissionSettings
        fields = ("prefix", "number_format", "padding", "reset_policy", "year_policy")

    def validate_prefix(self, value):
        return value.strip().upper()

    def validate(self, attrs):
        merged = {
            field: attrs.get(field, getattr(self.instance, field))
            for field in self.Meta.fields
        }
        errors = validate_config(
            prefix=merged["prefix"],
            number_format=merged["number_format"],
            padding=merged["padding"],
            reset_policy=merged["reset_policy"],
            year_policy=merged["year_policy"],
        )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class GuardianInviteSerializer(serializers.Serializer):
    email = serializers.EmailField()
    relationship = serializers.CharField(max_length=40, required=False, allow_blank=True)
    is_primary = serializers.BooleanField(required=False, default=False)

    def validate_email(self, value):
        return value.strip().lower()


class PortalInviteSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


def _membership(user, tenant_id, roles):
    membership = Membership.objects.filter(
        tenant_id=tenant_id,
        user=user,
        role__in=roles,
        status=Membership.Status.ACTIVE,
        tenant__status=Tenant.Status.ACTIVE,
        user__is_active=True,
    ).first()
    # Checked only after membership so outsiders cannot probe subscription state.
    if membership is not None:
        require_active_subscription(tenant_id)
    return membership


def _not_found():
    return Response(
        {"detail": "Not found."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _audit(request, tenant_id, action, entity_type, entity_id, metadata=None):
    AuditEvent.objects.create(
        tenant_id=tenant_id,
        actor=request.user,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        metadata=metadata or {},
        ip_address=request.META.get("REMOTE_ADDR") or None,
        user_agent=request.headers.get("User-Agent", "")[:240],
    )


def _send_invitation(tenant, student, email, role, token):
    accept_url = (
        f"{settings.NLSCHOOL_FRONTEND_URL.rstrip('/')}"
        f"/invitations/accept?token={quote(token)}"
    )
    if role == MembershipInvitation.Role.PARENT:
        purpose = f"to follow {student.first_name} {student.last_name} as a parent or guardian"
    else:
        purpose = "to activate your student portal"
    sent = send_mail(
        subject=f"Join {tenant.name} on NLSchool",
        message=(
            f"{tenant.name} has invited you {purpose}.\n\n"
            f"Accept the invitation within 7 days: {accept_url}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
    if sent != 1:
        raise RuntimeError("The invitation email was not sent.")


def _create_student_invitation(request, tenant, student, email, role, **extra):
    token = secrets.token_urlsafe(32)
    invitation = MembershipInvitation.objects.create(
        tenant=tenant,
        student=student,
        email=email,
        role=role,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        invited_by=request.user,
        expires_at=timezone.now() + timedelta(days=7),
        **extra,
    )
    _send_invitation(tenant, student, email, role, token)
    _audit(
        request,
        tenant.pk,
        "membership.invitation_created",
        "MembershipInvitation",
        invitation.pk,
        {"role": role, "student_id": str(student.pk)},
    )
    return invitation


def _has_current_invitation(tenant, student, email, role):
    return MembershipInvitation.objects.filter(
        tenant=tenant,
        student=student,
        email__iexact=email,
        role=role,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()


class StudentListCreateView(APIView):
    def get(self, request, tenant_id):
        if _membership(request.user, tenant_id, READ_ROLES) is None:
            return _not_found()
        students = Student.objects.filter(tenant_id=tenant_id).select_related("campus")
        current_year = AcademicYear.objects.filter(
            tenant_id=tenant_id, is_current=True
        ).first()
        if current_year is not None:
            from academics.models import StudentEnrollment

            students = students.prefetch_related(
                Prefetch(
                    "academic_enrollments",
                    queryset=StudentEnrollment.objects.filter(
                        tenant_id=tenant_id,
                        school_class__academic_year=current_year,
                        status=StudentEnrollment.Status.ACTIVE,
                    ).select_related("school_class__campus", "campus"),
                    to_attr="current_year_enrollments",
                )
            )
        status_filter = request.query_params.get("status")
        if status_filter in Student.Status.values:
            students = students.filter(status=status_filter)
        return Response(StudentSerializer(
            students, many=True, context={"current_year_id": current_year.pk if current_year else None}
        ).data)

    def post(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            return _not_found()
        serializer = StudentCreateSerializer(data=request.data, context={"tenant_id": tenant_id})
        serializer.is_valid(raise_exception=True)
        enrolled_on = serializer.validated_data.get("enrolled_on") or timezone.localdate()
        from academics.models import SchoolClass, StudentEnrollment

        try:
            with transaction.atomic():
                year = AcademicYear.objects.select_for_update().filter(
                    pk=serializer.validated_data["initial_academic_year"],
                    tenant_id=tenant_id,
                ).first()
                school_class = SchoolClass.objects.select_for_update().filter(
                    pk=serializer.validated_data["initial_school_class"],
                    tenant_id=tenant_id,
                ).first()
                if year is None:
                    raise serializers.ValidationError(
                        {"initial_academic_year": "Choose an academic year in this school."}
                    )
                if school_class is None:
                    raise serializers.ValidationError(
                        {"initial_school_class": "Choose a class group in this school."}
                    )
                if school_class.academic_year_id != year.pk:
                    raise serializers.ValidationError(
                        {"initial_school_class": "Choose a class group from the selected academic year."}
                    )
                if not year.start_date <= enrolled_on <= year.end_date:
                    raise serializers.ValidationError(
                        {"enrolled_on": "The enrollment date must be within the academic year."}
                    )
                if year.status != "active" or not school_class.is_active:
                    raise serializers.ValidationError(
                        {"initial_school_class": "Choose an active academic year and class group."}
                    )
                campus = serializer.validated_data.get("campus")
                if campus and school_class.campus_id and campus.pk != school_class.campus_id:
                    raise serializers.ValidationError(
                        {"campus": "The selected campus does not match the class group."}
                    )
                if school_class.campus_id and campus is None:
                    campus = school_class.campus
                number = next_admission_number(tenant_id, enrolled_on)
                student = serializer.save(
                    tenant_id=tenant_id,
                    admission_number=number,
                    campus=campus,
                    grade_level=school_class.name if year.is_current else "",
                )
                enrollment = StudentEnrollment.objects.create(
                    tenant_id=tenant_id,
                    student=student,
                    campus=campus,
                    school_class=school_class,
                    enrolled_on=enrolled_on,
                )
                _audit(
                    request, tenant_id, "student.enrolled", "Student", student.pk,
                    {
                        "admission_number": number,
                        "generated": True,
                        "academic_year": str(year.pk),
                        "school_class": str(school_class.pk),
                    },
                )
                _audit(
                    request, tenant_id, "academic.enrollments.created", "StudentEnrollment", enrollment.pk,
                    {"student": str(student.pk), "school_class": str(school_class.pk), "initial_admission": True},
                )
        except IntegrityError:
            return Response(
                {"detail": "The admission number could not be reserved. Please try again."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            StudentSerializer(student, context={"current_year_id": year.pk if year.is_current else None}).data,
            status=status.HTTP_201_CREATED,
        )


class StudentDetailView(APIView):
    def get(self, request, tenant_id, student_id):
        student = Student.objects.filter(pk=student_id, tenant_id=tenant_id).first()
        if student is None:
            return _not_found()

        if _membership(request.user, tenant_id, READ_ROLES) is not None:
            allowed = True
        elif _membership(request.user, tenant_id, (Membership.Role.PARENT,)) is not None:
            allowed = StudentGuardian.objects.filter(
                student=student, user=request.user
            ).exists()
        elif _membership(request.user, tenant_id, (Membership.Role.STUDENT,)) is not None:
            allowed = student.user_id == request.user.pk
        else:
            allowed = False
        if not allowed:
            return _not_found()
        return Response(StudentSerializer(student).data)

    def patch(self, request, tenant_id, student_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            return _not_found()
        student = Student.objects.filter(pk=student_id, tenant_id=tenant_id).first()
        if student is None:
            return _not_found()
        previous_number = student.admission_number
        serializer = StudentUpdateSerializer(student, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                serializer.save()
                _audit(request, tenant_id, "student.updated", "Student", student.pk)
                if student.admission_number != previous_number:
                    _audit(
                        request, tenant_id, "student.admission_number_overridden", "Student", student.pk,
                        {"previous": previous_number, "new": student.admission_number},
                    )
        except IntegrityError:
            return Response(
                {"admission_number": "This admission number is already in use."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(serializer.data)


class AdmissionSettingsView(APIView):
    def get(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            return _not_found()
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
            settings = get_settings(tenant)
        return Response(AdmissionSettingsSerializer(settings).data)

    def patch(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            return _not_found()
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
            settings = get_settings(tenant)
            previous = AdmissionSettingsSerializer(settings).data
            serializer = AdmissionSettingsSerializer(settings, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            _audit(
                request, tenant_id, "admission_settings.updated", "Tenant", tenant_id,
                {"previous": dict(previous), "new": dict(serializer.data)},
            )
        return Response(serializer.data)


class GuardianInviteView(APIView):
    def post(self, request, tenant_id, student_id):
        serializer = GuardianInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        relationship = serializer.validated_data.get("relationship", "")
        is_primary = serializer.validated_data["is_primary"]

        with transaction.atomic():
            if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
                return _not_found()
            student = (
                Student.objects.select_for_update()
                .select_related("tenant")
                .filter(pk=student_id, tenant_id=tenant_id)
                .first()
            )
            if student is None:
                return _not_found()

            user = User.objects.filter(email__iexact=email).first()
            if user is not None:
                if StudentGuardian.objects.filter(student=student, user=user).exists():
                    return Response(
                        {"detail": "This guardian is already linked to the student."},
                        status=status.HTTP_409_CONFLICT,
                    )
                existing = Membership.objects.filter(tenant_id=tenant_id, user=user).first()
                if existing is not None:
                    # A verified parent already in this school is linked directly.
                    if (
                        existing.role == Membership.Role.PARENT
                        and existing.status == Membership.Status.ACTIVE
                        and user.is_active
                        and user.email_verified
                    ):
                        link = StudentGuardian.objects.create(
                            tenant_id=tenant_id,
                            student=student,
                            user=user,
                            relationship=relationship,
                            is_primary=is_primary,
                        )
                        _audit(
                            request,
                            tenant_id,
                            "student.guardian_linked",
                            "StudentGuardian",
                            link.pk,
                            {"student_id": str(student.pk)},
                        )
                        return Response(
                            {"status": "linked", "email": email},
                            status=status.HTTP_201_CREATED,
                        )
                    return Response(
                        {"detail": "This account already has another role in the school."},
                        status=status.HTTP_409_CONFLICT,
                    )

            if _has_current_invitation(
                student.tenant, student, email, MembershipInvitation.Role.PARENT
            ):
                return Response(
                    {"detail": "A current invitation already exists for this email."},
                    status=status.HTTP_409_CONFLICT,
                )
            invitation = _create_student_invitation(
                request,
                student.tenant,
                student,
                email,
                MembershipInvitation.Role.PARENT,
                guardian_relationship=relationship,
                guardian_is_primary=is_primary,
            )

        return Response(
            {
                "status": "invited",
                "email": email,
                "expires_at": invitation.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class GuardianDetailView(APIView):
    def delete(self, request, tenant_id, student_id, link_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            return _not_found()
        with transaction.atomic():
            link = StudentGuardian.objects.filter(
                pk=link_id, student_id=student_id, tenant_id=tenant_id
            ).first()
            if link is None:
                return _not_found()
            link.delete()
            _audit(
                request,
                tenant_id,
                "student.guardian_unlinked",
                "StudentGuardian",
                link_id,
                {"student_id": str(student_id)},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class StudentPortalInviteView(APIView):
    def post(self, request, tenant_id, student_id):
        serializer = PortalInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        with transaction.atomic():
            if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
                return _not_found()
            student = (
                Student.objects.select_for_update()
                .select_related("tenant")
                .filter(pk=student_id, tenant_id=tenant_id)
                .first()
            )
            if student is None:
                return _not_found()
            if student.status != Student.Status.ACTIVE:
                return Response(
                    {"detail": "Only active students can have a portal account."},
                    status=status.HTTP_409_CONFLICT,
                )
            if student.user_id is not None:
                return Response(
                    {"detail": "This student already has a portal account."},
                    status=status.HTTP_409_CONFLICT,
                )
            existing_user = User.objects.filter(email__iexact=email).first()
            if existing_user is not None and Membership.objects.filter(
                tenant_id=tenant_id, user=existing_user
            ).exists():
                return Response(
                    {"detail": "This account is already a member of the school."},
                    status=status.HTTP_409_CONFLICT,
                )
            if MembershipInvitation.objects.filter(
                tenant=student.tenant,
                student=student,
                role=MembershipInvitation.Role.STUDENT,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).exists():
                return Response(
                    {"detail": "A current portal invitation already exists for this student."},
                    status=status.HTTP_409_CONFLICT,
                )
            invitation = _create_student_invitation(
                request,
                student.tenant,
                student,
                email,
                MembershipInvitation.Role.STUDENT,
            )

        return Response(
            {
                "status": "invited",
                "email": email,
                "expires_at": invitation.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class MyChildrenView(APIView):
    def get(self, request, tenant_id):
        if _membership(request.user, tenant_id, (Membership.Role.PARENT,)) is None:
            return _not_found()
        links = (
            StudentGuardian.objects.filter(tenant_id=tenant_id, user=request.user)
            .select_related("student")
            .order_by("student__last_name", "student__first_name")
        )
        return Response(
            [
                {
                    **StudentSerializer(link.student).data,
                    "relationship": link.relationship,
                    "is_primary": link.is_primary,
                }
                for link in links
            ]
        )


class MyStudentProfileView(APIView):
    def get(self, request, tenant_id):
        if _membership(request.user, tenant_id, (Membership.Role.STUDENT,)) is None:
            return _not_found()
        student = Student.objects.filter(tenant_id=tenant_id, user=request.user).first()
        if student is None:
            return _not_found()
        return Response(StudentSerializer(student).data)
