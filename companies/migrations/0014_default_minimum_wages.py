from datetime import date

from django.db import migrations


STATES = (
    "AN", "AP", "AR", "AS", "BR", "CH", "CG", "DH", "DL", "GA", "GJ", "HR", "HP", "JK", "JH", "KA",
    "KL", "LA", "LD", "MP", "MH", "MN", "ML", "MZ", "NL", "OD", "PY", "PB", "RJ", "SK", "TN", "TG",
    "TR", "UP", "UK", "WB",
)


def add_default_minimum_wages(apps, schema_editor):
    MinimumWage = apps.get_model("companies", "MinimumWage")
    database = schema_editor.connection.alias
    for state in STATES:
        MinimumWage.objects.using(database).get_or_create(
            state=state,
            effective_from=date(2026, 10, 1),
            defaults={
                "unskilled": 20358,
                "semi_skilled": 22568,
                "skilled": 22411,
                "highly_skilled": None,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0013_alter_company_amount_paid_mode_and_more"),
    ]

    operations = [
        migrations.RunPython(add_default_minimum_wages, migrations.RunPython.noop),
    ]
