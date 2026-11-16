from core.models import AuditEvent, Campus, Membership

from .models import Account, Expense, FinanceCampusGrant, Invoice, Payment, PaymentAllocation
from .test_finance import FinanceBase, Role, make_user


class CampusFinanceAccessTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.second = Campus.objects.create(tenant=self.tenant, name="Second", code="SECOND")
        self.membership = Membership.objects.get(tenant=self.tenant, user=self.accountant)
        self.grants = f"{self.base}/campus-access/{self.membership.pk}/"
        self.schedule()
        self.first = self.issued_invoice(period="First")
        self.student.campus = self.second
        self.student.save(update_fields=("campus",))
        self.second_invoice = self.issued_invoice(period="Second")
        self.student.campus = None
        self.student.save(update_fields=("campus",))
        self.unassigned = self.issued_invoice(period="Unassigned")

    def grant(self, ids):
        return self.client.put(self.grants, {"campuses": [str(item) for item in ids]}, format="json")

    def test_scoped_invoices_do_not_leak_other_campus_balances(self):
        accountant = self.as_user(self.accountant)
        self.assertEqual(accountant.get(f"{self.base}/invoices/").data, [])
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        rows = accountant.get(f"{self.base}/invoices/").data
        self.assertEqual([row["id"] for row in rows], [self.second_invoice["id"]])
        self.assertEqual(accountant.get(f"{self.base}/invoices/{self.first['id']}/").status_code, 404)
        self.assertEqual(accountant.get(f"{self.base}/invoices/{self.unassigned['id']}/").status_code, 404)
        detail = accountant.get(f"{self.base}/invoices/{self.second_invoice['id']}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["brought_forward"], "0.00")
        self.assertTrue(detail.data["brought_forward_campus_scoped"])
        self.assertIsNone(detail.data["calculation"])
        self.assertEqual([row["id"] for row in accountant.get(f"{self.base}/campuses/").data], [str(self.second.pk)])

    def test_scoped_issue_and_revocation(self):
        accountant = self.as_user(self.accountant)
        self.student.campus = self.second
        self.student.save(update_fields=("campus",))
        draft = self.client.post(f"{self.base}/invoices/", {
            "student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Draft",
        }, format="json")
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(self.grant([self.campus.pk]).status_code, 200)
        self.assertEqual(accountant.post(f"{self.base}/invoices/{draft.data['id']}/issue/").status_code, 404)
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        issued = accountant.post(f"{self.base}/invoices/{draft.data['id']}/issue/")
        self.assertEqual(issued.status_code, 200, issued.data)
        self.assertEqual(issued.data["status"], "issued")
        self.assertEqual(self.grant([]).status_code, 200)
        self.assertEqual(accountant.get(f"{self.base}/invoices/").data, [])
        self.assertTrue(AuditEvent.objects.filter(action="finance.campus_access_updated", entity_id=str(self.membership.pk)).exists())

    def test_outstanding_report_uses_granted_invoice_snapshots(self):
        Invoice.objects.filter(pk=self.second_invoice["id"]).update(grade_level="Basic 2")
        self.student.grade_level = "Current class"
        self.student.save(update_fields=("grade_level",))
        accountant = self.as_user(self.accountant)
        url = f"{self.base}/reports/outstanding/"
        self.assertEqual(accountant.get(url).data, [])
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        rows = accountant.get(url).data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["balance"], "1000.00")
        self.assertEqual(rows[0]["invoices"], 1)
        self.assertEqual(rows[0]["grade_level"], "Basic 2")
        self.assertEqual(accountant.get(f"{url}?campus={self.campus.pk}").status_code, 404)
        self.assertEqual(accountant.get(f"{url}?grade_level=JHS%201").data, [])
        self.assertEqual(self.client.get(url).data[0]["balance"], "3000.00")
        self.assertEqual(self.client.get(url).data[0]["grade_level"], "Current class")

    def test_money_received_reports_only_authorized_allocation_portions(self):
        payment = self.pay("800.00", allocations=[
            {"invoice": self.first["id"], "amount": "300.00"},
            {"invoice": self.second_invoice["id"], "amount": "200.00"},
            {"invoice": self.unassigned["id"], "amount": "100.00"},
        ])
        self.assertEqual(payment.status_code, 201, payment.data)
        accountant = self.as_user(self.accountant)
        url = f"{self.base}/reports/money-received/"
        self.assertEqual(accountant.get(url).data["total"], "0.00")
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        report = accountant.get(url)
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data["total"], "200.00")
        self.assertEqual(report.data["rows"][0]["amount"], "200.00")
        self.assertIn("authorized campuses", report.data["basis"])
        self.assertEqual(accountant.get(url, {"campus": str(self.campus.pk)}).status_code, 404)
        self.assertEqual(accountant.get(url, {"source": "other"}).status_code, 403)
        self.assertEqual(accountant.get(url, {"status": "pending"}).data["total"], "0.00")
        self.assertEqual(self.client.get(url).data["total"], "800.00")
        self.assertEqual(self.grant([]).status_code, 200)
        self.assertEqual(accountant.get(url).data["total"], "0.00")

    def test_accountant_records_only_fully_allocated_authorized_invoice_payments(self):
        accountant = self.as_user(self.accountant)
        url = f"{self.base}/invoices/{self.second_invoice['id']}/payments/"
        payload = {"method": str(self.method["cash"].pk), "amount": "200.00"}
        headers = {"HTTP_IDEMPOTENCY_KEY": "campus-payment"}
        self.assertEqual(accountant.post(url, payload, format="json", **headers).status_code, 404)
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        for invoice in (self.first, self.unassigned):
            self.assertEqual(accountant.post(
                f"{self.base}/invoices/{invoice['id']}/payments/", payload, format="json", **headers
            ).status_code, 404)
        self.assertEqual(accountant.post(url, payload, format="json").status_code, 400)
        self.assertEqual(accountant.post(url, {**payload, "amount": "1001.00"}, format="json", **headers).status_code, 400)
        self.assertEqual(accountant.post(url, {**payload, "method": str(self.method["mobile_money"].pk)},
                                         format="json", **headers).status_code, 400)
        result = accountant.post(url, payload, format="json", **headers)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["unallocated"], "0.00")
        self.student.grade_level = "Other campus class"
        self.student.save(update_fields=("grade_level",))
        receipt_url = f"{self.base}/payments/{result.data['id']}/receipt/"
        receipt = accountant.get(receipt_url)
        self.assertEqual(receipt.status_code, 200, receipt.data)
        self.assertEqual(receipt.data["student"]["grade_level"], "JHS 1")
        self.assertEqual(accountant.get(f"{self.base}/payments/{result.data['id']}/receipt.pdf").status_code, 200)
        self.assertEqual(PaymentAllocation.objects.get(payment_id=result.data["id"]).invoice_id, Invoice.objects.get(pk=self.second_invoice["id"]).pk)
        retry = accountant.post(url, payload, format="json", **headers)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data["id"], result.data["id"])
        self.assertEqual(accountant.post(url, {**payload, "amount": "300.00"}, format="json", **headers).status_code, 409)
        self.assertEqual(Payment.objects.filter(idempotency_key="campus-payment").count(), 1)
        self.assertEqual(Invoice.objects.get(pk=self.second_invoice["id"]).amount_paid, 200)
        self.assertEqual(self.grant([]).status_code, 200)
        self.assertEqual(accountant.post(url, payload, format="json", **headers).status_code, 404)
        self.assertEqual(accountant.get(receipt_url).status_code, 404)

    def test_receipt_rejects_mixed_and_unallocated_payments(self):
        mixed = self.pay("300.00", allocations=[
            {"invoice": self.first["id"], "amount": "100.00"},
            {"invoice": self.second_invoice["id"], "amount": "200.00"},
        ])
        advance = self.pay("150.00", allocations=[{"invoice": self.second_invoice["id"], "amount": "100.00"}])
        self.assertEqual(mixed.status_code, 201, mixed.data)
        self.assertEqual(advance.status_code, 201, advance.data)
        accountant = self.as_user(self.accountant)
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        for payment in (mixed, advance):
            self.assertEqual(accountant.get(f"{self.base}/payments/{payment.data['id']}/receipt/").status_code, 404)
        self.assertEqual(self.grant([self.campus.pk, self.second.pk]).status_code, 200)
        self.assertEqual(accountant.get(f"{self.base}/payments/{mixed.data['id']}/receipt/").status_code, 200)
        self.assertEqual(accountant.get(f"{self.base}/payments/{advance.data['id']}/receipt/").status_code, 404)

    def test_restricted_features_fail_closed_and_grants_are_tenant_isolated(self):
        accountant = self.as_user(self.accountant)
        for path in ("payments/", "reports/summary/", "reports/trial-balance/", "other-income/", "supplier-bills/"):
            self.assertEqual(accountant.get(f"{self.base}/{path}").status_code, 403, path)
        self.assertEqual(accountant.put(self.grants, {"campuses": [str(self.campus.pk)]}, format="json").status_code, 404)
        other_tenant = self.make_other_tenant()
        foreign_campus = Campus.objects.create(tenant=other_tenant, name="Foreign", code="X")
        foreign_accountant = make_user(other_tenant, "foreign-access@example.com", Role.ACCOUNTANT)
        foreign_membership = Membership.objects.get(tenant=other_tenant, user=foreign_accountant)
        self.assertEqual(self.grant([foreign_campus.pk]).status_code, 400)
        self.assertEqual(self.client.put(f"{self.base}/campus-access/{foreign_membership.pk}/", {
            "campuses": [str(self.campus.pk)],
        }, format="json").status_code, 404)
        self.assertFalse(FinanceCampusGrant.objects.filter(membership=self.membership).exists())

    def test_expense_views_are_limited_to_granted_campuses(self):
        from datetime import date
        from decimal import Decimal

        account = Account.objects.get(tenant=self.tenant, code="6100")
        expenses = [
            Expense.objects.create(
                tenant=self.tenant, campus=campus, expense_account=account, payee="Payee",
                description="Expense", transaction_date=date(2026, 1, 1), amount=Decimal("10.00")
            )
            for campus in (self.campus, self.second, None)
        ]
        accountant = self.as_user(self.accountant)
        self.assertEqual(accountant.get(f"{self.base}/expenses/").data, [])
        self.assertEqual(accountant.get(f"{self.base}/reports/expenses/").data, [])
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        self.assertEqual([item["id"] for item in accountant.get(f"{self.base}/expenses/").data], [str(expenses[1].pk)])
        report_url = f"{self.base}/reports/expenses/"
        self.assertEqual([item["id"] for item in accountant.get(report_url).data], [str(expenses[1].pk)])
        self.assertEqual(accountant.get(f"{report_url}?campus={self.campus.pk}").status_code, 404)
        self.assertEqual(len(accountant.get(f"{report_url}?campus={self.second.pk}").data), 1)
        self.assertEqual(len(self.client.get(report_url).data), 3)
        self.assertEqual(accountant.get(f"{self.base}/expenses/{expenses[1].pk}/").status_code, 200)
        for expense in (expenses[0], expenses[2]):
            self.assertEqual(accountant.get(f"{self.base}/expenses/{expense.pk}/").status_code, 404)
        self.assertEqual(accountant.post(f"{self.base}/expenses/{expenses[0].pk}/submit/").status_code, 404)
        self.assertEqual(accountant.post(f"{self.base}/expenses/{expenses[2].pk}/submit/").status_code, 404)
        self.assertEqual(accountant.post(f"{self.base}/expenses/{expenses[1].pk}/submit/").status_code, 200)

    def test_accountant_can_prepare_expenses_only_for_granted_campuses(self):
        accountant = self.as_user(self.accountant)
        account = Account.objects.get(tenant=self.tenant, code="6100")
        payload = {
            "payee": "Electrician", "description": "Classroom lights", "expense_account": str(account.pk),
            "transaction_date": "2026-10-10", "amount": "250.00",
        }
        self.assertEqual(accountant.get(f"{self.base}/accounts/").status_code, 403)
        self.assertEqual(accountant.get(f"{self.base}/accounts/?purpose=expense").data, [])
        self.assertEqual(accountant.post(f"{self.base}/expenses/", payload, format="json").status_code, 404)
        self.assertEqual(self.grant([self.second.pk]).status_code, 200)
        options = accountant.get(f"{self.base}/accounts/?purpose=expense")
        self.assertEqual(options.status_code, 200)
        self.assertTrue(options.data)
        self.assertTrue(all(row["type"] == "expense" and row["is_active"] and not row["is_header"] for row in options.data))
        self.assertEqual(accountant.post(f"{self.base}/expenses/", payload, format="json").status_code, 404)
        self.assertEqual(accountant.post(f"{self.base}/expenses/", {**payload, "campus": str(self.campus.pk)}, format="json").status_code, 404)
        created = accountant.post(f"{self.base}/expenses/", {**payload, "campus": str(self.second.pk)}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["campus"], str(self.second.pk))
        expense_id = created.data["id"]
        self.assertEqual(accountant.post(f"{self.base}/expenses/{expense_id}/approve/").status_code, 404)
        self.assertEqual(accountant.post(f"{self.base}/expenses/{expense_id}/pay/").status_code, 403)
        self.assertEqual(accountant.post(f"{self.base}/expenses/{expense_id}/submit/").status_code, 200)
        approved = self.client.post(f"{self.base}/expenses/{expense_id}/approve/")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertTrue(AuditEvent.objects.filter(action="finance.expense_created", entity_id=expense_id).exists())
        self.assertTrue(AuditEvent.objects.filter(action="finance.expense_submitted", entity_id=expense_id).exists())
        self.assertEqual(self.grant([]).status_code, 200)
        self.assertEqual(accountant.get(f"{self.base}/expenses/{expense_id}/").status_code, 404)

    def make_other_tenant(self):
        from core.tests import create_tenant
        return create_tenant(name="Other", slug="other-campus-grants", email="other@example.com", phone="1")
