"""
Payslip designs (README §11.1, Form V of the Wages (Central) Rules).

Every design prints the same particulars — only the look differs. A company
chooses its design once in Settings → Payslip design (company.payslip_style).
"""
from decimal import Decimal as D
from itertools import zip_longest

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.formatting import month_end, month_start

STYLE_TEMPLATES = {
    "CLASSIC": "reports/payslip_classic.html",
    "MODERN": "reports/payslip_modern.html",
    "SIMPLE": "reports/payslip_simple.html",
}
STYLE_NOTES = {
    "CLASSIC": gettext_lazy("Bordered like a register book — the traditional Form V wage slip."),
    "MODERN": gettext_lazy("Clean and corporate: shaded header, details in boxes, monthly rate next to each "
                           "earning and a big net pay box."),
    "SIMPLE": gettext_lazy("One easy table and a receipt strip for the employee's signature or thumb "
                           "impression — good when salary is paid in cash."),
}
RELATION_LABELS = {"Father": "Father's name", "Mother": "Mother's name", "Spouse": "Spouse's name"}


def template_for(company):
    return STYLE_TEMPLATES.get(company.payslip_style, STYLE_TEMPLATES["CLASSIC"])


def slip_context(line, advance_balance):
    """Everything one payslip shows, read from the frozen PayrollLine (never recalculated)."""
    s = line.emp_snapshot
    other_label = _("Other")
    if line.other_deduction_reason:
        other_label = _("Other (%(reason)s)") % {"reason": line.other_deduction_reason}
    # (label, monthly rate or None, earned this month)
    earnings = [
        (_("Basic"), line.rate_basic, line.earned_basic),
        (_("DA"), line.rate_da, line.earned_da),
        (_("HRA"), line.rate_hra, line.earned_hra),
        (_("Conveyance"), line.rate_conveyance, line.earned_conveyance),
        (_("Other allowance"), line.rate_other, line.earned_other),
        (_("Overtime"), None, line.ot_pay),
        (_("Incentive / Bonus"), None, line.incentive_pay),
    ]
    deductions = [
        (_("Provident Fund"), line.ee_pf), (_("ESI"), line.ee_esi), (_("Professional Tax"), line.pt),
        (_("Income Tax (TDS)"), line.tds), (_("Advance recovery"), line.advance),
        (other_label, line.other_deduction_amt),
    ]
    earnings_shown = [e for i, e in enumerate(earnings) if i == 0 or e[1] or e[2]]
    deductions_shown = [d for d in deductions if d[1]]
    # A shop without PF (or ESI) should not print a row of zeros for it
    employer_paid = [(label, amount) for label, amount in (
        (_("PF"), line.er_pf_total), (_("EDLI"), line.edli), (_("PF admin"), line.pf_admin), (_("ESI"), line.er_esi),
    ) if amount]
    return {
        "line": line,
        "s": s,
        "relation_label": _(RELATION_LABELS.get(s.get("relation_type"), "Father's/Mother's/Spouse's name")),
        # Classic shows every row side by side, like a register
        "rows": list(zip_longest([(e[0], e[2]) for e in earnings], deductions)),
        # Modern and Simple leave out rows that are zero
        "earnings": earnings_shown,
        "deductions": deductions_shown,
        "advance_balance": advance_balance,
        "employer_paid": employer_paid,
    }


def sample_line(company):
    """A made-up line (Sunita Devi, README §11.1) to preview the designs before any month exists."""
    from payroll.models import PayrollLine

    return PayrollLine(
        company=company, days_in_month=30, present=D(22), paid_leave=D(1), week_off_holiday=D(4), lop_days=D(3),
        rate_basic=D(9000), rate_hra=D(6000), rate_conveyance=D(2000), rate_other=D(1000),
        lop=D(3), paid_days=D(27), earned_basic=D(8100), earned_hra=D(5400), earned_conveyance=D(1800),
        earned_other=D(900), gross=D(16200), code_wages=D(9000), epf_wage=D(9000), eps_wage=D(9000),
        edli_wage=D(9000), ee_pf=D(1080), er_eps=D(750), er_epf=D(330), edli=D(45), pf_admin=D(45),
        esi_covered=True, esi_wage=D(9000), ee_esi=D(68), er_esi=D(293), total_deductions=D(1148),
        net_pay=D(15052), employer_cost=D(17663),
        emp_snapshot={
            "code": "E002", "name": "Sunita Devi", "title": "Ms.", "gender": "F", "relation_type": "Father",
            "relation_name": "Ram Lal", "designation": "Cashier", "department": "Store", "doj": "01-07-2021",
            "dol": "", "uan": "101234567802", "esic_ip_no": "1234567802", "pan": "ABCPD1234E",
            "payment_mode": "Bank", "bank_name": "SBI", "bank_account_no": "1234567890", "ifsc": "SBIN0001234",
            "pf_member": True, "eps_member": True, "tax_regime": "NEW",
        },
    )


def preview_context(company):
    """Context to draw each design with this company's latest payslip (or the sample)."""
    from payroll.models import PayrollLine

    line = (PayrollLine.objects.filter(company=company).select_related("run")
            .order_by("-run__status", "-run__month", "employee__emp_code").first())
    if line is not None:
        month, issued = line.run.month, (line.run.finalised_at or timezone.now())
    else:
        line = sample_line(company)
        month, issued = month_start(company.payroll_start_month), timezone.now()
    return {
        "slip": slip_context(line, D(0)), "company": company, "issue_date": timezone.localtime(issued).date(),
        "period_start": month, "period_end": month_end(month), "two_per_page": False, "revised": False,
        "is_sample": line.pk is None,
    }
