"""Tenant-scoped, concurrency-safe admission number generation.

All generation happens inside ``transaction.atomic()`` with the tenant row locked
(``select_for_update``). That serialises admissions per school, so the sequence
row can be read and incremented without races, and it also covers the first
admission of a period where no sequence row exists yet.
"""

import re

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import AcademicYear, AdmissionSequence, AdmissionSettings, Student, Tenant

MAX_LENGTH = Student._meta.get_field("admission_number").max_length
PREFIX_RE = re.compile(r"^[A-Z0-9]{1,12}$")
FORMAT_RE = re.compile(r"^[A-Za-z0-9/_.\-{}]+$")
TOKEN_RE = re.compile(r"\{([a-z]*)\}")
MAX_PADDING = 8
MAX_SKIPS = 10000


def default_prefix(tenant):
    words = re.findall(r"[A-Za-z0-9]+", tenant.name)
    initials = "".join(word[0] for word in words).upper()[:6]
    return initials or "SCH"


def validate_config(*, prefix, number_format, padding, reset_policy, year_policy):
    """Return a dict of field -> message; empty when the configuration is valid."""
    errors = {}
    if not PREFIX_RE.match(prefix or ""):
        errors["prefix"] = "Use 1-12 capital letters or digits."
    tokens = TOKEN_RE.findall(number_format or "")
    if (
        not FORMAT_RE.match(number_format or "")
        or number_format.count("{") != len(tokens)
        or number_format.count("}") != len(tokens)
        or any(token not in {"prefix", "year", "seq"} for token in tokens)
        or tokens.count("seq") != 1
    ):
        errors["number_format"] = (
            "Use letters, digits and - / _ . with {prefix}, {year} and exactly one {seq}."
        )
    elif reset_policy == AdmissionSettings.ResetPolicy.ANNUAL and "year" not in tokens:
        errors["number_format"] = "An annual sequence needs {year} in the format."
    if not isinstance(padding, int) or not 1 <= padding <= MAX_PADDING:
        errors["padding"] = f"Padding must be between 1 and {MAX_PADDING}."
    if reset_policy not in AdmissionSettings.ResetPolicy.values:
        errors["reset_policy"] = "Choose annual or continuous."
    if year_policy not in AdmissionSettings.YearPolicy.values:
        errors["year_policy"] = "Choose calendar or academic_year."
    if not errors:
        longest = _render(number_format, prefix, "9999", "9" * padding)
        if len(longest) > MAX_LENGTH:
            errors["number_format"] = f"The generated number would exceed {MAX_LENGTH} characters."
    return errors


def _render(number_format, prefix, year, seq):
    # Plain replacement (not str.format) so a stored template can never run code.
    return number_format.replace("{prefix}", prefix).replace("{year}", year).replace("{seq}", seq)


def get_settings(tenant):
    settings, _ = AdmissionSettings.objects.get_or_create(
        tenant=tenant, defaults={"prefix": default_prefix(tenant)}
    )
    return settings


def admission_year(tenant_id, settings, on_date):
    if settings.year_policy == AdmissionSettings.YearPolicy.ACADEMIC_YEAR:
        current = AcademicYear.objects.filter(tenant_id=tenant_id, is_current=True).first()
        if current is not None:
            return current.start_date.year
    return on_date.year


def _existing_max(tenant_id, settings, year):
    """Highest sequence already used by this tenant for the period (first use only)."""
    pattern = re.escape(settings.number_format)
    pattern = pattern.replace(re.escape("{prefix}"), re.escape(settings.prefix))
    pattern = pattern.replace(re.escape("{year}"), str(year))
    pattern = pattern.replace(re.escape("{seq}"), r"(\d+)")
    regex = re.compile(f"^{pattern}$")
    highest = 0
    numbers = Student.objects.filter(tenant_id=tenant_id).values_list("admission_number", flat=True)
    for number in numbers.iterator():
        match = regex.match(number)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def next_admission_number(tenant_id, on_date):
    """Reserve and return the next admission number for the tenant.

    Must be called inside ``transaction.atomic()`` together with the student insert
    so a failed admission rolls the sequence back too.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("next_admission_number must run inside transaction.atomic().")
    tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
    settings = get_settings(tenant)
    year = admission_year(tenant_id, settings, on_date)
    period = year if settings.reset_policy == AdmissionSettings.ResetPolicy.ANNUAL else 0

    sequence, created = AdmissionSequence.objects.get_or_create(tenant=tenant, period=period)
    if created:
        sequence.last_number = _existing_max(tenant_id, settings, year)

    # Skip numbers that were assigned by hand or by an earlier format.
    for _ in range(MAX_SKIPS):
        sequence.last_number += 1
        number = _render(
            settings.number_format, settings.prefix, str(year), str(sequence.last_number).zfill(settings.padding)
        )
        if not Student.objects.filter(tenant_id=tenant_id, admission_number=number).exists():
            sequence.save(update_fields=("last_number",))
            return number
    raise ValidationError("Could not find a free admission number.")
