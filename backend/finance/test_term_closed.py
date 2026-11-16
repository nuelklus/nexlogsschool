from datetime import date

from academics.models import AcademicTerm
from core.models import Student

from .models import Invoice
from .test_finance import FinanceBase


class ClosedTermBillingTests(FinanceBase):
    def close(self, name="Term 1"):
        AcademicTerm.objects.create(
            tenant=self.tenant, academic_year=self.year, name=name, start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 15), is_completed=True,
        )

    def test_new_invoices_are_blocked_for_a_completed_term_but_payments_continue(self):
        self.schedule(amount="1000.00")
        invoice = self.issued_invoice(period="Term 1")
        self.close("Term 1")
        body = {"student": str(self.sibling.pk), "academic_year": str(self.year.pk), "period_label": "Term 1"}
        blocked = self.client.post(f"{self.base}/invoices/", body, format="json")
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("closed", str(blocked.data).lower())
        supplementary = self.client.post(f"{self.base}/invoices/", {**body, "supplementary": True, "categories": []}, format="json")
        self.assertIn(supplementary.status_code, (400, 409))
        paid = self.pay("300.00", auto_allocate=True)
        self.assertEqual(paid.status_code, 201)
        after = Invoice.objects.get(pk=invoice["id"])
        self.assertEqual(str(after.balance), "700.00")
        self.assertEqual(self.client.get(f"{self.base}/invoices/{invoice['id']}/").status_code, 200)

    def test_other_terms_stay_open_and_carry_forward_is_unchanged(self):
        self.schedule(amount="1000.00")
        self.issued_invoice(period="Term 1")
        self.close("Term 1")
        second = self.issued_invoice(period="Term 2")
        detail = self.client.get(f"{self.base}/invoices/{second['id']}/").data
        self.assertEqual(detail["brought_forward"], "1000.00")

    def test_bulk_billing_reports_the_closed_term_per_student(self):
        self.schedule(amount="1000.00")
        self.close("Term 1")
        students = list(Student.objects.filter(tenant=self.tenant))
        from . import billing

        results = billing.generate_bulk(
            self.tenant.pk, students, academic_year_id=self.year.pk, period_label="Term 1", due_date=None, user=self.owner
        )
        self.assertTrue(all(row["invoice"] is None and "closed" in row["error"] for row in results))
        self.assertFalse(Invoice.objects.filter(tenant=self.tenant).exists())
