from django.db import migrations


GHANA_LEVELS = [
    ("gh-creche", "Creche", "early_years"),
    ("gh-nursery-1", "Nursery 1", "early_years"),
    ("gh-nursery-2", "Nursery 2", "early_years"),
    ("gh-kg-1", "KG 1", "kindergarten"),
    ("gh-kg-2", "KG 2", "kindergarten"),
    ("gh-basic-1", "Basic 1", "primary"),
    ("gh-basic-2", "Basic 2", "primary"),
    ("gh-basic-3", "Basic 3", "primary"),
    ("gh-basic-4", "Basic 4", "primary"),
    ("gh-basic-5", "Basic 5", "primary"),
    ("gh-basic-6", "Basic 6", "primary"),
    ("gh-jhs-1", "JHS 1", "junior_high"),
    ("gh-jhs-2", "JHS 2", "junior_high"),
    ("gh-jhs-3", "JHS 3", "junior_high"),
    ("gh-shs-1", "SHS 1", "senior_high"),
    ("gh-shs-2", "SHS 2", "senior_high"),
    ("gh-shs-3", "SHS 3", "senior_high"),
]


def seed_ghana_levels(apps, schema_editor):
    catalogue = apps.get_model("academics", "ClassLevelCatalogue")
    for order, (code, name, stage) in enumerate(GHANA_LEVELS, start=1):
        catalogue.objects.using(schema_editor.connection.alias).get_or_create(
            code=code,
            defaults={
                "name": name,
                "education_stage": stage,
                "country_code": "GH",
                "sort_order": order,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("academics", "0005_classlevelcatalogue_schoolclasslevel")]
    operations = [migrations.RunPython(seed_ghana_levels, migrations.RunPython.noop)]
