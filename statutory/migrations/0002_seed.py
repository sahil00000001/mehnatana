"""Statutory seed data, README §9 (law as of 4 November 2026)."""
from datetime import date
from decimal import Decimal

from django.db import migrations

# NOTE: README §8.4 says the ₹25,000 PF ceiling applies "for months from October 2026",
# while §9.1 dates it 17-09-2026 and §4.10 uses the rate in force on the last day of the
# month. Following §9.1 + §4.10, September 2026 already uses ₹25,000 and carries the
# RATE_CHANGED_IN_MONTH warning (a split-month calculation is not in v1).
RATES = [
    ("PF_WAGE_CEILING", "15000", date(2014, 9, 1), ""),
    ("PF_WAGE_CEILING", "25000", date(2026, 9, 17), "S.O. 5109(E) dated 17-09-2026"),
    ("PF_ER_RATE", "12.00", date(2014, 9, 1), "EPS + EPF"),
    ("EPS_RATE", "8.33", date(2014, 9, 1), ""),
    ("EDLI_RATE", "0.50", date(2014, 9, 1), ""),
    ("PF_ADMIN_RATE", "0.50", date(2018, 6, 1), ""),
    ("PF_ADMIN_MIN", "500", date(2018, 6, 1), "per establishment per month"),
    ("EPS_MAX_AGE", "58", date(2014, 9, 1), ""),
    ("ESI_EE_RATE", "0.75", date(2019, 7, 1), ""),
    ("ESI_ER_RATE", "3.25", date(2019, 7, 1), ""),
    ("ESI_CEILING", "21000", date(2017, 1, 1), ""),
    ("ESI_CEILING_PWD", "25000", date(2017, 1, 1), ""),
    ("ESI_EE_EXEMPT_DAILY_WAGE", "176", date(2019, 7, 1), "value confirmed for 2026"),
    ("ESI_ON_CODE_WAGES", "0", date(2017, 1, 1), "1 = use code wages"),
    ("ESI_ON_CODE_WAGES", "1", date(2025, 11, 21), "ESIC circular, Dec 2025 payroll onward"),
    ("CODE_WAGE_MIN_PCT", "0", date(2014, 9, 1), ""),
    ("CODE_WAGE_MIN_PCT", "50", date(2025, 11, 21), "Labour Codes in force"),
]

# state, gender, from, to (None = and above), monthly, February, effective from
PT_SLABS = [
    ("KA", "ANY", "25000", None, "200", "300", date(2025, 4, 1)),
    ("MH", "M", "7501", "10000", "175", "175", date(2023, 4, 1)),
    ("MH", "M", "10001", None, "200", "300", date(2023, 4, 1)),
    ("MH", "O", "7501", "10000", "175", "175", date(2023, 4, 1)),
    ("MH", "O", "10001", None, "200", "300", date(2023, 4, 1)),
    ("MH", "F", "25001", None, "200", "300", date(2023, 4, 1)),
]

NEW_REGIME = dict(
    regime="NEW", std_deduction=Decimal("75000"), rebate_limit=Decimal("1200000"), rebate_max=Decimal("60000"),
    cess_pct=Decimal("4"),
    slabs=[[400000, 0], [800000, 5], [1200000, 10], [1600000, 15], [2000000, 20], [2400000, 25], [None, 30]],
    surcharge=[[5000000, 10], [10000000, 15], [20000000, 25]],
    senior_exemption=None, super_senior_exemption=None,
)
OLD_REGIME = dict(
    regime="OLD", std_deduction=Decimal("50000"), rebate_limit=Decimal("500000"), rebate_max=Decimal("12500"),
    cess_pct=Decimal("4"),
    slabs=[[250000, 0], [500000, 5], [1000000, 20], [None, 30]],
    surcharge=[[5000000, 10], [10000000, 15], [20000000, 25], [50000000, 37]],
    senior_exemption=Decimal("300000"), super_senior_exemption=Decimal("500000"),
)

# item, label, day of next month, March exception (day, month), state, note
DUE_DATES = [
    ("SALARY", "Pay {month} salary", 7, None, None, "", "Code on Wages: before the 7th of next month"),
    ("TDS", "Deposit TDS for {month}", 7, 30, 4, "", "For March: 30 April"),
    ("PF", "Deposit PF for {month}", 15, None, None, "", "EPFO ECR and challan"),
    ("ESI", "Deposit ESI for {month}", 15, None, None, "", "ESIC monthly contribution"),
    ("PT_KA", "Pay professional tax for {month}", 20, None, None, "KA", "Karnataka (verify)"),
]


def seed(apps, schema_editor):
    StatutoryRate = apps.get_model("statutory", "StatutoryRate")
    PTSlab = apps.get_model("statutory", "PTSlab")
    TaxConfig = apps.get_model("statutory", "TaxConfig")
    DueDate = apps.get_model("statutory", "DueDate")

    for key, value, effective, note in RATES:
        StatutoryRate.objects.update_or_create(key=key, effective_from=effective,
                                               defaults={"value": Decimal(value), "note": note})
    for state, gender, lo, hi, amount, feb, effective in PT_SLABS:
        PTSlab.objects.get_or_create(
            state=state, gender=gender, from_amount=Decimal(lo), effective_from=effective,
            defaults={"to_amount": Decimal(hi) if hi else None, "amount": Decimal(amount),
                      "february_amount": Decimal(feb)},
        )
    for year in (2025, 2026):
        for cfg in (NEW_REGIME, OLD_REGIME):
            values = dict(cfg)
            regime = values.pop("regime")
            TaxConfig.objects.update_or_create(tax_year=year, regime=regime, defaults=values)
    for item, label, day, m_day, m_month, state, note in DUE_DATES:
        DueDate.objects.update_or_create(item=item, defaults={
            "label": label, "day_of_next_month": day, "march_due_day": m_day, "march_due_month": m_month,
            "applies_to_state": state, "note": note,
        })


class Migration(migrations.Migration):
    dependencies = [("statutory", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
