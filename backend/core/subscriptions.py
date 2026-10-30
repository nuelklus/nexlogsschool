from calendar import monthrange

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

from .models import AuditEvent, SubscriptionPlan, Tenant, TenantSubscription

TRIAL_PLAN_CODE = "trial"

INTERVAL_MONTHS = {
    SubscriptionPlan.Interval.MONTHLY: 1,
    SubscriptionPlan.Interval.TERMLY: 3,
    SubscriptionPlan.Interval.YEARLY: 12,
}


class SubscriptionError(Exception):
    """A subscription change that must not be applied."""


def add_months(moment, months, anchor_day=None):
    """Add calendar months, clamping to month end; anchor_day avoids day drift."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(anchor_day or moment.day, monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def activate_subscription(tenant, plan, actor=None, source="system", operator=""):
    """Start a paid period for the plan, or extend the current one by a period.

    Audit events always record where the change came from (`source`). A signed-in
    user is the actor; otherwise the actor is empty and `operator` names who ran it.
    """
    if plan.code == TRIAL_PLAN_CODE:
        raise SubscriptionError("The trial plan cannot be activated as a paid plan.")
    if not plan.is_active:
        raise SubscriptionError(f"The plan '{plan.name}' is not active.")
    months = INTERVAL_MONTHS.get(plan.billing_interval)
    if months is None:
        raise SubscriptionError(f"Unsupported billing interval '{plan.billing_interval}'.")

    with transaction.atomic():
        # Locking the school serialises concurrent changes even when it has no live row.
        locked = Tenant.objects.select_for_update().get(pk=tenant.pk)
        expire_subscriptions(locked.pk)
        live = list(
            TenantSubscription.objects.select_for_update().filter(
                tenant=locked,
                status__in=(
                    TenantSubscription.Status.TRIALING,
                    TenantSubscription.Status.ACTIVE,
                    TenantSubscription.Status.SUSPENDED,
                ),
            )
        )
        if any(item.status == TenantSubscription.Status.SUSPENDED for item in live):
            raise SubscriptionError(
                "This school's subscription is suspended; lift the suspension first."
            )
        paid = next(
            (item for item in live if item.status == TenantSubscription.Status.ACTIVE),
            None,
        )
        now = timezone.now()

        if paid is not None:
            if paid.plan_id != plan.pk:
                raise SubscriptionError(
                    f"The school is already paid up on '{paid.plan.name}' until "
                    f"{paid.current_period_end:%Y-%m-%d}; changing plans mid-period is not supported."
                )
            # Early renewal extends from the existing expiry, not from today.
            previous_end = paid.current_period_end
            paid.current_period_end = add_months(
                previous_end, months, anchor_day=paid.current_period_start.day
            )
            paid.save(update_fields=("current_period_end", "updated_at"))
            subscription, action = paid, "subscription.renewed"
            metadata = {
                "plan": plan.code,
                "previous_period_end": previous_end.isoformat(),
            }
        else:
            for item in live:
                item.status = TenantSubscription.Status.CANCELLED
                item.cancelled_at = now
                item.save(update_fields=("status", "cancelled_at", "updated_at"))
            subscription = TenantSubscription.objects.create(
                tenant=locked,
                plan=plan,
                status=TenantSubscription.Status.ACTIVE,
                started_at=now,
                current_period_start=now,
                current_period_end=add_months(now, months),
            )
            action, metadata = "subscription.activated", {"plan": plan.code}

        metadata["period_end"] = subscription.current_period_end.isoformat()
        metadata["source"] = source
        if actor is not None:
            # Kept in the event so attribution survives deletion of the user.
            metadata["actor_email"] = actor.email
        elif operator:
            metadata["operator"] = operator
        AuditEvent.objects.create(
            tenant=locked,
            actor=actor,
            action=action,
            entity_type="TenantSubscription",
            entity_id=str(subscription.pk),
            metadata=metadata,
        )
        return subscription


LIVE_STATUSES = (
    TenantSubscription.Status.TRIALING,
    TenantSubscription.Status.ACTIVE,
)


class SubscriptionRequired(APIException):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = (
        "This school's subscription has expired. Contact NLSchool to renew it."
    )
    default_code = "subscription_required"


def expire_subscriptions(tenant_id=None):
    """Mark trials and paid periods that have ended as expired; return the count."""
    now = timezone.now()
    subscriptions = TenantSubscription.objects.all()
    if tenant_id is not None:
        subscriptions = subscriptions.filter(tenant_id=tenant_id)
    expired = 0
    expired += subscriptions.filter(
        status=TenantSubscription.Status.TRIALING, trial_ends_at__lte=now
    ).update(status=TenantSubscription.Status.EXPIRED)
    expired += subscriptions.filter(
        status=TenantSubscription.Status.ACTIVE, current_period_end__lte=now
    ).update(status=TenantSubscription.Status.EXPIRED)
    return expired


def has_active_subscription(tenant_id):
    expire_subscriptions(tenant_id)
    return TenantSubscription.objects.filter(
        tenant_id=tenant_id, status__in=LIVE_STATUSES
    ).exists()


def require_active_subscription(tenant_id):
    if not has_active_subscription(tenant_id):
        raise SubscriptionRequired()
