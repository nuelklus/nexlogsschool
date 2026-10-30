from django.db import migrations, models


def create_platform_setup_state(apps, schema_editor):
    PlatformSetupState = apps.get_model("core", "PlatformSetupState")
    Tenant = apps.get_model("core", "Tenant")
    PlatformSetupState.objects.get_or_create(
        pk=1,
        defaults={"initial_setup_completed": Tenant.objects.exists()},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformSetupState",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("initial_setup_completed", models.BooleanField(default=False)),
            ],
        ),
        migrations.RunPython(
            create_platform_setup_state,
            migrations.RunPython.noop,
        ),
    ]
