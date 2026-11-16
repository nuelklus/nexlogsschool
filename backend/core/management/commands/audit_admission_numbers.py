import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.admission_numbers import next_admission_number
from core.models import AuditEvent, Student, Tenant


class Command(BaseCommand):
    help = (
        "Report blank, padded and case-variant duplicate admission numbers for one school. "
        "Nothing is changed unless --assign-blank is given, and existing numbers are never rewritten."
    )

    def add_arguments(self, parser):
        parser.add_argument("school_slug")
        parser.add_argument(
            "--assign-blank",
            action="store_true",
            help="Generate numbers for students whose admission number is blank.",
        )

    def handle(self, *args, school_slug, assign_blank, **options):
        try:
            tenant = Tenant.objects.get(slug=school_slug)
        except Tenant.DoesNotExist:
            raise CommandError(f"No school with slug '{school_slug}'.")

        students = list(Student.objects.filter(tenant=tenant).order_by("created_at"))
        blank = [s.pk for s in students if not s.admission_number.strip()]
        padded = [s for s in students if s.admission_number.strip() and s.admission_number != s.admission_number.strip()]
        seen = {}
        for student in students:
            key = student.admission_number.strip().casefold()
            if key:
                seen.setdefault(key, []).append(student)
        lookalikes = [group for group in seen.values() if len(group) > 1]

        self.stdout.write(f"{tenant.name}: {len(students)} students.")
        self.stdout.write(f"Blank admission numbers: {len(blank)}")
        self.stdout.write(f"Numbers with leading/trailing spaces: {len(padded)}")
        self.stdout.write(f"Groups that differ only by case or spaces: {len(lookalikes)}")
        for group in lookalikes:
            self.stdout.write("  " + ", ".join(repr(s.admission_number) for s in group))

        if not assign_blank or not blank:
            return
        operator = getpass.getuser()
        for student_id in blank:
            with transaction.atomic():
                # Match admission's tenant-row reservation lock and recheck the
                # candidate after waiting, so a stale report cannot overwrite a
                # number assigned by another workflow.
                locked_tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
                student = Student.objects.select_for_update().filter(
                    pk=student_id, tenant=locked_tenant,
                ).first()
                if student is None or student.admission_number.strip():
                    continue
                number = next_admission_number(
                    locked_tenant.pk, student.enrolled_on or timezone.localdate()
                )
                student.admission_number = number
                student.save(update_fields=("admission_number",))
                AuditEvent.objects.create(
                    tenant=locked_tenant,
                    action="student.admission_number_backfilled",
                    entity_type="Student",
                    entity_id=str(student.pk),
                    metadata={"admission_number": number, "source": "management_command", "operator": operator},
                )
            self.stdout.write(f"  {student.first_name} {student.last_name}: {number}")
