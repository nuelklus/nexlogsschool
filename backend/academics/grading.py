"""Pure calculation primitives for configurable academic grading."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction


ZERO = Decimal("0")
HUNDRED = Decimal("100")


class GradingError(ValueError):
    """A grading configuration or set of marks cannot produce a result."""


class MarkStatus(str, Enum):
    SCORED = "scored"
    MISSING = "missing"
    ABSENT = "absent"
    EXEMPT = "exempt"
    NOT_APPLICABLE = "not_applicable"


class RoundingRule(str, Enum):
    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    DOWN = "down"


@dataclass(frozen=True)
class Component:
    key: str
    maximum: Decimal
    weight: Decimal


@dataclass(frozen=True)
class Mark:
    component: str
    status: MarkStatus
    score: Decimal | None = None


@dataclass(frozen=True)
class GradeBand:
    minimum: Decimal
    grade: str
    description: str
    passed: bool


@dataclass(frozen=True)
class GradingPolicy:
    components: tuple[Component, ...]
    bands: tuple[GradeBand, ...]
    decimal_places: int
    rounding: RoundingRule
    allow_decimal_scores: bool
    exclude_not_applicable: bool


@dataclass(frozen=True)
class SubjectResult:
    score: Decimal
    grade: str
    description: str
    passed: bool


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise GradingError(f"{name} must be a finite decimal.")
    return value


def validate_policy(policy: GradingPolicy) -> None:
    if not policy.components:
        raise GradingError("At least one component is required.")
    keys = [component.key for component in policy.components]
    if any(not isinstance(key, str) or not key.strip() for key in keys) or len(set(keys)) != len(keys):
        raise GradingError("Component identifiers must be non-empty and unique.")
    for component in policy.components:
        if _decimal(component.maximum, "Component maximum") <= ZERO:
            raise GradingError("Component maximum must be greater than zero.")
        if not ZERO < _decimal(component.weight, "Component weight") <= HUNDRED:
            raise GradingError("Component weights must be greater than zero and at most 100.")
    if sum((Fraction(component.weight) for component in policy.components), Fraction(0)) != 100:
        raise GradingError("Component weights must add up to 100.")
    if not policy.bands:
        raise GradingError("At least one grade band is required.")
    minimums = []
    for band in policy.bands:
        minimum = _decimal(band.minimum, "Grade minimum")
        if not ZERO <= minimum <= HUNDRED:
            raise GradingError("Grade minimums must be between 0 and 100.")
        if not isinstance(band.grade, str) or not band.grade.strip() or not isinstance(band.description, str):
            raise GradingError("Each grade needs a name and a description.")
        if not isinstance(band.passed, bool):
            raise GradingError("Each grade must specify whether it passes.")
        minimums.append(minimum)
    if len(set(minimums)) != len(minimums) or ZERO not in minimums:
        raise GradingError("Grade minimums must be unique and include 0.")
    if isinstance(policy.decimal_places, bool) or not isinstance(policy.decimal_places, int) or not 0 <= policy.decimal_places <= 6:
        raise GradingError("Decimal places must be an integer from 0 to 6.")
    if not isinstance(policy.rounding, RoundingRule):
        raise GradingError("Choose a supported rounding rule.")
    if not isinstance(policy.allow_decimal_scores, bool):
        raise GradingError("Decimal-score permission must be specified.")
    if not isinstance(policy.exclude_not_applicable, bool):
        raise GradingError("The treatment of not-applicable marks must be specified.")


def _round_score(value: Fraction, places: int, rule: RoundingRule) -> Decimal:
    # Round the exact rational result, including ties formed by repeating decimals.
    units, remainder = divmod(value.numerator * 10**places, value.denominator)
    if rule != RoundingRule.DOWN and (
        2 * remainder > value.denominator
        or (
            2 * remainder == value.denominator
            and (rule == RoundingRule.HALF_UP or units % 2 == 1)
        )
    ):
        units += 1
    return Decimal(units).scaleb(-places)


def calculate_subject(policy: GradingPolicy, marks: tuple[Mark, ...]) -> SubjectResult:
    """Calculate a complete result; absent/missing marks never become zero."""
    validate_policy(policy)
    by_component = {}
    for mark in marks:
        if mark.component in by_component:
            raise GradingError("A component has more than one mark.")
        if not isinstance(mark.status, MarkStatus):
            raise GradingError("A mark has an invalid status.")
        by_component[mark.component] = mark
    expected = {component.key for component in policy.components}
    if set(by_component) - expected:
        raise GradingError("A mark refers to an unknown component.")
    if expected - set(by_component):
        raise GradingError("Marks are incomplete.")

    weighted, included_weight = Fraction(0), Fraction(0)
    for component in policy.components:
        mark = by_component[component.key]
        if mark.status != MarkStatus.SCORED:
            if mark.score is not None:
                raise GradingError("Only scored marks may contain a score.")
            if mark.status in (MarkStatus.MISSING, MarkStatus.ABSENT):
                raise GradingError("Missing or absent marks prevent finalization.")
            if mark.status == MarkStatus.NOT_APPLICABLE and not policy.exclude_not_applicable:
                raise GradingError("Not-applicable marks prevent finalization under this policy.")
            continue
        score = _decimal(mark.score, "Score")
        if not ZERO <= score <= component.maximum:
            raise GradingError("A score must be between zero and the component maximum.")
        if not policy.allow_decimal_scores and score != score.to_integral_value():
            raise GradingError("This policy does not allow decimal scores.")
        weighted += Fraction(score) / Fraction(component.maximum) * Fraction(component.weight)
        included_weight += Fraction(component.weight)
    if included_weight == 0:
        raise GradingError("At least one component must have a score.")
    result = _round_score(weighted * 100 / included_weight, policy.decimal_places, policy.rounding)
    band = max((band for band in policy.bands if result >= band.minimum), key=lambda item: item.minimum)
    return SubjectResult(result, band.grade, band.description, band.passed)
