from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0008_schoolclasslevel_is_terminal_and_more'),
        ('core', '0007_term_closure_headteacher'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentenrollment',
            name='ended_on',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='studentenrollment',
            name='status',
            field=models.CharField(choices=[('active', 'Active'), ('withdrawn', 'Withdrawn'), ('transferred', 'Transferred')], default='active', max_length=12),
        ),
        migrations.AddConstraint(
            model_name='studentenrollment',
            constraint=models.CheckConstraint(condition=models.Q(('ended_on__isnull', True), ('ended_on__gte', models.F('enrolled_on')), _connector='OR'), name='academic_enrollment_dates_ordered'),
        ),
    ]
