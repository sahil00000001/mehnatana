"""Salary from the amount paid: write what the owner pays each person (gross or take-home, as the company chose).

Basic becomes the minimum wage, ESI and PF (if applicable) are worked out on it and the rest is incentive, so
the salary matches the amount. Works on a month started from the attendance sheet, or starts the month itself
from the salaries alone (full month worked, minus any absent days written here).
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

NOTE_CODES = ("PAID_BELOW_MINIMUM", "AMOUNT_NOT_EXACT", "BELOW_MINIMUM_WAGE")


def _amount(raw):
    return WholeRupeesField(required=False).clean(raw)


def _days(raw):
    raw = (raw or "").strip()
    if not raw:
        return Decimal("0")
    try:
        value = Decimal(raw)
    except ArithmeticError:
        raise ValidationError(_("Write the absent days as a number, like 2 or 1.5."))
    if value < 0 or value * 2 != (value * 2).to_integral_value():
        raise ValidationError(_("Use whole or half days only, like 2 or 1.5."))
    return value


def _read(post, keys, with_days):
    """{key: amount} or {key: (absent, amount)}, and {key: [errors]}, from the form."""
    values, errors = {}, {}
    for key in keys:
        problems = []
        try:
            amount = _amount(post.get(f"amount_{key}", ""))
        except ValidationError as exc:
            problems += exc.messages
            amount = None
        absent = Decimal("0")
        if with_days:
            try:
                absent = _days(post.get(f"absent_{key}", ""))
            except ValidationError as exc:
                problems += exc.messages
        if problems:
            errors[key] = problems
        values[key] = (absent, amount) if with_days else amount
    return values, errors


@company_required
def amounts(request, month):
    company = request.company
    if month < company.payroll_start_month:
        return redirect("payroll:months")
    back = reverse("payroll:month", args=[month])
    if company.amount_paid_mode == Company.AmountPaid.OFF:
        messages.info(request, _("Turn on 'Salary from the amount paid' in Settings → Payroll rules first."))
        return redirect("companies:payroll")
    run = PayrollRun.objects.filter(company=company, month=month).first()
    if run is not None and run.is_final:
        messages.error(request, _("%(month)s is finalised. Reopen it to make changes.") % {"month": run.label})
        return redirect(back)
    started = run is not None and run.lines.exists()
    if not started:
        blocker = service.upload_blocker(company, month)
        if blocker:
            messages.error(request, blocker)
            return redirect(back)

    posted = request.method == "POST"
    try:
        if started:
            lines = list(run.lines.select_related("employee").order_by("employee__emp_code"))
            typed, typing_errors = _read(request.POST, [l.pk for l in lines], False) if posted else ({}, {})
            if posted and not typing_errors:
                try:
                    changed = service.save_amounts(run, typed, request.user, request=request)
                except service.PayrollError as exc:
                    if "Correct the amounts" not in str(exc):  # row mistakes are shown row by row below
                        messages.error(request, str(exc))
                        return redirect(back)
                else:
                    messages.success(request, _("Saved. %(count)s people worked out again.") % {"count": changed})
                    return redirect(reverse("payroll:amounts", args=[month]))
            items, errors = service.amounts_preview(run, {k: v for k, v in typed.items() if k not in typing_errors})
            for item in items:
                line = item["line"]
                item.update(key=line.pk, code=line.code, name=line.name, emp_id=line.employee_id,
                            days=item["result"].paid_days if item["result"] else line.paid_days,
                            saved_amount=line.amount_paid_input)
        else:
            people = list(service.employees_for_month(company, month))
            typed, typing_errors = _read(request.POST, [e.pk for e in people], True) if posted else ({}, {})
            if posted and not typing_errors:
                try:
                    service.save_salaries(company, month, typed, request.user, request=request)
                except service.PayrollError as exc:
                    if "Correct the rows" not in str(exc):
                        messages.error(request, str(exc))
                        return redirect(back)
                else:
                    messages.success(request, _("%(month)s is started from the salaries written here. Check the "
                                                "figures below, then finalise the month.") % {"month": month_label(month)})
                    return redirect(reverse("payroll:amounts", args=[month]))
            items, errors = service.salaries_preview(
                company, month, {k: v for k, v in typed.items() if k not in typing_errors})
            for item in items:
                emp = item["emp"]
                item.update(key=emp.pk, code=emp.emp_code, name=emp.full_name, emp_id=emp.pk, days=item["working"],
                            saved_amount=None)
    except MissingRate as exc:
        messages.error(request, str(exc))
        return redirect(back)

    for line_id, problems in typing_errors.items():
        errors.setdefault(line_id, []).extend(problems)
    for item in items:
        key = item["key"]
        item["errors"] = errors.get(key, [])
        item["notes"] = [str(message_for(code)) for code in item["warnings"] if code in NOTE_CODES]
        if posted:
            item["value"] = request.POST.get(f"amount_{key}", "")
            item["absent"] = request.POST.get(f"absent_{key}", "")
        else:
            item["value"] = "" if item["saved_amount"] is None else f"{item['saved_amount']:.0f}"
            item["absent"] = ""
    in_hand = company.amount_paid_mode == Company.AmountPaid.IN_HAND
    title = (_("Take-home pay — %(month)s") if in_hand else _("Gross salaries — %(month)s")) % {
        "month": month_label(month)}
    return page(request, "payroll/amounts.html", title,
                _("Write what the owner pays each person and press Save. Leave a box empty to use the person's "
                  "salary structure."),
                run=run, month=month, rows=items, has_errors=bool(errors), in_hand=in_hand, started=started,
                minimum_wages=MinimumWage.in_force(company.state, month))
