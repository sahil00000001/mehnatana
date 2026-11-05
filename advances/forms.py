from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import IndianDatesMixin, whole_rupees
from employees.models import Employee

from .models import Advance


class AdvanceForm(IndianDatesMixin, forms.ModelForm):
    amount = whole_rupees(_("Amount given ₹"), required=True)
    monthly_installment = whole_rupees(_("Cut every month ₹"), required=True,
                                       help_text=_("This amount is filled in the monthly sheet until the advance is repaid."))

    class Meta:
        model = Advance
        fields = ["employee", "date_given", "amount", "monthly_installment", "note"]
        help_texts = {"note": _("Optional, for example 'Festival advance'.")}

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = company
        working = Q(status=Employee.Status.ACTIVE) | Q(date_of_leaving__gte=timezone.localdate())
        self.fields["employee"].queryset = Employee.objects.filter(working, company=company).order_by("emp_code")
        self.fields["employee"].label_from_instance = lambda e: f"{e.full_name} ({e.emp_code})"

    def clean_date_given(self):
        given = self.cleaned_data["date_given"]
        if given and given > timezone.localdate():
            raise forms.ValidationError(_("This date is in the future. Record the advance on the day it is given."))
        return given

    def clean(self):
        data = super().clean()
        amount, instalment = data.get("amount"), data.get("monthly_installment")
        if amount is not None and amount <= 0:
            self.add_error("amount", _("The amount must be more than zero."))
        if amount and instalment and instalment > amount:
            self.add_error("monthly_installment", _("The monthly cut cannot be more than the advance."))
        return data


class InstalmentForm(forms.Form):
    monthly_installment = whole_rupees(_("New monthly cut ₹"), required=True)
