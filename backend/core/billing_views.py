from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Membership, Tenant, TenantSubscription
from .subscriptions import LIVE_STATUSES, TRIAL_PLAN_CODE, expire_subscriptions

MANAGE_ROLES = (Membership.Role.OWNER, Membership.Role.SCHOOL_ADMIN)
CURRENT_STATUSES = LIVE_STATUSES + (TenantSubscription.Status.SUSPENDED,)


def _plan_payload(plan):
    is_trial = plan.code == TRIAL_PLAN_CODE
    return {
        "code": plan.code,
        "name": plan.name,
        "is_trial": is_trial,
        # A trial is not billed, so it carries no billing interval.
        "billing_interval": None if is_trial else plan.billing_interval,
        "price": f"{plan.price:.2f}",
        "currency": plan.currency,
    }


class SubscriptionStatusView(APIView):
    """A school administrator's read-only view of their own school's subscription.

    Deliberately does not require an active subscription, so an expired school can
    still see why it is locked out and how to renew.
    """

    def get(self, request, tenant_id):
        is_admin = Membership.objects.filter(
            tenant_id=tenant_id,
            user=request.user,
            role__in=MANAGE_ROLES,
            status=Membership.Status.ACTIVE,
            tenant__status=Tenant.Status.ACTIVE,
            user__is_active=True,
        ).exists()
        # Same answer for "no such school" and "not your school".
        if not is_admin:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        expire_subscriptions(tenant_id)
        subscriptions = TenantSubscription.objects.filter(
            tenant_id=tenant_id
        ).select_related("plan")
        subscription = (
            subscriptions.filter(status__in=CURRENT_STATUSES).first()
            or subscriptions.order_by("-created_at").first()
        )

        payload = {
            "status": subscription.status if subscription else "none",
            "has_access": bool(subscription and subscription.status in LIVE_STATUSES),
            "plan": _plan_payload(subscription.plan) if subscription else None,
            "started_at": None,
            "current_period_start": None,
            "expires_at": None,
            "renewal": {
                "instructions": settings.NLSCHOOL_BILLING_INSTRUCTIONS,
                "support_email": settings.NLSCHOOL_SUPPORT_EMAIL,
            },
        }
        if subscription:
            is_trial = subscription.plan.code == TRIAL_PLAN_CODE
            expires_at = (
                subscription.trial_ends_at if is_trial else subscription.current_period_end
            )
            started_at = (
                subscription.trial_started_at if is_trial else subscription.started_at
            )
            payload.update(
                started_at=started_at or subscription.created_at,
                current_period_start=subscription.current_period_start,
                expires_at=expires_at,
            )
        return Response(payload)
