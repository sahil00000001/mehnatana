from django.contrib import messages
from django.core.files.base import ContentFile
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _

from companies.models import Company
from core.formatting import indian_format, month_label, plain_number
from core.tenancy import company_get, company_required, page
from excel_io import builders
from excel_io.common import as_number, table_workbook, xlsx_response
from excel_io.errors import forget_failed_upload, marked_file_response, remember_failed_upload
from excel_io.parsers import SheetError, parse_monthly, read_upload
from statutory.rates import MissingRate

from . import service
from .payments import payment_totals
from .forms import LineForm, ReopenForm, UploadForm
from .models import PayrollLine, PayrollRun
from .warnings import message_for


def _check_month(request, month):
    if month < request.company.payroll_start_month:
        raise Http404
    return PayrollRun.objects.filter(company=request.company, month=month).first()


def _kind(month):
    return f"monthly:{month:%Y-%m}"


def _note_texts(line):
    texts = []
    for code in line.warnings:
        if code == "DAYS_REDUCED" and line.days_given is not None:
            texts.append(_("Paid days reduced from %(given)s to %(paid)s so that Basic (minimum wage for the days) "
                           "stays within the take-home amount.") % {"given": plain_number(line.days_given),
                                                                 "paid": plain_number(line.paid_days)})
        else:
            texts.append(str(message_for(code)))
    return texts


@company_required
def months(request):
    rows = []
    for month, run in service.month_rows(request.company):
        totals = run.totals() if run is not None and run.lines.exists() else None
        rows.append({"month": month, "run": run, "totals": totals})
    return page(request, "payroll/months.html", _("Monthly Salary"),
                _("Open a month to download its sheet, upload it, check the salary and finalise."), rows=rows,
                open_month=service.first_open_month(request.company))


def _month_context(request, month, run, **extra):
    company = request.company
    blocker = service.upload_blocker(company, month)
    lines, totals, only_notes = [], None, request.GET.get("notes") == "1"
    notes_count = 0
    if run is not None:
        lines = list(run.lines.select_related("employee").order_by("employee__emp_code"))
        if lines:
            totals = run.totals()
        for line in lines:
            line.note_texts = _note_texts(line)
        notes_count = sum(1 for l in lines if l.warnings)
        if only_notes:
            lines = [l for l in lines if l.warnings]
    has_lines = totals is not None
    context = {
        "month": month, "run": run, "label": month_label(month), "blocker": blocker, "lines": lines,
        "totals": totals, "has_lines": has_lines, "only_notes": only_notes, "notes_count": notes_count,
        "full": request.GET.get("full") == "1",
        "splits_gross": company.amount_paid_mode == Company.AmountPaid.GROSS,
        "is_final": bool(run and run.is_final), "upload_form": UploadForm(),
        "finalise_blockers": service.finalise_blockers(run) if run and has_lines and not run.is_final else [],
        "can_reopen": bool(run and run.is_final and not service.reopen_blockers(run)),
        "payment": payment_totals(lines) if run and run.is_final and lines and not only_notes else None,
    }
    context.update(extra)
    return context


@company_required
def month_page(request, month):
    run = _check_month(request, month)
    context = _month_context(request, month, run)
    return page(request, "payroll/month.html", month_label(month),
                _("Follow the five steps in order. Each step has one button."), **context)


@company_required
def sheet(request, month):
    _check_month(request, month)
    company = request.company
    try:
        ctx = service.RunContext(company, month)
    except MissingRate as exc:
        messages.error(request, str(exc))
        return redirect("payroll:month", month=month)
    if request.GET.get("sample") == "1":
        return xlsx_response(builders.monthly_sample_workbook(ctx), f"{company.code}_salary_sample.xlsx")
    run = PayrollRun.objects.filter(company=company, month=month).first()
    existing = {}
    if run is not None and not run.is_final:
        existing = {line.employee_id: line for line in run.lines.all()}
    return xlsx_response(builders.monthly_workbook(ctx, existing), f"{company.code}_salary_{month:%Y-%m}.xlsx")


@company_required
def upload(request, month):
    run = _check_month(request, month)
    if request.method != "POST":
        return redirect("payroll:month", month=month)
    company = request.company
    blocker = service.upload_blocker(company, month)
    if blocker:
        messages.error(request, blocker)
        return redirect("payroll:month", month=month)
    form = UploadForm(request.POST, request.FILES)
    title = month_label(month)
    instruction = _("Follow the five steps in order. Each step has one button.")
    if not form.is_valid():
        context = _month_context(request, month, run, upload_form=form)
        return page(request, "payroll/month.html", title, instruction, **context)

    data, rows, errors, file_errors = None, {}, [], []
    try:
        data = read_upload(form.cleaned_data["file"])
        ctx = service.RunContext(company, month)
        rows, errors, file_errors = parse_monthly(data, ctx)
    except (SheetError, MissingRate) as exc:
        file_errors = [str(exc)]
    if errors or file_errors:
        forget_failed_upload(request, _kind(month))
        if data is not None and errors:
            remember_failed_upload(request, _kind(month), data, errors, file_errors)
        context = _month_context(request, month, run, upload_errors=errors, file_errors=file_errors,
                                 errors_file=bool(data is not None and errors))
        return page(request, "payroll/month.html", title, instruction, **context)
    try:
        run = service.save_inputs(company, month, rows, request.user,
                                  uploaded_file=ContentFile(data), request=request)
    except service.PayrollError as exc:
        messages.error(request, str(exc))
        return redirect("payroll:month", month=month)
    forget_failed_upload(request, _kind(month))
    count = len(rows)
    messages.success(request, _("The sheet is checked and saved for %(count)s employees. Look at the salary table, "
                                "then finalise.") % {"count": count})
    return redirect(reverse("payroll:month", args=[month]) + "#review")


@company_required
def errors_file(request, month):
    _check_month(request, month)
    return marked_file_response(request, _kind(month),
                                f"{request.company.code}_salary_{month:%Y-%m}_mistakes.xlsx")


@company_required
def line_edit(request, month, line_id):
    run = _check_month(request, month)
    if run is None:
        raise Http404
    line = company_get(request, PayrollLine.objects.select_related("employee"), pk=line_id, run=run)
    if run.is_final:
        messages.error(request, _("%(month)s is finalised. Reopen it to make changes.") % {"month": run.label})
        return redirect("payroll:month", month=month)
    form = LineForm(request.POST or None, initial=LineForm.initial_from(line))
    if request.method == "POST" and form.is_valid():
        row = form.row_input()
        try:
            ctx = service.RunContext(request.company, month)
        except MissingRate as exc:
            messages.error(request, str(exc))
            return redirect("payroll:month", month=month)
        emp = next((e for e in ctx.employees if e.pk == line.employee_id), None)
        problems = (service.check_row(ctx, emp, row, strict=True) if emp
                    else [("present", _("This employee is not paid this month."))])
        for key, message in problems:
            form.add_error(key if key in form.fields else None, message)
        if not problems:
            try:
                service.update_line(run, line, row, request.user, request=request)
            except service.PayrollError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, _("Saved. %(name)s: net pay is now ₹%(net)s.")
                                 % {"name": line.name, "net": indian_format(line.net_pay)})
                return redirect(reverse("payroll:month", args=[month]) + "#review")
    more = ("ot_hours", "ot_amount", "incentive", "increment", "advance", "other_deduction", "other_reason",
            "tds_override", "remarks")
    more_open = any(form[name].errors for name in more) or any(
        form.initial.get(name) not in (None, "", 0) for name in more)
    return page(request, "payroll/line_edit.html", _("Change %(name)s — %(month)s") % {"name": line.name, "month": run.label},
                _("Change the numbers and press Save. The salary is worked out again."), form=form, line=line, run=run,
                more_open=more_open)


@company_required
def finalise(request, month):
    run = _check_month(request, month)
    if run is None or not run.lines.exists():
        messages.error(request, _("Upload the attendance sheet first."))
        return redirect("payroll:month", month=month)
    blockers = service.finalise_blockers(run)
    if not blockers:
        # Work the month out again now, so the totals the owner confirms are exactly what gets locked.
        try:
            service.recompute_run(run)
        except (service.PayrollError, MissingRate) as exc:
            blockers = [str(exc)]
    if request.method == "POST" and not blockers:
        try:
            service.finalise(run, request.user, request=request)
        except (service.PayrollError, MissingRate) as exc:
            messages.error(request, str(exc))
            return redirect("payroll:month", month=month)
        messages.success(request, _("%(month)s is finalised. You can now print payslips and download the files.")
                         % {"month": run.label})
        return redirect(reverse("payroll:month", args=[month]) + "#documents")
    return page(request, "payroll/finalise.html", _("Finalise %(month)s") % {"month": run.label},
                _("Check the totals below. If they are right, press Yes, finalise."),
                run=run, totals=run.totals(), blockers=blockers)


@company_required
def reopen(request, month):
    run = _check_month(request, month)
    if run is None:
        raise Http404
    blockers = service.reopen_blockers(run)
    form = ReopenForm(request.POST or None)
    if request.method == "POST" and not blockers and form.is_valid():
        try:
            service.reopen(run, request.user, form.cleaned_data["reason"], request=request)
        except service.PayrollError as exc:
            messages.error(request, str(exc))
            return redirect("payroll:month", month=month)
        messages.warning(request, _("%(month)s is open again. Make the changes, then finalise it again. "
                                    "Payslips will say 'Revised'.") % {"month": run.label})
        return redirect("payroll:month", month=month)
    return page(request, "payroll/reopen.html", _("Reopen %(month)s") % {"month": run.label},
                _("Write why you are reopening the month, then press Reopen."), run=run, form=form, blockers=blockers)


REVIEW_HEADERS = ["Emp Code", "Name", "Paid days", "LOP", "Gross", "PF (emp)", "ESI (emp)", "PT", "TDS", "Advance",
                  "Other ded.", "Net pay", "Employer PF+EDLI+admin", "Employer ESI", "Total cost", "Notes"]
REVIEW_MONEY = REVIEW_HEADERS[4:15]


@company_required
def export(request, month):
    run = _check_month(request, month)
    if run is None or not run.lines.exists():
        raise Http404
    lines = run.lines.select_related("employee").order_by("employee__emp_code")
    rows = [[l.code, l.name, as_number(l.paid_days), as_number(l.lop), as_number(l.gross), as_number(l.ee_pf),
             as_number(l.ee_esi), as_number(l.pt), as_number(l.tds), as_number(l.advance),
             as_number(l.other_deduction_amt), as_number(l.net_pay), as_number(l.employer_pf),
             as_number(l.er_esi), as_number(l.employer_cost), "; ".join(_note_texts(l))] for l in lines]
    t = run.totals()
    totals = ["Total", f"{t['count']} employees", None, None, as_number(t["gross"]), as_number(t["ee_pf"]),
              as_number(t["ee_esi"]), as_number(t["pt"]), as_number(t["tds"]), as_number(t["advance"]),
              as_number(t["other_deduction_amt"]), as_number(t["net_pay"]), as_number(t["employer_pf"]),
              as_number(t["er_esi"]), as_number(t["employer_cost"]),
              f"PF admin minimum top-up {as_number(run.pf_admin_topup)}" if run.pf_admin_topup else None]
    wb = table_workbook(month_label(month), REVIEW_HEADERS, rows, money_columns=REVIEW_MONEY, totals=totals,
                        widths={"Name": 24, "Notes": 60})
    return xlsx_response(wb, f"{request.company.code}_salary_table_{month:%Y-%m}.xlsx")
