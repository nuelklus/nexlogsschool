from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APIClient

from core.models import Campus, Membership
from .models import EnrollmentProvenanceAttestation, EnrollmentProvenanceRevocation, StudentEnrollment
from .tests import AcademicFixture


@skipUnlessDBFeature("has_select_for_update")
class ProvenanceConcurrencyTests(AcademicFixture, TransactionTestCase):
    def test_simultaneous_different_attestations_create_only_one_version(self):
        if connection.vendor != "postgresql":
            self.skipTest("Concurrency verification requires PostgreSQL.")
        admin = self.make_member(self.tenant, "provenance-admin@northstar.example", Membership.Role.SCHOOL_ADMIN)
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        enrollment.status = StudentEnrollment.Status.WITHDRAWN
        enrollment.ended_on = date(2026, 3, 19)
        enrollment.save(update_fields=("status", "ended_on"))
        url = self.base + "enrollment-provenance/"
        report = self.client.post(url + "report/", {
            "term": str(self.term.pk), "school_class": str(self.school_class.pk),
        }, format="json")
        self.assertEqual(report.status_code, 200, report.data)
        row = report.data["students"][0]
        self.assertEqual(row["blocking"], [])
        payload = {
            "term": str(self.term.pk), "enrollment": str(enrollment.pk),
            "previous_attestation": None,
            "fingerprint": row["fingerprint"], "segment_start": row["segment_start"],
            "segment_end": row["segment_end"], "campus": None, "no_campus": True,
            "subjects": [str(self.subject.pk)], "reason": "Verified register", "evidence": "Register page 12",
        }
        gate = Barrier(2, timeout=15)

        def submit(args):
            user, selected_campus = args
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                gate.wait()
                response = client.post(url + "attest/", {**payload, **selected_campus}, format="json")
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            campus = Campus.objects.create(tenant=self.tenant, name="Campus", code="C")
            responses = list(pool.map(submit, (
                (self.owner, {}), (admin, {"campus": str(campus.pk), "no_campus": False}),
            )))
        self.assertEqual(sorted(responses), [201, 400])
        self.assertEqual(EnrollmentProvenanceAttestation.objects.filter(enrollment=enrollment).count(), 1)

    def test_simultaneous_revocations_record_one_event(self):
        if connection.vendor != "postgresql":
            self.skipTest("Concurrency verification requires PostgreSQL.")
        admin = self.make_member(self.tenant, "revoke-admin@northstar.example", Membership.Role.SCHOOL_ADMIN)
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        enrollment.status = StudentEnrollment.Status.WITHDRAWN
        enrollment.ended_on = date(2026, 3, 19)
        enrollment.save(update_fields=("status", "ended_on"))
        url = self.base + "enrollment-provenance/"
        row = self.client.post(url + "report/", {
            "term": str(self.term.pk), "school_class": str(self.school_class.pk),
        }, format="json").data["students"][0]
        response = self.client.post(url + "attest/", {
            "term": str(self.term.pk), "enrollment": str(enrollment.pk), "previous_attestation": None,
            "fingerprint": row["fingerprint"], "segment_start": row["segment_start"],
            "segment_end": row["segment_end"], "campus": None, "no_campus": True,
            "subjects": [str(self.subject.pk)], "reason": "Verified", "evidence": "Register",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        attestation = response.data["id"]
        gate = Barrier(2, timeout=15)

        def revoke(user):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                gate.wait()
                return client.post(url + f"{attestation}/revoke/", {"reason": "Correction"}, format="json").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(revoke, (self.owner, admin)))
        self.assertEqual(sorted(responses), [201, 400])
        self.assertEqual(EnrollmentProvenanceRevocation.objects.filter(attestation_id=attestation).count(), 1)
