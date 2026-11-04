from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class OwnerLoginForm(AuthenticationForm):
    username = forms.CharField(
        label=_("Username (mobile number or email)"), max_length=150,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "autocapitalize": "none"}),
    )
    password = forms.CharField(label=_("Password"), strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    error_messages = {
        "invalid_login": _("The username or password is wrong. Please check and try again."),
        "inactive": _("This login is switched off. Please call your administrator."),
    }

    def clean(self):
        try:
            return super().clean()
        except ValidationError:
            until = getattr(self.request, "login_locked_until", None)
            if until:
                local = timezone.localtime(until)
                raise ValidationError(
                    _("This login is locked for 15 minutes because of 5 wrong passwords. "
                      "Please try again after %(time)s.") % {"time": local.strftime("%I:%M %p")},
                    code="locked",
                )
            raise

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser and (user.company is None or not user.company.is_active):
            raise ValidationError(_("This business account is switched off. Please call your administrator."),
                                  code="company_inactive")
