"""Read-only figures for the dashboard and reports. Numbers come only from stored PayrollLine rows."""
import calendar
import re
from datetime import date
from decimal import Decimal as D

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _

from advances.models import Advance
from core.formatting import add_months, month_label, month_start, tax_year_of
from payroll.models import PayrollLine, PayrollRun
from statutory.models import DueDate

ZERO = D("0")

# Dashboard / cost report toggle: key -> label
COST_VIEWS = {
    "net": _("In-hand pay"),
    "gross": _("Gross salary"),
    "ctc": _("Total cost to company"),
}

UAN_RE = re.compile(r"^[0-9]{12}$")
IP_RE = re.compile(r"^[0-9]{10}$")


def finalised_runs(company):
    return PayrollRun.objects.filter(company=company, status=PayrollRun.Status.FINALISED).order_by("-month")


def latest_finalised(company):
    return finalised_runs(company).first()


def run_amounts(runs):
    """{run_id: {"count", "net", "gross", "employer_cost"}} in one query."""
    rows = (
        PayrollLine.objects.filter(run__in=runs)
        .values("run_id")
        .annotate(count=Count("id"), net=Sum("net_pay"), gross=Sum("gross"), employer_cost=Sum("employer_cost"))
    )
    return {r["run_id"]: r for r in rows}


def cost_rows(company, view, limit=12):
    """Last `limit` finalised months, newest first: [{run, count, amount}], largest amount."""
    runs = list(finalised_runs(company)[:limit])
    amounts = run_amounts(runs)
    rows = []
    for run in runs:
        a = amounts.get(run.pk, {})
        if view == "gross":
            amount = a.get("gross") or ZERO
        elif view == "ctc":
            amount = (a.get("employer_cost") or ZERO) + run.pf_admin_topup
        else:
            amount = a.get("net") or ZERO
        rows.append({"run": run, "count": a.get("count", 0), "amount": amount})
    largest = max((r["amount"] for r in rows), default=ZERO)
    return rows, largest


def employer_year_so_far(company, upto_run):
    """Employer PF (EPS + EPF + EDLI + establishment admin) and employer ESI, from 1 April to this month."""
    year = tax_year_of(upto_run.month)
    runs = finalised_runs(company).filter(month__gte=date(year, 4, 1), month__lte=upto_run.month)
    sums = PayrollLine.objects.filter(run__in=runs).aggregate(
        eps=Sum("er_eps"), epf=Sum("er_epf"), edli=Sum("edli"), esi=Sum("er_esi"))
    admin = runs.aggregate(t=Sum("pf_admin_establishment"))["t"] or ZERO
    pf = (sums["eps"] or ZERO) + (sums["epf"] or ZERO) + (sums["edli"] or ZERO) + admin
    return {"pf": pf, "esi": sums["esi"] or ZERO, "from": date(year, 4, 1), "to": upto_run.month}


def advance_balances(run, lines):
    """Advance still to recover after this month, per employee id.

    = advances given up to month end − recoveries up to and including this month;
    for a draft month this month's deduction is not recorded yet, so it is taken off.
    """
    ids = [l.employee_id for l in lines]
    out = {pk: ZERO for pk in ids}
    advances = (
        Advance.objects.filter(company=run.company, employee_id__in=ids, date_given__lte=run.month_end)
        .annotate(rec=Coalesce(Sum("recoveries__amount", filter=Q(recoveries__month__lte=run.month)), ZERO))
    )
    for adv in advances:
        out[adv.employee_id] += adv.amount - adv.rec
    if not run.is_final:
        for line in lines:
            out[line.employee_id] -= line.advance
    return {pk: max(ZERO, value) for pk, value in out.items()}


# ---------------------------------------------------------------- reminders
SWITCHES = {"PF": "pf_enabled", "ESI": "esi_enabled", "TDS": "tds_enabled", "PT": "pt_enabled"}


def due_dates_for(company, month):
    """[(due date, text)] for one salary month, from the DueDate table."""
    out = []
    state = company.pt_state or company.state
    nxt = add_months(month, 1)
    for d in DueDate.objects.all():
        if d.applies_to_state and d.applies_to_state != state:
            continue
        switch = next((flag for prefix, flag in SWITCHES.items() if d.item.upper().startswith(prefix)), None)
        if switch and not getattr(company, switch):
            continue
        if month.month == 3 and d.march_due_day and d.march_due_month:
            due = date(month.year, d.march_due_month,
                       min(d.march_due_day, calendar.monthrange(month.year, d.march_due_month)[1]))
        else:
            due = date(nxt.year, nxt.month, min(d.day_of_next_month, calendar.monthrange(nxt.year, nxt.month)[1]))
        out.append((due, d.label.replace("{month}", month_label(month))))
    return out


def reminders(company, open_month, today):
    """Dues of the month being worked on, plus upcoming dues of the last finalised month."""
    items = [{"due": due, "text": text, "overdue": due < today} for due, text in due_dates_for(company, open_month)]
    last = latest_finalised(company)
    if last and last.month != open_month:
        for due, text in due_dates_for(company, last.month):
            if due >= today:
                items.append({"due": due, "text": text, "overdue": False})
    return sorted(items, key=lambda i: i["due"])


# ---------------------------------------------------------------- government files
def clean_uan_name(name):
    """ECR name: capitals, only letters, spaces and dots."""
    cleaned = re.sub(r"[^A-Z. ]", " ", (name or "").upper())
    return " ".join(cleaned.split())


def pf_lines(run, lines):
    """Lines of PF members for this month (with PF wages, or zero-pay members while PF is on)."""
    settings = (run.rates_snapshot or {}).get("settings", {})
    pf_on = settings.get("pf_enabled", str(run.company.pf_enabled)) == "True"
    return [l for l in lines
            if l.epf_wage > 0 or (pf_on and l.emp_snapshot.get("pf_member") and l.code_wages == 0)]


def ecr_members(run, lines):
    """([(uan, name, line)] for the ECR file, [lines left out for want of a valid UAN])."""
    included, missing = [], []
    for line in pf_lines(run, lines):
        s = line.emp_snapshot
        uan = (s.get("uan") or "").strip()
        if UAN_RE.match(uan):
            included.append((uan, clean_uan_name(s.get("name_as_per_uan") or s.get("name")), line))
        else:
            missing.append(line)
    return included, missing


def esi_members(lines):
    """([lines for the ESI file], [covered lines left out for want of a 10-digit IP number])."""
    included, missing = [], []
    for line in lines:
        if not line.esi_covered:
            continue
        if IP_RE.match((line.emp_snapshot.get("esic_ip_no") or "").strip()):
            included.append(line)
        else:
            missing.append(line)
    return included, missing


def month_choices(company):
    """Months that have a salary run, newest first."""
    return list(PayrollRun.objects.filter(company=company).order_by("-month").values_list("month", flat=True))


def tax_year_choices(company, today):
    first = tax_year_of(company.payroll_start_month)
    last = max(tax_year_of(today), first)
    return list(range(last, first - 1, -1))


def default_month(company):
    run = latest_finalised(company) or PayrollRun.objects.filter(company=company).order_by("-month").first()
    return run.month if run else month_start(company.payroll_start_month)
