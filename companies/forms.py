from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms import IndianDatesMixin, ModelErrorsOnPage, whole_rupees
from core.validators import upper_no_spaces

from .models import WEEKDAYS, Company, MinimumWage, validate_logo
from .states import STATE_CHOICES

PAYROLL_HELP = {
    "salary_divisor": _("Salary for absent days is cut as monthly salary ÷ this number of days. With working days, "
                        "February, March and April each use their own count (for a six-day week: days in the month "
                        "minus Sundays)."),
    "pf_enabled": _("PF is compulsory once you have 20 or more workers (you may join earlier)."),
    "pf_employee_rate": _("Use 10% only if EPFO has notified your establishment for 10%."),
    "pf_restrict_to_ceiling": _("Usual practice: PF only on wages up to the ceiling (₹25,000 from October 2026)."),
    "esi_enabled": _("ESI applies to places with 10 or more persons in areas notified by ESIC."),
    "pt_enabled": _("Professional tax is a state tax, worked out from the slabs of your state (see More rules)."),
    "pt_state": _("The state whose professional tax slabs are used. Usually your own state."),
    "tds_enabled": _("Income tax is cut each month from staff whose yearly income is taxable."),
    "default_tax_regime": _("Filled in for new staff. Each person can still choose."),
    "ot_multiplier": _("The Labour Codes say overtime is paid at twice the ordinary rate."),
    "ot_days_divisor": _("Hourly rate = (Basic + DA + Other) ÷ this number ÷ hours in a day."),
    "ot_hours_per_day": _("Normal working hours in one day."),
    "notice_period_days": _("Printed on the appointment letter."),
    "probation_months": _("0 = no probation line in the appointment letter."),
    "higher_wages_avenue": _("Printed in the appointment letter's particulars (how a person can earn more)."),
    "payroll_start_month": _("The first month you pay salary with this app. It can be changed only before the first upload."),
}


class WeeklyOffMixin:
    """Weekly off days as tick boxes (stored as digits, Monday = 0). At least one working day."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weekly_off_days"] = forms.MultipleChoiceField(
            label=_("Weekly off days"), choices=WEEKDAYS, required=False,
            widget=forms.CheckboxSelectMultiple(attrs={"class": "week-days"}),
            help_text=_("Tick the days the business is closed. Six-day week (Monday to Saturday): tick only Sunday. "
                        "Working days of each month are worked out from this."))
        if not self.is_bound:
            self.initial["weekly_off_days"] = list(self.instance.weekly_off_days if self.instance.pk
                                                   else self.initial.get("weekly_off_days", "6"))

    def clean_weekly_off_days(self):
        days = sorted(set(self.cleaned_data.get("weekly_off_days") or []))
        if len(days) >= 7:
            raise forms.ValidationError(_("At least one day of the week must be a working day."))
        return "".join(days)


class CompanyCreateForm(ModelErrorsOnPage, WeeklyOffMixin, IndianDatesMixin, forms.ModelForm):
    """All companies → Add a company. Everything else can be changed later in Settings."""

    class Meta:
        model = Company
        fields = [
            "code", "trade_name", "legal_name", "address_line1", "address_line2", "city", "state", "pincode",
            "phone", "email", "pan", "tan", "gstin", "pf_establishment_code", "esic_employer_code", "lin",
            "pt_registration_no", "signatory_name", "signatory_designation",
            "payroll_start_month", "weekly_off_days", "salary_divisor", "pf_enabled", "esi_enabled", "pt_enabled",
            "tds_enabled",
        ]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {}).update({"weekly_off_days": "6", "salary_divisor": Company.Divisor.WORKING})
        super().__init__(*args, **kwargs)
        for name in ("salary_divisor", "payroll_start_month", "pf_enabled", "esi_enabled", "pt_enabled",
                     "tds_enabled"):
            self.fields[name].help_text = PAYROLL_HELP[name]
        self.fields["pt_enabled"].help_text = _("Professional tax is a state tax, worked out from the slabs of the "
                                                "company's state.")
        self.fields["code"].help_text = _("3 to 6 capital letters, like SRKS. Used in file names and letter numbers. "
                                          "It cannot be changed later.")

    def clean_code(self):
        return upper_no_spaces(self.cleaned_data["code"])

    def clean_pan(self):
        return "".join(self.cleaned_data["pan"].split()).upper()

    def clean_tan(self):
        return "".join(self.cleaned_data["tan"].split()).upper()

    def clean_gstin(self):
        return "".join(self.cleaned_data["gstin"].split()).upper()

    def clean_payroll_start_month(self):
        value = self.cleaned_data["payroll_start_month"]
        return value.replace(day=1) if value else value


class CompanyDetailsForm(ModelErrorsOnPage, forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "legal_name", "trade_name", "address_line1", "address_line2", "city", "state", "pincode", "phone",
            "email", "logo", "pan", "tan", "gstin", "pf_establishment_code", "esic_employer_code", "lin",
            "pt_registration_no", "signatory_name", "signatory_designation",
        ]

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo and hasattr(logo, "content_type"):  # a new upload
            validate_logo(logo)
            head = logo.read(4)
            logo.seek(0)
            logo.name = "logo.png" if head.startswith(b"\x89PNG") else "logo.jpg"
        return logo

    def clean_pan(self):
        return "".join(self.cleaned_data["pan"].split()).upper()

    def clean_tan(self):
        return "".join(self.cleaned_data["tan"].split()).upper()

    def clean_gstin(self):
        return "".join(self.cleaned_data["gstin"].split()).upper()


class PayrollRulesForm(ModelErrorsOnPage, WeeklyOffMixin, IndianDatesMixin, forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "payroll_start_month", "weekly_off_days", "salary_divisor", "amount_paid_mode", "take_home_esi",
            "pf_enabled",
            "pf_employee_rate", "pf_restrict_to_ceiling", "esi_enabled", "pt_enabled", "pt_state", "tds_enabled",
            "default_tax_regime", "ot_multiplier", "ot_days_divisor", "ot_hours_per_day", "notice_period_days",
            "probation_months", "higher_wages_avenue",
        ]
        widgets = {"higher_wages_avenue": forms.Textarea(attrs={"rows": 2}), "amount_paid_mode": forms.RadioSelect}

    def __init__(self, *args, start_locked=False, pt_warning="", **kwargs):
        super().__init__(*args, **kwargs)
        for name, text in PAYROLL_HELP.items():
            self.fields[name].help_text = text
        if pt_warning:
            self.fields["pt_enabled"].help_text = pt_warning
        if start_locked:
            self.fields["payroll_start_month"].disabled = True
        self.fields["pt_state"].required = True

    def clean_payroll_start_month(self):
        value = self.cleaned_data["payroll_start_month"]
        return value.replace(day=1) if value else value


class PayslipDesignForm(ModelErrorsOnPage, forms.ModelForm):
    class Meta:
        model = Company
        fields = ["payslip_style", "payslip_show_employer", "payslip_two_per_page", "payslip_note"]
        widgets = {"payslip_style": forms.RadioSelect}
        help_texts = {
            "payslip_show_employer": _("Prints the employer's PF, EDLI, PF admin and ESI under the net pay."),
            "payslip_two_per_page": _("Saves paper: two payslips on one A4 sheet, with a cut line."),
        }


class OpeningBalanceForm(forms.Form):
    ytd_gross = whole_rupees(_("Gross paid this year before the start month ₹"),
                             help_text=_("From April until the month before you started using the app."))
    ytd_tds = whole_rupees(_("TDS already deducted ₹"))
    ytd_pt = whole_rupees(_("Professional tax already deducted ₹"))
    prev_employer_income = whole_rupees(_("Income from previous employer this year ₹"))
    prev_employer_tds = whole_rupees(_("TDS by previous employer ₹"))


class MinimumWageForm(IndianDatesMixin, forms.Form):
    """A plain form: saving the same state and month again replaces those rates (done in the view)."""

    state = forms.ChoiceField(label=_("State"), choices=[("", _("— choose —"))] + list(STATE_CHOICES))
    effective_from = forms.DateField(label=_("From month"),
                                     help_text=_("Any day of the first month these rates apply, like 01-04-2026."))
    unskilled = whole_rupees(_("Unskilled ₹ per month"))
    semi_skilled = whole_rupees(_("Semi-skilled ₹ per month"))
    skilled = whole_rupees(_("Skilled ₹ per month"))
    highly_skilled = whole_rupees(_("Highly skilled ₹ per month"))

    def clean_effective_from(self):
        value = self.cleaned_data["effective_from"]
        return value.replace(day=1) if value else value

    def clean(self):
        data = super().clean()
        if not any(data.get(name) for name in MinimumWage.SKILLS):
            raise forms.ValidationError(_("Write at least one minimum wage."))
        return data
