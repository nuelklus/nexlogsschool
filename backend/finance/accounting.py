"""Opening-balance posting and general-ledger read models."""

from django.db import transaction

from .ledger import FinanceError, lock_finance, post_entry, reverse_entry
from .models import Account, JournalEntry, JournalLine, OpeningBalanceImport, OpeningBalanceLine, ZERO


def opening_balance_out(imported, *, detail=False):
    data = {
        "id": str(imported.pk),
        "name": imported.name,
        "as_of_date": imported.as_of_date.isoformat(),
        "import_reference": imported.import_reference,
        "status": imported.status,
        "created_at": imported.created_at.isoformat(),
        "created_by": imported.created_by.full_name if imported.created_by_id else "",
        "created_by_id": str(imported.created_by_id) if imported.created_by_id else None,
        "approved_by": imported.approved_by.full_name if imported.approved_by_id else "",
    }
    if detail:
        lines = list(imported.lines.select_related("account").order_by("account__code"))
        data["lines"] = [
            {
                "account": str(line.account_id),
                "account_code": line.account.code,
                "account_name": line.account.name,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "description": line.description,
            }
            for line in lines
        ]
        data["total_debit"] = str(sum((line.debit for line in lines), ZERO))
        data["total_credit"] = str(sum((line.credit for line in lines), ZERO))
        data["balanced"] = bool(lines) and sum((line.debit for line in lines), ZERO) == sum(
            (line.credit for line in lines), ZERO
        )
    return data


def create_opening_import(tenant_id, *, name, as_of_date, import_reference, lines, user):
    accounts, debit_total, credit_total = validate_opening_lines(tenant_id, lines)
    if debit_total <= 0 or debit_total != credit_total:
        raise FinanceError("Opening balances must have equal, non-zero debit and credit totals.", 400, "lines")

    with transaction.atomic():
        lock_finance(tenant_id)
        if import_reference and OpeningBalanceImport.objects.filter(
            tenant_id=tenant_id, import_reference=import_reference
        ).exists():
            raise FinanceError("This import reference has already been used.", 409, "import_reference")
        imported = OpeningBalanceImport.objects.create(
            tenant_id=tenant_id,
            name=name,
            as_of_date=as_of_date,
            import_reference=import_reference,
            created_by=user,
        )
        OpeningBalanceLine.objects.bulk_create(
            [
                OpeningBalanceLine(
                    opening_import=imported,
                    account=accounts[line["account"]],
                    debit=line["debit"],
                    credit=line["credit"],
                    description=line["description"],
                )
                for line in lines
            ]
        )
    return imported


def validate_opening_lines(tenant_id, lines):
    if not isinstance(lines, list) or len(lines) < 2:
        raise FinanceError("Provide at least two opening-balance lines.", 400, "lines")
    account_ids = [line["account"] for line in lines]
    if len(set(account_ids)) != len(account_ids):
        raise FinanceError("Each account can appear only once in an opening import.", 400, "lines")
    accounts = {
        account.pk: account
        for account in Account.objects.filter(
            tenant_id=tenant_id, pk__in=account_ids, is_active=True, is_header=False
        )
    }
    if len(accounts) != len(account_ids):
        raise FinanceError("Every account must be active and belong to this school.", 400, "lines")
    debit_total = sum((line["debit"] for line in lines), ZERO)
    credit_total = sum((line["credit"] for line in lines), ZERO)
    return accounts, debit_total, credit_total


def approve_opening_import(tenant_id, import_id, *, user):
    with transaction.atomic():
        lock_finance(tenant_id)
        imported = OpeningBalanceImport.objects.select_for_update().filter(
            tenant_id=tenant_id, pk=import_id
        ).first()
        if imported is None:
            raise FinanceError("Opening-balance import not found.", 404)
        if imported.status != OpeningBalanceImport.Status.DRAFT:
            raise FinanceError("Only a draft opening-balance import can be approved.", 409)
        if imported.created_by_id == user.pk:
            raise FinanceError("The person who prepared an opening-balance import cannot approve it.", 409)
        lines = list(imported.lines.select_related("account"))
        debit = sum((line.debit for line in lines), ZERO)
        credit = sum((line.credit for line in lines), ZERO)
        if len(lines) < 2 or debit <= 0 or debit != credit:
            raise FinanceError("Opening balances do not reconcile. Correct the import before approval.", 409)
        imported.status = OpeningBalanceImport.Status.APPROVED
        imported.approved_by = user
        imported.save(update_fields=("status", "approved_by"))
    return imported


def post_opening_import(tenant_id, import_id, *, user):
    with transaction.atomic():
        lock_finance(tenant_id)
        imported = OpeningBalanceImport.objects.select_for_update().filter(
            tenant_id=tenant_id, pk=import_id
        ).first()
        if imported is None:
            raise FinanceError("Opening-balance import not found.", 404)
        if imported.status == OpeningBalanceImport.Status.POSTED:
            return imported, False
        if imported.status != OpeningBalanceImport.Status.APPROVED:
            raise FinanceError("Approve the opening-balance import before posting.", 409)
        lines = list(imported.lines.select_related("account"))
        entry, created = post_entry(
            tenant_id,
            entry_date=imported.as_of_date,
            memo=f"Opening balances: {imported.name}",
            source_type="opening_balance",
            source_id=imported.pk,
            idempotency_key=f"opening-balance:{imported.pk}",
            lines=[
                (line.account, line.debit, line.credit, line.description or imported.name)
                for line in lines
            ],
            user=user,
        )
        imported.status = OpeningBalanceImport.Status.POSTED
        imported.journal_entry = entry
        imported.save(update_fields=("status", "journal_entry"))
    return imported, created


def reverse_opening_import(tenant_id, import_id, *, user, reason):
    with transaction.atomic():
        lock_finance(tenant_id)
        imported = OpeningBalanceImport.objects.select_for_update().filter(
            tenant_id=tenant_id, pk=import_id
        ).first()
        if imported is None:
            raise FinanceError("Opening-balance import not found.", 404)
        if imported.status == OpeningBalanceImport.Status.REVERSED:
            return imported, imported.journal_entry.reversed_by, False
        if imported.status != OpeningBalanceImport.Status.POSTED or not imported.journal_entry_id:
            raise FinanceError("Only a posted opening-balance import can be reversed.", 409)
        reversal, created = reverse_entry(
            imported.journal_entry,
            user=user,
            memo=f"Reverse opening balances: {reason[:180]}",
        )
        imported.status = OpeningBalanceImport.Status.REVERSED
        imported.save(update_fields=("status",))
    return imported, reversal, created


def journal_register(tenant_id, *, start=None, end=None, limit=500):
    entries = JournalEntry.objects.filter(tenant_id=tenant_id).prefetch_related("lines")
    if start:
        entries = entries.filter(entry_date__gte=start)
    if end:
        entries = entries.filter(entry_date__lte=end)
    result = []
    for entry in entries.order_by("-entry_date", "-created_at")[:limit]:
        debit = sum((line.debit for line in entry.lines.all()), ZERO)
        credit = sum((line.credit for line in entry.lines.all()), ZERO)
        result.append(
            {
                "id": str(entry.pk),
                "number": entry.number,
                "date": entry.entry_date.isoformat(),
                "memo": entry.memo,
                "source_type": entry.source_type,
                "source_id": entry.source_id,
                "status": entry.status,
                "debit_total": str(debit),
                "credit_total": str(credit),
                "created_by": entry.created_by.full_name if entry.created_by_id else "",
            }
        )
    return result


def general_ledger(tenant_id, *, account_id, start=None, end=None, campus_id=None, limit=1000):
    account = Account.objects.filter(tenant_id=tenant_id, pk=account_id).first()
    if account is None:
        raise FinanceError("Account not found.", 404)
    all_lines = JournalLine.objects.filter(entry__tenant_id=tenant_id, account=account)
    if campus_id:
        all_lines = all_lines.filter(campus_id=campus_id)
    before = all_lines.filter(entry__entry_date__lt=start) if start else all_lines.none()
    prior = list(before.select_related("entry"))
    opening = sum((line.debit - line.credit for line in prior), ZERO)
    movements = all_lines.select_related("entry", "campus")
    if start:
        movements = movements.filter(entry__entry_date__gte=start)
    if end:
        movements = movements.filter(entry__entry_date__lte=end)
    movements = movements.order_by("entry__entry_date", "entry__created_at", "id")[:limit]
    running = opening
    entries = []
    for line in movements:
        running += line.debit - line.credit
        entries.append(
            {
                "date": line.entry.entry_date.isoformat(),
                "journal": line.entry.number,
                "source_type": line.entry.source_type,
                "source_id": line.entry.source_id,
                "description": line.description or line.entry.memo,
                "campus": line.campus.name if line.campus_id else "",
                "debit": str(line.debit),
                "credit": str(line.credit),
                "balance": str(running),
            }
        )
    return {
        "account": {"id": str(account.pk), "code": account.code, "name": account.name, "type": account.type},
        "opening_balance": str(opening),
        "entries": entries,
        "closing_balance": str(running),
        "balance_basis": "debit minus credit",
    }
