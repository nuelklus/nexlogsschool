from core.models import AcademicYear, Campus
from core.tests import create_tenant

from .models import Invoice, Payment
from .test_finance import FinanceBase, make_student


class LegacyAttributionReconciliationTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.url = f"{self.base}/reports/legacy-attribution-reconciliation/"

    def test_owner_report_flags_unsafe_attribution_without_mutating_records(self):
        self.schedule()
        missing = self.issued_invoice(period="Missing snapshot")
        Invoice.objects.filter(pk=missing["id"]).update(grade_level="", campus=None)

        foreign = create_tenant(name="Other", slug="other-reconciliation", email="other@example.com", phone="2")
        foreign_year = AcademicYear.objects.create(
            tenant=foreign, name="2026/2027", start_date=self.year.start_date, end_date=self.year.end_date,
        )
        foreign_campus = Campus.objects.create(tenant=foreign, name="Foreign campus", code="FOREIGN")
        foreign_student = make_student(foreign, n=90, grade="Private foreign class")
        mismatched = self.issued_invoice(period="Wrong references")
        Invoice.objects.filter(pk=mismatched["id"]).update(
            campus=foreign_campus, academic_year=foreign_year,
        )
        wrong_student = self.issued_invoice(period="Wrong student")
        Invoice.objects.filter(pk=wrong_student["id"]).update(student=foreign_student)

        payment = self.pay("100.00", auto_allocate=False)
        self.assertEqual(payment.status_code, 201, payment.data)
        Payment.objects.filter(pk=payment.data["id"]).update(allocated_total="20.00", student=foreign_student)
        before = {
            "invoices": list(Invoice.objects.filter(pk__in=[missing["id"], mismatched["id"], wrong_student["id"]]).order_by("pk").values_list(
                "grade_level", "campus_id", "academic_year_id", "total", "amount_paid"
            )),
            "payments": list(Payment.objects.filter(pk=payment.data["id"]).order_by("pk").values_list(
                "allocated_total", "refunded_total", "status"
            )),
        }

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        report = response.data
        self.assertTrue(report["read_only"])
        by_id = {row["id"]: row for row in report["invoice_issues"]}
        self.assertIn("invoice_missing_class_snapshot", by_id[missing["id"]]["issues"])
        self.assertIn("invoice_missing_campus_snapshot", by_id[missing["id"]]["issues"])
        self.assertIn("invoice_campus_tenant_mismatch", by_id[mismatched["id"]]["issues"])
        self.assertIn("invoice_academic_year_tenant_mismatch", by_id[mismatched["id"]]["issues"])
        self.assertIsNone(by_id[mismatched["id"]]["campus_snapshot"])
        self.assertIn("invoice_student_tenant_mismatch", by_id[wrong_student["id"]]["issues"])
        self.assertEqual(by_id[wrong_student["id"]]["student"], "Student record belongs to another school")
        self.assertEqual(by_id[wrong_student["id"]]["admission_number"], "")

        payment_row = next(row for row in report["payment_issues"] if row["id"] == payment.data["id"])
        self.assertIn("payment_allocation_total_mismatch", payment_row["issues"])
        self.assertIn("payment_has_unattributed_amount", payment_row["issues"])
        self.assertIn("payment_student_tenant_mismatch", payment_row["issues"])
        self.assertEqual(payment_row["student"], "Student record belongs to another school")
        self.assertEqual(payment_row["admission_number"], "")
        self.assertEqual(payment_row["unattributed_amount"], "80.00")
        self.assertIn("not proposed corrections", report["basis"])
        after = {
            "invoices": list(Invoice.objects.filter(pk__in=[missing["id"], mismatched["id"], wrong_student["id"]]).order_by("pk").values_list(
                "grade_level", "campus_id", "academic_year_id", "total", "amount_paid"
            )),
            "payments": list(Payment.objects.filter(pk=payment.data["id"]).order_by("pk").values_list(
                "allocated_total", "refunded_total", "status"
            )),
        }
        self.assertEqual(after, before)

    def test_report_is_tenant_scoped_owner_admin_only_and_paginated(self):
        self.schedule()
        for period in ("First", "Second"):
            invoice = self.issued_invoice(period=period)
            Invoice.objects.filter(pk=invoice["id"]).update(campus=None)

        response = self.client.get(self.url, {"limit": 1})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["pagination"]["invoice_total"], 2)
        self.assertEqual(len(response.data["invoice_issues"]), 1)
        self.assertEqual(response.data["pagination"]["offset"], 0)
        second = self.client.get(self.url, {"limit": 1, "offset": 1})
        self.assertEqual(len(second.data["invoice_issues"]), 1)
        self.assertNotEqual(response.data["invoice_issues"][0]["id"], second.data["invoice_issues"][0]["id"])
        self.assertEqual(self.as_user(self.accountant).get(self.url).status_code, 404)
        self.assertEqual(self.as_user(self.teacher).get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.url, {"limit": 101}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {"offset": -1}).status_code, 400)

    def test_payment_allocations_are_compared_to_invoice_attribution(self):
        self.schedule()
        invoice = self.issued_invoice()
        payment = self.pay("100.00", allocations=[{"invoice": invoice["id"], "amount": "100.00"}])
        self.assertEqual(payment.status_code, 201, payment.data)
        Payment.objects.filter(pk=payment.data["id"]).update(student=self.sibling)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        row = next(item for item in response.data["payment_issues"] if item["id"] == payment.data["id"])
        self.assertIn("allocation_student_mismatch", row["allocations"][0]["issues"])
