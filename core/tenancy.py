"""
Multi-company isolation (README §2.1).

One login manages every company. The company being worked on is chosen on
All companies and kept in the session (SESSION_COMPANY); the middleware puts it
in request.company. Function views use @company_required and fetch single
objects with company_get(), which always adds company=request.company, so a
link to another company's row gives 404. Class-based views use CompanyScopedMixin.
"""
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

SESSION_COMPANY = "company_id"


class NoSalaryMonth(Http404):
    """A month with no salary worked out yet: the 404 page says so and links to the month."""

    def __init__(self, month):
        super().__init__(f"No salary for {month:%Y-%m} yet")
        self.month = month


def choose_company_first(request):
    """No company chosen yet: go to All companies, and come back here after Open."""
    url = reverse("portal:list")
    if request.method == "GET" and request.path != reverse("home"):
        messages.info(request, _("Choose the company to work on first."))
        url += "?" + urlencode({"next": request.get_full_path()})
    return redirect(url)


def company_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if getattr(request, "company", None) is None:
            return choose_company_first(request)
        return view(request, *args, **kwargs)

    return wrapper


def company_get(request, model_or_queryset, **lookup):
    return get_object_or_404(model_or_queryset, company=request.company, **lookup)


class CompanyScopedMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(request, "company", None) is None:
            return choose_company_first(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(company=self.request.company)


def page(request, template, title, instruction="", http_status=200, **context):
    """Render a normal screen: every page has a title and a one-line instruction."""
    context.setdefault("title", title)
    context.setdefault("instruction", instruction)
    return render(request, template, context, status=http_status)
