import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.models import AcademicYear, Student, Tenant


class ClassLevelCatalogue(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    education_stage = models.CharField(max_length=40)
    country_code = models.CharField(max_length=2)
    sort_order = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("country_code", "sort_order", "code")

    def __str__(self):
        return f"{self.name} ({self.country_code})"


class SchoolClassLevel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="class_levels")
    catalogue_level = models.ForeignKey(
        ClassLevelCatalogue, null=True, blank=True, on_delete=models.PROTECT, related_name="school_levels"
    )
    display_name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_terminal = models.BooleanField(default=False)

    class Meta:
        ordering = ("sort_order", "display_name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "catalogue_level"),
                condition=Q(catalogue_level__isnull=False),
                name="one_catalogue_level_per_school",
            ),
            models.UniqueConstraint(
                "tenant", models.functions.Lower("display_name"), name="unique_school_level_name_ci"
            ),
            models.UniqueConstraint(
                fields=("tenant",), condition=Q(is_terminal=True), name="one_terminal_level_per_school"
            ),
            models.CheckConstraint(
                check=Q(is_terminal=False) | Q(is_active=True), name="terminal_level_must_be_active"
            ),
        ]

    def __str__(self):
        return self.display_name


class AcademicTerm(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="academic_terms")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("start_date", "name")
        constraints = [
            models.UniqueConstraint(fields=("academic_year", "name"), name="unique_term_name_per_year"),
            models.UniqueConstraint(fields=("tenant",), condition=Q(is_current=True), name="one_current_term_per_tenant"),
            models.CheckConstraint(check=Q(end_date__gt=F("start_date")), name="academic_term_end_after_start"),
        ]


class SchoolClass(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="school_classes")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="classes")
    campus = models.ForeignKey(
        "core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="school_classes"
    )
    class_level = models.ForeignKey(
        SchoolClassLevel, null=True, blank=True, on_delete=models.PROTECT, related_name="classes"
    )
    class_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="class_groups"
    )
    is_active = models.BooleanField(default=True)
    name = models.CharField(max_length=80)
    section = models.CharField(max_length=40, blank=True)
    room = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name", "section")
        constraints = [
            models.UniqueConstraint(fields=("academic_year", "name", "section"), name="unique_class_section_per_year"),
        ]


class Subject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="subjects")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="unique_subject_code_per_tenant"),
        ]


class TeachingAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="teaching_assignments")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="teaching_assignments")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="teaching_assignments")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teaching_assignments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("school_class__name", "subject__name")
        constraints = [
            models.UniqueConstraint(fields=("school_class", "subject"), name="unique_subject_per_class"),
        ]


class StudentEnrollment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        WITHDRAWN = "withdrawn"
        TRANSFERRED = "transferred"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="academic_enrollments")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="academic_enrollments")
    campus = models.ForeignKey(
        "core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="academic_enrollments"
    )
    school_class = models.ForeignKey(SchoolClass, on_delete=models.PROTECT, related_name="enrollments")
    subjects = models.ManyToManyField(Subject, blank=True, related_name="student_enrollments")
    enrolled_on = models.DateField(default=timezone.localdate)
    ended_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ("student__last_name", "student__first_name")
        constraints = [
            models.UniqueConstraint(fields=("student", "school_class"), name="unique_student_class_enrollment"),
            models.CheckConstraint(
                check=Q(ended_on__isnull=True) | Q(ended_on__gte=F("enrolled_on")),
                name="academic_enrollment_dates_ordered",
            ),
        ]


class EnrollmentProvenanceAttestation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="enrollment_provenance_attestations")
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="enrollment_provenance_attestations")
    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.PROTECT, related_name="provenance_attestations")
    version = models.PositiveIntegerField()
    segment_start = models.DateField()
    segment_end = models.DateField()
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    subject_ids = models.JSONField()
    original = models.JSONField()
    source_fingerprint = models.CharField(max_length=64)
    reason = models.CharField(max_length=500)
    evidence = models.CharField(max_length=1000)
    attested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    attested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("term", "enrollment", "version"), name="unique_enrollment_provenance_version"),
            models.CheckConstraint(check=Q(segment_end__gte=F("segment_start")), name="provenance_segment_dates_ordered"),
        ]


class EnrollmentProvenanceRevocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attestation = models.OneToOneField(
        EnrollmentProvenanceAttestation, on_delete=models.PROTECT, related_name="revocation",
    )
    reason = models.CharField(max_length=500)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    revoked_at = models.DateTimeField(auto_now_add=True)


class AttendanceRecord(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present"
        ABSENT = "absent"
        LATE = "late"
        EXCUSED = "excused"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="attendance_records")
    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices)
    notes = models.CharField(max_length=240, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "enrollment__student__last_name")
        constraints = [
            models.UniqueConstraint(fields=("enrollment", "date"), name="unique_attendance_per_enrollment_date"),
        ]


class TimetableEntry(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="timetable_entries")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="timetable_entries")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="timetable_entries")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timetable_entries")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ("weekday", "start_time")
        constraints = [
            models.CheckConstraint(check=Q(end_time__gt=F("start_time")), name="timetable_end_after_start"),
        ]


class ExaminationSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="examination_sessions")
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="examination_sessions")
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "name")


class Examination(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"
        APPROVED = "approved"
        PUBLISHED = "published"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="examinations")
    session = models.ForeignKey(
        ExaminationSession, null=True, blank=True, on_delete=models.PROTECT, related_name="examinations"
    )
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="examinations")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.PROTECT, related_name="examinations")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="examinations")
    configuration = models.ForeignKey("GradingConfiguration", on_delete=models.PROTECT, related_name="examinations")
    name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    marks_deadline = models.DateField()
    publication_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    submitted_fingerprint = models.CharField(max_length=64, blank=True)
    version = models.PositiveIntegerField(default=1)
    published_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-start_date", "name")
        constraints = [
            models.CheckConstraint(check=Q(end_date__gte=F("start_date")), name="exam_dates_ordered"),
        ]


class Assessment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="assessments")
    term = models.ForeignKey(AcademicTerm, on_delete=models.CASCADE, related_name="assessments")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="assessments")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="assessments")
    name = models.CharField(max_length=120)
    assessment_date = models.DateField()
    max_score = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(0)])
    examination = models.ForeignKey(
        Examination, null=True, blank=True, on_delete=models.PROTECT, related_name="components"
    )
    component_key = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ("-assessment_date", "name")
        constraints = [
            models.UniqueConstraint(fields=("term", "school_class", "subject", "name"), name="unique_assessment_per_class_subject_term"),
        ]


class AssessmentResult(models.Model):
    class Status(models.TextChoices):
        SCORED = "scored"
        MISSING = "missing"
        ABSENT = "absent"
        EXEMPT = "exempt"
        NOT_APPLICABLE = "not_applicable"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="assessment_results")
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="results")
    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.CASCADE, related_name="assessment_results")
    score = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(0)], null=True, blank=True)
    mark_status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCORED)
    remarks = models.CharField(max_length=240, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("assessment", "enrollment"), name="unique_result_per_assessment_student"),
            models.CheckConstraint(
                check=(Q(mark_status="scored", score__isnull=False) | (~Q(mark_status="scored") & Q(score__isnull=True))),
                name="assessment_score_matches_status",
            ),
        ]


class GradingConfiguration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="grading_configurations")
    family = models.UUIDField(default=uuid.uuid4, editable=False)
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=120)
    class_level = models.ForeignKey(
        SchoolClassLevel, null=True, blank=True, on_delete=models.PROTECT, related_name="grading_configurations"
    )
    examination_type = models.CharField(max_length=80)
    configuration = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name", "-version")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "family", "version"), name="unique_grading_config_version"),
        ]


class ExaminationPublication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="examination_publications")
    examination = models.ForeignKey(Examination, on_delete=models.PROTECT, related_name="publications")
    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.PROTECT, related_name="examination_publications")
    version = models.PositiveIntegerField(default=1)
    snapshot = models.JSONField()
    published_at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("examination", "enrollment", "version"), name="unique_exam_publication_version"),
        ]


class ReportCardVersion(models.Model):
    class Status(models.TextChoices):
        REVIEWED = "reviewed"
        PUBLISHED = "published"
        WITHDRAWN = "withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="report_card_versions")
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="report_card_versions")
    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.PROTECT, related_name="report_card_versions")
    provenance_attestation = models.ForeignKey(
        EnrollmentProvenanceAttestation, null=True, blank=True, on_delete=models.PROTECT, related_name="report_card_versions"
    )
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="report_card_versions")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REVIEWED)
    snapshot = models.JSONField()
    reason = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(fields=("term", "student", "version"), name="unique_student_term_report_version"),
        ]


class TermClosure(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started"
        IN_PROGRESS = "in_progress"
        SUBMITTED_FOR_REVIEW = "submitted_for_review"
        RETURNED_FOR_CORRECTION = "returned_for_correction"
        ACADEMIC_APPROVED = "academic_approved"
        PENDING_FINAL_APPROVAL = "pending_final_approval"
        COMPLETED = "completed"
        REJECTED = "rejected"
        CANCELLED = "cancelled"
        REOPENED = "reopened"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="term_closures")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="term_closures")
    term = models.ForeignKey(AcademicTerm, on_delete=models.PROTECT, related_name="closures")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.IN_PROGRESS)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    academic_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    academic_approved_at = models.DateTimeField(null=True, blank=True)
    final_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    final_approved_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("tenant", "term"))]
        constraints = [
            # One active closure per term; cancelled or rejected ones stay as history.
            models.UniqueConstraint(
                fields=("term",),
                condition=~Q(status__in=("cancelled", "rejected")),
                name="one_active_closure_per_term",
            ),
        ]


class TermClosureChecklistItem(models.Model):
    class Status(models.TextChoices):
        COMPLETE = "complete"
        INCOMPLETE = "incomplete"
        NOT_APPLICABLE = "not_applicable"
        EXCEPTION_APPROVED = "exception_approved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    closure = models.ForeignKey(TermClosure, on_delete=models.CASCADE, related_name="items")
    category = models.CharField(max_length=20)
    code = models.CharField(max_length=40)
    title = models.CharField(max_length=160)
    description = models.CharField(max_length=300, blank=True)
    required = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INCOMPLETE)
    outstanding_count = models.PositiveIntegerField(default=0)
    evidence_summary = models.CharField(max_length=300, blank=True)
    # Affected classes and a sample of affected students, recalculated from live data.
    details = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ("category", "code")
        constraints = [models.UniqueConstraint(fields=("closure", "code"), name="unique_checklist_code_per_closure")]


class TermClosureException(models.Model):
    class Decision(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="term_closure_exceptions")
    checklist_item = models.ForeignKey(TermClosureChecklistItem, on_delete=models.CASCADE, related_name="exceptions")
    reason = models.CharField(max_length=500)
    supporting_notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approval_status = models.CharField(max_length=10, choices=Decision.choices, default=Decision.PENDING)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_comments = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("checklist_item",), condition=Q(approval_status="pending"), name="one_pending_exception_per_item"
            ),
        ]


class TermClosureApproval(models.Model):
    """Append-only history of every workflow action on a closure."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="term_closure_approvals")
    closure = models.ForeignKey(TermClosure, on_delete=models.CASCADE, related_name="approvals")
    approval_stage = models.CharField(max_length=20)
    decision = models.CharField(max_length=20)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    comments = models.CharField(max_length=500, blank=True)
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("timestamp",)
