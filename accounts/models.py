from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """The one login that manages every company. There are no separate business logins."""

    must_change_password = models.BooleanField(default=False)
    failed_logins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mtn_user"
        verbose_name = _("login")
        verbose_name_plural = _("logins")

    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())
