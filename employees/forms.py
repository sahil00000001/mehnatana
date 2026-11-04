import re
from datetime import date

from django import forms
from django.core.validators import MaxLengthValidator
from django.utils.translation import gettext_lazy as _

from core.forms import IndianDatesMixin, whole_rupees
from core.formatting import add_months, month_label, month_start
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
TIDIED_FIELDS = ["emp_code", "mobile", "pan", "aadhaar", "uan", "esic_ip_no", "ifsc"]


class EmployeeForm(IndianDatesMixin, forms.ModelForm):
    GROUPS = [
        (_("Personal"), ["emp_code", "first_name", "surname", "relation_type", "relation_name", "gender",
                         "date_of_birth", "mobile", "email", "address"]),
        (_("Job"), ["date_of_joining", "department", "designation", "reports_to", "employment_type",
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

    class Meta:
        model = Employee
        fields = [
            "emp_code", "first_name", "surname", "relation_type", "relation_name", "gender", "date_of_birth",
            "mobile", "email", "address", "date_of_joining", "department", "designation", "reports_to",
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
        for name in TIDIED_FIELDS:  # people type spaces; the saved value is checked after tidying
            field = self.fields[name]
            field.max_length = None
            field.validators = [v for v in field.validators if not isinstance(v, MaxLengthValidator)]
            field.widget.attrs.pop("maxlength", None)
        for name, text in HELP.items():
            if name in self.fields:
                self.fields[name].help_text = text
        active = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE)
        if self.instance.pk:
            active = active.exclude(pk=self.instance.pk)
        self.fields["reports_to"].queryset = active.order_by("emp_code")
        self.fields["reports_to"].label_from_instance = lambda e: f"{e.full_name} ({e.emp_code})"
        self.fields["department"].queryset = Department.objects.filter(company=company, is_active=True)
        self.fields["designation"].queryset = Designation.objects.filter(company=company, is_active=True)
        if self.instance.pk:  # keep a value that was made inactive later
            if self.instance.department_id:
                self.fields["department"].queryset |= Department.objects.filter(pk=self.instance.department_id)
            if self.instance.designation_id:
                self.fields["designation"].queryset |= Designation.objects.filter(pk=self.instance.designation_id)
        self.fields["designation"].help_text = _("Add new ones on the Designations page (Settings).")
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
        return self._digits("mobile")

    def clean_aadhaar(self):
        value = self._digits("aadhaar")
        if not value and self.instance.pk and self.instance.aadhaar:
            return self.instance.aadhaar  # empty box on edit = keep the saved number
        return value

    def clean_old_regime_deductions_annual(self):
        return self.cleaned_data.get("old_regime_deductions_annual") or 0

    def structure_values(self):
        return {name: self.cleaned_data.get(name) or 0 for name in SALARY_FIELDS}


class RelieveForm(IndianDatesMixin, forms.ModelForm):
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
        if data.get("leaving_reason") == Employee.LeavingReason.RESIGNED and not data.get("resignation_date"):
            self.add_error("resignation_date", _("Enter the date of the resignation letter."))
        return data


def revision_month_choices(company, employee, first_open):
    start = max(first_open, company.payroll_start_month, month_start(employee.date_of_joining))
    return [(f"{m:%Y-%m}", month_label(m)) for m in (add_months(start, i) for i in range(13))]


class ReviseSalaryForm(forms.Form):
    effective_month = forms.ChoiceField(label=_("New salary from"),
                                        help_text=_("A finalised month cannot be changed, so it is not in the list."))
    basic = whole_rupees(_("Basic ₹"), required=True)
    da = whole_rupees(_("DA ₹"))
    hra = whole_rupees(_("HRA ₹"))
    conveyance = whole_rupees(_("Conveyance ₹"))
    other_allowance = whole_rupees(_("Other allowance ₹"))
    reason = forms.CharField(label=_("Reason"), max_length=200, help_text=_("For example: yearly increment."))

    def __init__(self, *args, month_choices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["effective_month"].choices = month_choices

    def clean_effective_month(self):
        year, month = self.cleaned_data["effective_month"].split("-")
        return date(int(year), int(month), 1)


class NameForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=100)
    is_active = forms.BooleanField(label=_("In use"), required=False, initial=True,
                                   help_text=_("Untick to hide it from the lists. It is never deleted."))
