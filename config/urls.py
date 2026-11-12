from urllib.parse import urlencode

from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import include, path, register_converter, reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.converters import MonthConverter, TaxYearConverter

# Converters must be registered before the app URL files are included.
register_converter(MonthConverter, "ym")
register_converter(TaxYearConverter, "ty")

# The admin screens keep only the government rate tables (and the read-only audit log).
# Companies and everything else are managed on the main site, with the same one login.
admin.site.site_header = f"{settings.APP_NAME} — rates and tax tables"
admin.site.site_title = f"{settings.APP_NAME} rates and tax tables"
admin.site.index_title = "Rates and tax tables"
admin.site.site_url = "/companies/"

from django.contrib.auth.models import Group  # noqa: E402

if admin.site.is_registered(Group):
    admin.site.unregister(Group)


def admin_login(request):
    """There is no separate admin login: the admin screens use the one login page."""
    target = request.GET.get("next") or reverse("admin:index")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse("admin:index")
    if request.user.is_authenticated and request.user.is_staff:
        return redirect(target)
    if request.user.is_authenticated:  # logged in, but this login may not change the rate tables
        messages.error(request, "This login cannot change the rates and tax tables.")
        return redirect("portal:list")
    return redirect(reverse("login") + "?" + urlencode({"next": target}))


urlpatterns = [
    path("admin/login/", admin_login),
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("core.urls")),
    path("companies/", include("companies.portal_urls")),
    path("", include("employees.urls")),
    path("salary/", include("payroll.urls")),
    path("advances/", include("advances.urls")),
    path("letters/", include("letters.urls")),
    path("settings/", include("companies.urls")),
    path("", include("reports.urls")),
]

handler404 = "core.views.not_found"
handler403 = "core.views.forbidden"
