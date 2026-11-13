"""
All companies: the one login sees every company here, adds new ones and chooses
which company to work on. The chosen company is kept in the session; every other
screen works only on that company (request.company, set by core.middleware).
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.audit import audit
from core.tenancy import SESSION_COMPANY, page
from employees.models import Employee
from payroll.models import PayrollRun

from .forms import CompanyCreateForm, MinimumWageForm
from .models import Company, MinimumWage

logger = logging.getLogger(__name__)


def _safe_next(request, value):
    if value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()},
                                                 require_https=request.is_secure()):
        return value
    return None


def open_company(request, company):
    request.session[SESSION_COMPANY] = company.pk
    request.company = company


def _attention_count(company):
    from core.notices import needs_attention, notices_for

    try:
        return len(needs_attention(notices_for(company)))
    except Exception:  # noqa: BLE001 - the count is a convenience; the list must always open
        logger.exception("Could not work out notices for company %s", company.pk)
        return 0


@login_required
def company_list(request):
    companies = list(Company.objects.order_by("trade_name"))
    staff = dict(Employee.objects.filter(status=Employee.Status.ACTIVE).values("company_id")
                 .annotate(n=Count("id")).values_list("company_id", "n"))
    latest = {}
    for run in PayrollRun.objects.order_by("company_id", "-month"):
        latest.setdefault(run.company_id, run)
    rows = [{"company": c, "staff": staff.get(c.pk, 0), "latest": latest.get(c.pk),
             "attention": _attention_count(c) if c.is_active and settings.SHOW_NOTICES else 0,
             "current": request.company is not None and request.company.pk == c.pk}
            for c in companies]
    active = [r for r in rows if r["company"].is_active]
    return page(request, "companies/list.html", _("All companies"),
                _("Press Open to work on a company. Everything you do after that is for that company only."),
                active=active, switched_off=[r for r in rows if not r["company"].is_active],
                next=_safe_next(request, request.GET.get("next")) or "")


@login_required
def company_add(request):
    form = CompanyCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        company = form.save()
        audit(request, "company.add", company, {"code": company.code}, company=company)
        open_company(request, company)
        messages.success(request, _("%(name)s is added and open. Next, add the employees: one by one, or many at "
                                    "once from an Excel file.") % {"name": company.trade_name})
        return redirect("employees:list")
    return page(request, "companies/add.html", _("Add a company"),
                _("Fill the boxes marked * and press Save. Everything can be changed later in Settings."), form=form)


@login_required
@require_POST
def company_open(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if not company.is_active:
        messages.error(request, _("%(name)s is switched off. Switch it on first.") % {"name": company.trade_name})
        return redirect("portal:list")
    open_company(request, company)
    return redirect(_safe_next(request, request.POST.get("next")) or "home")


def _recalculate_state(request, state):
    """Draft months of every company in the state are worked out again with the new minimum wages."""
    from payroll.service import recalculate_drafts

    for company in Company.objects.filter(state=state, is_active=True):
        for label, problem in recalculate_drafts(company):
            messages.warning(request, _("%(company)s, %(month)s could not be worked out again: %(problem)s")
                             % {"company": company.trade_name, "month": label, "problem": problem})


@login_required
def minimum_wages(request):
    """Minimum wages per state (used by 'Salary from the amount paid' and the minimum wage check)."""
    from core.formatting import month_start
    from django.utils import timezone

    if request.method == "POST" and request.POST.get("delete"):
        row = get_object_or_404(MinimumWage, pk=request.POST["delete"])
        audit(request, "minimum_wage.delete", row, {"state": row.state, "from": str(row.effective_from)})
        row.delete()
        _recalculate_state(request, row.state)
        messages.success(request, _("Removed the minimum wages of %(row)s.") % {"row": row})
        return redirect("portal:minimum_wages")

    editing = None
    if request.GET.get("edit"):
        editing = MinimumWage.objects.filter(pk=request.GET["edit"]).first()
    company = getattr(request, "company", None)
    if editing:
        initial = {"state": editing.state, "effective_from": editing.effective_from,
                   **{name: getattr(editing, name) for name in MinimumWage.SKILLS}}
    else:
        initial = {"state": company.state if company else "DL", "effective_from": month_start(timezone.localdate())}
    form = MinimumWageForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        row = MinimumWage.objects.filter(state=data["state"], effective_from=data["effective_from"]).first() \
            or MinimumWage(state=data["state"], effective_from=data["effective_from"])
        for name in MinimumWage.SKILLS:
            setattr(row, name, data.get(name))
        row.save()
        audit(request, "minimum_wage.save", row, {name: getattr(row, name) for name in MinimumWage.SKILLS}
              | {"state": row.state, "from": str(row.effective_from)})
        _recalculate_state(request, row.state)
        messages.success(request, _("Minimum wages saved for %(row)s. Months that are not finalised use them now.")
                         % {"row": row})
        return redirect("portal:minimum_wages")
    return page(request, "companies/minimum_wages.html", _("Minimum wages"),
                _("Write the minimum wage per month for each skill and the month they start. Enter them again "
                  "when the government revises them (usually April and October)."),
                rows=MinimumWage.objects.all(), form=form, editing=editing)


@login_required
@require_POST
def company_switch(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    company.is_active = not company.is_active
    company.save(update_fields=["is_active", "updated_at"])
    audit(request, "company.switch_on" if company.is_active else "company.switch_off", company, company=company)
    if company.is_active:
        messages.success(request, _("%(name)s is switched on.") % {"name": company.trade_name})
    else:
        if request.session.get(SESSION_COMPANY) == company.pk:
            request.session.pop(SESSION_COMPANY, None)
        messages.success(request, _("%(name)s is switched off. Its records are kept; switch it on to open it "
                                    "again.") % {"name": company.trade_name})
    return redirect("portal:list")
