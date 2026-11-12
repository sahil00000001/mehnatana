from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from core.audit import audit, diff, snapshot
from core.formatting import month_label, tax_year_label, tax_year_of
from core.tenancy import company_get, company_required, page
from employees.models import Employee, OpeningBalance
from excel_io import builders
from excel_io.common import xlsx_response
from excel_io.errors import forget_failed_upload, marked_file_response, remember_failed_upload
from excel_io.parsers import SheetError, parse_opening, read_upload
from payroll import service
from payroll.forms import UploadForm
from payroll.models import PayrollRun
from statutory.rates import has_pt_slabs

from .forms import CompanyDetailsForm, OpeningBalanceForm, PayrollRulesForm, PayslipDesignForm

OPENING_KIND = "opening"
OPENING_FIELDS = ["ytd_gross", "ytd_tds", "ytd_pt", "prev_employer_income", "prev_employer_tds"]


@company_required
def settings_index(request):
    return page(request, "companies/index.html", _("Settings"), _("Choose what you want to change."))


@company_required
def company_details(request):
    company = request.company
    before = snapshot(company)
    old_state, old_pt_state = company.state, company.pt_state
    form = CompanyDetailsForm(request.POST or None, request.FILES or None, instance=company)
    if request.method == "POST" and form.is_valid():
        company = form.save(commit=False)
        state_moved = company.state != old_state and old_pt_state in ("", old_state)
        if state_moved:  # professional tax follows the corrected state, unless it was set to another one
            company.pt_state = company.state
        company.save()
        audit(request, "settings.company", company, {"changes": diff(before, snapshot(company))})
        if state_moved:
            _recalculate_drafts(request, company)
            messages.info(request, _("Professional tax now uses the slabs of %(state)s (Settings → Payroll rules).")
                          % {"state": company.state_name})
        messages.success(request, _("Company details saved."))
        return redirect("companies:company")
    return page(request, "companies/company.html", _("Company details"),
                _("These details are printed on payslips and letters. Change them and press Save."), form=form)


def _recalculate_drafts(request, company):
    """Draft months are worked out again with the new rules. Problems are reported, not hidden."""
    for label, problem in service.recalculate_drafts(company):
        messages.warning(request, _("%(month)s could not be worked out again: %(problem)s")
                         % {"month": label, "problem": problem})


@company_required
def payroll_rules(request):
    company = request.company
    before = snapshot(company)
    started = PayrollRun.objects.filter(company=company).exists()
    pt_warning = ""
    state = (request.POST.get("pt_state") if request.method == "POST" else None) or company.pt_state or company.state
    if not has_pt_slabs(state, timezone.localdate()):
        pt_warning = _("No professional tax slabs are set for your state, so PT will be 0. Many states (like Delhi) "
                       "have no professional tax. If yours has, add the slabs in Rates and tax tables (on All companies).")
    form = PayrollRulesForm(request.POST or None, instance=company, start_locked=started, pt_warning=pt_warning)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            company = form.save()
            audit(request, "settings.payroll", company, {"changes": diff(before, snapshot(company))})
        _recalculate_drafts(request, company)
        messages.success(request, _("Payroll rules saved. Months that are not finalised use the new rules."))
        return redirect("companies:payroll")
    more = ("pf_employee_rate", "pf_restrict_to_ceiling", "pt_state", "default_tax_regime", "ot_multiplier",
            "ot_days_divisor", "ot_hours_per_day", "notice_period_days", "probation_months", "higher_wages_avenue")
    return page(request, "companies/payroll.html", _("Payroll rules"),
                _("Each rule has a short explanation. Change what is needed and press Save."), form=form,
                more_open=any(form[name].errors for name in more))


@company_required
def sheet_checks(request):
    """Settings → Attendance sheet checks: switch checks on or off (payroll/checks.py)."""
    from payroll import checks

    company = request.company
    current = checks.settings_for(company)
    errors = {}
    if request.method == "POST":
        chosen = {}
        for key, (label, _help, _default, kind, _number) in checks.CHECKS.items():
            item = {"on": request.POST.get(f"{key}_on") == "on"}
            if kind == "limit":
                raw = request.POST.get(f"{key}_limit", "").strip()
                try:
                    number = Decimal(raw)
                    if number <= 0 or number > 744 or number * 2 != (number * 2).to_integral_value():
                        raise ValueError
                    item["limit"] = int(number) if number == number.to_integral_value() else float(number)
                except (ArithmeticError, ValueError):
                    errors[key] = _("Write a number above 0, like 4 or 4.5.")
                    item["limit"] = raw
            chosen[key] = item
        if not errors:
            before = company.sheet_checks
            company.sheet_checks = chosen
            company.save(update_fields=["sheet_checks", "updated_at"])
            audit(request, "settings.sheet_checks", company, {"before": before, "after": chosen})
            messages.success(request, _("Attendance sheet checks saved. They are used from the next upload."))
            return redirect("companies:checks")
        current = chosen
    rows = []
    for key, (label, help_text, default, kind, number) in checks.CHECKS.items():
        rows.append({"key": key, "label": label, "help": help_text, "kind": kind, "on": current[key]["on"],
                     "limit": current[key].get("limit"), "default_on": default, "error": errors.get(key)})
    return page(request, "companies/checks.html", _("Attendance sheet checks"),
                _("Tick the checks you want when a sheet is uploaded or a person is changed, then press Save."),
                rows=rows, always_on=checks.ALWAYS_ON, has_errors=bool(errors))


@company_required
def payslip_design(request):
    from reports import payslip as designs

    company = request.company
    before = snapshot(company)
    form = PayslipDesignForm(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        company = form.save()
        audit(request, "settings.payslip", company, {"changes": diff(before, snapshot(company))})
        messages.success(request, _("Saved. Payslips will now print in the %(style)s design.")
                         % {"style": company.get_payslip_style_display()})
        return redirect("companies:payslip_design")
    preview = designs.preview_context(company)
    choices = [{"value": value, "label": label, "note": designs.STYLE_NOTES[value],
                "template": designs.STYLE_TEMPLATES[value], "checked": form["payslip_style"].value() == value}
               for value, label in company.PayslipStyle.choices]
    return page(request, "companies/payslip_design.html", _("Payslip design"),
                _("Choose how your payslips look, then press Save. Every design has all the details the law asks for."),
                form=form, choices=choices, **preview)


# ---------------------------------------------------------------- opening balances
def _tax_year(request):
    default = tax_year_of(request.company.payroll_start_month)
    try:
        year = int(request.GET.get("year", default))
    except ValueError:
        return default
    return year if 2000 <= year <= 2100 else default


def _opening_upload_form(*args):
    form = UploadForm(*args)
    form.fields["file"].label = _("Opening balances sheet (.xlsx)")
    return form


@company_required
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
                upload_form=_opening_upload_form(), start_label=month_label(company.payroll_start_month))


@company_required
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


@company_required
def opening_sample(request):
    company = request.company
    year = _tax_year(request)
    employees = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE).order_by("emp_code")
    balances = {ob.employee_id: ob for ob in OpeningBalance.objects.filter(company=company, tax_year=year)}
    return xlsx_response(builders.opening_workbook(company, employees, year, balances), "opening_balances_sample.xlsx")


@company_required
def opening_upload(request):
    company = request.company
    if request.method != "POST":
        return redirect("companies:opening_balances")
    form = _opening_upload_form(request.POST, request.FILES)
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
    year = _tax_year(request)
    return page(request, "companies/opening_upload_result.html", _("Opening balances — mistakes"),
                _("Correct these mistakes in your file and upload it again."), upload_errors=errors,
                file_errors=file_errors, errors_file=bool(data is not None and errors), year=year)


@company_required
def opening_errors(request):
    return marked_file_response(request, OPENING_KIND, "opening_balances_mistakes.xlsx")
