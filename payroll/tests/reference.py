"""
Test-only copy of README Appendix A's constants and the §16.1 demo staff.
The real values come from the database (statutory seed); a test checks they match.
"""
from datetime import date
from decimal import Decimal as D

from payroll import engine

PT_SLABS = [
    ("KA", "ANY", D("25000"), None, D("200"), D("300")),
    ("MH", "M", D("7501"), D("10000"), D("175"), D("175")),
    ("MH", "M", D("10001"), None, D("200"), D("300")),
    ("MH", "O", D("7501"), D("10000"), D("175"), D("175")),
    ("MH", "O", D("10001"), None, D("200"), D("300")),
    ("MH", "F", D("25001"), None, D("200"), D("300")),
]
NEW_REGIME = dict(
    std_deduction=D("75000"),
    slabs=[(D("400000"), D("0")), (D("800000"), D("5")), (D("1200000"), D("10")),
           (D("1600000"), D("15")), (D("2000000"), D("20")), (D("2400000"), D("25")), (None, D("30"))],
    rebate_limit=D("1200000"), rebate_max=D("60000"),
    surcharge=[(D("5000000"), D("10")), (D("10000000"), D("15")), (D("20000000"), D("25"))],
    cess=D("4"), senior_exemption=None, super_senior_exemption=None,
)
OLD_REGIME = dict(
    std_deduction=D("50000"),
    slabs=[(D("250000"), D("0")), (D("500000"), D("5")), (D("1000000"), D("20")), (None, D("30"))],
    senior_exemption=D("300000"), super_senior_exemption=D("500000"),
    rebate_limit=D("500000"), rebate_max=D("12500"),
    surcharge=[(D("5000000"), D("10")), (D("10000000"), D("15")), (D("20000000"), D("25")), (D("50000000"), D("37"))],
    cess=D("4"),
)


def ref_rates(**overrides):
    return engine.Rates(pt_slabs=PT_SLABS, tax_new=NEW_REGIME, tax_old=OLD_REGIME, **overrides)


E = engine.Employee
S = engine.Structure
I = engine.MonthInput

# code -> (employee, structure, November 2026 inputs, YTD before November)
SRKS = {
    "E001": (E("E001", "Ramesh Kumar", "M", date(1995, 6, 15), date(2022, 4, 1)),
             S(D(12000)),
             I(D(26), week_off_holiday=D(4), ot_hours=D(10), increment=D(500), advance=D(1000)), engine.YTD()),
    "E002": (E("E002", "Sunita Devi", "F", date(1990, 2, 20), date(2021, 7, 1)),
             S(D(9000), hra=D(6000), conveyance=D(2000), other=D(1000)),
             I(D(22), paid_leave=D(1), week_off_holiday=D(4), lop=D(3)), engine.YTD()),
    "E003": (E("E003", "Mohd. Arif", "M", date(1985, 11, 5), date(2018, 1, 10)),
             S(D(20000), hra=D(10000), other=D(10000)),
             I(D(26), week_off_holiday=D(4)), engine.YTD()),
    "E004": (E("E004", "Priya Sharma", "F", date(1992, 8, 30), date(2026, 4, 1), eps_member=False),
             S(D(60000), hra=D(30000), other=D(20000)),
             I(D(26), week_off_holiday=D(4)), engine.YTD(D(770000), D(27300), D(1400))),
    "E005": (E("E005", "Gopal Rao", "M", date(1967, 5, 10), date(2015, 8, 1)),
             S(D(14000), hra=D(4000), other=D(2000)),
             I(D(26), week_off_holiday=D(4)), engine.YTD()),
    "E006": (E("E006", "Kiran B", "M", date(2003, 1, 25), date(2026, 11, 16)),
             S(D(8000), hra=D(6000), conveyance=D(4000)),
             I(D(13), week_off_holiday=D(2)), engine.YTD()),
    "E007": (E("E007", "Neha Joshi", "F", date(1988, 3, 12), date(2024, 6, 1), eps_member=False,
               tax_regime="OLD", old_regime_deductions=D(200000)),
             S(D(50000), hra=D(25000), other=D(15000)),
             I(D(26), week_off_holiday=D(4)), engine.YTD(D(630000), D(47320), D(1400))),
}

# §17.1 golden numbers:
# code: paid, lop, ncp, gross, code wages, epf, eps, ee_pf, er_eps, er_epf, edli, admin,
#       esi wage, ee_esi, er_esi, pt, tds, advance, total ded, net, employer cost
GOLDEN = {
    "E001": (30, 0, 0, 13702, 12500, 12500, 12500, 1500, 1041, 459, 63, 63, 12500, 94, 407, 0, 0, 1000, 2594, 11108, 15735),
    "E002": (27, 3, 3, 16200, 9000, 9000, 9000, 1080, 750, 330, 45, 45, 9000, 68, 293, 0, 0, 0, 1148, 15052, 17663),
    "E003": (30, 0, 0, 40000, 30000, 25000, 25000, 3000, 2083, 917, 125, 125, 0, 0, 0, 200, 0, 0, 3200, 36800, 43250),
    "E004": (30, 0, 0, 110000, 80000, 25000, 0, 3000, 0, 3000, 125, 125, 0, 0, 0, 200, 3900, 0, 7100, 102900, 113250),
    "E005": (30, 0, 0, 20000, 16000, 16000, 0, 1920, 0, 1920, 80, 80, 16000, 120, 520, 0, 0, 0, 2040, 17960, 22600),
    "E006": (15, 15, 0, 9000, 4500, 4500, 4500, 540, 375, 165, 23, 23, 4500, 34, 147, 0, 0, 0, 574, 8426, 9733),
    "E007": (30, 0, 0, 90000, 65000, 25000, 0, 3000, 0, 3000, 125, 125, 0, 0, 0, 200, 6760, 0, 9960, 80040, 93250),
}
GOLDEN_FIELDS = ["paid_days", "lop", "ncp_days", "gross", "code_wages", "epf_wage", "eps_wage", "ee_pf", "er_eps",
                 "er_epf", "edli", "pf_admin", "esi_wage", "ee_esi", "er_esi", "pt", "tds", "advance",
                 "total_deductions", "net_pay", "employer_cost"]
GOLDEN_TOTALS = dict(gross=298902, ee_pf=14040, er_eps=4249, er_epf=9791, edli=586, pf_admin=586, ee_esi=316,
                     er_esi=1367, pt=600, tds=10660, advance=1000, total_deductions=26616, net_pay=272286,
                     employer_cost=315481)
