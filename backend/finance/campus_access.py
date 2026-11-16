from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from core.models import Campus, Membership

from .models import FinanceCampusGrant
from .views import APPROVERS, FinanceView


class CampusGrantRequest(serializers.Serializer):
    campuses = serializers.ListField(child=serializers.UUIDField(), allow_empty=True)

    def validate_campuses(self, values):
        if len(set(values)) != len(values):
            raise serializers.ValidationError("List each campus only once.")
        return values


class FinanceCampusGrantListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        memberships = Membership.objects.filter(
            tenant_id=tenant_id, role=Membership.Role.ACCOUNTANT
        ).select_related("user").prefetch_related("finance_campus_grants__campus")
        return Response([
            {
                "membership": str(member.pk), "name": member.user.full_name,
                "email": member.user.email, "status": member.status,
                "campuses": [
                    str(grant.campus_id) for grant in member.finance_campus_grants.all()
                    if grant.campus.tenant_id == tenant_id
                ],
            }
            for member in memberships.order_by("user__full_name", "user__email")
        ])


class FinanceCampusGrantDetailView(FinanceView):
    def put(self, request, tenant_id, membership_id):
        self.member(request, tenant_id, APPROVERS)
        serializer = CampusGrantRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = set(serializer.validated_data["campuses"])
        with transaction.atomic():
            member = Membership.objects.select_for_update().filter(
                pk=membership_id, tenant_id=tenant_id, role=Membership.Role.ACCOUNTANT,
            ).first()
            if member is None:
                raise NotFound()
            valid = set(Campus.objects.filter(tenant_id=tenant_id, pk__in=ids).values_list("pk", flat=True))
            if ids != valid:
                raise ValidationError({"campuses": "Select only campuses belonging to this school."})
            previous = set(FinanceCampusGrant.objects.filter(membership=member).values_list("campus_id", flat=True))
            FinanceCampusGrant.objects.filter(membership=member).exclude(campus_id__in=ids).delete()
            FinanceCampusGrant.objects.bulk_create([
                FinanceCampusGrant(membership=member, campus_id=campus_id) for campus_id in ids - previous
            ])
            if previous != ids:
                self.audit(request, tenant_id, "campus_access_updated", "Membership", member.pk,
                           previous=sorted(map(str, previous)), campuses=sorted(map(str, ids)))
        return Response({"membership": str(member.pk), "campuses": sorted(map(str, ids))})
