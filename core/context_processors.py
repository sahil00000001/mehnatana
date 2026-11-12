import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

MENU = [
    ("companies", _("All companies"), "portal:list", ("/companies/",)),
    ("home", _("Home"), "home", ()),
    ("notices", _("Notices"), "notices", ("/notices/",)),
    ("employees", _("Employees"), "employees:list", ("/employees/", "/organisation/")),
    ("salary", _("Monthly Salary"), "payroll:months", ("/salary/",)),
    ("advances", _("Advances"), "advances:list", ("/advances/",)),
    ("letters", _("Letters"), "letters:index", ("/letters/",)),
    ("reports", _("Reports"), "reports:index", ("/reports/",)),
    ("settings", _("Settings"), "companies:index", ("/settings/", "/departments/", "/designations/", "/password/change/")),
    ("help", _("Help"), "help", ("/help/",)),
]


# Before a company is opened: only what works for every company.
PORTAL_MENU = [
    ("companies", _("All companies"), "portal:list", ("/companies/",)),
    ("add", _("Add a company"), "portal:add", ("/companies/add/",)),
    ("password", _("Change password"), "password_change", ("/password/change/",)),
    ("help", _("Help"), "help", ("/help/",)),
]


def _current(path, menu=MENU):
    if path == "/":
        return "home"
    if path.startswith("/companies/add/") and menu is PORTAL_MENU:
        return "add"
    for key, _label, _url, prefixes in menu:
        if prefixes and path.startswith(prefixes):
            return key
    return ""


def _attention(company):
    """Notices due soon or late. A problem here must never stop a page from opening."""
    from core.notices import needs_attention, notices_for

    try:
        return needs_attention(notices_for(company))
    except Exception:  # noqa: BLE001 - the notice strip is a convenience
        logger.exception("Could not work out notices for company %s", company.pk)
        return []


def app(request):
    context = {
        "APP_NAME": settings.APP_NAME,
        "APP_TAGLINE": settings.APP_TAGLINE,
        "SUPPORT_PHONE": settings.SUPPORT_PHONE,
        "SUPPORT_EMAIL": settings.SUPPORT_EMAIL,
    }
    company = getattr(request, "company", None)
    user = getattr(request, "user", None)
    if company is not None:
        current = _current(request.path)
        menu = [(key, label, reverse(url)) for key, label, url, _p in MENU]
        context.update({
            "MENU": menu,
            "menu_current": current,
            "menu_section": next(((label, url) for key, label, url in menu if key == current), None),
            "today": timezone.localdate(),
            "notice_strip": _attention(company),
        })
    elif user is not None and user.is_authenticated and not user.must_change_password:
        current = _current(request.path, PORTAL_MENU)
        menu = [(key, label, reverse(url)) for key, label, url, _p in PORTAL_MENU]
        context.update({"MENU": menu, "menu_current": current, "today": timezone.localdate()})
    return context
