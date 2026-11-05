from django.contrib import admin

from .audit import audit, diff, snapshot
from .models import AuditLog


class AuditedAdminMixin:
    """Admin edits (including bulk delete) are written to the audit log."""

    def save_model(self, request, obj, form, change):
        before = snapshot(type(obj).objects.get(pk=obj.pk)) if change and obj.pk else {}
        super().save_model(request, obj, form, change)
        audit(request, f"admin.{'change' if change else 'add'}", obj,
              {"changes": diff(before, snapshot(obj))}, company=getattr(obj, "company", None))

    def delete_model(self, request, obj):
        audit(request, "admin.delete", obj, {"before": snapshot(obj)}, company=getattr(obj, "company", None))
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            audit(request, "admin.delete", obj, {"before": snapshot(obj)}, company=getattr(obj, "company", None))
        super().delete_queryset(request, queryset)


class AuditedAdmin(AuditedAdminMixin, admin.ModelAdmin):
    pass


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "company", "user", "action", "object_type", "object_id", "ip"]
    list_filter = ["action", "company"]
    search_fields = ["action", "object_type", "object_id", "user__username"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
