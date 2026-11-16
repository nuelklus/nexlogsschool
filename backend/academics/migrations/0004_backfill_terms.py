from django.db import migrations


# Schools enter their own terms and dates, so nothing is generated. The file stays so databases that
# applied an earlier version of this migration remain consistent.
class Migration(migrations.Migration):
    dependencies = [("academics", "0003_term_closure_headteacher")]
    operations = []
