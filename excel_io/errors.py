"""
"Download my sheet with mistakes marked": the owner's own file with bad cells
filled red (with a comment) and an Errors column at the end. The failed file
is kept privately and the list of mistakes is remembered in the session.
"""
import uuid
from collections import defaultdict
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import Http404
from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font

from .common import ERROR_FILL, XLSX_TYPE

SESSION_KEY = "mtn_upload_errors"


def remember_failed_upload(request, kind, data, errors, file_errors):
    """Store the rejected file and its mistakes; kind is e.g. 'monthly:2026-11' or 'employees'."""
    path = default_storage.save(f"private/uploads/{request.company.code}/failed/{uuid.uuid4().hex}.xlsx",
                                ContentFile(data))
    store = request.session.get(SESSION_KEY, {})
    store[kind] = {"path": path, "errors": [e.as_dict() for e in errors], "file_errors": list(file_errors),
                   "company": request.company.pk}
    request.session[SESSION_KEY] = store


def forget_failed_upload(request, kind):
    store = request.session.get(SESSION_KEY, {})
    if kind in store:
        store.pop(kind)
        request.session[SESSION_KEY] = store


PENDING_KEY = "mtn_pending_uploads"


def keep_pending_upload(request, kind, data):
    """A checked file waiting for the owner's answer (e.g. 'change these salaries?')."""
    path = default_storage.save(f"private/uploads/{request.company.code}/pending/{uuid.uuid4().hex}.xlsx",
                                ContentFile(data))
    store = request.session.get(PENDING_KEY, {})
    store[kind] = {"path": path, "company": request.company.pk}
    request.session[PENDING_KEY] = store


def pending_upload_data(request, kind):
    entry = request.session.get(PENDING_KEY, {}).get(kind)
    if not entry or entry.get("company") != request.company.pk:
        return None
    try:
        with default_storage.open(entry["path"], "rb") as handle:
            return handle.read()
    except (FileNotFoundError, OSError):
        return None


def forget_pending_upload(request, kind):
    store = request.session.get(PENDING_KEY, {})
    if store.pop(kind, None) is not None:
        request.session[PENDING_KEY] = store


def failed_upload(request, kind):
    entry = request.session.get(SESSION_KEY, {}).get(kind)
    if not entry or entry.get("company") != request.company.pk:
        return None
    return entry


def _norm(text):
    from .parsers import _norm as heading_key  # same heading matching as the upload itself

    return heading_key(text)


def marked_workbook_bytes(data, errors):
    wb = load_workbook(BytesIO(data))
    ws = wb.worksheets[0]
    headers = {}
    for cell in ws[1]:
        if cell.value is not None:
            headers.setdefault(_norm(cell.value), cell.column)
    error_col = headers.get("errors") or ws.max_column + 1
    head = ws.cell(row=1, column=error_col, value="Errors")
    head.font = Font(bold=True, color="8B1E1E")
    head.fill = ERROR_FILL
    by_row = defaultdict(list)
    for e in errors:
        column = headers.get(_norm(e["column"]))
        if column:
            cell = ws.cell(row=e["row"], column=column)
            cell.fill = ERROR_FILL
            cell.comment = Comment(e["message"], settings.APP_NAME, width=320, height=120)
        by_row[e["row"]].append(f'{e["column"]}: {e["message"]}')
    for row in range(2, ws.max_row + 1):
        ws.cell(row=row, column=error_col, value="; ".join(by_row.get(row, [])) or None)
    for row, messages in by_row.items():
        ws.cell(row=row, column=error_col).fill = ERROR_FILL
    ws.column_dimensions[head.column_letter].width = 80
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def marked_file_response(request, kind, filename):
    from django.http import HttpResponse

    entry = failed_upload(request, kind)
    if entry is None:
        raise Http404
    try:
        with default_storage.open(entry["path"], "rb") as handle:
            data = handle.read()
    except (FileNotFoundError, OSError):
        raise Http404
    response = HttpResponse(marked_workbook_bytes(data, entry["errors"]), content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
