from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def backfill_trials(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    SubscriptionPlan = apps.get_model("core", "SubscriptionPlan")
    TenantSubscription = apps.get_model("core", "TenantSubscription")

    live = ("trialing", "active", "suspended")
    tenants = Tenant.objects.exclude(subscriptions__status__in=live)
    if not tenants.exists():
        return
    plan, _ = SubscriptionPlan.objects.get_or_create(
        code="trial",
        defaults={"name": "Free trial", "price": 0, "trial_days": 30},
    )
    now = timezone.now()
    for tenant in tenants:
        TenantSubscription.objects.create(
            tenant=tenant,
            plan=plan,
            status="trialing",
            trial_started_at=now,
            trial_ends_at=now + timedelta(days=plan.trial_days),
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0003_students_guardians")]

    operations = [migrations.RunPython(backfill_trials, migrations.RunPython.noop)]
