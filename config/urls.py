from django.conf import settings
from django.contrib import admin
from django.urls import include, path, register_converter

from core.converters import MonthConverter, TaxYearConverter

# Converters must be registered before the app URL files are included.
register_converter(MonthConverter, "ym")
register_converter(TaxYearConverter, "ty")

admin.site.site_header = f"{settings.APP_NAME} — platform admin"
admin.site.site_title = f"{settings.APP_NAME} admin"
admin.site.index_title = "Companies, logins and statutory tables"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("core.urls")),
    path("", include("employees.urls")),
    path("salary/", include("payroll.urls")),
    path("advances/", include("advances.urls")),
    path("letters/", include("letters.urls")),
    path("settings/", include("companies.urls")),
    path("", include("reports.urls")),
]

handler404 = "core.views.not_found"
handler403 = "core.views.forbidden"
