"""One login for every company: the old per-business owner logins are switched off (kept for the audit log)."""
from django.db import migrations


def switch_off_business_logins(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(company__isnull=False, is_superuser=False).update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(switch_off_business_logins, migrations.RunPython.noop),
        migrations.RemoveField(model_name="user", name="company"),
        migrations.RemoveField(model_name="user", name="role"),
    ]
