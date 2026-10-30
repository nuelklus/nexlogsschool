import getpass

from django.core.management.base import BaseCommand, CommandError

from core.models import SubscriptionPlan, Tenant
from core.subscriptions import SubscriptionError, activate_subscription


class Command(BaseCommand):
    help = "Put a school on a paid plan for one billing period, or extend it by one."

    def add_arguments(self, parser):
        parser.add_argument("school_slug")
        parser.add_argument("plan_code")

    def handle(self, *args, school_slug, plan_code, **options):
        try:
            tenant = Tenant.objects.get(slug=school_slug)
        except Tenant.DoesNotExist:
            raise CommandError(f"No school with slug '{school_slug}'.")
        try:
            plan = SubscriptionPlan.objects.get(code=plan_code)
        except SubscriptionPlan.DoesNotExist:
            raise CommandError(f"No plan with code '{plan_code}'.")
        try:
            subscription = activate_subscription(
                tenant,
                plan,
                source="management_command",
                operator=getpass.getuser(),
            )
        except SubscriptionError as error:
            raise CommandError(str(error))
        self.stdout.write(
            f"{tenant.name} is on '{plan.name}' until "
            f"{subscription.current_period_end:%Y-%m-%d}."
        )
