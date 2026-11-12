"""Working days of the month on every salary line, and salary payments (paid amount and date)."""
import calendar
from datetime import date

from django.db import migrations, models


def fill_working_days(apps, schema_editor):
    """Lines worked out before this change: days in the month minus Sundays (the default weekly off)."""
    PayrollLine = apps.get_model("payroll", "PayrollLine")
    for line in PayrollLine.objects.filter(working_days__isnull=True).select_related("run"):
        y, m = line.run.month.year, line.run.month.month
        dim = calendar.monthrange(y, m)[1]
        sundays = sum(1 for d in range(1, dim + 1) if date(y, m, d).weekday() == 6)
        PayrollLine.objects.filter(pk=line.pk).update(working_days=dim - sundays)


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="payrollline",
            name="working_days",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payrollline",
            name="paid_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="payrollline",
            name="paid_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(fill_working_days, migrations.RunPython.noop),
    ]
