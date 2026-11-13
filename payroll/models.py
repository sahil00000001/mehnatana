from decimal import Decimal

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from core.formatting import month_end, month_label
from core.models import CompanyOwned, days, money

TOTAL_FIELDS = [
    "gross", "earned_basic", "earned_da", "earned_hra", "earned_conveyance", "earned_other", "ot_pay",
    "incentive_pay", "ee_pf", "er_eps", "er_epf", "edli", "pf_admin", "ee_esi", "er_esi", "pt", "tds",
    "advance", "other_deduction_amt", "total_deductions", "net_pay", "employer_cost",
    "epf_wage", "eps_wage", "edli_wage", "esi_wage", "code_wages",
]


def upload_path(instance, filename):
    return f"private/uploads/{instance.company.code}/{instance.month:%Y-%m}/{filename}"


class PayrollRun(CompanyOwned):
    """One salary month of one company."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        FINALISED = "FINALISED", _("Finalised")

    month = models.DateField(help_text=_("First day of the month."))
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.DRAFT)
    uploaded_file = models.FileField(upload_to=upload_path, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    finalised_at = models.DateTimeField(null=True, blank=True)
    finalised_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    revision_no = models.PositiveSmallIntegerField(default=0)
    reopen_reason = models.TextField(blank=True)
    # Establishment PF admin charge = max(sum of line admin charges, minimum); top-up is the difference.
    pf_admin_establishment = money()
    pf_admin_topup = money()
    rates_snapshot = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        db_table = "mtn_payroll_run"
        ordering = ["-month"]
        constraints = [models.UniqueConstraint(fields=["company", "month"], name="mtn_payroll_run_month_unique")]

    def __str__(self):
        return f"{self.company.code} {month_label(self.month)} ({self.get_status_display()})"

    @property
    def is_final(self):
        return self.status == self.Status.FINALISED

    @property
    def label(self):
        return month_label(self.month)

    @property
    def month_end(self):
        return month_end(self.month)

    def totals(self):
        sums = self.lines.aggregate(**{f: Sum(f) for f in TOTAL_FIELDS})
        totals = {k: (v or Decimal("0")) for k, v in sums.items()}
        totals["count"] = self.lines.count()
        totals["company_cost"] = totals["employer_cost"] + self.pf_admin_topup
        totals["employer_pf"] = totals["er_eps"] + totals["er_epf"] + totals["edli"] + totals["pf_admin"]
        totals["pf_challan"] = (totals["ee_pf"] + totals["er_eps"] + totals["er_epf"] + totals["edli"]
                                + self.pf_admin_establishment)
        totals["esi_challan"] = totals["ee_esi"] + totals["er_esi"]
        return totals


class PayrollLine(CompanyOwned):
    """One employee in one month: the inputs and a complete frozen copy of the results."""

    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="lines")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="payroll_lines")
    # Employee details as they were for this month (printed on the payslip).
    emp_snapshot = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    # ---- inputs (from the sheet)
    days_in_month = models.PositiveSmallIntegerField(default=30)
    # Days in the month minus the company's weekly offs, when the month was worked out.
    working_days = models.PositiveSmallIntegerField(null=True, blank=True)
    present = days()
    paid_leave = days()
    week_off_holiday = days()
    lop_days = days(null=True, blank=True, default=None)  # as entered; empty = worked out
    ot_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    ot_amount_override = money(null=True, blank=True, default=None)
    incentive = money()
    increment = money()
    advance_deduction = money()
    other_deduction = money()
    other_deduction_reason = models.CharField(max_length=200, blank=True)
    tds_override = money(null=True, blank=True, default=None)
    remarks = models.CharField(max_length=200, blank=True)

    # ---- structure used (after increment)
    rate_basic = money()
    rate_da = money()
    rate_hra = money()
    rate_conveyance = money()
    rate_other = money()

    # ---- results
    lop = days()
    paid_days = days()
    ncp_days = models.PositiveSmallIntegerField(default=0)
    earned_basic = money()
    earned_da = money()
    earned_hra = money()
    earned_conveyance = money()
    earned_other = money()
    ot_pay = money()
    incentive_pay = money()
    gross = money()
    code_wages = money()
    epf_wage = money()
    eps_wage = money()
    edli_wage = money()
    ee_pf = money()
    er_eps = money()
    er_epf = money()
    edli = money()
    pf_admin = money()
    esi_covered = models.BooleanField(default=False)
    esi_wage = money()
    ee_esi = money()
    er_esi = money()
    pt = money()
    tds = money()
    advance = money()
    other_deduction_amt = money()
    total_deductions = money()
    net_pay = money()
    employer_cost = money()
    warnings = models.JSONField(default=list, blank=True)
    engine_version = models.CharField(max_length=10, blank=True)

    # ---- payment (marked on Salary payments after the month is finalised)
    paid_amount = money()
    paid_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "mtn_payroll_line"
        ordering = ["employee__emp_code"]
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="mtn_payroll_line_unique")]

    def __str__(self):
        return f"{self.run} {self.emp_snapshot.get('code', self.employee_id)}"

    @property
    def code(self):
        return self.emp_snapshot.get("code", "")

    @property
    def name(self):
        return self.emp_snapshot.get("name", "")

    @property
    def rate_allowances(self):
        return self.rate_hra + self.rate_conveyance + self.rate_other

    @property
    def rate_gross(self):
        return self.rate_basic + self.rate_da + self.rate_allowances

    @property
    def pending(self):
        """Net pay not yet marked as paid (never below zero)."""
        return max(self.net_pay - self.paid_amount, Decimal("0"))

    @property
    def is_paid(self):
        return self.paid_on is not None and self.pending == 0

    @property
    def employer_pf(self):
        return self.er_eps + self.er_epf + self.edli + self.pf_admin

    @property
    def er_pf_total(self):
        return self.er_eps + self.er_epf
