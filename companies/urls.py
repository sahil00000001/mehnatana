from django.urls import path

from . import views

app_name = "companies"

urlpatterns = [
    path("", views.settings_index, name="index"),
    path("company/", views.company_details, name="company"),
    path("payroll/", views.payroll_rules, name="payroll"),
    path("sheet-checks/", views.sheet_checks, name="checks"),
    path("payslip/", views.payslip_design, name="payslip_design"),
    path("opening-balances/", views.opening_balances, name="opening_balances"),
    path("opening-balances/<int:emp_id>/", views.opening_balance_edit, name="opening_balance_edit"),
    path("opening-balances/upload/", views.opening_upload, name="opening_upload"),
    path("opening-balances/sample.xlsx", views.opening_sample, name="opening_sample"),
    path("opening-balances/errors.xlsx", views.opening_errors, name="opening_errors"),
]
