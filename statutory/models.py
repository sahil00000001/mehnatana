"""
Statutory tables (platform level, no company). "Law is data": a new
notification is a new row with an effective date, never new code.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _

from companies.states import STATE_CHOICES
from core.models import TimeStamped


class StatutoryRate(TimeStamped):
    key = models.CharField(max_length=40, db_index=True)
    value = models.DecimalField(max_digits=14, decimal_places=4)
    effective_from = models.DateField()
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "mtn_statutory_rate"
        ordering = ["key", "-effective_from"]
        constraints = [models.UniqueConstraint(fields=["key", "effective_from"], name="mtn_rate_key_date_unique")]

    def __str__(self):
        return f"{self.key} = {self.value.normalize()} from {self.effective_from:%d-%m-%Y}"


class PTSlab(TimeStamped):
    class Gender(models.TextChoices):
        ANY = "ANY", _("Any")
        M = "M", _("Male")
        F = "F", _("Female")
        O = "O", _("Other")

    state = models.CharField(max_length=2, choices=STATE_CHOICES)
    gender = models.CharField(max_length=3, choices=Gender.choices, default=Gender.ANY)
    from_amount = models.DecimalField(max_digits=12, decimal_places=2)
    to_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                    help_text=_("Leave empty for 'and above'."))
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    february_amount = models.DecimalField(max_digits=12, decimal_places=2)
    effective_from = models.DateField()

    class Meta:
        db_table = "mtn_pt_slab"
        ordering = ["state", "-effective_from", "gender", "from_amount"]
        verbose_name = _("PT slab")

    def __str__(self):
        upper = f"{self.to_amount:.0f}" if self.to_amount is not None else "above"
        return f"{self.state} {self.gender} {self.from_amount:.0f}-{upper}: {self.amount:.0f}"


class TaxConfig(TimeStamped):
    class Regime(models.TextChoices):
        NEW = "NEW", _("New regime")
        OLD = "OLD", _("Old regime")

    tax_year = models.PositiveSmallIntegerField(help_text=_("2026 means April 2026 to March 2027."))
    regime = models.CharField(max_length=3, choices=Regime.choices)
    std_deduction = models.DecimalField(max_digits=12, decimal_places=2)
    rebate_limit = models.DecimalField(max_digits=12, decimal_places=2)
    rebate_max = models.DecimalField(max_digits=12, decimal_places=2)
    cess_pct = models.DecimalField(max_digits=5, decimal_places=2)
    slabs = models.JSONField(help_text=_("[[upper limit or null, rate %], ...] e.g. [[400000, 0], [800000, 5], [null, 30]]"))
    surcharge = models.JSONField(default=list, help_text=_("[[income above, rate %], ...]"))
    senior_exemption = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    super_senior_exemption = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "mtn_tax_config"
        ordering = ["-tax_year", "regime"]
        constraints = [models.UniqueConstraint(fields=["tax_year", "regime"], name="mtn_tax_year_regime_unique")]

    def __str__(self):
        return f"{self.tax_year}-{(self.tax_year + 1) % 100:02d} {self.regime}"


class DueDate(TimeStamped):
    item = models.CharField(max_length=30, unique=True, help_text=_("Short name, e.g. PF"))
    label = models.CharField(max_length=120, help_text=_("Reminder text, e.g. 'Deposit PF'"))
    day_of_next_month = models.PositiveSmallIntegerField()
    # NOTE: README §4.10 lists only item / day_of_next_month / note. The TDS due
    # date for March is 30 April, so two optional fields hold that exception.
    march_due_day = models.PositiveSmallIntegerField(null=True, blank=True)
    march_due_month = models.PositiveSmallIntegerField(null=True, blank=True)
    applies_to_state = models.CharField(max_length=2, blank=True, choices=STATE_CHOICES,
                                        help_text=_("Only for companies in this state (for PT). Empty = all."))
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "mtn_due_date"
        ordering = ["day_of_next_month", "item"]

    def __str__(self):
        return f"{self.label} by day {self.day_of_next_month}"
