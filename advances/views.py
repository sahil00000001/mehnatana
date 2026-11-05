from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _

from core.audit import audit
from core.formatting import indian_format, month_end, month_label, month_start
from core.tenancy import company_get, owner_required, page
from employees.models import Employee
from payroll import service
from payroll.models import PayrollLine, PayrollRun

from .forms import AdvanceForm, InstalmentForm
from .models import Advance


def _hint_for_uploaded_month(request, employee, since):
    """A month whose sheet is already uploaded keeps the advance typed in it. Say so, and link to the row."""
    line = (PayrollLine.objects.filter(company=request.company, employee=employee, run__status=PayrollRun.Status.DRAFT,
                                       run__month__gte=month_start(since))
            .select_related("run").order_by("run__month").first())
    if line is None:
        return
    month = line.run.month
    due = service.suggested_advance(
        service.open_advances(request.company, [employee.pk], given_by=month_end(month)).get(employee.pk, []))
    if due == line.advance_deduction:
        return
    url = reverse("payroll:line_edit", args=[month, line.pk])
    messages.warning(request, format_html(
        _("The salary sheet for {month} is already uploaded with ₹{typed} advance recovery for {name}, "
          "so this change is not in it yet. <a href=\"{url}\">Change the row for {month}</a> to ₹{due}, "
          "or upload the sheet again."),
        month=month_label(month), typed=indian_format(line.advance_deduction), name=employee.full_name,
        url=url, due=indian_format(due)))


def _advances(request):
    return (Advance.objects.filter(company=request.company).select_related("employee")
            .annotate(recovered_sum=Coalesce(Sum("recoveries__amount"), Decimal("0"))))


@owner_required
def advance_list(request):
    qs = _advances(request).order_by("-date_given", "-id")
    status = request.GET.get("status", "OPEN")
    fix = request.GET.get("fix", "")
    fix_label = ""
    if fix:
        from reports.fixes import fix_querysets

        fixes = fix_querysets(request.company, timezone.localdate())
        if fix in fixes:
            fix_label, fix_qs = fixes[fix]
            qs = qs.filter(pk__in=fix_qs.values("pk"))
    if status in ("OPEN", "CLOSED"):
        qs = qs.filter(status=status)
    rows = [{"adv": a, "recovered": a.recovered_sum, "outstanding": a.amount - a.recovered_sum} for a in qs]
    total = sum((r["outstanding"] for r in rows), Decimal("0"))
    return page(request, "advances/list.html", _("Advances"),
                _("Money given to staff before salary. It is cut from salary month by month."),
                rows=rows, status=status, total=total, fix=fix, fix_label=fix_label)


@owner_required
def advance_add(request):
    initial = {"date_given": timezone.localdate()}
    if request.GET.get("employee", "").isdigit():
        emp = company_get(request, Employee, pk=int(request.GET["employee"]))
        initial["employee"] = emp.pk
    form = AdvanceForm(request.POST or None, company=request.company, initial=initial)
    if request.method == "POST" and form.is_valid():
        adv = form.save(commit=False)
        adv.created_by = request.user
        adv.save()
        audit(request, "advance.create", adv, {"employee": adv.employee.emp_code, "amount": adv.amount,
                                               "instalment": adv.monthly_installment})
        messages.success(request, _("Advance of ₹%(amount)s given to %(name)s. ₹%(cut)s will be cut every month.") % {
            "amount": indian_format(adv.amount), "name": adv.employee.full_name, "cut": indian_format(adv.monthly_installment)})
        _hint_for_uploaded_month(request, adv.employee, adv.date_given)
        return redirect("advances:detail", pk=adv.pk)
    return page(request, "advances/form.html", _("Give advance"),
                _("Fill this form and press Save."), form=form)


@owner_required
def advance_detail(request, pk):
    adv = company_get(request, Advance.objects.select_related("employee"), pk=pk)
    form = InstalmentForm(request.POST or None, initial={"monthly_installment": adv.monthly_installment})
    if request.method == "POST" and adv.status == Advance.Status.OPEN and form.is_valid():
        new = form.cleaned_data["monthly_installment"]
        if new > adv.outstanding:
            form.add_error("monthly_installment", _("The monthly cut cannot be more than what is still to be recovered."))
        else:
            old = adv.monthly_installment
            adv.monthly_installment = new
            adv.save(update_fields=["monthly_installment", "updated_at"])
            audit(request, "advance.instalment", adv, {"before": old, "after": new})
            messages.success(request, _("Monthly cut changed to ₹%(cut)s.") % {"cut": indian_format(new)})
            _hint_for_uploaded_month(request, adv.employee, adv.date_given)
            return redirect("advances:detail", pk=adv.pk)
    recoveries = adv.recoveries.select_related("payroll_line__run").order_by("month")
    return page(request, "advances/detail.html",
                _("Advance — %(name)s") % {"name": adv.employee.full_name},
                _("What was given and what has been cut so far."), adv=adv, recoveries=recoveries, form=form)
