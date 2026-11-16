import django.db.models.deletion
import django.db.models.functions.text
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0004_backfill_terms'),
        ('core', '0007_term_closure_headteacher'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClassLevelCatalogue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=40, unique=True)),
                ('name', models.CharField(max_length=80)),
                ('education_stage', models.CharField(max_length=40)),
                ('country_code', models.CharField(max_length=2)),
                ('sort_order', models.PositiveIntegerField()),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ('country_code', 'sort_order', 'code'),
            },
        ),
        migrations.CreateModel(
            name='SchoolClassLevel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('display_name', models.CharField(max_length=80)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('catalogue_level', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='school_levels', to='academics.classlevelcatalogue')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='class_levels', to='core.tenant')),
            ],
            options={
                'ordering': ('sort_order', 'display_name', 'id'),
                'constraints': [models.UniqueConstraint(condition=models.Q(('catalogue_level__isnull', False)), fields=('tenant', 'catalogue_level'), name='one_catalogue_level_per_school'), models.UniqueConstraint(models.F('tenant'), django.db.models.functions.text.Lower('display_name'), name='unique_school_level_name_ci')],
            },
        ),
    ]
