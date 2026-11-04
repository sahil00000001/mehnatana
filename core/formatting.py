"""Indian number format, amount in words, dates, months and masking."""
import calendar
from datetime import date
from decimal import Decimal

from django.utils import dateformat

# The two money helpers live in the pure payroll engine (README Appendix A).
from payroll.engine import amount_in_words, indian_format  # noqa: F401


def fmt_date(value):
    return value.strftime("%d-%m-%Y") if value else ""


def month_label(value):
    """date(2026, 11, 1) -> 'November 2026'"""
    return dateformat.format(value, "F Y") if value else ""


def month_short(value):
    """date(2026, 11, 1) -> 'Nov-2026'"""
    return dateformat.format(value, "M-Y") if value else ""


def month_start(value):
    return date(value.year, value.month, 1)


def month_end(value):
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def add_months(value, count):
    index = value.year * 12 + (value.month - 1) + count
    return date(index // 12, index % 12 + 1, 1)


def tax_year_of(value):
    """Tax year that contains the date: April 2026 - March 2027 -> 2026."""
    return value.year if value.month >= 4 else value.year - 1


def tax_year_label(year):
    return f"{year}-{(year + 1) % 100:02d}"


def plain_number(value):
    """Days and similar small numbers: 26 -> '26', 25.5 -> '25.5'."""
    if value is None or value == "":
        return ""
    value = Decimal(value)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def mask(value, keep=4):
    if not value:
        return ""
    value = str(value)
    return "X" * max(0, len(value) - keep) + value[-keep:]


def mask_aadhaar(value):
    if not value:
        return ""
    return f"XXXX XXXX {str(value)[-4:]}"
