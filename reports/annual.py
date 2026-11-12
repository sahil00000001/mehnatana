"""
Annual salary report (annual return) for one financial year, April to March.

For each employee: salary payable for the year, paid to date, pending, attendance
(working days, present, paid days, LOP) and a month-by-month breakdown; and the
consolidated figures of the company month by month. Read from the stored salary
lines only — nothing is recalculated. Draft months are included and marked, because
their salary is payable too; it can be marked paid only after finalising.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal as D

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.formatting import add_months, month_short, tax_year_label
from core.tenancy import company_required, page
from excel_io.common import add_table_sheet, as_number, table_workbook, xlsx_response
from payroll import engine
from payroll.models import PayrollLine, PayrollRun

from . import queries

ZERO = D("0")

SHOW = {
    "net": gettext_lazy("In-hand pay (net)"),
    "gross": gettext_lazy("Gross salary"),
    "paid": gettext_lazy("Paid"),
    "days": gettext_lazy("Paid days"),
}
SHOW_FIELD = {"net": "net_pay", "gross": "gross", "paid": "paid_amount", "days": "paid_days"}
PERSON_SUMS = ["present", "paid_leave", "week_off_holiday", "lop", "paid_days", "gross", "total_deductions", "net_pay",
               "paid_amount"]
MONTH_SUMS = ["gross", "ee_pf", "ee_esi", "pt", "tds", "advance", "other_deduction_amt", "total_deductions", "net_pay",
              "paid_amount", "employer_cost"]


def _n(value):
    return as_number(value)


def year_figures(company, tax_year):
    start = date(tax_year, 4, 1)
    months = [add_months(start, i) for i in range(12)]
    runs = {r.month: r for r in PayrollRun.objects.filter(company=company, month__gte=start, month__lte=months[-1])}
    lines = list(PayrollLine.objects.filter(company=company, run__in=list(runs.values()))
                 .select_related("run", "employee").order_by("employee__emp_code", "run__month"))
    weekly_off = company.weekly_off

    people, by_run = {}, defaultdict(list)
    for line in lines:
        by_run[line.run.month].append(line)
        emp = line.employee
        p = people.setdefault(line.employee_id, {
            "code": line.code, "name": line.name, "doj": emp.date_of_joining, "dol": emp.date_of_leaving,
            "lines": {}, "working_days": 0, **{f: ZERO for f in PERSON_SUMS}, "pending": ZERO})
        p["lines"][line.run.month] = line
        p["working_days"] += engine.working_days_employed(line.run.month.year, line.run.month.month,
                                                          emp.date_of_joining, emp.date_of_leaving, weekly_off)
        for f in PERSON_SUMS:
            p[f] += getattr(line, f)
        p["pending"] += line.pending
    rows = sorted(people.values(), key=lambda p: p["code"])
    for p in rows:
        p["months_count"] = len(p["lines"])

    month_rows = []
    for m in months:
        run = runs.get(m)
        month_lines = by_run.get(m, [])
        if run is None or not month_lines:
            continue
        sums = {f: sum((getattr(l, f) for l in month_lines), ZERO) for f in MONTH_SUMS}
        working = month_lines[0].working_days
        month_rows.append({
            "month": m, "run": run, "staff": len(month_lines),
            "working_days": working if working is not None else engine.working_days(m.year, m.month, weekly_off),
            "pending": sum((l.pending for l in month_lines), ZERO),
            "company_cost": sums["employer_cost"] + run.pf_admin_topup, **sums})
    totals = {f: sum((r[f] for r in month_rows), ZERO)
              for f in MONTH_SUMS + ["pending", "company_cost"]}
    totals["working_days"] = sum(r["working_days"] for r in month_rows)
    person_totals = {f: sum((p[f] for p in rows), ZERO) for f in PERSON_SUMS + ["pending"]}
    person_totals["working_days"] = sum(p["working_days"] for p in rows)
    drafts = [r["month"] for r in month_rows if not r["run"].is_final]
    return {"months": months, "runs": runs, "rows": rows, "month_rows": month_rows, "totals": totals,
            "person_totals": person_totals, "drafts": drafts}


def _cells(p, months, show):
    field = SHOW_FIELD[show]
    return [getattr(p["lines"][m], field) if m in p["lines"] else None for m in months]


def _workbook(company, tax_year, data):
    months, rows = data["months"], data["rows"]
    pt = data["person_totals"]
    headers = ["Emp No.", "Name", "Date of Joining", "Months", "Working Days", "Present", "Paid Leave",
               "Weekly Off + Holidays", "LOP", "Paid Days", "Gross Salary", "Deductions", "Net Payable", "Paid",
               "Pending"]
    money = ["Gross Salary", "Deductions", "Net Payable", "Paid", "Pending"]
    body = [[p["code"], p["name"], p["doj"], p["months_count"], p["working_days"]]
            + [_n(p[f]) for f in PERSON_SUMS[:5]]
            + [_n(p["gross"]), _n(p["total_deductions"]), _n(p["net_pay"]), _n(p["paid_amount"]), _n(p["pending"])]
            for p in rows]
    total = (["", "Total", "", "", pt["working_days"]] + [_n(pt[f]) for f in PERSON_SUMS[:5]]
             + [_n(pt["gross"]), _n(pt["total_deductions"]), _n(pt["net_pay"]), _n(pt["paid_amount"]),
                _n(pt["pending"])])
    wb = table_workbook(f"Summary {tax_year_label(tax_year)}", headers, body, money_columns=money, totals=total,
                        widths={"Name": 24, "Date of Joining": 14})
    for row in wb.active.iter_rows(min_row=2, max_row=len(body) + 1):
        row[2].number_format = "DD-MM-YYYY"

    month_headers = [month_short(m) for m in months]
    for show, title in (("net", "Net pay by month"), ("gross", "Gross by month"), ("paid", "Paid by month"),
                        ("days", "Paid days by month")):
        cells_by_person = [_cells(p, months, show) for p in rows]
        sheet_rows = [[p["code"], p["name"]] + [_n(c) for c in cells]
                      + [_n(sum((c for c in cells if c is not None), ZERO))]
                      for p, cells in zip(rows, cells_by_person)]
        col_totals = [sum((cells[i] for cells in cells_by_person if cells[i] is not None), ZERO)
                      for i in range(len(months))]
        add_table_sheet(wb, title, ["Emp No.", "Name"] + month_headers + ["Total"], sheet_rows,
                        money_columns=[] if show == "days" else month_headers + ["Total"],
                        totals=["", "Total"] + [_n(t) for t in col_totals] + [_n(sum(col_totals, ZERO))],
                        widths={"Name": 24})

    company_headers = ["Month", "Status", "Staff", "Working Days", "Gross", "PF", "ESI", "PT", "TDS", "Advance",
                       "Other Deductions", "Total Deductions", "Net Payable", "Paid", "Pending", "Company Cost"]
    t = data["totals"]
    company_rows = [[month_short(r["month"]), r["run"].get_status_display(), r["staff"], r["working_days"]]
                    + [_n(r[f]) for f in ("gross", "ee_pf", "ee_esi", "pt", "tds", "advance", "other_deduction_amt",
                                          "total_deductions", "net_pay", "paid_amount", "pending", "company_cost")]
                    for r in data["month_rows"]]
    add_table_sheet(wb, "Company by month", company_headers, company_rows, money_columns=company_headers[4:],
                    totals=["Total", "", "", t["working_days"]]
                    + [_n(t[f]) for f in ("gross", "ee_pf", "ee_esi", "pt", "tds", "advance", "other_deduction_amt",
                                          "total_deductions", "net_pay", "paid_amount", "pending", "company_cost")])
    return wb


@company_required
def annual(request, tax_year):
    company = request.company
    data = year_figures(company, tax_year)
    if request.GET.get("format") == "xlsx":
        return xlsx_response(_workbook(company, tax_year, data),
                             f"{company.code}_annual_salary_{tax_year_label(tax_year)}.xlsx")
    show = request.GET.get("show") if request.GET.get("show") in SHOW else "net"
    months = data["months"]
    for p in data["rows"]:
        p["cells"] = _cells(p, months, show)
        p["cells_total"] = sum((c for c in p["cells"] if c is not None), ZERO)
    month_totals = [sum((p["cells"][i] for p in data["rows"] if p["cells"][i] is not None), ZERO)
                    for i in range(len(months))]
    return page(
        request, "reports/annual.html",
        _("Annual salary report — %(year)s") % {"year": tax_year_label(tax_year)},
        _("Salary payable, paid and pending for every employee from April to March, with attendance and a "
          "month-by-month breakdown. Mark payments on each month's Salary payments page."),
        tax_year=tax_year, tax_years=queries.tax_year_choices(company, timezone.localdate()),
        show=show, show_choices=SHOW.items(), month_totals=month_totals,
        month_totals_sum=sum(month_totals, ZERO), draft_months=set(data["drafts"]), **data,
    )
