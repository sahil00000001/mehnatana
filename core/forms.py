from datetime import date
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.translation import gettext_lazy as _

# The biggest amount any one box may hold (₹99,99,999). Anything larger is a typing mistake,
# and the database columns could not store the totals.
MAX_AMOUNT = Decimal("9999999")

# Never accept the US month/day order: 05/11/2026 is 5 November.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"]


class DateInput(forms.DateInput):
    """Browser date picker; still accepts typed DD-MM-YYYY where the picker is missing."""

    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


def sensible_date(value):
    """Catches typing mistakes like 0201-05-12 or 2206-05-12."""
    if value and not (date(1900, 1, 1) <= value <= date(2100, 12, 31)):
        raise ValidationError(_("Please check the year: %(year)s does not look right.") % {"year": value.year})


class IndianDatesMixin:
    """Every DateField of the form uses the date picker and DD-MM-YYYY / ISO input."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.widget = DateInput(attrs=field.widget.attrs)
                field.input_formats = DATE_INPUT_FORMATS
                field.validators.append(sensible_date)


class WholeRupeesField(forms.DecimalField):
    """An amount in whole rupees. Accepts 12000, 12000.00, 12,000 or ₹12000 (also the value the
    form itself filled in), refuses paise, negative numbers and amounts above ₹99,99,999."""

    default_error_messages = {
        "invalid": _("Write an amount in rupees, like 12000."),
        "paise": _("Use whole rupees, without paise."),
        "max_digits": _("This looks too big. Please check."),
        "max_whole_digits": _("This looks too big. Please check."),
        "max_decimal_places": _("Use whole rupees, without paise."),
    }

    def __init__(self, maximum=MAX_AMOUNT, **kwargs):
        kwargs.setdefault("widget", forms.NumberInput(attrs={"step": "1", "inputmode": "numeric", "min": "0"}))
        super().__init__(max_digits=14, decimal_places=2, **kwargs)
        self.validators += [
            MinValueValidator(0, message=_("Cannot be a negative number.")),
            MaxValueValidator(maximum, message=_("This looks too big (more than ₹%(max)s). Please check.")
                              % {"max": f"{int(maximum):,}"}),
        ]

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(",", "").replace("₹", "").strip()
        return super().to_python(value)

    def validate(self, value):
        super().validate(value)
        if value is not None and value.is_finite() and value != value.to_integral_value():
            raise ValidationError(self.error_messages["paise"], code="paise")

    def prepare_value(self, value):
        if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
            return int(value)  # show 12000, not 12000.00
        return value


def whole_rupees(label, required=False, help_text=""):
    return WholeRupeesField(label=label, required=required, help_text=help_text)
