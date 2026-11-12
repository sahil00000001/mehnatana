"""Shared Excel look and helpers (openpyxl). Header in row 1, bold with a light fill, first row frozen."""
from io import BytesIO

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MONEY_FORMAT = "#,##0"
HEADER_FILL = PatternFill("solid", fgColor="F3EEDF")
PREFILLED_FILL = PatternFill("solid", fgColor="E4E1D8")
ERROR_FILL = PatternFill("solid", fgColor="F4B6B6")
BOLD = Font(bold=True)
DOUBLE_TOP = Border(top=Side(style="double"))
WRAP = Alignment(wrap_text=True, vertical="top")


def style_header(ws, headers, widths=None):
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=text)
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="bottom")
        width = (widths or {}).get(text) or max(10, min(40, len(str(text)) + 4))
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"


def table_workbook(sheet_title, headers, rows, money_columns=(), totals=None, widths=None):
    """A plain one-sheet workbook: header row, data rows, optional bold totals row.

    money_columns: header texts whose cells get the #,##0 format.
    totals: list of values for the totals row (same length as headers), or None.
    """
    wb = Workbook()
    fill_table(wb.active, sheet_title, headers, rows, money_columns, totals, widths)
    return wb


def add_table_sheet(wb, sheet_title, headers, rows, money_columns=(), totals=None, widths=None):
    """One more sheet in the same plain look as table_workbook."""
    return fill_table(wb.create_sheet(), sheet_title, headers, rows, money_columns, totals, widths)


def fill_table(ws, sheet_title, headers, rows, money_columns=(), totals=None, widths=None):
    ws.title = sheet_title[:31]
    style_header(ws, headers, widths)
    money_idx = {i for i, h in enumerate(headers, start=1) if h in set(money_columns)}
    for r, row in enumerate(rows, start=2):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            if c in money_idx:
                cell.number_format = MONEY_FORMAT
    if totals is not None:
        r = len(rows) + 2
        for c, value in enumerate(totals, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = BOLD
            cell.border = DOUBLE_TOP
            if c in money_idx:
                cell.number_format = MONEY_FORMAT
    return ws


def neutralise_formulas(wb):
    """Text typed by people (names, notes) that starts with '=' must stay text in Excel, never a formula."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"


def workbook_bytes(wb):
    neutralise_formulas(wb)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def xlsx_response(wb, filename):
    response = HttpResponse(workbook_bytes(wb), content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def as_number(value):
    """Decimal -> int when whole (Excel shows 12000, not 12000.00)."""
    if value is None:
        return None
    try:
        if value == int(value):
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return value
