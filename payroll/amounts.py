"""Amounts paid: write what the owner paid each person; Basic becomes the minimum wage and the rest incentive."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _

from companies.models import Company, MinimumWage
from core.forms import WholeRupeesField
from core.tenancy import company_required, page
from statutory.rates import MissingRate

from . import service
from .models import PayrollRun
from .warnings import message_for


def field_name(line):
    return f"amount_{line.pk}"


def _read_amounts(post, lines):
    """{line id: Decimal or None} and {line id: error} from the form."""
    field = WholeRupeesField(required=False)
    amounts, errors = {}, {}
    for line in lines:
        raw = post.get(field_name(line), "")
        try:
            amounts[line.pk] = field.clean(raw)
        except ValidationError as exc:
            errors[line.pk] = [str(m) for m in exc.messages]
            amounts[line.pk] = None
    return amounts, errors


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
    if run is None or not run.lines.exists():
        messages.error(request, _("Upload the attendance sheet first: the days worked come from it."))
        return redirect(back)
    if run.is_final:
        messages.error(request, _("%(month)s is finalised. Reopen it to make changes.") % {"month": run.label})
        return redirect(back)

    lines = list(run.lines.select_related("employee").order_by("employee__emp_code"))
    typed, typing_errors = {}, {}
    try:
        if request.method == "POST":
            typed, typing_errors = _read_amounts(request.POST, lines)
            if not typing_errors:
                try:
                    changed = service.save_amounts(run, typed, request.user, request=request)
                except service.PayrollError:
                    pass  # shown row by row below
                else:
                    messages.success(request, _("Saved. %(count)s people worked out again from the amounts paid.")
                                     % {"count": changed})
                    return redirect(reverse("payroll:amounts", args=[month]))
        rows, errors = service.amounts_preview(run, {k: v for k, v in typed.items() if k not in typing_errors})
    except MissingRate as exc:
        messages.error(request, str(exc))
        return redirect(back)
    for line_id, problems in typing_errors.items():
        errors.setdefault(line_id, []).extend(problems)
    for item in rows:
        line = item["line"]
        item["name"] = field_name(line)
        item["value"] = (request.POST.get(field_name(line), "") if request.method == "POST"
                         else ("" if line.amount_paid_input is None else f"{line.amount_paid_input:.0f}"))
        item["errors"] = errors.get(line.pk, [])
        item["notes"] = [str(message_for(code)) for code in item["warnings"]
                         if code in ("PAID_BELOW_MINIMUM", "AMOUNT_NOT_EXACT", "BELOW_MINIMUM_WAGE")]
    in_hand = company.amount_paid_mode == Company.AmountPaid.IN_HAND
    return page(request, "payroll/amounts.html", _("Amounts paid — %(month)s") % {"month": run.label},
                _("Write what the owner paid each person and press Save. Leave a box empty to use the person's "
                  "salary structure."),
                run=run, month=month, rows=rows, has_errors=bool(errors), in_hand=in_hand,
                minimum_wages=MinimumWage.in_force(company.state, month))
