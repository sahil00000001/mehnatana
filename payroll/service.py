"""
Salary month service: database -> engine -> PayrollLine.

engine.py is pure arithmetic. This module loads the employees, salary
structures, inputs, year-to-date figures and statutory rates for a month,
runs the engine, stores frozen lines, and handles finalise / reopen.
"""
from dataclasses import dataclass, fields as dc_fields
from datetime import date
from decimal import Decimal as D
from typing import Optional

from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from advances.models import Advance, AdvanceRecovery
from core.audit import audit
from core.formatting import add_months, fmt_date, month_end, month_label, month_start, plain_number, tax_year_of
from employees.models import Employee, OpeningBalance, SalaryRevision
from statutory.rates import rates_for_month

from . import engine
from .models import PayrollLine, PayrollRun

ZERO = D("0")
DAY_KEYS = ("present", "paid_leave", "week_off_holiday", "lop")
NUMBER_KEYS = ("present", "paid_leave", "week_off_holiday", "lop", "ot_hours", "ot_amount", "incentive",
               "increment", "advance", "other_deduction", "tds_override")


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
            tds_override=dec(line.tds_override), remarks=line.remarks,
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


def open_advances(company, employee_ids):
    """{employee_id: [(advance, outstanding), ...]} oldest first."""
    out = {pk: [] for pk in employee_ids}
    qs = (
        Advance.objects.filter(company=company, employee_id__in=employee_ids, status=Advance.Status.OPEN)
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
        self.advances = open_advances(company, ids)

    def outstanding(self, emp):
        return sum((left for _a, left in self.advances.get(emp.pk, [])), ZERO)

    def suggested_advance(self, emp):
        return suggested_advance(self.advances.get(emp.pk, []))


# ---------------------------------------------------------------- calculate one row
def compute(ctx, emp, row):
    """Run the engine for one employee. Returns (engine.Line, structure used, warnings). Raises ValueError."""
    structure = structure_for_month(emp, ctx.month)
    result = engine.compute_line(
        ctx.month.year, ctx.month.month, engine_employee(emp), structure,
        esi_structure(emp, ctx.month, row.increment), row.to_engine(), ctx.settings, ctx.rates,
        ctx.ytd.get(emp.pk, engine.YTD()),
    )
    used = structure.plus_increment(row.increment) if row.increment else structure
    warnings = list(result.warnings)
    start, end = ctx.month, month_end(ctx.month)
    if ctx.settings.pf_enabled and emp.pf_member and result.epf_wage > 0 and not emp.uan:
        warnings.append("NO_UAN")
    if result.esi_covered and not emp.esic_ip_no:
        warnings.append("NO_ESIC_NUMBER")
    if start <= emp.date_of_joining <= end:
        warnings.append("JOINED_THIS_MONTH")
    if emp.date_of_leaving and start <= emp.date_of_leaving <= end:
        warnings.append("LEFT_THIS_MONTH")
    if row.increment and row.increment > structure.gross() / 2:
        warnings.append("INCREMENT_LARGE")
    if result.paid_days == 0:
        warnings.append("ZERO_PAY")
    for code in ctx.month_rates.warnings:
        if code == "TAX_SLABS_NOT_UPDATED" and not ctx.settings.tds_enabled:
            continue
        warnings.append(code)
    return result, used, list(dict.fromkeys(warnings))


def check_row(ctx, emp, row, days_in_month_value=None):
    """Mistakes that block saving, as [(column key, message)]. Empty list = fine."""
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
                       % {"a": plain_number(row.advance), "o": plain_number(outstanding)}))
    if row.other_deduction > 0 and not row.other_reason.strip():
        errors.append(("other_reason", _("Write the reason for the other deduction.")))
    if errors:
        return errors
    try:
        compute(ctx, emp, row)
    except ValueError as exc:
        if "deductions are more than earnings" in str(exc):
            errors.append(("advance", _("Deductions are more than the salary earned this month. Reduce the advance or other deduction.")))
        else:
            errors.append(("present", str(exc)))
    except PayrollError as exc:
        errors.append(("present", str(exc)))
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
    line.company = ctx.company
    line.employee = emp
    line.emp_snapshot = employee_snapshot(emp)
    line.days_in_month = engine.days_in_month(ctx.month.year, ctx.month.month)
    line.present, line.paid_leave, line.week_off_holiday = row.present, row.paid_leave, row.week_off_holiday
    line.lop_days = row.lop
    line.ot_hours, line.ot_amount_override = row.ot_hours, row.ot_amount
    line.incentive, line.increment = row.incentive, row.increment
    line.advance_deduction, line.other_deduction = row.advance, row.other_deduction
    line.other_deduction_reason = row.other_reason.strip()
    line.tds_override, line.remarks = row.tds_override, row.remarks.strip()
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


def recompute_run(run, ctx=None):
    """Calculate every line of a draft month again from its stored inputs."""
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
        raise PayrollError(_("These employees are missing from %(month)s: %(names)s. Upload the sheet again.") % {
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
        errors = check_row(ctx, emp, row)
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
    earlier = earlier_open_month(company, month)
    if earlier:
        return _("Finalise %(month)s first. Months are done in order.") % {"month": month_label(earlier)}
    run = PayrollRun.objects.filter(company=company, month=month_start(month)).first()
    if run and run.is_final:
        return _("This month is finalised. Reopen it to make changes.")
    return ""


def finalise_blockers(run):
    if run.is_final:
        return [_("This month is already finalised.")]
    earlier = earlier_open_month(run.company, run.month)
    if earlier:
        return [_("Finalise %(month)s first. Months are finalised in order.") % {"month": month_label(earlier)}]
    if not run.lines.exists():
        return [_("Upload the attendance sheet first.")]
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
                SalaryRevision.objects.create(
                    company=run.company, employee=line.employee, effective_from=run.month,
                    basic=line.rate_basic, da=line.rate_da, hra=line.rate_hra, conveyance=line.rate_conveyance,
                    other_allowance=line.rate_other, source=SalaryRevision.Source.MONTHLY_SHEET, payroll_run=run,
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


# ---------------------------------------------------------------- helpers for other screens
def esi_covered_now(emp, company, month_rates, month):
    if not company.esi_enabled:
        return False
    try:
        return engine.esi_covered(engine_employee(emp), esi_structure(emp, month), month_rates.rates)
    except PayrollError:
        return False
