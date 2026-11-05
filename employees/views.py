import math
from collections import Counter
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.translation import ngettext

from advances.models import Advance
from core.audit import audit, diff, snapshot
from core.formatting import add_months, fmt_date, indian_format, month_label, month_start, plain_number
from core.tenancy import company_get, owner_required, page
from excel_io import builders
from excel_io.common import xlsx_response
from excel_io.errors import (
    forget_failed_upload, forget_pending_upload, keep_pending_upload, marked_file_response, pending_upload_data,
    pending_upload_is_newer, remember_failed_upload,
)
from excel_io.parsers import SheetError, parse_employees, read_upload
from letters.models import IssuedLetter, LetterType
from payroll import engine, service
from payroll.forms import UploadForm
from payroll.models import PayrollLine
from statutory.rates import MissingRate, rates_for_month

from .forms import EmployeeForm, NameForm, RelieveForm, ReviseSalaryForm, revision_month_choices
from .importer import UploadRejected, apply_salary_changes, salary_changes, save_employees
from .models import Department, Designation, Employee, SalaryRevision
from .rules import (
    code_locked, default_eps_member, finalised_after, finalised_month_after, first_revision_month, next_emp_code,
)

UPLOAD_KIND = "employees"


def _employees(request):
    return Employee.objects.filter(company=request.company).select_related("designation", "department", "reports_to")


def _report_draft_problems(request):
    """After a change that affects pay, draft months are worked out again (finalised months never change)."""
    for label, problem in service.recalculate_drafts(request.company):
        messages.warning(request, _("%(month)s could not be worked out again: %(problem)s Upload its sheet again.")
                         % {"month": label, "problem": problem})


def _warn_joined_in_finalised_months(request, codes):
    """New staff whose joining month is already finalised are paid from the next open month: say so."""
    last = service.latest_finalised(request.company)
    if not last or not codes:
        return
    late = Employee.objects.filter(company=request.company, emp_code__in=codes, date_of_joining__lte=last.month_end)
    names = ", ".join(e.full_name for e in late)
    if names:
        messages.warning(request, _("%(month)s is already finalised, so %(names)s will be paid from %(next)s. "
                                    "To pay them for earlier days, reopen the month or add it as incentive.")
                         % {"month": last.label, "names": names, "next": month_label(add_months(last.month, 1))})


def _fix_filters(company):
    try:
        from reports.fixes import fix_querysets
    except ImportError:
        return {}
    return fix_querysets(company, timezone.localdate())


# ---------------------------------------------------------------- list
@owner_required
def employee_list(request):
    company = request.company
    qs = _employees(request).prefetch_related("revisions")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "ACTIVE")
    dept = request.GET.get("dept", "")
    fix = request.GET.get("fix", "")
    fix_label = ""
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(surname__icontains=q) | Q(emp_code__icontains=q)
                       | Q(mobile__icontains=q))
    if fix:
        fixes = _fix_filters(company)
        if fix in fixes:
            fix_label, fix_qs = fixes[fix]
            qs = qs.filter(pk__in=fix_qs.values("pk"))
            status = "ALL"
    today = timezone.localdate()
    if status == "ACTIVE":  # people leaving on a future date are still working
        qs = qs.filter(Q(status=Employee.Status.ACTIVE) | Q(date_of_leaving__gte=today))
    elif status == "RELIEVED":
        qs = qs.filter(status=Employee.Status.RELIEVED, date_of_leaving__lt=today)
    if dept.isdigit() and len(dept) < 10:
        qs = qs.filter(department_id=int(dept))

    month = service.first_open_month(company)
    try:
        month_rates = rates_for_month(month)
    except MissingRate:
        month_rates = None
    rows = []
    for emp in qs:
        rev = emp.structure_on(month)
        esi = service.esi_covered_now(emp, company, month_rates, month) if month_rates else False
        rows.append({"emp": emp, "gross": rev.gross if rev else None,
                     "pf": company.pf_enabled and emp.pf_member, "esi": esi,
                     "leaving_on": emp.date_of_leaving if emp.date_of_leaving and emp.date_of_leaving >= today else None})
    return page(request, "employees/list.html", _("Employees"),
                _("Find a person, or add new staff one by one or from Excel."),
                rows=rows, q=q, status=status, dept=dept, fix=fix, fix_label=fix_label,
                departments=Department.objects.filter(company=company))


# ---------------------------------------------------------------- add / edit
@owner_required
def employee_add(request):
    company = request.company
    initial = {"emp_code": next_emp_code(company), "pf_member": company.pf_enabled,
               "tax_regime": company.default_tax_regime}
    form = EmployeeForm(request.POST or None, company=company, adding=True, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            emp = form.save(commit=False)
            emp.created_by = request.user
            values = form.structure_values()
            structure = engine.Structure(values["basic"], values["da"], values["hra"], values["conveyance"],
                                         values["other_allowance"])
            choice = form.cleaned_data["eps_choice"]
            emp.eps_member = emp.pf_member and (
                default_eps_member(emp.date_of_birth, emp.date_of_joining, structure) if choice == "AUTO"
                else choice == "YES")
            emp.save()
            SalaryRevision.objects.create(
                company=company, employee=emp, effective_from=first_revision_month(company, emp.date_of_joining),
                source=SalaryRevision.Source.OPENING, reason=_("Starting salary"), created_by=request.user, **values)
            audit(request, "employee.create", emp, {"after": snapshot(emp), "salary": values})
        messages.success(request, _("%(name)s is added with code %(code)s.") % {"name": emp.full_name, "code": emp.emp_code})
        if engine.age_on(emp.date_of_birth, emp.date_of_joining) < 18:
            messages.warning(request, _("This person was under 18 at joining. Please check the rules for young workers."))
        _warn_joined_in_finalised_months(request, [emp.emp_code])
        _report_draft_problems(request)
        if request.POST.get("then") == "another":
            return redirect("employees:add")
        return redirect("employees:detail", pk=emp.pk)
    return page(request, "employees/form.html", _("Add employee"),
                _("Fill this form and press Save. Fields with * must be filled."), form=form, adding=True)


@owner_required
def employee_edit(request, pk):
    emp = company_get(request, _employees(request), pk=pk)
    before = snapshot(emp)
    form = EmployeeForm(request.POST or None, instance=emp, company=request.company, adding=False,
                        code_locked=code_locked(emp))
    if request.method == "POST" and form.is_valid():
        emp = form.save()
        changes = diff(before, snapshot(emp))
        if "aadhaar" in changes:
            changes["aadhaar"] = ["(hidden)", "(hidden)"]
        audit(request, "employee.update", emp, {"changes": changes})
        messages.success(request, _("Saved."))
        _report_draft_problems(request)
        return redirect("employees:detail", pk=emp.pk)
    return page(request, "employees/form.html", _("Edit %(name)s") % {"name": emp.full_name},
                _("Change what is needed and press Save. To change the salary use Revise salary."),
                form=form, emp=emp, adding=False)


# ---------------------------------------------------------------- view page
@owner_required
def employee_detail(request, pk):
    emp = company_get(request, _employees(request).prefetch_related("revisions"), pk=pk)
    revisions = list(emp.revisions.select_related("payroll_run").order_by("-effective_from", "-id"))
    advances = list(Advance.objects.filter(company=request.company, employee=emp).order_by("-date_given"))
    payslips = (PayrollLine.objects.filter(company=request.company, employee=emp, run__status="FINALISED")
                .select_related("run").order_by("-run__month"))
    letters = IssuedLetter.objects.filter(company=request.company, employee=emp).order_by("-issue_date")
    has_appointment = letters.filter(letter_type=LetterType.APPOINTMENT).exists()
    current = emp.structure_on(timezone.localdate())
    return page(request, "employees/detail.html", emp.full_name,
                _("Everything about this person in one place. Use the buttons to make changes."),
                emp=emp, revisions=revisions, advances=advances, payslips=payslips, letters=letters,
                current=current, has_appointment=has_appointment,
                can_undo=emp.status == Employee.Status.RELIEVED
                and finalised_month_after(request.company, emp.date_of_leaving) is None)


# ---------------------------------------------------------------- relieve
@owner_required
def relieve(request, pk):
    emp = company_get(request, _employees(request), pk=pk)
    if emp.status == Employee.Status.RELIEVED:
        messages.info(request, _("%(name)s is already relieved.") % {"name": emp.full_name})
        return redirect("employees:detail", pk=emp.pk)
    form = RelieveForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        dol = form.cleaned_data["date_of_leaving"]
        paid_later = finalised_after(emp, dol).select_related("run").first()
        paid_that_month = (emp.payroll_lines.filter(run__status="FINALISED", run__month=month_start(dol))
                           .select_related("run").first())
        first_day = max(month_start(dol), emp.date_of_joining)
        if dol < emp.date_of_joining:
            form.add_error("date_of_leaving", _("The last working day cannot be before the date of joining."))
        elif paid_later:
            form.add_error("date_of_leaving", _("%(name)s was paid in %(month)s, which is finalised. The last working "
                                                "day cannot be before that month.")
                           % {"name": emp.first_name, "month": paid_later.run.label})
        elif paid_that_month and paid_that_month.paid_days > (dol - first_day).days + 1:
            earliest = first_day + timedelta(days=math.ceil(paid_that_month.paid_days) - 1)
            form.add_error("date_of_leaving", _(
                "%(month)s is finalised and paid %(name)s for %(paid)s days. Choose a last working day on or "
                "after %(day)s, or reopen %(month)s first.") % {
                    "month": paid_that_month.run.label, "name": emp.first_name,
                    "paid": plain_number(paid_that_month.paid_days), "day": fmt_date(earliest)})
        else:
            before = snapshot(emp)
            emp = form.save(commit=False)
            emp.status = Employee.Status.RELIEVED
            emp.save()
            audit(request, "employee.relieve", emp, {"changes": diff(before, snapshot(emp))})
            if paid_that_month:
                messages.success(request, _("%(name)s is relieved from %(date)s. %(month)s is already paid.")
                                 % {"name": emp.full_name, "date": fmt_date(dol), "month": month_label(dol)})
            else:
                messages.success(request, _("%(name)s is relieved from %(date)s and will be paid for %(month)s.")
                                 % {"name": emp.full_name, "date": fmt_date(dol), "month": month_label(dol)})
            messages.warning(request, _("Under the Code on Wages, final dues must be paid within two working days of leaving."))
            _report_draft_problems(request)
            return redirect("employees:detail", pk=emp.pk)
    return page(request, "employees/relieve.html", _("Relieve %(name)s") % {"name": emp.full_name},
                _("Fill the last working day and the reason, then press Relieve."), form=form, emp=emp)


@owner_required
def undo_relieve(request, pk):
    emp = company_get(request, _employees(request), pk=pk)
    if emp.status != Employee.Status.RELIEVED:
        return redirect("employees:detail", pk=emp.pk)
    blocked = finalised_month_after(request.company, emp.date_of_leaving)
    if request.method == "POST" and not blocked:
        before = snapshot(emp)
        emp.status = Employee.Status.ACTIVE
        emp.date_of_leaving = None
        emp.leaving_reason = ""
        emp.resignation_date = None
        emp.save()
        audit(request, "employee.undo_relieve", emp, {"changes": diff(before, snapshot(emp))})
        messages.success(request, _("%(name)s is active again.") % {"name": emp.full_name})
        _report_draft_problems(request)
        return redirect("employees:detail", pk=emp.pk)
    return page(request, "employees/undo_relieve.html", _("Undo relieve — %(name)s") % {"name": emp.full_name},
                _("This makes the person active again, as if they never left."), emp=emp, blocked=blocked)


# ---------------------------------------------------------------- revise salary
@owner_required
def revise_salary(request, pk):
    emp = company_get(request, _employees(request).prefetch_related("revisions"), pk=pk)
    company = request.company
    first_open = service.first_open_month(company)
    choices = revision_month_choices(company, emp, first_open)
    current = emp.structure_on(first_open)
    initial = {}
    if current:
        initial = {"basic": current.basic, "da": current.da, "hra": current.hra, "conveyance": current.conveyance,
                   "other_allowance": current.other_allowance}
    form = ReviseSalaryForm(request.POST or None, month_choices=choices, initial=initial)
    if request.method == "POST" and form.is_valid():
        values = {n: form.cleaned_data[n] or 0 for n in ("basic", "da", "hra", "conveyance", "other_allowance")}
        rev = SalaryRevision.objects.create(
            company=company, employee=emp, effective_from=form.cleaned_data["effective_month"],
            reason=form.cleaned_data["reason"], source=SalaryRevision.Source.MANUAL, created_by=request.user, **values)
        old_gross = current.gross if current else 0
        audit(request, "salary.revise", rev, {"employee": emp.emp_code, "from": str(rev.effective_from),
                                              "old_gross": old_gross, "new_gross": rev.gross, "values": values})
        change = rev.gross - old_gross
        messages.success(request, _("New salary from %(month)s. Monthly gross: ₹%(old)s → ₹%(new)s (%(sign)s₹%(diff)s).") % {
            "month": month_label(rev.effective_from), "old": indian_format(old_gross), "new": indian_format(rev.gross),
            "sign": "+" if change >= 0 else "−", "diff": indian_format(abs(change))})
        _report_draft_problems(request)
        return redirect("employees:detail", pk=emp.pk)
    return page(request, "employees/revise.html", _("Revise salary — %(name)s") % {"name": emp.full_name},
                _("Choose the month and type the new monthly amounts, then press Save. Old salaries are kept."),
                form=form, emp=emp, current=current)


# ---------------------------------------------------------------- organisation chart
@owner_required
def organisation(request):
    people = list(_employees(request).filter(status=Employee.Status.ACTIVE).order_by("emp_code"))
    ids = {p.pk for p in people}
    children = {}
    for p in people:
        boss = p.reports_to_id if p.reports_to_id in ids else None
        children.setdefault(boss, []).append(p)

    def build(boss_id, seen):
        nodes = []
        for p in children.get(boss_id, []):
            if p.pk in seen:
                continue
            nodes.append({"emp": p, "children": build(p.pk, seen | {p.pk})})
        return nodes

    return page(request, "employees/organisation.html", _("Organisation"),
                _("Who reports to whom. Change it with Edit on the employee page."), tree=build(None, set()))


# ---------------------------------------------------------------- departments and designations
def _names_page(request, model, title, instruction, url_name, edit_url):
    company = request.company
    form = NameForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        name = " ".join(form.cleaned_data["name"].split())
        if model.objects.filter(company=company, name__iexact=name).exists():
            form.add_error("name", _("This name is already in the list."))
        else:
            obj = model.objects.create(company=company, name=name, created_by=request.user)
            audit(request, f"{model._meta.model_name}.create", obj, {"name": name})
            messages.success(request, _("Added %(name)s.") % {"name": name})
            return redirect(url_name)
    items = model.objects.filter(company=company).order_by("-is_active", "name")
    return page(request, "employees/names.html", title, instruction, form=form, items=items,
                edit_url=edit_url)


def _name_edit(request, model, pk, back):
    obj = company_get(request, model, pk=pk)
    form = NameForm(request.POST or None, initial={"name": obj.name, "is_active": obj.is_active})
    if request.method == "POST" and form.is_valid():
        name = " ".join(form.cleaned_data["name"].split())
        if model.objects.filter(company=request.company, name__iexact=name).exclude(pk=obj.pk).exists():
            form.add_error("name", _("This name is already in the list."))
        else:
            before = snapshot(obj)
            obj.name, obj.is_active = name, form.cleaned_data["is_active"]
            obj.save()
            audit(request, f"{model._meta.model_name}.update", obj, {"changes": diff(before, snapshot(obj))})
            messages.success(request, _("Saved."))
            return redirect(back)
    return page(request, "employees/name_edit.html", obj.name, _("Change the name or untick 'In use', then press Save."),
                form=form, back=back)


@owner_required
def departments(request):
    return _names_page(request, Department, _("Departments"),
                       _("Type a new department and press Add. Departments are never deleted."), "employees:departments",
                       "employees:department_edit")


@owner_required
def department_edit(request, pk):
    return _name_edit(request, Department, pk, "employees:departments")


@owner_required
def designations(request):
    return _names_page(request, Designation, _("Designations"),
                       _("Type a new designation (job title) and press Add."), "employees:designations",
                       "employees:designation_edit")


@owner_required
def designation_edit(request, pk):
    return _name_edit(request, Designation, pk, "employees:designations")


# ---------------------------------------------------------------- Excel
@owner_required
def upload_sample(request):
    return xlsx_response(builders.employee_workbook(request.company), "employees_sample.xlsx")


@owner_required
def export(request):
    employees = _employees(request).prefetch_related("revisions").order_by("emp_code")
    return xlsx_response(builders.employee_workbook(request.company, employees),
                         f"{request.company.code}_employees.xlsx")


UPLOAD_TITLE = _lazy("Upload employees from Excel")
UPLOAD_INSTRUCTION = _lazy("Download the sample, fill one row per person, then choose the file and press Upload and check.")


def _upload_failed(request, form, data, errors, file_errors):
    forget_failed_upload(request, UPLOAD_KIND)
    forget_pending_upload(request, UPLOAD_KIND)
    if data is not None and errors:
        remember_failed_upload(request, UPLOAD_KIND, data, errors, file_errors)
    return page(request, "employees/upload.html", UPLOAD_TITLE, UPLOAD_INSTRUCTION, form=form,
                upload_errors=errors, file_errors=file_errors, errors_file=bool(data is not None and errors))


def _parse(company, data):
    try:
        rows, errors, file_errors = parse_employees(data, company)
    except SheetError as exc:
        return [], [], [str(exc)]
    if not errors and not file_errors and not rows:
        file_errors = [_("The sheet has no employees in it. Fill one row per person under the headings.")]
    return rows, errors, file_errors


def _salary_months(company):
    first_open = service.first_open_month(company)
    return [add_months(first_open, i) for i in range(13)]


def _finish_upload(request, rows, change_salaries_from=None):
    """Save the checked rows (and the salary changes, if the owner said yes) in one go."""
    company = request.company
    with transaction.atomic():
        added, updated = save_employees(company, rows, request.user, request=request)
        months_used = []
        if change_salaries_from is not None:
            months_used = apply_salary_changes(company, salary_changes(company, rows), change_salaries_from,
                                               request.user, request=request)
        audit(request, "employee.upload", None,
              {"added": added, "updated": updated, "salaries_changed": len(months_used)})
    forget_failed_upload(request, UPLOAD_KIND)
    forget_pending_upload(request, UPLOAD_KIND)
    text = _("%(added)s added, %(updated)s updated, %(same)s already up to date.") % {
        "added": added, "updated": updated, "same": len(rows) - added - updated}
    for month, count in sorted(Counter(months_used).items()):
        text += " " + ngettext("New salary from %(month)s for %(count)s person.",
                               "New salary from %(month)s for %(count)s people.", count) % {
            "month": month_label(month), "count": count}
    messages.success(request, text)
    _warn_joined_in_finalised_months(request, [r["emp_code"] for r in rows if r["_new"]])
    _report_draft_problems(request)
    return redirect("employees:list")


@owner_required
def upload(request):
    company = request.company
    form = UploadForm(request.POST or None, request.FILES or None)
    form.fields["file"].label = _("Employee list (.xlsx)")
    if request.method == "POST" and form.is_valid():
        try:
            data = read_upload(form.cleaned_data["file"])
        except SheetError as exc:
            return _upload_failed(request, form, None, [], [str(exc)])
        rows, errors, file_errors = _parse(company, data)
        if errors or file_errors:
            return _upload_failed(request, form, data, errors, file_errors)
        changes = salary_changes(company, rows)
        if not changes:
            try:
                return _finish_upload(request, rows)
            except UploadRejected as exc:
                return _upload_failed(request, form, data, exc.errors, [])
        # Ask once: the file has different salaries for people already in the app.
        token = keep_pending_upload(request, UPLOAD_KIND, data)
        months = _salary_months(company)
        return page(request, "employees/upload_confirm.html", _("Salary changes in your file"),
                    _("Your file is correct. Some people have a different salary in it. Choose what to do."),
                    changes=changes, months=months, added=sum(1 for r in rows if r["_new"]),
                    updated=sum(1 for r in rows if not r["_new"]), token=token)
    if request.method == "GET":
        forget_pending_upload(request, UPLOAD_KIND)  # Cancel or a fresh start: the waiting file is not needed
    return page(request, "employees/upload.html", UPLOAD_TITLE, UPLOAD_INSTRUCTION, form=form)


@owner_required
def upload_confirm(request):
    """The owner's one answer about salary changes: change them (from a month) or keep the old ones."""
    if request.method != "POST":
        return redirect("employees:upload")
    company = request.company
    token = request.POST.get("token", "")
    if pending_upload_is_newer(request, UPLOAD_KIND, token):
        messages.error(request, _("Nothing was saved: you uploaded another file after this one. "
                                  "Please choose the file you want and press Upload and check again."))
        forget_pending_upload(request, UPLOAD_KIND)
        return redirect("employees:upload")
    data = pending_upload_data(request, UPLOAD_KIND, token)
    form = UploadForm()
    form.fields["file"].label = _("Employee list (.xlsx)")
    if data is None:
        messages.error(request, _("Please choose the file again and press Upload and check."))
        return redirect("employees:upload")
    rows, errors, file_errors = _parse(company, data)  # checked again: the list may have changed meanwhile
    if errors or file_errors:
        return _upload_failed(request, form, data, errors, file_errors)
    month = None
    if request.POST.get("salaries") == "change":
        allowed = {f"{m:%Y-%m}": m for m in _salary_months(company)}
        month = allowed.get(request.POST.get("month"), next(iter(allowed.values())))
    try:
        return _finish_upload(request, rows, change_salaries_from=month)
    except UploadRejected as exc:
        return _upload_failed(request, form, data, exc.errors, [])


@owner_required
def upload_errors(request):
    return marked_file_response(request, UPLOAD_KIND, "employees_mistakes.xlsx")
