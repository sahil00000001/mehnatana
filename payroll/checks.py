"""
Checks of the attendance sheet that each company can switch on or off (Settings → Attendance sheet checks).

The checks that keep the salary correct (everyone in the sheet once, days that fit in the month, no negative
numbers, deductions not more than the salary) are always on and are only listed for information.

The switchable ones are applied when figures are entered (sheet upload, Change one person, Amounts paid),
never when a draft month is worked out again, so changing a setting never breaks a month already entered.
"""
from django.utils.translation import gettext_lazy as _

ALWAYS_ON = [
    _("Everyone paid this month is in the sheet, once, with a known Emp Code."),
    _("A sheet with no days filled in at all is refused."),
    _("No negative numbers; days in whole or half days; amounts below ₹1 crore."),
    _("Days cannot be more than the days in the month, or than the days the person was employed."),
    _("When Absent / LOP is written, all the days must add up to the days in the month."),
    _("Deductions cannot be more than the salary, and the advance cut cannot be more than the advance still due."),
]

# key: (label, help, default, kind, default number) — kind "switch" or "limit" (a switch with a number)
CHECKS = {
    "days_filled": (_("Every row must have its days filled in"),
                    _("Off: an empty row means the person did not work this month (no pay). A sheet with no days "
                      "at all is still refused."), True, "switch", None),
    "other_reason": (_("An other deduction needs a reason"), "", True, "switch", None),
    "ot_limit": (_("Stop when overtime is more than this many hours in a month"), "", True, "limit", 300),
    "present_le_working": (_("Days present cannot be more than the working days of the month"),
                           _("Working days are the days employed minus the weekly offs set in Payroll rules."),
                           False, "switch", None),
    "week_off_filled": (_("Weekly offs must be filled in"),
                        _("Stops a row where the weekly off column is empty and days were counted as absent."),
                        False, "switch", None),
    "paid_leave_limit": (_("Stop when paid leave is more than this many days in a month"), "", False, "limit", 4),
    "min_wage": (_("Stop when the pay is below the minimum wage"),
                 _("Basic + DA + other allowance for the days paid must reach the minimum wage entered for your "
                   "state (All companies → Minimum wages)."), False, "switch", None),
}


def settings_for(company):
    """{key: {"on": bool, "limit": number or None}} with the company's choices over the defaults."""
    saved = company.sheet_checks or {}
    out = {}
    for key, (_label, _help, default, kind, number) in CHECKS.items():
        mine = saved.get(key, {})
        out[key] = {"on": bool(mine.get("on", default)),
                    "limit": mine.get("limit", number) if kind == "limit" else None}
    return out


def is_on(company, key):
    return settings_for(company)[key]["on"]


def limit(company, key):
    return settings_for(company)[key]["limit"]
