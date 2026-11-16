"""Actual admission and finance year endpoints on a disposable PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from academics.models import SchoolClass, StudentEnrollment
from core.models import AcademicYear, AdmissionSequence, Membership, Student, User
from core.tests import create_tenant


class AdmissionFinanceRaceTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or connection.vendor != "postgresql"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise SkipTest("Requires the disposable PostgreSQL fixture runner.")
        super().setUpClass()

    def setUp(self):
        self.tenant = create_tenant(
            name="Race School", slug="race-school", email="race@example.com", phone="1"
        )
        self.owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER)
        self.year = AcademicYear.objects.create(
            tenant=self.tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        self.school_class = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Class"
        )

    @staticmethod
    def admission_year_lock(sql):
        text = sql.upper()
        return 'FROM "CORE_ACADEMICYEAR"' in text and "FOR UPDATE" in text

    @staticmethod
    def finance_year_update(sql):
        return sql.upper().startswith('UPDATE "CORE_ACADEMICYEAR"')

    @staticmethod
    def admission_tenant_lock(sql):
        text = sql.upper()
        return 'FROM "CORE_TENANT"' in text and "FOR UPDATE" in text

    def race(self, first_kind, second_kind, *, separate_classes=None):
        entered, release = Event(), Event()
        pids = Queue()
        traces = {"first": [], "second": []}

        def worker(kind, label, hold):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((label, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    traces[label].append(sql)
                    result = execute(sql, params, many, context)
                    if separate_classes is not None:
                        match = self.admission_tenant_lock(sql)
                    else:
                        match = self.admission_year_lock(sql) if kind == "admission" else self.finance_year_update(sql)
                    if hold and match and not entered.is_set():
                        entered.set()
                        if not release.wait(10):
                            raise AssertionError("First transaction was not released")
                    return result

                client = APIClient()
                client.force_authenticate(user=self.owner)
                try:
                    with db.execute_wrapper(trace):
                        if kind == "admission":
                            year, school_class = (
                                separate_classes[label] if separate_classes is not None
                                else (self.year, self.school_class)
                            )
                            response = client.post(
                                f"/api/tenants/{self.tenant.pk}/students/",
                                {"first_name": label, "last_name": "Student", "enrolled_on": "2026-06-01",
                                 "initial_academic_year": str(year.pk),
                                 "initial_school_class": str(school_class.pk)},
                                format="json",
                            )
                        else:
                            response = client.post(
                                f"/api/tenants/{self.tenant.pk}/finance/academic-years/",
                                {"name": "Next", "start_date": "2027-01-01",
                                 "end_date": "2027-12-31", "is_current": True},
                                format="json",
                            )
                    return response.status_code, dict(response.data), None
                except DatabaseError as exc:
                    return None, str(exc), getattr(exc.__cause__, "sqlstate", None)
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, first_kind, "first", True)
            try:
                label, first_pid = pids.get(timeout=5)
                self.assertEqual(label, "first")
                self.assertTrue(entered.wait(8), "First endpoint did not reach its target statement")
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT mode FROM pg_locks WHERE pid = %s AND granted "
                        "AND relation = 'core_academicyear'::regclass",
                        [first_pid],
                    )
                    modes = {row[0] for row in cursor.fetchall()}
                self.assertIn("RowShareLock" if first_kind == "admission" else "RowExclusiveLock", modes)
                if separate_classes is not None:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT mode FROM pg_locks WHERE pid = %s AND granted "
                            "AND relation = 'core_tenant'::regclass",
                            [first_pid],
                        )
                        self.assertIn("RowShareLock", {row[0] for row in cursor.fetchall()})
                second = pool.submit(worker, second_kind, "second", False)
                label, second_pid = pids.get(timeout=5)
                self.assertEqual(label, "second")
                self.assertNotEqual(first_pid, second_pid)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_blocking_pids(%s), wait_event_type, query "
                            "FROM pg_stat_activity WHERE pid = %s",
                            [second_pid, second_pid],
                        )
                        state = cursor.fetchone()
                    if state and first_pid in state[0]:
                        break
                    sleep(0.02)
                else:
                    self.fail("PostgreSQL did not report the expected blocking relationship")
                self.assertEqual(state[1], "Lock")
                expected_query = self.admission_tenant_lock if separate_classes is not None else (
                    self.admission_year_lock if second_kind == "admission" else self.finance_year_update
                )
                self.assertTrue(expected_query(state[2]), state[2])
            finally:
                release.set()
            outcomes = first.result(timeout=15), second.result(timeout=15)
        return outcomes, traces

    def assert_admission_trace(self, trace):
        statements = [sql.upper() for sql in trace]
        def find(fragment, start=0):
            return next(i for i, sql in enumerate(statements[start:], start) if fragment in sql)
        year = find('FROM "CORE_ACADEMICYEAR"')
        while "FOR UPDATE" not in statements[year]:
            year = find('FROM "CORE_ACADEMICYEAR"', year + 1)
        school_class = find('FROM "ACADEMICS_SCHOOLCLASS"', year + 1)
        self.assertIn("FOR UPDATE", statements[school_class])
        tenant = find('FROM "CORE_TENANT"', school_class + 1)
        self.assertIn("FOR UPDATE", statements[tenant])
        student = find('INSERT INTO "CORE_STUDENT"', tenant + 1)
        enrollment = find('INSERT INTO "ACADEMICS_STUDENTENROLLMENT"', student + 1)
        self.assertLess(student, enrollment)

    def assert_finance_trace(self, trace):
        statements = [sql.upper() for sql in trace]
        update = next(i for i, sql in enumerate(statements) if sql.startswith('UPDATE "CORE_ACADEMICYEAR"'))
        insert = next(i for i, sql in enumerate(statements) if sql.startswith('INSERT INTO "CORE_ACADEMICYEAR"'))
        self.assertLess(update, insert)

    def check_year_race(self, first_kind, second_kind):
        outcomes, traces = self.race(first_kind, second_kind)
        self.assertEqual([outcome[0] for outcome in outcomes], [201, 201], outcomes)
        self.assertEqual([outcome[2] for outcome in outcomes], [None, None], outcomes)
        for label, kind in (("first", first_kind), ("second", second_kind)):
            (self.assert_admission_trace if kind == "admission" else self.assert_finance_trace)(traces[label])
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(StudentEnrollment.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(AdmissionSequence.objects.get(tenant=self.tenant, period=2026).last_number, 1)
        self.assertEqual(AcademicYear.objects.filter(tenant=self.tenant, is_current=True).get().name, "Next")
        self.year.refresh_from_db()
        self.assertFalse(self.year.is_current)

    def test_admission_blocks_finance_year_creation(self):
        self.check_year_race("admission", "finance")

    def test_finance_year_creation_blocks_admission(self):
        self.check_year_race("finance", "admission")

    def test_admissions_serialize_and_reserve_unique_numbers(self):
        outcomes, traces = self.race("admission", "admission")
        self.assertEqual([outcome[0] for outcome in outcomes], [201, 201], outcomes)
        self.assertEqual([outcome[2] for outcome in outcomes], [None, None], outcomes)
        self.assert_admission_trace(traces["first"])
        self.assert_admission_trace(traces["second"])
        numbers = {outcome[1]["admission_number"] for outcome in outcomes}
        self.assertEqual(len(numbers), 2)
        self.assertEqual(set(Student.objects.filter(tenant=self.tenant).values_list("admission_number", flat=True)), numbers)
        self.assertEqual(StudentEnrollment.objects.filter(tenant=self.tenant).count(), 2)
        self.assertEqual(AdmissionSequence.objects.get(tenant=self.tenant, period=2026).last_number, 2)

    def test_admissions_in_different_years_wait_on_numbering_tenant(self):
        other_year = AcademicYear.objects.create(
            tenant=self.tenant, name="Other", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        other_class = SchoolClass.objects.create(tenant=self.tenant, academic_year=other_year, name="Other")
        outcomes, traces = self.race(
            "admission", "admission",
            separate_classes={"first": (self.year, self.school_class), "second": (other_year, other_class)},
        )
        self.assertEqual([outcome[0] for outcome in outcomes], [201, 201], outcomes)
        self.assertEqual([outcome[2] for outcome in outcomes], [None, None], outcomes)
        for trace in traces.values():
            self.assert_admission_trace(trace)
        numbers = {outcome[1]["admission_number"] for outcome in outcomes}
        self.assertEqual(len(numbers), 2)
        self.assertEqual(StudentEnrollment.objects.filter(tenant=self.tenant).count(), 2)
        self.assertEqual(AdmissionSequence.objects.get(tenant=self.tenant, period=2026).last_number, 2)

    def test_test_schema_foreign_keys_and_uniqueness(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.relname, p.relname, k.condeferrable, k.condeferred "
                "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
                "JOIN pg_class p ON p.oid=k.confrelid WHERE k.contype='f' "
                "AND c.relname IN ('core_academicyear', 'core_student', 'core_admissionsequence', "
                "'academics_schoolclass', 'academics_studentenrollment')"
            )
            fks = cursor.fetchall()
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                "AND tablename IN ('core_academicyear', 'core_student', 'core_admissionsequence', "
                "'academics_studentenrollment') AND indexdef LIKE '%%UNIQUE%%'"
            )
            indexes = {row[0] for row in cursor.fetchall()}
        self.assertTrue(fks)
        self.assertTrue(all(deferrable and deferred for _, _, deferrable, deferred in fks), fks)
        self.assertTrue({
            ("core_academicyear", "core_tenant"),
            ("core_student", "core_tenant"),
            ("core_admissionsequence", "core_tenant"),
            ("academics_schoolclass", "core_academicyear"),
            ("academics_studentenrollment", "core_student"),
            ("academics_studentenrollment", "academics_schoolclass"),
        }.issubset({(table, parent) for table, parent, _, _ in fks}))
        self.assertTrue({
            "one_current_academic_year_per_tenant", "unique_academic_year_name_per_tenant",
            "unique_admission_number_per_tenant", "unique_admission_sequence_per_period",
            "unique_student_class_enrollment",
        }.issubset(indexes), indexes)
