from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
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


def first_of_this_month():
    today = date.today()
    return date(today.year, today.month, 1)


class Company(TimeStamped):
    class Divisor(models.TextChoices):
        CALENDAR = "CALENDAR", _("Days in the month (28 to 31)")
        FIXED_30 = "FIXED_30", _("Always 30 days")
        FIXED_26 = "FIXED_26", _("Always 26 days")

    class Regime(models.TextChoices):
        NEW = "NEW", _("New regime")
        OLD = "OLD", _("Old regime")

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
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Inactive companies cannot log in."))

    # ---- payroll settings
    salary_divisor = models.CharField(_("salary is divided by"), max_length=10, choices=Divisor.choices,
                                      default=Divisor.CALENDAR)
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
                                        default=Decimal("2"))
    ot_days_divisor = models.PositiveSmallIntegerField(_("overtime: working days in a month"), default=26)
    ot_hours_per_day = models.DecimalField(_("overtime: hours in a working day"), max_digits=4, decimal_places=2,
                                           default=Decimal("8"))
    notice_period_days = models.PositiveSmallIntegerField(_("notice period (days)"), default=30)
    probation_months = models.PositiveSmallIntegerField(_("probation (months)"), default=0)
    higher_wages_avenue = models.TextField(_("avenue for higher wages or position"),
                                           default=DEFAULT_HIGHER_WAGES_AVENUE)
    payslip_two_per_page = models.BooleanField(_("print two payslips on one A4 sheet"), default=False)

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
    def address(self):
        parts = [self.address_line1, self.address_line2, self.city]
        text = ", ".join(p for p in parts if p)
        return f"{text} {self.pincode}".strip()

    @property
    def state_name(self):
        return dict(STATE_CHOICES).get(self.state, self.state)


def validate_logo(upload):
    from django.conf import settings

    if upload.size > settings.MAX_LOGO_BYTES:
        raise ValidationError(_("The logo is bigger than 500 KB. Please use a smaller picture."))
    head = upload.read(8)
    upload.seek(0)
    if not (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff")):
        raise ValidationError(_("The logo must be a PNG or JPG picture."))
