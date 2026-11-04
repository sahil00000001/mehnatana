from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms import IndianDatesMixin, whole_rupees

from .models import Company, validate_logo

PAYROLL_HELP = {
    "salary_divisor": _("Salary for absent days is cut as monthly salary ÷ this number of days. Most shops use days in the month."),
    "pf_enabled": _("PF is compulsory once you have 20 or more workers (you may join earlier)."),
    "pf_employee_rate": _("Use 10% only if EPFO has notified your establishment for 10%."),
    "pf_restrict_to_ceiling": _("Usual practice: PF only on wages up to the ceiling (₹25,000 from October 2026)."),
    "esi_enabled": _("ESI applies to places with 10 or more persons in areas notified by ESIC."),
    "pt_enabled": _("Professional tax is a state tax. It is worked out from the slabs for the state below."),
    "pt_state": _("The state whose professional tax slabs are used. Usually your own state."),
    "tds_enabled": _("Income tax is cut each month from staff whose yearly income is taxable."),
    "default_tax_regime": _("Filled in for new staff. Each person can still choose."),
    "ot_multiplier": _("The Labour Codes say overtime is paid at twice the ordinary rate."),
    "ot_days_divisor": _("Hourly rate = (Basic + DA + Other) ÷ this number ÷ hours in a day."),
    "ot_hours_per_day": _("Normal working hours in one day."),
    "notice_period_days": _("Printed on the appointment letter."),
    "probation_months": _("0 = no probation line in the appointment letter."),
    "higher_wages_avenue": _("Printed in the appointment letter's particulars (how a person can earn more)."),
    "payslip_two_per_page": _("Saves paper: two payslips on one A4 sheet, with a cut line."),
    "payroll_start_month": _("The first month you pay salary with this app. It can be changed only before the first upload."),
}


class CompanyDetailsForm(forms.ModelForm):
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
        return logo

    def clean_pan(self):
        return "".join(self.cleaned_data["pan"].split()).upper()

    def clean_tan(self):
        return "".join(self.cleaned_data["tan"].split()).upper()

    def clean_gstin(self):
        return "".join(self.cleaned_data["gstin"].split()).upper()


class PayrollRulesForm(IndianDatesMixin, forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "payroll_start_month", "salary_divisor", "pf_enabled", "pf_employee_rate", "pf_restrict_to_ceiling",
            "esi_enabled", "pt_enabled", "pt_state", "tds_enabled", "default_tax_regime", "ot_multiplier",
            "ot_days_divisor", "ot_hours_per_day", "notice_period_days", "probation_months", "higher_wages_avenue",
            "payslip_two_per_page",
        ]
        widgets = {"higher_wages_avenue": forms.Textarea(attrs={"rows": 2})}

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


class OpeningBalanceForm(forms.Form):
    ytd_gross = whole_rupees(_("Gross paid this year before the start month ₹"),
                             help_text=_("From April until the month before you started using the app."))
    ytd_tds = whole_rupees(_("TDS already deducted ₹"))
    ytd_pt = whole_rupees(_("Professional tax already deducted ₹"))
    prev_employer_income = whole_rupees(_("Income from previous employer this year ₹"))
    prev_employer_tds = whole_rupees(_("TDS by previous employer ₹"))
