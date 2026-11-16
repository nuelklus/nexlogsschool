from dataclasses import replace
from decimal import Decimal

from django.test import SimpleTestCase

from .grading import (
    Component, GradeBand, GradingError, GradingPolicy, Mark, MarkStatus,
    RoundingRule, calculate_subject, validate_policy,
)

D = Decimal


class GradingCalculationTests(SimpleTestCase):
    def setUp(self):
        self.policy = GradingPolicy(
            components=(Component("coursework", D("20"), D("40")), Component("exam", D("100"), D("60"))),
            bands=(
                GradeBand(D("0"), "F", "Needs improvement", False),
                GradeBand(D("50"), "P", "Pass", True),
                GradeBand(D("80"), "A", "Excellent", True),
            ),
            decimal_places=2,
            rounding=RoundingRule.HALF_UP,
            allow_decimal_scores=True,
            exclude_not_applicable=False,
        )

    def test_weighted_marks_and_boundaries(self):
        marks = (Mark("coursework", MarkStatus.SCORED, D("10")), Mark("exam", MarkStatus.SCORED, D("50")))
        result = calculate_subject(self.policy, marks)
        self.assertEqual((result.score, result.grade, result.passed), (D("50.00"), "P", True))
        marks = (replace(marks[0], score=D("20")), replace(marks[1], score=D("66.6667")))
        self.assertEqual(calculate_subject(self.policy, marks).grade, "A")
        self.assertEqual(calculate_subject(self.policy, (replace(marks[0], score=D("0")), replace(marks[1], score=D("0")))).grade, "F")

    def test_exempt_components_are_redistributed(self):
        marks = (Mark("coursework", MarkStatus.EXEMPT), Mark("exam", MarkStatus.SCORED, D("75")))
        self.assertEqual(calculate_subject(self.policy, marks).score, D("75.00"))

    def test_not_applicable_requires_explicit_policy(self):
        marks = (Mark("coursework", MarkStatus.NOT_APPLICABLE), Mark("exam", MarkStatus.SCORED, D("75")))
        with self.assertRaises(GradingError):
            calculate_subject(self.policy, marks)
        self.assertEqual(calculate_subject(replace(self.policy, exclude_not_applicable=True), marks).score, D("75.00"))

    def test_incomplete_or_invalid_marks_are_never_converted_to_zero(self):
        scored = Mark("exam", MarkStatus.SCORED, D("50"))
        for marks in (
            (scored,),
            (Mark("coursework", MarkStatus.ABSENT), scored),
            (Mark("coursework", MarkStatus.MISSING), scored),
            (Mark("coursework", MarkStatus.EXEMPT, D("0")), scored),
            (Mark("coursework", MarkStatus.SCORED, D("21")), scored),
            (Mark("coursework", MarkStatus.SCORED, D("-1")), scored),
            (Mark("coursework", MarkStatus.SCORED, D("NaN")), scored),
            (Mark("coursework", MarkStatus.EXEMPT), Mark("exam", MarkStatus.EXEMPT)),
            (scored, scored),
            (Mark("other", MarkStatus.SCORED, D("1")), scored),
        ):
            with self.subTest(marks=marks), self.assertRaises(GradingError):
                calculate_subject(self.policy, marks)

    def test_rounding_is_configurable_and_applied_before_band_selection(self):
        policy = replace(
            self.policy, components=(Component("exam", D("100"), D("100")),), decimal_places=0
        )
        marks = (Mark("exam", MarkStatus.SCORED, D("79.5")),)
        self.assertEqual(calculate_subject(policy, marks).grade, "A")
        self.assertEqual(calculate_subject(replace(policy, rounding=RoundingRule.DOWN), marks).grade, "P")
        marks = (Mark("exam", MarkStatus.SCORED, D("78.5")),)
        self.assertEqual(calculate_subject(replace(policy, rounding=RoundingRule.HALF_EVEN), marks).score, D("78"))
        self.assertEqual(calculate_subject(policy, marks).score, D("79"))

    def test_decimal_scores_follow_school_policy(self):
        marks = (Mark("coursework", MarkStatus.SCORED, D("10.5")), Mark("exam", MarkStatus.SCORED, D("50")))
        with self.assertRaises(GradingError):
            calculate_subject(replace(self.policy, allow_decimal_scores=False), marks)
        self.assertEqual(calculate_subject(self.policy, marks).score, D("51.00"))

    def test_exact_rounding_after_repeating_fractions(self):
        policy = replace(
            self.policy,
            components=(Component("a", D("3"), D("48.5")), Component("b", D("3"), D("51.5"))),
            decimal_places=0,
        )
        marks = (Mark("a", MarkStatus.SCORED, D("1")), Mark("b", MarkStatus.SCORED, D("2")))
        self.assertEqual(calculate_subject(policy, marks).score, D("51"))
        self.assertEqual(calculate_subject(replace(policy, rounding=RoundingRule.HALF_EVEN), marks).score, D("50"))

    def test_invalid_configuration_is_rejected(self):
        for policy in (
            replace(self.policy, components=()),
            replace(self.policy, components=(Component("a", D("0"), D("100")),)),
            replace(self.policy, components=(Component("a", D("10"), D("99")),)),
            replace(self.policy, bands=(GradeBand(D("50"), "P", "Pass", True),)),
            replace(self.policy, bands=(GradeBand(D("0"), "P", "", True), GradeBand(D("0"), "F", "", False))),
            replace(self.policy, decimal_places=7),
            replace(self.policy, rounding="invalid"),
        ):
            with self.subTest(policy=policy), self.assertRaises(GradingError):
                validate_policy(policy)
