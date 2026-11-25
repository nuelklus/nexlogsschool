from datetime import date
from unittest.mock import patch

from academics.models import AcademicTerm, SchoolClass, SchoolClassLevel, StudentEnrollment
from core.models import AcademicYear
from core.tests import create_tenant

from .models import Invoice
from .test_finance import FinanceBase


@patch("django.utils.timezone.localdate", return_value=date(2026, 11, 21))
class LateEnrollmentBillingTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.term = AcademicTerm.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Term 1",
            start_date=date(2026, 9, 1), end_date=date(2026, 12, 15),
        )
        self.level = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Creche")
        self.school_class = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, class_level=self.level,
            campus=self.campus, name="Creche",
        )
        self.student.grade_level = "Creche"
        self.student.save(update_fields=("grade_level",))
        self.schedule(amount="500.00", class_level=self.level)
        self.body = {"academic_year": str(self.year.pk), "period_label": "Term 1", "grade_level": "Creche"}

    def enroll(self, student, school_class, enrolled_on, **extra):
        return StudentEnrollment.objects.create(
            tenant=self.tenant, student=student, school_class=school_class, enrolled_on=enrolled_on, **extra,
        )

    def test_late_student_is_selected_and_full_fee_matches_bulk_draft(self, _today):
        enrollment = self.enroll(self.student, self.school_class, date(2026, 11, 20))
        preview = self.client.post(f"{self.base}/invoices/preview/", self.body, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        row = preview.data["students"][0]
        self.assertEqual(row["student"], str(self.student.pk))
        self.assertEqual(row["totals"]["total"], "500.00")
        self.assertEqual(row["calculation"]["academic_enrollment"]["id"], str(enrollment.pk))
        self.assertEqual(row["calculation"]["academic_enrollment"]["as_of"], "2026-11-20")
        self.assertTrue(any("first class enrollment" in warning for warning in row["warnings"]))
        for selection in ({"class_level": str(self.level.pk)}, {"campus": str(self.campus.pk)}):
            filtered = self.client.post(
                f"{self.base}/invoices/preview/",
                {"academic_year": str(self.year.pk), "period_label": "Term 1", **selection}, format="json",
            )
            self.assertEqual(filtered.status_code, 200, filtered.data)
            self.assertEqual(filtered.data["students"][0]["student"], str(self.student.pk))
        bulk = self.client.post(f"{self.base}/invoices/bulk/", self.body, format="json")
        self.assertEqual(bulk.status_code, 201, bulk.data)
        invoice = Invoice.objects.get(pk=bulk.data["results"][0]["invoice"])
        self.assertEqual(str(invoice.total), "500.00")
        self.assertEqual(invoice.calculation["fees"], row["calculation"]["fees"])

    def test_first_late_class_is_used_after_transfer(self, _today):
        first = self.enroll(
            self.student, self.school_class, date(2026, 9, 10),
            ended_on=date(2026, 10, 1), status=StudentEnrollment.Status.TRANSFERRED,
        )
        other_level = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Nursery")
        other_class = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, class_level=other_level, name="Nursery",
        )
        self.enroll(self.student, other_class, date(2026, 10, 2))
        self.student.grade_level = "Nursery"
        self.student.save(update_fields=("grade_level",))
        preview = self.client.post(f"{self.base}/invoices/preview/", self.body, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["students"][0]["calculation"]["academic_enrollment"]["id"], str(first.pk))
        wrong_class = self.client.post(
            f"{self.base}/invoices/preview/", {**self.body, "grade_level": "Nursery"}, format="json",
        )
        self.assertEqual(wrong_class.status_code, 400, wrong_class.data)

    def test_term_start_class_still_wins_over_later_transfer(self, _today):
        original = self.enroll(
            self.student, self.school_class, date(2026, 8, 15),
            ended_on=date(2026, 10, 1), status=StudentEnrollment.Status.TRANSFERRED,
        )
        other = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Other")
        self.enroll(self.student, other, date(2026, 10, 2))
        preview = self.client.post(f"{self.base}/invoices/preview/", self.body, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["students"][0]["calculation"]["academic_enrollment"]["id"], str(original.pk))
        self.assertEqual(preview.data["students"][0]["calculation"]["academic_enrollment"]["as_of"], "2026-09-01")

    def test_future_or_after_term_enrollments_do_not_bill_using_profile_fallback(self, _today):
        enrollment = self.enroll(self.student, self.school_class, date(2026, 11, 22))
        for start, today in (
            (date(2026, 11, 22), date(2026, 11, 21)),
            (date(2026, 12, 16), date(2027, 1, 1)),
        ):
            _today.return_value = today
            enrollment.enrolled_on = start
            enrollment.save(update_fields=("enrolled_on",))
            preview = self.client.post(f"{self.base}/invoices/preview/", self.body, format="json")
            self.assertEqual(preview.status_code, 400, preview.data)
            explicit = self.client.post(
                f"{self.base}/invoices/preview/",
                {"academic_year": str(self.year.pk), "period_label": "Term 1", "students": [str(self.student.pk)]},
                format="json",
            )
            self.assertFalse(explicit.data["students"][0]["can_generate"])
            self.assertIn("No eligible class enrollment", explicit.data["students"][0]["issues"][0])

    def test_single_student_invoice_uses_late_enrollment(self, _today):
        self.enroll(self.student, self.school_class, date(2026, 11, 20))
        draft = self.client.post(
            f"{self.base}/invoices/",
            {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 1"},
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(draft.data["total"], "500.00")
        self.assertEqual(draft.data["calculation"]["academic_enrollment"]["as_of"], "2026-11-20")

    def test_conflicting_first_enrollments_are_blocked(self, _today):
        self.enroll(self.student, self.school_class, date(2026, 9, 10))
        other = SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Other")
        self.enroll(self.student, other, date(2026, 9, 10))
        preview = self.client.post(f"{self.base}/invoices/preview/", self.body, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertFalse(preview.data["students"][0]["can_generate"])
        self.assertIn("multiple class enrollments", preview.data["students"][0]["issues"][0])

    def test_other_school_enrollment_does_not_change_billing(self, _today):
        self.enroll(self.student, self.school_class, date(2026, 11, 20))
        other = create_tenant(name="Other", slug="other-late-billing", email="o@example.com", phone="3")
        foreign_record_class = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Conflicting foreign record",
        )
        StudentEnrollment.objects.create(
            tenant=other, student=self.student, school_class=foreign_record_class, enrolled_on=date(2026, 9, 1),
        )
        preview = self.client.post(f"{self.base}/invoices/preview/", self.body, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["students"][0]["totals"]["total"], "500.00")

    def test_class_list_is_year_scoped_and_contains_classes_not_levels(self, _today):
        other_year = AcademicYear.objects.create(
            tenant=self.tenant, name="2027/2028", start_date=date(2027, 9, 1), end_date=date(2028, 7, 31),
        )
        SchoolClass.objects.create(tenant=self.tenant, academic_year=other_year, name="Next year")
        level = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Level only")
        section = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Creche", section="A", class_level=level, is_active=False,
        )
        response = self.client.get(f"{self.base}/billing-classes/", {"academic_year": str(self.year.pk)})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({row["id"] for row in response.data}, {str(self.school_class.pk), str(section.pk)})
        self.assertEqual(next(row for row in response.data if row["id"] == str(section.pk))["section"], "A")
        self.assertFalse(next(row for row in response.data if row["id"] == str(section.pk))["is_active"])
        self.assertEqual(self.as_user(self.accountant).get(
            f"{self.base}/billing-classes/", {"academic_year": str(self.year.pk)},
        ).status_code, 403)

    def test_class_id_selects_exact_section_in_preview_and_bulk(self, _today):
        self.enroll(self.student, self.school_class, date(2026, 11, 20))
        section = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Creche", section="A", class_level=self.level,
        )
        self.enroll(self.sibling, section, date(2026, 11, 20))
        body = {"academic_year": str(self.year.pk), "period_label": "Term 1", "school_class": str(self.school_class.pk)}
        preview = self.client.post(f"{self.base}/invoices/preview/", body, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual([row["student"] for row in preview.data["students"]], [str(self.student.pk)])
        bulk = self.client.post(f"{self.base}/invoices/bulk/", body, format="json")
        self.assertEqual(bulk.status_code, 201, bulk.data)
        self.assertEqual(Invoice.objects.get(pk=bulk.data["results"][0]["invoice"]).student_id, self.student.pk)

    def test_class_id_rejects_wrong_year_and_other_tenant(self, _today):
        other = create_tenant(name="Other", slug="other-bulk-class", email="o@example.com", phone="3")
        foreign_year = AcademicYear.objects.create(
            tenant=other, name="Other year", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31),
        )
        foreign_class = SchoolClass.objects.create(tenant=other, academic_year=foreign_year, name="Creche")
        other_year = AcademicYear.objects.create(
            tenant=self.tenant, name="Next year", start_date=date(2027, 9, 1), end_date=date(2028, 7, 31),
        )
        wrong_year_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=other_year, name="Creche")
        for school_class in (foreign_class, wrong_year_class):
            body = {"academic_year": str(self.year.pk), "period_label": "Term 1", "school_class": str(school_class.pk)}
            self.assertEqual(self.client.post(f"{self.base}/invoices/preview/", body, format="json").status_code, 404)
            self.assertEqual(self.client.post(f"{self.base}/invoices/bulk/", body, format="json").status_code, 404)

    def test_class_id_does_not_guess_section_from_legacy_profile(self, _today):
        body = {"academic_year": str(self.year.pk), "period_label": "Term 1", "school_class": str(self.school_class.pk)}
        response = self.client.post(f"{self.base}/invoices/preview/", body, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("No active students", response.data["detail"])
