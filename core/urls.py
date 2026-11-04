from django.urls import path

from reports import views as report_views

from . import views

urlpatterns = [
    path("", report_views.dashboard, name="home"),
    path("help/", views.help_page, name="help"),
    path("files/logo/", views.files_logo, name="files_logo"),
    path("files/upload/<int:run_id>/", views.files_upload, name="files_upload"),
]
