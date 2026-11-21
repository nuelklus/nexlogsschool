from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.class_subjects import class_subject_ids
from academics.models import StudentEnrollment, TeachingAssignment
from core.models import Tenant


class Command(BaseCommand):
    help = (
        "Report or remove legacy student-specific subject restrictions for one school. "
        "Students follow their class assignments; no per-student subject rows are created."
    )

    def add_arguments(self, parser):
        parser.add_argument("school_slug")
        parser.add_argument("--apply", action="store_true", help="Remove legacy restrictions after reporting.")

    def handle(self, *args, school_slug, **options):
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().filter(slug=school_slug).first()
            if tenant is None:
                raise CommandError(f"No school with slug '{school_slug}'.")
            enrollments = list(
                StudentEnrollment.objects.select_for_update()
                .filter(tenant=tenant)
                .select_related("student", "school_class")
                .prefetch_related("subjects")
                .order_by("pk")
            )
            if any(row.student.tenant_id != tenant.pk or row.school_class.tenant_id != tenant.pk for row in enrollments):
                raise CommandError("Enrollment ownership is inconsistent. Reconcile it before conversion.")
            if TeachingAssignment.objects.filter(school_class__tenant=tenant).exclude(
                tenant=tenant, subject__tenant=tenant
            ).exists():
                raise CommandError("Class subject assignment ownership is inconsistent. Reconcile it before conversion.")

            assigned_by_class = {
                class_id: set(class_subject_ids(tenant.pk, class_id))
                for class_id in {row.school_class_id for row in enrollments}
            }
            added = retained = removed = changed = restrictions = 0
            for enrollment in enrollments:
                old = {subject.pk for subject in enrollment.subjects.all()}
                desired = assigned_by_class[enrollment.school_class_id]
                previous_effective = old if old else desired
                added += len(desired - previous_effective)
                retained += len(desired & previous_effective)
                removed += len(previous_effective - desired)
                restrictions += len(old)
                changed += previous_effective != desired

            self.stdout.write(f"School: {tenant.name}")
            self.stdout.write(f"Enrollments reviewed: {len(enrollments)}")
            self.stdout.write(f"Effective subject memberships added: {added}")
            self.stdout.write(f"Effective subject memberships retained: {retained}")
            self.stdout.write(f"Effective subject memberships removed: {removed}")
            self.stdout.write(f"Enrollments with changed effective subjects: {changed}")
            self.stdout.write(f"Legacy restriction rows to remove: {restrictions}")
            if options["apply"]:
                StudentEnrollment.subjects.through.objects.filter(
                    studentenrollment__tenant=tenant
                ).delete()
                self.stdout.write("Conversion applied. Effective memberships are derived from class assignments.")
            else:
                self.stdout.write("Dry run only. Use --apply to remove legacy restrictions.")
