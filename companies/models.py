from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStamped
from core.validators import (
    company_code_validator, gstin_validator, pan_validator, pincode_validator, tan_validator,
)

from .states import STATE_CHOICES

DEFAULT_HIGHER_WAGES_AVENUE = (
    "Yearly review of work; increments and promotion to higher posts based on performance and vacancies."
)


def logo_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"private/logos/{instance.code}/logo.{ext}"


WEEKDAYS = [("0", _("Monday")), ("1", _("Tuesday")), ("2", _("Wednesday")), ("3", _("Thursday")),
            ("4", _("Friday")), ("5", _("Saturday")), ("6", _("Sunday"))]


def first_of_this_month():
    today = date.today()
    return date(today.year, today.month, 1)


class Company(TimeStamped):
    class Divisor(models.TextChoices):
        WORKING = "WORKING", _("Working days of the month (without weekly offs)")
        CALENDAR = "CALENDAR", _("Days in the month (28 to 31)")
        FIXED_30 = "FIXED_30", _("Always 30 days")
        FIXED_26 = "FIXED_26", _("Always 26 days")

    class Regime(models.TextChoices):
        NEW = "NEW", _("New regime")
        OLD = "OLD", _("Old regime")

    class AmountPaid(models.TextChoices):
        GROSS = "GROSS", _("On: write each person's gross salary (before PF and ESI) — PayCore matches it")
        IN_HAND = "IN_HAND", _("On: write each person's take-home pay (after PF and ESI) — PayCore matches it")
        OFF = "OFF", _("Off: salary comes only from each person's salary structure")

    class PayslipStyle(models.TextChoices):
        CLASSIC = "CLASSIC", _("Classic ledger")
        MODERN = "MODERN", _("Modern")
        SIMPLE = "SIMPLE", _("Simple, with receipt")

    PF_RATE_CHOICES = [(Decimal("12.00"), _("12% (normal)")), (Decimal("10.00"), _("10% (only if notified for 10%)"))]

    # ---- identity
    code = models.CharField(_("company code"), max_length=6, unique=True, validators=[company_code_validator],
                            help_text=_("3 to 6 capital letters, used in letter numbers and file names."))
    legal_name = models.CharField(_("legal name"), max_length=200)
    trade_name = models.CharField(_("trade name"), max_length=200,
                                  help_text=_("The shop name printed on payslips and letters."))
    address_line1 = models.CharField(_("address line 1"), max_length=200)
    address_line2 = models.CharField(_("address line 2"), max_length=200, blank=True)
    city = models.CharField(_("city"), max_length=100)
    state = models.CharField(_("state"), max_length=2, choices=STATE_CHOICES)
    pincode = models.CharField(_("PIN code"), max_length=6, validators=[pincode_validator])
    phone = models.CharField(_("phone"), max_length=15, blank=True)
    email = models.EmailField(_("email"), blank=True)
    logo = models.FileField(_("logo"), upload_to=logo_path, blank=True,
                            help_text=_("Optional. PNG or JPG, not more than 500 KB."))
    pan = models.CharField(_("PAN"), max_length=10, blank=True, validators=[pan_validator])
    tan = models.CharField(_("TAN"), max_length=10, blank=True, validators=[tan_validator])
    gstin = models.CharField(_("GSTIN"), max_length=15, blank=True, validators=[gstin_validator])
    pf_establishment_code = models.CharField(_("PF establishment code"), max_length=30, blank=True)
    esic_employer_code = models.CharField(_("ESIC employer code"), max_length=30, blank=True)
    lin = models.CharField(_("Labour Identification Number (LIN)"), max_length=30, blank=True,
                           help_text=_("Printed on appointment letters."))
    pt_registration_no = models.CharField(_("PT registration number"), max_length=30, blank=True)
    signatory_name = models.CharField(_("signatory name"), max_length=120, blank=True,
                                      help_text=_("Person who signs payslips and letters."))
    signatory_designation = models.CharField(_("signatory designation"), max_length=120, blank=True)
    payroll_start_month = models.DateField(_("first salary month in this app"), default=first_of_this_month)
    is_active = models.BooleanField(_("active"), default=True,
                                    help_text=_("A switched-off company is kept, but cannot be opened until it is switched on."))

    # ---- working days and payroll settings
    weekly_off_days = models.CharField(
        _("weekly off days"), max_length=7, blank=True, default="6",
        help_text=_("Days of the week the business is closed (0 = Monday … 6 = Sunday). "
                    "Six-day week (Monday to Saturday): only Sunday."))
    salary_divisor = models.CharField(_("salary is divided by"), max_length=10, choices=Divisor.choices,
                                      default=Divisor.CALENDAR)
    amount_paid_mode = models.CharField(
        _("salary from the amount paid"), max_length=8, choices=AmountPaid.choices, default=AmountPaid.GROSS,
        help_text=_("When on, you can write what the owner actually pays each person, with or without the "
                    "attendance sheet. Basic becomes the minimum wage, ESI and PF (if applicable) are worked out on "
                    "it, and the rest is shown as incentive, so the salary matches exactly."))
    # Optional checks of the attendance sheet (see payroll/checks.py); empty = the defaults.
    sheet_checks = models.JSONField(_("attendance sheet checks"), default=dict, blank=True)
    pf_enabled = models.BooleanField(_("PF applies"), default=True)
    pf_employee_rate = models.DecimalField(_("employee PF rate"), max_digits=4, decimal_places=2,
                                           choices=PF_RATE_CHOICES, default=Decimal("12.00"))
    pf_restrict_to_ceiling = models.BooleanField(_("PF only on wages up to the ceiling"), default=True)
    esi_enabled = models.BooleanField(_("ESI applies"), default=False)
    pt_enabled = models.BooleanField(_("professional tax applies"), default=True)
    tds_enabled = models.BooleanField(_("deduct income tax (TDS)"), default=True)
    pt_state = models.CharField(_("state for professional tax"), max_length=2, choices=STATE_CHOICES, blank=True)
    default_tax_regime = models.CharField(_("default tax regime for new staff"), max_length=3,
                                          choices=Regime.choices, default=Regime.NEW)
    ot_multiplier = models.DecimalField(_("overtime rate (times ordinary)"), max_digits=4, decimal_places=2,
                                        validators=[MinValueValidator(1), MaxValueValidator(5)],
                                        default=Decimal("2"))
    ot_days_divisor = models.PositiveSmallIntegerField(_("overtime: working days in a month"), default=26,
                                                       validators=[MinValueValidator(1), MaxValueValidator(31)])
    ot_hours_per_day = models.DecimalField(_("overtime: hours in a working day"), max_digits=4, decimal_places=2,
                                           validators=[MinValueValidator(1), MaxValueValidator(24)],
                                           default=Decimal("8"))
    notice_period_days = models.PositiveSmallIntegerField(_("notice period (days)"), default=30,
                                                          validators=[MaxValueValidator(365)])
    probation_months = models.PositiveSmallIntegerField(_("probation (months)"), default=0,
                                                        validators=[MaxValueValidator(36)])
    higher_wages_avenue = models.TextField(_("avenue for higher wages or position"),
                                           default=DEFAULT_HIGHER_WAGES_AVENUE)
    payslip_two_per_page = models.BooleanField(_("print two payslips on one A4 sheet"), default=False)
    payslip_style = models.CharField(_("payslip design"), max_length=10, choices=PayslipStyle.choices,
                                     default=PayslipStyle.CLASSIC)
    payslip_show_employer = models.BooleanField(_("show what the employer also paid (PF, ESI)"), default=True)
    payslip_note = models.CharField(_("note at the bottom of every payslip"), max_length=200, blank=True,
                                    help_text=_("Optional, for example 'Thank you for your hard work.'"))

    class Meta:
        db_table = "mtn_company"
        ordering = ["trade_name"]
        verbose_name = _("company")
        verbose_name_plural = _("companies")

    def __str__(self):
        return f"{self.trade_name} ({self.code})"

    def clean(self):
        if self.payroll_start_month and self.payroll_start_month.day != 1:
            self.payroll_start_month = self.payroll_start_month.replace(day=1)
        if self.payroll_start_month:
            latest = date(date.today().year + 1, 12, 1)
            if not (date(2017, 1, 1) <= self.payroll_start_month <= latest):
                raise ValidationError({"payroll_start_month": _(
                    "Choose a month between January 2017 and %(latest)s.") % {"latest": latest.strftime("%B %Y")}})
        if self.code:
            self.code = self.code.upper()
        for name in ("pan", "tan", "gstin"):
            setattr(self, name, (getattr(self, name) or "").upper().strip())

    def save(self, *args, **kwargs):
        if not self.pt_state:
            self.pt_state = self.state
        if self.payroll_start_month and self.payroll_start_month.day != 1:
            self.payroll_start_month = self.payroll_start_month.replace(day=1)
        super().save(*args, **kwargs)

    @property
    def weekly_off(self):
        """Weekly off days as weekday numbers (Monday = 0), for example (6,) for a six-day week."""
        return tuple(sorted({int(ch) for ch in self.weekly_off_days or "" if ch in "0123456"}))

    @property
    def working_week_label(self):
        offs = self.weekly_off
        if not offs:
            return _("Seven days a week (no weekly off)")
        if offs == (6,):
            return _("Six days a week, Monday to Saturday (Sunday off)")
        if offs == (5, 6):
            return _("Five days a week, Monday to Friday (Saturday and Sunday off)")
        names = dict(WEEKDAYS)
        return _("%(n)s days a week (off: %(days)s)") % {
            "n": 7 - len(offs), "days": ", ".join(str(names[str(d)]) for d in offs)}

    @property
    def address(self):
        parts = [self.address_line1, self.address_line2, self.city]
        text = ", ".join(p for p in parts if p)
        return f"{text} {self.pincode}".strip()

    @property
    def state_name(self):
        return dict(STATE_CHOICES).get(self.state, self.state)


class MinimumWage(TimeStamped):
    """Minimum wages per month for one state, from a month onwards. Entered once and used by every company there."""

    SKILLS = ("unskilled", "semi_skilled", "skilled", "highly_skilled")
    # Employee.skill_category value -> field
    BY_SKILL = {"Unskilled": "unskilled", "Semi-skilled": "semi_skilled", "Skilled": "skilled",
                "Highly skilled": "highly_skilled"}

    state = models.CharField(_("state"), max_length=2, choices=STATE_CHOICES)
    effective_from = models.DateField(_("from month"))
    unskilled = models.DecimalField(_("unskilled ₹ per month"), max_digits=12, decimal_places=2, null=True, blank=True)
    semi_skilled = models.DecimalField(_("semi-skilled ₹ per month"), max_digits=12, decimal_places=2, null=True,
                                       blank=True)
    skilled = models.DecimalField(_("skilled ₹ per month"), max_digits=12, decimal_places=2, null=True, blank=True)
    highly_skilled = models.DecimalField(_("highly skilled ₹ per month"), max_digits=12, decimal_places=2, null=True,
                                         blank=True)

    class Meta:
        db_table = "mtn_minimum_wage"
        ordering = ["state", "-effective_from"]
        constraints = [models.UniqueConstraint(fields=["state", "effective_from"], name="mtn_minimum_wage_unique")]

    def __str__(self):
        return f"{self.get_state_display()} from {self.effective_from:%m-%Y}"

    def save(self, *args, **kwargs):
        self.effective_from = self.effective_from.replace(day=1)
        super().save(*args, **kwargs)

    def for_skill(self, skill_category):
        """Monthly minimum wage for an Employee.skill_category, or None if not entered."""
        return getattr(self, self.BY_SKILL.get(skill_category, "unskilled"))

    @classmethod
    def in_force(cls, state, month):
        """The rates for a state in force in a month (latest 'from month' on or before it), or None."""
        return cls.objects.filter(state=state, effective_from__lte=month).order_by("-effective_from").first()


def validate_logo(upload):
    from django.conf import settings

    if upload.size > settings.MAX_LOGO_BYTES:
        raise ValidationError(_("The logo is bigger than 500 KB. Please use a smaller picture."))
    head = upload.read(8)
    upload.seek(0)
    if not (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff")):
        raise ValidationError(_("The logo must be a PNG or JPG picture."))
