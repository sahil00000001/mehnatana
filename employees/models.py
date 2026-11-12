import unicodedata
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core import formatting
from core.models import CompanyOwned, money
from core.validators import (
    aadhaar_validator, esic_validator, ifsc_validator, mobile_validator, pan_validator, uan_validator,
)
from payroll import engine


class Department(CompanyOwned):
    name = models.CharField(_("name"), max_length=100)
    is_active = models.BooleanField(_("in use"), default=True)

    class Meta:
        db_table = "mtn_department"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="mtn_department_unique")]

    def __str__(self):
        return self.name


class Designation(CompanyOwned):
    name = models.CharField(_("name"), max_length=100)
    is_active = models.BooleanField(_("in use"), default=True)

    class Meta:
        db_table = "mtn_designation"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="mtn_designation_unique")]

    def __str__(self):
        return self.name


class Employee(CompanyOwned):
    class Relation(models.TextChoices):
        FATHER = "Father", _("Father")
        MOTHER = "Mother", _("Mother")
        SPOUSE = "Spouse", _("Spouse")

    class Gender(models.TextChoices):
        M = "M", _("Male")
        F = "F", _("Female")
        O = "O", _("Other")

    class EmploymentType(models.TextChoices):
        PERMANENT = "Permanent", _("Permanent")
        PROBATION = "Probation", _("Probation")
        FIXED_TERM = "Fixed-term", _("Fixed-term")
        CONTRACT = "Contract", _("Contract")
        PART_TIME = "Part-time", _("Part-time")

    class Skill(models.TextChoices):
        UNSKILLED = "Unskilled", _("Unskilled")
        SEMI_SKILLED = "Semi-skilled", _("Semi-skilled")
        SKILLED = "Skilled", _("Skilled")
        HIGHLY_SKILLED = "Highly skilled", _("Highly skilled")

    class PaymentMode(models.TextChoices):
        BANK = "Bank", _("Bank transfer")
        CASH = "Cash", _("Cash")
        CHEQUE = "Cheque", _("Cheque")
        UPI = "UPI", _("UPI")

    class EsiOverride(models.TextChoices):
        AUTO = "AUTO", _("Auto (by salary)")
        YES = "YES", _("Always yes")
        NO = "NO", _("Always no")

    class Regime(models.TextChoices):
        NEW = "NEW", _("New regime")
        OLD = "OLD", _("Old regime")

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        RELIEVED = "RELIEVED", _("Relieved")

    class LeavingReason(models.TextChoices):
        RESIGNED = "Resigned", _("Resigned")
        TERMINATED = "Terminated", _("Terminated")
        RETIRED = "Retired", _("Retired")
        ABSCONDING = "Absconding", _("Absconding")
        DECEASED = "Deceased", _("Deceased")
        OTHER = "Other", _("Other")

    # ---- identity
    emp_code = models.CharField(_("employee code"), max_length=20)
    first_name = models.CharField(_("first name"), max_length=60)
    surname = models.CharField(_("surname"), max_length=60, blank=True)
    relation_type = models.CharField(_("relation"), max_length=10, choices=Relation.choices, default=Relation.FATHER)
    # These two may be empty only for people brought over from an older payroll system that did not
    # have them. The add / edit form and the Excel upload still ask for both, and "Things to fix" lists
    # everyone who is missing them.
    relation_name = models.CharField(_("father's / mother's / spouse's name"), max_length=120, blank=True)
    gender = models.CharField(_("gender"), max_length=1, choices=Gender.choices)
    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)
    mobile = models.CharField(_("mobile"), max_length=10, blank=True, validators=[mobile_validator])
    email = models.EmailField(_("email"), blank=True)
    address = models.TextField(_("address"), blank=True)

    # ---- job
    date_of_joining = models.DateField(_("date of joining"))
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="employees", verbose_name=_("department"))
    designation = models.ForeignKey(Designation, on_delete=models.PROTECT, related_name="employees",
                                    verbose_name=_("designation"))
    reports_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="reportees", verbose_name=_("reports to"))
    employment_type = models.CharField(_("type of employment"), max_length=12, choices=EmploymentType.choices,
                                       default=EmploymentType.PERMANENT)
    skill_category = models.CharField(_("skill category"), max_length=15, choices=Skill.choices,
                                      default=Skill.UNSKILLED)
    nature_of_duties = models.TextField(_("nature of duties"), blank=True)

    # ---- statutory IDs
    pan = models.CharField(_("PAN"), max_length=10, blank=True, validators=[pan_validator])
    aadhaar = models.CharField(_("Aadhaar number"), max_length=12, blank=True, validators=[aadhaar_validator])
    aadhaar_consent = models.BooleanField(_("employee agrees to give Aadhaar"), default=False)
    uan = models.CharField(_("UAN"), max_length=12, blank=True, validators=[uan_validator])
    name_as_per_uan = models.CharField(_("name as per UAN"), max_length=120, blank=True)
    esic_ip_no = models.CharField(_("ESIC IP number"), max_length=10, blank=True, validators=[esic_validator])

    # ---- bank
    payment_mode = models.CharField(_("salary paid by"), max_length=6, choices=PaymentMode.choices,
                                    default=PaymentMode.BANK)
    bank_name = models.CharField(_("bank name"), max_length=100, blank=True)
    bank_account_no = models.CharField(_("bank account number"), max_length=20, blank=True)
    ifsc = models.CharField(_("IFSC"), max_length=11, blank=True, validators=[ifsc_validator])

    # ---- rules
    pf_member = models.BooleanField(_("PF member"), default=True)
    eps_member = models.BooleanField(_("pension (EPS) member"), default=True)
    esi_override = models.CharField(_("ESI"), max_length=4, choices=EsiOverride.choices, default=EsiOverride.AUTO)
    is_pwd = models.BooleanField(_("person with disability"), default=False)
    pt_exempt = models.BooleanField(_("exempt from professional tax"), default=False)
    tax_regime = models.CharField(_("income tax regime"), max_length=3, choices=Regime.choices, default=Regime.NEW)
    old_regime_deductions_annual = money(verbose_name=_("old regime deductions per year"))

    # ---- exit
    status = models.CharField(_("status"), max_length=8, choices=Status.choices, default=Status.ACTIVE)
    date_of_leaving = models.DateField(_("last working day"), null=True, blank=True)
    leaving_reason = models.CharField(_("reason for leaving"), max_length=12, choices=LeavingReason.choices,
                                      blank=True)
    resignation_date = models.DateField(_("date of resignation letter"), null=True, blank=True)
    exit_remarks = models.TextField(_("remarks"), blank=True)

    class Meta:
        db_table = "mtn_employee"
        ordering = ["emp_code"]
        constraints = [models.UniqueConstraint(fields=["company", "emp_code"], name="mtn_employee_code_unique")]

    def __str__(self):
        return f"{self.full_name} ({self.emp_code})"

    # ---------------------------------------------------------------- names
    @property
    def full_name(self):
        return " ".join(p for p in (self.first_name, self.surname) if p)

    @property
    def title(self):
        return {"M": "Mr.", "F": "Ms."}.get(self.gender, "Mx.")

    @property
    def him_her(self):
        return {"M": "him", "F": "her"}.get(self.gender, "them")

    @staticmethod
    def uan_name_from(full_name):
        """Default 'name as per UAN': capitals, letters and spaces (Mohd. Arif -> MOHD ARIF).

        NOTE: README §13.1 lets ECR names keep dots, but its golden ECR line (§17.1)
        prints "Mohd. Arif" as "MOHD ARIF". So the *default* drops dots (EPFO records
        usually have none); a dot typed into name_as_per_uan by the owner is kept.
        """
        cleaned = "".join(ch if ch.isalpha() or unicodedata.category(ch).startswith("M") else " "
                          for ch in full_name.upper())
        return " ".join(cleaned.split())

    # ---------------------------------------------------------------- masks
    @property
    def pan_masked(self):
        return formatting.mask(self.pan)

    @property
    def aadhaar_masked(self):
        return formatting.mask_aadhaar(self.aadhaar)

    @property
    def account_masked(self):
        return formatting.mask(self.bank_account_no)

    # ---------------------------------------------------------------- salary
    def structure_on(self, on):
        """Salary structure in force on a date: latest revision with effective_from <= date.

        Uses prefetched revisions when available. Falls back to the earliest
        revision for dates before the first one.
        """
        revisions = sorted(self.revisions.all(), key=lambda r: (r.effective_from, r.pk or 0))
        if not revisions:
            return None
        chosen = revisions[0]
        for rev in revisions:
            if rev.effective_from <= on:
                chosen = rev
        return chosen

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def clean(self):
        errors = {}
        if self.aadhaar and not self.aadhaar_consent:
            errors["aadhaar_consent"] = _("Tick the consent box to save the Aadhaar number, or leave Aadhaar empty.")
        if self.payment_mode == self.PaymentMode.BANK:
            for name in ("bank_name", "bank_account_no", "ifsc"):
                if not getattr(self, name):
                    errors[name] = _("Needed when salary is paid by bank transfer.")
        if self.date_of_birth and self.date_of_joining:
            age = engine.age_on(self.date_of_birth, self.date_of_joining)
            if age < 14 or age > 80:
                errors["date_of_birth"] = _("Please check the date of birth: age at joining would be %(age)s.") % {"age": age}
        if self.department_id and self.department.company_id != self.company_id:
            errors["department"] = _("Choose a department of your company.")
        if self.designation_id and self.designation.company_id != self.company_id:
            errors["designation"] = _("Choose a designation of your company.")
        if self.reports_to_id:
            boss = self.reports_to
            if boss.company_id != self.company_id:
                errors["reports_to"] = _("Choose a person from your company.")
            elif self.pk and boss.pk == self.pk:
                errors["reports_to"] = _("A person cannot report to himself or herself.")
            elif self.pk and self.reports_up_to(boss):
                errors["reports_to"] = _("%(name)s cannot report to someone who reports to %(him)s.") % {
                    "name": self.first_name, "him": self.him_her}
        if self.status == self.Status.RELIEVED and not self.date_of_leaving:
            errors["date_of_leaving"] = _("Enter the last working day.")
        if self.date_of_leaving and self.date_of_joining and self.date_of_leaving < self.date_of_joining:
            errors["date_of_leaving"] = _("The last working day cannot be before the date of joining.")
        if errors:
            raise ValidationError(errors)

    def reports_up_to(self, boss):
        """True if `boss` (directly or indirectly) reports to this employee."""
        seen = set()
        node = boss
        while node is not None and node.pk not in seen:
            if node.pk == self.pk:
                return True
            seen.add(node.pk)
            node = node.reports_to
        return False

    def save(self, *args, **kwargs):
        if not self.name_as_per_uan:
            self.name_as_per_uan = self.uan_name_from(self.full_name)
        if not self.aadhaar_consent:
            self.aadhaar = ""
        super().save(*args, **kwargs)


class SalaryRevision(CompanyOwned):
    """Salary structure history. The structure for a month is the latest row with effective_from <= month start."""

    class Source(models.TextChoices):
        MANUAL = "MANUAL", _("Changed by owner")
        MONTHLY_SHEET = "MONTHLY_SHEET", _("Increment in monthly sheet")
        OPENING = "OPENING", _("Starting salary")

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="revisions")
    effective_from = models.DateField(_("effective from"))
    basic = money(verbose_name=_("Basic"))
    da = money(verbose_name=_("DA"))
    hra = money(verbose_name=_("HRA"))
    conveyance = money(verbose_name=_("Conveyance"))
    other_allowance = money(verbose_name=_("Other allowance"))
    reason = models.CharField(_("reason"), max_length=200, blank=True)
    source = models.CharField(max_length=14, choices=Source.choices, default=Source.MANUAL)
    payroll_run = models.ForeignKey("payroll.PayrollRun", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="salary_revisions")

    class Meta:
        db_table = "mtn_salary_revision"
        ordering = ["-effective_from", "-id"]

    def __str__(self):
        return f"{self.employee.emp_code} from {self.effective_from:%m-%Y}: {self.gross}"

    @property
    def gross(self):
        return self.basic + self.da + self.hra + self.conveyance + self.other_allowance

    @property
    def allowances(self):
        return self.hra + self.conveyance + self.other_allowance

    def as_structure(self):
        return engine.Structure(
            basic=Decimal(self.basic), da=Decimal(self.da), hra=Decimal(self.hra),
            conveyance=Decimal(self.conveyance), other=Decimal(self.other_allowance),
        )


class OpeningBalance(CompanyOwned):
    """Pay and tax before this app was used (or from a previous employer). Used only for TDS."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="opening_balances")
    tax_year = models.PositiveSmallIntegerField(_("tax year"))
    ytd_gross = money(verbose_name=_("gross paid this year before the start month"))
    ytd_tds = money(verbose_name=_("TDS deducted"))
    ytd_pt = money(verbose_name=_("professional tax deducted"))
    prev_employer_income = money(verbose_name=_("income from previous employer"))
    prev_employer_tds = money(verbose_name=_("TDS by previous employer"))

    class Meta:
        db_table = "mtn_opening_balance"
        ordering = ["-tax_year", "employee__emp_code"]
        constraints = [models.UniqueConstraint(fields=["employee", "tax_year"], name="mtn_opening_balance_unique")]

    def __str__(self):
        return f"{self.employee.emp_code} {formatting.tax_year_label(self.tax_year)}"
