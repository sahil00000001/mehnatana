"""Salary payments: after a month is finalised, tick who has been paid and when.

Used by the annual salary report for "paid to date" and "pending". Marking a payment
never changes the salary figures themselves.
"""
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.audit import audit
from core.formatting import fmt_date, indian_format
from core.forms import IndianDatesMixin
from core.tenancy import NoSalaryMonth, company_required, page

from .models import PayrollRun

ZERO = Decimal("0")


class PaymentForm(IndianDatesMixin, forms.Form):
    paid_on = forms.DateField(label=gettext_lazy("Paid on"),
                              help_text=gettext_lazy("The day the salary was paid (bank transfer, cash or cheque)."))

    def clean_paid_on(self):
        value = self.cleaned_data["paid_on"]
        if value > timezone.localdate():
            raise ValidationError(_("The payment date cannot be in the future."))
        return value


def payment_totals(lines):
    return {
        "net": sum((l.net_pay for l in lines), ZERO),
        "paid": sum((l.paid_amount for l in lines), ZERO),
        "pending": sum((l.pending for l in lines), ZERO),
        "count": len(lines),
        "paid_count": sum(1 for l in lines if l.is_paid),
    }


@company_required
def payments(request, month):
    run = PayrollRun.objects.filter(company=request.company, month=month).first()
    if run is None:
        raise NoSalaryMonth(month)
    if not run.is_final:
        messages.warning(request, _("Salary payments can be marked after %(month)s is finalised.")
                         % {"month": run.label})
        return redirect("payroll:month", month=run.month)
    lines = list(run.lines.select_related("employee").order_by("employee__emp_code"))
    form = PaymentForm(request.POST or None, initial={"paid_on": timezone.localdate()})
    if request.method == "POST":
        action = request.POST.get("action")
        ids = {int(x) for x in request.POST.getlist("line") if x.isdigit()}
        chosen = [l for l in lines if l.pk in ids]
        if not chosen:
            messages.error(request, _("Tick at least one person first."))
        elif action == "unpaid":
            with transaction.atomic():
                for line in chosen:
                    line.paid_amount, line.paid_on = ZERO, None
                    line.save(update_fields=["paid_amount", "paid_on", "updated_at"])
                audit(request, "payroll.payment_undo", run, {"month": str(run.month),
                                                             "employees": [l.code for l in chosen]})
            messages.success(request, _("%(count)s marked as not paid.") % {"count": len(chosen)})
            return redirect("payroll:payments", month=run.month)
        elif action == "paid" and form.is_valid():
            paid_on = form.cleaned_data["paid_on"]
            with transaction.atomic():
                for line in chosen:
                    line.paid_amount, line.paid_on = line.net_pay, paid_on
                    line.save(update_fields=["paid_amount", "paid_on", "updated_at"])
                amount = sum((l.net_pay for l in chosen), ZERO)
                audit(request, "payroll.payment", run, {"month": str(run.month), "paid_on": str(paid_on),
                                                        "employees": [l.code for l in chosen], "amount": amount})
            messages.success(request, _("%(count)s marked as paid on %(date)s: ₹%(amount)s.") % {
                "count": len(chosen), "date": fmt_date(paid_on), "amount": indian_format(amount)})
            return redirect("payroll:payments", month=run.month)
    return page(request, "payroll/payments.html", _("Salary payments — %(month)s") % {"month": run.label},
                _("Tick the people who have been paid, write the date and press Mark as paid. "
                  "The annual salary report shows what is paid and what is still pending."),
                run=run, lines=lines, form=form, totals=payment_totals(lines))
