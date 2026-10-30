"""Supplier payables and approved direct-expense accounting workflows."""

from decimal import Decimal
from pathlib import PurePath
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.core.files.uploadedfile import UploadedFile

from core.models import Campus

from .ledger import (
    FinanceError,
    lock_finance,
    next_number,
    post_entry,
    reverse_entry,
    round_money,
    system_account,
)
from .models import (
    Account,
    Expense,
    ExpensePayment,
    JournalEntry,
    JournalLine,
    OpeningBalanceImport,
    PaymentMethod,
    Supplier,
    SupplierBill,
    SupplierBillDocument,
    SupplierBillLine,
    SupplierOpeningBalance,
    SupplierPaymentAllocation,
    SupplierPaymentVoucher,
    SupplierTaxCode,
    ZERO,
)


def _bill_paid(bill):
    return SupplierPaymentAllocation.objects.filter(
        bill=bill, voucher__status=SupplierPaymentVoucher.Status.PAID
    ).aggregate(total=Sum("amount"))["total"] or ZERO


def _bill_open(bill):
    return bill.total - _bill_paid(bill)


def _entry_reversed_after(entry, on_date):
    if entry is None:
        return False
    try:
        return entry.reversed_by.entry_date > on_date
    except JournalEntry.DoesNotExist:
        return False


def _bill_paid_as_of(bill, on_date):
    return bill.payment_allocations.filter(voucher__payment_date__lte=on_date).filter(
        Q(voucher__status=SupplierPaymentVoucher.Status.PAID)
        | Q(
            voucher__status=SupplierPaymentVoucher.Status.REVERSED,
            voucher__reversal_entry__entry_date__gt=on_date,
        )
    ).aggregate(total=Sum("amount"))["total"] or ZERO


def _expense_paid_as_of(expense, on_date):
    paid = ZERO
    for payment in expense.payments.all():
        if payment.payment_date <= on_date and (
            payment.status == "paid" or _entry_reversed_after(payment.journal_entry, on_date)
        ):
            paid += payment.amount
    return paid


def supplier_out(supplier):
    return {
        "id": str(supplier.pk),
        "code": supplier.code,
        "name": supplier.name,
        "contact_person": supplier.contact_person,
        "email": supplier.email,
        "phone": supplier.phone,
        "address": supplier.address,
        "tax_id": supplier.tax_id,
        "payment_terms_days": supplier.payment_terms_days,
        "notes": supplier.notes,
        "is_active": supplier.is_active,
    }


def bill_out(bill, *, detail=False):
    data = {
        "id": str(bill.pk),
        "supplier": str(bill.supplier_id),
        "supplier_name": bill.supplier.name,
        "supplier_reference": bill.supplier_reference,
        "invoice_date": bill.invoice_date.isoformat(),
        "due_date": bill.due_date.isoformat(),
        "status": bill.status,
        "total": str(bill.total),
        "amount_paid": str(_bill_paid(bill)),
        "balance": str(_bill_open(bill)),
        "journal_entry": str(bill.journal_entry_id) if bill.journal_entry_id else None,
        "created_by_id": str(bill.created_by_id) if bill.created_by_id else None,
        "approved_by_id": str(bill.approved_by_id) if bill.approved_by_id else None,
    }
    if detail:
        data["lines"] = [
            {
                "id": str(line.pk),
                "account": str(line.account_id),
                "account_code": line.account.code,
                "account_name": line.account.name,
                "tax_code": str(line.tax_code_id) if line.tax_code_id else None,
                "tax_code_name": line.tax_code.name if line.tax_code_id else "",
                "tax_rate": str(line.tax_rate),
                "tax_amount": str(line.tax_amount),
                "campus": str(line.campus_id) if line.campus_id else None,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit_cost": str(line.unit_cost),
                "amount": str(line.amount),
                "line_total": str(line.amount + line.tax_amount),
            }
            for line in bill.lines.select_related("account", "campus", "tax_code").all()
        ]
    data["documents"] = [
        {
            "id": str(document.pk),
            "name": document.original_name,
            "content_type": document.content_type,
            "file_size": document.file_size,
            "created_at": document.created_at.isoformat(),
        }
        for document in bill.documents.all()
    ]
    return data


def tax_code_out(tax_code):
    return {
        "id": str(tax_code.pk),
        "code": tax_code.code,
        "name": tax_code.name,
        "rate": str(tax_code.rate),
        "account": str(tax_code.account_id),
        "account_name": f"{tax_code.account.code} {tax_code.account.name}",
        "is_inclusive": tax_code.is_inclusive,
        "is_active": tax_code.is_active,
    }


def supplier_opening_balance_out(balance):
    return {
        "id": str(balance.pk),
        "opening_import": str(balance.opening_import_id),
        "import_name": balance.opening_import.name,
        "as_of_date": balance.opening_import.as_of_date.isoformat(),
        "supplier": str(balance.supplier_id),
        "supplier_name": balance.supplier.name,
        "debit": str(balance.debit),
        "credit": str(balance.credit),
    }


def create_supplier_opening_balance(tenant_id, *, import_id, supplier_id, debit, credit, user):
    if debit < 0 or credit < 0 or (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
        raise FinanceError("Enter one positive debit or credit balance.", 400, "balance")
    with transaction.atomic():
        lock_finance(tenant_id)
        imported = OpeningBalanceImport.objects.select_for_update().filter(
            tenant_id=tenant_id,
            pk=import_id,
            status=OpeningBalanceImport.Status.POSTED,
        ).first()
        if imported is None:
            raise FinanceError("Choose a posted opening-balance import.", 404, "opening_import")
        supplier = _validate_supplier(tenant_id, supplier_id)
        if SupplierOpeningBalance.objects.filter(opening_import=imported, supplier=supplier).exists():
            raise FinanceError("This supplier already has an allocation in that opening import.", 409)
        ap_account = Account.objects.filter(tenant_id=tenant_id, system_key="accounts_payable").first()
        if ap_account is None:
            raise FinanceError("The accounts-payable control account is not configured.", 409)
        control_line = imported.lines.filter(account=ap_account).first()
        if control_line is None:
            raise FinanceError("The opening import has no accounts-payable control balance.", 409)
        allocated = imported.supplier_opening_balances.aggregate(
            debit_total=Sum("debit"), credit_total=Sum("credit")
        )
        debit_total = allocated["debit_total"] or ZERO
        credit_total = allocated["credit_total"] or ZERO
        if debit_total + debit > control_line.debit or credit_total + credit > control_line.credit:
            raise FinanceError("Supplier allocations cannot exceed the imported accounts-payable control balance.", 409)
        return SupplierOpeningBalance.objects.create(
            tenant_id=tenant_id,
            opening_import=imported,
            supplier=supplier,
            debit=debit,
            credit=credit,
            created_by=user,
        )


def create_supplier_tax_code(tenant_id, *, code, name, rate, account_id, is_inclusive):
    account = Account.objects.filter(tenant_id=tenant_id, pk=account_id, is_active=True).first()
    if account is None or account.type not in (Account.Type.ASSET, Account.Type.EXPENSE) or account.children.exists():
        raise FinanceError("Choose an active asset or expense posting account for purchase tax.", 400, "account")
    try:
        return SupplierTaxCode.objects.create(
            tenant_id=tenant_id,
            code=code,
            name=name,
            rate=rate,
            account=account,
            is_inclusive=is_inclusive,
        )
    except IntegrityError:
        raise FinanceError("A tax code with this code already exists.", 409, "code")


def upload_supplier_bill_document(tenant_id, bill_id, *, uploaded_file, user):
    if not isinstance(uploaded_file, UploadedFile):
        raise FinanceError("Choose a document to upload.", 400, "file")
    if uploaded_file.size <= 0 or uploaded_file.size > 10 * 1024 * 1024:
        raise FinanceError("Documents must be between 1 byte and 10 MB.", 400, "file")
    filename = PurePath(uploaded_file.name.replace("\\", "/")).name
    extension = PurePath(filename).suffix.lower()
    signatures = {
        ".pdf": (b"%PDF-", "application/pdf"),
        ".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
        ".jpg": (b"\xff\xd8\xff", "image/jpeg"),
        ".jpeg": (b"\xff\xd8\xff", "image/jpeg"),
    }
    allowed = signatures.get(extension)
    if allowed is None:
        raise FinanceError("Only PDF, PNG, and JPEG documents are supported.", 400, "file")
    header = uploaded_file.read(8)
    uploaded_file.seek(0)
    if not header.startswith(allowed[0]):
        raise FinanceError("The document contents do not match its file type.", 400, "file")
    original_name = "".join(character for character in filename if character.isprintable())[:160]
    original_name = original_name or f"supporting-document{extension}"
    with transaction.atomic():
        bill = SupplierBill.objects.select_for_update().filter(tenant_id=tenant_id, pk=bill_id).first()
        if bill is None:
            raise FinanceError("Supplier bill not found.", 404)
        if bill.status in (SupplierBill.Status.CANCELLED, SupplierBill.Status.REVERSED):
            raise FinanceError("Documents cannot be added to a cancelled or reversed bill.", 409)
        if bill.documents.count() >= 10:
            raise FinanceError("A bill can have at most 10 supporting documents.", 400, "file")
        uploaded_file.name = f"{uuid4().hex}{extension}"
        return SupplierBillDocument.objects.create(
            tenant_id=tenant_id,
            bill=bill,
            file=uploaded_file,
            original_name=original_name,
            content_type=allowed[1],
            file_size=uploaded_file.size,
            uploaded_by=user,
        )


def voucher_out(voucher, *, detail=False):
    data = {
        "id": str(voucher.pk),
        "number": voucher.number,
        "supplier": str(voucher.supplier_id),
        "supplier_name": voucher.supplier.name,
        "payment_date": voucher.payment_date.isoformat(),
        "account": str(voucher.account_id),
        "account_name": f"{voucher.account.code} {voucher.account.name}",
        "amount": str(voucher.amount),
        "reference": voucher.reference,
        "status": voucher.status,
        "journal_entry": str(voucher.journal_entry_id) if voucher.journal_entry_id else None,
        "created_by_id": str(voucher.created_by_id) if voucher.created_by_id else None,
        "approved_by_id": str(voucher.approved_by_id) if voucher.approved_by_id else None,
    }
    if detail:
        data["allocations"] = [
            {"bill": str(a.bill_id), "bill_reference": a.bill.supplier_reference, "amount": str(a.amount)}
            for a in voucher.allocations.select_related("bill").all()
        ]
    return data


def expense_out(expense, *, detail=False):
    data = {
        "id": str(expense.pk),
        "payee": expense.payee,
        "description": expense.description,
        "expense_account": str(expense.expense_account_id),
        "expense_account_name": f"{expense.expense_account.code} {expense.expense_account.name}",
        "campus": str(expense.campus_id) if expense.campus_id else None,
        "transaction_date": expense.transaction_date.isoformat(),
        "amount": str(expense.amount),
        "amount_paid": str(expense.amount_paid),
        "balance": str(expense.amount - expense.amount_paid),
        "status": expense.status,
        "rejection_reason": expense.rejection_reason,
        "journal_entry": str(expense.journal_entry_id) if expense.journal_entry_id else None,
        "created_by_id": str(expense.created_by_id) if expense.created_by_id else None,
        "approved_by_id": str(expense.approved_by_id) if expense.approved_by_id else None,
    }
    if detail:
        data["payments"] = [
            {
                "id": str(payment.pk),
                "amount": str(payment.amount),
                "payment_date": payment.payment_date.isoformat(),
                "reference": payment.reference,
                "status": payment.status,
            }
            for payment in expense.payments.all()
        ]
    return data


def _validate_supplier(tenant_id, supplier_id):
    supplier = Supplier.objects.filter(tenant_id=tenant_id, pk=supplier_id, is_active=True).first()
    if supplier is None:
        raise FinanceError("Supplier not found or inactive.", 404, "supplier")
    return supplier


def _validate_asset_account(tenant_id, account_id):
    """Money paid out must leave a cash, bank or mobile-money account, never e.g. receivables."""
    account = Account.objects.filter(tenant_id=tenant_id, pk=account_id, is_active=True).first()
    if account is None or account.type != Account.Type.ASSET or account.children.exists():
        raise FinanceError("Choose an active posting asset account belonging to this school.", 400, "account")
    is_money_account = account.system_key in ("cash", "bank", "mobile_money") or PaymentMethod.objects.filter(
        tenant_id=tenant_id, account=account, is_active=True
    ).exists()
    if not is_money_account:
        raise FinanceError("Pay from a cash, bank or mobile-money account.", 400, "account")
    return account


def _bill_status(bill):
    if bill.status in (SupplierBill.Status.CANCELLED, SupplierBill.Status.REVERSED):
        return
    paid = _bill_paid(bill)
    if paid >= bill.total:
        bill.status = SupplierBill.Status.PAID
    elif paid:
        bill.status = SupplierBill.Status.PARTIALLY_PAID
    else:
        bill.status = SupplierBill.Status.POSTED
    bill.save(update_fields=("status",))


def create_supplier(tenant_id, *, data, user):
    try:
        return Supplier.objects.create(
            tenant_id=tenant_id,
            code=data["code"],
            name=data["name"],
            contact_person=data["contact_person"],
            email=data["email"],
            phone=data["phone"],
            address=data["address"],
            tax_id=data["tax_id"],
            payment_terms_days=data["payment_terms_days"],
            notes=data["notes"],
            created_by=user,
        )
    except IntegrityError:
        raise FinanceError("A supplier with this code already exists.", 409, "code")


def update_supplier(tenant_id, supplier_id, *, fields):
    supplier = Supplier.objects.filter(tenant_id=tenant_id, pk=supplier_id).first()
    if supplier is None:
        raise FinanceError("Supplier not found.", 404)
    for key, value in fields.items():
        setattr(supplier, key, value)
    try:
        supplier.save()
    except IntegrityError:
        raise FinanceError("A supplier with this code already exists.", 409, "code")
    return supplier


def create_supplier_bill(tenant_id, *, supplier_id, supplier_reference, invoice_date, due_date, lines, user):
    supplier = _validate_supplier(tenant_id, supplier_id)
    if due_date < invoice_date:
        raise FinanceError("The due date cannot be before the supplier invoice date.", 400, "due_date")
    if not isinstance(lines, list) or not lines:
        raise FinanceError("Add at least one bill line.", 400, "lines")
    clean = []
    for line in lines:
        account = Account.objects.filter(tenant_id=tenant_id, pk=line["account"], is_active=True).first()
        if account is None or account.type not in (Account.Type.EXPENSE, Account.Type.ASSET) or account.children.exists():
            raise FinanceError("Each bill line must use an active expense or asset account in this school.", 400, "lines")
        tax_code = None
        if line.get("tax_code"):
            tax_code = SupplierTaxCode.objects.filter(
                tenant_id=tenant_id, pk=line["tax_code"], is_active=True
            ).select_related("account").first()
            if tax_code is None:
                raise FinanceError("Choose an active purchase tax code belonging to this school.", 400, "lines")
        campus = None
        if line.get("campus"):
            campus = Campus.objects.filter(tenant_id=tenant_id, pk=line["campus"]).first()
            if campus is None:
                raise FinanceError("Campus not found.", 400, "campus")
        quantity, unit_cost = line["quantity"], line["unit_cost"]
        entered_total = round_money(quantity * unit_cost)
        if entered_total <= 0:
            raise FinanceError("Each bill line total must be greater than zero.", 400, "lines")
        if tax_code is None:
            amount, tax_rate, tax_amount = entered_total, ZERO, ZERO
        elif tax_code.is_inclusive:
            amount = round_money(entered_total / (Decimal("1") + tax_code.rate / Decimal("100")))
            tax_rate = tax_code.rate
            tax_amount = entered_total - amount
        else:
            amount = entered_total
            tax_rate = tax_code.rate
            tax_amount = round_money(amount * tax_rate / Decimal("100"))
        clean.append((account, tax_code, campus, line["description"], quantity, unit_cost, amount, tax_rate, tax_amount))
    total = sum((line[6] + line[8] for line in clean), ZERO)
    with transaction.atomic():
        lock_finance(tenant_id)
        try:
            bill = SupplierBill.objects.create(
                tenant_id=tenant_id,
                supplier=supplier,
                supplier_reference=supplier_reference,
                invoice_date=invoice_date,
                due_date=due_date,
                total=total,
                created_by=user,
            )
        except IntegrityError:
            raise FinanceError("This supplier invoice reference is already recorded.", 409, "supplier_reference")
        SupplierBillLine.objects.bulk_create(
            [
                SupplierBillLine(
                    bill=bill,
                    account=account,
                    tax_code=tax_code,
                    campus=campus,
                    description=description,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    amount=amount,
                    tax_rate=tax_rate,
                    tax_amount=tax_amount,
                )
                for account, tax_code, campus, description, quantity, unit_cost, amount, tax_rate, tax_amount in clean
            ]
        )
    return bill


def submit_supplier_bill(tenant_id, bill_id):
    bill = SupplierBill.objects.filter(tenant_id=tenant_id, pk=bill_id).first()
    if bill is None:
        raise FinanceError("Supplier bill not found.", 404)
    if bill.status != SupplierBill.Status.DRAFT:
        raise FinanceError("Only a draft supplier bill can be submitted.", 409)
    bill.status = SupplierBill.Status.SUBMITTED
    bill.save(update_fields=("status",))
    return bill


def approve_supplier_bill(tenant_id, bill_id, *, user):
    bill = SupplierBill.objects.filter(tenant_id=tenant_id, pk=bill_id).first()
    if bill is None:
        raise FinanceError("Supplier bill not found.", 404)
    if bill.status != SupplierBill.Status.SUBMITTED:
        raise FinanceError("Only a submitted supplier bill can be approved.", 409)
    if bill.created_by_id == user.pk:
        raise FinanceError("The person who prepared a supplier bill cannot approve it.", 409)
    bill.status = SupplierBill.Status.APPROVED
    bill.approved_by = user
    bill.save(update_fields=("status", "approved_by"))
    return bill


def post_supplier_bill(tenant_id, bill_id, *, user):
    with transaction.atomic():
        lock_finance(tenant_id)
        bill = SupplierBill.objects.select_for_update().filter(tenant_id=tenant_id, pk=bill_id).first()
        if bill is None:
            raise FinanceError("Supplier bill not found.", 404)
        if bill.status in (SupplierBill.Status.POSTED, SupplierBill.Status.PARTIALLY_PAID, SupplierBill.Status.PAID):
            return bill, False
        if bill.status != SupplierBill.Status.APPROVED:
            raise FinanceError("Approve the supplier bill before posting.", 409)
        lines = list(bill.lines.select_related("account", "tax_code__account", "campus"))
        payable = system_account(tenant_id, "accounts_payable")
        postings = []
        for line in lines:
            postings.append((line.account, line.amount, ZERO, line.description, line.campus))
            if line.tax_amount:
                postings.append((
                    line.tax_code.account,
                    line.tax_amount,
                    ZERO,
                    f"Purchase tax: {line.description}",
                    line.campus,
                ))
        postings.append((payable, ZERO, bill.total, f"Accounts payable: {bill.supplier.name}"))
        entry, created = post_entry(
            tenant_id,
            entry_date=bill.invoice_date,
            memo=f"Supplier bill {bill.supplier_reference}",
            source_type="supplier_bill",
            source_id=bill.pk,
            idempotency_key=f"supplier-bill:{bill.pk}",
            lines=postings,
            user=user,
        )
        bill.status = SupplierBill.Status.POSTED
        bill.journal_entry = entry
        bill.save(update_fields=("status", "journal_entry"))
    return bill, created


def cancel_supplier_bill(tenant_id, bill_id, *, user, reason):
    with transaction.atomic():
        lock_finance(tenant_id)
        bill = SupplierBill.objects.select_for_update().filter(tenant_id=tenant_id, pk=bill_id).first()
        if bill is None:
            raise FinanceError("Supplier bill not found.", 404)
        if bill.status in (SupplierBill.Status.CANCELLED, SupplierBill.Status.REVERSED):
            return bill, False
        if bill.status in (SupplierBill.Status.DRAFT, SupplierBill.Status.SUBMITTED, SupplierBill.Status.APPROVED):
            bill.status = SupplierBill.Status.CANCELLED
            bill.save(update_fields=("status",))
            return bill, True
        if _bill_paid(bill) > ZERO:
            raise FinanceError("Reverse supplier payments before reversing this bill.", 409)
        if not bill.journal_entry_id:
            raise FinanceError("Posted bill is missing its journal entry.", 409)
        reverse_entry(bill.journal_entry, user=user, memo=f"Reverse bill {bill.supplier_reference}: {reason}")
        bill.status = SupplierBill.Status.REVERSED
        bill.save(update_fields=("status",))
    return bill, True


def create_supplier_voucher(tenant_id, *, supplier_id, payment_date, account_id, amount, reference, allocations, idempotency_key, user):
    supplier = _validate_supplier(tenant_id, supplier_id)
    account = _validate_asset_account(tenant_id, account_id)
    if not idempotency_key:
        raise FinanceError("An idempotency key is required.", 400, "idempotency_key")
    if not isinstance(allocations, list):
        raise FinanceError("Allocations must be a list.", 400, "allocations")
    with transaction.atomic():
        lock_finance(tenant_id)
        prior = SupplierPaymentVoucher.objects.filter(tenant_id=tenant_id, idempotency_key=idempotency_key).first()
        if prior:
            old_allocations = {
                row.bill_id: row.amount for row in prior.allocations.all()
            }
            new_allocations = {row["bill"]: row["amount"] for row in allocations}
            if (
                prior.supplier_id != supplier.pk
                or prior.amount != amount
                or prior.account_id != account.pk
                or prior.payment_date != payment_date
                or prior.reference != reference
                or old_allocations != new_allocations
            ):
                raise FinanceError("That idempotency key was already used for a different voucher.", 409)
            return prior, False
        voucher = SupplierPaymentVoucher.objects.create(
            tenant_id=tenant_id,
            supplier=supplier,
            payment_date=payment_date,
            account=account,
            amount=amount,
            reference=reference,
            idempotency_key=idempotency_key,
            created_by=user,
        )
        seen = set()
        total = ZERO
        for item in allocations:
            if item["bill"] in seen:
                raise FinanceError("List each supplier bill only once.", 400, "allocations")
            seen.add(item["bill"])
            bill = SupplierBill.objects.select_for_update().filter(
                tenant_id=tenant_id,
                supplier=supplier,
                pk=item["bill"],
                status__in=(SupplierBill.Status.POSTED, SupplierBill.Status.PARTIALLY_PAID),
            ).first()
            if bill is None:
                raise FinanceError("An allocated bill is not payable or belongs to another supplier.", 400, "allocations")
            if item["amount"] > _bill_open(bill):
                raise FinanceError(f"Bill {bill.supplier_reference} has an open balance of {_bill_open(bill)}.", 400, "allocations")
            SupplierPaymentAllocation.objects.create(voucher=voucher, bill=bill, amount=item["amount"])
            total += item["amount"]
        if total > amount:
            raise FinanceError("Allocated payments cannot exceed the voucher amount.", 400, "allocations")
    return voucher, True


def approve_supplier_voucher(tenant_id, voucher_id, *, user):
    with transaction.atomic():
        settings = lock_finance(tenant_id)
        voucher = SupplierPaymentVoucher.objects.select_for_update().filter(tenant_id=tenant_id, pk=voucher_id).first()
        if voucher is None:
            raise FinanceError("Payment voucher not found.", 404)
        if voucher.status != SupplierPaymentVoucher.Status.DRAFT:
            raise FinanceError("Only a draft payment voucher can be approved.", 409)
        if voucher.created_by_id == user.pk:
            raise FinanceError("The person who prepared a payment voucher cannot approve it.", 409)
        voucher.status = SupplierPaymentVoucher.Status.APPROVED
        voucher.approved_by = user
        voucher.number = next_number(settings, "supplier_voucher")
        voucher.save(update_fields=("status", "approved_by", "number"))
    return voucher


def pay_supplier_voucher(tenant_id, voucher_id, *, user):
    with transaction.atomic():
        settings = lock_finance(tenant_id)
        voucher = SupplierPaymentVoucher.objects.select_for_update(of=("self",)).select_related("account", "supplier").filter(
            tenant_id=tenant_id, pk=voucher_id
        ).first()
        if voucher is None:
            raise FinanceError("Payment voucher not found.", 404)
        if voucher.status == SupplierPaymentVoucher.Status.PAID:
            return voucher, False
        if voucher.status != SupplierPaymentVoucher.Status.APPROVED:
            raise FinanceError("Approve the payment voucher before marking it paid.", 409)
        allocations = list(voucher.allocations.select_related("bill"))
        allocated = sum((item.amount for item in allocations), ZERO)
        advance = voucher.amount - allocated
        postings = [(system_account(tenant_id, "accounts_payable"), allocated, ZERO, "Supplier bills paid")]
        if advance:
            postings.append((system_account(tenant_id, "supplier_advances"), advance, ZERO, "Supplier advance"))
        postings.append((voucher.account, ZERO, voucher.amount, f"Payment voucher {voucher.number}"))
        entry, created = post_entry(
            tenant_id,
            entry_date=voucher.payment_date,
            memo=f"Supplier payment {voucher.number}",
            source_type="supplier_payment",
            source_id=voucher.pk,
            idempotency_key=f"supplier-payment:{voucher.pk}",
            lines=postings,
            user=user,
        )
        voucher.status = SupplierPaymentVoucher.Status.PAID
        voucher.journal_entry = entry
        voucher.save(update_fields=("status", "journal_entry"))
        for allocation in allocations:
            _bill_status(allocation.bill)
    return voucher, created


def reverse_supplier_voucher(tenant_id, voucher_id, *, user, reason):
    with transaction.atomic():
        lock_finance(tenant_id)
        voucher = SupplierPaymentVoucher.objects.select_for_update().filter(tenant_id=tenant_id, pk=voucher_id).first()
        if voucher is None:
            raise FinanceError("Payment voucher not found.", 404)
        if voucher.status == SupplierPaymentVoucher.Status.REVERSED:
            return voucher, False
        if voucher.status != SupplierPaymentVoucher.Status.PAID or not voucher.journal_entry_id:
            raise FinanceError("Only a paid voucher can be reversed.", 409)
        reversal, created = reverse_entry(
            voucher.journal_entry, user=user, memo=f"Reverse voucher {voucher.number}: {reason}"
        )
        voucher.status = SupplierPaymentVoucher.Status.REVERSED
        voucher.reversal_entry = reversal
        voucher.save(update_fields=("status", "reversal_entry"))
        for allocation in voucher.allocations.select_related("bill"):
            _bill_status(allocation.bill)
    return voucher, created


def create_expense(tenant_id, *, payee, description, account_id, campus_id, transaction_date, amount, user):
    account = Account.objects.filter(tenant_id=tenant_id, pk=account_id, is_active=True).first()
    if account is None or account.type != Account.Type.EXPENSE or account.children.exists():
        raise FinanceError("Choose an active expense posting account belonging to this school.", 400, "expense_account")
    campus = None
    if campus_id:
        campus = Campus.objects.filter(tenant_id=tenant_id, pk=campus_id).first()
        if campus is None:
            raise FinanceError("Campus not found.", 400, "campus")
    return Expense.objects.create(
        tenant_id=tenant_id,
        payee=payee,
        description=description,
        expense_account=account,
        campus=campus,
        transaction_date=transaction_date,
        amount=amount,
        created_by=user,
    )


def submit_expense(tenant_id, expense_id):
    expense = Expense.objects.filter(tenant_id=tenant_id, pk=expense_id).first()
    if expense is None:
        raise FinanceError("Expense not found.", 404)
    if expense.status != Expense.Status.DRAFT:
        raise FinanceError("Only a draft expense can be submitted.", 409)
    expense.status = Expense.Status.SUBMITTED
    expense.save(update_fields=("status",))
    return expense


def approve_expense(tenant_id, expense_id, *, user):
    with transaction.atomic():
        lock_finance(tenant_id)
        expense = Expense.objects.select_for_update(of=("self",)).select_related("expense_account", "campus").filter(
            tenant_id=tenant_id, pk=expense_id
        ).first()
        if expense is None:
            raise FinanceError("Expense not found.", 404)
        if expense.status != Expense.Status.SUBMITTED:
            raise FinanceError("Only a submitted expense can be approved.", 409)
        if expense.created_by_id == user.pk:
            raise FinanceError("The person who prepared an expense cannot approve it.", 409)
        entry, _ = post_entry(
            tenant_id,
            entry_date=expense.transaction_date,
            memo=f"Expense: {expense.description}",
            source_type="expense",
            source_id=expense.pk,
            idempotency_key=f"expense:{expense.pk}",
            lines=[
                (expense.expense_account, expense.amount, ZERO, expense.description, expense.campus),
                (system_account(tenant_id, "accounts_payable"), ZERO, expense.amount, f"Payable to {expense.payee}"),
            ],
            user=user,
        )
        expense.status = Expense.Status.APPROVED
        expense.approved_by = user
        expense.journal_entry = entry
        expense.save(update_fields=("status", "approved_by", "journal_entry"))
    return expense


def reject_expense(tenant_id, expense_id, *, reason):
    expense = Expense.objects.filter(tenant_id=tenant_id, pk=expense_id).first()
    if expense is None:
        raise FinanceError("Expense not found.", 404)
    if expense.status != Expense.Status.SUBMITTED:
        raise FinanceError("Only a submitted expense can be rejected.", 409)
    expense.status = Expense.Status.REJECTED
    expense.rejection_reason = reason
    expense.save(update_fields=("status", "rejection_reason"))
    return expense


def pay_expense(tenant_id, expense_id, *, account_id, amount, payment_date, reference, idempotency_key, user):
    if not idempotency_key:
        raise FinanceError("An idempotency key is required.", 400, "idempotency_key")
    account = _validate_asset_account(tenant_id, account_id)
    with transaction.atomic():
        lock_finance(tenant_id)
        expense = Expense.objects.select_for_update().filter(tenant_id=tenant_id, pk=expense_id).first()
        if expense is None:
            raise FinanceError("Expense not found.", 404)
        prior = ExpensePayment.objects.filter(tenant_id=tenant_id, idempotency_key=idempotency_key).first()
        if prior:
            if (
                prior.expense_id != expense.pk
                or prior.amount != amount
                or prior.account_id != account.pk
                or prior.payment_date != payment_date
                or prior.reference != reference
            ):
                raise FinanceError("That idempotency key was used for a different expense payment.", 409)
            return prior, False
        if expense.status not in (Expense.Status.APPROVED, Expense.Status.PARTIALLY_PAID):
            raise FinanceError("Only an approved unpaid expense can be paid.", 409)
        if amount <= 0 or amount > expense.amount - expense.amount_paid:
            raise FinanceError("Payment exceeds the expense balance.", 400, "amount")
        payable = system_account(tenant_id, "accounts_payable")
        entry, _ = post_entry(
            tenant_id,
            entry_date=payment_date,
            memo=f"Payment for expense {expense.description}",
            source_type="expense_payment",
            source_id=expense.pk,
            idempotency_key=f"expense-payment:{idempotency_key}",
            lines=[
                (payable, amount, ZERO, f"Clear payable to {expense.payee}"),
                (account, ZERO, amount, f"Expense payment {reference}".strip()),
            ],
            user=user,
        )
        payment = ExpensePayment.objects.create(
            tenant_id=tenant_id,
            expense=expense,
            account=account,
            amount=amount,
            payment_date=payment_date,
            reference=reference,
            idempotency_key=idempotency_key,
            journal_entry=entry,
            created_by=user,
        )
        expense.amount_paid += amount
        expense.status = Expense.Status.PAID if expense.amount_paid == expense.amount else Expense.Status.PARTIALLY_PAID
        expense.save(update_fields=("amount_paid", "status"))
    return payment, True


def reverse_expense_payment(tenant_id, payment_id, *, user, reason):
    with transaction.atomic():
        lock_finance(tenant_id)
        payment = ExpensePayment.objects.select_for_update(of=("self",)).select_related("expense").filter(
            tenant_id=tenant_id, pk=payment_id
        ).first()
        if payment is None:
            raise FinanceError("Expense payment not found.", 404)
        if payment.status == "reversed":
            return payment, False
        reversal, created = reverse_entry(
            payment.journal_entry, user=user, memo=f"Reverse expense payment: {reason}"
        )
        expense = Expense.objects.select_for_update().get(pk=payment.expense_id, tenant_id=tenant_id)
        expense.amount_paid -= payment.amount
        expense.status = Expense.Status.PARTIALLY_PAID if expense.amount_paid else Expense.Status.APPROVED
        expense.save(update_fields=("amount_paid", "status"))
        payment.status = "reversed"
        payment.reversal_entry = reversal
        payment.save(update_fields=("status", "reversal_entry"))
    return payment, created


def reverse_expense(tenant_id, expense_id, *, user, reason):
    with transaction.atomic():
        lock_finance(tenant_id)
        expense = Expense.objects.select_for_update().filter(tenant_id=tenant_id, pk=expense_id).first()
        if expense is None:
            raise FinanceError("Expense not found.", 404)
        if expense.status == Expense.Status.REVERSED:
            return expense, False
        if expense.status not in (Expense.Status.APPROVED, Expense.Status.PARTIALLY_PAID, Expense.Status.PAID):
            raise FinanceError("Only an approved expense can be reversed.", 409)
        if expense.amount_paid:
            raise FinanceError("Reverse all payments before reversing the expense.", 409)
        if not expense.journal_entry_id:
            raise FinanceError("Expense is missing its journal entry.", 409)
        reverse_entry(expense.journal_entry, user=user, memo=f"Reverse expense: {reason}")
        expense.status = Expense.Status.REVERSED
        expense.save(update_fields=("status",))
    return expense, True


def accounts_payable_report(tenant_id, *, as_of=None):
    today = as_of or timezone.localdate()
    bills = SupplierBill.objects.filter(
        tenant_id=tenant_id,
        invoice_date__lte=today,
        status__in=(
            SupplierBill.Status.POSTED,
            SupplierBill.Status.PARTIALLY_PAID,
            SupplierBill.Status.PAID,
            SupplierBill.Status.REVERSED,
        ),
    ).select_related("supplier", "journal_entry__reversed_by")

    suppliers = {}
    for bill in bills:
        if bill.status == SupplierBill.Status.REVERSED and not _entry_reversed_after(bill.journal_entry, today):
            continue
        balance = bill.total - _bill_paid_as_of(bill, today)
        if balance <= ZERO:
            continue
        row = suppliers.setdefault(
            bill.supplier_id,
            {
                "supplier": str(bill.supplier_id),
                "supplier_name": bill.supplier.name,
                "bills": 0,
                "balance": ZERO,
                "current": ZERO,
                "days_1_30": ZERO,
                "days_31_60": ZERO,
                "days_61_90": ZERO,
                "days_over_90": ZERO,
                "oldest_due_date": bill.due_date,
            },
        )
        row["bills"] += 1
        row["balance"] += balance
        row["oldest_due_date"] = min(row["oldest_due_date"], bill.due_date)
        age = (today - bill.due_date).days
        bucket = "current" if age <= 0 else "days_1_30" if age <= 30 else "days_31_60" if age <= 60 else "days_61_90" if age <= 90 else "days_over_90"
        row[bucket] += balance
    outstanding_expenses = Expense.objects.filter(
        tenant_id=tenant_id,
        transaction_date__lte=today,
        status__in=(
            Expense.Status.APPROVED,
            Expense.Status.PARTIALLY_PAID,
            Expense.Status.PAID,
            Expense.Status.REVERSED,
        ),
    ).select_related("campus", "journal_entry__reversed_by").prefetch_related("payments__journal_entry__reversed_by")
    for expense in outstanding_expenses:
        if expense.status == Expense.Status.REVERSED and not _entry_reversed_after(expense.journal_entry, today):
            continue
        paid = _expense_paid_as_of(expense, today)
        balance = expense.amount - paid
        if balance <= ZERO:
            continue
        row_id = f"expense:{expense.pk}"
        row = suppliers.setdefault(
            row_id,
            {
                "supplier": None,
                "supplier_name": expense.payee,
                "bills": 0,
                "balance": ZERO,
                "current": ZERO,
                "days_1_30": ZERO,
                "days_31_60": ZERO,
                "days_61_90": ZERO,
                "days_over_90": ZERO,
                "oldest_due_date": expense.transaction_date,
                "kind": "expense",
            },
        )
        row["bills"] += 1
        row["balance"] += balance
        row["oldest_due_date"] = min(row["oldest_due_date"], expense.transaction_date)
        age = (today - expense.transaction_date).days
        bucket = "current" if age <= 0 else "days_1_30" if age <= 30 else "days_31_60" if age <= 60 else "days_61_90" if age <= 90 else "days_over_90"
        row[bucket] += balance
    opening_balances = SupplierOpeningBalance.objects.filter(
        tenant_id=tenant_id,
        opening_import__as_of_date__lte=today,
        opening_import__status__in=(OpeningBalanceImport.Status.POSTED, OpeningBalanceImport.Status.REVERSED),
    ).select_related("supplier", "opening_import__journal_entry__reversed_by")
    for allocation in opening_balances:
        imported = allocation.opening_import
        if imported.status == OpeningBalanceImport.Status.REVERSED and not _entry_reversed_after(
            imported.journal_entry, today
        ):
            continue
        balance = allocation.credit - allocation.debit
        if balance == ZERO:
            continue
        supplier = allocation.supplier
        row = suppliers.setdefault(
            supplier.pk,
            {
                "supplier": str(supplier.pk),
                "supplier_name": supplier.name,
                "bills": 0,
                "balance": ZERO,
                "current": ZERO,
                "days_1_30": ZERO,
                "days_31_60": ZERO,
                "days_61_90": ZERO,
                "days_over_90": ZERO,
                "oldest_due_date": imported.as_of_date,
            },
        )
        row["bills"] += 1
        row["balance"] += balance
        row["oldest_due_date"] = min(row["oldest_due_date"], imported.as_of_date)
        age = (today - imported.as_of_date).days
        bucket = "current" if age <= 0 else "days_1_30" if age <= 30 else "days_31_60" if age <= 60 else "days_61_90" if age <= 90 else "days_over_90"
        row[bucket] += balance
    for row in suppliers.values():
        row.setdefault("kind", "supplier")
        row["oldest_due_date"] = row["oldest_due_date"].isoformat()
        for key in ("balance", "current", "days_1_30", "days_31_60", "days_61_90", "days_over_90"):
            row[key] = str(row[key])
    return sorted(suppliers.values(), key=lambda row: Decimal(row["balance"]), reverse=True)


def accounts_payable_reconciliation(tenant_id, *, as_of=None):
    today = as_of or timezone.localdate()
    control_account = Account.objects.filter(tenant_id=tenant_id, system_key="accounts_payable").first()
    if control_account is None:
        raise FinanceError("The accounts-payable control account is not configured.", 409)
    control_totals = JournalLine.objects.filter(
        account=control_account,
        entry__entry_date__lte=today,
    ).aggregate(debits=Sum("debit"), credits=Sum("credit"))
    control_balance = (control_totals["credits"] or ZERO) - (control_totals["debits"] or ZERO)

    subledger_balance = ZERO
    bills = SupplierBill.objects.filter(
        tenant_id=tenant_id,
        invoice_date__lte=today,
        status__in=(
            SupplierBill.Status.POSTED,
            SupplierBill.Status.PARTIALLY_PAID,
            SupplierBill.Status.PAID,
            SupplierBill.Status.REVERSED,
        ),
    ).select_related("journal_entry__reversed_by")
    for bill in bills:
        if bill.status != SupplierBill.Status.REVERSED or _entry_reversed_after(bill.journal_entry, today):
            subledger_balance += bill.total - _bill_paid_as_of(bill, today)

    expenses = Expense.objects.filter(
        tenant_id=tenant_id,
        transaction_date__lte=today,
        status__in=(
            Expense.Status.APPROVED,
            Expense.Status.PARTIALLY_PAID,
            Expense.Status.PAID,
            Expense.Status.REVERSED,
        ),
    ).select_related("journal_entry__reversed_by").prefetch_related("payments__journal_entry__reversed_by")
    for expense in expenses:
        if expense.status != Expense.Status.REVERSED or _entry_reversed_after(expense.journal_entry, today):
            subledger_balance += expense.amount - _expense_paid_as_of(expense, today)

    allocations = SupplierOpeningBalance.objects.filter(
        tenant_id=tenant_id,
        opening_import__as_of_date__lte=today,
        opening_import__status__in=(OpeningBalanceImport.Status.POSTED, OpeningBalanceImport.Status.REVERSED),
    ).select_related("opening_import__journal_entry__reversed_by")
    for allocation in allocations:
        imported = allocation.opening_import
        if imported.status != OpeningBalanceImport.Status.REVERSED or _entry_reversed_after(imported.journal_entry, today):
            subledger_balance += allocation.credit - allocation.debit

    difference = control_balance - subledger_balance
    return {
        "as_of": today.isoformat(),
        "control_account": f"{control_account.code} {control_account.name}",
        "control_balance": str(control_balance),
        "subledger_balance": str(subledger_balance),
        "difference": str(difference),
        "reconciled": difference == ZERO,
    }


def supplier_statement(tenant_id, supplier_id):
    supplier = Supplier.objects.filter(tenant_id=tenant_id, pk=supplier_id).first()
    if supplier is None:
        raise FinanceError("Supplier not found.", 404)
    bills = SupplierBill.objects.filter(
        tenant_id=tenant_id, supplier=supplier,
        status__in=(SupplierBill.Status.POSTED, SupplierBill.Status.PARTIALLY_PAID, SupplierBill.Status.PAID, SupplierBill.Status.REVERSED),
    )
    entries = []
    opening_balances = SupplierOpeningBalance.objects.filter(
        tenant_id=tenant_id, supplier=supplier,
        opening_import__status__in=(OpeningBalanceImport.Status.POSTED, OpeningBalanceImport.Status.REVERSED),
    ).select_related("opening_import__journal_entry__reversed_by")
    for allocation in opening_balances:
        imported = allocation.opening_import
        entries.append((
            imported.as_of_date,
            "opening_balance",
            imported.name,
            allocation.credit,
            allocation.debit,
        ))
        if imported.status == OpeningBalanceImport.Status.REVERSED:
            try:
                reversed_entry = imported.journal_entry.reversed_by
            except JournalEntry.DoesNotExist:
                reversed_entry = None
            if reversed_entry:
                entries.append((
                    reversed_entry.entry_date,
                    "opening_balance_reversal",
                    imported.name,
                    allocation.debit,
                    allocation.credit,
                ))
    for bill in bills:
        entries.append((bill.invoice_date, "bill", bill.supplier_reference, bill.total, ZERO))
        if bill.status == SupplierBill.Status.REVERSED and bill.journal_entry_id:
            reversal = getattr(bill.journal_entry, "reversed_by", None)
            if reversal:
                entries.append((reversal.entry_date, "bill_reversal", bill.supplier_reference, ZERO, bill.total))
        allocations = bill.payment_allocations.filter(
            voucher__status__in=(SupplierPaymentVoucher.Status.PAID, SupplierPaymentVoucher.Status.REVERSED)
        ).select_related("voucher__reversal_entry")
        for allocation in allocations:
            entries.append((allocation.voucher.payment_date, "payment", allocation.voucher.number, ZERO, allocation.amount))
            if allocation.voucher.status == SupplierPaymentVoucher.Status.REVERSED and allocation.voucher.reversal_entry_id:
                entries.append((
                    allocation.voucher.reversal_entry.entry_date,
                    "payment_reversal",
                    allocation.voucher.number,
                    allocation.amount,
                    ZERO,
                ))
    vouchers = SupplierPaymentVoucher.objects.filter(
        tenant_id=tenant_id,
        supplier=supplier,
        status__in=(SupplierPaymentVoucher.Status.PAID, SupplierPaymentVoucher.Status.REVERSED),
    ).select_related("reversal_entry").prefetch_related("allocations")
    for voucher in vouchers:
        advance = voucher.amount - sum((allocation.amount for allocation in voucher.allocations.all()), ZERO)
        if advance <= ZERO:
            continue
        entries.append((voucher.payment_date, "supplier_advance", voucher.number, ZERO, advance))
        if voucher.status == SupplierPaymentVoucher.Status.REVERSED and voucher.reversal_entry_id:
            entries.append((voucher.reversal_entry.entry_date, "advance_reversal", voucher.number, advance, ZERO))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    balance = ZERO
    result = []
    for date, kind, reference, debit, credit in entries:
        balance += debit - credit
        result.append(
            {
                "date": date.isoformat(),
                "type": kind,
                "reference": reference,
                "debit": str(debit),
                "credit": str(credit),
                "balance": str(balance),
            }
        )
    return {"supplier": supplier_out(supplier), "entries": result, "closing_balance": str(balance)}


def expense_report(tenant_id, *, start=None, end=None, campus_id=None):
    queryset = Expense.objects.filter(tenant_id=tenant_id).select_related("expense_account", "campus")
    if start:
        queryset = queryset.filter(transaction_date__gte=start)
    if end:
        queryset = queryset.filter(transaction_date__lte=end)
    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    return [
        {
            "id": str(expense.pk),
            "date": expense.transaction_date.isoformat(),
            "payee": expense.payee,
            "description": expense.description,
            "category": expense.expense_account.name,
            "campus": expense.campus.name if expense.campus_id else "No campus",
            "amount": str(expense.amount),
            "status": expense.status,
        }
        for expense in queryset.order_by("-transaction_date", "-created_at")[:1000]
    ]
