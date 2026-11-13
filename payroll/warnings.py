"""Plain-language notes shown in the review table (README §8.11)."""
from django.utils.translation import gettext_lazy as _

MESSAGES = {
    "STRUCTURE_BELOW_50": _("Allowances are more than half of salary; PF/ESI worked out on half of gross."),
    "EPS_STOPPED_AGE_58": _("Employee is 58 or older: no pension (EPS); full employer share goes to PF."),
    "ESI_EMPLOYEE_SHARE_WAIVED": _("Daily wage ₹176 or less: employee ESI not deducted; employer still pays."),
    "DEDUCTIONS_ABOVE_50": _("Deductions are more than half of the salary. Check the advance/other deduction."),
    "NO_UAN": _("PF member without UAN — will be left out of the ECR file."),
    "NO_ESIC_NUMBER": _("ESI covered but no IP number — will be left out of the ESI file."),
    "JOINED_THIS_MONTH": _("Joined this month."),
    "LEFT_THIS_MONTH": _("Left this month."),
    "INCREMENT_LARGE": _("Increment is more than 50% of current salary — please confirm."),
    "RATE_CHANGED_IN_MONTH": _("A statutory rate changed inside this month; check with your CA."),
    "TAX_SLABS_NOT_UPDATED": _("Tax slabs for this year are not set; last year's were used."),
    "ZERO_PAY": _("No paid days this month."),
    "OT_HOURS_HIGH": _("More than 100 overtime hours this month — please check."),
    "TDS_OVERRIDE_HIGH": _("The TDS override is more than 30% of the salary — please check."),
    "WEEK_OFF_EMPTY": _("Weekly offs were left empty, so those days were counted as LOP — please check."),
    "BELOW_MINIMUM_WAGE": _("Basic + DA + other allowance is below the minimum wage for the days paid."),
    "DAYS_REDUCED": _("Paid days were reduced so that Basic (minimum wage for the days) stays within the take-home "
                      "amount."),
    "TAKE_HOME_NOT_EXACT": _("The take-home could not be made exactly the amount written (a deduction changes in "
                             "steps). Please check."),
}


def message_for(code):
    return MESSAGES.get(code, code)
