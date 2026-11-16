import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0006_seed_ghana_levels'),
    ]

    operations = [
        migrations.AddField(
            model_name='schoolclass',
            name='class_level',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='classes', to='academics.schoolclasslevel'),
        ),
    ]
