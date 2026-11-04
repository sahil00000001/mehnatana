"""Reports: payslips, register, government files, dashboard, isolation (README §10, §11, §13, §17)."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from core.testing import NOV, DemoTestCase
from employees.models import Employee
from payroll.models import PayrollLine

from .fixes import fix_querysets

DEC = date(2026, 12, 1)


def sheet_rows(response):
    wb = load_workbook(BytesIO(response.content), read_only=True, data_only=True)
    rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    wb.close()
    return rows


class FinalisedMonthTest(DemoTestCase):
    def setUp(self):
        super().setUp()
        self.finalise_november()

    def line(self, code):
        return PayrollLine.objects.get(run__company=self.srks, run__month=NOV, employee__emp_code=code)

    def test_ecr_file(self):
        response = self.client.get(reverse("reports:ecr", args=[NOV]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="ECR_SRKS_202611.txt"', response["Content-Disposition"])
        self.assertFalse(response.content.startswith(b"\xef\xbb\xbf"))
        lines = response.content.decode("utf-8").split("\n")
        self.assertIn("101234567802#~#SUNITA DEVI#~#16200#~#9000#~#9000#~#9000#~#1080#~#750#~#330#~#3#~#0", lines)
        self.assertIn("101234567803#~#MOHD ARIF#~#40000#~#25000#~#25000#~#25000#~#3000#~#2083#~#917#~#0#~#0", lines)
        self.assertEqual(len(lines), 7)

    def test_pf_statement(self):
        response = self.client.get(reverse("reports:pf", args=[NOV]))
        self.assertContains(response, "29,252")
        self.assertContains(response, reverse("reports:ecr", args=[NOV]))
        self.assertNotContains(response, "Generate UAN")

    def test_esi_statement_and_file(self):
        self.assertContains(self.client.get(reverse("reports:esi", args=[NOV])), "1,683")
        response = self.client.get(reverse("reports:esi_file", args=[NOV]))
        self.assertIn('filename="ESI_SRKS_202611.xlsx"', response["Content-Disposition"])
        rows = sheet_rows(response)
        self.assertEqual(rows[0][0], "IP Number")
        by_ip = {r[0]: r for r in rows[1:]}
        self.assertEqual(sorted(by_ip), ["1234567801", "1234567802", "1234567805", "1234567806"])
        self.assertEqual(by_ip["1234567802"][1:5], ["Sunita Devi", 27, 9000, 0])
        self.assertEqual(by_ip["1234567806"][2:5], [15, 4500, 0])

    def test_register_totals(self):
        response = self.client.get(reverse("reports:register", args=[NOV]))
        self.assertContains(response, "Register of Wages — November 2026 — Shri Ram Kirana Stores")
        self.assertContains(response, "2,72,286")
        self.assertContains(response, "2,98,902")
        self.assertNotContains(response, "DRAFT")
        rows = sheet_rows(self.client.get(reverse("reports:register_xlsx", args=[NOV])))
        self.assertEqual(rows[-1][2], "Total")
        self.assertIn(272286, rows[-1])

    def test_payslip_e002(self):
        response = self.client.get(reverse("reports:payslip", args=[NOV, self.line("E002").pk]))
        self.assertContains(response, "15,052")
        self.assertContains(response, "Rupees Fifteen Thousand Fifty-Two Only")
        self.assertContains(response, "Form V")
        self.assertContains(response, "Ram Lal")
        self.assertContains(response, "8,100")
        self.assertNotContains(response, "DRAFT")
        self.assertNotContains(response, "Revised")

    def test_payslip_advance_balance(self):
        response = self.client.get(reverse("reports:payslip", args=[NOV, self.line("E001").pk]))
        self.assertContains(response, "Advance balance after this month: ₹ 4,000")

    def test_all_payslips(self):
        response = self.client.get(reverse("reports:payslips", args=[NOV]))
        self.assertEqual(response.content.decode().count('class="payslip'), 7)

    def test_two_payslips_per_page(self):
        self.srks.payslip_two_per_page = True
        self.srks.save()
        response = self.client.get(reverse("reports:payslips", args=[NOV]))
        self.assertEqual(response.content.decode().count('<div class="sheet">'), 4)
        self.assertContains(response, "cut here", count=3)

    def test_bank_sheet_and_cash_list(self):
        response = self.client.get(reverse("reports:bank_sheet", args=[NOV]))
        names = [r[2] for r in sheet_rows(response)[1:]]
        self.assertIn("Sunita Devi", names)
        self.assertNotIn("Kiran B", names)
        self.assertEqual(sheet_rows(response)[1][7], "Salary Nov-2026")
        cash = self.client.get(reverse("reports:cash_list", args=[NOV]))
        self.assertContains(cash, "Kiran B")
        self.assertContains(cash, "8,426")
        self.assertNotContains(cash, "Sunita Devi")

    def test_dashboard_toggle(self):
        home = reverse("home")
        self.assertContains(self.client.get(home + "?view=net"), "2,72,286")
        self.assertContains(self.client.get(home + "?view=gross"), "2,98,902")
        self.assertContains(self.client.get(home + "?view=ctc"), "3,15,481")
        response = self.client.get(home)
        self.assertContains(response, "29,252")  # PF challan
        self.assertContains(response, "1,683")  # ESI challan
        self.assertContains(response, "December 2026")  # next month to work on

    def test_statements_render(self):
        for name, args in [("reports:pt", [NOV]), ("reports:tds", [2026]), ("reports:cost", []),
                           ("reports:advances", []), ("reports:joiners_leavers", []), ("reports:increments", []),
                           ("reports:index", [])]:
            with self.subTest(report=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)
        self.assertContains(self.client.get(reverse("reports:pt", args=[NOV])), "600")
        tds = self.client.get(reverse("reports:tds", args=[2026]))
        self.assertContains(tds, "3,900")
        self.assertContains(tds, "6,760")
        self.assertContains(self.client.get(reverse("reports:joiners_leavers")), "Kiran B")
        self.assertContains(self.client.get(reverse("reports:increments")), "Increment of ₹500")
        self.assertContains(self.client.get(reverse("reports:advances")), "4,000")

    def test_excel_downloads(self):
        for name, args in [("reports:pf", [NOV]), ("reports:esi", [NOV]), ("reports:pt", [NOV]),
                           ("reports:tds", [2026]), ("reports:cost", []), ("reports:advances", []),
                           ("reports:joiners_leavers", []), ("reports:increments", [])]:
            with self.subTest(report=name):
                response = self.client.get(reverse(name, args=args) + "?format=xlsx")
                self.assertEqual(response.status_code, 200)
                self.assertIn(".xlsx", response["Content-Disposition"])

    def test_index_picker_redirects(self):
        response = self.client.get(reverse("reports:index"), {"go": "pf", "month": "2026-11"})
        self.assertRedirects(response, reverse("reports:pf", args=[NOV]))
        response = self.client.get(reverse("reports:index"), {"go": "tds", "tax_year": "2026"})
        self.assertRedirects(response, reverse("reports:tds", args=[2026]))

    def test_missing_uan_listed_and_left_out(self):
        # Ramesh has no UAN in December -> left out of that month's ECR and listed on the PF page.
        Employee.objects.filter(company=self.srks, emp_code="E001").update(uan="")
        from payroll import service
        ctx = service.RunContext(self.srks, DEC)
        rows = {e.pk: service.RowInput(present=D(27), week_off_holiday=D(4)) for e in ctx.employees}
        run = service.save_inputs(self.srks, DEC, rows, self.srks_user)
        service.finalise(run, self.srks_user)
        page = self.client.get(reverse("reports:pf", args=[DEC]))
        self.assertContains(page, "Generate UAN on the EPFO portal for:")
        self.assertContains(page, "Ramesh Kumar")
        ecr = self.client.get(reverse("reports:ecr", args=[DEC])).content.decode()
        self.assertNotIn("RAMESH", ecr)
        self.assertEqual(len(ecr.split("\n")), 6)


class DraftMonthTest(DemoTestCase):
    def test_files_refused_for_draft(self):
        for name in ("reports:ecr", "reports:esi_file", "reports:bank_sheet", "reports:register_xlsx"):
            with self.subTest(file=name):
                response = self.client.get(reverse(name, args=[NOV]))
                self.assertRedirects(response, reverse("payroll:month", args=[NOV]), fetch_redirect_response=False)

    def test_print_previews_show_draft(self):
        line = PayrollLine.objects.get(run__company=self.srks, run__month=NOV, employee__emp_code="E002")
        self.assertContains(self.client.get(reverse("reports:payslip", args=[NOV, line.pk])), "DRAFT — not final")
        self.assertContains(self.client.get(reverse("reports:register", args=[NOV])), "DRAFT — not final")
        self.assertContains(self.client.get(reverse("reports:pf", args=[NOV])), "DRAFT")

    def test_dashboard_before_finalising(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "This month: November 2026")
        self.assertContains(response, "Check and finalise November 2026")
        self.assertContains(response, "No month is finalised yet")
        self.assertContains(response, "Pay November 2026 salary")
        self.assertContains(response, "fix=no_appointment_letter")

    def test_things_to_fix(self):
        fixes = fix_querysets(self.srks, today=date(2026, 11, 5))
        self.assertEqual(fixes["no_appointment_letter"][1].count(), 7)
        self.assertEqual(fixes["no_uan"][1].count(), 0)
        self.assertEqual(fixes["no_esic"][1].count(), 0)
        self.assertEqual(list(fixes["below_50"][1].values_list("emp_code", flat=True)), ["E006"])
        self.assertEqual(fixes["no_instalment"][1].count(), 0)
        Employee.objects.filter(company=self.srks, emp_code="E002").update(esic_ip_no="")
        self.assertEqual(list(fix_querysets(self.srks)["no_esic"][1].values_list("emp_code", flat=True)), ["E002"])

    def test_turning_58(self):
        e = self.employee("E003")
        e.date_of_birth = date(1968, 12, 20)
        e.save()
        fixes = fix_querysets(self.srks, today=date(2026, 11, 5))
        self.assertEqual(list(fixes["turning_58"][1].values_list("emp_code", flat=True)), ["E003"])


class IsolationTest(DemoTestCase):
    """Logged in as SRKS, nothing of Annapurna Sweets (ANPS) can be seen."""

    def setUp(self):
        super().setUp()
        self.finalise_november()
        self.finalise_november(self.anps)
        self.anps_line = PayrollLine.objects.filter(run__company=self.anps).first()

    def test_other_company_line_is_404(self):
        self.assertEqual(self.client.get(reverse("reports:payslip", args=[NOV, self.anps_line.pk])).status_code, 404)

    def test_month_without_own_run_is_404(self):
        for name in ("reports:payslips", "reports:register", "reports:register_xlsx", "reports:bank_sheet",
                     "reports:cash_list", "reports:ecr", "reports:esi_file", "reports:pf", "reports:esi", "reports:pt"):
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name, args=[DEC])).status_code, 404)
        self.assertEqual(self.client.get(reverse("reports:cost") + "?month=2026-12").status_code, 404)

    def test_lists_never_show_other_company(self):
        urls = [reverse("home"), reverse("reports:payslips", args=[NOV]), reverse("reports:register", args=[NOV]),
                reverse("reports:cash_list", args=[NOV]), reverse("reports:pf", args=[NOV]),
                reverse("reports:esi", args=[NOV]), reverse("reports:pt", args=[NOV]), reverse("reports:tds", args=[2026]),
                reverse("reports:cost"), reverse("reports:advances"), reverse("reports:joiners_leavers") + "?from=2020-01-01",
                reverse("reports:increments") + "?from=2020-01-01"]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Seema Patil")
                self.assertNotContains(response, "Rahul More")
        for name in ("reports:ecr", "reports:esi_file", "reports:bank_sheet", "reports:register_xlsx"):
            with self.subTest(file=name):
                body = self.client.get(reverse(name, args=[NOV])).content
                self.assertNotIn(b"Seema", body)
                self.assertNotIn(b"2234567801", body)

    def test_anps_sees_own_figures(self):
        self.client.force_login(self.anps_user)
        response = self.client.get(reverse("reports:register", args=[NOV]))
        self.assertContains(response, "Seema Patil")
        self.assertNotContains(response, "Sunita Devi")
        self.assertContains(self.client.get(reverse("reports:pt", args=[NOV])), "175")
