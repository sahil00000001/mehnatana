from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from .tenancy import SESSION_COMPANY

ALWAYS_ALLOWED = ("/static/", "/logout/", "/password/change/")


class TenantMiddleware:
    """
    Sets request.company: the company chosen on All companies (kept in the session).
    A company that was switched off is forgotten. A new password is forced when the
    login was given a temporary one.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from companies.models import Company

        request.company = None
        user = request.user
        if user.is_authenticated:
            if user.must_change_password and not request.path.startswith(ALWAYS_ALLOWED):
                messages.warning(request, _("Please choose a new password before you continue."))
                return redirect("password_change")
            company_id = request.session.get(SESSION_COMPANY)
            if company_id is not None:
                request.company = Company.objects.filter(pk=company_id, is_active=True).first()
                if request.company is None:
                    request.session.pop(SESSION_COMPANY, None)
        return self.get_response(request)
