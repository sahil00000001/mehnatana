from django.urls import path

from . import views

app_name = "payroll"

urlpatterns = [
    path("", views.months, name="months"),
    path("<ym:month>/", views.month_page, name="month"),
    path("<ym:month>/sheet.xlsx", views.sheet, name="sheet"),
    path("<ym:month>/upload/", views.upload, name="upload"),
    path("<ym:month>/errors.xlsx", views.errors_file, name="errors"),
    path("<ym:month>/line/<int:line_id>/edit/", views.line_edit, name="line_edit"),
    path("<ym:month>/finalise/", views.finalise, name="finalise"),
    path("<ym:month>/reopen/", views.reopen, name="reopen"),
    path("<ym:month>/export.xlsx", views.export, name="export"),
]
