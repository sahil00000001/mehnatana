from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils.translation import gettext as _

ALWAYS_ALLOWED = ("/static/", "/logout/")


class TenantMiddleware:
    """
    Sets request.company for a logged-in owner.
    Platform admins (superusers) are sent to /admin/; an owner whose company
    is switched off is logged out; a new password is forced on first login.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.company = None
        user = request.user
        if user.is_authenticated:
            path = request.path
            if user.is_superuser:
                if not path.startswith(("/admin/",) + ALWAYS_ALLOWED):
                    return redirect("/admin/")
            else:
                company = user.company
                if company is None or not company.is_active:
                    logout(request)
                    messages.error(request, _("This business account is switched off. Please call your administrator."))
                    return redirect("login")
                request.company = company
                if user.must_change_password and not path.startswith(("/password/change/",) + ALWAYS_ALLOWED):
                    messages.warning(request, _("Please choose a new password before you continue."))
                    return redirect("password_change")
        return self.get_response(request)
