"""
"Things to fix" (README §5.3 item 6). Shared with the Employees and Advances
lists: /employees/?fix=<key> and /advances/?fix=no_instalment use the same
querysets, so the count on the Home page always matches the list.
"""
import calendar
from datetime import date
from decimal import Decimal as D

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from advances.models import Advance
from core.formatting import add_months, month_start
from employees.models import Employee
from letters.models import LetterType
from payroll import engine, service
from statutory.rates import MissingRate, rates_for_month

EMPLOYEE_FIXES = ("no_uan", "no_esic", "no_appointment_letter", "below_50", "turning_58")
ADVANCE_FIXES = ("no_instalment",)


def _plus_months(day, count):
    m = add_months(month_start(day), count)
    return date(m.year, m.month, min(day.day, calendar.monthrange(m.year, m.month)[1]))


def _birthday(dob, years):
    try:
        return dob.replace(year=dob.year + years)
    except ValueError:  # born on 29 February
        return date(dob.year + years, 2, 28)


def fix_querysets(company, today=None):
    """{key: (label, queryset)} in display order. Employee keys give Employee querysets;
    'no_instalment' gives an Advance queryset."""
    today = today or timezone.localdate()
    month = month_start(today)
    active = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE)
    out = {}

    if company.pf_enabled:
        out["no_uan"] = (_("PF members without UAN"), active.filter(pf_member=True, uan=""))
    else:
        out["no_uan"] = (_("PF members without UAN"), active.none())

    ids = []
    if company.esi_enabled:
        try:
            month_rates = rates_for_month(month)
        except MissingRate:
            month_rates = None
        if month_rates is not None:
            for emp in active.filter(esic_ip_no="").prefetch_related("revisions"):
                if service.esi_covered_now(emp, company, month_rates, month):
                    ids.append(emp.pk)
    out["no_esic"] = (_("ESI-covered staff without IP number"), active.filter(pk__in=ids))

    out["no_appointment_letter"] = (
        _("Active staff with no appointment letter issued"),
        active.exclude(letters__letter_type=LetterType.APPOINTMENT),
    )

    try:
        min_pct = rates_for_month(month).rates.code_wage_min_pct
    except MissingRate:
        min_pct = D("50")
    below = []
    for emp in active.prefetch_related("revisions"):
        rev = emp.structure_on(month)
        if rev is None:
            continue
        s = rev.as_structure()
        if s.gross() > 0 and s.wage_part() < engine.pct(s.gross(), min_pct):
            below.append(emp.pk)
    out["below_50"] = (_("Salary structures where allowances are more than half of pay"),
                       active.filter(pk__in=below))

    limit = _plus_months(today, 3)
    turning = [e.pk for e in active.only("pk", "date_of_birth")
               if today <= _birthday(e.date_of_birth, 58) <= limit]
    out["turning_58"] = (_("Employees turning 58 in the next 3 months"), active.filter(pk__in=turning))

    out["no_instalment"] = (
        _("Open advances with no instalment set"),
        Advance.objects.filter(company=company, status=Advance.Status.OPEN, monthly_installment=0),
    )
    return out
