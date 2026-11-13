"""Take-home page (Payroll rules: take-home matching on), filled from the owner's monthly list.

For each person: the paid days and the amount the owner pays in hand that month. The take-home pay is made
exactly that amount: Basic is the minimum wage for the paid days (fewer days are shown if needed), ESI and PF are
worked out on Basic, and the incentive makes up the rest. The amount can differ every month; the people's
salaries are not changed. No attendance sheet is needed.
"""
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
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

NOTE_CODES = ("DAYS_REDUCED",)


def _days(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except ArithmeticError:
        raise ValidationError(_("Write the paid days as a number, like 26 or 25.5."))
    if value < 0 or value * 2 != (value * 2).to_integral_value():
        raise ValidationError(_("Use whole or half days only, like 26 or 25.5."))
    return value


def _read(post, people):
    """{employee id: (paid days or None, take-home or None)} and {employee id: [errors]} from the form."""
    field = WholeRupeesField(required=False)
    values, errors = {}, {}
    for emp in people:
        problems, pay, paid = [], None, None
        try:
            pay = field.clean(post.get(f"pay_{emp.pk}", ""))
        except ValidationError as exc:
            problems += exc.messages
        try:
            paid = _days(post.get(f"days_{emp.pk}", ""))
        except ValidationError as exc:
            problems += exc.messages
        if problems:
            errors[emp.pk] = problems
        values[emp.pk] = (paid, pay)
    return values, errors


@company_required
def amounts(request, month):
    company = request.company
    if month < company.payroll_start_month:
        return redirect("payroll:months")
    back = reverse("payroll:month", args=[month])
    if company.amount_paid_mode != Company.AmountPaid.GROSS:
        messages.info(request, _("Turn on 'Take-home matching' in Settings → Payroll rules first."))
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
                service.save_gross_page(company, month, typed, request.user, request=request)
            except service.PayrollError as exc:
                if "Correct the rows" not in str(exc):  # row mistakes are shown row by row below
                    messages.error(request, str(exc))
                    return redirect(back)
            else:
                messages.success(request, _("Saved. In %(month)s each take-home pay is the amount written.")
                                 % {"month": month_label(month)})
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
        item["notes"] = []
        result = item["result"]
        if result is not None and "DAYS_REDUCED" in item["warnings"]:
            item["notes"] = [_("Paid days shown as %(paid)s (not %(given)s): at the minimum wage, %(given)s days "
                               "would pay more than the take-home amount.") % {
                "paid": plain_number(result.paid_days), "given": plain_number(item["row"].days_given)}]
        if "TAKE_HOME_NOT_EXACT" in item["warnings"]:
            item["notes"].append(_("The take-home could not be made exactly the amount (a deduction changes in "
                                   "steps). Please check."))
        if request.method == "POST":
            item["pay_value"] = request.POST.get(f"pay_{emp.pk}", "")
            item["days_input"] = request.POST.get(f"days_{emp.pk}", "")
        else:
            item["pay_value"] = "" if item["pay"] is None else f"{item['pay']:.0f}"
            item["days_input"] = plain_number(item["days_value"])
    return page(request, "payroll/amounts.html", _("Paid days and take-home — %(month)s") % {"month": month_label(month)},
                _("Write each person's paid days and the amount the owner pays in hand, from the owner's list, and "
                  "press Save. The take-home pay becomes exactly that amount."),
                run=run, month=month, rows=rows, has_errors=bool(errors), started=started,
                minimum_wages=MinimumWage.in_force(company.state, month))
