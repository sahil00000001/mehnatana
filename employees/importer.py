"""Save a checked employee upload (README §6.2). All or nothing."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from core.audit import audit, diff, snapshot
from excel_io.columns import EMPLOYEE_COLUMNS, header_of
from excel_io.parsers import CellError
from payroll import engine

from .models import Department, Designation, Employee, SalaryRevision
from .rules import default_eps_member, first_revision_month

# Columns copied onto the employee. Empty cells clear text fields; for yes/no and
# drop-down columns an empty cell on an existing employee keeps the saved value.
TEXT_FIELDS = ["first_name", "surname", "relation_name", "mobile", "email", "address", "nature_of_duties", "pan",
               "uan", "esic_ip_no", "bank_name", "bank_account_no", "ifsc"]
CHOICE_FIELDS = ["relation_type", "gender", "employment_type", "skill_category", "payment_mode", "esi_override",
                 "tax_regime"]
BOOL_FIELDS = ["aadhaar_consent", "pf_member", "eps_member", "is_pwd", "pt_exempt"]
SALARY = ["basic", "da", "hra", "conveyance", "other_allowance"]


class UploadRejected(Exception):
    def __init__(self, errors):
        super().__init__("upload rejected")
        self.errors = errors


def _lookup(model, company, cache, name, user):
    if not name:
        return None
    key = name.strip().lower()
    if key not in cache:
        obj = model.objects.filter(company=company, name__iexact=name.strip()).first()
        if obj is None:
            obj = model.objects.create(company=company, name=name.strip(), created_by=user)
        cache[key] = obj
    return cache[key]


def _model_errors(exc, row, code, name):
    errors = []
    for field, messages in exc.message_dict.items():
        column = header_of(EMPLOYEE_COLUMNS, field) if field != "__all__" else ""
        for message in messages:
            errors.append(CellError(row, code, name, column, message))
    return errors


def salary_changes(company, rows):
    """Existing employees whose salary in the sheet differs from their latest salary in the app.

    A row with an empty Basic means "no salary given" and is skipped; other empty
    salary cells count as 0 (as the sample says).
    """
    codes = [r["emp_code"] for r in rows if not r["_new"] and r.get("basic") is not None]
    employees = Employee.objects.filter(company=company, emp_code__in=codes).prefetch_related("revisions")
    by_code = {e.emp_code: e for e in employees}
    changes = []
    for row in rows:
        emp = by_code.get(row["emp_code"])
        if emp is None:
            continue
        latest = max(emp.revisions.all(), key=lambda r: (r.effective_from, r.pk), default=None)
        old = {n: (getattr(latest, n) if latest else Decimal("0")) for n in SALARY}
        new = {n: row.get(n) or Decimal("0") for n in SALARY}
        if any(old[n] != new[n] for n in SALARY):
            # A new salary cannot start before the person joins, nor before a salary already set for later
            # (it would be hidden behind it and the change would be lost).
            earliest = max([first_revision_month(company, emp.date_of_joining)]
                           + ([latest.effective_from] if latest else []))
            changes.append({"employee": emp, "code": emp.emp_code, "name": emp.full_name, "old": old, "new": new,
                            "old_gross": sum(old.values()), "new_gross": sum(new.values()), "earliest": earliest})
    return changes


def apply_salary_changes(company, changes, month, user, request=None):
    """New salary structure from `month` (or later, see `earliest`) for each change. Returns the months used.

    Old salaries stay in the history.
    """
    used = []
    for change in changes:
        month_for_this = max(month, change["earliest"])
        used.append(month_for_this)
        rev = SalaryRevision.objects.create(
            company=company, employee=change["employee"], effective_from=month_for_this,
            source=SalaryRevision.Source.MANUAL, reason="Changed by employee upload", created_by=user,
            **change["new"])
        audit(request, "salary.revise", rev, {"employee": change["code"], "from": str(month_for_this),
                                              "source": "upload", "old_gross": change["old_gross"],
                                              "new_gross": change["new_gross"]},
              company=company)
    return used


def save_employees(company, rows, user, request=None):
    """Create new employees (with their first salary structure) and update existing ones. Returns (added, updated)."""
    added = updated = 0
    errors = []
    with transaction.atomic():
        existing = {e.emp_code: e for e in Employee.objects.filter(company=company)}
        depts, desigs, saved = {}, {}, {}
        for row in rows:
            code = row["emp_code"]
            emp = existing.get(code)
            is_new = emp is None
            before = {} if is_new else snapshot(emp)
            if is_new:
                emp = Employee(company=company, emp_code=code, created_by=user)
            for name in TEXT_FIELDS:
                if name in row:
                    setattr(emp, name, row[name] or "")
            for name in CHOICE_FIELDS + BOOL_FIELDS:
                if row.get(name) is not None:
                    setattr(emp, name, row[name])
            for name in ("date_of_birth", "date_of_joining"):
                if row.get(name):
                    setattr(emp, name, row[name])
            if row.get("aadhaar"):
                emp.aadhaar = row["aadhaar"]
            if row.get("old_regime_deductions_annual") is not None:
                emp.old_regime_deductions_annual = row["old_regime_deductions_annual"]
            emp.department = _lookup(Department, company, depts, row.get("department"), user)
            emp.designation = _lookup(Designation, company, desigs, row.get("designation"), user) or emp.designation
            emp.reports_to = None if is_new else emp.reports_to
            if is_new:
                if row.get("pf_member") is None:
                    emp.pf_member = company.pf_enabled
                if row.get("tax_regime") is None:
                    emp.tax_regime = company.default_tax_regime
                structure = engine.Structure(*(row.get(n) or Decimal("0") for n in SALARY))
                if row.get("eps_member") is None:
                    emp.eps_member = emp.pf_member and default_eps_member(
                        emp.date_of_birth, emp.date_of_joining, structure)
            try:
                emp.full_clean(exclude=["reports_to"], validate_unique=True)
            except ValidationError as exc:
                errors.extend(_model_errors(exc, row["_row"], code, emp.full_name))
                continue
            emp.save()
            if is_new:
                SalaryRevision.objects.create(
                    company=company, employee=emp, effective_from=first_revision_month(company, emp.date_of_joining),
                    basic=row.get("basic") or 0, da=row.get("da") or 0, hra=row.get("hra") or 0,
                    conveyance=row.get("conveyance") or 0, other_allowance=row.get("other_allowance") or 0,
                    source=SalaryRevision.Source.OPENING, reason="Starting salary (employee upload)",
                    created_by=user,
                )
                added += 1
                audit(request, "employee.create", emp, {"source": "upload"}, company=company)
            else:
                changes = diff(before, snapshot(emp))
                if changes:
                    updated += 1
                    audit(request, "employee.update", emp, {"source": "upload", "changes": changes}, company=company)
            saved[code] = (emp, row)
        if errors:
            raise UploadRejected(errors)
        everyone = {e.emp_code: e for e in Employee.objects.filter(company=company)}
        for code, (emp, row) in saved.items():
            boss = row.get("reports_to")
            new_boss = everyone.get(boss) if boss else None
            if (new_boss.pk if new_boss else None) != emp.reports_to_id:
                emp.reports_to = new_boss
                emp.save(update_fields=["reports_to", "updated_at"])
    return added, updated
