"""
Payroll engine (pure Python, Decimal only). Port of README Appendix A.

Rules for this file:
- No Django imports and no database access. payroll/service.py loads the
  employee, structure, inputs, YTD and rates from the database, builds these
  dataclasses and stores the returned Line.
- Appendix A's module constants PT_SLABS, NEW_REGIME and OLD_REGIME are not
  here: the same values arrive in Rates.pt_slabs / Rates.tax_new /
  Rates.tax_old, loaded from the PTSlab and TaxConfig tables (seeded with
  exactly the Appendix A numbers). The results are unchanged.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR
from typing import Optional

ENGINE_VERSION = "1.1"  # 1.1: salary per working day (weekly offs) as a divisor

ZERO = D("0")
HUNDRED = D("100")


# ---------------------------------------------------------------- rounding
def rupee(x) -> D:
    """Nearest rupee, 0.50 goes up (PF, EPS, earnings, PT, TDS, net pay)."""
    return D(x).quantize(D("1"), rounding=ROUND_HALF_UP)


def rupee_up(x) -> D:
    """Next higher rupee (ESI shares - each share separately)."""
    return D(x).quantize(D("1"), rounding=ROUND_CEILING)


def pct(amount: D, rate: D) -> D:
    return amount * rate / HUNDRED


# ---------------------------------------------------------------- inputs
@dataclass
class Rates:
    """Statutory values in force on the LAST day of the payroll month."""
    pf_ceiling: D = D("25000")          # from 17-09-2026 (15000 before)
    pf_er_rate: D = D("12")
    eps_rate: D = D("8.33")
    edli_rate: D = D("0.50")
    pf_admin_rate: D = D("0.50")
    pf_admin_min: D = D("500")          # per establishment per month
    eps_max_age: int = 58
    esi_ee_rate: D = D("0.75")
    esi_er_rate: D = D("3.25")
    esi_ceiling: D = D("21000")
    esi_ceiling_pwd: D = D("25000")
    esi_ee_exempt_daily: D = D("176")
    esi_on_code_wages: bool = True      # False = old practice (gross)
    code_wage_min_pct: D = D("50")
    # From PTSlab: (state, gender, from_inclusive, to_inclusive or None, amount, february_amount)
    pt_slabs: list = field(default_factory=list)
    # From TaxConfig, one dict per regime with the keys of Appendix A's NEW_REGIME / OLD_REGIME
    tax_new: dict = field(default_factory=dict)
    tax_old: dict = field(default_factory=dict)


@dataclass
class Settings:
    salary_divisor: str = "CALENDAR"    # CALENDAR | FIXED_30 | FIXED_26 | WORKING
    pf_enabled: bool = True
    pf_ee_rate: D = D("12")
    pf_restrict_to_ceiling: bool = True
    esi_enabled: bool = True
    pt_enabled: bool = True
    pt_state: str = "KA"
    tds_enabled: bool = True
    ot_multiplier: D = D("2")
    ot_days_divisor: D = D("26")
    ot_hours_per_day: D = D("8")
    weekly_off: tuple = (6,)            # weekday numbers the business is closed (Monday = 0); (6,) = six-day week


@dataclass
class Structure:
    basic: D
    da: D = ZERO
    hra: D = ZERO
    conveyance: D = ZERO
    other: D = ZERO

    def wage_part(self) -> D:           # counts as "wages" under the Codes
        return self.basic + self.da + self.other

    def gross(self) -> D:
        return self.basic + self.da + self.hra + self.conveyance + self.other

    def plus_increment(self, inc: D) -> "Structure":
        return Structure(self.basic + inc, self.da, self.hra, self.conveyance, self.other)


@dataclass
class Employee:
    code: str
    name: str
    gender: str                          # M / F / O
    dob: date
    doj: date
    dol: Optional[date] = None
    pf_member: bool = True
    eps_member: bool = True
    esi_override: str = "AUTO"           # AUTO / YES / NO
    is_pwd: bool = False
    pt_exempt: bool = False
    tax_regime: str = "NEW"              # NEW / OLD
    old_regime_deductions: D = ZERO      # per year, entered by owner/CA


@dataclass
class MonthInput:
    present: D
    paid_leave: D = ZERO
    week_off_holiday: D = ZERO
    lop: Optional[D] = None
    ot_hours: D = ZERO
    ot_amount: Optional[D] = None
    incentive: D = ZERO
    increment: D = ZERO
    advance: D = ZERO
    other_deduction: D = ZERO
    tds_override: Optional[D] = None


@dataclass
class YTD:
    """Tax-year-to-date BEFORE this month: finalised months + opening balance."""
    gross: D = ZERO
    tds: D = ZERO
    pt: D = ZERO


# ---------------------------------------------------------------- helpers
def days_in_month(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def weekly_offs_between(start: date, end: date, weekly_off) -> int:
    """How many weekly off days fall from `start` to `end` (both included)."""
    if end < start:
        return 0
    count = 0
    for weekday in set(weekly_off):
        first = start + timedelta(days=(weekday - start.weekday()) % 7)
        if first <= end:
            count += (end - first).days // 7 + 1
    return count


def working_days(y: int, m: int, weekly_off) -> int:
    """Days in the month minus weekly offs: February, March and April each have their own count."""
    dim = days_in_month(y, m)
    return dim - weekly_offs_between(date(y, m, 1), date(y, m, dim), weekly_off)


def employed_span(y: int, m: int, doj: date, dol: Optional[date]):
    """First and last day of the month the person is employed (None, None if not at all)."""
    first, last = date(y, m, 1), date(y, m, days_in_month(y, m))
    start = max(first, doj)
    end = min(last, dol) if dol else last
    return (start, end) if start <= end else (None, None)


def working_days_employed(y: int, m: int, doj: date, dol: Optional[date], weekly_off) -> int:
    """Working days of the month on which the person was employed (joiners and leavers get fewer)."""
    start, end = employed_span(y, m, doj, dol)
    if start is None:
        return 0
    return (end - start).days + 1 - weekly_offs_between(start, end, weekly_off)


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def code_wages(wage_part: D, total: D, min_pct: D) -> D:
    """Labour Code wages: wages part, but never less than 50% of all pay.

    This is the one place to change if the 50% test should also count employer
    PF inside "total remuneration" (see DECISIONS.md); v1 uses gross pay only.
    """
    return max(wage_part, pct(total, min_pct))


def esi_covered(emp: Employee, structure_at_period_start: Structure, rates: Rates) -> bool:
    """Coverage fixed at start of contribution period (Apr-Sep / Oct-Mar) or at joining."""
    if emp.esi_override == "YES":
        return True
    if emp.esi_override == "NO":
        return False
    s = structure_at_period_start
    full = code_wages(s.wage_part(), s.gross(), rates.code_wage_min_pct) if rates.esi_on_code_wages else s.gross()
    ceiling = rates.esi_ceiling_pwd if emp.is_pwd else rates.esi_ceiling
    return rupee(full) <= ceiling


def professional_tax(state: str, gender: str, gross: D, month: int, slabs) -> D:
    for st, g, lo, hi, amt, feb in slabs:
        if st != state or (g != "ANY" and g != gender):
            continue
        if gross >= lo and (hi is None or gross <= hi):
            return feb if month == 2 else amt
    return ZERO


def _slab_tax(income: D, slabs) -> D:
    tax, lower = ZERO, ZERO
    for upper, rate in slabs:
        if upper is None or income <= upper:
            tax += pct(income - lower, rate)
            return tax
        tax += pct(upper - lower, rate)
        lower = upper
    return tax


def _old_slabs_for_age(age: int, cfg: dict):
    """Old regime: the nil band grows to 3 L at 60+ and 5 L at 80+.

    NOTE: Appendix A writes the senior slab lists out by hand. They are built
    here from the configured slabs and exemptions instead (bands that end inside
    the larger nil band are dropped); with the seeded TaxConfig the lists are
    identical: 60+ -> 3L/5L/10L/above, 80+ -> 5L/10L/above.
    """
    if age >= 80:
        exemption = cfg.get("super_senior_exemption")
    elif age >= 60:
        exemption = cfg.get("senior_exemption")
    else:
        exemption = None
    if not exemption:
        return cfg["slabs"]
    slabs = [(exemption, ZERO)]
    for upper, rate in cfg["slabs"]:
        if upper is None or upper > exemption:
            slabs.append((upper, rate))
    return slabs


def annual_income_tax(taxable: D, regime: str, age: int, cfg: dict) -> D:
    slabs = cfg["slabs"] if regime == "NEW" else _old_slabs_for_age(age, cfg)
    base = _slab_tax(taxable, slabs)
    # rebate + marginal relief at the rebate limit
    if taxable <= cfg["rebate_limit"]:
        base = max(ZERO, base - cfg["rebate_max"])
    elif regime == "NEW":
        base = min(base, taxable - cfg["rebate_limit"])
    # surcharge with marginal relief
    sc = ZERO
    for threshold, rate in reversed(cfg["surcharge"]):
        if taxable > threshold:
            sc = pct(base, rate)
            # relief: tax+surcharge may not exceed tax at threshold (+its surcharge) + excess income
            lower_rate = ZERO
            for t2, r2 in cfg["surcharge"]:
                if t2 < threshold:
                    lower_rate = r2
            at_threshold = _slab_tax(threshold, slabs)
            at_threshold += pct(at_threshold, lower_rate)
            sc = min(sc, at_threshold + (taxable - threshold) - base)
            sc = max(sc, ZERO)
            break
    return rupee((base + sc) * (1 + cfg["cess"] / HUNDRED))


def months_left_in_tax_year(month: int) -> int:
    """Including the current month. April -> 12 ... March -> 1."""
    return 12 - ((month - 4) % 12)


def tax_year_end(year: int, month: int) -> date:
    return date(year + 1, 3, 31) if month >= 4 else date(year, 3, 31)


def future_months(year: int, month: int, count: int):
    out, y, m = [], year, month
    for _ in range(count):
        m += 1
        if m == 13:
            m, y = 1, y + 1
        out.append((y, m))
    return out


# ---------------------------------------------------------------- one line
@dataclass
class Line:
    code: str
    name: str
    paid_days: D = ZERO
    lop: D = ZERO
    ncp_days: int = 0
    basic: D = ZERO
    da: D = ZERO
    hra: D = ZERO
    conveyance: D = ZERO
    other: D = ZERO
    ot_pay: D = ZERO
    incentive: D = ZERO
    gross: D = ZERO
    code_wages: D = ZERO
    epf_wage: D = ZERO
    eps_wage: D = ZERO
    edli_wage: D = ZERO
    ee_pf: D = ZERO
    er_eps: D = ZERO
    er_epf: D = ZERO
    edli: D = ZERO
    pf_admin: D = ZERO
    esi_covered: bool = False
    esi_wage: D = ZERO
    ee_esi: D = ZERO
    er_esi: D = ZERO
    pt: D = ZERO
    tds: D = ZERO
    advance: D = ZERO
    other_deduction: D = ZERO
    total_deductions: D = ZERO
    net_pay: D = ZERO
    employer_cost: D = ZERO
    warnings: list = field(default_factory=list)


def compute_line(year: int, month: int, emp: Employee, structure: Structure,
                 structure_at_esi_period_start: Structure, inp: MonthInput,
                 settings: Settings, rates: Rates, ytd: YTD) -> Line:
    L = Line(emp.code, emp.name)
    dim = days_in_month(year, month)
    month_start, month_end = date(year, month, 1), date(year, month, dim)

    # 1. salary for this month (increment applies from this month)
    s = structure.plus_increment(inp.increment) if inp.increment else structure

    # 2. days
    counted = inp.present + inp.paid_leave + inp.week_off_holiday
    lop = inp.lop if inp.lop is not None else max(ZERO, D(dim) - counted)
    if counted + lop != D(dim):
        raise ValueError(f"{emp.code}: days do not add up to {dim}")
    L.lop = lop
    L.paid_days = D(dim) - lop

    # days outside employment (before joining, after leaving)
    not_employed = 0
    if emp.doj > month_start:
        not_employed += (emp.doj - month_start).days
    if emp.dol and emp.dol < month_end:
        not_employed += (month_end - emp.dol).days

    if settings.salary_divisor == "WORKING":
        # Salary per working day of this month. Unpaid days while employed are working days;
        # before joining / after leaving only the working days count (weekly offs are not cut twice).
        wd = working_days(year, month, settings.weekly_off)
        outside = wd - working_days_employed(year, month, emp.doj, emp.dol, settings.weekly_off)
        lop_working = min(D(wd), max(ZERO, lop - not_employed) + outside)
        factor = max(ZERO, 1 - lop_working / D(wd)) if wd else ZERO
    else:
        divisor = {"CALENDAR": D(dim), "FIXED_30": D(30), "FIXED_26": D(26)}[settings.salary_divisor]
        factor = max(ZERO, 1 - lop / divisor)

    # days outside employment are not NCP days for EPFO
    L.ncp_days = int(max(ZERO, lop - not_employed).to_integral_value(rounding=ROUND_FLOOR))

    # 3. earnings
    L.basic, L.da, L.hra = rupee(s.basic * factor), rupee(s.da * factor), rupee(s.hra * factor)
    L.conveyance, L.other = rupee(s.conveyance * factor), rupee(s.other * factor)
    if inp.ot_amount is not None:
        L.ot_pay = rupee(inp.ot_amount)
    elif inp.ot_hours:
        hourly = s.wage_part() / settings.ot_days_divisor / settings.ot_hours_per_day
        L.ot_pay = rupee(hourly * settings.ot_multiplier * inp.ot_hours)
    L.incentive = rupee(inp.incentive)
    wage_part = L.basic + L.da + L.other
    L.gross = wage_part + L.hra + L.conveyance + L.ot_pay + L.incentive

    # 4. Labour Code wages (50% rule)
    L.code_wages = rupee(code_wages(wage_part, L.gross, rates.code_wage_min_pct))
    if s.wage_part() < pct(s.gross(), rates.code_wage_min_pct):
        L.warnings.append("STRUCTURE_BELOW_50")

    # 5. PF
    if settings.pf_enabled and emp.pf_member and L.code_wages > 0:
        cap = rates.pf_ceiling
        L.epf_wage = min(L.code_wages, cap) if settings.pf_restrict_to_ceiling else L.code_wages
        # No date of birth on record (brought over from an older system): EPS follows the eps_member tick.
        too_old = emp.dob is not None and age_on(emp.dob, month_start) >= rates.eps_max_age
        if too_old and emp.eps_member:
            L.warnings.append("EPS_STOPPED_AGE_58")
        L.eps_wage = min(L.code_wages, cap) if (emp.eps_member and not too_old) else ZERO
        L.edli_wage = min(L.code_wages, cap)
        L.ee_pf = rupee(pct(L.epf_wage, settings.pf_ee_rate))
        er_total = rupee(pct(L.epf_wage, rates.pf_er_rate))
        L.er_eps = rupee(pct(L.eps_wage, rates.eps_rate))
        L.er_epf = er_total - L.er_eps
        L.edli = rupee(pct(L.edli_wage, rates.edli_rate))
        L.pf_admin = rupee(pct(L.epf_wage, rates.pf_admin_rate))

    # 6. ESI
    if settings.esi_enabled:
        L.esi_covered = esi_covered(emp, structure_at_esi_period_start, rates)
        if L.esi_covered:
            L.esi_wage = L.code_wages if rates.esi_on_code_wages else L.gross
            L.er_esi = rupee_up(pct(L.esi_wage, rates.esi_er_rate))
            daily = (L.esi_wage / L.paid_days) if L.paid_days else ZERO
            if L.esi_wage > 0 and daily <= rates.esi_ee_exempt_daily:
                L.warnings.append("ESI_EMPLOYEE_SHARE_WAIVED")
            elif L.esi_wage > 0:
                L.ee_esi = rupee_up(pct(L.esi_wage, rates.esi_ee_rate))

    # 7. Professional tax (on gross earned this month)
    if settings.pt_enabled and not emp.pt_exempt:
        L.pt = professional_tax(settings.pt_state, emp.gender, L.gross, month, rates.pt_slabs)

    # 8. TDS (projection over the tax year, spread evenly over months left)
    if inp.tds_override is not None:
        L.tds = rupee(inp.tds_override)
    elif settings.tds_enabled:
        left = months_left_in_tax_year(month)
        if emp.dol and emp.dol <= month_end:
            # NOTE: Appendix A always projects salary until March. Someone who leaves this month
            # earns nothing after it, so the year's tax is worked out on actual pay and settled now
            # (otherwise a leaver is over-taxed in the final pay). README cases are unchanged.
            left = 1
        later = future_months(year, month, left - 1)
        projected = ytd.gross + L.gross + s.gross() * (left - 1)
        if emp.tax_regime == "NEW":
            cfg = rates.tax_new
            taxable = projected - min(cfg["std_deduction"], projected)
        else:
            cfg = rates.tax_old
            pt_later = sum((professional_tax(settings.pt_state, emp.gender, s.gross(), m, rates.pt_slabs)
                            for _, m in later), ZERO) if settings.pt_enabled and not emp.pt_exempt else ZERO
            pt_year = ytd.pt + L.pt + pt_later
            taxable = projected - min(cfg["std_deduction"], projected) - pt_year - emp.old_regime_deductions
        taxable = max(ZERO, taxable)
        age = age_on(emp.dob, tax_year_end(year, month)) if emp.dob else 0  # unknown: the normal (under 60) slabs
        annual = annual_income_tax(taxable, emp.tax_regime, age, cfg)
        L.tds = max(ZERO, rupee((annual - ytd.tds) / left))

    # 9. other deductions and net
    L.advance, L.other_deduction = rupee(inp.advance), rupee(inp.other_deduction)
    L.total_deductions = L.ee_pf + L.ee_esi + L.pt + L.tds + L.advance + L.other_deduction
    L.net_pay = L.gross - L.total_deductions
    if L.net_pay < 0:
        raise ValueError(f"{emp.code}: deductions are more than earnings")
    if L.total_deductions > pct(L.gross, D("50")):
        L.warnings.append("DEDUCTIONS_ABOVE_50")
    L.employer_cost = L.gross + L.er_eps + L.er_epf + L.edli + L.pf_admin + L.er_esi
    return L


def establishment_pf_admin(lines, rates: Rates) -> D:
    """PF admin charges for the whole establishment: 0.5% of EPF wages, minimum Rs 500."""
    total = sum((l.pf_admin for l in lines), ZERO)
    has_member = any(l.epf_wage > 0 for l in lines)
    return max(total, rates.pf_admin_min) if has_member else ZERO


def ecr_line(uan: str, name_as_per_uan: str, l: Line) -> str:
    f = [uan, name_as_per_uan, l.gross, l.epf_wage, l.eps_wage, l.edli_wage,
         l.ee_pf, l.er_eps, l.er_epf, l.ncp_days, 0]
    return "#~#".join(str(int(x)) if isinstance(x, D) else str(x) for x in f)


# ---------------------------------------------------------------- words
ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")


def _three(n: int) -> str:
    h, rest = divmod(n, 100)
    parts = []
    if h:
        parts.append(ONES[h] + " Hundred")
    if rest:
        parts.append(_two(rest))
    return " ".join(parts)


def amount_in_words(amount: D) -> str:
    n = int(rupee(amount))
    if n == 0:
        return "Rupees Zero Only"
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    parts = []
    if crore:
        parts.append((amount_in_words(D(crore))[7:-5] if crore >= 100 else _two(crore)) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if n:
        parts.append(_three(n))
    return "Rupees " + " ".join(parts) + " Only"


def indian_format(n) -> str:
    n = int(rupee(n))
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups) + "," + tail
