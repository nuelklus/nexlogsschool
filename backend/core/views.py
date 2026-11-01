import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import (
    AuditEvent,
    EmailActionToken,
    Membership,
    MembershipInvitation,
    PlatformSetupState,
    Student,
    StudentGuardian,
    SubscriptionPlan,
    Tenant,
    TenantSubscription,
    User,
)
from .serializers import (
    InitialSchoolSetupSerializer,
    StaffInvitationAcceptanceSerializer,
    StaffInvitationSerializer,
)
from .auth_email import send_email_action
from .subscriptions import TRIAL_PLAN_CODE, require_active_subscription


STAFF_INVITATION_ROLES = (
    MembershipInvitation.Role.SCHOOL_ADMIN,
    MembershipInvitation.Role.ACCOUNTANT,
    MembershipInvitation.Role.TEACHER,
)


def start_trial_subscription(tenant):
    plan, _ = SubscriptionPlan.objects.get_or_create(
        code=TRIAL_PLAN_CODE,
        defaults={"name": "Free trial", "price": 0, "trial_days": 30},
    )
    now = timezone.now()
    return TenantSubscription.objects.create(
        tenant=tenant,
        plan=plan,
        status=TenantSubscription.Status.TRIALING,
        trial_started_at=now,
        trial_ends_at=now + timedelta(days=plan.trial_days),
    )


def health(request):
    return JsonResponse({"status": "ok", "service": "nlschool-api"})


def create_school_with_owner(data, audit_action):
    school_slug = data["school_slug"]
    if Tenant.objects.filter(slug=school_slug).exists():
        suffix = Tenant.objects.count() + 1
        school_slug = f"{school_slug[: 79 - len(str(suffix))]}-{suffix}"

    tenant = Tenant.objects.create(
        name=data["school_name"],
        slug=school_slug,
        email=data["school_email"],
        phone=data["school_phone"],
        country=data["country"],
        region=data.get("region", ""),
        address=data.get("address", ""),
        school_type=data.get("school_type", ""),
    )
    owner = User.objects.create_user(
        email=data["owner_email"],
        password=data["owner_password"],
        full_name=data["owner_name"],
    )
    Membership.objects.create(
        tenant=tenant,
        user=owner,
        role=Membership.Role.OWNER,
    )
    start_trial_subscription(tenant)
    AuditEvent.objects.create(
        tenant=tenant,
        actor=owner,
        action=audit_action,
        entity_type="Tenant",
        entity_id=str(tenant.pk),
    )
    send_email_action(owner, EmailActionToken.Purpose.VERIFY_EMAIL)
    return tenant, owner


class InitialSchoolSetupView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        setup_state = PlatformSetupState.objects.get(pk=1)
        enabled = self._setup_enabled()
        available = enabled and not setup_state.initial_setup_completed and not Tenant.objects.exists()
        reason = None
        if not enabled:
            reason = "disabled"
        elif not available:
            reason = "completed"
        return Response({"available": available, "reason": reason})

    def post(self, request):
        if not self._setup_enabled() or not self._valid_setup_token(request):
            return Response(
                {"detail": "School setup is disabled or the setup token is invalid."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if (
            not PlatformSetupState.objects.filter(
                pk=1,
                initial_setup_completed=False,
            ).exists()
            or Tenant.objects.exists()
        ):
            return Response(
                {"detail": "Initial school setup has already been completed."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = InitialSchoolSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            claimed = PlatformSetupState.objects.filter(
                pk=1,
                initial_setup_completed=False,
            ).update(initial_setup_completed=True)
            if not claimed or Tenant.objects.exists():
                return Response(
                    {"detail": "Initial school setup has already been completed."},
                    status=status.HTTP_409_CONFLICT,
                )

            tenant, owner = create_school_with_owner(
                data, "school.initial_setup_completed"
            )

        return Response(
            {
                "school": {
                    "id": str(tenant.pk),
                    "name": tenant.name,
                    "slug": tenant.slug,
                },
                "owner": {
                    "email": owner.email,
                    "name": owner.full_name,
                    "email_verified": owner.email_verified,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _setup_enabled():
        return bool(settings.NLSCHOOL_SETUP_TOKEN) or settings.DEBUG

    @staticmethod
    def _valid_setup_token(request):
        expected_token = settings.NLSCHOOL_SETUP_TOKEN
        if expected_token:
            provided_token = request.headers.get("X-NLSchool-Setup-Token", "")
            return hmac.compare_digest(provided_token, expected_token)
        return settings.DEBUG


class SchoolRegistrationView(APIView):
    """Public self-service sign-up; the owner must verify their email before signing in."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_email"

    def post(self, request):
        serializer = InitialSchoolSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            tenant, owner = create_school_with_owner(
                serializer.validated_data, "school.self_registered"
            )
        return Response(
            {"school": {"name": tenant.name}, "owner": {"email": owner.email}},
            status=status.HTTP_201_CREATED,
        )


class StaffInvitationView(APIView):
    def post(self, request, tenant_id):
        serializer = StaffInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        role = serializer.validated_data["role"]
        now = timezone.now()

        with transaction.atomic():
            tenant = (
                Tenant.objects.select_for_update()
                .filter(pk=tenant_id, status=Tenant.Status.ACTIVE)
                .first()
            )
            if tenant is None:
                return Response(
                    {"detail": "School not found or you cannot invite staff there."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            membership = (
                Membership.objects.filter(
                    tenant=tenant,
                    user=request.user,
                    role__in=(Membership.Role.OWNER, Membership.Role.SCHOOL_ADMIN),
                    status=Membership.Status.ACTIVE,
                    user__is_active=True,
                )
                .first()
            )
            if membership is None:
                return Response(
                    {"detail": "School not found or you cannot invite staff there."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            require_active_subscription(tenant_id)
            invited_user = User.objects.filter(email__iexact=email).first()
            if invited_user and Membership.objects.filter(
                tenant=tenant,
                user=invited_user,
            ).exists():
                return Response(
                    {"detail": "This account is already a member of the school."},
                    status=status.HTTP_409_CONFLICT,
                )

            if MembershipInvitation.objects.filter(
                tenant=tenant,
                email__iexact=email,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gt=now,
            ).exists():
                return Response(
                    {"detail": "A current invitation already exists for this email."},
                    status=status.HTTP_409_CONFLICT,
                )

            token = secrets.token_urlsafe(32)
            invitation = MembershipInvitation.objects.create(
                tenant=tenant,
                email=email,
                role=role,
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                invited_by=request.user,
                expires_at=now + timedelta(days=7),
            )
            accept_url = (
                f"{settings.NLSCHOOL_FRONTEND_URL.rstrip('/')}"
                f"/invitations/accept?token={quote(token)}"
            )
            role_label = dict(MembershipInvitation.Role.choices)[role]
            sent = send_mail(
                subject=f"Join {tenant.name} on NLSchool",
                message=(
                    f"You have been invited to join {tenant.name} as {role_label}.\n\n"
                    f"Accept the invitation within 7 days: {accept_url}\n"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            if sent != 1:
                raise RuntimeError("The staff invitation email was not sent.")

            AuditEvent.objects.create(
                tenant=tenant,
                actor=request.user,
                action="membership.invitation_created",
                entity_type="MembershipInvitation",
                entity_id=str(invitation.pk),
                metadata={"role": role},
                ip_address=request.META.get("REMOTE_ADDR") or None,
                user_agent=request.headers.get("User-Agent", "")[:240],
            )

        return Response(
            {
                "id": str(invitation.pk),
                "email": invitation.email,
                "role": invitation.role,
                "expires_at": invitation.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class AcceptStaffInvitationView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @staticmethod
    def _role_is_acceptable(invitation):
        if invitation.role in STAFF_INVITATION_ROLES:
            return True
        # Parent and student invitations must be tied to a student record.
        return invitation.student_id is not None and invitation.role in (
            MembershipInvitation.Role.PARENT,
            MembershipInvitation.Role.STUDENT,
        )

    def post(self, request):
        serializer = StaffInvitationAcceptanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token_hash = hashlib.sha256(
            serializer.validated_data["token"].encode("utf-8")
        ).hexdigest()
        now = timezone.now()

        with transaction.atomic():
            invitation = (
                MembershipInvitation.objects.select_for_update()
                .select_related("tenant")
                .filter(token_hash=token_hash)
                .first()
            )
            if (
                invitation is None
                or invitation.accepted_at is not None
                or invitation.revoked_at is not None
                or invitation.expires_at <= now
                or not self._role_is_acceptable(invitation)
                or invitation.tenant.status != Tenant.Status.ACTIVE
            ):
                return Response(
                    {"detail": "This staff invitation is invalid or no longer active."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            student = None
            if invitation.student_id:
                student = Student.objects.select_for_update().get(pk=invitation.student_id)
                if invitation.role == MembershipInvitation.Role.STUDENT and student.user_id:
                    return Response(
                        {"detail": "This student already has a portal account."},
                        status=status.HTTP_409_CONFLICT,
                    )

            user = (
                User.objects.select_for_update()
                .filter(email__iexact=invitation.email)
                .first()
            )
            if user is not None and not user.is_active:
                return Response(
                    {"detail": "This account is inactive. Contact the school administrator."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            existing_membership = (
                Membership.objects.filter(tenant=invitation.tenant, user=user).first()
                if user is not None
                else None
            )
            # A parent with several children keeps one membership per school.
            reuse_membership = (
                existing_membership is not None
                and invitation.role == MembershipInvitation.Role.PARENT
                and existing_membership.role == Membership.Role.PARENT
                and existing_membership.status == Membership.Status.ACTIVE
            )
            if existing_membership is not None and not reuse_membership:
                return Response(
                    {"detail": "This account is already a member of the school."},
                    status=status.HTTP_409_CONFLICT,
                )

            password = serializer.validated_data.get("password", "")
            if user is not None and user.has_usable_password():
                if not password or not user.check_password(password):
                    return Response(
                        {"password": "Enter the existing account password to accept this invitation."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                full_name = serializer.validated_data.get("full_name", "").strip()
                if not full_name:
                    return Response(
                        {"full_name": "Enter your full name."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not password:
                    return Response(
                        {"password": "Choose a password to activate your account."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    validate_password(
                        password,
                        user=User(email=invitation.email, full_name=full_name),
                    )
                except DjangoValidationError as error:
                    return Response(
                        {"password": error.messages},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if user is None:
                    user = User.objects.create_user(
                        email=invitation.email,
                        password=password,
                        full_name=full_name,
                    )
                else:
                    user.set_password(password)
                    if not user.full_name:
                        user.full_name = full_name
                    user.save(update_fields=("password", "full_name"))

            if not user.email_verified:
                user.email_verified = True
                user.save(update_fields=("email_verified",))

            membership = existing_membership if reuse_membership else Membership.objects.create(
                tenant=invitation.tenant,
                user=user,
                role=invitation.role,
            )
            if student is not None:
                if invitation.role == MembershipInvitation.Role.PARENT:
                    StudentGuardian.objects.get_or_create(
                        student=student,
                        user=user,
                        defaults={
                            "tenant": invitation.tenant,
                            "relationship": invitation.guardian_relationship,
                            "is_primary": invitation.guardian_is_primary,
                        },
                    )
                else:
                    student.user = user
                    student.save(update_fields=("user", "updated_at"))
            invitation.accepted_at = now
            invitation.save(update_fields=("accepted_at",))
            AuditEvent.objects.create(
                tenant=invitation.tenant,
                actor=user,
                action="membership.invitation_accepted",
                entity_type="Membership",
                entity_id=str(membership.pk),
                metadata={"role": invitation.role},
                ip_address=request.META.get("REMOTE_ADDR") or None,
                user_agent=request.headers.get("User-Agent", "")[:240],
            )

        return Response(
            {
                "membership": {
                    "id": str(membership.pk),
                    "tenant_id": str(invitation.tenant_id),
                    "role": membership.role,
                },
                "user": {"email": user.email, "name": user.full_name},
            },
            status=status.HTTP_201_CREATED,
        )


class RevokeStaffInvitationView(APIView):
    def delete(self, request, tenant_id, invitation_id):
        membership = Membership.objects.filter(
            tenant_id=tenant_id,
            user=request.user,
            role__in=(Membership.Role.OWNER, Membership.Role.SCHOOL_ADMIN),
            status=Membership.Status.ACTIVE,
            tenant__status=Tenant.Status.ACTIVE,
            user__is_active=True,
        ).first()
        if membership is None:
            return Response(
                {"detail": "School not found or you cannot manage staff there."},
                status=status.HTTP_404_NOT_FOUND,
            )
        require_active_subscription(tenant_id)

        with transaction.atomic():
            invitation = (
                MembershipInvitation.objects.select_for_update()
                .filter(
                    pk=invitation_id,
                    tenant_id=tenant_id,
                )
                .first()
            )
            if invitation is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if invitation.accepted_at is not None:
                return Response(
                    {"detail": "An accepted invitation cannot be revoked."},
                    status=status.HTTP_409_CONFLICT,
                )
            if invitation.revoked_at is None:
                invitation.revoked_at = timezone.now()
                invitation.save(update_fields=("revoked_at",))
                AuditEvent.objects.create(
                    tenant_id=tenant_id,
                    actor=request.user,
                    action="membership.invitation_revoked",
                    entity_type="MembershipInvitation",
                    entity_id=str(invitation.pk),
                )

        return Response(status=status.HTTP_204_NO_CONTENT)
