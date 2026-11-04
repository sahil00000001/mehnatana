"""
Statutory lookups. Rule (README §4.10): use the row in force on the LAST day
of the payroll month; if any StatutoryRate changed inside the month, warn
RATE_CHANGED_IN_MONTH.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from core.formatting import month_end, month_start, tax_year_of
from payroll import engine

from .models import PTSlab, StatutoryRate, TaxConfig

# StatutoryRate key -> engine.Rates attribute
RATE_FIELDS = {
    "PF_WAGE_CEILING": "pf_ceiling",
    "PF_ER_RATE": "pf_er_rate",
    "EPS_RATE": "eps_rate",
    "EDLI_RATE": "edli_rate",
    "PF_ADMIN_RATE": "pf_admin_rate",
    "PF_ADMIN_MIN": "pf_admin_min",
    "EPS_MAX_AGE": "eps_max_age",
    "ESI_EE_RATE": "esi_ee_rate",
    "ESI_ER_RATE": "esi_er_rate",
    "ESI_CEILING": "esi_ceiling",
    "ESI_CEILING_PWD": "esi_ceiling_pwd",
    "ESI_EE_EXEMPT_DAILY_WAGE": "esi_ee_exempt_daily",
    "ESI_ON_CODE_WAGES": "esi_on_code_wages",
    "CODE_WAGE_MIN_PCT": "code_wage_min_pct",
}
BOOL_KEYS = {"ESI_ON_CODE_WAGES"}
INT_KEYS = {"EPS_MAX_AGE"}


class MissingRate(Exception):
    """A statutory value needed for the calculation is not in the tables."""


def clean(value):
    """Decimal('25000.0000') -> Decimal('25000'), Decimal('8.3300') -> Decimal('8.33')."""
    if value is None:
        return None
    value = Decimal(value)
    if value == value.to_integral_value():
        return value.quantize(Decimal("1"))
    return value.normalize()


def values_on(on):
    """Every StatutoryRate key with the value in force on the date."""
    out = {}
    for row in StatutoryRate.objects.filter(effective_from__lte=on).order_by("key", "effective_from", "id"):
        out[row.key] = clean(row.value)
    return out


def value_on(key, on):
    row = StatutoryRate.objects.filter(key=key, effective_from__lte=on).order_by("-effective_from", "-id").first()
    return clean(row.value) if row else None


def changed_inside(start, end):
    """Keys whose new value starts after the first day and on or before the last day."""
    keys = StatutoryRate.objects.filter(effective_from__gt=start, effective_from__lte=end).values_list("key", flat=True)
    return sorted(set(keys))


def pt_slabs_on(on, state=None):
    """PT slabs in force on the date, as engine tuples. Per state, the latest effective set wins."""
    rows = PTSlab.objects.filter(effective_from__lte=on)
    if state:
        rows = rows.filter(state=state)
    rows = list(rows.order_by("state", "gender", "from_amount"))
    latest = {}
    for r in rows:
        latest[r.state] = max(latest.get(r.state, r.effective_from), r.effective_from)
    return [
        (r.state, r.gender, clean(r.from_amount), clean(r.to_amount), clean(r.amount), clean(r.february_amount))
        for r in rows if r.effective_from == latest[r.state]
    ]


def has_pt_slabs(state, on):
    return bool(pt_slabs_on(on, state))


def _cfg(row):
    def pairs(items):
        return [(clean(upper) if upper is not None else None, clean(rate)) for upper, rate in items]

    return {
        "std_deduction": clean(row.std_deduction),
        "slabs": pairs(row.slabs),
        "rebate_limit": clean(row.rebate_limit),
        "rebate_max": clean(row.rebate_max),
        "surcharge": pairs(row.surcharge),
        "cess": clean(row.cess_pct),
        "senior_exemption": clean(row.senior_exemption),
        "super_senior_exemption": clean(row.super_senior_exemption),
    }


def tax_config(tax_year):
    """(new regime cfg, old regime cfg, tax year actually used). Falls back to the latest earlier year."""
    rows = {}
    for row in TaxConfig.objects.filter(tax_year__lte=tax_year).order_by("-tax_year"):
        rows.setdefault(row.tax_year, {})[row.regime] = row
    for year in sorted(rows, reverse=True):
        if "NEW" in rows[year] and "OLD" in rows[year]:
            return _cfg(rows[year]["NEW"]), _cfg(rows[year]["OLD"]), year
    raise MissingRate(f"No income tax slabs are set for tax year {tax_year} or earlier.")


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


@dataclass
class MonthRates:
    rates: engine.Rates
    warnings: list = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)


def rates_for_month(month):
    start, end = month_start(month), month_end(month)
    values = values_on(end)
    missing = [k for k in RATE_FIELDS if k not in values]
    if missing:
        raise MissingRate("These statutory rates are not set: " + ", ".join(missing))
    kwargs = {}
    for key, attr in RATE_FIELDS.items():
        value = values[key]
        if key in BOOL_KEYS:
            value = value != 0
        elif key in INT_KEYS:
            value = int(value)
        kwargs[attr] = value
    pt = pt_slabs_on(end)
    year = tax_year_of(start)
    tax_new, tax_old, used_year = tax_config(year)
    warnings = []
    changed = changed_inside(start, end)
    if changed:
        warnings.append("RATE_CHANGED_IN_MONTH")
    if used_year != year:
        warnings.append("TAX_SLABS_NOT_UPDATED")
    rates = engine.Rates(pt_slabs=pt, tax_new=tax_new, tax_old=tax_old, **kwargs)
    snapshot = {
        "in_force_on": end.isoformat(),
        "values": {k: str(values[k]) for k in RATE_FIELDS},
        "changed_in_month": changed,
        "pt_slabs": _jsonable(pt),
        "tax_year": year,
        "tax_year_used": used_year,
        "tax_new": _jsonable(tax_new),
        "tax_old": _jsonable(tax_old),
    }
    return MonthRates(rates=rates, warnings=warnings, snapshot=snapshot)
