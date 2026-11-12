from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms import WholeRupeesField


class UploadForm(forms.Form):
    file = forms.FileField(label=_("Filled attendance sheet (.xlsx)"),
                           widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}))


def _days(label, help_text="", required=False):
    return forms.DecimalField(label=label, help_text=help_text, required=required, min_value=0, max_digits=4,
                              decimal_places=1, widget=forms.NumberInput(attrs={"step": "0.5", "inputmode": "decimal"}))


def _money(label, help_text=""):
    return WholeRupeesField(label=label, help_text=help_text, required=False)


class LineForm(forms.Form):
    """One employee's inputs for a draft month (same meaning as the sheet columns)."""

    present = _days(_("Days present"), _("Half days allowed, like 25.5."), required=True)
    paid_leave = _days(_("Paid leave days"))
    week_off_holiday = _days(_("Weekly off + holidays"), _("Paid weekly offs and paid holidays."))
    lop = _days(_("Absent / LOP days"), _("Leave empty to work it out from the other days."))
    ot_hours = forms.DecimalField(label=_("OT hours"), required=False, min_value=0, max_digits=8,
                                  decimal_places=2, help_text=_("Overtime hours this month."))
    ot_amount = _money(_("OT amount ₹ (optional)"), _("If filled, used instead of OT hours."))
    amount_paid = _money(_("Amount paid ₹"), _("What the owner actually paid this month. Basic becomes the minimum "
                                               "wage and the incentive is worked out. Leave empty to use the "
                                               "salary structure."))
    incentive = _money(_("Incentive / bonus ₹"), _("One-time extra pay this month."))
    increment = _money(_("Increment ₹ per month"), _("Raise added to Basic from this month."))
    advance = _money(_("Advance deduction ₹"))
    other_deduction = _money(_("Other deduction ₹"), _("For example canteen. Needs a reason."))
    other_reason = forms.CharField(label=_("Other deduction reason"), required=False, max_length=200)
    tds_override = _money(_("TDS override ₹ (optional)"), _("Only if your CA gave a TDS figure."))
    remarks = forms.CharField(label=_("Remarks"), required=False, max_length=200,
                              help_text=_("Shown in the salary register."))

    # Shown first, the rest under "More" (most changes are only the days).
    MAIN = ["present", "paid_leave", "week_off_holiday", "lop", "amount_paid"]

    def __init__(self, *args, amount_paid_on=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not amount_paid_on:
            del self.fields["amount_paid"]

    @property
    def main_fields(self):
        return [self[name] for name in self.MAIN if name in self.fields]

    @property
    def more_fields(self):
        return [self[name] for name in self.fields if name not in self.MAIN]

    def row_input(self):
        from .service import RowInput

        d = self.cleaned_data
        zero = Decimal("0")
        return RowInput(
            present=d["present"] or zero, paid_leave=d["paid_leave"] or zero,
            week_off_holiday=d["week_off_holiday"] or zero, lop=d["lop"], ot_hours=d["ot_hours"] or zero,
            ot_amount=d["ot_amount"], incentive=d["incentive"] or zero, increment=d["increment"] or zero,
            advance=d["advance"] or zero, other_deduction=d["other_deduction"] or zero,
            other_reason=d["other_reason"], tds_override=d["tds_override"], remarks=d["remarks"],
            amount_paid=d.get("amount_paid"),
        )

    @staticmethod
    def initial_from(line):
        return {
            "present": line.present, "paid_leave": line.paid_leave, "week_off_holiday": line.week_off_holiday,
            "lop": line.lop_days, "ot_hours": line.ot_hours, "ot_amount": line.ot_amount_override,
            "incentive": line.incentive, "increment": line.increment, "advance": line.advance_deduction,
            "other_deduction": line.other_deduction, "other_reason": line.other_deduction_reason,
            "tds_override": line.tds_override, "remarks": line.remarks, "amount_paid": line.amount_paid_input,
        }


class ReopenForm(forms.Form):
    reason = forms.CharField(label=_("Why are you reopening this month?"), max_length=500,
                             widget=forms.Textarea(attrs={"rows": 3}),
                             help_text=_("For example: wrong attendance for Ramesh. This is kept in the record."))
