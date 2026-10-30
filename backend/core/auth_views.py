from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .auth_email import (
    get_email_action,
    invalidate_user_refresh_tokens,
    mark_email_action_used,
    send_email_action,
)
from .models import AuditEvent, EmailActionToken, Membership, User
from .serializers import (
    EmailActionSerializer,
    EmailAddressSerializer,
    EmailLoginSerializer,
    PasswordResetConfirmSerializer,
)


REFRESH_COOKIE_NAME = "nlschool_refresh"
REFRESH_COOKIE_PATH = "/api/auth/"


def has_untrusted_browser_origin(request):
    origin = request.headers.get("Origin")
    return bool(origin and origin.rstrip("/") not in {
        allowed.rstrip("/") for allowed in settings.CORS_ALLOWED_ORIGINS
    })


def set_refresh_cookie(response, token):
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response):
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        samesite="Lax",
    )


def record_user_audit(user, action):
    memberships = list(
        user.memberships.filter(status=Membership.Status.ACTIVE).values_list(
            "tenant_id",
            flat=True,
        )
    )
    if memberships:
        AuditEvent.objects.bulk_create(
            [
                AuditEvent(
                    tenant_id=tenant_id,
                    actor=user,
                    action=action,
                    entity_type="User",
                    entity_id=str(user.pk),
                )
                for tenant_id in memberships
            ]
        )
    else:
        AuditEvent.objects.create(
            actor=user,
            action=action,
            entity_type="User",
            entity_id=str(user.pk),
        )


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    def post(self, request):
        if has_untrusted_browser_origin(request):
            return Response(
                {"detail": "This browser origin is not allowed to sign in."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = EmailLoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        refresh = serializer.validated_data.pop("refresh")
        response = Response(serializer.validated_data)
        set_refresh_cookie(response, str(refresh))
        return response


class RefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if has_untrusted_browser_origin(request):
            return Response(
                {"detail": "This browser origin is not allowed to refresh a session."},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw_refresh = request.COOKIES.get(REFRESH_COOKIE_NAME) or request.data.get(
            "refresh"
        )
        if not raw_refresh:
            response = Response(
                {"detail": "A refresh session is required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            clear_refresh_cookie(response)
            return response

        serializer = TokenRefreshSerializer(data={"refresh": raw_refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError:
            response = Response(
                {"detail": "This refresh session is invalid or has been revoked."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            clear_refresh_cookie(response)
            return response
        data = serializer.validated_data
        refresh = data.pop("refresh", None)
        response = Response(data)
        if refresh:
            set_refresh_cookie(response, refresh)
        return response


class LogoutView(APIView):
    def post(self, request):
        raw_refresh = request.COOKIES.get(REFRESH_COOKIE_NAME) or request.data.get(
            "refresh"
        )
        response = Response(status=status.HTTP_204_NO_CONTENT)
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except TokenError:
                pass
        clear_refresh_cookie(response)
        return response


class CurrentUserView(APIView):
    def get(self, request):
        memberships = (
            request.user.memberships.filter(
                status=Membership.Status.ACTIVE,
                tenant__status="active",
            )
            .select_related("tenant")
            .order_by("tenant__name")
        )
        return Response(
            {
                "id": str(request.user.pk),
                "email": request.user.email,
                "full_name": request.user.full_name,
                "email_verified": request.user.email_verified,
                "memberships": [
                    {
                        "tenant_id": str(membership.tenant_id),
                        "tenant_name": membership.tenant.name,
                        "role": membership.role,
                    }
                    for membership in memberships
                ],
            }
        )


class SendVerificationEmailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_email"

    def post(self, request):
        serializer = EmailAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"],
            is_active=True,
            email_verified=False,
        ).first()
        if user is not None:
            with transaction.atomic():
                send_email_action(user, EmailActionToken.Purpose.VERIFY_EMAIL)
        return Response(
            {"detail": "If the account needs verification, an email has been sent."}
        )


class VerifyEmailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = EmailActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            action = get_email_action(
                serializer.validated_data["token"],
                EmailActionToken.Purpose.VERIFY_EMAIL,
                lock=True,
            )
            if action is None or not action.user.is_active:
                return Response(
                    {"detail": "This verification link is invalid, expired, or already used."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = action.user
            if not user.email_verified:
                user.email_verified = True
                user.save(update_fields=("email_verified",))
                record_user_audit(user, "account.email_verified")
            mark_email_action_used(action)

        return Response({"detail": "Email address verified. You can now sign in."})


class PasswordResetRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_email"

    def post(self, request):
        serializer = EmailAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"],
            is_active=True,
        ).first()
        if user is not None:
            with transaction.atomic():
                send_email_action(user, EmailActionToken.Purpose.RESET_PASSWORD)
        return Response(
            {"detail": "If an active account exists, a password reset email has been sent."}
        )


class PasswordResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_email"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            action = get_email_action(
                serializer.validated_data["token"],
                EmailActionToken.Purpose.RESET_PASSWORD,
                lock=True,
            )
            if action is None or not action.user.is_active:
                return Response(
                    {"detail": "This password reset link is invalid, expired, or already used."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = action.user
            try:
                validate_password(
                    serializer.validated_data["new_password"],
                    user=user,
                )
            except DjangoValidationError as error:
                return Response(
                    {"new_password": error.messages},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.set_password(serializer.validated_data["new_password"])
            user.save(update_fields=("password",))
            mark_email_action_used(action)
            EmailActionToken.objects.filter(
                user=user,
                used_at__isnull=True,
            ).update(used_at=timezone.now())
            invalidate_user_refresh_tokens(user)
            record_user_audit(user, "account.password_reset")

        return Response({"detail": "Password updated. You can now sign in."})
