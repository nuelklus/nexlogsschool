import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields.get("is_staff") or not extra_fields.get("is_superuser"):
            raise ValueError("A superuser must have staff and superuser privileges.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=False)
    full_name = models.CharField(max_length=160)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def __str__(self):
        return self.email


class Tenant(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        SUSPENDED = "suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    country = models.CharField(max_length=100, default="Ghana")
    region = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=240, blank=True)
    school_type = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices,default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class PlatformSetupState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    initial_setup_completed = models.BooleanField(default=False)


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner"
        SCHOOL_ADMIN = "school_admin"
        ACCOUNTANT = "accountant"
        TEACHER = "teacher"
        PARENT = "parent"
        STUDENT = "student"

    class Status(models.TextChoices):
        ACTIVE = "active"
        SUSPENDED = "suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    status = models.CharField(max_length=16, choices=Status.choices,default=Status.ACTIVE)
    joined_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "user"), name="unique_tenant_membership"),
        ]


class Campus(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        INACTIVE = "inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="campuses")
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=40)
    address = models.CharField(max_length=240, blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    contact_email = models.EmailField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices,default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="unique_campus_code_per_tenant"),
        ]


class AcademicYear(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="academic_years")
    name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=(("active", "Active"), ("closed", "Closed")), default="active")
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "name"), name="unique_academic_year_name_per_tenant"),
            models.UniqueConstraint(fields=("tenant",), condition=Q(is_current=True), name="one_current_academic_year_per_tenant"),
            models.CheckConstraint(check=Q(end_date__gt=F("start_date")), name="academic_year_end_after_start"),
        ]


class SubscriptionPlan(models.Model):
    class Interval(models.TextChoices):
        MONTHLY = "monthly"
        TERMLY = "termly"
        YEARLY = "yearly"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=40, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="GHS")
    billing_interval = models.CharField(max_length=12, choices=Interval.choices, default=Interval.MONTHLY)
    trial_days = models.PositiveSmallIntegerField(default=30)
    entitlements = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class TenantSubscription(models.Model):
    class Status(models.TextChoices):
        TRIALING = "trialing"
        ACTIVE = "active"
        EXPIRED = "expired"
        CANCELLED = "cancelled"
        SUSPENDED = "suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey("SubscriptionPlan", on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices,default=Status.TRIALING)
    trial_started_at = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("tenant",),
                condition=Q(status__in=("trialing", "active", "suspended")),
                name="one_live_subscription_per_tenant",
            )
        ]


class MembershipInvitation(models.Model):
    class Role(models.TextChoices):
        SCHOOL_ADMIN = "school_admin"
        ACCOUNTANT = "accountant"
        TEACHER = "teacher"
        PARENT = "parent"
        STUDENT = "student"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=Role.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey("User", on_delete=models.PROTECT,related_name="sent_invitations")
    student = models.ForeignKey("Student", null=True, blank=True, on_delete=models.CASCADE, related_name="invitations")
    guardian_relationship = models.CharField(max_length=40, blank=True)
    guardian_is_primary = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AdmissionSettings(models.Model):
    class ResetPolicy(models.TextChoices):
        ANNUAL = "annual", "Restart every year"
        CONTINUOUS = "continuous", "Continuous"

    class YearPolicy(models.TextChoices):
        CALENDAR = "calendar", "Calendar year of admission"
        ACADEMIC_YEAR = "academic_year", "Start year of the current academic year"

    tenant = models.OneToOneField(Tenant, primary_key=True, on_delete=models.CASCADE, related_name="admission_settings")
    prefix = models.CharField(max_length=12)
    number_format = models.CharField(max_length=60, default="{prefix}-{year}-{seq}")
    padding = models.PositiveSmallIntegerField(default=4)
    reset_policy = models.CharField(max_length=12, choices=ResetPolicy.choices, default=ResetPolicy.ANNUAL)
    year_policy = models.CharField(max_length=14, choices=YearPolicy.choices, default=YearPolicy.CALENDAR)
    updated_at = models.DateTimeField(auto_now=True)


class AdmissionSequence(models.Model):
    # period is the admission year for annual sequences and 0 for continuous ones.
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="admission_sequences")
    period = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "period"), name="unique_admission_sequence_per_period"),
        ]


class Student(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        WITHDRAWN = "withdrawn"
        GRADUATED = "graduated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="students")
    campus = models.ForeignKey(Campus, null=True, blank=True, on_delete=models.SET_NULL, related_name="students")
    admission_number = models.CharField(max_length=40)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    grade_level = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    # Portal account; set only when the student accepts a portal invitation.
    user = models.OneToOneField("User", null=True, blank=True, on_delete=models.SET_NULL, related_name="student_profile")
    enrolled_on = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("last_name", "first_name")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "admission_number"), name="unique_admission_number_per_tenant"),
        ]


class StudentGuardian(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="student_guardians")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardians")
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="guardian_links")
    relationship = models.CharField(max_length=40, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("student", "user"), name="unique_guardian_per_student"),
        ]


class EmailActionToken(models.Model):
    class Purpose(models.TextChoices):
        VERIFY_EMAIL = "verify_email"
        RESET_PASSWORD = "reset_password"

    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="email_action_tokens")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    actor = models.ForeignKey("User", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=80, blank=True)
    entity_id = models.CharField(max_length=80, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    request_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ("-timestamp",)
