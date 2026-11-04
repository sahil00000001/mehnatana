"""Audit log helpers. Every change listed in README §4.12 calls audit()."""
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Model
from django.db.models.fields.files import FieldFile

from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _plain(value):
    if isinstance(value, Model):
        return str(value.pk)
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, FieldFile):
        return value.name or ""
    return value


def snapshot(obj, fields=None):
    """Plain dict of field values, used for before/after in the audit log."""
    out = {}
    for f in obj._meta.concrete_fields:
        if fields and f.name not in fields:
            continue
        if f.name in ("created_at", "updated_at", "password"):
            continue
        out[f.name] = _plain(getattr(obj, f.attname))
    return out


def diff(before, after):
    return {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}


def audit(request, action, obj=None, details=None, company=None):
    user = getattr(request, "user", None)
    if user is not None and not user.is_authenticated:
        user = None
    if company is None:
        company = getattr(request, "company", None)
        if company is None and obj is not None:
            company = getattr(obj, "company", None)
    return AuditLog.objects.create(
        company=company,
        user=user,
        action=action,
        object_type=obj._meta.label if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        details=details or {},
        ip=client_ip(request),
    )
