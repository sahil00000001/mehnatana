from django.contrib import admin, messages

from core.admin import AuditedAdmin

from .models import DueDate, PTSlab, StatutoryRate, TaxConfig


class RecalculatesDraftsAdmin(AuditedAdmin):
    """A change to a rate, slab or tax table is applied to every company's draft months at once
    (finalised months never change)."""

    def _recalculate(self, request):
        from companies.models import Company
        from payroll.service import recalculate_drafts

        problems = []
        for company in Company.objects.filter(is_active=True):
            problems += [f"{company.code} {label}: {text}" for label, text in recalculate_drafts(company)]
        for problem in problems:
            self.message_user(request, problem, messages.WARNING)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._recalculate(request)

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        self._recalculate(request)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        self._recalculate(request)


@admin.register(StatutoryRate)
class StatutoryRateAdmin(RecalculatesDraftsAdmin):
    list_display = ["key", "value", "effective_from", "note"]
    list_filter = ["key"]
    search_fields = ["key", "note"]


@admin.register(PTSlab)
class PTSlabAdmin(RecalculatesDraftsAdmin):
    list_display = ["state", "gender", "from_amount", "to_amount", "amount", "february_amount", "effective_from"]
    list_filter = ["state", "gender"]


@admin.register(TaxConfig)
class TaxConfigAdmin(RecalculatesDraftsAdmin):
    list_display = ["tax_year", "regime", "std_deduction", "rebate_limit", "rebate_max", "cess_pct"]
    list_filter = ["regime"]


@admin.register(DueDate)
class DueDateAdmin(AuditedAdmin):
    list_display = ["item", "label", "day_of_next_month", "march_due_day", "march_due_month", "applies_to_state", "note"]
