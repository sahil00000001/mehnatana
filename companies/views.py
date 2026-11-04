from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from core.audit import audit, diff, snapshot
from core.formatting import month_label, tax_year_label, tax_year_of
from core.tenancy import company_get, owner_required, page
from employees.models import Employee, OpeningBalance
from excel_io import builders
from excel_io.common import xlsx_response
from excel_io.errors import forget_failed_upload, marked_file_response, remember_failed_upload
from excel_io.parsers import SheetError, parse_opening, read_upload
from payroll import service
from payroll.forms import UploadForm
from payroll.models import PayrollRun
from statutory.rates import MissingRate, has_pt_slabs

from .forms import CompanyDetailsForm, OpeningBalanceForm, PayrollRulesForm

OPENING_KIND = "opening"
OPENING_FIELDS = ["ytd_gross", "ytd_tds", "ytd_pt", "prev_employer_income", "prev_employer_tds"]


@owner_required
def settings_index(request):
    return page(request, "companies/index.html", _("Settings"), _("Choose what you want to change."))


@owner_required
def company_details(request):
    company = request.company
    before = snapshot(company)
    form = CompanyDetailsForm(request.POST or None, request.FILES or None, instance=company)
    if request.method == "POST" and form.is_valid():
        company = form.save()
        audit(request, "settings.company", company, {"changes": diff(before, snapshot(company))})
        messages.success(request, _("Company details saved."))
        return redirect("companies:company")
    return page(request, "companies/company.html", _("Company details"),
                _("These details are printed on payslips and letters. Change them and press Save."), form=form)


def _recalculate_drafts(request, company):
    """Draft months are worked out again with the new rules. Problems are reported, not hidden."""
    for run in PayrollRun.objects.filter(company=company, status=PayrollRun.Status.DRAFT):
        if not run.lines.exists():
            continue
        try:
            service.recompute_run(run)
        except (service.PayrollError, MissingRate) as exc:
            messages.warning(request, _("%(month)s could not be worked out again: %(problem)s Upload its sheet again.")
                             % {"month": run.label, "problem": exc})


@owner_required
def payroll_rules(request):
    company = request.company
    before = snapshot(company)
    started = PayrollRun.objects.filter(company=company).exists()
    pt_warning = ""
    state = (request.POST.get("pt_state") if request.method == "POST" else None) or company.pt_state or company.state
    if not has_pt_slabs(state, timezone.localdate()):
        pt_warning = _("No PT slabs set for your state — PT will be 0. Ask your administrator to add them.")
    form = PayrollRulesForm(request.POST or None, instance=company, start_locked=started, pt_warning=pt_warning)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            company = form.save()
            audit(request, "settings.payroll", company, {"changes": diff(before, snapshot(company))})
        _recalculate_drafts(request, company)
        messages.success(request, _("Payroll rules saved. Months that are not finalised use the new rules."))
        return redirect("companies:payroll")
    return page(request, "companies/payroll.html", _("Payroll rules"),
                _("Each rule has a short explanation. Change what is needed and press Save."), form=form)


# ---------------------------------------------------------------- opening balances
def _tax_year(request):
    default = tax_year_of(request.company.payroll_start_month)
    try:
        return int(request.GET.get("year", default))
    except ValueError:
        return default


@owner_required
def opening_balances(request):
    company = request.company
    year = _tax_year(request)
    employees = Employee.objects.filter(company=company).order_by("emp_code")
    balances = {ob.employee_id: ob for ob in OpeningBalance.objects.filter(company=company, tax_year=year)}
    rows = [{"emp": e, "ob": balances.get(e.pk)} for e in employees
            if e.status == Employee.Status.ACTIVE or e.pk in balances]
    start = tax_year_of(company.payroll_start_month)
    years = [start - 1, start, start + 1]
    return page(request, "companies/opening_balances.html", _("Opening balances"),
                _("Only needed if you started using the app after April, or a new person had another job earlier "
                  "this tax year. Used only for income tax (TDS)."),
                rows=rows, year=year, year_label=tax_year_label(year), years=[(y, tax_year_label(y)) for y in years],
                upload_form=UploadForm(), start_label=month_label(company.payroll_start_month))


@owner_required
def opening_balance_edit(request, emp_id):
    company = request.company
    emp = company_get(request, Employee, pk=emp_id)
    year = _tax_year(request)
    ob = OpeningBalance.objects.filter(company=company, employee=emp, tax_year=year).first()
    initial = {f: getattr(ob, f) for f in OPENING_FIELDS} if ob else {}
    form = OpeningBalanceForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        values = {f: form.cleaned_data[f] or Decimal("0") for f in OPENING_FIELDS}
        before = {f: str(getattr(ob, f)) for f in OPENING_FIELDS} if ob else {}
        ob, _created = OpeningBalance.objects.update_or_create(
            company=company, employee=emp, tax_year=year, defaults={**values, "created_by": request.user})
        audit(request, "opening_balance.save", ob, {"employee": emp.emp_code, "tax_year": year, "before": before,
                                                    "after": {k: str(v) for k, v in values.items()}})
        _recalculate_drafts(request, company)
        messages.success(request, _("Opening balance saved for %(name)s.") % {"name": emp.full_name})
        return redirect(reverse("companies:opening_balances") + f"?year={year}")
    return page(request, "companies/opening_balance_form.html",
                _("Opening balance — %(name)s — %(year)s") % {"name": emp.full_name, "year": tax_year_label(year)},
                _("Type the amounts and press Save. Leave a box empty for zero."), form=form, emp=emp, year=year)


@owner_required
def opening_sample(request):
    company = request.company
    year = _tax_year(request)
    employees = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE).order_by("emp_code")
    balances = {ob.employee_id: ob for ob in OpeningBalance.objects.filter(company=company, tax_year=year)}
    return xlsx_response(builders.opening_workbook(company, employees, year, balances), "opening_balances_sample.xlsx")


@owner_required
def opening_upload(request):
    company = request.company
    if request.method != "POST":
        return redirect("companies:opening_balances")
    form = UploadForm(request.POST, request.FILES)
    data, rows, errors, file_errors = None, [], [], []
    if form.is_valid():
        try:
            data = read_upload(form.cleaned_data["file"])
            rows, errors, file_errors = parse_opening(data, company)
        except SheetError as exc:
            file_errors = [str(exc)]
    else:
        file_errors = [_("Choose the file first.")]
    if not errors and not file_errors:
        with transaction.atomic():
            for row in rows:
                values = {f: row[f] for f in OPENING_FIELDS}
                ob, _created = OpeningBalance.objects.update_or_create(
                    company=company, employee=row["employee"], tax_year=row["tax_year"],
                    defaults={**values, "created_by": request.user})
            audit(request, "opening_balance.upload", None, {"rows": len(rows)})
        forget_failed_upload(request, OPENING_KIND)
        _recalculate_drafts(request, company)
        messages.success(request, _("%(count)s opening balances saved.") % {"count": len(rows)})
        return redirect("companies:opening_balances")
    forget_failed_upload(request, OPENING_KIND)
    if data is not None and errors:
        remember_failed_upload(request, OPENING_KIND, data, errors, file_errors)
    messages.error(request, _("Nothing was saved. Please correct the mistakes listed below and upload again."))
    year = _tax_year(request)
    return page(request, "companies/opening_upload_result.html", _("Opening balances — mistakes"),
                _("Correct these mistakes in your file and upload it again."), upload_errors=errors,
                file_errors=file_errors, errors_file=bool(data is not None and errors), year=year)


@owner_required
def opening_errors(request):
    return marked_file_response(request, OPENING_KIND, "opening_balances_mistakes.xlsx")
