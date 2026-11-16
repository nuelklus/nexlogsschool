from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from core.models import AcademicYear, Campus, Membership, Student, User

from .models import (
    AcademicTerm,
    SchoolClass,
    Subject,
    TeachingAssignment,
)
from core.tests import create_tenant


class AcademicFixture:
    def setUp(self):
        self.tenant = create_tenant(
            name="North Star Academy",
            slug="north-star-academics",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.other_tenant = create_tenant(
            name="South Star Academy",
            slug="south-star-academics",
            email="office@southstar.example",
            phone="+233201234568",
        )
        self.owner = self.make_member(self.tenant, "owner@northstar.example", Membership.Role.OWNER)
        self.teacher = self.make_member(self.tenant, "teacher@northstar.example", Membership.Role.TEACHER)
        self.other_owner = self.make_member(self.other_tenant, "owner@southstar.example", Membership.Role.OWNER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.base = f"/api/tenants/{self.tenant.pk}/academics/"
        self.year = AcademicYear.objects.create(
            tenant=self.tenant,
            name="2026/2027",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            is_current=True,
        )
        self.term = AcademicTerm.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            name="Term 1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 4, 30),
            is_current=True,
        )
        self.fixture_campus = (
            Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
            if getattr(self, "require_fixture_campus", False) else None
        )
        self.school_class = SchoolClass.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            name="Grade 1",
            campus=self.fixture_campus,
        )
        self.subject = Subject.objects.create(tenant=self.tenant, name="Mathematics", code="MATH")
        TeachingAssignment.objects.create(
            tenant=self.tenant,
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            admission_number="NSA-001",
            first_name="Ama",
            last_name="Mensah",
            campus=self.fixture_campus,
        )
        self.enrollment = self.client.post(
            self.base + "enrollments/",
            {
                "student": str(self.student.pk),
                "school_class": str(self.school_class.pk),
                "subjects": [str(self.subject.pk)],
                "enrolled_on": "2026-01-05",
            },
            format="json",
        ).data
        self.assessment = self.client.post(
            self.base + "assessments/",
            {
                "term": str(self.term.pk),
                "school_class": str(self.school_class.pk),
                "subject": str(self.subject.pk),
                "name": "Midterm",
                "assessment_date": "2026-03-10",
                "max_score": "100",
            },
            format="json",
        ).data

    @staticmethod
    def make_member(tenant, email, role):
        user = User.objects.create_user(email=email, password="Taller-River-804", full_name=email)
        Membership.objects.create(tenant=tenant, user=user, role=role)
        return user

class AcademicManagementApiTests(AcademicFixture, TestCase):
    def test_manager_can_create_and_list_academic_records(self):
        self.assertIn("id", self.enrollment)
        self.assertEqual(self.enrollment["subjects"], [self.subject.pk])
        self.assertEqual(self.assessment["name"], "Midterm")
        self.student.refresh_from_db()
        self.assertEqual(self.student.grade_level, self.school_class.name)

        response = self.client.post(
            self.base + "attendance/",
            {
                "enrollment": self.enrollment["id"],
                "date": "2026-02-02",
                "status": "present",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.get(self.base + "attendance/").data[0]["status"], "present")

        result = self.client.post(
            self.base + "results/",
            {
                "assessment": self.assessment["id"],
                "enrollment": self.enrollment["id"],
                "score": "87.5",
                "remarks": "Good work",
            },
            format="json",
        )
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data["score"], "87.50")

        timetable = self.client.post(
            self.base + "timetable/",
            {
                "school_class": str(self.school_class.pk),
                "subject": str(self.subject.pk),
                "teacher": str(self.teacher.pk),
                "weekday": 0,
                "start_time": "08:00",
                "end_time": "09:00",
                "room": "A1",
            },
            format="json",
        )
        self.assertEqual(timetable.status_code, 201)
        self.assertEqual(self.client.get(self.base + "timetable/").data[0]["room"], "A1")

    def test_current_academic_year_is_switched_atomically(self):
        response = self.client.post(
            self.base + "years/",
            {
                "name": "2027/2028",
                "start_date": "2027-01-01",
                "end_date": "2027-12-31",
                "is_current": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.year.refresh_from_db()
        self.assertFalse(self.year.is_current)
        self.assertTrue(AcademicYear.objects.get(pk=response.data["id"]).is_current)
        self.assertFalse(AcademicTerm.objects.filter(academic_year_id=response.data["id"]).exists())

    def test_school_sets_and_edits_its_own_terms(self):
        year = self.year
        created = self.client.post(
            self.base + "terms/",
            {"academic_year": str(year.pk), "name": "Term 2", "start_date": "2026-05-01", "end_date": "2026-08-01"},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        url = f"{self.base}terms/{created.data['id']}/"
        edited = self.client.patch(url, {"end_date": "2026-08-15"}, format="json")
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(edited.data["end_date"], "2026-08-15")
        overlap = self.client.patch(url, {"start_date": "2026-04-01"}, format="json")
        self.assertEqual(overlap.status_code, 400)
        outside = self.client.patch(url, {"end_date": "2027-02-01"}, format="json")
        self.assertEqual(outside.status_code, 400)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.patch(url, {"name": "X"}, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.patch(url, {"name": "X"}, format="json").status_code, 404)
        AcademicTerm.objects.filter(pk=created.data["id"]).update(is_completed=True)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.patch(url, {"name": "Term Two"}, format="json").status_code, 400)

    def test_teacher_can_record_assigned_class_attendance_and_results_only(self):
        self.client.force_authenticate(user=self.teacher)
        attendance = self.client.post(
            self.base + "attendance/",
            {
                "enrollment": self.enrollment["id"],
                "date": "2026-02-03",
                "status": "late",
            },
            format="json",
        )
        result = self.client.post(
            self.base + "results/",
            {
                "assessment": self.assessment["id"],
                "enrollment": self.enrollment["id"],
                "score": "70",
            },
            format="json",
        )
        self.assertEqual(attendance.status_code, 201)
        self.assertEqual(result.status_code, 201)

        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.get(self.base + "years/").status_code, 404)

    def test_validation_rejects_cross_year_terms_and_scores_above_maximum(self):
        invalid_term = self.client.post(
            self.base + "terms/",
            {
                "academic_year": str(self.year.pk),
                "name": "Outside year",
                "start_date": "2025-12-01",
                "end_date": "2026-02-01",
            },
            format="json",
        )
        invalid_score = self.client.post(
            self.base + "results/",
            {
                "assessment": self.assessment["id"],
                "enrollment": self.enrollment["id"],
                "score": "101",
            },
            format="json",
        )
        self.assertEqual(invalid_term.status_code, 400)
        self.assertEqual(invalid_score.status_code, 400)

    def test_teacher_list_only_exposes_teachers_in_the_school(self):
        response = self.client.get(self.base + "teachers/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [str(self.teacher.pk)])

    def test_manager_cannot_assign_objects_from_another_tenant(self):
        outside_year = AcademicYear.objects.create(
            tenant=self.other_tenant,
            name="Other year",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        response = self.client.post(
            self.base + "classes/",
            {"academic_year": str(outside_year.pk), "name": "Grade 2"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class PromotionTests(AcademicFixture, TestCase):
    require_fixture_campus = True

    def setUp(self):
        super().setUp()
        self.next_year = AcademicYear.objects.create(
            tenant=self.tenant, name="2030/2031", start_date=date(2030, 1, 1), end_date=date(2030, 12, 31)
        )
        self.grade2 = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.next_year, name="Grade 2", campus=self.fixture_campus
        )
        self.grade1_again = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.next_year, name="Grade 1", campus=self.fixture_campus
        )
        self.url = self.base + "promotion/"

    def params(self):
        return {"from_year": str(self.year.pk), "to_year": str(self.next_year.pk)}

    def test_preview_suggests_next_class(self):
        data = self.client.get(self.url, self.params()).data
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["suggested_action"], "promote")
        self.assertEqual(data["rows"][0]["suggested_class"], str(self.grade2.pk))

    def test_preview_can_load_one_source_class(self):
        from .models import StudentEnrollment
        other_class = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Grade 3", campus=self.fixture_campus,
        )
        other_student = Student.objects.create(
            tenant=self.tenant, admission_number="NSA-002", first_name="Kofi", last_name="Mensah",
            campus=self.fixture_campus,
        )
        other_enrollment = StudentEnrollment.objects.create(
            tenant=self.tenant, student=other_student, school_class=other_class, campus=self.fixture_campus,
        )
        response = self.client.get(self.url, {**self.params(), "source_class": str(other_class.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["enrollment"] for row in response.data["rows"]], [str(other_enrollment.pk)])
        self.assertEqual(len(self.client.get(self.url, self.params()).data["rows"]), 2)

    def test_preview_rejects_invalid_wrong_year_or_other_school_class(self):
        foreign_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="Foreign", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        foreign_class = SchoolClass.objects.create(tenant=self.other_tenant, academic_year=foreign_year, name="Grade 1")
        for class_id in ("", "invalid", str(self.grade2.pk), str(foreign_class.pk)):
            with self.subTest(class_id=class_id):
                response = self.client.get(self.url, {**self.params(), "source_class": class_id})
                self.assertEqual(response.status_code, 400)
                self.assertIn("source_class", response.data)

    def test_apply_promotes_repeats_and_makes_year_current(self):
        response = self.client.post(
            self.url,
            {**self.params(), "make_current": True,
             "decisions": [{"enrollment": self.enrollment["id"], "action": "promote", "target_class": str(self.grade2.pk)}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["promote"], 1)
        self.student.refresh_from_db()
        self.assertEqual(self.student.grade_level, "Grade 2")
        self.assertTrue(AcademicYear.objects.get(pk=self.next_year.pk).is_current)
        self.assertTrue(self.client.get(self.url, self.params()).data["rows"][0]["already_enrolled"])

    def test_promotion_rejects_missing_or_conflicting_campus(self):
        from .models import StudentEnrollment
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        other = Campus.objects.create(tenant=self.tenant, name="Other", code="OTHER")
        for value in (None, other):
            self.grade2.campus = value
            self.grade2.save(update_fields=("campus",))
            response = self.client.post(self.url, {
                **self.params(),
                "decisions": [{"enrollment": str(enrollment.pk), "action": "promote", "target_class": str(self.grade2.pk)}],
            }, format="json")
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn("campus", str(response.data).lower())
            self.assertFalse(StudentEnrollment.objects.filter(student=self.student, school_class=self.grade2).exists())

    def test_graduate_and_validation(self):
        bad = self.client.post(self.url, {**self.params(), "decisions": [{"enrollment": self.enrollment["id"], "action": "promote"}]}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(self.url, {**self.params(), "decisions": [{"enrollment": self.enrollment["id"], "action": "graduate"}]}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, "graduated")

    def test_teacher_cannot_promote(self):
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.get(self.url, self.params()).status_code, 404)

    def test_promotion_rejects_duplicate_and_invalid_enrollments(self):
        decision = {"enrollment": self.enrollment["id"], "action": "promote", "target_class": str(self.grade2.pk)}
        duplicate = self.client.post(self.url, {**self.params(), "decisions": [decision, decision]}, format="json")
        self.assertEqual(duplicate.status_code, 400)
        invalid = self.client.post(self.url, {
            **self.params(), "decisions": [{"enrollment": "not-a-uuid", "action": "promote"}],
        }, format="json")
        self.assertEqual(invalid.status_code, 400)

    def test_promotion_rejects_previous_or_overlapping_year(self):
        previous = AcademicYear.objects.create(
            tenant=self.tenant, name="2025", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)
        )
        response = self.client.get(self.url, {"from_year": str(self.year.pk), "to_year": str(previous.pk)})
        self.assertEqual(response.status_code, 400)

    def test_graduation_does_not_change_student_already_enrolled_in_target(self):
        from .models import StudentEnrollment
        StudentEnrollment.objects.create(tenant=self.tenant, student=self.student, school_class=self.grade2)
        response = self.client.post(self.url, {
            **self.params(), "decisions": [{"enrollment": self.enrollment["id"], "action": "graduate"}],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["skipped"], 1)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.ACTIVE)

    def test_terminal_suggestion_requires_school_configuration_and_class_mapping(self):
        from .models import SchoolClassLevel
        self.school_class.name = "JHS 3"
        self.school_class.save(update_fields=("name",))
        preview = self.client.get(self.url, self.params()).data["rows"][0]
        self.assertNotEqual(preview["suggested_action"], "graduate")
        level = SchoolClassLevel.objects.create(
            tenant=self.tenant, display_name="JHS 3", is_terminal=True
        )
        self.school_class.class_level = level
        self.school_class.save(update_fields=("class_level",))
        preview = self.client.get(self.url, self.params()).data["rows"][0]
        self.assertEqual(preview["suggested_action"], "graduate")
        self.assertIsNone(preview["suggested_class"])

    def test_level_order_suggests_unambiguous_group_only(self):
        from .models import SchoolClassLevel
        first = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="First", sort_order=1)
        second = SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Second", sort_order=2)
        self.school_class.class_level = first
        self.school_class.save(update_fields=("class_level",))
        self.grade2.class_level = second
        self.grade2.save(update_fields=("class_level",))
        preview = self.client.get(self.url, self.params()).data["rows"][0]
        self.assertEqual(preview["suggested_class"], str(self.grade2.pk))
        SchoolClass.objects.create(tenant=self.tenant, academic_year=self.next_year, name="Second", section="A", class_level=second)
        preview = self.client.get(self.url, self.params()).data["rows"][0]
        self.assertEqual(preview["suggested_class"], str(self.grade2.pk))
        self.grade2.section = "B"
        self.grade2.save(update_fields=("section",))
        preview = self.client.get(self.url, self.params()).data["rows"][0]
        self.assertEqual(preview["suggested_action"], "")
