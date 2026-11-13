import re
from datetime import date

from django import forms
from django.core.validators import MaxLengthValidator
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from core.forms import IndianDatesMixin, ModelErrorsOnPage, whole_rupees
from core.formatting import add_months, fmt_date, month_label, month_start
from core.validators import digits_only, upper_no_spaces

from .models import Department, Designation, Employee

HELP = {
    "emp_code": _("A short code, different for each person, like E001."),
    "relation_name": _("Printed on the payslip and letters."),
    "mobile": _("10 digits, starting with 6, 7, 8 or 9."),
    "date_of_joining": _("The first day of work."),
    "reports_to": _("The person's manager. Leave empty for the person at the top."),
    "nature_of_duties": _("The main work in a few words. Printed on the appointment letter."),
    "pan": _("Needed for income tax. Like ABCDE1234F."),
    "aadhaar": _("Optional. Saved only if the person agrees (tick the box below)."),
    "aadhaar_consent": _("The employee has agreed to give the Aadhaar number."),
    "uan": _("12-digit number from the EPFO portal. Needed for the PF file."),
    "name_as_per_uan": _("Exactly as in the EPFO records, in capitals. Leave empty to use the name above."),
    "esic_ip_no": _("10-digit ESIC insurance number. Needed for the ESI file."),
    "pf_member": _("Tick if PF is cut from this person's salary."),
    "eps_member": _("Pension scheme. Usually yes if PF wages at joining were within the PF ceiling and age under 58."),
    "esi_override": _("Auto: ESI applies when salary is up to ₹21,000 (₹25,000 for a person with disability)."),
    "is_pwd": _("Person with disability. ESI limit becomes ₹25,000."),
    "pt_exempt": _("Tick only if the law exempts this person from professional tax."),
    "tax_regime": _("New regime is the default. Ask the employee or your CA."),
    "old_regime_deductions_annual": _("Old regime only: yearly total of 80C (incl. PF), 80D, HRA exemption, home-loan interest."),
    "payment_mode": _("How salary is paid each month."),
    "ifsc": _("Like SBIN0001234. Printed on the cheque book."),
}

SALARY_FIELDS = ["basic", "da", "hra", "conveyance", "other_allowance"]
UNIQUE_IDS = {"uan": _("UAN"), "pan": _("PAN"), "esic_ip_no": _("ESIC IP number"), "aadhaar": _("Aadhaar number")}


class SuggestInput(forms.TextInput):
    """A text box with a list of suggestions (HTML datalist: works without JavaScript)."""

    def __init__(self, options=(), attrs=None):
        super().__init__(attrs={"autocomplete": "off", **(attrs or {})})
        self.options = list(options)

    def render(self, name, value, attrs=None, renderer=None):
        attrs = dict(attrs or {})
        list_id = f"{attrs.get('id', name)}_list"
        attrs["list"] = list_id
        html = super().render(name, value, attrs, renderer)
        options = format_html_join("", '<option value="{}">', ((o,) for o in self.options))
        return format_html('{}<datalist id="{}">{}</datalist>', html, list_id, options)
TIDIED_FIELDS = ["emp_code", "mobile", "pan", "aadhaar", "uan", "esic_ip_no", "ifsc"]


class EmployeeForm(ModelErrorsOnPage, IndianDatesMixin, forms.ModelForm):
    GROUPS = [
        (_("Personal"), ["emp_code", "first_name", "surname", "relation_type", "relation_name", "gender",
                         "date_of_birth", "mobile", "email", "address"]),
        (_("Job"), ["date_of_joining", "designation_name", "department_name", "reports_to", "employment_type",
                    "skill_category", "nature_of_duties"]),
        (_("Salary per month"), SALARY_FIELDS),
        (_("PF / ESI / Tax"), ["pf_member", "eps_choice", "eps_member", "uan", "name_as_per_uan", "esic_ip_no",
                               "esi_override", "is_pwd", "pt_exempt", "tax_regime", "old_regime_deductions_annual"]),
        (_("Bank"), ["payment_mode", "bank_name", "bank_account_no", "ifsc"]),
        (_("Documents"), ["pan", "aadhaar", "aadhaar_consent"]),
    ]

    basic = whole_rupees(_("Basic ₹"), required=True)
    da = whole_rupees(_("DA (dearness allowance) ₹"))
    hra = whole_rupees(_("HRA (house rent allowance) ₹"))
    conveyance = whole_rupees(_("Conveyance ₹"))
    other_allowance = whole_rupees(_("Other allowance ₹"))
    eps_choice = forms.ChoiceField(
        label=_("Pension (EPS) member"),
        choices=[("AUTO", _("Decide by the rule")), ("YES", _("Yes")), ("NO", _("No"))], initial="AUTO",
        help_text=_("Rule: Yes if PF wages at joining are within the PF ceiling and age is under 58, else No."))
    old_regime_deductions_annual = whole_rupees(_("Old regime deductions per year ₹"))
    designation_name = forms.CharField(
        label=_("Designation"), max_length=100,
        help_text=_("Job title, like Helper or Cashier. Pick from the list or type a new one."))
    department_name = forms.CharField(
        label=_("Department"), max_length=100, required=False,
        help_text=_("Optional, like Store or Accounts. Pick from the list or type a new one."))

    class Meta:
        model = Employee
        fields = [
            "emp_code", "first_name", "surname", "relation_type", "relation_name", "gender", "date_of_birth",
            "mobile", "email", "address", "date_of_joining", "reports_to",
            "employment_type", "skill_category", "nature_of_duties", "pan", "aadhaar", "aadhaar_consent", "uan",
            "name_as_per_uan", "esic_ip_no", "payment_mode", "bank_name", "bank_account_no", "ifsc", "pf_member",
            "eps_member", "esi_override", "is_pwd", "pt_exempt", "tax_regime", "old_regime_deductions_annual",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "nature_of_duties": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, company, adding, code_locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.company, self.adding = company, adding
        self.instance.company = company
        if adding:
            one_gross_salary(self, company)
        for name in TIDIED_FIELDS:  # people type spaces; the saved value is checked after tidying
            field = self.fields[name]
            field.max_length = None
            field.validators = [v for v in field.validators if not isinstance(v, MaxLengthValidator)]
            field.widget.attrs.pop("maxlength", None)
        for name, text in HELP.items():
            if name in self.fields:
                self.fields[name].help_text = text
        # Empty only on records brought over from an older system (see the model); always asked for here.
        self.fields["date_of_birth"].required = True
        self.fields["relation_name"].required = True
        active = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE)
        if self.instance.pk:
            active = active.exclude(pk=self.instance.pk)
        self.fields["reports_to"].queryset = active.order_by("emp_code")
        self.fields["reports_to"].label_from_instance = lambda e: f"{e.full_name} ({e.emp_code})"
        self.fields["reports_to"].empty_label = _("— nobody (this person is at the top) —")
        self.fields["gender"].choices = [("", _("— choose —"))] + [c for c in self.fields["gender"].choices if c[0]]
        self.fields["designation_name"].widget = SuggestInput(
            Designation.objects.filter(company=company, is_active=True).values_list("name", flat=True))
        self.fields["department_name"].widget = SuggestInput(
            Department.objects.filter(company=company, is_active=True).values_list("name", flat=True))
        if self.instance.pk:
            self.initial.setdefault("designation_name", self.instance.designation.name if self.instance.designation_id else "")
            self.initial.setdefault("department_name", self.instance.department.name if self.instance.department_id else "")
        if adding:
            del self.fields["eps_member"]
        else:
            for name in SALARY_FIELDS + ["eps_choice"]:
                del self.fields[name]
            if self.instance.aadhaar:
                self.initial["aadhaar"] = ""
                self.fields["aadhaar"].help_text = _(
                    "Saved: %(masked)s. Type a new number only to change it. Untick consent to remove it."
                ) % {"masked": self.instance.aadhaar_masked}
        if code_locked:
            self.fields["emp_code"].disabled = True
            self.fields["emp_code"].help_text = _("The code cannot change after a month with this person is finalised.")
        if not company.pf_enabled:
            for name in ("pf_member", "eps_choice", "eps_member", "uan", "name_as_per_uan"):
                if name in self.fields:
                    self.fields[name].help_text = _("Your business has PF switched off in Payroll rules.")

    def groups(self):
        out = []
        for title, names in self.GROUPS:
            fields = [self[name] for name in names if name in self.fields]
            if fields:
                out.append((title, fields))
        return out

    # ---- tidy the identity numbers people type with spaces or small letters
    def clean_emp_code(self):
        code = upper_no_spaces(self.cleaned_data["emp_code"])
        clash = Employee.objects.filter(company=self.company, emp_code=code)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("Another employee already has the code %(code)s.") % {"code": code})
        return code

    def clean_pan(self):
        return upper_no_spaces(self.cleaned_data["pan"])

    def clean_ifsc(self):
        return upper_no_spaces(self.cleaned_data["ifsc"])

    def clean_name_as_per_uan(self):
        return " ".join(self.cleaned_data["name_as_per_uan"].upper().split())

    def _digits(self, name):
        value = self.cleaned_data[name]
        cleaned = digits_only(value)
        return cleaned if re.fullmatch(r"[\d\s-]*", value or "") else value

    def clean_uan(self):
        return self._digits("uan")

    def clean_esic_ip_no(self):
        return self._digits("esic_ip_no")

    def clean_mobile(self):
        value = self.cleaned_data["mobile"] or ""
        digits = digits_only(value)
        if re.fullmatch(r"[\d\s()+-]*", value):
            if len(digits) == 12 and digits.startswith("91"):
                return digits[2:]  # +91 98450 12345
            if len(digits) == 11 and digits.startswith("0"):
                return digits[1:]  # 098450 12345
            return digits
        return value

    def clean_aadhaar(self):
        value = self._digits("aadhaar")
        if not value and self.instance.pk and self.instance.aadhaar:
            return self.instance.aadhaar  # empty box on edit = keep the saved number
        return value

    @staticmethod
    def _named(model, company, name):
        """Existing department/designation with this name (any case), or a new unsaved one."""
        name = " ".join((name or "").split())
        if not name:
            return None
        return model.objects.filter(company=company, name__iexact=name).first() or model(company=company, name=name)

    def clean(self):
        data = super().clean()
        doj = data.get("date_of_joining")
        if doj and self.instance.pk:
            paid_before = (self.instance.payroll_lines.filter(run__status="FINALISED", run__month__lt=month_start(doj))
                           .select_related("run").order_by("run__month").first())
            if paid_before:
                self.add_error("date_of_joining", _(
                    "%(name)s was already paid for %(month)s (finalised), so the date of joining cannot be after "
                    "that month.") % {"name": self.instance.first_name, "month": paid_before.run.label})
            elif self.instance.date_of_leaving and doj > self.instance.date_of_leaving:
                self.add_error("date_of_joining", _(
                    "The date of joining cannot be after the last working day (%(date)s).")
                    % {"date": fmt_date(self.instance.date_of_leaving)})
        self._designation = self._named(Designation, self.company, data.get("designation_name"))
        self._department = self._named(Department, self.company, data.get("department_name"))
        for field, label in UNIQUE_IDS.items():
            value = data.get(field)
            if not value or field in self.errors:
                continue
            owner = Employee.objects.filter(company=self.company, **{field: value}).exclude(pk=self.instance.pk).first()
            if owner:
                self.add_error(field, _("This %(what)s already belongs to %(name)s (%(code)s). Each person has their own.")
                               % {"what": label, "name": owner.full_name, "code": owner.emp_code})
        return data

    def save(self, commit=True):
        for attr, field in (("_designation", "designation"), ("_department", "department")):
            obj = getattr(self, attr, None)
            if obj is not None and obj.pk is None:
                obj.save()  # a new name typed by the owner
            setattr(self.instance, field, obj)
        return super().save(commit)

    def clean_old_regime_deductions_annual(self):
        return self.cleaned_data.get("old_regime_deductions_annual") or 0

    def structure_values(self):
        return {name: self.cleaned_data.get(name) or 0 for name in SALARY_FIELDS}


class RelieveForm(ModelErrorsOnPage, IndianDatesMixin, forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["date_of_leaving", "leaving_reason", "resignation_date", "exit_remarks"]
        widgets = {"exit_remarks": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "date_of_leaving": _("The last day the person worked."),
            "resignation_date": _("Only if the person resigned: the date on the resignation letter."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_of_leaving"].required = True
        self.fields["leaving_reason"].required = True

    def clean(self):
        data = super().clean()
        if data.get("leaving_reason") != Employee.LeavingReason.RESIGNED:
            data["resignation_date"] = None  # only a person who resigned has a resignation letter
            return data
        resigned, last_day = data.get("resignation_date"), data.get("date_of_leaving")
        if not resigned and "resignation_date" not in self.errors:
            self.add_error("resignation_date", _("Enter the date of the resignation letter."))
        elif resigned and last_day and resigned > last_day:
            self.add_error("resignation_date", _("The resignation letter must be dated on or before the last "
                                                 "working day (%(date)s).") % {"date": fmt_date(last_day)})
        return data


def revision_month_choices(company, employee, first_open):
    start = max(first_open, company.payroll_start_month, month_start(employee.date_of_joining))
    return [(f"{m:%Y-%m}", month_label(m)) for m in (add_months(start, i) for i in range(13))]


def one_gross_salary(form, company):
    """With the minimum-wage split on, a salary is one amount a month (kept in Basic): Basic is paid at the
    minimum wage and the rest as incentive, so the other heads are not asked for."""
    from companies.models import Company

    if company.amount_paid_mode != Company.AmountPaid.GROSS:
        return
    for name in SALARY_FIELDS[1:]:
        form.fields.pop(name, None)
    form.fields["basic"].label = _("Monthly salary ₹")
    form.fields["basic"].help_text = _("The salary for a full month. A month worked out by days present pays it for "
                                       "the days: Basic at the minimum wage, the rest incentive. A month worked out "
                                       "by take-home uses the amounts written for that month instead.")


class ReviseSalaryForm(forms.Form):
    effective_month = forms.ChoiceField(label=_("New salary from"),
                                        help_text=_("A finalised month cannot be changed, so it is not in the list."))
    basic = whole_rupees(_("Basic ₹"), required=True)
    da = whole_rupees(_("DA ₹"))
    hra = whole_rupees(_("HRA ₹"))
    conveyance = whole_rupees(_("Conveyance ₹"))
    other_allowance = whole_rupees(_("Other allowance ₹"))
    reason = forms.CharField(label=_("Reason"), max_length=200, help_text=_("For example: yearly increment."))

    def __init__(self, *args, month_choices, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["effective_month"].choices = month_choices
        if company is not None:
            one_gross_salary(self, company)

    def clean_effective_month(self):
        year, month = self.cleaned_data["effective_month"].split("-")
        return date(int(year), int(month), 1)


class NameForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=100)
    is_active = forms.BooleanField(label=_("In use"), required=False, initial=True,
                                   help_text=_("Untick to hide it from the lists. It is never deleted."))
