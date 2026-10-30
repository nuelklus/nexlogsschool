import logging

from django.contrib import admin, messages

from .models import SubscriptionPlan, TenantSubscription
from .subscriptions import TRIAL_PLAN_CODE, SubscriptionError, activate_subscription

logger = logging.getLogger(__name__)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "billing_interval",
        "price",
        "currency",
        "trial_days",
        "is_active",
    )


@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "plan", "status", "trial_ends_at", "current_period_end")
    list_filter = ("status", "plan")
    actions = ("renew_for_one_period",)

    @admin.action(
        description="Renew selected schools for one period of their plan",
        permissions=("change",),
    )
    def renew_for_one_period(self, request, queryset):
        eligible = (
            TenantSubscription.Status.ACTIVE,
            TenantSubscription.Status.EXPIRED,
        )
        renewed = []
        seen = set()
        # Newest first, so each school is renewed from its latest paid subscription.
        for subscription in queryset.select_related("tenant", "plan").order_by(
            "-created_at"
        ):
            tenant = subscription.tenant
            if tenant.pk in seen:
                continue
            if (
                subscription.plan.code == TRIAL_PLAN_CODE
                or subscription.status not in eligible
            ):
                self.message_user(
                    request,
                    f"{tenant}: skipped; only active or expired paid subscriptions "
                    "can be renewed here. Use the activate_subscription command "
                    "to move a school onto a paid plan.",
                    messages.WARNING,
                )
                continue
            seen.add(tenant.pk)
            try:
                renewed_subscription = activate_subscription(
                    tenant, subscription.plan, actor=request.user, source="django_admin"
                )
            except SubscriptionError as error:
                self.message_user(request, f"{tenant}: {error}", messages.ERROR)
                continue
            except Exception:
                # Each school is its own transaction: this one is rolled back, while
                # schools already renewed stay renewed and are reported as such.
                logger.exception("Unexpected error renewing school %s", tenant.pk)
                self.message_user(
                    request,
                    f"{tenant}: renewal failed unexpectedly and nothing was changed "
                    "for this school. Check the server logs and try again.",
                    messages.ERROR,
                )
                continue
            renewed.append((tenant, renewed_subscription))
        for tenant, renewed_subscription in renewed:
            self.message_user(
                request,
                f"{tenant}: renewed until "
                f"{renewed_subscription.current_period_end:%Y-%m-%d}.",
                messages.SUCCESS,
            )
        if renewed:
            self.message_user(request, f"Renewed {len(renewed)} school(s).")
