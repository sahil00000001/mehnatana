from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

MENU = [
    ("home", _("Home"), "home", ()),
    ("employees", _("Employees"), "employees:list", ("/employees/", "/organisation/")),
    ("salary", _("Monthly Salary"), "payroll:months", ("/salary/",)),
    ("advances", _("Advances"), "advances:list", ("/advances/",)),
    ("letters", _("Letters"), "letters:index", ("/letters/",)),
    ("reports", _("Reports"), "reports:index", ("/reports/",)),
    ("settings", _("Settings"), "companies:index", ("/settings/", "/departments/", "/designations/", "/password/change/")),
    ("help", _("Help"), "help", ("/help/",)),
]


def _current(path):
    if path == "/":
        return "home"
    for key, _label, _url, prefixes in MENU:
        if prefixes and path.startswith(prefixes):
            return key
    return ""


def app(request):
    context = {
        "APP_NAME": settings.APP_NAME,
        "APP_TAGLINE": settings.APP_TAGLINE,
        "SUPPORT_PHONE": settings.SUPPORT_PHONE,
        "SUPPORT_EMAIL": settings.SUPPORT_EMAIL,
    }
    if getattr(request, "company", None) is not None:
        context["MENU"] = [(key, label, reverse(url)) for key, label, url, _p in MENU]
        context["menu_current"] = _current(request.path)
    return context
