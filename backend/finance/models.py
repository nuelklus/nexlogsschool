import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

MONEY = {"max_digits": 14, "decimal_places": 2}
ZERO = Decimal("0.00")


class ImmutableError(Exception):
    """Raised when code tries to edit or delete a posted accounting record."""


class PostedQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableError("Posted accounting records cannot be updated in bulk.")

    def delete(self):
        raise ImmutableError("Posted accounting records cannot be deleted. Post a reversal instead.")


class FinanceSettings(models.Model):
    """Per-school finance configuration and document counters.

    The row doubles as the lock that serialises every finance write for the school.
    """

    tenant = models.OneToOneField("core.Tenant", primary_key=True, on_delete=models.CASCADE, related_name="finance_settings")
    currency = models.CharField(max_length=3, default="GHS")
    invoice_prefix = models.CharField(max_length=10, default="INV")
    receipt_prefix = models.CharField(max_length=10, default="RCT")
    credit_note_prefix = models.CharField(max_length=10, default="CN")
    journal_prefix = models.CharField(max_length=10, default="JE")
    supplier_voucher_prefix = models.CharField(max_length=10, default="PV")
    default_payment_terms_days = models.PositiveSmallIntegerField(default=30)
    next_invoice_number = models.PositiveIntegerField(default=1)
    next_receipt_number = models.PositiveIntegerField(default=1)
    next_credit_note_number = models.PositiveIntegerField(default=1)
    next_journal_number = models.PositiveIntegerField(default=1)
    next_supplier_voucher_number = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class AccountingPeriod(models.Model):
    class Status(models.TextChoices):
        OPEN = "open"
        CLOSED = "closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="accounting_periods")
    name = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ("start_date",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "name"), name="unique_period_name_per_tenant"),
            models.CheckConstraint(check=Q(end_date__gte=F("start_date")), name="period_end_not_before_start"),
        ]


class Account(models.Model):
    class Type(models.TextChoices):
        ASSET = "asset"
        LIABILITY = "liability"
        EQUITY = "equity"
        INCOME = "income"
        EXPENSE = "expense"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="ledger_accounts")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=160)
    type = models.CharField(max_length=10, choices=Type.choices)
    normal_balance = models.CharField(max_length=6, choices=(("debit", "Debit"), ("credit", "Credit")), blank=True)
    reporting_class = models.CharField(max_length=40, blank=True)
    is_header = models.BooleanField(default=False)
    # Marks the accounts the automatic postings rely on (receivable, advances, ...).
    system_key = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="unique_account_code_per_tenant"),
            models.UniqueConstraint(
                fields=("tenant", "system_key"), condition=~Q(system_key=""), name="unique_system_account_per_tenant"
            ),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"


class JournalEntry(models.Model):
    """An immutable, balanced, posted entry. Corrections are new reversing entries."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="journal_entries")
    number = models.CharField(max_length=30)
    entry_date = models.DateField()
    memo = models.CharField(max_length=240, blank=True)
    source_type = models.CharField(max_length=30)
    source_id = models.CharField(max_length=40, blank=True)
    # Replaying a request with the same key can never post a second entry.
    idempotency_key = models.CharField(max_length=120)
    status = models.CharField(max_length=10, choices=(("posted", "Posted"),), default="posted")
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    posted_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)

    objects = PostedQuerySet.as_manager()

    class Meta:
        ordering = ("-entry_date", "-created_at")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "number"), name="unique_journal_number_per_tenant"),
            models.UniqueConstraint(fields=("tenant", "idempotency_key"), name="unique_journal_idempotency_key"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Posted journal entries cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Posted journal entries cannot be deleted. Post a reversal instead.")


class JournalLine(models.Model):
    id = models.BigAutoField(primary_key=True)
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(default=ZERO, **MONEY)
    credit = models.DecimalField(default=ZERO, **MONEY)
    description = models.CharField(max_length=240, blank=True)

    objects = PostedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0)),
                name="journal_line_one_positive_side",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Posted journal lines cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Posted journal lines cannot be deleted.")


class PaymentMethod(models.Model):
    class Kind(models.TextChoices):
        CASH = "cash"
        BANK_TRANSFER = "bank_transfer"
        CHEQUE = "cheque"
        MOBILE_MONEY = "mobile_money"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="payment_methods")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=14, choices=Kind.choices)
    # The cash, bank or mobile-money ledger account the money lands in.
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payment_methods")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "name"), name="unique_payment_method_per_tenant"),
        ]


class FeeCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="fee_categories")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    income_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="fee_categories")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="unique_fee_category_code_per_tenant"),
        ]


class FeeSchedule(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        INACTIVE = "inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="fee_schedules")
    academic_year = models.ForeignKey("core.AcademicYear", on_delete=models.PROTECT, related_name="fee_schedules")
    category = models.ForeignKey(FeeCategory, on_delete=models.PROTECT, related_name="schedules")
    # Matches Student.grade_level; blank means every class.
    grade_level = models.CharField(max_length=40, blank=True)
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="fee_schedules")
    # For a per-unit fee this is the price of one unit (for example one meal).
    amount = models.DecimalField(**MONEY)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Applicability(models.TextChoices):
        COMPULSORY = "compulsory"  # every eligible student
        OPTIONAL = "optional"  # only students subscribed to the service
        CONDITIONAL = "conditional"  # only students meeting the condition below
        INDIVIDUAL = "individual"  # only students it has been individually assigned to

    class Basis(models.TextChoices):
        FIXED = "fixed"
        PER_UNIT = "per_unit"

    class Condition(models.TextChoices):
        NONE = ""
        GENDER = "gender"
        NEW_STUDENT = "new_student"  # enrolled during the academic year
        SERVICE = "service"  # subscribed to another service; value is its category code

    applicability = models.CharField(max_length=12, choices=Applicability.choices, default=Applicability.COMPULSORY)
    billing_basis = models.CharField(max_length=10, choices=Basis.choices, default=Basis.FIXED)
    unit_label = models.CharField(max_length=30, blank=True)
    condition_type = models.CharField(max_length=12, choices=Condition.choices, blank=True, default="")
    condition_value = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("academic_year", "grade_level", "category__name")
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="fee_schedule_amount_positive"),
            models.CheckConstraint(
                check=Q(billing_basis="fixed") | ~Q(unit_label=""), name="fee_schedule_per_unit_needs_label"
            ),
            models.CheckConstraint(
                check=(Q(applicability="conditional") & ~Q(condition_type=""))
                | (~Q(applicability="conditional") & Q(condition_type="")),
                name="fee_schedule_condition_only_when_conditional",
            ),
            models.CheckConstraint(
                check=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                name="fee_schedule_dates_ordered",
            ),
        ]


class StudentAdjustment(models.Model):
    """A scholarship, approved discount or extra charge applied when an invoice is generated."""

    class Kind(models.TextChoices):
        SCHOLARSHIP = "scholarship"
        DISCOUNT = "discount"
        CHARGE = "charge"
        # Waives one fee category for the student for the whole academic year.
        EXEMPTION = "exemption"

    class Status(models.TextChoices):
        APPROVED = "approved"
        REVOKED = "revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="student_adjustments")
    student = models.ForeignKey("core.Student", on_delete=models.CASCADE, related_name="fee_adjustments")
    academic_year = models.ForeignKey("core.AcademicYear", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    category = models.ForeignKey("FeeCategory", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=160)
    reason = models.CharField(max_length=240, blank=True)
    # Exactly one of amount / percent. A percentage applies to the fee subtotal.
    amount = models.DecimalField(null=True, blank=True, **MONEY)
    percent = models.DecimalField(null=True, blank=True, max_digits=5, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.APPROVED)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(kind="exemption", category__isnull=False, amount__isnull=True, percent__isnull=True)
                    | Q(~Q(kind="exemption"), amount__gt=0, percent__isnull=True)
                    | Q(~Q(kind="exemption"), percent__gt=0, percent__lte=100, amount__isnull=True)
                ),
                name="adjustment_value_matches_kind",
            ),
        ]


class ServiceEnrollment(models.Model):
    """A student's subscription to an optional or individually assigned service (lunch, transport, ...)."""

    class Status(models.TextChoices):
        ACTIVE = "active"
        SUSPENDED = "suspended"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="service_enrollments")
    student = models.ForeignKey("core.Student", on_delete=models.CASCADE, related_name="service_enrollments")
    category = models.ForeignKey(FeeCategory, on_delete=models.PROTECT, related_name="enrollments")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-start_date", "-created_at")
        indexes = [models.Index(fields=("tenant", "student", "category"), name="service_enrol_lookup")]
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="service_enrollment_dates_ordered"
            ),
        ]

    def covers(self, on_date):
        return (
            self.status == self.Status.ACTIVE
            and self.start_date <= on_date
            and (self.end_date is None or self.end_date >= on_date)
        )


class ServiceEnrollmentEvent(models.Model):
    """Append-only history of every change to a service enrollment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="+")
    enrollment = models.ForeignKey(ServiceEnrollment, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=20)
    detail = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Enrollment history cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Enrollment history cannot be deleted.")


class ServiceUsage(models.Model):
    """Units consumed in a billing period (for example meals taken), used by per-unit fees."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="service_usage")
    student = models.ForeignKey("core.Student", on_delete=models.CASCADE, related_name="service_usage")
    category = models.ForeignKey(FeeCategory, on_delete=models.PROTECT, related_name="usage")
    academic_year = models.ForeignKey("core.AcademicYear", on_delete=models.PROTECT, related_name="+")
    period_label = models.CharField(max_length=60)
    quantity = models.PositiveIntegerField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "student", "category", "academic_year", "period_label"), name="one_usage_per_student_period"
            ),
        ]


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        ISSUED = "issued"
        PARTIALLY_PAID = "partially_paid"
        PAID = "paid"
        CREDITED = "credited"
        VOIDED = "voided"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="invoices")
    # Assigned on issue; blank while a draft.
    number = models.CharField(max_length=30, blank=True)
    student = models.ForeignKey("core.Student", on_delete=models.PROTECT, related_name="invoices")
    academic_year = models.ForeignKey("core.AcademicYear", on_delete=models.PROTECT, related_name="invoices")
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    # Snapshot of the student's class at billing time (there is no enrollment model yet).
    grade_level = models.CharField(max_length=40, blank=True)
    period_label = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.DRAFT)
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField()
    subtotal = models.DecimalField(default=ZERO, **MONEY)
    discount_total = models.DecimalField(default=ZERO, **MONEY)
    total = models.DecimalField(default=ZERO, **MONEY)
    amount_paid = models.DecimalField(default=ZERO, **MONEY)
    credited_total = models.DecimalField(default=ZERO, **MONEY)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=240, blank=True)
    # What was billed, what was left out and why, as calculated when the draft was created.
    calculation = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "number"), condition=~Q(number=""), name="unique_invoice_number_per_tenant"),
            # One live invoice per student, year and billing period.
            models.UniqueConstraint(
                fields=("tenant", "student", "academic_year", "period_label"),
                condition=~Q(status="voided"),
                name="one_live_invoice_per_student_period",
            ),
            models.CheckConstraint(check=Q(amount_paid__gte=0) & Q(credited_total__gte=0), name="invoice_settlements_non_negative"),
            models.CheckConstraint(
                check=Q(amount_paid__lte=F("total")) | Q(status="voided"), name="invoice_not_overpaid"
            ),
        ]

    @property
    def balance(self):
        if self.status in (self.Status.DRAFT, self.Status.VOIDED):
            return ZERO
        return self.total - self.amount_paid - self.credited_total

    @property
    def is_receivable(self):
        return self.status in (self.Status.ISSUED, self.Status.PARTIALLY_PAID)


class InvoiceLine(models.Model):
    class Kind(models.TextChoices):
        FEE = "fee"
        CHARGE = "charge"
        DISCOUNT = "discount"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    category = models.ForeignKey(FeeCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    schedule = models.ForeignKey(FeeSchedule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    description = models.CharField(max_length=160)
    # Snapshot: later fee-schedule edits never change an issued invoice.
    amount = models.DecimalField(**MONEY)
    # Set only for per-unit fees: amount = quantity x unit_price.
    quantity = models.PositiveIntegerField(null=True, blank=True)
    unit_price = models.DecimalField(null=True, blank=True, **MONEY)

    class Meta:
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="invoice_line_amount_positive")]


class CreditNote(models.Model):
    class Reason(models.TextChoices):
        CORRECTION = "correction"
        BAD_DEBT = "bad_debt"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="credit_notes")
    number = models.CharField(max_length=30)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="credit_notes")
    reason_type = models.CharField(max_length=12, choices=Reason.choices)
    reason = models.CharField(max_length=240)
    amount = models.DecimalField(**MONEY)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "number"), name="unique_credit_note_number_per_tenant"),
            models.CheckConstraint(check=Q(amount__gt=0), name="credit_note_amount_positive"),
        ]


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        CONFIRMED = "confirmed"
        FAILED = "failed"
        REVERSED = "reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="payments")
    student = models.ForeignKey("core.Student", on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(**MONEY)
    payment_date = models.DateField()
    # Payer's reference: bank slip, cheque number or mobile-money transaction id.
    reference = models.CharField(max_length=80, blank=True)
    payer_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    receipt_number = models.CharField(max_length=30, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    allocated_total = models.DecimalField(default=ZERO, **MONEY)
    refunded_total = models.DecimalField(default=ZERO, **MONEY)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reversal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-payment_date", "-created_at")
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="payment_amount_positive"),
            models.CheckConstraint(
                check=Q(allocated_total__gte=0, refunded_total__gte=0)
                & Q(amount__gte=F("allocated_total") + F("refunded_total")),
                name="payment_totals_within_amount",
            ),
            models.UniqueConstraint(
                fields=("tenant", "receipt_number"), condition=~Q(receipt_number=""), name="unique_receipt_number_per_tenant"
            ),
            models.UniqueConstraint(
                fields=("tenant", "idempotency_key"), condition=~Q(idempotency_key=""), name="unique_payment_idempotency_key"
            ),
            # The same bank slip or mobile-money id cannot be recorded twice.
            models.UniqueConstraint(
                fields=("tenant", "method", "reference"),
                condition=~Q(reference="") & ~Q(status="failed"),
                name="unique_payment_reference_per_method",
            ),
        ]

    @property
    def unallocated(self):
        if self.status != self.Status.CONFIRMED:
            return ZERO
        return self.amount - self.allocated_total - self.refunded_total


class PaymentAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="+")
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(**MONEY)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="allocation_amount_positive")]


class Refund(models.Model):
    """Money returned from the unallocated (advance) part of a confirmed payment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="refunds")
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=240)
    refund_date = models.DateField()
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="refund_amount_positive")]


class OpeningBalanceImport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        APPROVED = "approved"
        POSTED = "posted"
        REVERSED = "reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="opening_balance_imports")
    name = models.CharField(max_length=120)
    as_of_date = models.DateField()
    import_reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "import_reference"), condition=~Q(import_reference=""), name="unique_opening_import_reference"),
        ]


class OpeningBalanceLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opening_import = models.ForeignKey(OpeningBalanceImport, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="opening_balance_lines")
    debit = models.DecimalField(default=ZERO, **MONEY)
    credit = models.DecimalField(default=ZERO, **MONEY)
    description = models.CharField(max_length=240, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0)),
                name="opening_balance_line_one_positive_side",
            ),
            models.UniqueConstraint(fields=("opening_import", "account"), name="one_opening_line_per_account"),
        ]


class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="finance_suppliers")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.CharField(max_length=240, blank=True)
    tax_id = models.CharField(max_length=40, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=30)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [models.UniqueConstraint(fields=("tenant", "code"), name="unique_supplier_code_per_tenant")]


class SupplierTaxCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="supplier_tax_codes")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="supplier_tax_codes")
    is_inclusive = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="unique_supplier_tax_code_per_tenant"),
            models.CheckConstraint(check=Q(rate__gte=0, rate__lte=100), name="supplier_tax_rate_range"),
        ]


class SupplierOpeningBalance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="supplier_opening_balances")
    opening_import = models.ForeignKey(
        OpeningBalanceImport, on_delete=models.PROTECT, related_name="supplier_opening_balances"
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="opening_balances")
    debit = models.DecimalField(default=ZERO, **MONEY)
    credit = models.DecimalField(default=ZERO, **MONEY)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("opening_import__as_of_date", "supplier__name")
        constraints = [
            models.UniqueConstraint(
                fields=("opening_import", "supplier"), name="unique_supplier_per_opening_import"
            ),
            models.CheckConstraint(
                check=Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0),
                name="supplier_opening_line_one_positive_side",
            ),
        ]


class SupplierBill(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"
        APPROVED = "approved"
        POSTED = "posted"
        PARTIALLY_PAID = "partially_paid"
        PAID = "paid"
        CANCELLED = "cancelled"
        REVERSED = "reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="supplier_bills")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="bills")
    supplier_reference = models.CharField(max_length=80)
    invoice_date = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.DRAFT)
    total = models.DecimalField(default=ZERO, **MONEY)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("due_date", "created_at")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "supplier", "supplier_reference"), name="unique_supplier_bill_reference"),
            models.CheckConstraint(check=Q(total__gte=0), name="supplier_bill_total_nonnegative"),
            models.CheckConstraint(check=Q(due_date__gte=F("invoice_date")), name="supplier_bill_dates_ordered"),
        ]


class SupplierBillLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bill = models.ForeignKey(SupplierBill, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="supplier_bill_lines")
    tax_code = models.ForeignKey(SupplierTaxCode, null=True, blank=True, on_delete=models.PROTECT, related_name="bill_lines")
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_bill_lines")
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    unit_cost = models.DecimalField(**MONEY)
    amount = models.DecimalField(**MONEY)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(default=ZERO, **MONEY)

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="supplier_bill_line_quantity_positive"),
            models.CheckConstraint(check=Q(unit_cost__gte=0), name="supplier_bill_line_unit_cost_nonnegative"),
            models.CheckConstraint(check=Q(amount__gt=0), name="supplier_bill_line_amount_positive"),
            models.CheckConstraint(check=Q(tax_rate__gte=0, tax_rate__lte=100), name="supplier_bill_line_tax_rate_range"),
            models.CheckConstraint(check=Q(tax_amount__gte=0), name="supplier_bill_line_tax_amount_nonnegative"),
        ]


class SupplierBillDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="supplier_bill_documents")
    bill = models.ForeignKey(SupplierBill, on_delete=models.PROTECT, related_name="documents")
    file = models.FileField(upload_to="finance/supplier-bills/")
    original_name = models.CharField(max_length=160)
    content_type = models.CharField(max_length=40)
    file_size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class SupplierPaymentVoucher(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        APPROVED = "approved"
        PAID = "paid"
        REVERSED = "reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="supplier_payment_vouchers")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payment_vouchers")
    number = models.CharField(max_length=30, blank=True)
    payment_date = models.DateField()
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="supplier_vouchers")
    amount = models.DecimalField(**MONEY)
    reference = models.CharField(max_length=80, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reversal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-payment_date", "-created_at")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "number"), condition=~Q(number=""), name="unique_supplier_voucher_number"),
            models.UniqueConstraint(fields=("tenant", "idempotency_key"), condition=~Q(idempotency_key=""), name="unique_supplier_voucher_idempotency"),
            models.CheckConstraint(check=Q(amount__gt=0), name="supplier_voucher_amount_positive"),
        ]


class SupplierPaymentAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voucher = models.ForeignKey(SupplierPaymentVoucher, on_delete=models.PROTECT, related_name="allocations")
    bill = models.ForeignKey(SupplierBill, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(**MONEY)

    class Meta:
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="supplier_payment_allocation_positive")]


class Expense(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"
        APPROVED = "approved"
        REJECTED = "rejected"
        PARTIALLY_PAID = "partially_paid"
        PAID = "paid"
        REVERSED = "reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="expenses")
    payee = models.CharField(max_length=160)
    description = models.CharField(max_length=240)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expenses")
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses")
    transaction_date = models.DateField()
    amount = models.DecimalField(**MONEY)
    amount_paid = models.DecimalField(default=ZERO, **MONEY)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rejection_reason = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-transaction_date", "-created_at")
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="expense_amount_positive"),
            models.CheckConstraint(check=Q(amount_paid__gte=0) & Q(amount_paid__lte=F("amount")), name="expense_paid_within_total"),
        ]


class ExpensePayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.PROTECT, related_name="expense_payments")
    expense = models.ForeignKey(Expense, on_delete=models.PROTECT, related_name="payments")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expense_payments")
    amount = models.DecimalField(**MONEY)
    payment_date = models.DateField()
    reference = models.CharField(max_length=80, blank=True)
    idempotency_key = models.CharField(max_length=120)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+")
    reversal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=(("paid", "Paid"), ("reversed", "Reversed")), default="paid")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "idempotency_key"), name="unique_expense_payment_idempotency"),
            models.CheckConstraint(check=Q(amount__gt=0), name="expense_payment_amount_positive"),
        ]
