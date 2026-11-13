"""
Sample and pre-filled Excel files (README §6.1). Every template has:
sheet 1 = data (header row 1), sheet 2 = "How to fill" (English + Hindi line),
a hidden "_meta" sheet, grey pre-filled columns, drop-downs, frozen header.
"""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from core.formatting import month_label, tax_year_label, tax_year_of

from .columns import (
    DROPDOWNS, EMPLOYEE_COLUMNS, MONTHLY_PREFILLED_GREY, OPENING_COLUMNS, OPENING_PREFILLED_GREY, monthly_columns,
)
from .common import MONEY_FORMAT, PREFILLED_FILL, as_number, style_header

TEMPLATE_VERSION = "1"
EMPLOYEES = "employees"
MONTHLY = "monthly_salary"
OPENING = "opening_balances"
DATA_ROWS = 500  # rows covered by drop-down lists
TEXT_ID_KEYS = {"emp_code", "reports_to", "mobile", "pan", "aadhaar", "uan", "esic_ip_no", "bank_account_no", "ifsc"}


def _widths(columns):
    out = {}
    for key, header, _req, kind, _en, _hi in columns:
        if kind == "text" and key in ("name", "address", "nature_of_duties", "remarks", "other_reason"):
            out[header] = 28
        elif kind == "date":
            out[header] = 14
        else:
            out[header] = max(10, min(24, len(header) + 2))
    return out


def add_help_sheet(wb, columns, intro):
    ws = wb.create_sheet("How to fill")
    ws["A1"] = intro
    ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = "Rows whose first cell is EXAMPLE or empty are not read. Save as Excel Workbook (.xlsx)."
    ws["A3"] = "EXAMPLE या खाली पहले खाने वाली पंक्तियाँ नहीं पढ़ी जातीं। फ़ाइल .xlsx में सेव करें।"
    ws.append([])
    ws.append(["Column", "Required", "What to write / क्या लिखें"])
    for cell in ws[5]:
        cell.font = Font(bold=True)
    for key, header, required, _kind, en, hi in columns:
        ws.append([header, "Yes" if required else "", en])
        ws.append(["", "", hi])
        ws.cell(row=ws.max_row, column=3).font = Font(italic=True, color="555555")
    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 90
    for row in ws.iter_rows(min_row=6):
        row[2].alignment = Alignment(wrap_text=True, vertical="top")


def add_meta_sheet(wb, template_type, company, month=None):
    ws = wb.create_sheet("_meta")
    ws.append(["template_type", template_type])
    ws.append(["template_version", TEMPLATE_VERSION])
    ws.append(["company_code", company.code])
    if month is not None:
        ws.append(["month", f"{month:%Y-%m}"])
    ws.sheet_state = "hidden"


def add_dropdowns(ws, columns):
    for idx, (_key, _header, _req, kind, _en, _hi) in enumerate(columns, start=1):
        name = kind.split(":", 1)[1] if kind.startswith("choice:") else ("yesno" if kind == "yesno" else None)
        if not name:
            continue
        dv = DataValidation(type="list", formula1='"' + ",".join(DROPDOWNS[name]) + '"', allow_blank=True,
                            showErrorMessage=False)
        letter = get_column_letter(idx)
        dv.add(f"{letter}2:{letter}{DATA_ROWS}")
        ws.add_data_validation(dv)


def _format_row(ws, row_idx, columns, grey_keys=()):
    for idx, (key, _header, _req, kind, _en, _hi) in enumerate(columns, start=1):
        cell = ws.cell(row=row_idx, column=idx)
        if kind == "money":
            cell.number_format = MONEY_FORMAT
        elif kind == "date":
            cell.number_format = "DD-MM-YYYY"
        elif key in TEXT_ID_KEYS:
            cell.number_format = "@"  # stored as text so Excel keeps all digits and leading zeros
        if key in grey_keys:
            cell.fill = PREFILLED_FILL


# ---------------------------------------------------------------- employees
def _yes_no(value):
    return "Yes" if value else "No"


def employee_row(emp):
    rev = emp.revisions.order_by("-effective_from", "-id").first()  # latest salary structure
    values = {
        "emp_code": emp.emp_code, "first_name": emp.first_name, "surname": emp.surname,
        "relation_type": emp.relation_type, "relation_name": emp.relation_name, "gender": emp.gender,
        "date_of_birth": emp.date_of_birth, "date_of_joining": emp.date_of_joining, "mobile": emp.mobile,
        "email": emp.email, "address": emp.address,
        "department": emp.department.name if emp.department_id else "",
        "designation": emp.designation.name if emp.designation_id else "",
        "reports_to": emp.reports_to.emp_code if emp.reports_to_id else "",
        "employment_type": emp.employment_type, "skill_category": emp.skill_category,
        "nature_of_duties": emp.nature_of_duties, "pan": emp.pan,
        "aadhaar": "", "aadhaar_consent": _yes_no(emp.aadhaar_consent),  # Aadhaar is never written out in full
        "uan": emp.uan, "esic_ip_no": emp.esic_ip_no, "payment_mode": emp.payment_mode, "bank_name": emp.bank_name,
        "bank_account_no": emp.bank_account_no, "ifsc": emp.ifsc,
        "basic": as_number(rev.basic) if rev else None, "da": as_number(rev.da) if rev else None,
        "hra": as_number(rev.hra) if rev else None, "conveyance": as_number(rev.conveyance) if rev else None,
        "other_allowance": as_number(rev.other_allowance) if rev else None,
        "pf_member": _yes_no(emp.pf_member), "eps_member": _yes_no(emp.eps_member),
        "esi_override": {"AUTO": "Auto", "YES": "Yes", "NO": "No"}[emp.esi_override],
        "is_pwd": _yes_no(emp.is_pwd), "pt_exempt": _yes_no(emp.pt_exempt),
        "tax_regime": {"NEW": "New", "OLD": "Old"}[emp.tax_regime],
        "old_regime_deductions_annual": as_number(emp.old_regime_deductions_annual),
    }
    return [values[c[0]] for c in EMPLOYEE_COLUMNS]


EXAMPLE_EMPLOYEE = [
    "EXAMPLE", "Ramesh", "Kumar", "Father", "Suresh Kumar", "M", "15-06-1995", "01-04-2022", "9845012345",
    "", "12, 4th Cross, Jayanagar", "Store", "Helper", "E003", "Permanent", "Unskilled",
    "Loading and arranging stock", "ABCPK1234A", "", "No", "101234567801", "1234567801", "Bank",
    "State Bank of India", "1234567801", "SBIN0001234", 12000, 0, 0, 0, 0, "Yes", "Yes", "Auto", "No", "No",
    "New", 0,
]


def employee_workbook(company, employees=None):
    """The employee sample (employees=None) or the full list for 'Download list (Excel)'."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Employees"
    headers = [c[1] for c in EMPLOYEE_COLUMNS]
    style_header(ws, headers, _widths(EMPLOYEE_COLUMNS))
    rows = [EXAMPLE_EMPLOYEE] if employees is None else [employee_row(e) for e in employees]
    for r, values in enumerate(rows, start=2):
        for c, value in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=value)
        _format_row(ws, r, EMPLOYEE_COLUMNS)
    if employees is None:
        for cell in ws[2]:
            cell.font = Font(italic=True, color="666666")
    add_dropdowns(ws, EMPLOYEE_COLUMNS)
    add_help_sheet(wb, EMPLOYEE_COLUMNS, "Employee list — one row per person. Columns marked Required must be filled.")
    add_meta_sheet(wb, EMPLOYEES, company)
    return wb


# ---------------------------------------------------------------- monthly sheet
def monthly_workbook(ctx, existing_lines=None):
    """Attendance and pay sheet for one month, pre-filled with everyone to be paid.

    existing_lines: {employee_id: PayrollLine} of a draft month, to pre-fill what was uploaded before.
    """
    from payroll import engine

    wb = Workbook()
    ws = wb.active
    ws.title = month_label(ctx.month)[:31]
    columns = monthly_columns(ctx.company)
    headers = [c[1] for c in columns]
    style_header(ws, headers, _widths(columns))
    year, month = ctx.month.year, ctx.month.month
    dim = engine.days_in_month(year, month)
    weekly_off = ctx.company.weekly_off
    existing_lines = existing_lines or {}
    for r, emp in enumerate(ctx.employees, start=2):
        line = existing_lines.get(emp.pk)
        start, end = engine.employed_span(year, month, emp.date_of_joining, emp.date_of_leaving)
        offs = engine.weekly_offs_between(start, end, weekly_off) if start else 0
        values = {
            "emp_code": emp.emp_code, "name": emp.full_name,
            "designation": emp.designation.name if emp.designation_id else "",
            "doj": emp.date_of_joining, "pan": emp.pan, "uan": emp.uan, "esic_ip_no": emp.esic_ip_no,
            "days_in_month": dim,
            "working_days": engine.working_days_employed(year, month, emp.date_of_joining, emp.date_of_leaving,
                                                         weekly_off),
            "week_off_holiday": offs or None,
            "advance": as_number(ctx.suggested_advance(emp)) or None,
        }
        if line is not None:
            values.update({
                "present": as_number(line.present), "paid_leave": as_number(line.paid_leave) or None,
                "week_off_holiday": as_number(line.week_off_holiday) or None, "lop": as_number(line.lop_days),
                "ot_hours": as_number(line.ot_hours) or None, "ot_amount": as_number(line.ot_amount_override),
                "incentive": as_number(line.incentive) or None, "increment": as_number(line.increment) or None,
                "advance": as_number(line.advance_deduction) or None,
                "other_deduction": as_number(line.other_deduction) or None,
                "other_reason": line.other_deduction_reason, "tds_override": as_number(line.tds_override),
                "remarks": line.remarks,
            })
        for c, (key, *_rest) in enumerate(columns, start=1):
            ws.cell(row=r, column=c, value=values.get(key))
        _format_row(ws, r, columns, MONTHLY_PREFILLED_GREY)
    add_help_sheet(wb, columns,
                   f"Attendance and pay sheet for {month_label(ctx.month)} — fill one row per person, then upload.")
    add_meta_sheet(wb, MONTHLY, ctx.company, ctx.month)
    return wb


def monthly_sample_workbook(ctx):
    """'Download sample file' for the monthly upload: same columns with one EXAMPLE row."""
    from payroll import engine

    wb = Workbook()
    ws = wb.active
    ws.title = "Sample"
    columns = monthly_columns(ctx.company)
    headers = [c[1] for c in columns]
    style_header(ws, headers, _widths(columns))
    dim = engine.days_in_month(ctx.month.year, ctx.month.month)
    offs = engine.weekly_offs_between(ctx.month, ctx.month.replace(day=dim), ctx.company.weekly_off)
    example = {"emp_code": "EXAMPLE", "name": "Ramesh Kumar", "designation": "Helper", "days_in_month": dim,
               "working_days": dim - offs, "present": dim - offs - 1, "paid_leave": 1, "week_off_holiday": offs, "ot_hours": 10, "increment": 500,
               "advance": 1000, "remarks": "Example row — not read"}
    for c, (key, *_rest) in enumerate(columns, start=1):
        ws.cell(row=2, column=c, value=example.get(key))
    _format_row(ws, 2, columns, MONTHLY_PREFILLED_GREY)
    for cell in ws[2]:
        cell.font = Font(italic=True, color="666666")
    add_help_sheet(wb, columns,
                   "Sample attendance sheet. Use 'Download sheet' on the month page to get one with your staff filled in.")
    add_meta_sheet(wb, MONTHLY, ctx.company, ctx.month)
    return wb


# ---------------------------------------------------------------- opening balances
def opening_workbook(company, employees, tax_year, balances=None):
    balances = balances or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Opening balances"
    headers = [c[1] for c in OPENING_COLUMNS]
    style_header(ws, headers, _widths(OPENING_COLUMNS))
    for r, emp in enumerate(employees, start=2):
        ob = balances.get(emp.pk)
        values = {"emp_code": emp.emp_code, "name": emp.full_name, "tax_year": tax_year_label(tax_year)}
        if ob is not None:
            for key in ("ytd_gross", "ytd_tds", "ytd_pt", "prev_employer_income", "prev_employer_tds"):
                values[key] = as_number(getattr(ob, key))
        for c, (key, *_rest) in enumerate(OPENING_COLUMNS, start=1):
            ws.cell(row=r, column=c, value=values.get(key))
        _format_row(ws, r, OPENING_COLUMNS, OPENING_PREFILLED_GREY)
    add_help_sheet(wb, OPENING_COLUMNS,
                   "Opening balances — pay and tax of this tax year before you started using the app. Used for TDS only.")
    add_meta_sheet(wb, OPENING, company)
    return wb


def default_opening_tax_year(company):
    return tax_year_of(company.payroll_start_month)
