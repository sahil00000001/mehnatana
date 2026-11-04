import secrets

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

from core.audit import audit

from .models import User

ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def temporary_password():
    return "".join(secrets.choice(ALPHABET) for _ in range(10))


@admin.register(User)
class LoginAdmin(UserAdmin):
    list_display = ["username", "company", "first_name", "is_active", "must_change_password", "locked_until",
                    "last_login"]
    list_filter = ["company", "is_active", "is_superuser"]
    fieldsets = UserAdmin.fieldsets + (
        ("Business", {"fields": ("company", "role", "must_change_password", "failed_logins", "locked_until")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (("Business", {"fields": ("company",)}),)
    actions = ["unlock", "reset_password"]

    @admin.action(description="Unlock selected logins")
    def unlock(self, request, queryset):
        count = queryset.update(failed_logins=0, locked_until=None)
        for user in queryset:
            audit(request, "admin.unlock", user, company=user.company)
        self.message_user(request, f"{count} login(s) unlocked.")

    @admin.action(description="Reset password to a temporary one (must change at next login)")
    def reset_password(self, request, queryset):
        for user in queryset:
            password = temporary_password()
            user.set_password(password)
            user.must_change_password = True
            user.failed_logins, user.locked_until = 0, None
            user.save()
            audit(request, "admin.reset_password", user, company=user.company)
            self.message_user(request, f"{user.username}: temporary password {password} — give it to the owner.",
                              messages.WARNING)
