from datetime import date
from decimal import Decimal

from academics.models import AcademicTerm
from academics.models import SchoolClass, SchoolClassLevel, StudentEnrollment

from .models import Invoice, JournalLine
from .test_finance import FinanceBase
from core.tests import create_tenant


class ClassLevelCompatibilityTests(FinanceBase):
    def test_fee_schedule_class_level_is_tenant_scoped(self):
        level = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="JHS")
        payload = {
            "academic_year": str(self.year.pk),
            "category": str(self.cat["tuition"].pk),
            "class_level": str(level.pk),
            "amount": "900.00",
            "effective_from": "2026-09-01",
        }
        created = self.client.post(f"{self.base}/fee-schedules/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["class_level"], str(level.pk))

        other = create_tenant(name="Other", slug="other-fee-level", email="o@o.example", phone="3")
        foreign_level = SchoolClassLevel.objects.create(tenant=other, display_name="JHS")
        foreign = self.client.post(
            f"{self.base}/fee-schedules/",
            {**payload, "class_level": str(foreign_level.pk)},
            format="json",
        )
        self.assertEqual(foreign.status_code, 404)

        mixed = self.client.post(
            f"{self.base}/fee-schedules/",
            {**payload, "grade_level": "JHS 1"},
            format="json",
        )
        self.assertEqual(mixed.status_code, 400)

    def test_new_billing_uses_the_term_start_enrollment_class_level(self):
        level = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="JHS")
        school_class = SchoolClass.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            campus=self.campus,
            class_level=level,
            name="JHS 1",
        )
        self.student.campus = None
        self.student.save(update_fields=("campus",))
        enrollment = StudentEnrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            school_class=school_class,
            enrolled_on=date(2026, 9, 1),
        )
        AcademicTerm.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            name="Term 1",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 31),
        )
        self.student.grade_level = "Creche"
        self.student.save(update_fields=("grade_level",))
        self.schedule(grade="Creche", amount="100.00")
        self.schedule(grade="", amount="1200.00", class_level=level, campus=self.campus)

        preview = self.client.post(f"{self.base}/invoices/preview/", {
            "academic_year": str(self.year.pk),
            "period_label": "Term 1",
            "campus": str(self.campus.pk),
        }, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["students"][0]["student"], str(self.student.pk))

        draft = self.client.post(f"{self.base}/invoices/", {
            "student": str(self.student.pk),
            "academic_year": str(self.year.pk),
            "period_label": "Term 1",
        }, format="json")

        self.assertEqual(draft.status_code, 201, draft.data)
        invoice = Invoice.objects.get(pk=draft.data["id"])
        self.assertEqual(invoice.total, Decimal("1200.00"))
        self.assertEqual(invoice.grade_level, "JHS 1")
        self.assertEqual(invoice.campus_id, self.campus.pk)
        self.assertEqual(invoice.calculation["class_at_term_start"], "JHS 1")
        self.assertEqual(invoice.calculation["academic_enrollment"]["id"], str(enrollment.pk))
        self.assertEqual(invoice.calculation["academic_enrollment"]["class_level"], str(level.pk))
        self.assertEqual(
            invoice.calculation["warnings"],
            ["The enrollment has no campus snapshot; the class-group or legacy profile campus was used for billing."],
        )

    def test_mapping_class_keeps_legacy_billing_and_existing_invoice_snapshot(self):
        self.schedule(grade="JHS 1", amount="1000.00")
        issued = self.issued_invoice()
        invoice = Invoice.objects.get(pk=issued["id"])
        original = (invoice.grade_level, invoice.total, invoice.calculation, invoice.journal_entry_id)
        journal_lines = list(
            JournalLine.objects.filter(entry_id=invoice.journal_entry_id)
            .order_by("pk").values_list("pk", "debit", "credit")
        )
        school_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="JHS 1")
        enrollment = StudentEnrollment.objects.create(tenant=self.tenant, student=self.student, school_class=school_class)
        level = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Renamed school level")

        result = self.client.patch(
            f"/api/tenants/{self.tenant.pk}/academics/classes/{school_class.pk}/level/",
            {"class_level": str(level.pk)}, format="json",
        )
        self.assertEqual(result.status_code, 200, result.data)
        invoice.refresh_from_db()
        self.student.refresh_from_db()
        enrollment.refresh_from_db()
        self.assertEqual((invoice.grade_level, invoice.total, invoice.calculation, invoice.journal_entry_id), original)
        self.assertEqual(list(
            JournalLine.objects.filter(entry_id=invoice.journal_entry_id)
            .order_by("pk").values_list("pk", "debit", "credit")
        ), journal_lines)
        self.assertEqual(self.student.grade_level, "JHS 1")
        self.assertEqual(enrollment.school_class_id, school_class.pk)
        draft = self.client.post(f"{self.base}/invoices/", {
            "student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 2",
        }, format="json")
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(draft.data["total"], "1000.00")
