from django.contrib.auth import authenticate
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings

from .models import MembershipInvitation, User


class InitialSchoolSetupSerializer(serializers.Serializer):
    school_name = serializers.CharField(max_length=200)
    school_email = serializers.EmailField()
    school_phone = serializers.CharField(max_length=32)
    country = serializers.CharField(max_length=100, default="Ghana")
    region = serializers.CharField(max_length=100, allow_blank=True, required=False)
    address = serializers.CharField(max_length=240, allow_blank=True, required=False)
    school_type = serializers.CharField(max_length=80, allow_blank=True, required=False)
    owner_name = serializers.CharField(max_length=160)
    owner_email = serializers.EmailField()
    owner_password = serializers.CharField(
        min_length=8,
        max_length=128,
        trim_whitespace=False,
        write_only=True,
    )

    def validate_school_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter the school name.")
        return value

    def validate_owner_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter the owner’s full name.")
        return value

    def validate(self, attrs):
        owner_email = attrs["owner_email"].strip().lower()
        attrs["owner_email"] = owner_email
        if User.objects.filter(email__iexact=owner_email).exists():
            raise serializers.ValidationError(
                {"owner_email": "An account with this email address already exists."}
            )

        owner = User(email=owner_email, full_name=attrs["owner_name"])
        validate_password(attrs["owner_password"], user=owner)
        attrs["school_slug"] = slugify(attrs["school_name"])[:80].strip("-") or "school"
        return attrs


class StaffInvitationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=(
            MembershipInvitation.Role.SCHOOL_ADMIN,
            MembershipInvitation.Role.ACCOUNTANT,
            MembershipInvitation.Role.TEACHER,
        )
    )

    def validate_email(self, value):
        return value.strip().lower()


class StaffInvitationAcceptanceSerializer(serializers.Serializer):
    token = serializers.CharField(min_length=32, max_length=128, write_only=True)
    full_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    password = serializers.CharField(
        max_length=128,
        required=False,
        trim_whitespace=False,
        write_only=True,
    )


class EmailLoginSerializer(TokenObtainPairSerializer):
    default_error_messages = {
        "no_active_account": "Email or password is incorrect, or the account is inactive."
    }

    def validate(self, attrs):
        email = attrs[self.username_field].strip().lower()
        self.user = authenticate(
            request=self.context.get("request"),
            **{
                self.username_field: email,
                "password": attrs["password"],
            },
        )
        if self.user is None:
            raise serializers.ValidationError(
                {"detail": self.error_messages["no_active_account"]}
            )
        if not self.user.email_verified:
            raise serializers.ValidationError(
                {"detail": "Verify your email address before signing in."}
            )

        refresh = self.get_token(self.user)
        data = {"refresh": str(refresh), "access": str(refresh.access_token)}
        if api_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, self.user)

        memberships = (
            self.user.memberships.filter(
                status="active",
                tenant__status="active",
            )
            .select_related("tenant")
            .order_by("tenant__name")
        )
        data["user"] = {
            "id": str(self.user.pk),
            "email": self.user.email,
            "full_name": self.user.full_name,
            "memberships": [
                {
                    "tenant_id": str(membership.tenant_id),
                    "tenant_name": membership.tenant.name,
                    "role": membership.role,
                }
                for membership in memberships
            ],
        }
        return data


class EmailAddressSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class EmailActionSerializer(serializers.Serializer):
    token = serializers.CharField(min_length=32, max_length=128, write_only=True)


class PasswordResetConfirmSerializer(EmailActionSerializer):
    new_password = serializers.CharField(
        min_length=8,
        max_length=128,
        trim_whitespace=False,
        write_only=True,
    )
