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
from employees.rules import first_revision_month
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
    days_given: Optional[D] = None  # gross split: paid days as written, before PayCore reduced them

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
        )

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}


# ---------------------------------------------------------------- who is paid this month
def employees_for_month(company, month):
    """Joined on or before month end and not left before month start."""
    start, end = month_start(month), month_end(month)
    return (
        Employee.objects.filter(company=company, date_of_joining__lte=end)
        .filter(Q(date_of_leaving__isnull=True) | Q(date_of_leaving__gte=start))
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
        # Payroll rules → Gross salary split: Basic at the minimum wage, the rest of the gross as incentive.
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


def monthly_gross(ctx, emp, increment=ZERO, gross=None):
    """The person's gross salary for the month: the one written on the Gross salaries page, else their salary."""
    return (gross if gross is not None else structure_for_month(emp, ctx.month).gross()) + (increment or ZERO)


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


def split_gross(ctx, emp, row, gross=None):
    """Gross salary split (Payroll rules): the gross salary for the month is paid exactly as given.

    Basic is the minimum wage for the person's skill, for the paid days. If that would be more than the gross,
    paid days are taken off one at a time (days present first) until Basic fits; the days shown on the payslip
    and registers are then the reduced days, and the days as written are kept in days_given. The rest of the
    gross is incentive. ESI and PF are worked out on Basic (the incentive is not "wages").
    Returns (engine.Line, structure used, the row actually used, days were reduced).
    """
    wage = ctx.minimum_wage(emp)
    if not wage:
        raise PayrollError(ctx.no_minimum_wage_message(emp))
    wage, monthly = D(wage), monthly_gross(ctx, emp, row.increment, gross)
    y, m = ctx.month.year, ctx.month.month
    dim = D(engine.days_in_month(y, m))
    counted = row.present + row.paid_leave + row.week_off_holiday
    lop = row.lop if row.lop is not None else max(ZERO, dim - counted)
    person = engine_employee(emp)

    def basic_for(lop_days):
        factor, _outside = engine.pay_factor(y, m, person, lop_days, ctx.settings)
        return engine.rupee(wage * factor), factor

    reduce = ZERO
    while basic_for(lop + reduce)[0] > monthly and lop + reduce + 1 <= dim and counted - reduce >= 1:
        reduce += 1
    if basic_for(lop + reduce)[0] > monthly:  # less than a day of minimum wage: nothing paid as Basic
        reduce = counted
    used_row = _take_days_off(row, reduce) if reduce else replace(row)
    if used_row.days_given is None and reduce:
        used_row.days_given = dim - lop
    basic, factor = basic_for(lop + reduce)
    # Structure for ESI coverage and the tax projection: Basic at the minimum wage, the rest of the gross as a
    # monthly incentive. This month's incentive is set so that the gross is exactly the gross as given.
    structure = engine.Structure(basic=wage, incentive=max(ZERO, monthly - wage))
    inp = used_row.to_engine()
    inp.increment = ZERO  # already in the monthly gross
    inp.incentive = (monthly - basic) - engine.rupee(structure.incentive * factor) + row.incentive
    result = _engine_line(ctx, emp, structure, structure, inp)
    return result, structure, used_row, bool(reduce)


def compute(ctx, emp, row, gross=None):
    """Run the engine for one employee. Returns (engine.Line, structure used, warnings). Raises ValueError.

    With the gross salary split the row actually used (days reduced, if they had to be) is result.row_used.
    """
    if ctx.splits:
        result, used, used_row, reduced = split_gross(ctx, emp, row, gross)
        result.row_used = used_row
        base_gross = monthly_gross(ctx, emp, row.increment, gross)
        warnings = list(result.warnings)
        if reduced or (used_row.days_given is not None and used_row.days_given != result.paid_days):
            warnings.append("DAYS_REDUCED")
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
    line.days_given = row.days_given
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
    rows = {}
    for line in lines:
        emp = by_emp.get(line.employee_id)
        if emp is None:
            raise PayrollError(_("%(name)s is no longer paid in %(month)s. Upload the sheet again.")
                               % {"name": line.name, "month": month_label(run.month)})
        rows[emp.pk] = RowInput.from_line(line)
    missing = [e for e in ctx.employees if e.pk not in rows]
    if missing:
        raise PayrollError(_("New in %(month)s: %(names)s. Download the %(month)s sheet again (it now has them), "
                             "fill their days and upload it.") % {
            "month": month_label(run.month), "names": ", ".join(f"{e.emp_code} {e.full_name}" for e in missing)})
    _check_or_raise(ctx, rows)
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
    """Attendance from paid days alone (the Gross salaries page): weekly offs as the sheet pre-fills them (only
    those inside the days employed), the rest present. Other inputs (overtime, advance ...) come from `base`."""
    y, m, offs_days = ctx.month.year, ctx.month.month, ctx.settings.weekly_off
    start, end = engine.employed_span(y, m, emp.date_of_joining, emp.date_of_leaving)
    offs = D(engine.weekly_offs_between(start, end, offs_days) if start else 0)
    row = replace(base) if base is not None else RowInput(advance=ctx.suggested_advance(emp))
    row.week_off_holiday = min(offs, paid_days)
    row.present = paid_days - row.week_off_holiday
    row.paid_leave, row.lop, row.days_given = ZERO, None, None
    return row


def set_monthly_gross(company, emp, month, gross, user, request=None):
    """New monthly gross salary for a person from `month` (a draft month), written on the Gross salaries page.

    Kept as a salary structure (Basic = the gross; the company splits it into minimum wage + incentive).
    A salary already set for that same month is replaced instead of adding a second one.
    """
    month = max(month_start(month), first_revision_month(company, emp.date_of_joining))
    rev = (SalaryRevision.objects.filter(employee=emp, effective_from=month, payroll_run__isnull=True)
           .order_by("-id").first())
    old = rev.gross if rev else None
    if rev is None:
        rev = SalaryRevision(company=company, employee=emp, effective_from=month, created_by=user,
                             source=SalaryRevision.Source.MANUAL)
    rev.basic, rev.da, rev.hra, rev.conveyance, rev.other_allowance = gross, ZERO, ZERO, ZERO, ZERO
    rev.reason = _("Gross salary written on the Gross salaries page")
    rev.save()
    audit(request, "salary.revise", rev, {"employee": emp.emp_code, "from": str(month), "source": "gross page",
                                          "old_gross": old, "new_gross": gross}, company=company)
    return rev


def gross_page(company, month, entries=None):
    """Rows of the Gross salaries page: everyone paid in the month, their paid days and gross for the month.

    entries: {employee id: (paid days or None, gross or None)} as typed. Paid days default to the days written
    before (or the days employed in the month); the gross defaults to the person's salary.
    Returns (rows, errors {employee id: [messages]}, started).
    """
    month = month_start(month)
    ctx = RunContext(company, month)
    run = PayrollRun.objects.filter(company=company, month=month).first()
    lines = {l.employee_id: l for l in run.lines.all()} if run else {}
    entries = entries or {}
    rows, errors = [], {}
    for emp in ctx.employees:
        typed_days, typed_gross = entries.get(emp.pk, (None, None))
        line = lines.get(emp.pk)
        employed = D(days_employed(emp, month))
        given = (line.days_given if line is not None and line.days_given is not None
                 else line.paid_days if line is not None else employed)
        days = typed_days if typed_days is not None else given
        if line is not None and days == line.paid_days and line.days_given is None:
            row = RowInput.from_line(line)  # keep the days as uploaded (paid leave, holidays ...)
        else:
            row = days_row(ctx, emp, days, RowInput.from_line(line) if line is not None else None)
        try:
            current = structure_for_month(emp, month).gross()
        except PayrollError:
            current = None
        gross = typed_gross if typed_gross is not None else current
        item = {"emp": emp, "row": row, "line": line, "days_value": days, "employed": employed, "current": current,
                "gross": gross, "minimum": ctx.minimum_wage(emp), "result": None, "warnings": []}
        problems = []
        if gross is None:
            problems = [_("Write the gross salary.")]
        elif days > employed:
            problems = [_("Paid days (%(d)s) cannot be more than the %(e)s days employed in %(month)s.")
                        % {"d": plain_number(days), "e": plain_number(employed), "month": month_label(month)}]
        if not problems:
            problems = [m for _k, m in check_row(ctx, emp, row, strict=True)]
        if not problems:
            try:
                item["result"], _used, item["warnings"] = compute(ctx, emp, row, gross=gross)
                item["row"] = getattr(item["result"], "row_used", row)
            except PayrollError as exc:
                problems = [str(exc)]
            except ValueError as exc:
                problems = [_("Deductions are more than the salary.") if "deductions" in str(exc) else str(exc)]
        if problems:
            errors[emp.pk] = problems
        rows.append(item)
    return rows, errors, bool(lines)


def save_gross_page(company, month, entries, user, request=None):
    """Save the Gross salaries page: changed gross salaries from this month, then start (or work out again) the
    month with the days shown. All or nothing. Returns (run, number of gross salaries changed)."""
    with transaction.atomic():
        blocker = upload_blocker(company, month)
        if blocker:
            raise PayrollError(blocker)
        rows, errors, _started = gross_page(company, month, entries)
        if errors:
            raise PayrollError(_("Nothing was saved. Correct the rows marked in red."))
        changed = 0
        for item in rows:
            if item["gross"] != item["current"]:
                set_monthly_gross(company, item["emp"], month, item["gross"], user, request=request)
                changed += 1
        run = save_inputs(company, month, {item["emp"].pk: item["row"] for item in rows}, user, request=request)
    return run, changed


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
