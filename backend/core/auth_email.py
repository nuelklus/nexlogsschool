import hashlib
import secrets
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import EmailActionToken


EMAIL_ACTION_LIFETIMES = {
    EmailActionToken.Purpose.VERIFY_EMAIL: timedelta(hours=24),
    EmailActionToken.Purpose.RESET_PASSWORD: timedelta(hours=1),
}


def create_email_action(user, purpose):
    now = timezone.now()
    EmailActionToken.objects.filter(
        user=user,
        purpose=purpose,
        used_at__isnull=True,
    ).update(used_at=now)

    token = secrets.token_urlsafe(32)
    action = EmailActionToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        expires_at=now + EMAIL_ACTION_LIFETIMES[purpose],
    )
    return action, token


def send_email_action(user, purpose):
    action, token = create_email_action(user, purpose)
    if purpose == EmailActionToken.Purpose.VERIFY_EMAIL:
        url = (
            f"{settings.NLSCHOOL_FRONTEND_URL.rstrip('/')}"
            f"/account/verify?token={quote(token)}"
        )
        subject = "Verify your NLSchool email"
        message = (
            f"Hello {user.full_name},\n\n"
            f"Verify your email address within 24 hours: {url}\n"
        )
    elif purpose == EmailActionToken.Purpose.RESET_PASSWORD:
        url = (
            f"{settings.NLSCHOOL_FRONTEND_URL.rstrip('/')}"
            f"/password-reset/confirm?token={quote(token)}"
        )
        subject = "Reset your NLSchool password"
        message = (
            f"Hello {user.full_name},\n\n"
            f"Reset your password within one hour: {url}\n"
            "If you did not request this, you can ignore this message.\n"
        )
    else:
        raise ValueError(f"Unsupported email action purpose: {purpose}")

    sent = send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    if sent != 1:
        raise RuntimeError("The account email was not sent.")

    return action


def get_email_action(token, purpose, *, lock=False):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    actions = EmailActionToken.objects.select_related("user")
    if lock:
        actions = actions.select_for_update()
    return actions.filter(
        token_hash=token_hash,
        purpose=purpose,
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()


def mark_email_action_used(action):
    action.used_at = timezone.now()
    action.save(update_fields=("used_at",))


def invalidate_user_refresh_tokens(user):
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    with transaction.atomic():
        for token in OutstandingToken.objects.select_for_update().filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token)
