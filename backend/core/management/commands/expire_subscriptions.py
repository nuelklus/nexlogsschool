from django.core.management.base import BaseCommand

from core.subscriptions import expire_subscriptions


class Command(BaseCommand):
    help = "Mark trials and paid periods that have ended as expired."

    def handle(self, *args, **options):
        count = expire_subscriptions()
        self.stdout.write(f"Expired {count} subscription(s).")
