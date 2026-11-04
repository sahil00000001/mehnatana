"""README §17.1 golden numbers and §17.2 edge cases, run on the pure engine."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase

from core.formatting import indian_format
from payroll import engine
from payroll.engine import amount_in_words, annual_income_tax, compute_line, professional_tax
from statutory.rates import rates_for_month

from .reference import GOLDEN, GOLDEN_FIELDS, GOLDEN_TOTALS, NEW_REGIME, OLD_REGIME, PT_SLABS, SRKS, ref_rates

SETTINGS = engine.Settings()


def line(code, year=2026, month=11, settings=SETTINGS, rates=None, **input_changes):
    emp, structure, inp, ytd = SRKS[code]
    if input_changes:
        inp = replace(inp, **input_changes)
    return compute_line(year, month, emp, structure, structure, inp, settings, rates or ref_rates(), ytd)


class GoldenNumbersTest(SimpleTestCase):
    def test_every_employee_matches_17_1(self):
        for code, expected in GOLDEN.items():
            result = line(code)
            for field, value in zip(GOLDEN_FIELDS, expected):
                with self.subTest(code=code, field=field):
                    self.assertEqual(D(getattr(result, field)), D(value))

    def test_totals_and_establishment_figures(self):
        lines = [line(code) for code in GOLDEN]
        for field, value in GOLDEN_TOTALS.items():
            with self.subTest(field=field):
                self.assertEqual(sum(getattr(l, field) for l in lines), D(value))
        admin = engine.establishment_pf_admin(lines, ref_rates())
        self.assertEqual(admin, D(586))  # no top-up
        pf_challan = sum(l.ee_pf + l.er_eps + l.er_epf + l.edli for l in lines) + admin
        self.assertEqual(pf_challan, D(29252))
        self.assertEqual(sum(l.ee_esi + l.er_esi for l in lines), D(1683))

    def test_extra_assertions(self):
        e001 = line("E001")
        self.assertEqual(e001.ot_pay, D(1202))
        self.assertEqual(e001.basic, D(12500))
        e002 = line("E002")
        self.assertEqual((e002.basic, e002.hra, e002.conveyance, e002.other), (D(8100), D(5400), D(1800), D(900)))
        self.assertIn("EPS_STOPPED_AGE_58", line("E005").warnings)
        self.assertIn("STRUCTURE_BELOW_50", line("E006").warnings)

    def test_ecr_lines(self):
        self.assertEqual(
            engine.ecr_line("101234567802", "SUNITA DEVI", line("E002")),
            "101234567802#~#SUNITA DEVI#~#16200#~#9000#~#9000#~#9000#~#1080#~#750#~#330#~#3#~#0",
        )
        self.assertEqual(
            engine.ecr_line("101234567803", "MOHD ARIF", line("E003")),
            "101234567803#~#MOHD ARIF#~#40000#~#25000#~#25000#~#25000#~#3000#~#2083#~#917#~#0#~#0",
        )


class EdgeCasesTest(SimpleTestCase):
    def test_lop_blank_gives_same_result(self):
        blank = line("E002", lop=None)
        self.assertEqual(blank.lop, D(3))
        self.assertEqual(blank, line("E002"))

    def test_fixed_26_divisor(self):
        l = line("E002", settings=replace(SETTINGS, salary_divisor="FIXED_26"))
        self.assertEqual((l.basic, l.hra, l.conveyance, l.other), (D(7962), D(5308), D(1769), D(885)))
        self.assertEqual((l.gross, l.code_wages, l.ee_pf, l.ee_esi, l.net_pay),
                         (D(15924), D(8847), D(1062), D(67), D(14795)))

    def test_esi_on_gross(self):
        l = line("E002", rates=ref_rates(esi_on_code_wages=False))
        self.assertEqual((l.esi_wage, l.ee_esi, l.er_esi), (D(16200), D(122), D(527)))

    def test_pf_not_restricted_to_ceiling(self):
        l = line("E003", settings=replace(SETTINGS, pf_restrict_to_ceiling=False))
        self.assertEqual((l.epf_wage, l.ee_pf, l.er_eps, l.er_epf, l.edli, l.pf_admin),
                         (D(30000), D(3600), D(2083), D(1517), D(125), D(150)))

    def test_esi_employee_share_waived(self):
        emp = engine.Employee("X1", "Low Wage", "M", date(1995, 1, 1), date(2020, 1, 1))
        s = engine.Structure(D(4500))
        l = compute_line(2026, 11, emp, s, s, engine.MonthInput(D(30)), SETTINGS, ref_rates(), engine.YTD())
        self.assertEqual((l.ee_esi, l.er_esi), (D(0), D(147)))
        self.assertIn("ESI_EMPLOYEE_SHARE_WAIVED", l.warnings)

    def test_karnataka_pt_february(self):
        self.assertEqual(line("E003", year=2027, month=2, present=D(24), week_off_holiday=D(4)).pt, D(300))

    def test_tds_march_with_ytd(self):
        emp, s, _, _ = SRKS["E004"]
        inp = engine.MonthInput(D(27), week_off_holiday=D(4))  # full March
        l = compute_line(2027, 3, emp, s, s, inp, SETTINGS, ref_rates(), engine.YTD(D(1210000), D(42900)))
        self.assertEqual(l.tds, D(3900))

    def test_tds_april_no_ytd(self):
        emp, s, inp, _ = SRKS["E004"]
        l = compute_line(2026, 4, emp, s, s, inp, SETTINGS, ref_rates(pf_ceiling=D(15000)), engine.YTD())
        self.assertEqual(l.tds, D(3900))

    def test_karnataka_pt_threshold(self):
        self.assertEqual(professional_tax("KA", "M", D(24999), 11, PT_SLABS), D(0))
        self.assertEqual(professional_tax("KA", "M", D(25000), 11, PT_SLABS), D(200))

    def test_maharashtra_pt(self):
        cases = [("M", 7500, 11, 0), ("M", 9000, 11, 175), ("M", 15000, 11, 200), ("M", 15000, 2, 300),
                 ("F", 20000, 11, 0), ("F", 30000, 2, 300)]
        for gender, gross, month, expected in cases:
            with self.subTest(gender=gender, gross=gross, month=month):
                self.assertEqual(professional_tax("MH", gender, D(gross), month, PT_SLABS), D(expected))

    def test_ot_amount_wins_over_hours(self):
        l = line("E002", present=D(26), paid_leave=D(0), lop=D(0), ot_amount=D(750), ot_hours=D(5))
        self.assertEqual((l.ot_pay, l.gross, l.code_wages), (D(750), D(18750), D(10000)))

    def test_days_must_add_up(self):
        with self.assertRaisesMessage(ValueError, "days do not add up"):
            line("E002", present=D(28), paid_leave=D(0), week_off_holiday=D(4), lop=None)

    def test_annapurna_maharashtra_no_pf(self):
        settings = replace(SETTINGS, pf_enabled=False, pt_state="MH")
        seema = engine.Employee("A001", "Seema Patil", "F", date(1990, 1, 1), date(2024, 1, 1))
        rahul = engine.Employee("A002", "Rahul More", "M", date(1990, 1, 1), date(2024, 1, 1))
        full = engine.MonthInput(D(30))
        l1 = compute_line(2026, 11, seema, engine.Structure(D(15000)), engine.Structure(D(15000)), full,
                          settings, ref_rates(), engine.YTD())
        l2 = compute_line(2026, 11, rahul, engine.Structure(D(9500)), engine.Structure(D(9500)), full,
                          settings, ref_rates(), engine.YTD())
        self.assertEqual((l1.ee_esi, l1.er_esi, l1.pt, l1.net_pay), (D(113), D(488), D(0), D(14887)))
        self.assertEqual((l2.ee_esi, l2.er_esi, l2.pt, l2.net_pay), (D(72), D(309), D(175), D(9253)))

    def test_annual_tax_new_regime(self):
        cases = {1200000: 0, 1245000: 46800, 1275000: 74100, 1600000: 124800, 2500000: 343200,
                 5010000: 1133600, 5500000: 1407120}
        for taxable, expected in cases.items():
            with self.subTest(taxable=taxable):
                self.assertEqual(annual_income_tax(D(taxable), "NEW", 38, NEW_REGIME), D(expected))

    def test_annual_tax_old_regime(self):
        cases = [(500000, 38, 0), (827500, 38, 81120), (827500, 62, 78520), (1200000, 38, 179400)]
        for taxable, age, expected in cases:
            with self.subTest(taxable=taxable, age=age):
                self.assertEqual(annual_income_tax(D(taxable), "OLD", age, OLD_REGIME), D(expected))

    def test_amount_in_words(self):
        cases = {
            0: "Rupees Zero Only",
            21: "Rupees Twenty-One Only",
            11108: "Rupees Eleven Thousand One Hundred Eight Only",
            123456: "Rupees One Lakh Twenty-Three Thousand Four Hundred Fifty-Six Only",
            123456789: "Rupees Twelve Crore Thirty-Four Lakh Fifty-Six Thousand Seven Hundred Eighty-Nine Only",
        }
        for amount, text in cases.items():
            with self.subTest(amount=amount):
                self.assertEqual(amount_in_words(D(amount)), text)

    def test_indian_format(self):
        self.assertEqual(indian_format(1234567), "12,34,567")
        self.assertEqual(indian_format(999), "999")
        self.assertEqual(indian_format(100000), "1,00,000")


class SeededRatesMatchAppendixATest(TestCase):
    """The database seed (§9) must give the engine exactly the Appendix A values."""

    def test_november_2026(self):
        r = rates_for_month(date(2026, 11, 1)).rates
        self.assertEqual(r.pf_ceiling, D(25000))
        self.assertTrue(r.esi_on_code_wages)
        self.assertEqual(r.code_wage_min_pct, D(50))
        self.assertEqual(sorted(r.pt_slabs, key=str), sorted(PT_SLABS, key=str))
        self.assertEqual(r.tax_new, NEW_REGIME)
        self.assertEqual(r.tax_old, OLD_REGIME)
        for code, expected in GOLDEN.items():
            emp, s, inp, ytd = SRKS[code]
            result = compute_line(2026, 11, emp, s, s, inp, SETTINGS, r, ytd)
            self.assertEqual(result.net_pay, D(expected[-2]), code)
