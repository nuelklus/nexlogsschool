"""Double-entry ledger primitives: chart of accounts, locking, numbering and posting."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    ZERO,
    Account,
    AccountingPeriod,
    FeeCategory,
    FinanceSettings,
    JournalEntry,
    JournalLine,
    PaymentMethod,
)

CENT = Decimal("0.01")


class FinanceError(Exception):
    def __init__(self, message, status=400, field=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field


def money(value, field="amount"):
    """Parse and validate a money value. Floats are rejected so no binary rounding sneaks in."""
    if isinstance(value, float):
        raise FinanceError("Send money amounts as strings, e.g. \"150.00\".", field=field)
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise FinanceError("Enter a valid amount.", field=field)
    if not amount.is_finite():
        raise FinanceError("Enter a valid amount.", field=field)
    if amount != amount.quantize(CENT):
        raise FinanceError("Amounts can have at most 2 decimal places.", field=field)
    return amount.quantize(CENT)


def round_money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


DEFAULT_ACCOUNTS = (
    ("1100", "Cash on hand", "asset", "cash"),
    ("1110", "Bank account", "asset", "bank"),
    ("1120", "Mobile money wallet", "asset", "mobile_money"),
    ("1200", "Student fees receivable", "asset", "receivable"),
    ("2100", "Student advances (unearned fees)", "liability", "student_advances"),
    ("2110", "Supplier advances", "liability", "supplier_advances"),
    ("2120", "Accounts payable", "liability", "accounts_payable"),
    ("2130", "Tax and statutory liabilities", "liability", "tax_payable"),
    ("3000", "Opening balance clearing", "equity", "opening_balance_clearing"),
    ("4000", "Tuition fees", "income", ""),
    ("4010", "Examination fees", "income", ""),
    ("4020", "Development levy", "income", ""),
    ("4030", "Boarding fees", "income", ""),
    ("4040", "Transport fees", "income", ""),
    ("4050", "Donations and grants", "income", ""),
    ("4900", "Fee adjustments and credit notes", "income", "fee_adjustments"),
    ("5100", "Scholarships and discounts", "expense", "discounts"),
    ("5200", "Bad debts", "expense", "bad_debt"),
    ("6100", "Salaries and staff expenses", "expense", ""),
    ("6200", "Utilities", "expense", ""),
    ("6300", "Food and catering", "expense", ""),
    ("6400", "Transport", "expense", ""),
    ("6500", "Stationery and learning materials", "expense", ""),
    ("6600", "Repairs and maintenance", "expense", ""),
    ("1500", "Property and equipment", "asset", ""),
    ("1590", "Accumulated depreciation", "asset", ""),
)
DEFAULT_CATEGORIES = (
    ("tuition", "Tuition", "4000"),
    ("examination", "Examination", "4010"),
    ("development", "Development levy", "4020"),
    ("boarding", "Boarding", "4030"),
    ("transport", "Transport", "4040"),
)
DEFAULT_METHODS = (
    ("Cash", "cash", "1100"),
    ("Bank transfer", "bank_transfer", "1110"),
    ("Cheque", "cheque", "1110"),
    ("Mobile money", "mobile_money", "1120"),
)


def lock_finance(tenant_id):
    """Lock this school's finance settings row. Call first in every finance write.

    Taking one lock per school, always first, serialises writes for that school
    (so numbers cannot repeat) and rules out deadlocks between operations.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("lock_finance must run inside transaction.atomic().")
    FinanceSettings.objects.get_or_create(tenant_id=tenant_id)
    settings = FinanceSettings.objects.select_for_update().get(tenant_id=tenant_id)
    if not Account.objects.filter(tenant_id=tenant_id).exists():
        _seed_defaults(tenant_id)
    else:
        _seed_missing_system_accounts(tenant_id)
    return settings


def ensure_ready(tenant_id):
    """Make sure the settings row and default chart exist (used by read endpoints too)."""
    if (
        FinanceSettings.objects.filter(tenant_id=tenant_id).exists()
        and Account.objects.filter(tenant_id=tenant_id).exists()
        and Account.objects.filter(tenant_id=tenant_id, system_key="accounts_payable").exists()
    ):
        return
    with transaction.atomic():
        lock_finance(tenant_id)


def _seed_defaults(tenant_id):
    accounts = {}
    for code, name, type_, key in DEFAULT_ACCOUNTS:
        normal = "debit" if type_ in (Account.Type.ASSET, Account.Type.EXPENSE) else "credit"
        account = Account.objects.create(
            tenant_id=tenant_id, code=code, name=name, type=type_, system_key=key,
            normal_balance=normal, reporting_class=type_,
        )
        accounts[code] = account
    for code, name, account_code in DEFAULT_CATEGORIES:
        FeeCategory.objects.create(tenant_id=tenant_id, code=code, name=name, income_account=accounts[account_code])
    for name, kind, account_code in DEFAULT_METHODS:
        PaymentMethod.objects.create(tenant_id=tenant_id, name=name, kind=kind, account=accounts[account_code])


def _seed_missing_system_accounts(tenant_id):
    present = set(Account.objects.filter(tenant_id=tenant_id).exclude(system_key="").values_list("system_key", flat=True))
    for code, name, type_, key in DEFAULT_ACCOUNTS:
        if not key or key in present:
            continue
        used_codes = set(Account.objects.filter(tenant_id=tenant_id).values_list("code", flat=True))
        account_code = code if code not in used_codes else f"SYS-{key.upper()}"[:20]
        suffix = 1
        while account_code in used_codes:
            account_code = f"SYS-{key.upper()[:14]}-{suffix}"
            suffix += 1
        normal = "debit" if type_ in (Account.Type.ASSET, Account.Type.EXPENSE) else "credit"
        Account.objects.create(
            tenant_id=tenant_id,
            code=account_code,
            name=name,
            type=type_,
            system_key=key,
            normal_balance=normal,
            reporting_class=type_,
        )
        present.add(key)


def system_account(tenant_id, key):
    account = Account.objects.filter(tenant_id=tenant_id, system_key=key, is_active=True).first()
    if account is None:
        raise FinanceError(f"The '{key}' ledger account is missing or inactive. Ask an administrator to restore it.", 409)
    return account


def next_number(settings, kind):
    """Allocate the next document number. The settings row must already be locked."""
    prefix_field, counter_field = {
        "invoice": ("invoice_prefix", "next_invoice_number"),
        "receipt": ("receipt_prefix", "next_receipt_number"),
        "credit_note": ("credit_note_prefix", "next_credit_note_number"),
        "journal": ("journal_prefix", "next_journal_number"),
        "supplier_voucher": ("supplier_voucher_prefix", "next_supplier_voucher_number"),
    }[kind]
    value = getattr(settings, counter_field)
    setattr(settings, counter_field, value + 1)
    settings.save(update_fields=(counter_field,))
    return f"{getattr(settings, prefix_field)}-{value:06d}"


def assert_period_open(tenant_id, on_date):
    if AccountingPeriod.objects.filter(
        tenant_id=tenant_id, status=AccountingPeriod.Status.CLOSED, start_date__lte=on_date, end_date__gte=on_date
    ).exists():
        raise FinanceError("That accounting period is closed. Entries cannot be posted into it.", 409)


def post_entry(tenant_id, *, entry_date, memo, source_type, source_id, idempotency_key, lines, user=None, reverses=None):
    """Post one balanced journal entry; returns (entry, created).

    ``lines`` is a list of (account, debit, credit, description). Retrying with the
    same ``idempotency_key`` returns the existing entry instead of posting again.
    """
    settings = lock_finance(tenant_id)
    existing = JournalEntry.objects.filter(tenant_id=tenant_id, idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing, False

    cleaned = []
    for line in lines:
        if len(line) == 4:
            account, debit, credit, description = line
            campus = None
        elif len(line) == 5:
            account, debit, credit, description, campus = line
        else:
            raise FinanceError("A journal line has an invalid shape.", 500)
        debit, credit = Decimal(debit), Decimal(credit)
        if debit < 0 or credit < 0 or (debit > 0 and credit > 0):
            raise FinanceError("Each journal line must be a positive debit or credit.", 500)
        if debit == 0 and credit == 0:
            continue
        if account.tenant_id != tenant_id:
            raise FinanceError("Ledger account belongs to another school.", 500)
        if reverses is None and (not account.is_active or account.children.exists()):
            raise FinanceError("Journal entries require active posting accounts, not account headings.", 400)
        if campus is not None and campus.tenant_id != tenant_id:
            raise FinanceError("Campus belongs to another school.", 400)
        cleaned.append((account, debit, credit, description, campus))
    total_debit = sum((line[1] for line in cleaned), ZERO)
    total_credit = sum((line[2] for line in cleaned), ZERO)
    if len(cleaned) < 2 or total_debit != total_credit or total_debit <= 0:
        raise FinanceError("Journal entry is not balanced.", 500)
    assert_period_open(tenant_id, entry_date)

    try:
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                tenant_id=tenant_id,
                number=next_number(settings, "journal"),
                entry_date=entry_date,
                memo=memo[:240],
                source_type=source_type,
                source_id=str(source_id),
                idempotency_key=idempotency_key,
                reverses=reverses,
                created_by=user,
                posted_by=user,
                posted_at=timezone.now(),
            )
            JournalLine.objects.bulk_create(
                JournalLine(entry=entry, account=a, debit=d, credit=c, description=desc[:240], campus=campus)
                for a, d, c, desc, campus in cleaned
            )
    except IntegrityError:
        raise FinanceError("This operation was already posted.", 409)
    return entry, True


def reverse_entry(entry, *, user=None, memo="", on_date=None):
    """Post the mirror image of an entry. Safe to retry: the key is derived from the entry."""
    lines = [
        (line.account, line.credit, line.debit, f"Reversal: {line.description}".strip(), line.campus)
        for line in entry.lines.select_related("account")
    ]
    return post_entry(
        entry.tenant_id,
        entry_date=on_date or timezone.localdate(),
        memo=memo or f"Reversal of {entry.number}",
        source_type="reversal",
        source_id=entry.pk,
        idempotency_key=f"reversal:{entry.pk}",
        lines=lines,
        user=user,
        reverses=entry,
    )
