from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.audit import audit

from .forms import LoginForm


class LoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True
    extra_context = {"title": gettext_lazy("Login"),
                     "instruction": gettext_lazy("Type your username and password, then press Login.")}


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "registration/password_change.html"
    success_url = reverse_lazy("home")
    extra_context = {"title": gettext_lazy("Change password"),
                     "instruction": gettext_lazy("Type your old password once and the new password twice, then press Save.")}

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        audit(self.request, "password.change", user)
        messages.success(self.request, _("Your password has been changed."))
        return response


def forgot_password(request):
    return render(request, "registration/password_forgot.html", {
        "title": _("Forgot password"),
        "instruction": _("We will help you get back in."),
        "email_enabled": settings.EMAIL_ENABLED,
    })
