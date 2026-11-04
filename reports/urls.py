from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    # month documents and files (under /salary/<yyyy-mm>/)
    path("salary/<ym:month>/payslips/", views.payslips, name="payslips"),
    path("salary/<ym:month>/payslip/<int:line_id>/", views.payslip, name="payslip"),
    path("salary/<ym:month>/register/", views.register, name="register"),
    path("salary/<ym:month>/register.xlsx", views.register_xlsx, name="register_xlsx"),
    path("salary/<ym:month>/bank-sheet.xlsx", views.bank_sheet, name="bank_sheet"),
    path("salary/<ym:month>/cash-list/", views.cash_list, name="cash_list"),
    path("salary/<ym:month>/ecr.txt", views.ecr, name="ecr"),
    path("salary/<ym:month>/esi.xlsx", views.esi_file, name="esi_file"),
    # reports
    path("reports/", views.index, name="index"),
    path("reports/cost/", views.cost, name="cost"),
    path("reports/pf/<ym:month>/", views.pf, name="pf"),
    path("reports/esi/<ym:month>/", views.esi, name="esi"),
    path("reports/pt/<ym:month>/", views.pt, name="pt"),
    path("reports/tds/<ty:tax_year>/", views.tds, name="tds"),
    path("reports/advances/", views.advances, name="advances"),
    path("reports/joiners-leavers/", views.joiners_leavers, name="joiners_leavers"),
    path("reports/increments/", views.increments, name="increments"),
]
