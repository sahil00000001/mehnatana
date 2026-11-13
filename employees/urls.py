from django.urls import path

from . import views

app_name = "employees"

urlpatterns = [
    path("employees/", views.employee_list, name="list"),
    path("employees/add/", views.employee_add, name="add"),
    path("employees/upload/", views.upload, name="upload"),
    path("employees/upload/confirm/", views.upload_confirm, name="upload_confirm"),
    path("employees/upload/sample.xlsx", views.upload_sample, name="upload_sample"),
    path("employees/upload/errors.xlsx", views.upload_errors, name="upload_errors"),
    path("employees/export.xlsx", views.export, name="export"),
    path("employees/<int:pk>/", views.employee_detail, name="detail"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="edit"),
    path("employees/<int:pk>/relieve/", views.relieve, name="relieve"),
    path("employees/<int:pk>/active/", views.set_active, name="set_active"),
    path("employees/<int:pk>/undo-relieve/", views.undo_relieve, name="undo_relieve"),
    path("employees/<int:pk>/salary/revise/", views.revise_salary, name="revise"),
    path("organisation/", views.organisation, name="organisation"),
    path("departments/", views.departments, name="departments"),
    path("departments/<int:pk>/", views.department_edit, name="department_edit"),
    path("designations/", views.designations, name="designations"),
    path("designations/<int:pk>/", views.designation_edit, name="designation_edit"),
]
