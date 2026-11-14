"""The two quick entry pages of a month (no attendance sheet needed), filled from the owner's monthly list.

By days present: each person's days present. The monthly salary in the person's profile is paid for those days.
With the minimum-wage split on (Payroll rules), Basic is the minimum wage for the days and the rest is incentive.

By take-home (minimum-wage split on): each person's paid days and the amount the owner pays in hand that month.
The take-home pay is made exactly that amount: Basic is the month's Basic (the full month's minimum wage, or the
company's figure) for the days written, the paid days are never changed, ESI is the company's fixed amount, PF is worked out on Basic and the
incentive makes up the rest. An amount that would pay less than the minimum wage is refused, with the least that
must be paid.

The figures are worked out again as you type (preview, nothing saved); Save stores the whole month.
"""
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _

from companies.models import Company, MinimumWage
from core.formatting import month_label, plain_number
from core.forms import WholeRupeesField
from core.tenancy import company_required, page
from statutory.rates import MissingRate

from . import service
from .models import PayrollRun

FIGURES = ("basic", "incentive", "gross", "ee_esi", "ee_pf", "net_pay")


def _days(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except ArithmeticError:
        raise ValidationError(_("Write the paid days as a number, like 26 or 25.5."))
    if not value.is_finite() or value < 0 or value * 2 != (value * 2).to_integral_value():
        raise ValidationError(_("Use whole or half days only, like 26 or 25.5."))
    return value


def _typed(data, emp, method):
    """(paid days or None, take-home or None) and [problems] for one person, from the form or the preview."""
    problems, pay, paid = [], None, None
    if method == service.BY_TAKE_HOME:
        try:
            pay = WholeRupeesField(required=False).clean(data.get(f"pay_{emp.pk}", ""))
        except ValidationError as exc:
            problems += exc.messages
    try:
        paid = _days(data.get(f"days_{emp.pk}", ""))
    except ValidationError as exc:
        problems += exc.messages
    return (paid, pay), problems


def _notes(item):
    notes = []
    result = item["result"]
    if result is not None and "DAYS_REDUCED" in item["warnings"]:
        notes.append(_("Paid days shown as %(paid)s (not %(given)s): at the minimum wage, %(given)s days would pay "
                       "more than the take-home amount.") % {
            "paid": plain_number(result.paid_days), "given": plain_number(item["row"].days_given)})
    if "TAKE_HOME_NOT_EXACT" in item["warnings"]:
        notes.append(_("The take-home could not be made exactly the amount (a deduction changes in steps). "
                       "Please check."))
    if "SALARY_BELOW_MINIMUM_WAGE" in item["warnings"]:
        notes.append(_("The monthly salary in the profile is below the minimum wage, so Basic is paid at the "
                       "minimum wage. Revise the salary."))
    return notes


def _allowed(request, month, method):
    """None if the page can be used, else where to send the person (with a message)."""
    company = request.company
    if month < company.payroll_start_month:
        return redirect("payroll:months")
    back = reverse("payroll:month", args=[month])
    if method == service.BY_TAKE_HOME and company.amount_paid_mode != Company.AmountPaid.GROSS:
        messages.info(request, _("Turn on 'Minimum-wage split' in Settings → Payroll rules first."))
        return redirect("companies:payroll")
    run = PayrollRun.objects.filter(company=company, month=month).first()
    if run is not None and run.is_final:
        messages.error(request, _("%(month)s is finalised. Reopen it to make changes.") % {"month": run.label})
        return redirect(back)
    blocker = service.upload_blocker(company, month)
    if blocker:
        messages.error(request, blocker)
        return redirect(back)
    return None


def _entry(request, month, method):
    company = request.company
    refused = _allowed(request, month, method)
    if refused is not None:
        return refused
    back = reverse("payroll:month", args=[month])
    run = PayrollRun.objects.filter(company=company, month=month).first()
    people = list(service.employees_for_month(company, month))
    typed, typing_errors = {}, {}
    if request.method == "POST":
        for emp in people:
            typed[emp.pk], problems = _typed(request.POST, emp, method)
            if problems:
                typing_errors[emp.pk] = problems
    try:
        if request.method == "POST" and not typing_errors:
            try:
                service.save_entry_page(company, month, method, typed, request.user, request=request)
            except service.PayrollError as exc:
                if "Correct the rows" not in str(exc):  # row mistakes are shown row by row below
                    messages.error(request, str(exc))
                    return redirect(back)
            else:
                if method == service.BY_TAKE_HOME:
                    text = _("Saved. In %(month)s each take-home pay is the amount written.")
                else:
                    text = _("Saved. %(month)s is worked out from the days present.")
                messages.success(request, text % {"month": month_label(month)})
                return redirect(back + "#review")
        rows, errors, started = service.entry_page(
            company, month, method, {k: v for k, v in typed.items() if k not in typing_errors})
    except MissingRate as exc:
        messages.error(request, str(exc))
        return redirect(back)

    for emp_id, problems in typing_errors.items():
        errors.setdefault(emp_id, []).extend(problems)
    totals = dict.fromkeys(FIGURES, Decimal(0))
    for item in rows:
        emp = item["emp"]
        item["errors"] = errors.get(emp.pk, [])
        item["notes"] = _notes(item)
        if item["result"] is not None and not item["errors"]:
            for key in FIGURES:
                totals[key] += getattr(item["result"], key)
        if request.method == "POST":
            item["pay_value"] = request.POST.get(f"pay_{emp.pk}", "")
            item["days_input"] = request.POST.get(f"days_{emp.pk}", "")
        else:
            item["pay_value"] = "" if item["pay"] is None else f"{item['pay']:.0f}"
            item["days_input"] = plain_number(item["days_value"])
    if method == service.BY_TAKE_HOME:
        title = _("By take-home — %(month)s") % {"month": month_label(month)}
        instruction = _("Write each person's paid days and the amount the owner pays in hand, from the owner's "
                        "list. The figures change as you type; press Save when done.")
    else:
        title = _("By days present — %(month)s") % {"month": month_label(month)}
        instruction = _("Write the days each person was present. The salary in each profile is paid for those "
                        "days. The figures change as you type; press Save when done.")
    return page(request, "payroll/amounts.html", title, instruction,
                run=run, month=month, rows=rows, has_errors=bool(errors), started=started, method=method,
                by_take_home=method == service.BY_TAKE_HOME, totals=totals,
                splits=company.amount_paid_mode == Company.AmountPaid.GROSS,
                take_home_esi=company.take_home_esi, take_home_basic=company.take_home_basic, minimum_wages=MinimumWage.in_force(company.state, month))


@company_required
def amounts(request, month):
    """By take-home (the owner's list of paid days and amounts in hand)."""
    return _entry(request, month, service.BY_TAKE_HOME)


@company_required
def by_days(request, month):
    """By days present (the salary in each profile, paid for the days)."""
    return _entry(request, month, service.BY_DAYS)


@company_required
def preview(request, month):
    """One person worked out as typed on an entry page, as JSON. Nothing is saved."""
    company = request.company
    method = request.GET.get("method", "")
    if method not in (service.BY_DAYS, service.BY_TAKE_HOME) or month < company.payroll_start_month:
        raise Http404
    emp_id = request.GET.get("emp", "")
    emp = next((e for e in service.employees_for_month(company, month) if str(e.pk) == emp_id), None)
    if emp is None:
        raise Http404
    run = PayrollRun.objects.filter(company=company, month=month).first()
    if run is not None and run.is_final:
        return JsonResponse({"ok": False, "errors": [_("This month is finalised.")], "notes": []})
    data = {f"days_{emp.pk}": request.GET.get("days", ""), f"pay_{emp.pk}": request.GET.get("pay", "")}
    (days, pay), problems = _typed(data, emp, method)
    if problems:
        return JsonResponse({"ok": False, "errors": problems, "notes": []})
    try:
        item = service.preview_row(company, month, method, emp, days, pay)
    except MissingRate as exc:
        return JsonResponse({"ok": False, "errors": [str(exc)], "notes": []})
    if item["errors"] or item["result"] is None:
        return JsonResponse({"ok": False, "errors": item["errors"], "notes": []})
    result = item["result"]
    figures = {key: int(getattr(result, key)) for key in FIGURES}
    figures["paid_days"] = plain_number(result.paid_days)
    return JsonResponse({"ok": True, "errors": [], "notes": _notes(item), "figures": figures,
                         "reduced": "DAYS_REDUCED" in item["warnings"]})
