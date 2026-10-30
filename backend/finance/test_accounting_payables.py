from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from .models import (
    Account,
    JournalEntry,
    OpeningBalanceImport,
    PaymentMethod,
    Supplier,
    SupplierBill,
)
from .test_finance import FinanceBase


class AccountingAndPayablesTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.expense_account = Account.objects.get(tenant=self.tenant, code="6100")
        self.cash_account = Account.objects.get(tenant=self.tenant, system_key="cash")
        self.ap_account = Account.objects.get(tenant=self.tenant, system_key="accounts_payable")
        self.supplier = Supplier.objects.create(
            tenant=self.tenant, code="SUP-1", name="School Supplies Ltd", created_by=self.owner
        )

    def account_line(self, account, debit="0.00", credit="0.00"):
        return {"account": str(account.pk), "debit": debit, "credit": credit, "description": "Opening"}

    def test_opening_balance_requires_reconciliation_and_approval_before_posting(self):
        response = self.client.post(
            f"{self.base}/opening-balances/",
            {
                "name": "Opening 2026",
                "as_of_date": "2026-09-01",
                "import_reference": "MIGRATION-1",
                "lines": [self.account_line(self.cash_account, debit="1000.00"), self.account_line(self.ap_account, credit="900.00")],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(OpeningBalanceImport.objects.count(), 0)

        response = self.client.post(
            f"{self.base}/opening-balances/",
            {
                "name": "Opening 2026",
                "as_of_date": "2026-09-01",
                "import_reference": "MIGRATION-1",
                "lines": [self.account_line(self.cash_account, debit="1000.00"), self.account_line(self.ap_account, credit="1000.00")],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        import_id = response.data["id"]
        self.assertEqual(JournalEntry.objects.filter(source_type="opening_balance").count(), 0)
        denied = self.as_user(self.accountant).post(f"{self.base}/opening-balances/{import_id}/approve/", format="json")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(
            self.client.post(f"{self.base}/opening-balances/{import_id}/approve/", format="json").status_code,
            409,
        )
        approved = self.as_user(self.admin).post(f"{self.base}/opening-balances/{import_id}/approve/", format="json")
        self.assertEqual(approved.data["status"], "approved")
        posted = self.client.post(f"{self.base}/opening-balances/{import_id}/post/", format="json")
        self.assertEqual(posted.data["status"], "posted")
        self.client.post(f"{self.base}/opening-balances/{import_id}/post/", format="json")
        self.assertEqual(JournalEntry.objects.filter(source_type="opening_balance").count(), 1)
        mismatch = self.client.get(f"{self.base}/reports/accounts-payable/reconciliation/").data
        self.assertEqual(mismatch["difference"], "1000.00")
        allocation = self.client.post(
            f"{self.base}/supplier-opening-balances/",
            {
                "opening_import": import_id,
                "supplier": str(self.supplier.pk),
                "debit": "0.00",
                "credit": "1000.00",
            },
            format="json",
        )
        self.assertEqual(allocation.status_code, 201, allocation.data)
        reconciled = self.client.get(f"{self.base}/reports/accounts-payable/reconciliation/").data
        self.assertTrue(reconciled["reconciled"])
        self.assertEqual(reconciled["difference"], "0.00")
        statement = self.client.get(f"{self.base}/suppliers/{self.supplier.pk}/statement/").data
        self.assertEqual(statement["closing_balance"], "1000.00")
        self.cash_account.is_active = False
        self.cash_account.save(update_fields=("is_active",))
        reversed_import = self.client.post(
            f"{self.base}/opening-balances/{import_id}/reverse/",
            {"reason": "Correcting the migration"},
            format="json",
        )
        self.assertEqual(reversed_import.status_code, 200, reversed_import.data)
        self.assertEqual(reversed_import.data["status"], "reversed")
        self.assertEqual(JournalEntry.objects.filter(source_type="reversal").count(), 1)
        self.assertEqual(
            self.client.get(f"{self.base}/reports/accounts-payable/reconciliation/").data["subledger_balance"],
            "0.00",
        )
        self.assertEqual(
            self.client.get(f"{self.base}/suppliers/{self.supplier.pk}/statement/").data["closing_balance"],
            "0.00",
        )

    def test_supplier_bill_payment_and_statement_post_balanced_entries(self):
        draft = self.client.post(
            f"{self.base}/supplier-bills/",
            {
                "supplier": str(self.supplier.pk),
                "supplier_reference": "INV-EXT-10",
                "invoice_date": "2026-10-01",
                "due_date": "2026-10-31",
                "lines": [{
                    "account": str(self.expense_account.pk),
                    "description": "Classroom books",
                    "quantity": "2.5",
                    "unit_cost": "40.00",
                }],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(draft.data["total"], "100.00")
        bill_id = draft.data["id"]
        self.assertEqual(JournalEntry.objects.filter(source_type="supplier_bill").count(), 0)

        self.client.post(f"{self.base}/supplier-bills/{bill_id}/submit/", format="json")
        self.assertEqual(
            self.client.post(f"{self.base}/supplier-bills/{bill_id}/approve/", format="json").status_code,
            409,
        )
        approved = self.as_user(self.admin).post(f"{self.base}/supplier-bills/{bill_id}/approve/", format="json")
        self.assertEqual(approved.data["status"], "approved")
        posted = self.client.post(f"{self.base}/supplier-bills/{bill_id}/post/", format="json")
        self.assertEqual(posted.data["status"], "posted")
        self.client.post(f"{self.base}/supplier-bills/{bill_id}/post/", format="json")
        self.assertEqual(JournalEntry.objects.filter(source_type="supplier_bill").count(), 1)

        voucher = self.client.post(
            f"{self.base}/supplier-vouchers/",
            {
                "supplier": str(self.supplier.pk),
                "payment_date": "2026-10-15",
                "account": str(self.cash_account.pk),
                "amount": "60.00",
                "reference": "CHQ-100",
                "allocations": [{"bill": bill_id, "amount": "60.00"}],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="supplier-pay-1",
        )
        self.assertEqual(voucher.status_code, 201, voucher.data)
        voucher_id = voucher.data["id"]
        self.assertEqual(self.as_user(self.accountant).post(f"{self.base}/supplier-vouchers/{voucher_id}/approve/", format="json").status_code, 404)
        self.assertEqual(
            self.client.post(f"{self.base}/supplier-vouchers/{voucher_id}/approve/", format="json").status_code,
            409,
        )
        self.as_user(self.admin).post(f"{self.base}/supplier-vouchers/{voucher_id}/approve/", format="json")
        paid = self.as_user(self.accountant).post(f"{self.base}/supplier-vouchers/{voucher_id}/pay/", format="json")
        self.assertEqual(paid.data["status"], "paid")
        self.assertEqual(paid.data["allocations"][0]["amount"], "60.00")
        bill = self.client.get(f"{self.base}/supplier-bills/{bill_id}/").data
        self.assertEqual((bill["status"], bill["balance"]), ("partially_paid", "40.00"))
        statement = self.client.get(f"{self.base}/suppliers/{self.supplier.pk}/statement/").data
        self.assertEqual(statement["closing_balance"], "40.00")

        historical_payables = self.client.get(
            f"{self.base}/reports/accounts-payable/?as_of=2026-10-10"
        ).data
        self.assertEqual(historical_payables[0]["balance"], "100.00")
        payable_report = self.client.get(f"{self.base}/reports/accounts-payable/").data
        self.assertEqual(payable_report[0]["balance"], "40.00")
        trial = self.client.get(f"{self.base}/reports/trial-balance/").data
        self.assertTrue(trial["balanced"])

    def test_supplier_records_are_tenant_scoped(self):
        other = self.as_user(self.owner).get(f"{self.base}/suppliers/").data
        self.assertEqual(len(other), 1)
        unknown = self.client.get(f"{self.base}/suppliers/00000000-0000-0000-0000-000000000001/statement/")
        self.assertEqual(unknown.status_code, 404)

    def test_direct_expense_requires_approval_and_posts_to_payable_before_payment(self):
        draft = self.client.post(
            f"{self.base}/expenses/",
            {
                "payee": "Local electrician",
                "description": "Replace classroom lights",
                "expense_account": str(self.expense_account.pk),
                "transaction_date": "2026-10-10",
                "amount": "250.00",
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        expense_id = draft.data["id"]
        self.assertEqual(JournalEntry.objects.filter(source_type="expense").count(), 0)
        unapproved_payment = self.client.post(
            f"{self.base}/expenses/{expense_id}/pay/",
            {
                "amount": "250.00",
                "account": str(self.cash_account.pk),
                "payment_date": "2026-10-11",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="expense-pay-before-approval",
        )
        self.assertEqual(unapproved_payment.status_code, 409)

        self.client.post(f"{self.base}/expenses/{expense_id}/submit/", format="json")
        self.assertEqual(
            self.client.post(f"{self.base}/expenses/{expense_id}/approve/", format="json").status_code,
            409,
        )
        approved = self.as_user(self.admin).post(f"{self.base}/expenses/{expense_id}/approve/", format="json")
        self.assertEqual(approved.data["status"], "approved")
        self.assertEqual(JournalEntry.objects.filter(source_type="expense").count(), 1)
        paid = self.client.post(
            f"{self.base}/expenses/{expense_id}/pay/",
            {
                "amount": "250.00",
                "account": str(self.cash_account.pk),
                "payment_date": "2026-10-11",
                "reference": "CASH-10",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="expense-pay-1",
        )
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertEqual(paid.data["status"], "paid")
        self.assertEqual(JournalEntry.objects.filter(source_type="expense_payment").count(), 1)
        historical_payables = self.client.get(
            f"{self.base}/reports/accounts-payable/?as_of=2026-10-10"
        ).data
        self.assertEqual(historical_payables[0]["balance"], "250.00")
        conflicting_retry = self.client.post(
            f"{self.base}/expenses/{expense_id}/pay/",
            {
                "amount": "250.00",
                "account": str(self.cash_account.pk),
                "payment_date": "2026-10-11",
                "reference": "DIFFERENT-REFERENCE",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="expense-pay-1",
        )
        self.assertEqual(conflicting_retry.status_code, 409)
        self.assertEqual(self.client.get(f"{self.base}/reports/accounts-payable/").data, [])

    def test_expense_payment_must_come_from_a_money_account(self):
        expense = self.client.post(
            f"{self.base}/expenses/",
            {
                "payee": "Plumber",
                "description": "Repair",
                "expense_account": str(self.expense_account.pk),
                "transaction_date": "2026-10-10",
                "amount": "100.00",
            },
            format="json",
        ).data["id"]
        self.client.post(f"{self.base}/expenses/{expense}/submit/", format="json")
        self.as_user(self.admin).post(f"{self.base}/expenses/{expense}/approve/", format="json")
        receivable = Account.objects.get(tenant=self.tenant, system_key="receivable")
        property_account = Account.objects.get(tenant=self.tenant, code="1500")
        for n, account in enumerate((receivable, property_account)):
            response = self.client.post(
                f"{self.base}/expenses/{expense}/pay/",
                {"amount": "100.00", "account": str(account.pk), "payment_date": "2026-10-11"},
                format="json",
                HTTP_IDEMPOTENCY_KEY=f"bad-source-{n}",
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(JournalEntry.objects.filter(source_type="expense_payment").count(), 0)

        # A school-defined bank account linked to an active payment method is allowed.
        petty = Account.objects.create(
            tenant=self.tenant, code="1130", name="Second bank", type=Account.Type.ASSET, normal_balance="debit"
        )
        PaymentMethod.objects.create(tenant=self.tenant, name="Second bank", kind="bank_transfer", account=petty)
        ok = self.client.post(
            f"{self.base}/expenses/{expense}/pay/",
            {"amount": "100.00", "account": str(petty.pk), "payment_date": "2026-10-11"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="good-source",
        )
        self.assertEqual(ok.status_code, 201, ok.data)

    def test_bill_and_voucher_reject_cross_supplier_and_duplicate_references(self):
        draft = self.client.post(
            f"{self.base}/supplier-bills/",
            {
                "supplier": str(self.supplier.pk),
                "supplier_reference": "DUP-1",
                "invoice_date": "2026-10-01",
                "due_date": "2026-10-10",
                "lines": [{
                    "account": str(self.expense_account.pk),
                    "description": "Food",
                    "quantity": "1",
                    "unit_cost": "50.00",
                }],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201)
        duplicate = self.client.post(
            f"{self.base}/supplier-bills/",
            {
                "supplier": str(self.supplier.pk),
                "supplier_reference": "DUP-1",
                "invoice_date": "2026-10-01",
                "due_date": "2026-10-10",
                "lines": [{
                    "account": str(self.expense_account.pk),
                    "description": "Food",
                    "quantity": "1",
                    "unit_cost": "50.00",
                }],
            },
            format="json",
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_purchase_tax_rates_post_to_configured_accounts(self):
        tax_account = Account.objects.create(
            tenant=self.tenant,
            code="6190",
            name="Purchase tax expense",
            type=Account.Type.EXPENSE,
        )
        created = self.client.post(
            f"{self.base}/supplier-tax-codes/",
            {
                "code": "VAT10",
                "name": "Configured purchase tax",
                "rate": "10.00",
                "account": str(tax_account.pk),
                "is_inclusive": False,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)

        bill = self.client.post(
            f"{self.base}/supplier-bills/",
            {
                "supplier": str(self.supplier.pk),
                "supplier_reference": "TAX-EXCLUSIVE",
                "invoice_date": "2026-10-01",
                "due_date": "2026-10-31",
                "lines": [{
                    "account": str(self.expense_account.pk),
                    "tax_code": created.data["id"],
                    "description": "Taxable supplies",
                    "quantity": "1",
                    "unit_cost": "100.00",
                }],
            },
            format="json",
        )
        self.assertEqual(bill.status_code, 201, bill.data)
        self.assertEqual(bill.data["total"], "110.00")
        self.assertEqual(bill.data["lines"][0]["tax_amount"], "10.00")
        bill_id = bill.data["id"]
        self.client.post(f"{self.base}/supplier-bills/{bill_id}/submit/", format="json")
        self.as_user(self.admin).post(f"{self.base}/supplier-bills/{bill_id}/approve/", format="json")
        posted = self.client.post(f"{self.base}/supplier-bills/{bill_id}/post/", format="json")
        self.assertEqual(posted.status_code, 200, posted.data)
        entry = JournalEntry.objects.get(source_type="supplier_bill", source_id=bill_id)
        self.assertEqual(
            list(entry.lines.order_by("account_id").values_list("account_id", "debit", "credit")),
            sorted([
                (self.expense_account.pk, Decimal("100.00"), Decimal("0.00")),
                (tax_account.pk, Decimal("10.00"), Decimal("0.00")),
                (self.ap_account.pk, Decimal("0.00"), Decimal("110.00")),
            ]),
        )

        inclusive = self.client.post(
            f"{self.base}/supplier-tax-codes/",
            {
                "code": "VAT10INC",
                "name": "Inclusive purchase tax",
                "rate": "10.00",
                "account": str(tax_account.pk),
                "is_inclusive": True,
            },
            format="json",
        )
        inclusive_bill = self.client.post(
            f"{self.base}/supplier-bills/",
            {
                "supplier": str(self.supplier.pk),
                "supplier_reference": "TAX-INCLUSIVE",
                "invoice_date": "2026-10-01",
                "due_date": "2026-10-31",
                "lines": [{
                    "account": str(self.expense_account.pk),
                    "tax_code": inclusive.data["id"],
                    "description": "Tax-inclusive supplies",
                    "quantity": "1",
                    "unit_cost": "110.00",
                }],
            },
            format="json",
        )
        self.assertEqual(inclusive_bill.status_code, 201, inclusive_bill.data)
        self.assertEqual(inclusive_bill.data["total"], "110.00")
        self.assertEqual(inclusive_bill.data["lines"][0]["amount"], "100.00")
        self.assertEqual(inclusive_bill.data["lines"][0]["tax_amount"], "10.00")

    def test_opening_balance_csv_preview_and_private_bill_document(self):
        content = (
            "account_code,debit,credit,description\n"
            f"{self.cash_account.code},100.00,0.00,Cash opening\n"
            f"{self.ap_account.code},0.00,100.00,AP opening\n"
        ).encode()
        preview = self.client.post(
            f"{self.base}/opening-balances/preview-csv/",
            {"file": SimpleUploadedFile("opening.csv", content, content_type="text/csv")},
            format="multipart",
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertTrue(preview.data["balanced"])
        self.assertEqual(len(preview.data["lines"]), 2)
        self.assertEqual(OpeningBalanceImport.objects.count(), 0)

        bill = self.client.post(
            f"{self.base}/supplier-bills/",
            {
                "supplier": str(self.supplier.pk),
                "supplier_reference": "DOC-1",
                "invoice_date": "2026-10-01",
                "due_date": "2026-10-31",
                "lines": [{
                    "account": str(self.expense_account.pk),
                    "description": "Supplies",
                    "quantity": "1",
                    "unit_cost": "20.00",
                }],
            },
            format="json",
        )
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                upload = self.client.post(
                    f"{self.base}/supplier-bills/{bill.data['id']}/documents/",
                    {"file": SimpleUploadedFile("invoice.pdf", b"%PDF-1.7\nprivate bill proof")},
                    format="multipart",
                )
                self.assertEqual(upload.status_code, 201, upload.data)
                downloaded = self.client.get(
                    f"{self.base}/supplier-bill-documents/{upload.data['id']}/"
                )
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(b"".join(downloaded.streaming_content), b"%PDF-1.7\nprivate bill proof")
                self.assertEqual(downloaded["X-Content-Type-Options"], "nosniff")

        rejected = self.client.post(
            f"{self.base}/supplier-bills/{bill.data['id']}/documents/",
            {"file": SimpleUploadedFile("invoice.pdf", b"not a pdf")},
            format="multipart",
        )
        self.assertEqual(rejected.status_code, 400)

    def test_general_ledger_opening_and_running_balance_agree_with_trial_balance(self):
        entry = self.client.post(
            f"{self.base}/opening-balances/",
            {
                "name": "Ledger opening",
                "as_of_date": "2026-01-01",
                "lines": [self.account_line(self.cash_account, debit="500.00"), self.account_line(self.ap_account, credit="500.00")],
            },
            format="json",
        )
        self.assertEqual(entry.status_code, 201)
        import_id = entry.data["id"]
        self.as_user(self.admin).post(f"{self.base}/opening-balances/{import_id}/approve/", format="json")
        self.client.post(f"{self.base}/opening-balances/{import_id}/post/", format="json")
        ledger = self.client.get(
            f"{self.base}/reports/general-ledger/",
            {"account": str(self.cash_account.pk), "start": "2026-02-01"},
        )
        self.assertEqual(ledger.status_code, 200, ledger.data)
        self.assertEqual(ledger.data["opening_balance"], "500.00")
        self.assertEqual(ledger.data["closing_balance"], "500.00")
        self.assertTrue(self.client.get(f"{self.base}/reports/trial-balance/").data["balanced"])
