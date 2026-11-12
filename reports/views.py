"""
Home dashboard, month documents (payslips, register, bank sheet, cash list,
PF ECR, ESI file) and reports. Everything reads the stored PayrollLine rows —
nothing here recalculates salary.
"""
from datetime import date, datetime
from urllib.parse import urlencode
from decimal import ROUND_FLOOR, Decimal as D

from django.contrib import messages
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from advances.models import Advance
from core.formatting import add_months, fmt_date, month_label, month_short, tax_year_label, tax_year_of
from core.tenancy import NoSalaryMonth, company_get, company_required, page
from employees.models import Employee, OpeningBalance, SalaryRevision
from excel_io.common import as_number, table_workbook, xlsx_response
from payroll import engine, service
from payroll.models import PayrollLine, PayrollRun

from core.notices import notices_for

from . import payslip as payslip_design
from . import queries
from .fixes import fix_querysets

ZERO = D("0")


# ---------------------------------------------------------------- helpers
def _run(request, month):
    run = PayrollRun.objects.filter(company=request.company, month=month).first()
    if run is None:
        raise NoSalaryMonth(month)
    return run


def _lines(run):
    return list(run.lines.select_related("employee").order_by("employee__emp_code"))


def _wants_xlsx(request):
    return request.GET.get("format") == "xlsx"


def _files_need_final(request, run):
    """Government and bank files are made only from finalised figures."""
    if run.is_final:
        return None
    messages.warning(request, _("Files for %(month)s can be downloaded after the month is finalised.")
                     % {"month": run.label})
    return redirect("payroll:month", month=run.month)


def _files_phrase(company):
    """'bank, PF and ESI files', without the parts this business does not have."""
    if company.pf_enabled and company.esi_enabled:
        return _("bank, PF and ESI files")
    if company.pf_enabled:
        return _("bank and PF files")
    if company.esi_enabled:
        return _("bank and ESI files")
    return _("bank sheet")


def _month_param(value):
    try:
        year, month = str(value).split("-")
        return date(int(year), int(month), 1)
    except (TypeError, ValueError):
        return None


def _date_param(value):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _date_range(request):
    """?from=&to= (YYYY-MM-DD or DD-MM-YYYY); default = the current tax year."""
    year = tax_year_of(timezone.localdate())
    start = _date_param(request.GET.get("from")) or date(year, 4, 1)
    end = _date_param(request.GET.get("to")) or date(year + 1, 3, 31)
    if end < start:
        start, end = end, start
    return start, end


def _n(value):
    return as_number(value)


def _print_flags(run):
    return {"draft": not run.is_final, "revised": run.revision_no > 0}


# ---------------------------------------------------------------- Home
@company_required
def dashboard(request):
    company = request.company
    today = timezone.localdate()
    open_month = service.first_open_month(company)
    open_run = PayrollRun.objects.filter(company=company, month=open_month).first()
    uploaded = bool(open_run and open_run.lines.exists())
    label = month_label(open_month)
    steps = [
        {"label": _("Download the %(month)s attendance sheet") % {"month": label}, "done": uploaded},
        {"label": _("Fill days present, leave, overtime and advances in Excel"), "done": uploaded},
        {"label": _("Upload the sheet; the system checks every row"), "done": uploaded},
        {"label": _("Look at the salary table and finalise"), "done": False},
        {"label": _("Print payslips and register; download the %(files)s") % {"files": _files_phrase(company)},
         "done": False},
    ]
    if uploaded:
        next_step = {"label": _("Check and finalise %(month)s") % {"month": label},
                     "url": reverse("payroll:month", args=[open_month])}
    else:
        next_step = {"label": _("Download %(month)s sheet") % {"month": label},
                     "url": reverse("payroll:sheet", args=[open_month])}

    view = request.GET.get("view") if request.GET.get("view") in queries.COST_VIEWS else "net"
    cost_rows, largest = queries.cost_rows(company, view)
    last = queries.latest_finalised(company)
    last_totals = last.totals() if last else None
    year_so_far = queries.employer_year_so_far(company, last) if last else None

    fixes = []
    for key, (fix_label, qs) in fix_querysets(company, today).items():
        count = qs.count()
        if count:
            base = reverse("advances:list") if key == "no_instalment" else reverse("employees:list")
            fixes.append({"label": fix_label, "count": count, "url": f"{base}?fix={key}"})

    return page(
        request, "reports/dashboard.html", _("Home"),
        _("Your salary work for this month, and the figures from finalised months."),
        open_month=open_month, open_run=open_run, steps=steps, next_step=next_step,
        active_count=Employee.objects.filter(company=company, status=Employee.Status.ACTIVE).count(),
        last=last, last_totals=last_totals, year_so_far=year_so_far,
        view=view, views=queries.COST_VIEWS, cost_rows=cost_rows, largest=largest,
        home_notices=notices_for(company, today), fixes=fixes, today=today,
    )


# ---------------------------------------------------------------- payslips (Form V)
def _payslip_page(request, run, lines, single=False):
    balances = queries.advance_balances(run, lines)
    slips = [payslip_design.slip_context(line, balances.get(line.employee_id, ZERO)) for line in lines]
    two = run.company.payslip_two_per_page and not single
    groups = [slips[i:i + 2] for i in range(0, len(slips), 2)] if two else [[s] for s in slips]
    issue_date = timezone.localtime(run.finalised_at).date() if run.finalised_at else timezone.localdate()
    title = (_("Payslip — %(name)s — %(month)s") % {"name": lines[0].name, "month": run.label}) if single \
        else (_("Payslips — %(month)s") % {"month": run.label})
    return render(request, "reports/payslips.html", {
        "title": title, "run": run, "company": run.company, "groups": groups, "two_per_page": two,
        "slip_template": payslip_design.template_for(run.company),
        "issue_date": issue_date, "period_start": run.month, "period_end": run.month_end,
        "back_url": reverse("payroll:month", args=[run.month]), **_print_flags(run),
    })


@company_required
def payslips(request, month):
    run = _run(request, month)
    lines = _lines(run)
    if not lines:
        messages.warning(request, _("There are no salary lines for %(month)s yet.") % {"month": run.label})
        return redirect("payroll:month", month=run.month)
    return _payslip_page(request, run, lines)


@company_required
def payslip(request, month, line_id):
    run = _run(request, month)
    line = company_get(request, PayrollLine.objects.select_related("employee"), pk=line_id, run=run)
    return _payslip_page(request, run, [line], single=True)


# ---------------------------------------------------------------- register of wages
REGISTER_COLUMNS = [
    ("Basic", "earned_basic"), ("DA", "earned_da"), ("HRA", "earned_hra"), ("Conv.", "earned_conveyance"),
    ("Other", "earned_other"), ("OT", "ot_pay"), ("Incentive", "incentive_pay"), ("Gross", "gross"),
    ("PF", "ee_pf"), ("ESI", "ee_esi"), ("PT", "pt"), ("TDS", "tds"), ("Advance", "advance"),
    ("Other ded.", "other_deduction_amt"), ("Total ded.", "total_deductions"), ("Net", "net_pay"),
]


@company_required
def register(request, month):
    run = _run(request, month)
    lines = _lines(run)
    totals = run.totals()
    rows = [{"line": l, "values": [getattr(l, f) for _h, f in REGISTER_COLUMNS]} for l in lines]
    return render(request, "reports/register.html", {
        "title": _("Register of Wages — %(month)s — %(company)s") % {"month": run.label,
                                                                     "company": run.company.trade_name},
        "run": run, "company": run.company, "rows": rows, "headers": [h for h, _f in REGISTER_COLUMNS],
        "total_values": [totals[f] for _h, f in REGISTER_COLUMNS], "today": timezone.localdate(),
        "back_url": reverse("payroll:month", args=[run.month]), **_print_flags(run),
    })


@company_required
def register_xlsx(request, month):
    run = _run(request, month)
    blocked = _files_need_final(request, run)
    if blocked:
        return blocked
    lines = _lines(run)
    totals = run.totals()
    headers = ["Sl", "Code", "Name", "Designation", "Paid days", "LOP"] + [h for h, _f in REGISTER_COLUMNS] + ["Remarks"]
    rows = [
        [i, l.code, l.name, l.emp_snapshot.get("designation", ""), _n(l.paid_days), _n(l.lop)]
        + [_n(getattr(l, f)) for _h, f in REGISTER_COLUMNS] + [l.remarks]
        for i, l in enumerate(lines, start=1)
    ]
    total_row = ["", "", "Total", "", "", ""] + [_n(totals[f]) for _h, f in REGISTER_COLUMNS] + [""]
    wb = table_workbook(f"Register {month_short(run.month)}", headers, rows,
                        money_columns=[h for h, _f in REGISTER_COLUMNS], totals=total_row,
                        widths={"Name": 24, "Designation": 18, "Remarks": 30})
    return xlsx_response(wb, f"{run.company.code}_register_{run.month:%Y-%m}.xlsx")


# ---------------------------------------------------------------- attendance register
ATTENDANCE_DAY_FIELDS = ["present", "paid_leave", "week_off_holiday", "lop", "paid_days", "ot_hours"]


def _attendance(run):
    """Rows of the attendance register: employee details from the employee record, days from the month."""
    company = run.company
    year, month = run.month.year, run.month.month
    weekly_off = company.weekly_off
    rows, totals = [], {f: ZERO for f in ATTENDANCE_DAY_FIELDS + ["working_days"]}
    for line in _lines(run):
        emp, snap = line.employee, line.emp_snapshot
        working = engine.working_days_employed(year, month, emp.date_of_joining, emp.date_of_leaving, weekly_off)
        row = {
            "line": line, "code": line.code, "name": line.name, "designation": snap.get("designation", ""),
            "doj": emp.date_of_joining, "pan": emp.pan or snap.get("pan", ""), "uan": emp.uan or snap.get("uan", ""),
            "esi": emp.esic_ip_no or snap.get("esic_ip_no", ""), "working_days": working,
            "days": [getattr(line, f) for f in ATTENDANCE_DAY_FIELDS],
        }
        rows.append(row)
        totals["working_days"] += working
        for f in ATTENDANCE_DAY_FIELDS:
            totals[f] += getattr(line, f)
    month_working = (rows[0]["line"].working_days if rows and rows[0]["line"].working_days is not None
                     else engine.working_days(year, month, weekly_off))
    return rows, totals, month_working


@company_required
def attendance(request, month):
    run = _run(request, month)
    rows, totals, month_working = _attendance(run)
    return render(request, "reports/attendance.html", {
        "title": _("Attendance Register — %(month)s — %(company)s") % {"month": run.label,
                                                                       "company": run.company.trade_name},
        "run": run, "company": run.company, "rows": rows, "totals": totals,
        "total_days": [totals[f] for f in ATTENDANCE_DAY_FIELDS], "month_working": month_working,
        "days_in_month": engine.days_in_month(run.month.year, run.month.month), "today": timezone.localdate(),
        "back_url": reverse("payroll:month", args=[run.month]), **_print_flags(run),
    })


@company_required
def attendance_xlsx(request, month):
    run = _run(request, month)
    blocked = _files_need_final(request, run)
    if blocked:
        return blocked
    rows, totals, _month_working = _attendance(run)
    headers = ["Sl", "Emp No.", "Name", "Designation", "Date of Joining", "PAN", "UAN (PF No.)", "ESI No.",
               "Working Days", "Present", "Paid Leave", "Weekly Off + Holidays", "LOP", "Paid Days", "OT Hours"]
    data = [[i, r["code"], r["name"], r["designation"], r["doj"], r["pan"], r["uan"], r["esi"], r["working_days"]]
            + [_n(v) for v in r["days"]] for i, r in enumerate(rows, start=1)]
    total_row = ["", "", "Total", "", "", "", "", "", _n(totals["working_days"])] + [
        _n(totals[f]) for f in ATTENDANCE_DAY_FIELDS]
    wb = table_workbook(f"Attendance {month_short(run.month)}", headers, data, totals=total_row,
                        widths={"Name": 24, "Designation": 18, "Date of Joining": 14, "PAN": 13, "UAN (PF No.)": 15,
                                "ESI No.": 13, "Weekly Off + Holidays": 12})
    ws = wb.active
    for row in ws.iter_rows(min_row=2, max_row=len(data) + 1):
        row[4].number_format = "DD-MM-YYYY"
        for cell in row[5:8]:
            cell.number_format = "@"  # keep PAN, UAN and ESI numbers exactly as written
    return xlsx_response(wb, f"{run.company.code}_attendance_{run.month:%Y-%m}.xlsx")


# ---------------------------------------------------------------- paying the salary
@company_required
def bank_sheet(request, month):
    run = _run(request, month)
    blocked = _files_need_final(request, run)
    if blocked:
        return blocked
    lines = [l for l in _lines(run) if l.emp_snapshot.get("payment_mode") in ("Bank", "UPI")]
    narration = f"Salary {month_short(run.month)}"
    headers = ["Sl", "Emp Code", "Name", "Bank", "Account No", "IFSC", "Net Pay", "Narration"]
    rows = []
    for i, l in enumerate(lines, start=1):
        s = l.emp_snapshot
        rows.append([i, l.code, l.name, s.get("bank_name", ""), s.get("bank_account_no", ""), s.get("ifsc", ""),
                     _n(l.net_pay), narration])
    total = sum((l.net_pay for l in lines), ZERO)
    wb = table_workbook(f"Bank {month_short(run.month)}", headers, rows, money_columns=["Net Pay"],
                        totals=["", "", "Total", "", "", "", _n(total), ""],
                        widths={"Name": 24, "Bank": 22, "Account No": 20, "Narration": 20})
    return xlsx_response(wb, f"{run.company.code}_bank_{run.month:%Y-%m}.xlsx")


@company_required
def cash_list(request, month):
    run = _run(request, month)
    lines = [l for l in _lines(run) if l.emp_snapshot.get("payment_mode") in ("Cash", "Cheque")]
    return render(request, "reports/cash_list.html", {
        "title": _("Cash payment list — %(month)s") % {"month": run.label},
        "run": run, "company": run.company, "lines": lines,
        "total": sum((l.net_pay for l in lines), ZERO), "today": timezone.localdate(),
        "back_url": reverse("payroll:month", args=[run.month]), **_print_flags(run),
    })


# ---------------------------------------------------------------- government files
@company_required
def ecr(request, month):
    run = _run(request, month)
    blocked = _files_need_final(request, run)
    if blocked:
        return blocked
    included, _missing = queries.ecr_members(run, _lines(run))
    text = "\n".join(engine.ecr_line(uan, name, line) for uan, name, line in included)
    response = HttpResponse(text.encode("utf-8"), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="ECR_{run.company.code}_{run.month:%Y%m}.txt"'
    return response


ESI_HEADERS = ["IP Number", "IP Name", "No. of Days for which wages paid/payable", "Total Monthly Wages",
               "Reason Code for Zero working days", "Last Working Day"]


def _esi_rows(lines):
    rows = []
    for l in lines:
        s = l.emp_snapshot
        days = int(D(l.paid_days).to_integral_value(rounding=ROUND_FLOOR))
        last_day = ""
        if "LEFT_THIS_MONTH" in (l.warnings or []) and s.get("dol"):
            reason, last_day = 2, s["dol"].replace("-", "/")
        elif days == 0:
            reason = 1
        else:
            reason = 0
        rows.append([s.get("esic_ip_no", ""), l.name, days, _n(l.esi_wage), reason, last_day])
    return rows


@company_required
def esi_file(request, month):
    run = _run(request, month)
    blocked = _files_need_final(request, run)
    if blocked:
        return blocked
    included, _missing = queries.esi_members(_lines(run))
    wb = table_workbook("ESI", ESI_HEADERS, _esi_rows(included), money_columns=["Total Monthly Wages"],
                        widths={"IP Number": 14, "IP Name": 26})
    ws = wb.active
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "@"  # keep IP numbers as text
    return xlsx_response(wb, f"ESI_{run.company.code}_{run.month:%Y%m}.xlsx")


# ---------------------------------------------------------------- reports list
MONTH_REPORTS = {
    "payslips": "reports:payslips", "register": "reports:register", "attendance": "reports:attendance",
    "payments": "payroll:payments", "bank_sheet": "reports:bank_sheet",
    "cash_list": "reports:cash_list", "ecr": "reports:ecr", "esi_file": "reports:esi_file",
    "pf": "reports:pf", "esi": "reports:esi", "pt": "reports:pt",
}


@company_required
def index(request):
    company = request.company
    go = request.GET.get("go")
    if go in MONTH_REPORTS:
        month = _month_param(request.GET.get("month"))
        if month is None:
            raise Http404
        return redirect(MONTH_REPORTS[go], month=month)
    if go in ("tds", "annual"):
        try:
            year = int(request.GET.get("tax_year", ""))
        except ValueError:
            raise Http404
        if not 2000 <= year <= 2098:
            raise Http404
        return redirect(f"reports:{go}", tax_year=year)
    if go == "cost":
        month = _month_param(request.GET.get("month"))
        url = reverse("reports:cost")
        return redirect(f"{url}?month={month:%Y-%m}" if month else url)
    today = timezone.localdate()
    months = queries.month_choices(company)
    last = queries.latest_finalised(company)
    return page(
        request, "reports/index.html", _("Reports"),
        _("Choose a month, then press the button of the report you want."),
        months=months, default_month=(last.month if last else (months[0] if months else None)),
        tax_years=queries.tax_year_choices(company, today), default_tax_year=tax_year_of(
            last.month if last else today),
    )


# ---------------------------------------------------------------- PF / ESI / PT statements
@company_required
def pf(request, month):
    run = _run(request, month)
    lines = queries.pf_lines(run, _lines(run))
    _included, missing = queries.ecr_members(run, lines)
    totals = {f: sum((getattr(l, f) for l in lines), ZERO) for f in
              ("gross", "epf_wage", "eps_wage", "edli_wage", "ee_pf", "er_eps", "er_epf", "edli", "pf_admin")}
    challan = [
        (_("A/c 1 — Provident Fund (employee + employer EPF)"), totals["ee_pf"] + totals["er_epf"]),
        (_("A/c 2 — Administration charges"), run.pf_admin_establishment),
        (_("A/c 10 — Pension fund (EPS)"), totals["er_eps"]),
        (_("A/c 21 — EDLI"), totals["edli"]),
    ]
    challan_total = sum((amount for _l, amount in challan), ZERO)
    if _wants_xlsx(request):
        if blocked := _files_need_final(request, run):
            return blocked
        headers = ["UAN", "Code", "Name", "Gross", "EPF wages", "EPS wages", "EDLI wages", "Employee PF",
                   "Employer EPS", "Employer EPF", "EDLI", "Admin", "NCP days"]
        rows = [[l.emp_snapshot.get("uan", ""), l.code, l.name, _n(l.gross), _n(l.epf_wage), _n(l.eps_wage),
                 _n(l.edli_wage), _n(l.ee_pf), _n(l.er_eps), _n(l.er_epf), _n(l.edli), _n(l.pf_admin), l.ncp_days]
                for l in lines]
        total_row = ["", "", "Total"] + [_n(totals[f]) for f in ("gross", "epf_wage", "eps_wage", "edli_wage",
                                                                   "ee_pf", "er_eps", "er_epf", "edli", "pf_admin")] + [""]
        wb = table_workbook(f"PF {month_short(run.month)}", headers, rows, money_columns=headers[3:12],
                            totals=total_row, widths={"UAN": 15, "Name": 24})
        return xlsx_response(wb, f"{run.company.code}_pf_{run.month:%Y-%m}.xlsx")
    return page(
        request, "reports/pf.html", _("PF statement — %(month)s") % {"month": run.label},
        _("Provident Fund for each member and the challan totals. Download the ECR file to upload on the EPFO portal."),
        run=run, lines=lines, totals=totals, challan=challan, challan_total=challan_total, missing=missing,
    )


@company_required
def esi(request, month):
    run = _run(request, month)
    all_lines = _lines(run)
    covered = [l for l in all_lines if l.esi_covered]
    _included, missing = queries.esi_members(all_lines)
    totals = {f: sum((getattr(l, f) for l in covered), ZERO) for f in ("esi_wage", "ee_esi", "er_esi")}
    if _wants_xlsx(request):
        if blocked := _files_need_final(request, run):
            return blocked
        headers = ["IP Number", "Code", "Name", "Paid days", "ESI wages", "Employee ESI", "Employer ESI"]
        rows = [[l.emp_snapshot.get("esic_ip_no", ""), l.code, l.name, _n(l.paid_days), _n(l.esi_wage),
                 _n(l.ee_esi), _n(l.er_esi)] for l in covered]
        wb = table_workbook(f"ESI {month_short(run.month)}", headers, rows,
                            money_columns=["ESI wages", "Employee ESI", "Employer ESI"],
                            totals=["", "", "Total", "", _n(totals["esi_wage"]), _n(totals["ee_esi"]),
                                    _n(totals["er_esi"])], widths={"Name": 24})
        return xlsx_response(wb, f"{run.company.code}_esi_{run.month:%Y-%m}.xlsx")
    return page(
        request, "reports/esi.html", _("ESI statement — %(month)s") % {"month": run.label},
        _("ESI for each covered employee and the challan total. Download the ESI file to upload on the ESIC portal."),
        run=run, lines=covered, totals=totals, challan_total=totals["ee_esi"] + totals["er_esi"], missing=missing,
    )


@company_required
def pt(request, month):
    run = _run(request, month)
    lines = _lines(run)
    slabs = {}
    for l in lines:
        count, total = slabs.get(l.pt, (0, ZERO))
        slabs[l.pt] = (count + 1, total + l.pt)
    slab_rows = [{"amount": amount, "count": c, "total": t} for amount, (c, t) in sorted(slabs.items(), reverse=True)]
    total = sum((l.pt for l in lines), ZERO)
    if _wants_xlsx(request):
        if blocked := _files_need_final(request, run):
            return blocked
        headers = ["Code", "Name", "Gross", "Professional tax"]
        rows = [[l.code, l.name, _n(l.gross), _n(l.pt)] for l in lines]
        wb = table_workbook(f"PT {month_short(run.month)}", headers, rows,
                            money_columns=["Gross", "Professional tax"],
                            totals=["", "Total", _n(sum((l.gross for l in lines), ZERO)), _n(total)],
                            widths={"Name": 24})
        return xlsx_response(wb, f"{run.company.code}_pt_{run.month:%Y-%m}.xlsx")
    return page(
        request, "reports/pt.html", _("Professional tax — %(month)s") % {"month": run.label},
        _("Professional tax cut from each employee, and the totals for each slab."),
        run=run, lines=lines, slab_rows=slab_rows, total=total,
        state=run.company.get_pt_state_display() or run.company.state_name,
    )


# ---------------------------------------------------------------- TDS (tax year)
@company_required
def tds(request, tax_year):
    company = request.company
    start = date(tax_year, 4, 1)
    months = [add_months(start, i) for i in range(12)]
    runs = PayrollRun.objects.filter(company=company, status=PayrollRun.Status.FINALISED,
                                     month__gte=start, month__lte=months[-1])
    lines = (PayrollLine.objects.filter(company=company, run__in=runs)
             .select_related("run", "employee").order_by("employee__emp_code", "run__month"))
    openings = {o.employee_id: o for o in OpeningBalance.objects.filter(company=company, tax_year=tax_year)}
    people = {}
    for l in lines:
        p = people.setdefault(l.employee_id, {
            "code": l.code, "name": l.name, "regime": l.emp_snapshot.get("tax_regime", ""),
            "months": {}, "gross": ZERO, "tds": ZERO})
        p["months"][l.run.month] = l.tds
        p["gross"] += l.gross
        p["tds"] += l.tds
        p["regime"] = l.emp_snapshot.get("tax_regime", p["regime"])
    for emp_id, p in people.items():
        o = openings.get(emp_id)
        p["opening_tds"] = (o.ytd_tds + o.prev_employer_tds) if o else ZERO
        p["opening_gross"] = (o.ytd_gross + o.prev_employer_income) if o else ZERO
        p["cells"] = [p["months"].get(m) for m in months]
        p["ytd_tds"] = p["opening_tds"] + p["tds"]
    rows = sorted(people.values(), key=lambda p: p["code"])
    month_totals = [sum((p["months"].get(m, ZERO) for p in rows), ZERO) for m in months]
    if _wants_xlsx(request):
        headers = (["Code", "Name", "Regime", "Opening TDS"] + [month_short(m) for m in months]
                   + ["TDS this year", "TDS incl. opening", "Gross this year"])
        data = [[p["code"], p["name"], p["regime"], _n(p["opening_tds"])] + [_n(c) for c in p["cells"]]
                + [_n(p["tds"]), _n(p["ytd_tds"]), _n(p["gross"])] for p in rows]
        wb = table_workbook(f"TDS {tax_year_label(tax_year)}", headers, data, money_columns=headers[3:],
                            totals=["", "Total", "", _n(sum((p["opening_tds"] for p in rows), ZERO))]
                            + [_n(t) for t in month_totals]
                            + [_n(sum((p["tds"] for p in rows), ZERO)), _n(sum((p["ytd_tds"] for p in rows), ZERO)),
                               _n(sum((p["gross"] for p in rows), ZERO))],
                            widths={"Name": 24})
        return xlsx_response(wb, f"{company.code}_tds_{tax_year_label(tax_year)}.xlsx")
    return page(
        request, "reports/tds.html", _("Income tax (TDS) — tax year %(year)s") % {"year": tax_year_label(tax_year)},
        _("TDS cut each month from finalised salaries, with the amount from opening balances."),
        tax_year=tax_year, months=months, rows=rows, month_totals=month_totals,
        total_tds=sum((p["tds"] for p in rows), ZERO), total_ytd=sum((p["ytd_tds"] for p in rows), ZERO),
        total_opening=sum((p["opening_tds"] for p in rows), ZERO),
        tax_years=queries.tax_year_choices(company, timezone.localdate()),
    )


# ---------------------------------------------------------------- cost
@company_required
def cost(request):
    company = request.company
    view = request.GET.get("view") if request.GET.get("view") in queries.COST_VIEWS else "net"
    rows, largest = queries.cost_rows(company, view)
    finals = [r["run"] for r in rows]
    month = _month_param(request.GET.get("month"))
    run = None
    if month:
        run = PayrollRun.objects.filter(company=company, month=month, status=PayrollRun.Status.FINALISED).first()
        if run is None:  # a draft (or unknown) month: show the latest finalised month instead, and say so
            text = _("The cost report uses finalised months only. %(month)s is not finalised yet.") % {
                "month": month_label(month)}
            if finals:
                text += " " + _("Showing %(month)s instead.") % {"month": finals[0].label}
            messages.info(request, text)
            params = {"view": view} if "view" in request.GET else {}
            if finals:
                params["month"] = f"{finals[0].month:%Y-%m}"
            return redirect(f"{reverse('reports:cost')}?{urlencode(params)}" if params else reverse("reports:cost"))
    elif finals:
        run = finals[0]
    lines = _lines(run) if run else []
    per_emp = [{"line": l, "employer_pf": l.employer_pf, "cost": l.employer_cost} for l in lines]
    run_totals = run.totals() if run else None
    if _wants_xlsx(request):
        if run is None:
            raise Http404
        headers = ["Code", "Name", "Net pay", "Gross", "Employer PF + EDLI + admin", "Employer ESI", "Total cost"]
        data = [[l.code, l.name, _n(l.net_pay), _n(l.gross), _n(l.employer_pf), _n(l.er_esi), _n(l.employer_cost)]
                for l in lines]
        t = run_totals
        totals_row = ["", "Total", _n(t["net_pay"]), _n(t["gross"]), _n(t["employer_pf"]), _n(t["er_esi"]),
                      _n(t["employer_cost"])]
        if run.pf_admin_topup:
            data.append(["", "PF admin minimum top-up", None, None, _n(run.pf_admin_topup), None, _n(run.pf_admin_topup)])
            totals_row[-1] = _n(t["company_cost"])
            totals_row[4] = _n(t["employer_pf"] + run.pf_admin_topup)
        wb = table_workbook(f"Cost {month_short(run.month)}", headers, data, money_columns=headers[2:],
                            totals=totals_row, widths={"Name": 24, "Employer PF + EDLI + admin": 16})
        return xlsx_response(wb, f"{company.code}_cost_{run.month:%Y-%m}.xlsx")
    return page(
        request, "reports/cost.html", _("Salary cost"),
        _("What the salary costs each month, and how much each person costs in one month."),
        view=view, views=queries.COST_VIEWS, cost_rows=rows, largest=largest, run=run, per_emp=per_emp,
        run_totals=run_totals, employer_pf_total=(run_totals["employer_pf"] + run.pf_admin_topup) if run else None,
        finals=finals,
    )


# ---------------------------------------------------------------- advances register
@company_required
def advances(request):
    company = request.company
    items = list(
        Advance.objects.filter(company=company).select_related("employee")
        .annotate(recovered_sum=Coalesce(Sum("recoveries__amount"), ZERO))
        .order_by("employee__emp_code", "date_given", "id")
    )
    for a in items:
        a.left = a.amount - a.recovered_sum
    by_emp = {}
    for a in items:
        e = by_emp.setdefault(a.employee_id, {"employee": a.employee, "given": ZERO, "recovered": ZERO, "left": ZERO})
        e["given"] += a.amount
        e["recovered"] += a.recovered_sum
        e["left"] += a.left
    summary = [e for e in by_emp.values() if e["left"] > 0]
    totals = {"given": sum((a.amount for a in items), ZERO), "recovered": sum((a.recovered_sum for a in items), ZERO),
              "left": sum((a.left for a in items), ZERO)}
    if _wants_xlsx(request):
        headers = ["Code", "Name", "Date given", "Amount", "Instalment", "Recovered", "Outstanding", "Status", "Note"]
        data = [[a.employee.emp_code, a.employee.full_name, fmt_date(a.date_given), _n(a.amount),
                 _n(a.monthly_installment), _n(a.recovered_sum), _n(a.left), a.get_status_display(), a.note]
                for a in items]
        wb = table_workbook("Advances", headers, data,
                            money_columns=["Amount", "Instalment", "Recovered", "Outstanding"],
                            totals=["", "Total", "", _n(totals["given"]), "", _n(totals["recovered"]),
                                    _n(totals["left"]), "", ""], widths={"Name": 24, "Note": 30})
        return xlsx_response(wb, f"{company.code}_advances.xlsx")
    return page(
        request, "reports/advances.html", _("Advances register"),
        _("Every advance given, how much is recovered and how much is still to come."),
        items=items, summary=summary, totals=totals,
    )


# ---------------------------------------------------------------- joiners and leavers
@company_required
def joiners_leavers(request):
    company = request.company
    start, end = _date_range(request)
    staff = Employee.objects.filter(company=company).select_related("designation", "department")
    joiners = list(staff.filter(date_of_joining__range=(start, end)).order_by("date_of_joining", "emp_code"))
    leavers = list(staff.filter(date_of_leaving__range=(start, end)).order_by("date_of_leaving", "emp_code"))
    if _wants_xlsx(request):
        headers = ["Type", "Code", "Name", "Designation", "Department", "Date of joining", "Last working day", "Reason"]
        data = [["Joined", e.emp_code, e.full_name, str(e.designation), str(e.department or ""),
                 fmt_date(e.date_of_joining), "", ""] for e in joiners]
        data += [["Left", e.emp_code, e.full_name, str(e.designation), str(e.department or ""),
                  fmt_date(e.date_of_joining), fmt_date(e.date_of_leaving), e.get_leaving_reason_display()]
                 for e in leavers]
        wb = table_workbook("Joiners and leavers", headers, data, widths={"Name": 24, "Designation": 18})
        return xlsx_response(wb, f"{company.code}_joiners_leavers_{start:%Y%m%d}_{end:%Y%m%d}.xlsx")
    return page(
        request, "reports/joiners_leavers.html", _("Joiners and leavers"),
        _("Staff who joined or left between the two dates. Change the dates and press Show."),
        start=start, end=end, joiners=joiners, leavers=leavers,
    )


# ---------------------------------------------------------------- increments
@company_required
def increments(request):
    company = request.company
    start, end = _date_range(request)
    revisions = list(
        SalaryRevision.objects.filter(company=company, effective_from__range=(start, end))
        .select_related("employee").order_by("effective_from", "employee__emp_code", "id")
    )
    emp_ids = {r.employee_id for r in revisions}
    history = {}
    for r in SalaryRevision.objects.filter(company=company, employee_id__in=emp_ids).order_by("effective_from", "id"):
        history.setdefault(r.employee_id, []).append(r)
    rows = []
    for r in revisions:
        earlier = [h for h in history[r.employee_id] if (h.effective_from, h.pk) < (r.effective_from, r.pk)]
        before = earlier[-1].gross if earlier else None
        rows.append({"rev": r, "before": before, "change": (r.gross - before) if before is not None else None})
    if _wants_xlsx(request):
        headers = ["Code", "Name", "Effective from", "Basic", "DA", "HRA", "Conveyance", "Other", "Gross",
                   "Gross before", "Change", "Reason", "Source"]
        data = [[x["rev"].employee.emp_code, x["rev"].employee.full_name, month_label(x["rev"].effective_from),
                 _n(x["rev"].basic), _n(x["rev"].da), _n(x["rev"].hra), _n(x["rev"].conveyance),
                 _n(x["rev"].other_allowance), _n(x["rev"].gross), _n(x["before"]), _n(x["change"]),
                 x["rev"].reason, x["rev"].get_source_display()] for x in rows]
        wb = table_workbook("Increments", headers, data, money_columns=headers[3:11],
                            widths={"Name": 24, "Reason": 34, "Source": 22, "Effective from": 16})
        return xlsx_response(wb, f"{company.code}_increments_{start:%Y%m%d}_{end:%Y%m%d}.xlsx")
    return page(
        request, "reports/increments.html", _("Increment history"),
        _("Every salary change that starts between the two dates. Change the dates and press Show."),
        start=start, end=end, rows=rows,
    )
