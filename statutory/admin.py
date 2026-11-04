from django.contrib import admin

from core.admin import AuditedAdmin

from .models import DueDate, PTSlab, StatutoryRate, TaxConfig


@admin.register(StatutoryRate)
class StatutoryRateAdmin(AuditedAdmin):
    list_display = ["key", "value", "effective_from", "note"]
    list_filter = ["key"]
    search_fields = ["key", "note"]


@admin.register(PTSlab)
class PTSlabAdmin(AuditedAdmin):
    list_display = ["state", "gender", "from_amount", "to_amount", "amount", "february_amount", "effective_from"]
    list_filter = ["state", "gender"]


@admin.register(TaxConfig)
class TaxConfigAdmin(AuditedAdmin):
    list_display = ["tax_year", "regime", "std_deduction", "rebate_limit", "rebate_max", "cess_pct"]
    list_filter = ["regime"]


@admin.register(DueDate)
class DueDateAdmin(AuditedAdmin):
    list_display = ["item", "label", "day_of_next_month", "march_due_day", "march_due_month", "applies_to_state", "note"]
