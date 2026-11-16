from decimal import Decimal

from core.models import Campus

from .models import OtherIncome
from .test_finance import FinanceBase, D


class OtherIncomeTests(FinanceBase):
    def categories(self):
        data = self.client.get(f"{self.base}/income-categories/").data
        return {c["name"]: c["id"] for c in data}

    def record(self, name, amount, kind="cash", **extra):
        body = {"category": self.categories()[name], "method": str(self.method[kind].pk), "amount": amount, **extra}
        return self.client.post(f"{self.base}/other-income/", body, format="json")

    def test_default_categories_are_seeded(self):
        self.assertIn("Donations", self.categories())
        self.assertIn("Canteen sales", self.categories())

    def test_income_posts_to_ledger_and_reverses(self):
        response = self.record("Canteen sales", "250.00")
        self.assertEqual(response.status_code, 201, response.data)
        balances = self.balances()
        self.assertEqual(balances["cash"], D("250.00"))
        reversed_ = self.client.post(f"{self.base}/other-income/{response.data['id']}/reverse/", {"reason": "typo"}, format="json")
        self.assertEqual(reversed_.status_code, 200)
        self.assertEqual(self.balances()["cash"], D("0.00"))
        self.assertEqual(OtherIncome.objects.get().status, "reversed")

    def test_non_cash_needs_a_reference_and_accountant_cannot_reverse(self):
        self.assertEqual(self.record("Donations", "100.00", kind="bank_transfer").status_code, 400)
        ok = self.record("Donations", "100.00", kind="bank_transfer", reference="SLIP-1")
        self.assertEqual(ok.status_code, 201)
        accountant = self.as_user(self.accountant)
        self.assertEqual(accountant.post(f"{self.base}/other-income/", {
            "category": self.categories()["Rentals"], "method": str(self.method["cash"].pk), "amount": "10.00"}, format="json").status_code, 403)
        self.assertEqual(self.record("Rentals", "10.00").status_code, 201)
        self.assertEqual(accountant.post(f"{self.base}/other-income/{ok.data['id']}/reverse/", {"reason": "x"}, format="json").status_code, 404)

    def test_money_received_combines_fees_and_other_income_by_method(self):
        self.schedule(amount="1000.00")
        self.issued_invoice()
        self.assertEqual(self.pay("400.00", kind="cash", auto_allocate=True).status_code, 201)
        self.assertEqual(self.pay("300.00", kind="bank_transfer", reference="B1", auto_allocate=True).status_code, 201)
        self.record("Donations", "500.00", kind="bank_transfer", reference="D1")
        self.record("Canteen sales", "50.00")

        report = self.client.get(f"{self.base}/reports/money-received/").data
        self.assertEqual(report["total"], "1250.00")
        kinds = {row["kind"]: row["amount"] for row in report["by_kind"]}
        self.assertEqual(kinds["Cash"], "450.00")
        self.assertEqual(kinds["Bank"], "800.00")

        bank = self.client.get(f"{self.base}/reports/money-received/?kind=bank_transfer").data
        self.assertEqual(bank["total"], "800.00")
        fees = self.client.get(f"{self.base}/reports/money-received/?source=fees").data
        self.assertEqual(fees["total"], "700.00")
        by_grade = self.client.get(f"{self.base}/reports/money-received/?grade_level=JHS 1").data
        self.assertEqual(by_grade["total"], "700.00")
        other = self.client.get(f"{self.base}/reports/money-received/?source=other").data
        self.assertEqual(other["total"], "550.00")
        self.assertEqual(self.client.get(f"{self.base}/reports/money-received/?kind=bogus").status_code, 400)

    def test_class_filter_uses_allocations_not_current_student_grade(self):
        self.schedule(amount="1000.00")
        first = self.issued_invoice(period="Term 1")
        self.student.grade_level = "JHS 2"
        self.student.save(update_fields=("grade_level",))
        second = self.issued_invoice(period="Term 2")
        paid = self.pay("800.00", allocations=[
            {"invoice": first["id"], "amount": "300.00"},
            {"invoice": second["id"], "amount": "200.00"},
        ])
        self.assertEqual(paid.status_code, 201, paid.data)
        self.student.grade_level = "JHS 3"
        self.student.save(update_fields=("grade_level",))
        all_fees = self.client.get(f"{self.base}/reports/money-received/?source=fees").data
        self.assertEqual(all_fees["total"], "800.00")
        self.assertEqual(all_fees["rows"][0]["grade_level"], "JHS 1, JHS 2, unallocated or refunded")
        first_grade = self.client.get(f"{self.base}/reports/money-received/?grade_level=JHS 1").data
        self.assertEqual(first_grade["total"], "300.00")
        self.assertEqual(first_grade["rows"][0]["grade_level"], "JHS 1")
        self.assertEqual(self.client.get(f"{self.base}/reports/money-received/?grade_level=JHS 2").data["total"], "200.00")
        self.assertEqual(self.client.get(f"{self.base}/reports/money-received/?grade_level=JHS 3").data["total"], "0.00")
        self.assertIn("current invoice allocations", first_grade["basis"])

    def test_campus_filters_use_invoice_and_reversal_status(self):
        self.schedule(amount="1000.00")
        self.issued_invoice()
        second = Campus.objects.create(tenant=self.tenant, name="Second", code="SECOND")
        self.student.campus = second
        self.student.save(update_fields=("campus",))
        payment = self.pay("500.00", auto_allocate=True)
        self.assertEqual(payment.status_code, 201, payment.data)
        self.assertEqual(
            self.client.get(f"{self.base}/reports/money-received/", {"campus": str(self.campus.pk)}).data["total"],
            "500.00",
        )
        self.assertEqual(
            self.client.get(f"{self.base}/reports/money-received/", {"campus": str(second.pk)}).data["total"],
            "0.00",
        )
        reversed_ = self.client.post(f"{self.base}/payments/{payment.data['id']}/reverse/", {"reason": "error"}, format="json")
        self.assertEqual(reversed_.status_code, 200, reversed_.data)
        self.assertEqual(self.client.get(f"{self.base}/reports/money-received/").data["total"], "0.00")
        reversed_report = self.client.get(
            f"{self.base}/reports/money-received/", {"campus": str(self.campus.pk), "status": "reversed"}
        ).data
        self.assertEqual(reversed_report["total"], "500.00")
