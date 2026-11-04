"""
Reading uploaded Excel files (README §6.1). Forgiving about formats
(spaces, "₹12,000", text dates, Yes/Y/1), strict about meaning, and
all-or-nothing: the caller saves only when there is no error at all.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext as _
from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from core.formatting import month_label
from core.validators import (
    aadhaar_validator, esic_validator, ifsc_validator, mobile_validator, pan_validator, uan_validator,
)

from .builders import EMPLOYEES, MONTHLY, OPENING
from .columns import CHOICES, EMPLOYEE_COLUMNS, MONTHLY_COLUMNS, OPENING_COLUMNS, header_of

TEMPLATE_NAMES = {
    EMPLOYEES: "employee list",
    MONTHLY: "monthly attendance sheet",
    OPENING: "opening balances sheet",
}


@dataclass
class CellError:
    row: int
    code: str
    name: str
    column: str
    message: str

    def as_dict(self):
        return asdict(self)


class SheetError(Exception):
    """The whole file cannot be used (wrong type, wrong month, missing columns...)."""


# ---------------------------------------------------------------- opening the file
def read_upload(upload):
    """Bytes of an uploaded .xlsx file after the size/type checks."""
    name = (getattr(upload, "name", "") or "").lower()
    if not name.endswith(".xlsx"):
        raise SheetError(_("Please save the file as Excel Workbook (.xlsx) and upload again."))
    if upload.size > settings.MAX_UPLOAD_BYTES:
        raise SheetError(_("The file is bigger than 5 MB. Please remove extra sheets or pictures and upload again."))
    return upload.read()


def open_workbook(data):
    try:
        return load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception:  # any broken / renamed / non-Excel file
        raise SheetError(_("This file could not be opened. Please save it as Excel Workbook (.xlsx) and upload again."))


def read_meta(wb):
    if "_meta" not in wb.sheetnames:
        return {}
    meta = {}
    for row in wb["_meta"].iter_rows(values_only=True):
        if row and row[0]:
            meta[str(row[0]).strip()] = "" if len(row) < 2 or row[1] is None else str(row[1]).strip()
    return meta


def check_meta(meta, template_type, company, month=None):
    if not meta:
        return [_("This file was not made from our sample. Please download the sample or pre-filled sheet, "
                  "copy your data into it and upload again.")]
    found = meta.get("template_type", "")
    if found != template_type:
        return [_("This is the %(found)s, but this page needs the %(wanted)s.") % {
            "found": TEMPLATE_NAMES.get(found, _("wrong sheet")), "wanted": TEMPLATE_NAMES[template_type]}]
    errors = []
    if meta.get("company_code") != company.code:
        errors.append(_("This sheet belongs to company %(theirs)s, but you are logged in to %(ours)s.")
                      % {"theirs": meta.get("company_code") or "?", "ours": company.code})
    if month is not None and meta.get("month") != f"{month:%Y-%m}":
        try:
            year, mon = meta.get("month", "").split("-")
            sheet_month = month_label(date(int(year), int(mon), 1))
        except (ValueError, TypeError):
            sheet_month = meta.get("month") or _("another month")
        errors.append(_("This sheet is for %(sheet)s, but you are uploading for %(month)s.")
                      % {"sheet": sheet_month, "month": month_label(month)})
    return errors


def _norm(text):
    return " ".join(str(text or "").replace("*", "").split()).lower()


def header_index(ws, columns, must):
    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    positions = {}
    for i, value in enumerate(header):
        if value is not None and _norm(value) not in positions:
            positions[_norm(value)] = i
    index, missing = {}, []
    for key, title, *_rest in columns:
        pos = positions.get(_norm(title))
        if pos is None:
            if key in must:
                missing.append(title)
        else:
            index[key] = pos
    if missing:
        raise SheetError(_("These columns are missing: %(cols)s. Please use the sample file without changing the headings.")
                         % {"cols": ", ".join(missing)})
    return index


def _cell(values, index, key):
    pos = index.get(key)
    if pos is None or pos >= len(values):
        return None
    return values[pos]


# ---------------------------------------------------------------- forgiving values
def to_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.strftime("%d-%m-%Y")
    return " ".join(str(value).split())


def to_decimal(value):
    if value is None or isinstance(value, bool):
        if isinstance(value, bool):
            raise ValueError
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        text = str(value).strip().lower()
        for token in ("₹", "rs.", "rs", "inr"):
            text = text.replace(token, "")
        text = text.replace(",", "").replace(" ", "")
        if text in ("", "-"):
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError
    if not number.is_finite():
        raise ValueError
    return number


def to_money(value):
    number = to_decimal(value)
    if number is not None and number != number.to_integral_value():
        raise ValueError("paise")
    return number


def to_date(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return from_excel(value).date()
    text = str(value).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError


YES = {"yes", "y", "1", "true", "t", "haan", "ha"}
NO = {"no", "n", "0", "false", "f", "nahi"}


def to_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text == "":
        return None
    if text in YES:
        return True
    if text in NO:
        return False
    raise ValueError


def to_choice(value, choices):
    text = " ".join(str(value or "").split()).lower()
    if text == "":
        return None
    if text in choices:
        return choices[text]
    raise ValueError


def _value_error_message(kind, raw):
    if kind == "money":
        return _("'%(v)s' is not an amount in whole rupees (like 12000).") % {"v": raw}
    if kind in ("days", "number"):
        return _("'%(v)s' is not a number.") % {"v": raw}
    if kind == "date":
        return _("'%(v)s' is not a date. Write it like 15-06-1995.") % {"v": raw}
    if kind == "yesno":
        return _("Write Yes or No (found '%(v)s').") % {"v": raw}
    return _("'%(v)s' is not one of the allowed choices.") % {"v": raw}


def _rows(ws):
    """(row number, values) of data rows: skips empty first cells and EXAMPLE rows."""
    for number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not values:
            continue
        first = to_text(values[0])
        if not first or first.upper() == "EXAMPLE":
            continue
        yield number, values


# ---------------------------------------------------------------- monthly attendance sheet
MONTHLY_NUMBER_KEYS = ["days_in_month", "present", "paid_leave", "week_off_holiday", "lop", "ot_hours", "ot_amount",
                       "incentive", "increment", "advance", "other_deduction", "tds_override"]
MONTHLY_KINDS = {c[0]: c[3] for c in MONTHLY_COLUMNS}


def parse_monthly(data, ctx):
    """Returns (rows {employee pk: RowInput}, cell errors, file errors)."""
    from employees.models import Employee
    from payroll.service import RowInput, check_row

    wb = open_workbook(data)
    try:
        file_errors = check_meta(read_meta(wb), MONTHLY, ctx.company, ctx.month)
        if file_errors:
            return {}, [], file_errors
        ws = wb.worksheets[0]
        index = header_index(ws, MONTHLY_COLUMNS, must=("emp_code", "present"))
        expected = {e.emp_code: e for e in ctx.employees}
        known = set(Employee.objects.filter(company=ctx.company).values_list("emp_code", flat=True))
        rows, errors, seen = {}, [], {}
        for number, values in _rows(ws):
            code = to_text(_cell(values, index, "emp_code"))
            emp = expected.get(code)
            name = emp.full_name if emp else to_text(_cell(values, index, "name"))

            def err(key, message, number=number, code=code, name=name):
                errors.append(CellError(number, code, name, header_of(MONTHLY_COLUMNS, key), str(message)))

            if code in seen:
                err("emp_code", _("Emp Code %(code)s is written twice (rows %(a)s and %(b)s).")
                    % {"code": code, "a": seen[code], "b": number})
                continue
            seen[code] = number
            if emp is None:
                if code in known:
                    err("emp_code", _("%(code)s is not paid in %(month)s (joined after it or left before it). "
                                      "Remove this row.") % {"code": code, "month": month_label(ctx.month)})
                else:
                    err("emp_code", _("Unknown Emp Code %(code)s. Add the employee first or correct the code.")
                        % {"code": code})
                continue
            parsed, bad = {}, False
            for key in MONTHLY_NUMBER_KEYS:
                raw = _cell(values, index, key)
                try:
                    parsed[key] = to_money(raw) if MONTHLY_KINDS[key] == "money" else to_decimal(raw)
                except ValueError:
                    err(key, _value_error_message(MONTHLY_KINDS[key], raw))
                    bad = True
            if bad:
                continue
            zero = Decimal("0")
            row = RowInput(
                present=parsed["present"] or zero, paid_leave=parsed["paid_leave"] or zero,
                week_off_holiday=parsed["week_off_holiday"] or zero, lop=parsed["lop"],
                ot_hours=parsed["ot_hours"] or zero, ot_amount=parsed["ot_amount"],
                incentive=parsed["incentive"] or zero, increment=parsed["increment"] or zero,
                advance=parsed["advance"] or zero, other_deduction=parsed["other_deduction"] or zero,
                other_reason=to_text(_cell(values, index, "other_reason")),
                tds_override=parsed["tds_override"], remarks=to_text(_cell(values, index, "remarks"))[:200],
            )
            for key, message in check_row(ctx, emp, row, parsed["days_in_month"]):
                err(key, message)
            rows[emp.pk] = row
        missing = [e for code, e in expected.items() if code not in seen]
        if missing:
            file_errors.append(
                _("These employees are missing: %(names)s; add them with 0 days or mark them relieved.")
                % {"names": ", ".join(f"{e.emp_code} {e.full_name}" for e in missing)})
        return rows, errors, file_errors
    finally:
        wb.close()


# ---------------------------------------------------------------- employee list
EMPLOYEE_KINDS = {c[0]: c[3] for c in EMPLOYEE_COLUMNS}
ID_RULES = {
    "pan": (pan_validator, "upper"),
    "ifsc": (ifsc_validator, "upper"),
    "uan": (uan_validator, "digits"),
    "esic_ip_no": (esic_validator, "digits"),
    "aadhaar": (aadhaar_validator, "digits"),
    "mobile": (mobile_validator, "digits"),
}


def _clean_id(value, how):
    value = "".join(value.split())
    if how == "upper":
        return value.upper()
    if how == "digits" and value.replace("-", "").isdigit():
        return value.replace("-", "")
    return value


def parse_employees(data, company):
    """Returns (rows [dict], cell errors, file errors). Each row dict has the cleaned values and '_row'."""
    from employees.models import Employee
    from payroll.engine import age_on

    wb = open_workbook(data)
    try:
        file_errors = check_meta(read_meta(wb), EMPLOYEES, company)
        if file_errors:
            return [], [], file_errors
        ws = wb.worksheets[0]
        must = [c[0] for c in EMPLOYEE_COLUMNS if c[2]]
        index = header_index(ws, EMPLOYEE_COLUMNS, must=must)
        existing = {e.emp_code: e for e in Employee.objects.filter(company=company).select_related("reports_to")}
        rows, errors, seen = [], [], {}
        for number, values in _rows(ws):
            code = to_text(_cell(values, index, "emp_code"))
            name = " ".join(p for p in (to_text(_cell(values, index, "first_name")),
                                        to_text(_cell(values, index, "surname"))) if p)
            row_errors = []

            def err(key, message, number=number, code=code, name=name, row_errors=row_errors):
                row_errors.append(CellError(number, code, name, header_of(EMPLOYEE_COLUMNS, key), str(message)))

            if code in seen:
                err("emp_code", _("Emp Code %(code)s is written twice (rows %(a)s and %(b)s).")
                    % {"code": code, "a": seen[code], "b": number})
                errors.extend(row_errors)
                continue
            seen[code] = number
            if len(code) > 20:
                err("emp_code", _("Emp Code can have at most 20 characters."))
            is_new = code not in existing
            row = {"_row": number, "emp_code": code, "_new": is_new}
            for key, _title, required, kind, *_help in EMPLOYEE_COLUMNS[1:]:
                raw = _cell(values, index, key)
                try:
                    if kind == "text":
                        value = to_text(raw)
                    elif kind == "money":
                        value = to_money(raw)
                    elif kind == "date":
                        value = to_date(raw)
                    elif kind == "yesno":
                        value = to_bool(raw)
                    else:
                        value = to_choice(raw, CHOICES[kind.split(":", 1)[1]])
                except ValueError:
                    err(key, _value_error_message(kind, raw))
                    continue
                row[key] = value
                if required and value in (None, "") and (key != "basic" or is_new):
                    err(key, _("This column must be filled."))
            for key, (validator, how) in ID_RULES.items():
                if row.get(key):
                    row[key] = _clean_id(row[key], how)
                    try:
                        validator(row[key])
                    except ValidationError as exc:
                        err(key, exc.messages[0])
            if row.get("email"):
                try:
                    validate_email(row["email"])
                except ValidationError:
                    err("email", _("This email address does not look right."))
            if row.get("aadhaar") and row.get("aadhaar_consent") is not True:
                err("aadhaar_consent", _("Write Yes here to save the Aadhaar number, or leave Aadhaar empty."))
            if not row.get("payment_mode"):
                row["payment_mode"] = "Bank" if row.get("bank_account_no") else ("Cash" if is_new else None)
            if row.get("payment_mode") == "Bank" or (row.get("payment_mode") is None and not is_new
                                                    and existing[code].payment_mode == "Bank"):
                for key in ("bank_name", "bank_account_no", "ifsc"):
                    if not row.get(key):
                        err(key, _("Needed when Payment Mode is Bank."))
            dob, doj = row.get("date_of_birth"), row.get("date_of_joining")
            if dob and doj:
                age = age_on(dob, doj)
                if age < 14 or age > 80:
                    err("date_of_birth", _("Please check the date of birth: age at joining would be %(age)s.")
                        % {"age": age})
            if row.get("tax_regime") != "OLD" and row.get("old_regime_deductions_annual"):
                row["old_regime_deductions_annual"] = Decimal("0")
            errors.extend(row_errors)
            rows.append(row)

        # Reports To: must exist in the sheet or the company, and no loops.
        by_code = {row["emp_code"]: row for row in rows}
        graph = {c: (e.reports_to.emp_code if e.reports_to_id else None) for c, e in existing.items()}
        for row in rows:
            graph[row["emp_code"]] = row.get("reports_to") or None
        for row in rows:
            boss = row.get("reports_to")
            if not boss:
                continue
            problem = None
            if boss == row["emp_code"]:
                problem = _("A person cannot report to himself or herself.")
            elif boss not in by_code and boss not in existing:
                problem = _("There is no employee with code %(code)s in this sheet or in your list.") % {"code": boss}
            else:
                node, seen_nodes = boss, set()
                while node and node not in seen_nodes:
                    if node == row["emp_code"]:
                        problem = _("%(name)s cannot report to someone who reports to them.") % {
                            "name": row.get("first_name") or row["emp_code"]}
                        break
                    seen_nodes.add(node)
                    node = graph.get(node)
            if problem:
                errors.append(CellError(row["_row"], row["emp_code"], row.get("first_name", ""),
                                        header_of(EMPLOYEE_COLUMNS, "reports_to"), str(problem)))
        return rows, errors, file_errors
    finally:
        wb.close()


# ---------------------------------------------------------------- opening balances
OPENING_MONEY_KEYS = ["ytd_gross", "ytd_tds", "ytd_pt", "prev_employer_income", "prev_employer_tds"]


def parse_tax_year(value):
    text = to_text(value)
    if not text:
        raise ValueError
    if "-" in text:
        start, end = text.split("-", 1)
        start = int(start)
        if (start + 1) % 100 != int(end) % 100:
            raise ValueError
        return start
    return int(Decimal(text))


def parse_opening(data, company):
    from employees.models import Employee

    wb = open_workbook(data)
    try:
        file_errors = check_meta(read_meta(wb), OPENING, company)
        if file_errors:
            return [], [], file_errors
        ws = wb.worksheets[0]
        index = header_index(ws, OPENING_COLUMNS, must=("emp_code", "tax_year"))
        employees = {e.emp_code: e for e in Employee.objects.filter(company=company)}
        rows, errors, seen = [], [], {}
        for number, values in _rows(ws):
            code = to_text(_cell(values, index, "emp_code"))
            emp = employees.get(code)
            name = emp.full_name if emp else to_text(_cell(values, index, "name"))

            def err(key, message, number=number, code=code, name=name):
                errors.append(CellError(number, code, name, header_of(OPENING_COLUMNS, key), str(message)))

            if emp is None:
                err("emp_code", _("Unknown Emp Code %(code)s.") % {"code": code})
                continue
            try:
                tax_year = parse_tax_year(_cell(values, index, "tax_year"))
            except (ValueError, InvalidOperation):
                err("tax_year", _("Write the tax year like 2026-27."))
                continue
            key = (code, tax_year)
            if key in seen:
                err("emp_code", _("%(code)s is written twice for the same tax year.") % {"code": code})
                continue
            seen[key] = number
            row = {"_row": number, "employee": emp, "tax_year": tax_year}
            for money_key in OPENING_MONEY_KEYS:
                raw = _cell(values, index, money_key)
                try:
                    value = to_money(raw) or Decimal("0")
                except ValueError:
                    err(money_key, _value_error_message("money", raw))
                    continue
                if value < 0:
                    err(money_key, _("Cannot be a negative number."))
                row[money_key] = value
            rows.append(row)
        return rows, errors, file_errors
    finally:
        wb.close()
