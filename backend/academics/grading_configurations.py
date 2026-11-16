from django.db import transaction
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership

from .grading import Component, GradeBand, GradingError, GradingPolicy, RoundingRule, validate_policy
from .models import GradingConfiguration, SchoolClassLevel
from .views import MANAGE_ROLES, READ_ROLES


class ComponentSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=80, trim_whitespace=True)
    maximum = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0)
    weight = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0, max_value=100)


class BandSerializer(serializers.Serializer):
    minimum = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0, max_value=100)
    grade = serializers.CharField(max_length=40, trim_whitespace=True)
    description = serializers.CharField(max_length=240, allow_blank=True)
    passed = serializers.BooleanField()


class PolicySerializer(serializers.Serializer):
    components = ComponentSerializer(many=True, allow_empty=False, max_length=50)
    bands = BandSerializer(many=True, allow_empty=False, max_length=100)
    decimal_places = serializers.IntegerField(min_value=0, max_value=6)
    rounding = serializers.ChoiceField(choices=[rule.value for rule in RoundingRule])
    allow_decimal_scores = serializers.BooleanField()
    exclude_not_applicable = serializers.BooleanField()

    def validate(self, attrs):
        policy = GradingPolicy(
            components=tuple(Component(**item) for item in attrs["components"]),
            bands=tuple(GradeBand(**item) for item in attrs["bands"]),
            decimal_places=attrs["decimal_places"],
            rounding=RoundingRule(attrs["rounding"]),
            allow_decimal_scores=attrs["allow_decimal_scores"],
            exclude_not_applicable=attrs["exclude_not_applicable"],
        )
        try:
            validate_policy(policy)
        except GradingError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return attrs


class GradingConfigurationSerializer(serializers.ModelSerializer):
    configuration = PolicySerializer()
    class_level = serializers.PrimaryKeyRelatedField(
        queryset=SchoolClassLevel.objects.none(), allow_null=True, required=False
    )

    class Meta:
        model = GradingConfiguration
        fields = (
            "id", "family", "version", "name", "class_level", "examination_type",
            "configuration", "created_at",
        )
        read_only_fields = ("id", "family", "version", "created_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["class_level"].queryset = SchoolClassLevel.objects.filter(
            tenant_id=self.context["tenant_id"], is_active=True
        )

    def create(self, validated_data):
        policy = validated_data.pop("configuration")
        # DRF's representation converts decimals to strings, keeping JSON values exact.
        validated_data["configuration"] = PolicySerializer(policy).data
        return super().create(validated_data)


def _require_member(request, tenant_id, roles):
    if _membership(request.user, tenant_id, roles) is None:
        raise NotFound()


class GradingConfigurationListView(APIView):
    def get(self, request, tenant_id):
        _require_member(request, tenant_id, READ_ROLES)
        configs = GradingConfiguration.objects.filter(tenant_id=tenant_id)
        return Response(GradingConfigurationSerializer(
            configs, many=True, context={"tenant_id": tenant_id}
        ).data)

    def post(self, request, tenant_id):
        _require_member(request, tenant_id, MANAGE_ROLES)
        serializer = GradingConfigurationSerializer(data=request.data, context={"tenant_id": tenant_id})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            config = serializer.save(tenant_id=tenant_id, created_by=request.user)
            _audit(request, tenant_id, "academic.grading_configuration.created", "GradingConfiguration", config.pk)
        return Response(GradingConfigurationSerializer(config, context={"tenant_id": tenant_id}).data,
                        status=status.HTTP_201_CREATED)


class GradingConfigurationRevisionView(APIView):
    def post(self, request, tenant_id, config_id):
        _require_member(request, tenant_id, MANAGE_ROLES)
        serializer = GradingConfigurationSerializer(data=request.data, context={"tenant_id": tenant_id})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            previous = GradingConfiguration.objects.select_for_update().filter(
                pk=config_id, tenant_id=tenant_id
            ).first()
            if previous is None:
                raise NotFound()
            if GradingConfiguration.objects.filter(
                tenant_id=tenant_id, family=previous.family, version__gt=previous.version
            ).exists():
                raise ValidationError({"detail": "This version is no longer current. Reload before creating a revision."})
            config = serializer.save(
                tenant_id=tenant_id, created_by=request.user,
                family=previous.family, version=previous.version + 1,
            )
            _audit(request, tenant_id, "academic.grading_configuration.revised", "GradingConfiguration", config.pk,
                   {"previous_id": str(previous.pk)})
        return Response(GradingConfigurationSerializer(config, context={"tenant_id": tenant_id}).data,
                        status=status.HTTP_201_CREATED)
