from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0007_schoolclass_class_level'),
        ('core', '0007_term_closure_headteacher'),
    ]

    operations = [
        migrations.AddField(
            model_name='schoolclasslevel',
            name='is_terminal',
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name='schoolclasslevel',
            constraint=models.UniqueConstraint(condition=models.Q(('is_terminal', True)), fields=('tenant',), name='one_terminal_level_per_school'),
        ),
        migrations.AddConstraint(
            model_name='schoolclasslevel',
            constraint=models.CheckConstraint(condition=models.Q(('is_terminal', False), ('is_active', True), _connector='OR'), name='terminal_level_must_be_active'),
        ),
    ]
