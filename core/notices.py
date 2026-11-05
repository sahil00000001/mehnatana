"""
Notice board: what the owner should know or do soon, shown on every page.

"urgent" = late or due within 3 days, "soon" = due within 10 days, "info" = good to know.
Only urgent and soon notices are counted in the menu and shown in the strip on every page;
the Notices page and the Home page show all of them.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from core.formatting import add_months, fmt_date, month_end, month_label

URGENT_DAYS = 3
SOON_DAYS = 10
ORDER = {"urgent": 0, "soon": 1, "info": 2}


@dataclass
class Notice:
    level: str
    text: str
    url: str = ""
    when: date = None
    detail: str = ""
    kind: str = "due"
    sort_key: tuple = field(default=(), repr=False)


def _days_phrase(days):
    if days < 0:
        return _("%(n)s days late") % {"n": -days} if days < -1 else _("1 day late")
    if days == 0:
        return _("due today")
    if days == 1:
        return _("due tomorrow")
    return _("due in %(n)s days") % {"n": days}


def _level_for(days):
    if days <= URGENT_DAYS:
        return "urgent"
    if days <= SOON_DAYS:
        return "soon"
    return "info"


def due_notices(company, today):
    from payroll.models import PayrollRun
    from payroll.service import first_open_month
    from reports.queries import due_dates_for

    out = []
    open_month = first_open_month(company)
    finalised = set(PayrollRun.objects.filter(company=company, status=PayrollRun.Status.FINALISED)
                    .values_list("month", flat=True))
    # Dues of the month being worked on and of the last two months (a late payment stays visible).
    for month in (add_months(open_month, -2), add_months(open_month, -1), open_month):
        if month < company.payroll_start_month:
            continue
        for due, text in due_dates_for(company, month):
            days = (due - today).days
            if days < -45:
                continue  # long past: no longer shown
            if month != open_month and month in finalised and days < 0:
                continue  # the app cannot know if a past month's dues were paid; only upcoming ones are shown
            out.append(Notice(level=_level_for(days), text=text, when=due, detail=_days_phrase(days),
                              url=reverse("payroll:month", args=[month])))
    # The salary month has ended but is not finalised yet.
    if today > month_end(open_month):
        salary_due = date(add_months(open_month, 1).year, add_months(open_month, 1).month, 7)
        days = (salary_due - today).days
        out.append(Notice(level="urgent" if days <= URGENT_DAYS else "soon", kind="month",
                          text=_("%(month)s salary is not finalised yet") % {"month": month_label(open_month)},
                          detail=_("salary must be paid by %(date)s") % {"date": fmt_date(salary_due)},
                          when=salary_due, url=reverse("payroll:month", args=[open_month])))
    return out


def staff_notices(company, today):
    from employees.models import Employee

    out = []
    leaving = Employee.objects.filter(company=company, date_of_leaving__gte=today,
                                      date_of_leaving__lte=today + timedelta(days=14))
    for emp in leaving:
        days = (emp.date_of_leaving - today).days
        out.append(Notice(level="soon" if days <= SOON_DAYS else "info", kind="staff", when=emp.date_of_leaving,
                          text=_("%(name)s's last working day is %(date)s") % {
                              "name": emp.full_name, "date": fmt_date(emp.date_of_leaving)},
                          detail=_("pay final dues within 2 working days"),
                          url=reverse("employees:detail", args=[emp.pk])))
    if company.probation_months:
        on_probation = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE,
                                               employment_type=Employee.EmploymentType.PROBATION)
        for emp in on_probation:
            ends = add_months(emp.date_of_joining.replace(day=1), company.probation_months)
            ends = ends.replace(day=min(emp.date_of_joining.day, month_end(ends).day))
            days = (ends - today).days
            if -7 <= days <= 30:
                out.append(Notice(level="soon" if days <= SOON_DAYS else "info", kind="staff", when=ends,
                                  text=_("%(name)s's probation ends on %(date)s") % {
                                      "name": emp.full_name, "date": fmt_date(ends)},
                                  detail=_("confirm the job or extend probation"),
                                  url=reverse("employees:detail", args=[emp.pk])))
    return out


def fix_notices(company, today):
    from reports.fixes import fix_querysets

    out = []
    for key, (label, qs) in fix_querysets(company, today).items():
        count = qs.count()
        if count:
            base = reverse("advances:list") if key == "no_instalment" else reverse("employees:list")
            out.append(Notice(level="info", kind="fix", text=f"{label} ({count})", url=f"{base}?fix={key}"))
    return out


def notices_for(company, today=None, include_fixes=False):
    today = today or timezone.localdate()
    items = due_notices(company, today) + staff_notices(company, today)
    if include_fixes:
        items += fix_notices(company, today)
    return sorted(items, key=lambda n: (ORDER[n.level], n.when or date.max, n.text))


def needs_attention(notices):
    return [n for n in notices if n.level in ("urgent", "soon")]
