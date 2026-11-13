"""
Salary month service: database -> engine -> PayrollLine.

engine.py is pure arithmetic. This module loads the employees, salary
structures, inputs, year-to-date figures and statutory rates for a month,
runs the engine, stores frozen lines, and handles finalise / reopen.
"""
from dataclasses import dataclass, fields as dc_fields, replace
from datetime import date
from decimal import Decimal as D
from typing import Optional

from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from advances.models import Advance, AdvanceRecovery
from companies.models import Company, MinimumWage
from core.audit import audit
from core.formatting import (
    add_months, fmt_date, indian_format, month_end, month_label, month_start, plain_number, tax_year_of,
)
from employees.models import Employee, OpeningBalance, SalaryRevision
from statutory.rates import MissingRate, rates_for_month

from . import checks, engine
from .models import PayrollLine, PayrollRun

ZERO = D("0")
DAY_KEYS = ("present", "paid_leave", "week_off_holiday", "lop")
NUMBER_KEYS = ("present", "paid_leave", "week_off_holiday", "lop", "ot_hours", "ot_amount", "incentive",
               "increment", "advance", "other_deduction", "tds_override")
MONEY_KEYS = ("ot_amount", "incentive", "increment", "advance", "other_deduction", "tds_override")
MAX_AMOUNT = D("9999999")  # same limit as the forms (core.forms.MAX_AMOUNT)
HOURS_IN_A_MONTH = 744  # 31 x 24: always refused above this; the usual limit is a setting (payroll/checks.py)
OT_HOURS_NOTE = 100  # more than this gets a note to check


class PayrollError(Exception):
    """A problem explained in plain words for the owner."""


# ---------------------------------------------------------------- inputs of one row
@dataclass
class RowInput:
    present: D = ZERO
    paid_leave: D = ZERO
    week_off_holiday: D = ZERO
    lop: Optional[D] = None
    ot_hours: D = ZERO
    ot_amount: Optional[D] = None
    incentive: D = ZERO
    increment: D = ZERO
    advance: D = ZERO
    other_deduction: D = ZERO
    other_reason: str = ""
    tds_override: Optional[D] = None
    remarks: str = ""
    days_given: Optional[D] = None  # take-home method: paid days as written, before PayCore reduced them
    take_home: Optional[D] = None  # take-home method: the amount to pay in hand this month (None: by days)

    def to_engine(self):
        return engine.MonthInput(
            present=self.present, paid_leave=self.paid_leave, week_off_holiday=self.week_off_holiday,
            lop=self.lop, ot_hours=self.ot_hours, ot_amount=self.ot_amount, incentive=self.incentive,
            increment=self.increment, advance=self.advance, other_deduction=self.other_deduction,
            tds_override=self.tds_override,
        )

    @classmethod
    def from_line(cls, line):
        def dec(v):
            return None if v is None else D(v)

        return cls(
            present=D(line.present), paid_leave=D(line.paid_leave), week_off_holiday=D(line.week_off_holiday),
            lop=dec(line.lop_days), ot_hours=D(line.ot_hours), ot_amount=dec(line.ot_amount_override),
            incentive=D(line.incentive), increment=D(line.increment), advance=D(line.advance_deduction),
            other_deduction=D(line.other_deduction), other_reason=line.other_deduction_reason,
            tds_override=dec(line.tds_override), remarks=line.remarks, days_given=dec(line.days_given),
            take_home=dec(line.take_home),
        )

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}


# ---------------------------------------------------------------- who is paid this month
def employees_for_month(company, month):
    """Joined on or before month end, not left before month start and not made inactive from this month or
    earlier."""
    start, end = month_start(month), month_end(month)
    return (
        Employee.objects.filter(company=company, date_of_joining__lte=end)
        .filter(Q(date_of_leaving__isnull=True) | Q(date_of_leaving__gte=start))
        .exclude(status=Employee.Status.INACTIVE, inactive_from__lte=start)
        .select_related("designation", "department")
        .prefetch_related("revisions")
        .order_by("emp_code")
    )


def days_employed(emp, month):
    start, end = month_start(month), month_end(month)
    first = max(start, emp.date_of_joining)
    last = min(end, emp.date_of_leaving or end)
    return max(0, (last - first).days + 1)


def esi_period_start(month):
    """Contribution periods: 1 April - 30 September and 1 October - 31 March."""
    if 4 <= month.month <= 9:
        return date(month.year, 4, 1)
    if month.month >= 10:
        return date(month.year, 10, 1)
    return date(month.year - 1, 10, 1)


# ---------------------------------------------------------------- database -> engine objects
def engine_settings(company):
    return engine.Settings(
        salary_divisor=company.salary_divisor,
        pf_enabled=company.pf_enabled,
        pf_ee_rate=D(company.pf_employee_rate),
        pf_restrict_to_ceiling=company.pf_restrict_to_ceiling,
        esi_enabled=company.esi_enabled,
        pt_enabled=company.pt_enabled,
        pt_state=company.pt_state or company.state,
        tds_enabled=company.tds_enabled,
        ot_multiplier=D(company.ot_multiplier),
        ot_days_divisor=D(company.ot_days_divisor),
        ot_hours_per_day=D(company.ot_hours_per_day),
        weekly_off=company.weekly_off,
    )


def settings_snapshot(settings):
    return {f.name: str(getattr(settings, f.name)) for f in dc_fields(settings)}


def engine_employee(emp):
    return engine.Employee(
        code=emp.emp_code, name=emp.full_name, gender=emp.gender, dob=emp.date_of_birth,
        doj=emp.date_of_joining, dol=emp.date_of_leaving, pf_member=emp.pf_member, eps_member=emp.eps_member,
        esi_override=emp.esi_override, is_pwd=emp.is_pwd, pt_exempt=emp.pt_exempt, tax_regime=emp.tax_regime,
        old_regime_deductions=D(emp.old_regime_deductions_annual),
    )


def structure_for_month(emp, month):
    rev = emp.structure_on(month_start(month))
    if rev is None:
        raise PayrollError(_("%(code)s %(name)s has no salary structure. Use Revise salary on the employee page.")
                           % {"code": emp.emp_code, "name": emp.full_name})
    return rev.as_structure()


def esi_structure(emp, month, increment=ZERO):
    """Structure that decides ESI coverage: the one in force at the start of the
    contribution period, or at joining if that is later. An increment entered in
    the period's first month takes effect from its 1st day, so it counts too."""
    period_start = esi_period_start(month)
    on = max(period_start, emp.date_of_joining)
    rev = emp.structure_on(on)
    if rev is None:
        return structure_for_month(emp, month)
    structure = rev.as_structure()
    if increment and month_start(month) == period_start:
        structure = structure.plus_increment(increment)
    return structure


def employee_snapshot(emp):
    """Employee details printed on this month's payslip (frozen when the month is finalised)."""
    return {
        "code": emp.emp_code,
        "name": emp.full_name,
        "title": emp.title,
        "gender": emp.gender,
        "relation_type": emp.relation_type,
        "relation_name": emp.relation_name,
        "designation": emp.designation.name if emp.designation_id else "",
        "department": emp.department.name if emp.department_id else "",
        "doj": fmt_date(emp.date_of_joining),
        "dol": fmt_date(emp.date_of_leaving),
        "leaving_reason": emp.leaving_reason,
        "uan": emp.uan,
        "name_as_per_uan": emp.name_as_per_uan,
        "esic_ip_no": emp.esic_ip_no,
        "pan": emp.pan,
        "payment_mode": emp.payment_mode,
        "bank_name": emp.bank_name,
        "bank_account_no": emp.bank_account_no,
        "ifsc": emp.ifsc,
        "pf_member": emp.pf_member,
        "eps_member": emp.eps_member,
        "tax_regime": emp.tax_regime,
    }


def ytd_map(company, month, employee_ids):
    """Tax-year-to-date before this month: opening balance + previous employer + finalised months."""
    year = tax_year_of(month)
    out = {pk: engine.YTD() for pk in employee_ids}
    rows = (
        PayrollLine.objects.filter(
            run__company=company, run__status=PayrollRun.Status.FINALISED,
            run__month__gte=date(year, 4, 1), run__month__lt=month_start(month), employee_id__in=employee_ids,
        )
        .values("employee_id")
        .annotate(g=Sum("gross"), t=Sum("tds"), p=Sum("pt"))
    )
    for r in rows:
        y = out[r["employee_id"]]
        y.gross += r["g"] or ZERO
        y.tds += r["t"] or ZERO
        y.pt += r["p"] or ZERO
    for ob in OpeningBalance.objects.filter(company=company, tax_year=year, employee_id__in=employee_ids):
        y = out[ob.employee_id]
        y.gross += ob.ytd_gross + ob.prev_employer_income
        y.tds += ob.ytd_tds + ob.prev_employer_tds
        y.pt += ob.ytd_pt
    return out


def open_advances(company, employee_ids, given_by=None):
    """{employee_id: [(advance, outstanding), ...]} oldest first; only advances given by `given_by`."""
    out = {pk: [] for pk in employee_ids}
    qs = Advance.objects.filter(company=company, employee_id__in=employee_ids, status=Advance.Status.OPEN)
    if given_by is not None:
        qs = qs.filter(date_given__lte=given_by)  # an advance cannot be recovered before it is given
    qs = (
        qs
        .annotate(recovered_sum=Coalesce(Sum("recoveries__amount"), ZERO))
        .order_by("date_given", "id")
    )
    for adv in qs:
        outstanding = adv.amount - adv.recovered_sum
        if outstanding > 0:
            out[adv.employee_id].append((adv, outstanding))
    return out


def suggested_advance(advances):
    """Instalment due this month: each open advance's instalment, never more than what is left."""
    return sum((min(adv.monthly_installment, left) for adv, left in advances), ZERO)


class RunContext:
    """Everything needed to calculate one month, loaded once."""

    def __init__(self, company, month, employees=None):
        self.company = company
        self.month = month_start(month)
        self.month_rates = rates_for_month(self.month)
        self.rates = self.month_rates.rates
        self.settings = engine_settings(company)
        self.employees = list(employees if employees is not None else employees_for_month(company, self.month))
        ids = [e.pk for e in self.employees]
        self.ytd = ytd_map(company, self.month, ids)
        self.advances = open_advances(company, ids, given_by=month_end(self.month))
        # Payroll rules → Minimum-wage split: Basic at the minimum wage, the rest as incentive (by days present
        # from the profile salary, or by take-home from the amount written for the month).
        self.splits = company.amount_paid_mode == Company.AmountPaid.GROSS
        self.minimum_wages = MinimumWage.in_force(company.state, self.month)
        self.checks = checks.settings_for(company)

    def check_on(self, key):
        return self.checks[key]["on"]

    def minimum_wage(self, emp):
        """Monthly minimum wage for this person's skill in this month, or None if not entered."""
        return self.minimum_wages.for_skill(emp.skill_category) if self.minimum_wages else None

    def no_minimum_wage_message(self, emp):
        return _("There is no minimum wage for %(skill)s staff in %(state)s for %(month)s. Enter it on "
                 "All companies → Minimum wages, or set the right skill on the employee page.") % {
            "skill": emp.get_skill_category_display().lower(), "state": self.company.state_name,
            "month": month_label(self.month)}

    def outstanding(self, emp):
        return sum((left for _a, left in self.advances.get(emp.pk, [])), ZERO)

    def suggested_advance(self, emp):
        return suggested_advance(self.advances.get(emp.pk, []))


# ---------------------------------------------------------------- calculate one row
def _engine_line(ctx, emp, structure, esi_struct, inp):
    return engine.compute_line(ctx.month.year, ctx.month.month, engine_employee(emp), structure, esi_struct, inp,
                               ctx.settings, ctx.rates, ctx.ytd.get(emp.pk, engine.YTD()))


def minimum_for_days(ctx, emp, result):
    """Minimum wage earned for the paid days of `result`, or None when no minimum wage is entered."""
    wage = ctx.minimum_wage(emp)
    if not wage:
        return None
    factor, _outside = engine.pay_factor(ctx.month.year, ctx.month.month, engine_employee(emp), result.lop,
                                         ctx.settings)
    return engine.rupee(D(wage) * factor)


def _take_days_off(row, count):
    """`row` with `count` paid days fewer: from days present first, then paid leave, then weekly offs."""
    left, out = count, replace(row)
    for name in ("present", "paid_leave", "week_off_holiday"):
        take = min(left, getattr(out, name))
        setattr(out, name, getattr(out, name) - take)
        left -= take
    if out.lop is not None:
        out.lop += count
    return out


def split_by_days(ctx, emp, row):
    """By days present (minimum-wage split on, no take-home written for the month).

    The monthly salary in the person's profile (salary structure, plus a raise written for the month) is paid for
    the paid days. Basic is the minimum wage for the person's skill for those days and the rest is incentive; ESI
    and PF are worked out on Basic. A profile salary below the minimum wage still gets Basic at the minimum wage.
    A one-time incentive written for the month is paid on top. Returns (engine.Line, structure used, the profile
    salary is below the minimum wage).
    """
    wage = ctx.minimum_wage(emp)
    if not wage:
        raise PayrollError(ctx.no_minimum_wage_message(emp))
    wage = D(wage)
    monthly = structure_for_month(emp, ctx.month).gross() + (row.increment or ZERO)
    y, m = ctx.month.year, ctx.month.month
    dim = D(engine.days_in_month(y, m))
    counted = row.present + row.paid_leave + row.week_off_holiday
    lop = row.lop if row.lop is not None else max(ZERO, dim - counted)
    factor, _outside = engine.pay_factor(y, m, engine_employee(emp), lop, ctx.settings)
    # Structure for ESI coverage and the tax projection: Basic at the minimum wage, the rest as a monthly
    # incentive. This month's incentive is set so that the gross is exactly the monthly salary for the days.
    structure = engine.Structure(basic=wage, incentive=max(ZERO, monthly - wage))
    earned, basic = engine.rupee(max(monthly, wage) * factor), engine.rupee(wage * factor)
    inp = row.to_engine()
    inp.increment = ZERO  # already in the monthly salary
    inp.incentive = (earned - basic) - engine.rupee(structure.incentive * factor) + (row.incentive or ZERO)
    return _engine_line(ctx, emp, structure, structure, inp), structure, monthly < wage


def match_take_home(ctx, emp, row):
    """By take-home (minimum-wage split on, an amount written for the month): the take-home pay is exactly the
    amount the owner pays.

    Basic is the minimum wage for the person's skill, for the paid days. ESI is the company's fixed amount per
    person (Payroll rules, Rs 158 unless changed; empty = 0.75% of Basic) and PF is worked out on Basic. If Basic
    less ESI, PF and the other deductions would already be more than the amount, paid days are taken off one at a
    time (days present first) and the payslip and registers show the reduced days (the days as written are kept
    in days_given). Then the incentive is set so that the take-home is exactly the amount: take-home rises by at
    most Rs 1 for each Rs 1 of incentive, so stepping by the gap lands on it.
    Returns (engine.Line, structure used, the row actually used, days were reduced, exact).
    """
    wage = ctx.minimum_wage(emp)
    if not wage:
        raise PayrollError(ctx.no_minimum_wage_message(emp))
    wage, target = D(wage), row.take_home
    fixed_esi = ctx.company.take_home_esi
    y, m = ctx.month.year, ctx.month.month
    dim = D(engine.days_in_month(y, m))
    counted = row.present + row.paid_leave + row.week_off_holiday
    lop = row.lop if row.lop is not None else max(ZERO, dim - counted)
    if dim - lop <= 0:
        target = ZERO  # no paid days: nothing is paid this month
    person = engine_employee(emp)

    def run(days_off, incentive):
        """(engine line or None if the deductions do not fit, row used, structure) for these days and incentive."""
        used = _take_days_off(row, days_off) if days_off else replace(row)
        factor, _outside = engine.pay_factor(y, m, person, lop + days_off, ctx.settings)
        # The structure carries this month's incentive as a monthly figure, for ESI coverage and the tax
        # projection; the month's input makes the incentive paid exactly `incentive`.
        structure = engine.Structure(basic=wage, incentive=(incentive / factor) if factor else ZERO)
        inp = used.to_engine()
        inp.increment = ZERO
        inp.incentive = incentive - engine.rupee(structure.incentive * factor)
        inp.ee_esi = fixed_esi
        try:
            return _engine_line(ctx, emp, structure, structure, inp), used, structure
        except ValueError as exc:
            if "deductions are more than earnings" in str(exc):
                return None, used, structure
            raise

    days_off = ZERO
    line, used, structure = run(days_off, ZERO)
    while line is not None and line.net_pay > target and counted - days_off >= 1:
        days_off += 1
        line, used, structure = run(days_off, ZERO)
    if line is None or line.net_pay < target:
        incentive = target - (line.net_pay if line is not None else ZERO)
        for _step in range(80):
            line, used, structure = run(days_off, incentive)
            gap = target - (line.net_pay if line is not None else ZERO)
            if gap == 0:
                break
            incentive = max(ZERO, incentive + gap)
        if line is None:
            raise ValueError(f"{emp.emp_code}: deductions are more than earnings")
    if days_off and used.days_given is None:
        used.days_given = dim - lop
    return line, structure, used, bool(days_off), line.net_pay == target


def compute(ctx, emp, row):
    """Run the engine for one employee. Returns (engine.Line, structure used, warnings). Raises ValueError.

    By take-home the row actually used (days reduced, if they had to be) is result.row_used.
    """
    if ctx.splits and row.take_home is None:
        result, used, below = split_by_days(ctx, emp, row)
        base_gross = used.gross()
        warnings = list(result.warnings)
        if below:
            warnings.append("SALARY_BELOW_MINIMUM_WAGE")
    elif ctx.splits:
        result, used, used_row, reduced, exact = match_take_home(ctx, emp, row)
        result.row_used = used_row
        base_gross = used.gross()
        warnings = list(result.warnings)
        if reduced or (used_row.days_given is not None and used_row.days_given != result.paid_days):
            warnings.append("DAYS_REDUCED")
        if not exact:
            warnings.append("TAKE_HOME_NOT_EXACT")
    else:
        structure = structure_for_month(emp, ctx.month)
        result = _engine_line(ctx, emp, structure, esi_structure(emp, ctx.month, row.increment), row.to_engine())
        used = structure.plus_increment(row.increment) if row.increment else structure
        base_gross = structure.gross()
        warnings = list(result.warnings)
        minimum = minimum_for_days(ctx, emp, result)
        if minimum is not None and result.paid_days > 0 and result.basic + result.da + result.other < minimum:
            warnings.append("BELOW_MINIMUM_WAGE")
    start, end = ctx.month, month_end(ctx.month)
    if ctx.settings.pf_enabled and emp.pf_member and result.epf_wage > 0 and not emp.uan:
        warnings.append("NO_UAN")
    if result.esi_covered and not emp.esic_ip_no:
        warnings.append("NO_ESIC_NUMBER")
    if start <= emp.date_of_joining <= end:
        warnings.append("JOINED_THIS_MONTH")
    if emp.date_of_leaving and start <= emp.date_of_leaving <= end:
        warnings.append("LEFT_THIS_MONTH")
    if row.increment and row.increment > base_gross / 2:
        warnings.append("INCREMENT_LARGE")
    if result.paid_days == 0:
        warnings.append("ZERO_PAY")
    if row.ot_hours > OT_HOURS_NOTE:
        warnings.append("OT_HOURS_HIGH")
    if row.tds_override is not None and row.tds_override > result.gross * D("0.3"):
        warnings.append("TDS_OVERRIDE_HIGH")
    if row.lop is None and not row.week_off_holiday and result.lop > 0 and (row.present or row.paid_leave):
        warnings.append("WEEK_OFF_EMPTY")
    for code in ctx.month_rates.warnings:
        if code == "TAX_SLABS_NOT_UPDATED" and not ctx.settings.tds_enabled:
            continue
        warnings.append(code)
    return result, used, list(dict.fromkeys(warnings))


def _switchable_errors(ctx, emp, row):
    """The company's switchable checks that need no calculation (Settings → Attendance sheet checks)."""
    errors = []
    note = _("(This check is set in Settings → Attendance sheet checks.)")
    if ctx.check_on("other_reason") and row.other_deduction > 0 and not row.other_reason.strip():
        errors.append(("other_reason", _("Write the reason for the other deduction.")))
    if ctx.check_on("present_le_working"):
        working = engine.working_days_employed(ctx.month.year, ctx.month.month, emp.date_of_joining,
                                               emp.date_of_leaving, ctx.settings.weekly_off)
        if row.present > working:
            errors.append(("present", _("Days present (%(p)s) are more than the working days of this person in "
                                        "%(month)s (%(w)s). %(note)s") % {
                "p": plain_number(row.present), "month": month_label(ctx.month), "w": working, "note": note}))
    if ctx.check_on("paid_leave_limit"):
        most = D(ctx.checks["paid_leave_limit"]["limit"])
        if row.paid_leave > most:
            errors.append(("paid_leave", _("More than %(n)s days of paid leave in a month. %(note)s")
                           % {"n": plain_number(most), "note": note}))
    return errors


def _minimum_wage_errors(ctx, emp, row, result, warnings):
    note = _("(This check is set in Settings → Attendance sheet checks.)")
    if not ctx.minimum_wage(emp):
        return [("present", ctx.no_minimum_wage_message(emp))]
    if "BELOW_MINIMUM_WAGE" in warnings:
        return [("present", _("Basic + DA + other allowance (₹%(earned)s) is below the minimum wage for the days "
                              "paid (₹%(minimum)s). Revise the salary. %(note)s") % {
            "earned": indian_format(result.basic + result.da + result.other),
            "minimum": indian_format(minimum_for_days(ctx, emp, result)), "note": note})]
    return []


def check_row(ctx, emp, row, days_in_month_value=None, strict=False):
    """Mistakes that block saving, as [(column key, message)]. Empty list = fine.

    strict=True when figures are being entered (upload, Change one person, Amounts paid): the company's
    switchable checks (payroll/checks.py) are applied too. Working a saved month out again never uses them.
    """
    errors = []
    dim = engine.days_in_month(ctx.month.year, ctx.month.month)
    label = month_label(ctx.month)
    if days_in_month_value is not None and D(days_in_month_value) != dim:
        errors.append(("days_in_month", _("Days in Month must be %(dim)s for %(month)s. Please do not change this column.")
                       % {"dim": dim, "month": label}))
    for key in NUMBER_KEYS:
        value = getattr(row, key)
        if value is not None and value < 0:
            if key == "increment":
                errors.append((key, _("Increment cannot be negative. To cut the salary use Revise salary on the employee page.")))
            else:
                errors.append((key, _("Cannot be a negative number.")))
    for key in DAY_KEYS:
        value = getattr(row, key)
        if value is not None and (value * 2) != (value * 2).to_integral_value():
            errors.append((key, _("Use whole or half days only, like 25 or 25.5.")))
    for key in MONEY_KEYS:
        value = getattr(row, key)
        if value is not None and value > MAX_AMOUNT:
            errors.append((key, _("This looks too big (more than ₹99,99,999). Please check.")))
    if row.ot_hours > HOURS_IN_A_MONTH:
        errors.append(("ot_hours", _("More than %(max)s overtime hours in a month? Please check. A month has at "
                                     "most %(max)s hours.") % {"max": HOURS_IN_A_MONTH}))
    elif strict and ctx.check_on("ot_limit") and row.ot_hours > D(ctx.checks["ot_limit"]["limit"]):
        errors.append(("ot_hours", _("More than %(max)s overtime hours in a month? Please check. (This limit is set "
                                     "in Settings → Attendance sheet checks.)") % {"max": ctx.checks["ot_limit"]["limit"]}))
    if len(row.other_reason) > 200:
        errors.append(("other_reason", _("Write the reason in at most 200 letters.")))
    if errors:
        return errors

    counted = row.present + row.paid_leave + row.week_off_holiday
    if row.lop is not None and counted + row.lop != dim:
        errors.append(("lop", _(
            "Days do not add up: present %(p)s + paid leave %(l)s + weekly off/holidays %(w)s + LOP %(lop)s = %(total)s, "
            "but %(month)s has %(dim)s days.") % {
                "p": plain_number(row.present), "l": plain_number(row.paid_leave),
                "w": plain_number(row.week_off_holiday), "lop": plain_number(row.lop),
                "total": plain_number(counted + row.lop), "month": label, "dim": dim}))
    elif row.lop is None and counted > dim:
        errors.append(("present", _(
            "Days do not add up: present %(p)s + paid leave %(l)s + weekly off/holidays %(w)s = %(total)s, "
            "more than the %(dim)s days in %(month)s.") % {
                "p": plain_number(row.present), "l": plain_number(row.paid_leave),
                "w": plain_number(row.week_off_holiday), "total": plain_number(counted), "dim": dim, "month": label}))
    else:
        employed = days_employed(emp, ctx.month)
        if counted > employed:
            if emp.date_of_joining > ctx.month:
                why = _("joined on %(d)s") % {"d": fmt_date(emp.date_of_joining)}
            else:
                why = _("last working day %(d)s") % {"d": fmt_date(emp.date_of_leaving)}
            errors.append(("present", _(
                "Paid days (%(paid)s) are more than the days employed in %(month)s (%(employed)s, %(why)s). "
                "Put the other days in Absent / LOP.") % {
                    "paid": plain_number(counted), "month": label, "employed": employed, "why": why}))
    outstanding = ctx.outstanding(emp)
    if row.advance > outstanding:
        errors.append(("advance", _("Advance deduction (₹%(a)s) is more than the advance still to be recovered (₹%(o)s).")
                       % {"a": engine.indian_format(row.advance), "o": engine.indian_format(outstanding)}))
    if strict:
        errors.extend(_switchable_errors(ctx, emp, row))
    if errors:
        return errors
    try:
        result, _used, warnings = compute(ctx, emp, row)
        if strict and ctx.check_on("min_wage"):
            errors.extend(_minimum_wage_errors(ctx, emp, row, result, warnings))
        if strict and ctx.check_on("week_off_filled") and "WEEK_OFF_EMPTY" in warnings:
            errors.append(("week_off_holiday", _("Weekly offs are empty, so they were counted as absent. Write the "
                                                 "weekly offs (this check is set in Settings → Attendance sheet checks).")))
    except ValueError as exc:
        if "deductions are more than earnings" in str(exc):
            if row.advance:
                errors.append(("advance", _("Deductions are more than the salary earned this month. Reduce the advance.")))
            elif row.other_deduction:
                errors.append(("other_deduction", _("Deductions are more than the salary earned this month. "
                                                    "Reduce the other deduction.")))
            elif row.tds_override:
                errors.append(("tds_override", _("The TDS override is more than the salary earned this month.")))
            else:
                errors.append(("present", _("The salary earned this month is less than PF, ESI and tax. "
                                            "Check the days present.")))
        else:
            errors.append(("present", str(exc)))
    except PayrollError as exc:
        errors.append(("present", str(exc)))
    except ArithmeticError:
        errors.append(("present", _("These numbers could not be worked out. Please check this row.")))
    return errors


# ---------------------------------------------------------------- store results
LINE_RESULT_FIELDS = {
    # PayrollLine field: engine.Line attribute
    "lop": "lop", "paid_days": "paid_days", "ncp_days": "ncp_days",
    "earned_basic": "basic", "earned_da": "da", "earned_hra": "hra", "earned_conveyance": "conveyance",
    "earned_other": "other", "ot_pay": "ot_pay", "incentive_pay": "incentive", "gross": "gross",
    "code_wages": "code_wages", "epf_wage": "epf_wage", "eps_wage": "eps_wage", "edli_wage": "edli_wage",
    "ee_pf": "ee_pf", "er_eps": "er_eps", "er_epf": "er_epf", "edli": "edli", "pf_admin": "pf_admin",
    "esi_covered": "esi_covered", "esi_wage": "esi_wage", "ee_esi": "ee_esi", "er_esi": "er_esi",
    "pt": "pt", "tds": "tds", "advance": "advance", "other_deduction_amt": "other_deduction",
    "total_deductions": "total_deductions", "net_pay": "net_pay", "employer_cost": "employer_cost",
}


def fill_line(line, ctx, emp, row, result, used, warnings):
    row = getattr(result, "row_used", row)  # gross split: the days actually paid
    line.company = ctx.company
    line.employee = emp
    line.emp_snapshot = employee_snapshot(emp)
    line.days_in_month = engine.days_in_month(ctx.month.year, ctx.month.month)
    line.working_days = engine.working_days(ctx.month.year, ctx.month.month, ctx.settings.weekly_off)
    line.present, line.paid_leave, line.week_off_holiday = row.present, row.paid_leave, row.week_off_holiday
    line.lop_days = row.lop
    line.ot_hours, line.ot_amount_override = row.ot_hours, row.ot_amount
    line.incentive, line.increment = row.incentive, row.increment
    line.advance_deduction, line.other_deduction = row.advance, row.other_deduction
    line.other_deduction_reason = row.other_reason.strip()
    line.tds_override, line.remarks = row.tds_override, row.remarks.strip()
    line.days_given, line.take_home = row.days_given, row.take_home
    line.rate_basic, line.rate_da, line.rate_hra = used.basic, used.da, used.hra
    line.rate_conveyance, line.rate_other = used.conveyance, used.other
    for field_name, attr in LINE_RESULT_FIELDS.items():
        setattr(line, field_name, getattr(result, attr))
    line.warnings = warnings
    line.engine_version = engine.ENGINE_VERSION
    return line


def update_run_pf_admin(run, rates, lines):
    run.pf_admin_establishment = engine.establishment_pf_admin(lines, rates)
    run.pf_admin_topup = max(ZERO, run.pf_admin_establishment - sum((l.pf_admin for l in lines), ZERO))


def _check_or_raise(ctx, rows_by_emp):
    problems = []
    for emp in ctx.employees:
        for _key, message in check_row(ctx, emp, rows_by_emp[emp.pk]):
            problems.append(f"{emp.emp_code} {emp.full_name}: {message}")
    if problems:
        raise PayrollError(" ".join(problems))


def save_inputs(company, month, rows_by_emp, user, uploaded_file=None, request=None):
    """Store the inputs for a draft month (replacing earlier ones) and calculate every line."""
    month = month_start(month)
    with transaction.atomic():
        run, _created = PayrollRun.objects.select_for_update().get_or_create(
            company=company, month=month, defaults={"created_by": user})
        if run.is_final:
            raise PayrollError(_("%(month)s is finalised. Reopen it first.") % {"month": month_label(month)})
        ctx = RunContext(company, month)
        missing = [e for e in ctx.employees if e.pk not in rows_by_emp]
        if missing:
            raise PayrollError(_("These employees are missing: %(names)s.") % {
                "names": ", ".join(f"{e.emp_code} {e.full_name}" for e in missing)})
        _check_or_raise(ctx, rows_by_emp)
        existing = {l.employee_id: l for l in run.lines.all()}
        lines = []
        for emp in ctx.employees:
            row = rows_by_emp[emp.pk]
            result, used, warnings = compute(ctx, emp, row)
            line = existing.pop(emp.pk, None) or PayrollLine(run=run, created_by=user)
            fill_line(line, ctx, emp, row, result, used, warnings)
            line.save()
            lines.append(line)
        for stale in existing.values():  # draft rows of people no longer paid this month
            stale.delete()
        update_run_pf_admin(run, ctx.rates, lines)
        if uploaded_file is not None:
            uploaded_file.seek(0)
            run.uploaded_file.save(f"{company.code}_salary_{month:%Y-%m}_{timezone.now():%Y%m%d%H%M%S}.xlsx",
                                   uploaded_file, save=False)
            run.uploaded_at = timezone.now()
        run.save()
        audit(request, "payroll.upload", run, {"month": str(month), "employees": len(lines)}, company=company)
    return run


@transaction.atomic
def recompute_run(run, ctx=None):
    """Calculate every line of a draft month again from its stored inputs (all or nothing)."""
    if run.is_final:
        raise PayrollError(_("A finalised month cannot be recalculated."))
    ctx = ctx or RunContext(run.company, run.month)
    by_emp = {e.pk: e for e in ctx.employees}
    lines = list(run.lines.select_related("employee"))
    inactive = [l for l in lines if l.employee_id not in by_emp and l.employee.status == Employee.Status.INACTIVE]
    lines = [l for l in lines if l not in inactive]
    rows = {}
    for line in lines:
        emp = by_emp.get(line.employee_id)
        if emp is None:
            raise PayrollError(_("%(name)s is no longer paid in %(month)s. Upload the sheet again.")
                               % {"name": line.name, "month": month_label(run.month)})
        rows[emp.pk] = RowInput.from_line(line)
    missing = [e for e in ctx.employees if e.pk not in rows]
    if missing:
        raise PayrollError(_("New in %(month)s: %(names)s. Add their days: on the month page use By days present "
                             "or By take-home, or download the %(month)s sheet again (it now has them), fill it "
                             "and upload it.") % {
            "month": month_label(run.month), "names": ", ".join(f"{e.emp_code} {e.full_name}" for e in missing)})
    _check_or_raise(ctx, rows)
    for line in inactive:  # made inactive: left out of this draft month
        line.delete()
    for line in lines:
        emp = by_emp[line.employee_id]
        result, used, warnings = compute(ctx, emp, rows[emp.pk])
        fill_line(line, ctx, emp, rows[emp.pk], result, used, warnings)
        line.save()
    update_run_pf_admin(run, ctx.rates, lines)
    run.save()
    return lines


def update_line(run, line, row, user, request=None):
    """Owner edited one employee's inputs on a draft month."""
    with transaction.atomic():
        run = PayrollRun.objects.select_for_update().get(pk=run.pk)
        if run.is_final:
            raise PayrollError(_("%(month)s is finalised. Reopen it first.") % {"month": run.label})
        ctx = RunContext(run.company, run.month)
        emp = next((e for e in ctx.employees if e.pk == line.employee_id), None)
        if emp is None:
            raise PayrollError(_("This employee is not paid in %(month)s.") % {"month": run.label})
        errors = check_row(ctx, emp, row, strict=True)
        if errors:
            raise PayrollError(" ".join(m for _k, m in errors))
        result, used, warnings = compute(ctx, emp, row)
        fill_line(line, ctx, emp, row, result, used, warnings)
        line.save()
        update_run_pf_admin(run, ctx.rates, list(run.lines.all()))
        run.save()
        audit(request, "payroll.line_edit", line, {"month": str(run.month), "employee": emp.emp_code,
                                                   "inputs": row.as_dict()}, company=run.company)
    return line


def days_row(ctx, emp, paid_days, base=None):
    """Attendance from paid days alone (the entry pages): weekly offs as the sheet pre-fills them (only those
    inside the days employed), the rest present. Other inputs (overtime, advance ...) come from `base`."""
    y, m, offs_days = ctx.month.year, ctx.month.month, ctx.settings.weekly_off
    start, end = engine.employed_span(y, m, emp.date_of_joining, emp.date_of_leaving)
    offs = D(engine.weekly_offs_between(start, end, offs_days) if start else 0)
    row = replace(base) if base is not None else RowInput(advance=ctx.suggested_advance(emp))
    row.week_off_holiday = min(offs, paid_days)
    row.present = paid_days - row.week_off_holiday
    row.paid_leave, row.lop, row.days_given = ZERO, None, None
    return row


BY_DAYS, BY_TAKE_HOME = "days", "take-home"


def last_take_home(company, month, employee_ids):
    """{employee id: take-home written for the month before}, to start the take-home page from."""
    return dict(PayrollLine.objects.filter(
        run__company=company, run__month=add_months(month_start(month), -1), employee_id__in=employee_ids,
        take_home__isnull=False).values_list("employee_id", "take_home"))


def entry_row(ctx, emp, line, method, typed_days=None, typed_pay=None, last_pay=None):
    """One person on an entry page: by days present (BY_DAYS) or by take-home (BY_TAKE_HOME).

    Paid days default to the days written before (or the days employed in the month). By take-home the amount
    defaults to the amount written before for this month, else last month's. Returns the row as a dict with the
    worked-out line ("result", None if it could not be worked out) and "errors".
    """
    month = ctx.month
    employed = D(days_employed(emp, month))
    given = (line.days_given if line is not None and line.days_given is not None
             else line.paid_days if line is not None else employed)
    days = typed_days if typed_days is not None else given
    if line is not None and days == line.paid_days and line.days_given is None:
        row = RowInput.from_line(line)  # keep the days as uploaded (paid leave, holidays ...)
    else:
        row = days_row(ctx, emp, days, RowInput.from_line(line) if line is not None else None)
    try:
        monthly = structure_for_month(emp, month).gross()
    except PayrollError:
        monthly = None
    pay = None
    if method == BY_TAKE_HOME:
        row.incentive = ZERO  # the take-home written is the whole month's pay: its incentive is worked out
        pay = typed_pay if typed_pay is not None else (
            line.take_home if line is not None and line.take_home is not None else last_pay)
        if pay is None and days == 0:
            pay = ZERO
    row.take_home = pay
    item = {"emp": emp, "row": row, "line": line, "days_value": days, "employed": employed, "pay": pay,
            "monthly": monthly, "minimum": ctx.minimum_wage(emp), "result": None, "warnings": []}
    problems = []
    if method == BY_TAKE_HOME and pay is None:
        problems = [_("Write the take-home amount (or 0 paid days).")]
    elif days > employed:
        problems = [_("Paid days (%(d)s) cannot be more than the %(e)s days employed in %(month)s.")
                    % {"d": plain_number(days), "e": plain_number(employed), "month": month_label(month)}]
    if not problems:
        problems = [m for _k, m in check_row(ctx, emp, row, strict=True)]
    if not problems:
        try:
            item["result"], _used, item["warnings"] = compute(ctx, emp, row)
            item["row"] = getattr(item["result"], "row_used", row)
        except PayrollError as exc:
            problems = [str(exc)]
        except ValueError as exc:
            problems = [_("Deductions are more than the salary.") if "deductions" in str(exc) else str(exc)]
    item["errors"] = problems
    return item


def entry_page(company, month, method, entries=None):
    """Rows of an entry page (by days present or by take-home): everyone paid in the month.

    entries: {employee id: (paid days or None, take-home or None)} as typed.
    Returns (rows, errors {employee id: [messages]}, started).
    """
    month = month_start(month)
    ctx = RunContext(company, month)
    run = PayrollRun.objects.filter(company=company, month=month).first()
    lines = {l.employee_id: l for l in run.lines.all()} if run else {}
    last = last_take_home(company, month, [e.pk for e in ctx.employees]) if method == BY_TAKE_HOME else {}
    entries = entries or {}
    rows, errors = [], {}
    for emp in ctx.employees:
        typed_days, typed_pay = entries.get(emp.pk, (None, None))
        item = entry_row(ctx, emp, lines.get(emp.pk), method, typed_days, typed_pay, last.get(emp.pk))
        if item["errors"]:
            errors[emp.pk] = item["errors"]
        rows.append(item)
    return rows, errors, bool(lines)


def preview_row(company, month, method, emp, typed_days=None, typed_pay=None):
    """One person worked out as typed on an entry page, without saving (the figures change as you type)."""
    month = month_start(month)
    ctx = RunContext(company, month, employees=[emp])
    line = PayrollLine.objects.filter(run__company=company, run__month=month, employee=emp).first()
    last = last_take_home(company, month, [emp.pk]).get(emp.pk) if method == BY_TAKE_HOME else None
    return entry_row(ctx, emp, line, method, typed_days, typed_pay, last)


def save_entry_page(company, month, method, entries, user, request=None):
    """Save an entry page: start (or work out again) the month with the days (and take-home amounts) written.
    All or nothing. The people's salaries are not changed."""
    with transaction.atomic():
        blocker = upload_blocker(company, month)
        if blocker:
            raise PayrollError(blocker)
        rows, errors, _started = entry_page(company, month, method, entries)
        if errors:
            raise PayrollError(_("Nothing was saved. Correct the rows marked in red."))
        return save_inputs(company, month, {item["emp"].pk: item["row"] for item in rows}, user, request=request)


# ---------------------------------------------------------------- months, finalise, reopen
def month_rows(company, today=None):
    """[(month, run or None)] from the first salary month to next month, newest first."""
    today = today or timezone.localdate()
    runs = {r.month: r for r in PayrollRun.objects.filter(company=company)}
    last = max(add_months(month_start(today), 1), company.payroll_start_month, *runs.keys())
    rows, m = [], company.payroll_start_month
    while m <= last:
        rows.append((m, runs.get(m)))
        m = add_months(m, 1)
    return list(reversed(rows))


def first_open_month(company):
    finalised = set(PayrollRun.objects.filter(company=company, status=PayrollRun.Status.FINALISED)
                    .values_list("month", flat=True))
    m = company.payroll_start_month
    while m in finalised:
        m = add_months(m, 1)
    return m


def latest_finalised(company):
    return (PayrollRun.objects.filter(company=company, status=PayrollRun.Status.FINALISED)
            .order_by("-month").first())


def earlier_open_month(company, month):
    """The first earlier month that is not finalised yet, or None."""
    m = first_open_month(company)
    return m if m < month_start(month) else None


def upload_blocker(company, month):
    if month_start(month) < company.payroll_start_month:
        return _("This month is before you started using the app.")
    if not employees_for_month(company, month).exists():
        return _("No one is paid in %(month)s yet. Add your employees first (Employees → Add employee, "
                 "or Upload from Excel).") % {"month": month_label(month)}
    earlier = earlier_open_month(company, month)
    if earlier:
        return _("Finalise %(month)s first. Months are done in order.") % {"month": month_label(earlier)}
    run = PayrollRun.objects.filter(company=company, month=month_start(month)).first()
    if run and run.is_final:
        return _("This month is finalised. Reopen it to make changes.")
    return ""


def finalise_blockers(run, today=None):
    if run.is_final:
        return [_("This month is already finalised.")]
    today = today or timezone.localdate()
    if run.month > month_start(today):
        return [_("%(month)s has not started yet. It can be finalised from %(start)s.")
                % {"month": run.label, "start": fmt_date(run.month)}]
    earlier = earlier_open_month(run.company, run.month)
    if earlier:
        return [_("Finalise %(month)s first. Months are finalised in order.") % {"month": month_label(earlier)}]
    if not run.lines.exists():
        return [_("Upload the attendance sheet first.")]
    expected = {e.pk: e for e in employees_for_month(run.company, run.month)}
    have = set(run.lines.values_list("employee_id", flat=True))
    missing = [e for pk, e in expected.items() if pk not in have]
    if missing:
        return [_("These people are not in this month's sheet yet: %(names)s. Download the sheet again "
                  "(it now has them), fill their days and upload it.")
                % {"names": ", ".join(f"{e.emp_code} {e.full_name}" for e in missing)}]
    extra = run.lines.exclude(employee_id__in=list(expected)).count()
    if extra:
        return [_("Some people in this month's sheet are no longer paid in %(month)s (their dates changed). "
                  "Download the sheet again and upload it.") % {"month": run.label}]
    return []


def _allocate_recoveries(run, line, user):
    remaining = line.advance
    advances = (Advance.objects.select_for_update()
                .filter(company=run.company, employee=line.employee, status=Advance.Status.OPEN)
                .order_by("date_given", "id"))
    for adv in advances:
        if remaining <= 0:
            break
        left = adv.outstanding
        if left <= 0:
            continue
        take = min(left, remaining)
        AdvanceRecovery.objects.create(company=run.company, advance=adv, payroll_line=line, month=run.month,
                                       amount=take, created_by=user)
        remaining -= take
        adv.refresh_status()
    if remaining > 0:
        raise PayrollError(_("%(name)s: advance deduction is more than the advance still to be recovered.")
                           % {"name": line.name})


def finalise(run, user, request=None):
    with transaction.atomic():
        run = PayrollRun.objects.select_for_update().get(pk=run.pk)
        problems = finalise_blockers(run)
        if problems:
            raise PayrollError(problems[0])
        ctx = RunContext(run.company, run.month)
        lines = recompute_run(run, ctx)
        for line in lines:
            if line.advance > 0:
                _allocate_recoveries(run, line, user)
            if line.increment > 0:
                # The person's own structure plus the raise (not the minimum wage split of this month's line).
                raised = structure_for_month(line.employee, run.month).plus_increment(line.increment)
                SalaryRevision.objects.create(
                    company=run.company, employee=line.employee, effective_from=run.month,
                    basic=raised.basic, da=raised.da, hra=raised.hra, conveyance=raised.conveyance,
                    other_allowance=raised.other, source=SalaryRevision.Source.MONTHLY_SHEET, payroll_run=run,
                    reason=_("Increment of ₹%(amount)s in the %(month)s salary sheet")
                    % {"amount": plain_number(line.increment), "month": run.label},
                    created_by=user,
                )
        run.rates_snapshot = {**ctx.month_rates.snapshot, "settings": settings_snapshot(ctx.settings)}
        run.status = PayrollRun.Status.FINALISED
        run.finalised_at = timezone.now()
        run.finalised_by = user
        run.save()
        totals = run.totals()
        audit(request, "payroll.finalise", run, {
            "month": str(run.month), "revision_no": run.revision_no, "employees": totals["count"],
            "net_pay": totals["net_pay"], "company_cost": totals["company_cost"]}, company=run.company)
    return run


def reopen_blockers(run):
    if not run.is_final:
        return [_("This month is not finalised.")]
    if PayrollRun.objects.filter(company=run.company, month__gt=run.month,
                                 status=PayrollRun.Status.FINALISED).exists():
        return [_("Only the latest finalised month can be reopened.")]
    return []


def reopen(run, user, reason, request=None):
    reason = (reason or "").strip()
    if not reason:
        raise PayrollError(_("Please write why the month is being reopened."))
    with transaction.atomic():
        run = PayrollRun.objects.select_for_update().get(pk=run.pk)
        problems = reopen_blockers(run)
        if problems:
            raise PayrollError(problems[0])
        recoveries = AdvanceRecovery.objects.filter(payroll_line__run=run)
        advance_ids = set(recoveries.values_list("advance_id", flat=True))
        recoveries.delete()
        for adv in Advance.objects.filter(pk__in=advance_ids):
            adv.refresh_status()
        SalaryRevision.objects.filter(payroll_run=run).delete()
        run.status = PayrollRun.Status.DRAFT
        run.revision_no += 1
        run.reopen_reason = reason
        run.finalised_at = None
        run.finalised_by = None
        run.save()
        audit(request, "payroll.reopen", run, {"month": str(run.month), "reason": reason,
                                               "revision_no": run.revision_no}, company=run.company)
    return run


def recalculate_drafts(company):
    """Work out every draft month again after a change to staff, salary or rules.

    Finalised months never change. Returns [(month label, problem)] for drafts that
    could not be worked out (for example a person relieved before that month).
    """
    problems = []
    for run in PayrollRun.objects.filter(company=company, status=PayrollRun.Status.DRAFT).order_by("month"):
        if not run.lines.exists():
            continue
        try:
            recompute_run(run)
        except (PayrollError, MissingRate) as exc:
            text = str(exc)
            if "sheet again" not in text:
                text = f"{text} {_('Upload its sheet again.')}"
            problems.append((run.label, text))
    return problems


# ---------------------------------------------------------------- helpers for other screens
def esi_covered_now(emp, company, month_rates, month):
    if not company.esi_enabled:
        return False
    try:
        return engine.esi_covered(engine_employee(emp), esi_structure(emp, month), month_rates.rates)
    except PayrollError:
        return False
