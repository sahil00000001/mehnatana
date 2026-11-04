from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """One login per business in v1. `role` is here so roles can be added later."""

    class Role(models.TextChoices):
        OWNER = "OWNER", _("Owner")

    company = models.ForeignKey(
        "companies.Company", null=True, blank=True, on_delete=models.PROTECT, related_name="users",
        help_text=_("Empty only for platform admins."),
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OWNER)
    must_change_password = models.BooleanField(default=False)
    failed_logins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mtn_user"
        verbose_name = _("login")
        verbose_name_plural = _("logins")

    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())
