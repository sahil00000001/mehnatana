"""Gross salaries page (company setting 'Gross salary split' on).

Each person has one gross salary a month (kept as their salary structure). Basic is paid at the minimum wage and
the rest of the gross as incentive; absent days cut both; ESI and PF are worked out on Basic. This page shows the
gross of everyone paid in the month, lets you change it (from this month on), and - before the month is started -
takes absent days instead of an attendance sheet (everyone else counts as present on every working day).
"""
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _

from companies.models import Company, MinimumWage
from core.formatting import month_label
from core.forms import WholeRupeesField
from core.tenancy import company_required, page
from statutory.rates import MissingRate

from . import service
from .models import PayrollRun
from .warnings import message_for

NOTE_CODES = ("GROSS_BELOW_MINIMUM",)


def _days(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except ArithmeticError:
        raise ValidationError(_("Write the absent days as a number, like 2 or 1.5."))
    if value < 0 or value * 2 != (value * 2).to_integral_value():
        raise ValidationError(_("Use whole or half days only, like 2 or 1.5."))
    return value


def _read(post, people):
    """{employee id: (absent days or None, gross or None)} and {employee id: [errors]} from the form."""
    field = WholeRupeesField(required=False)
    values, errors = {}, {}
    for emp in people:
        problems, gross, absent = [], None, None
        try:
            gross = field.clean(post.get(f"gross_{emp.pk}", ""))
        except ValidationError as exc:
            problems += exc.messages
        try:
            absent = _days(post.get(f"absent_{emp.pk}", ""))
        except ValidationError as exc:
            problems += exc.messages
        if problems:
            errors[emp.pk] = problems
        values[emp.pk] = (absent, gross)
    return values, errors


@company_required
def amounts(request, month):
    company = request.company
    if month < company.payroll_start_month:
        return redirect("payroll:months")
    back = reverse("payroll:month", args=[month])
    if company.amount_paid_mode != Company.AmountPaid.GROSS:
        messages.info(request, _("Turn on 'Gross salary split' in Settings → Payroll rules first."))
        return redirect("companies:payroll")
    run = PayrollRun.objects.filter(company=company, month=month).first()
    if run is not None and run.is_final:
        messages.error(request, _("%(month)s is finalised. Reopen it to make changes.") % {"month": run.label})
        return redirect(back)
    blocker = service.upload_blocker(company, month)
    if blocker:
        messages.error(request, blocker)
        return redirect(back)

    people = list(service.employees_for_month(company, month))
    typed, typing_errors = _read(request.POST, people) if request.method == "POST" else ({}, {})
    try:
        if request.method == "POST" and not typing_errors:
            try:
                _run, changed = service.save_gross_page(company, month, typed, request.user, request=request)
            except service.PayrollError as exc:
                if "Correct the rows" not in str(exc):  # row mistakes are shown row by row below
                    messages.error(request, str(exc))
                    return redirect(back)
            else:
                text = _("Saved. %(month)s is worked out from the gross salaries.") % {"month": month_label(month)}
                if changed:
                    text += " " + _("%(count)s gross salaries changed from %(month)s on.") % {
                        "count": changed, "month": month_label(month)}
                messages.success(request, text)
                return redirect(back + "#review")
        rows, errors, started = service.gross_page(
            company, month, {k: v for k, v in typed.items() if k not in typing_errors})
    except MissingRate as exc:
        messages.error(request, str(exc))
        return redirect(back)

    for emp_id, problems in typing_errors.items():
        errors.setdefault(emp_id, []).extend(problems)
    for item in rows:
        emp = item["emp"]
        item["errors"] = errors.get(emp.pk, [])
        item["notes"] = [str(message_for(code)) for code in item["warnings"] if code in NOTE_CODES]
        if request.method == "POST":
            item["gross_value"] = request.POST.get(f"gross_{emp.pk}", "")
            item["absent_value"] = request.POST.get(f"absent_{emp.pk}", "")
        else:
            item["gross_value"] = "" if item["current"] is None else f"{item['current']:.0f}"
            item["absent_value"] = ""
        item["days"] = item["line"].paid_days if item["line"] is not None else item["working"]
    return page(request, "payroll/amounts.html", _("Gross salaries — %(month)s") % {"month": month_label(month)},
                _("Check each person's gross salary a month and press Save. PayCore cuts it for absent days and "
                  "works out ESI, PF and the take-home pay."),
                run=run, month=month, rows=rows, has_errors=bool(errors), started=started,
                minimum_wages=MinimumWage.in_force(company.state, month))
