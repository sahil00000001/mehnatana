from django import forms
from django.utils.translation import gettext_lazy as _

from .models import LetterType
from .render import eligible_employees

DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"]


class ChooseForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=None, label=_("Employee"), empty_label=_("— choose —"))
    date = forms.DateField(label=_("Date of the letter"), input_formats=DATE_FORMATS,
                           widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def __init__(self, company, letter_type, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["employee"]
        field.queryset = eligible_employees(company, letter_type)
        field.label_from_instance = lambda e: f"{e.emp_code} — {e.full_name} ({e.designation})"
        if letter_type == LetterType.APPOINTMENT:
            field.help_text = _("Only staff who are working now are listed.")
            field.error_messages["invalid_choice"] = _(
                "Choose a person who is working now. This person has been relieved.")
        else:
            field.help_text = _("Only staff who have left (relieved) are listed.")
            field.error_messages["invalid_choice"] = _(
                "Choose a relieved employee. Relieve the person from the Employees page first.")


class TemplateForm(forms.Form):
    body = forms.CharField(
        label=_("Default text"),
        widget=forms.Textarea(attrs={"class": "letter-text", "rows": 30, "spellcheck": "true"}),
        help_text=_("Plain text. A blank line starts a new paragraph. Words inside {{ }} are filled in "
                    "for each letter — see the list below."),
    )
