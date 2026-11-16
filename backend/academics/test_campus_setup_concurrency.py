from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from time import sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from core.enrollment import _audit
from core.models import AcademicYear, Campus, Membership, User
from core.tests import create_tenant
from .models import SchoolClass


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks.")
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CampusSetupConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = create_tenant(name="Test school", slug="campus-concurrency", email="test@example.test", phone="1")
        self.owner = User.objects.create_user(email="owner@example.test", full_name="Owner")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER)
        self.year = AcademicYear.objects.create(
            tenant=self.tenant, name="Year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        self.campus = Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
        self.campus_url = f"/api/tenants/{self.tenant.pk}/campuses/{self.campus.pk}/"
        self.class_url = f"/api/tenants/{self.tenant.pk}/academics/classes/"

    def request(self, method, url, payload, started=None):
        connections.close_all()
        try:
            client = APIClient()
            client.force_authenticate(user=self.owner)
            if started:
                started.set()
            return getattr(client, method)(url, payload, format="json").status_code
        finally:
            connections.close_all()

    def test_deactivation_first_revalidates_class_campus(self):
        locked, release, started = Event(), Event(), Event()
        def pause(*args, **kwargs):
            locked.set()
            if not release.wait(10):
                raise TimeoutError("Campus update was not released")
            return _audit(*args, **kwargs)
        with patch("core.campuses._audit", side_effect=pause), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.request, "patch", self.campus_url, {"status": "inactive"})
            try:
                self.assertTrue(locked.wait(10))
                second = pool.submit(self.request, "post", self.class_url, {
                    "academic_year": str(self.year.pk), "name": "Class", "campus": str(self.campus.pk),
                }, started)
                self.assertTrue(started.wait(10))
                sleep(0.2)
                self.assertFalse(second.done())
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 200)
            self.assertEqual(second.result(timeout=10), 400)
        self.assertFalse(SchoolClass.objects.filter(tenant=self.tenant).exists())

    def test_class_first_completes_before_deactivation(self):
        locked, release, started = Event(), Event(), Event()
        def pause(*args, **kwargs):
            locked.set()
            if not release.wait(10):
                raise TimeoutError("Class creation was not released")
            return _audit(*args, **kwargs)
        with patch("academics.views._audit", side_effect=pause), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.request, "post", self.class_url, {
                "academic_year": str(self.year.pk), "name": "Class", "campus": str(self.campus.pk),
            })
            try:
                self.assertTrue(locked.wait(10))
                second = pool.submit(self.request, "patch", self.campus_url, {"status": "inactive"}, started)
                self.assertTrue(started.wait(10))
                sleep(0.2)
                self.assertFalse(second.done())
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 201)
            self.assertEqual(second.result(timeout=10), 200)
        self.campus.refresh_from_db()
        self.assertEqual(self.campus.status, "inactive")
        self.assertTrue(SchoolClass.objects.filter(tenant=self.tenant, campus=self.campus).exists())
