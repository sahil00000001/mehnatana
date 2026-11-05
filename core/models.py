from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.translation import gettext_lazy as _


def money(**kwargs):
    """Whole rupees stored as DECIMAL(12, 2)."""
    kwargs.setdefault("default", 0)
    return models.DecimalField(max_digits=12, decimal_places=2, **kwargs)


def days(**kwargs):
    """Days, half days allowed."""
    kwargs.setdefault("default", 0)
    return models.DecimalField(max_digits=4, decimal_places=1, **kwargs)


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CompanyOwned(TimeStamped):
    """Every row that belongs to one business. Always filter by company."""

    company = models.ForeignKey("companies.Company", on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        abstract = True


class StoredFile(models.Model):
    """A private uploaded file kept in the database (see core/storage.py)."""

    name = models.CharField(max_length=255, unique=True)
    content = models.BinaryField()
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mtn_stored_file"

    def __str__(self):
        return self.name


class AuditLog(models.Model):
    company = models.ForeignKey("companies.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(_("action"), max_length=60)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    details = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "mtn_audit_log"
        ordering = ["-created_at", "-id"]
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log")

    def __str__(self):
        return f"{self.created_at:%d-%m-%Y %H:%M} {self.action} {self.object_type} {self.object_id}"
