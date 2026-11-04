from django import forms

# Never accept the US month/day order: 05/11/2026 is 5 November.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"]


class DateInput(forms.DateInput):
    """Browser date picker; still accepts typed DD-MM-YYYY where the picker is missing."""

    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


class IndianDatesMixin:
    """Every DateField of the form uses the date picker and DD-MM-YYYY / ISO input."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.widget = DateInput(attrs=field.widget.attrs)
                field.input_formats = DATE_INPUT_FORMATS


def whole_rupees(label, required=False, help_text=""):
    return forms.DecimalField(label=label, required=required, help_text=help_text, min_value=0, max_digits=12,
                              decimal_places=0, widget=forms.NumberInput(attrs={"step": "1", "inputmode": "numeric"}))
