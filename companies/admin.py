from django import forms
from django.contrib import admin, messages

from accounts.admin import temporary_password
from accounts.models import User
from core.admin import AuditedAdmin
from core.audit import audit
from core.forms import DATE_INPUT_FORMATS
from core.validators import upper_no_spaces

from .models import Company


class CompanyAdminForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.input_formats = DATE_INPUT_FORMATS  # 01-11-2026 is 1 November

    def clean_code(self):
        return upper_no_spaces(self.cleaned_data["code"])  # "srks" becomes "SRKS"


@admin.register(Company)
class CompanyAdmin(AuditedAdmin):
    form = CompanyAdminForm
    list_display = ["code", "trade_name", "city", "state", "payroll_start_month", "is_active", "owner_login"]
    list_filter = ["is_active", "state"]
    search_fields = ["code", "trade_name", "legal_name", "phone"]
    actions = ["create_owner_login"]
    fieldsets = (
        ("Identity", {"fields": ("code", "legal_name", "trade_name", "address_line1", "address_line2", "city", "state",
                                 "pincode", "phone", "email", "logo", "pan", "tan", "gstin")}),
        ("Registrations", {"fields": ("pf_establishment_code", "esic_employer_code", "lin", "pt_registration_no",
                                      "signatory_name", "signatory_designation")}),
        ("Payroll", {"fields": ("payroll_start_month", "is_active", "salary_divisor", "pf_enabled", "pf_employee_rate",
                                "pf_restrict_to_ceiling", "esi_enabled", "pt_enabled", "pt_state", "tds_enabled",
                                "default_tax_regime", "ot_multiplier", "ot_days_divisor", "ot_hours_per_day",
                                "notice_period_days", "probation_months", "higher_wages_avenue")}),
        ("Payslip", {"fields": ("payslip_style", "payslip_show_employer", "payslip_two_per_page", "payslip_note")}),
    )

    @admin.display(description="Owner login")
    def owner_login(self, obj):
        user = obj.users.order_by("pk").first()
        return user.username if user else "—"

    @admin.action(description="Create owner login (temporary password, must change at first login)")
    def create_owner_login(self, request, queryset):
        for company in queryset:
            if company.users.exists():
                self.message_user(request, f"{company.code} already has a login. Use 'Reset password' on Logins.",
                                  messages.INFO)
                continue
            candidates = ["".join(ch for ch in company.phone if ch.isdigit())[-10:], company.code.lower()]
            username = next((c for c in candidates if c and not User.objects.filter(username=c).exists()), None)
            number = 2
            while username is None:
                if not User.objects.filter(username=f"{company.code.lower()}{number}").exists():
                    username = f"{company.code.lower()}{number}"
                number += 1
            password = temporary_password()
            user = User.objects.create_user(username=username, password=password, company=company,
                                            must_change_password=True, email=company.email)
            audit(request, "admin.create_owner_login", user, company=company)
            self.message_user(request, f"{company.code}: username {username}, temporary password {password}. "
                                       "Give these to the owner; they must choose a new password at first login.",
                              messages.WARNING)
