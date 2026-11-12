"""Small rules about employees that need the statutory tables."""
import re

from core.formatting import month_start
from payroll import engine
from statutory.rates import value_on


def default_eps_member(date_of_birth, date_of_joining, structure):
    """README §4.4: EPS yes if PF wages at joining are within the PF ceiling and age is under 58."""
    ceiling = value_on("PF_WAGE_CEILING", date_of_joining)
    min_pct = value_on("CODE_WAGE_MIN_PCT", date_of_joining) or 0
    max_age = value_on("EPS_MAX_AGE", date_of_joining) or 58
    if ceiling is None:
        return True
    wages = engine.code_wages(structure.wage_part(), structure.gross(), min_pct)
    young = date_of_birth is None or engine.age_on(date_of_birth, date_of_joining) < int(max_age)
    return engine.rupee(wages) <= ceiling and young


def first_revision_month(company, date_of_joining):
    """The first salary structure starts in the joining month, or the first app month if they joined earlier."""
    return max(month_start(date_of_joining), company.payroll_start_month)


def next_emp_code(company):
    from .models import Employee

    numbers = []
    for code in Employee.objects.filter(company=company).values_list("emp_code", flat=True):
        match = re.fullmatch(r"E(\d+)", code)
        if match:
            numbers.append(int(match.group(1)))
    return f"E{(max(numbers) + 1) if numbers else 1:03d}"


def code_locked(employee):
    """Emp code cannot change once a finalised month contains the employee."""
    return employee.payroll_lines.filter(run__status="FINALISED").exists()


def finalised_after(employee, month):
    """Finalised months after `month` in which this employee was paid."""
    return employee.payroll_lines.filter(run__status="FINALISED", run__month__gt=month_start(month))


def finalised_month_after(company, day):
    """The first finalised salary month after the month of `day`, or None (blocks Undo relieve)."""
    from payroll.models import PayrollRun

    return (PayrollRun.objects.filter(company=company, status=PayrollRun.Status.FINALISED,
                                      month__gt=month_start(day)).order_by("month").first())
