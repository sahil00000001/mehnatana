"""Template filters: {% load mtn %} then {{ amount|inr }}, {{ d|dmy }} ..."""
from decimal import Decimal, InvalidOperation

from django import template

from core import formatting

register = template.Library()


@register.filter
def inr(value):
    """Indian grouping, whole rupees: 1234567 -> 12,34,567. Empty stays empty."""
    if value is None or value == "":
        return ""
    try:
        return formatting.indian_format(Decimal(value))
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter
def dash_inr(value):
    """Like inr, but shows a dash for zero (cleaner registers)."""
    if value is None or value == "" or Decimal(value) == 0:
        return "—"
    return inr(value)


@register.filter
def words(value):
    return formatting.amount_in_words(Decimal(value or 0))


@register.filter
def dmy(value):
    return formatting.fmt_date(value)


@register.filter
def month_label(value):
    return formatting.month_label(value)


@register.filter
def month_short(value):
    return formatting.month_short(value)


@register.filter
def num(value):
    return formatting.plain_number(value)


@register.filter
def masked(value):
    return formatting.mask(value)


@register.filter
def masked_aadhaar(value):
    return formatting.mask_aadhaar(value)


@register.filter
def tax_year_label(value):
    return formatting.tax_year_label(int(value))


@register.filter
def warning_text(code):
    from payroll.warnings import message_for

    return message_for(code)


@register.filter
def bar_percent(value, largest):
    """Width of a simple bar as a percentage of the largest value."""
    try:
        value, largest = Decimal(value or 0), Decimal(largest or 0)
    except (InvalidOperation, TypeError):
        return 0
    if largest <= 0:
        return 0
    return int((value * 100 / largest).to_integral_value())


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None
