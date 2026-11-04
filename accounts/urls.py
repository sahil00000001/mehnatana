from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password_change"),
    path("password/forgot/", views.forgot_password, name="password_forgot"),
    # Email reset links (offered only when email is configured)
    path("password/reset/", auth_views.PasswordResetView.as_view(
        template_name="registration/reset_form.html", success_url=reverse_lazy("password_reset_done")),
        name="password_reset"),
    path("password/reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="registration/reset_done.html"), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="registration/reset_confirm.html", success_url=reverse_lazy("password_reset_complete")),
        name="password_reset_confirm"),
    path("password/reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="registration/reset_complete.html"), name="password_reset_complete"),
]
