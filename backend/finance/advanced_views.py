"""Accounting foundation, supplier, payable and expense endpoints."""

import csv
import io

from django.http import FileResponse
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from core.models import Campus

from . import accounting, payables
from .ledger import FinanceError, money
from .models import (
    Account,
    Expense,
    ExpensePayment,
    JournalEntry,
    OpeningBalanceImport,
    Supplier,
    SupplierBill,
    SupplierBillDocument,
    SupplierOpeningBalance,
    SupplierPaymentVoucher,
    SupplierTaxCode,
)
from .serializers import parse_date, parse_money, parse_percentage, parse_quantity, parse_text, parse_uuid
from .views import APPROVERS, FINANCE, FinanceView, _range


def _opening_lines(data):
    rows = data.get("lines")
    if not isinstance(rows, list):
        raise FinanceError("Lines must be a list.", 400, "lines")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise FinanceError("Each line must be an object.", 400, "lines")
        debit = money(row.get("debit", "0.00"), "lines")
        credit = money(row.get("credit", "0.00"), "lines")
        if debit < 0 or credit < 0 or (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
            raise FinanceError("Each line must have a positive debit or credit, but not both.", 400, "lines")
        result.append({
            "account": parse_uuid(row.get("account"), "lines"),
            "debit": debit,
            "credit": credit,
            "description": parse_text(row, "description", 240, required=False),
        })
    return result


class OpeningBalanceListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        imports = OpeningBalanceImport.objects.filter(tenant_id=tenant_id).select_related("created_by", "approved_by")
        return Response([accounting.opening_balance_out(item) for item in imports[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        imported = accounting.create_opening_import(
            tenant_id,
            name=parse_text(data, "name", 120),
            as_of_date=parse_date(data, "as_of_date"),
            import_reference=parse_text(data, "import_reference", 120, required=False),
            lines=_opening_lines(data),
            user=request.user,
        )
        self.audit(request, tenant_id, "opening_balance_imported", "OpeningBalanceImport", imported.pk)
        return Response(accounting.opening_balance_out(imported, detail=True), status=201)


class OpeningBalanceCsvPreviewView(FinanceView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        uploaded = request.FILES.get("file")
        if uploaded is None or uploaded.size <= 0 or uploaded.size > 2 * 1024 * 1024:
            raise FinanceError("Choose a CSV file no larger than 2 MB.", 400, "file")
        try:
            content = uploaded.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise FinanceError("CSV files must use UTF-8 encoding.", 400, "file")
        reader = csv.DictReader(io.StringIO(content))
        if reader.fieldnames is None:
            raise FinanceError("The CSV file is empty.", 400, "file")
        headers = [header.strip().lower() for header in reader.fieldnames]
        required = {"account_code", "debit", "credit"}
        if len(set(headers)) != len(headers) or not required.issubset(headers):
            raise FinanceError("CSV headers must include account_code, debit, and credit.", 400, "file")
        reader.fieldnames = headers
        account_by_code = {}
        for account in Account.objects.filter(tenant_id=tenant_id, is_active=True, is_header=False):
            normalized_code = account.code.casefold()
            if normalized_code in account_by_code:
                raise FinanceError("The chart has account codes that differ only by case; CSV lookup is ambiguous.", 409)
            account_by_code[normalized_code] = account
        lines = []
        seen = set()
        for index, row in enumerate(reader, start=2):
            if index > 5001:
                raise FinanceError("CSV files can contain at most 5,000 account rows.", 400, "file")
            if None in row:
                raise FinanceError(f"CSV row {index} has more columns than the header.", 400, "file")
            code = str(row.get("account_code") or "").strip().casefold()
            account = account_by_code.get(code)
            if account is None:
                raise FinanceError(f"CSV row {index} uses an unknown or inactive posting account.", 400, "file")
            if account.pk in seen:
                raise FinanceError(f"CSV row {index} repeats account {account.code}.", 400, "file")
            seen.add(account.pk)
            debit = money(row.get("debit") or "0", f"row {index} debit")
            credit = money(row.get("credit") or "0", f"row {index} credit")
            if debit < 0 or credit < 0 or (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
                raise FinanceError(f"CSV row {index} must have one positive debit or credit.", 400, "file")
            lines.append({
                "account": account.pk,
                "debit": debit,
                "credit": credit,
                "description": str(row.get("description") or "").strip()[:240],
            })
        accounts, debit, credit = accounting.validate_opening_lines(tenant_id, lines)
        return Response({
            "lines": [
                {
                    "account": str(line["account"]),
                    "account_code": accounts[line["account"]].code,
                    "account_name": accounts[line["account"]].name,
                    "debit": str(line["debit"]),
                    "credit": str(line["credit"]),
                    "description": line["description"],
                }
                for line in lines
            ],
            "total_debit": str(debit),
            "total_credit": str(credit),
            "balanced": debit > 0 and debit == credit,
        })


class OpeningBalanceDetailView(FinanceView):
    def get(self, request, tenant_id, import_id):
        self.member(request, tenant_id)
        imported = self.get_or_404(OpeningBalanceImport, tenant_id, import_id)
        return Response(accounting.opening_balance_out(imported, detail=True))


class OpeningBalanceApproveView(FinanceView):
    def post(self, request, tenant_id, import_id):
        self.member(request, tenant_id, APPROVERS)
        imported = accounting.approve_opening_import(tenant_id, import_id, user=request.user)
        self.audit(request, tenant_id, "opening_balance_approved", "OpeningBalanceImport", imported.pk)
        return Response(accounting.opening_balance_out(imported, detail=True))


class OpeningBalancePostView(FinanceView):
    def post(self, request, tenant_id, import_id):
        self.member(request, tenant_id, APPROVERS)
        imported, created = accounting.post_opening_import(tenant_id, import_id, user=request.user)
        if created:
            self.audit(request, tenant_id, "opening_balance_posted", "OpeningBalanceImport", imported.pk)
        return Response(accounting.opening_balance_out(imported, detail=True))


class OpeningBalanceReverseView(FinanceView):
    def post(self, request, tenant_id, import_id):
        self.member(request, tenant_id, APPROVERS)
        imported, _, created = accounting.reverse_opening_import(
            tenant_id, import_id, user=request.user, reason=parse_text(request.data, "reason", 240)
        )
        if created:
            self.audit(request, tenant_id, "opening_balance_reversed", "OpeningBalanceImport", imported.pk)
        return Response(accounting.opening_balance_out(imported, detail=True))


class JournalRegisterView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        start, end = _range(request)
        return Response(accounting.journal_register(tenant_id, start=start, end=end))


class CampusDimensionListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        return Response([
            {"id": str(campus.pk), "code": campus.code, "name": campus.name, "status": campus.status}
            for campus in Campus.objects.filter(tenant_id=tenant_id).order_by("name")
        ])


class JournalDetailView(FinanceView):
    def get(self, request, tenant_id, journal_id):
        self.member(request, tenant_id)
        entry = self.get_or_404(JournalEntry, tenant_id, journal_id)
        return Response({
            "id": str(entry.pk),
            "number": entry.number,
            "date": entry.entry_date.isoformat(),
            "memo": entry.memo,
            "source_type": entry.source_type,
            "source_id": entry.source_id,
            "status": entry.status,
            "created_by": entry.created_by.full_name if entry.created_by_id else "",
            "posted_by": entry.posted_by.full_name if entry.posted_by_id else "",
            "posted_at": entry.posted_at.isoformat(),
            "lines": [
                {
                    "account": f"{line.account.code} {line.account.name}",
                    "campus": line.campus.name if line.campus_id else "",
                    "description": line.description,
                    "debit": str(line.debit),
                    "credit": str(line.credit),
                }
                for line in entry.lines.select_related("account", "campus")
            ],
        })


class GeneralLedgerView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        params = request.query_params
        account_id = parse_uuid(params.get("account"), "account")
        start, end = _range(request)
        campus_id = parse_uuid(params["campus"], "campus") if params.get("campus") else None
        if campus_id and not Campus.objects.filter(tenant_id=tenant_id, pk=campus_id).exists():
            raise NotFound()
        return Response(accounting.general_ledger(
            tenant_id, account_id=account_id, start=start, end=end, campus_id=campus_id
        ))


class SupplierListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        return Response([
            payables.supplier_out(supplier)
            for supplier in Supplier.objects.filter(tenant_id=tenant_id)
        ])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        try:
            terms = int(data.get("payment_terms_days", 30))
        except (TypeError, ValueError):
            raise FinanceError("Payment terms must be a whole number.", 400, "payment_terms_days")
        if not 0 <= terms <= 365:
            raise FinanceError("Payment terms must be between 0 and 365 days.", 400, "payment_terms_days")
        supplier = payables.create_supplier(tenant_id, data={
            "code": parse_text(data, "code", 30).upper(),
            "name": parse_text(data, "name", 160),
            "contact_person": parse_text(data, "contact_person", 120, required=False),
            "email": parse_text(data, "email", 254, required=False),
            "phone": parse_text(data, "phone", 32, required=False),
            "address": parse_text(data, "address", 240, required=False),
            "tax_id": parse_text(data, "tax_id", 40, required=False),
            "payment_terms_days": terms,
            "notes": parse_text(data, "notes", 2000, required=False),
        }, user=request.user)
        self.audit(request, tenant_id, "supplier_created", "Supplier", supplier.pk)
        return Response(payables.supplier_out(supplier), status=201)


class SupplierOpeningBalanceListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        balances = SupplierOpeningBalance.objects.filter(tenant_id=tenant_id).select_related(
            "opening_import", "supplier"
        )
        return Response([payables.supplier_opening_balance_out(item) for item in balances[:1000]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        balance = payables.create_supplier_opening_balance(
            tenant_id,
            import_id=parse_uuid(data.get("opening_import"), "opening_import"),
            supplier_id=parse_uuid(data.get("supplier"), "supplier"),
            debit=money(data.get("debit", "0"), "debit"),
            credit=money(data.get("credit", "0"), "credit"),
            user=request.user,
        )
        self.audit(request, tenant_id, "supplier_opening_balance_allocated", "SupplierOpeningBalance", balance.pk)
        return Response(payables.supplier_opening_balance_out(balance), status=201)


class SupplierDetailView(FinanceView):
    def patch(self, request, tenant_id, supplier_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        fields = {}
        limits = {
            "code": 30, "name": 160, "contact_person": 120, "email": 254, "phone": 32,
            "address": 240, "tax_id": 40, "notes": 2000,
        }
        for field, limit in limits.items():
            if field in data:
                fields[field] = parse_text(data, field, limit, required=field in ("code", "name"))
        if "code" in fields:
            fields["code"] = fields["code"].upper()
        if "payment_terms_days" in data:
            try:
                fields["payment_terms_days"] = int(data["payment_terms_days"])
            except (TypeError, ValueError):
                raise FinanceError("Payment terms must be a whole number.", 400, "payment_terms_days")
            if not 0 <= fields["payment_terms_days"] <= 365:
                raise FinanceError("Payment terms must be between 0 and 365 days.", 400, "payment_terms_days")
        if "is_active" in data:
            if not isinstance(data["is_active"], bool):
                raise FinanceError("is_active must be true or false.", 400, "is_active")
            fields["is_active"] = data["is_active"]
        supplier = payables.update_supplier(tenant_id, supplier_id, fields=fields)
        self.audit(request, tenant_id, "supplier_updated", "Supplier", supplier.pk)
        return Response(payables.supplier_out(supplier))


def _bill_lines(data):
    rows = data.get("lines")
    if not isinstance(rows, list):
        raise FinanceError("Bill lines must be a list.", 400, "lines")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise FinanceError("Each bill line must be an object.", 400, "lines")
        result.append({
            "account": parse_uuid(row.get("account"), "lines"),
            "tax_code": parse_uuid(row["tax_code"], "tax_code") if row.get("tax_code") else None,
            "campus": parse_uuid(row["campus"], "campus") if row.get("campus") else None,
            "description": parse_text(row, "description", 240),
            "quantity": parse_quantity(row.get("quantity", "1"), "quantity"),
            "unit_cost": parse_money(row, "unit_cost"),
        })
    return result


class SupplierBillListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        queryset = SupplierBill.objects.filter(tenant_id=tenant_id).select_related("supplier").prefetch_related("documents")
        if request.query_params.get("supplier"):
            queryset = queryset.filter(supplier_id=parse_uuid(request.query_params["supplier"], "supplier"))
        if request.query_params.get("status"):
            if request.query_params["status"] not in SupplierBill.Status.values:
                raise FinanceError("Choose a valid bill status.", 400, "status")
            queryset = queryset.filter(status=request.query_params["status"])
        return Response([payables.bill_out(bill) for bill in queryset[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        bill = payables.create_supplier_bill(
            tenant_id,
            supplier_id=parse_uuid(data.get("supplier"), "supplier"),
            supplier_reference=parse_text(data, "supplier_reference", 80),
            invoice_date=parse_date(data, "invoice_date"),
            due_date=parse_date(data, "due_date"),
            lines=_bill_lines(data),
            user=request.user,
        )
        self.audit(request, tenant_id, "supplier_bill_created", "SupplierBill", bill.pk, total=str(bill.total))
        return Response(payables.bill_out(bill, detail=True), status=201)


class SupplierTaxCodeListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        return Response([
            payables.tax_code_out(code)
            for code in SupplierTaxCode.objects.filter(tenant_id=tenant_id).select_related("account")
        ])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        is_inclusive = data.get("is_inclusive", False)
        if not isinstance(is_inclusive, bool):
            raise FinanceError("is_inclusive must be true or false.", 400, "is_inclusive")
        code = payables.create_supplier_tax_code(
            tenant_id,
            code=parse_text(data, "code", 30).upper(),
            name=parse_text(data, "name", 100),
            rate=parse_percentage(data.get("rate")),
            account_id=parse_uuid(data.get("account"), "account"),
            is_inclusive=is_inclusive,
        )
        self.audit(request, tenant_id, "supplier_tax_code_created", "SupplierTaxCode", code.pk)
        return Response(payables.tax_code_out(code), status=201)


class SupplierBillDocumentUploadView(FinanceView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, tenant_id, bill_id):
        self.member(request, tenant_id, FINANCE)
        document = payables.upload_supplier_bill_document(
            tenant_id, bill_id, uploaded_file=request.FILES.get("file"), user=request.user
        )
        self.audit(request, tenant_id, "supplier_bill_document_uploaded", "SupplierBillDocument", document.pk)
        return Response({
            "id": str(document.pk),
            "name": document.original_name,
            "content_type": document.content_type,
            "file_size": document.file_size,
        }, status=201)


class SupplierBillDocumentDownloadView(FinanceView):
    def get(self, request, tenant_id, document_id):
        self.member(request, tenant_id)
        document = self.get_or_404(SupplierBillDocument, tenant_id, document_id)
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.original_name,
            content_type=document.content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response


class SupplierBillDetailView(FinanceView):
    def get(self, request, tenant_id, bill_id):
        self.member(request, tenant_id)
        bill = self.get_or_404(SupplierBill, tenant_id, bill_id)
        return Response(payables.bill_out(bill, detail=True))


class SupplierBillSubmitView(FinanceView):
    def post(self, request, tenant_id, bill_id):
        self.member(request, tenant_id, FINANCE)
        bill = payables.submit_supplier_bill(tenant_id, bill_id)
        self.audit(request, tenant_id, "supplier_bill_submitted", "SupplierBill", bill.pk)
        return Response(payables.bill_out(bill, detail=True))


class SupplierBillApproveView(FinanceView):
    def post(self, request, tenant_id, bill_id):
        self.member(request, tenant_id, APPROVERS)
        bill = payables.approve_supplier_bill(tenant_id, bill_id, user=request.user)
        self.audit(request, tenant_id, "supplier_bill_approved", "SupplierBill", bill.pk)
        return Response(payables.bill_out(bill, detail=True))


class SupplierBillPostView(FinanceView):
    def post(self, request, tenant_id, bill_id):
        self.member(request, tenant_id, FINANCE)
        bill, created = payables.post_supplier_bill(tenant_id, bill_id, user=request.user)
        if created:
            self.audit(request, tenant_id, "supplier_bill_posted", "SupplierBill", bill.pk)
        return Response(payables.bill_out(bill, detail=True))


class SupplierBillCancelView(FinanceView):
    def post(self, request, tenant_id, bill_id):
        self.member(request, tenant_id, APPROVERS)
        bill, changed = payables.cancel_supplier_bill(
            tenant_id, bill_id, user=request.user, reason=parse_text(request.data, "reason", 240)
        )
        if changed:
            self.audit(request, tenant_id, "supplier_bill_cancelled", "SupplierBill", bill.pk)
        return Response(payables.bill_out(bill, detail=True))


class SupplierVoucherListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        queryset = SupplierPaymentVoucher.objects.filter(tenant_id=tenant_id).select_related("supplier", "account")
        return Response([payables.voucher_out(item, detail=True) for item in queryset[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        rows = data.get("allocations") or []
        if not isinstance(rows, list):
            raise FinanceError("Allocations must be a list.", 400, "allocations")
        allocations = []
        for row in rows:
            if not isinstance(row, dict):
                raise FinanceError("Each allocation must be an object.", 400, "allocations")
            allocations.append({"bill": parse_uuid(row.get("bill"), "allocations"), "amount": parse_money(row, "amount")})
        voucher, created = payables.create_supplier_voucher(
            tenant_id,
            supplier_id=parse_uuid(data.get("supplier"), "supplier"),
            payment_date=parse_date(data, "payment_date"),
            account_id=parse_uuid(data.get("account"), "account"),
            amount=parse_money(data),
            reference=parse_text(data, "reference", 80, required=False),
            allocations=allocations,
            idempotency_key=self.key(request),
            user=request.user,
        )
        if created:
            self.audit(request, tenant_id, "supplier_voucher_created", "SupplierPaymentVoucher", voucher.pk)
        return Response(payables.voucher_out(voucher, detail=True), status=201 if created else 200)


class SupplierVoucherApproveView(FinanceView):
    def post(self, request, tenant_id, voucher_id):
        self.member(request, tenant_id, APPROVERS)
        voucher = payables.approve_supplier_voucher(tenant_id, voucher_id, user=request.user)
        self.audit(request, tenant_id, "supplier_voucher_approved", "SupplierPaymentVoucher", voucher.pk)
        return Response(payables.voucher_out(voucher, detail=True))


class SupplierVoucherPayView(FinanceView):
    def post(self, request, tenant_id, voucher_id):
        self.member(request, tenant_id, FINANCE)
        voucher, created = payables.pay_supplier_voucher(tenant_id, voucher_id, user=request.user)
        if created:
            self.audit(request, tenant_id, "supplier_voucher_paid", "SupplierPaymentVoucher", voucher.pk)
        return Response(payables.voucher_out(voucher, detail=True))


class SupplierVoucherReverseView(FinanceView):
    def post(self, request, tenant_id, voucher_id):
        self.member(request, tenant_id, APPROVERS)
        voucher, created = payables.reverse_supplier_voucher(
            tenant_id, voucher_id, user=request.user, reason=parse_text(request.data, "reason", 240)
        )
        if created:
            self.audit(request, tenant_id, "supplier_voucher_reversed", "SupplierPaymentVoucher", voucher.pk)
        return Response(payables.voucher_out(voucher, detail=True))


class ExpenseListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        queryset = Expense.objects.filter(tenant_id=tenant_id).select_related("expense_account")
        if request.query_params.get("status"):
            if request.query_params["status"] not in Expense.Status.values:
                raise FinanceError("Choose a valid expense status.", 400, "status")
            queryset = queryset.filter(status=request.query_params["status"])
        return Response([payables.expense_out(expense) for expense in queryset[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        expense = payables.create_expense(
            tenant_id,
            payee=parse_text(data, "payee", 160),
            description=parse_text(data, "description", 240),
            account_id=parse_uuid(data.get("expense_account"), "expense_account"),
            campus_id=parse_uuid(data["campus"], "campus") if data.get("campus") else None,
            transaction_date=parse_date(data, "transaction_date"),
            amount=parse_money(data),
            user=request.user,
        )
        self.audit(request, tenant_id, "expense_created", "Expense", expense.pk, amount=str(expense.amount))
        return Response(payables.expense_out(expense), status=201)


class ExpenseDetailView(FinanceView):
    def get(self, request, tenant_id, expense_id):
        self.member(request, tenant_id)
        expense = self.get_or_404(Expense, tenant_id, expense_id)
        return Response(payables.expense_out(expense, detail=True))


class ExpenseSubmitView(FinanceView):
    def post(self, request, tenant_id, expense_id):
        self.member(request, tenant_id, FINANCE)
        expense = payables.submit_expense(tenant_id, expense_id)
        self.audit(request, tenant_id, "expense_submitted", "Expense", expense.pk)
        return Response(payables.expense_out(expense))


class ExpenseApproveView(FinanceView):
    def post(self, request, tenant_id, expense_id):
        self.member(request, tenant_id, APPROVERS)
        expense = payables.approve_expense(tenant_id, expense_id, user=request.user)
        self.audit(request, tenant_id, "expense_approved", "Expense", expense.pk)
        return Response(payables.expense_out(expense))


class ExpenseRejectView(FinanceView):
    def post(self, request, tenant_id, expense_id):
        self.member(request, tenant_id, APPROVERS)
        expense = payables.reject_expense(
            tenant_id, expense_id, reason=parse_text(request.data, "reason", 240)
        )
        self.audit(request, tenant_id, "expense_rejected", "Expense", expense.pk, reason=expense.rejection_reason)
        return Response(payables.expense_out(expense))


class ExpensePaymentView(FinanceView):
    def post(self, request, tenant_id, expense_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        payment, created = payables.pay_expense(
            tenant_id,
            expense_id,
            account_id=parse_uuid(data.get("account"), "account"),
            amount=parse_money(data),
            payment_date=parse_date(data, "payment_date"),
            reference=parse_text(data, "reference", 80, required=False),
            idempotency_key=self.key(request),
            user=request.user,
        )
        if created:
            self.audit(request, tenant_id, "expense_paid", "Expense", payment.expense_id, amount=str(payment.amount))
        return Response(payables.expense_out(payment.expense, detail=True), status=201 if created else 200)


class ExpenseReverseView(FinanceView):
    def post(self, request, tenant_id, expense_id):
        self.member(request, tenant_id, APPROVERS)
        expense, created = payables.reverse_expense(
            tenant_id, expense_id, user=request.user, reason=parse_text(request.data, "reason", 240)
        )
        if created:
            self.audit(request, tenant_id, "expense_reversed", "Expense", expense.pk)
        return Response(payables.expense_out(expense, detail=True))


class ExpensePaymentReverseView(FinanceView):
    def post(self, request, tenant_id, payment_id):
        self.member(request, tenant_id, APPROVERS)
        payment, created = payables.reverse_expense_payment(
            tenant_id, payment_id, user=request.user, reason=parse_text(request.data, "reason", 240)
        )
        if created:
            self.audit(request, tenant_id, "expense_payment_reversed", "ExpensePayment", payment.pk)
        return Response(payables.expense_out(payment.expense, detail=True))


class AccountsPayableReportView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        as_of = parse_date(request.query_params, "as_of", required=False)
        return Response(payables.accounts_payable_report(tenant_id, as_of=as_of))


class AccountsPayableReconciliationView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        as_of = parse_date(request.query_params, "as_of", required=False)
        return Response(payables.accounts_payable_reconciliation(tenant_id, as_of=as_of))


class SupplierStatementView(FinanceView):
    def get(self, request, tenant_id, supplier_id):
        self.member(request, tenant_id)
        return Response(payables.supplier_statement(tenant_id, supplier_id))


class ExpenseReportView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        start, end = _range(request)
        campus_id = parse_uuid(request.query_params["campus"], "campus") if request.query_params.get("campus") else None
        if campus_id and not Campus.objects.filter(tenant_id=tenant_id, pk=campus_id).exists():
            raise NotFound()
        return Response(payables.expense_report(tenant_id, start=start, end=end, campus_id=campus_id))
