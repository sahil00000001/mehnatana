from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from core.models import CompanyOwned, money


class Advance(CompanyOwned):
    class Status(models.TextChoices):
        OPEN = "OPEN", _("Open")
        CLOSED = "CLOSED", _("Closed")

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="advances",
                                 verbose_name=_("employee"))
    date_given = models.DateField(_("date given"))
    amount = money(verbose_name=_("amount"))
    monthly_installment = money(verbose_name=_("monthly instalment"))
    note = models.CharField(_("note"), max_length=200, blank=True)
    status = models.CharField(_("status"), max_length=6, choices=Status.choices, default=Status.OPEN)

    class Meta:
        db_table = "mtn_advance"
        ordering = ["date_given", "id"]

    def __str__(self):
        return f"{self.employee.emp_code} {self.amount:.0f} on {self.date_given:%d-%m-%Y}"

    @property
    def recovered(self):
        total = self.recoveries.aggregate(t=Sum("amount"))["t"]
        return total or Decimal("0")

    @property
    def outstanding(self):
        return self.amount - self.recovered

    def refresh_status(self):
        status = self.Status.CLOSED if self.outstanding <= 0 else self.Status.OPEN
        if status != self.status:
            self.status = status
            self.save(update_fields=["status", "updated_at"])


class AdvanceRecovery(CompanyOwned):
    """Created only when a month is finalised; removed if that month is reopened."""

    advance = models.ForeignKey(Advance, on_delete=models.PROTECT, related_name="recoveries")
    payroll_line = models.ForeignKey("payroll.PayrollLine", on_delete=models.PROTECT,
                                     related_name="advance_recoveries")
    month = models.DateField()
    amount = money()

    class Meta:
        db_table = "mtn_advance_recovery"
        ordering = ["month", "id"]
