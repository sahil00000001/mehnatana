"""Fixes from the third round of hands-on testing (letters, reports, advances, shops without PF)."""
from decimal import Decimal as D

from django.urls import reverse

from advances.models import Advance
from core.formatting import indian_format
from core.testing import NOV, DemoTestCase
from employees.models import OpeningBalance
from letters.models import IssuedLetter
from payroll.models import PayrollLine


class LetterFixesTest(DemoTestCase):
    def test_show_the_letter_without_an_employee_asks_for_one(self):
        response = self.client.get(reverse("letters:new", args=["appointment"]), {"employee": "", "date": "2026-11-05"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose an employee from the list.")

    def test_a_corrected_letter_is_saved_but_a_double_press_is_not(self):
        url = reverse("letters:new", args=["appointment"])
        data = {"employee": self.employee("E002").pk, "date": "2026-11-05", "action": "save", "body": ""}
        self.client.post(url, data)
        self.client.post(url, data)  # Save pressed twice
        self.assertEqual(IssuedLetter.objects.filter(company=self.srks).count(), 1)
        first = IssuedLetter.objects.get(company=self.srks)
        response = self.client.post(url, dict(data, body="Dear Respected Sunita Devi,\n\nWelcome."))
        self.assertEqual(IssuedLetter.objects.filter(company=self.srks).count(), 2)
        corrected = IssuedLetter.objects.exclude(pk=first.pk).get(company=self.srks)
        self.assertRedirects(response, reverse("letters:detail", args=[corrected.pk]), fetch_redirect_response=False)
        self.assertIn("Dear Respected", corrected.body_snapshot_html)

    def test_reset_to_original_text_asks_first(self):
        url = reverse("letters:template_edit", args=["appointment"])
        self.client.post(url, {"action": "save", "body": "My own words for {{ employee.name }}."})
        page = self.client.get(url)
        self.assertContains(page, "Reset to original text")
        self.assertNotContains(page, "Yes, put back the original text")
        self.assertContains(self.client.get(url, {"reset": "ask"}), "Yes, put back the original text")
        self.assertContains(self.client.get(url), "My own words")  # nothing changed by asking
        self.client.post(url, {"action": "reset"})
        self.assertNotContains(self.client.get(url), "My own words")


class RelieveFixesTest(DemoTestCase):
    def relieve(self, **fields):
        emp = self.employee("E005")
        data = {"date_of_leaving": "2026-11-30", "leaving_reason": "Terminated", "resignation_date": "",
                "exit_remarks": ""}
        data.update(fields)
        return emp, self.client.post(reverse("employees:relieve", args=[emp.pk]), data)

    def test_no_resignation_date_unless_resigned(self):
        emp, _response = self.relieve(resignation_date="2026-12-15")
        emp.refresh_from_db()
        self.assertEqual(emp.status, "RELIEVED")
        self.assertIsNone(emp.resignation_date)
        letter = self.client.get(reverse("letters:new", args=["relieving"]),
                                 {"employee": emp.pk, "date": "2026-11-30"})
        self.assertNotContains(letter, "resignation letter dated")

    def test_resignation_after_the_last_day_is_refused(self):
        emp, response = self.relieve(leaving_reason="Resigned", resignation_date="2026-12-15")
        self.assertContains(response, "must be dated on or before the last working day (30-11-2026)")
        emp.refresh_from_db()
        self.assertEqual(emp.status, "ACTIVE")

    def test_letter_dated_before_the_last_day_is_flagged(self):
        emp, _response = self.relieve(leaving_reason="Resigned", resignation_date="2026-11-01")
        url = reverse("letters:new", args=["relieving"])
        early = self.client.get(url, {"employee": emp.pk, "date": "2026-11-20"})
        self.assertContains(early, "This letter is dated before the last working day (30-11-2026)")
        self.assertContains(early, "This refers to your resignation letter dated 01-11-2026.")
        self.assertNotContains(self.client.get(url, {"employee": emp.pk, "date": "2026-11-30"}),
                               "dated before the last working day")


class ReportFixesTest(DemoTestCase):
    def test_cost_report_for_a_draft_month_shows_the_last_finalised_one(self):
        response = self.client.get(reverse("reports:index"), {"go": "cost", "month": "2026-11"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "November 2026 is not finalised yet")
        self.finalise_november()
        response = self.client.get(reverse("reports:cost"), {"month": "2026-12"}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("reports:cost") + "?month=2026-11")
        self.assertContains(response, "Showing November 2026 instead.")

    def test_no_excel_from_a_draft_statement(self):
        for name in ("pf", "esi", "pt"):
            with self.subTest(report=name):
                url = reverse(f"reports:{name}", args=[NOV])
                self.assertNotContains(self.client.get(url), "Download Excel")
                response = self.client.get(url, {"format": "xlsx"})
                self.assertRedirects(response, reverse("payroll:month", args=[NOV]), fetch_redirect_response=False)
        self.finalise_november()
        response = self.client.get(reverse("reports:pf", args=[NOV]), {"format": "xlsx"})
        self.assertEqual(response["Content-Type"].split(";")[0],
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_tds_totals_row_shows_opening_tds(self):
        self.finalise_november()
        response = self.client.get(reverse("reports:tds", args=[2026]))
        openings = sum((o.ytd_tds + o.prev_employer_tds
                        for o in OpeningBalance.objects.filter(company=self.srks, tax_year=2026)), D(0))
        self.assertGreater(openings, 0)
        self.assertEqual(response.context["total_opening"], openings)
        tfoot = response.content.decode().split("<tfoot>")[1]
        self.assertIn(f'<td class="num">{indian_format(openings)}</td>', tfoot)

    def test_simple_payslip_has_pan_and_bank_name(self):
        self.srks.payslip_style = "SIMPLE"
        self.srks.save()
        line = PayrollLine.objects.get(run__company=self.srks, run__month=NOV, employee__emp_code="E002")
        response = self.client.get(reverse("reports:payslip", args=[NOV, line.pk]))
        self.assertContains(response, "ABCPD1234E")
        self.assertContains(response, "State Bank of India")

    def test_home_says_the_month_in_full(self):
        self.finalise_november()
        home = self.client.get(reverse("home"))
        self.assertContains(home, '<div class="value">November 2026</div>')
        self.assertNotContains(home, "Nov-2026")


class ShopWithoutPfTest(DemoTestCase):
    """ANPS has no PF: no PF buttons, no rows of PF zeros, no stray separators."""

    def setUp(self):
        self.client.force_login(self.anps_user)
        self.finalise_november(self.anps)

    def test_reports_and_home(self):
        index = self.client.get(reverse("reports:index"))
        self.assertNotContains(index, "PF statement and ECR file")
        self.assertContains(index, "ESI statement and ESI file")
        home = self.client.get(reverse("home"))
        self.assertNotContains(home, "PF challan total")
        self.assertNotContains(home, "Employer PF ₹")
        self.assertNotContains(home, "· ·")
        self.assertNotRegex(home.content.decode(), r"Bank sheet</a>\s*·\s*·")
        self.assertContains(home, "ESI challan total")
        self.assertContains(home, "download the bank and ESI files")

    def test_payslip_shows_only_what_the_employer_paid(self):
        self.anps.payslip_show_employer = True
        self.anps.save()
        line = PayrollLine.objects.filter(run__company=self.anps, run__month=NOV, er_esi__gt=0).first()
        self.assertIsNotNone(line)
        for style in ("CLASSIC", "MODERN", "SIMPLE"):
            self.anps.payslip_style = style
            self.anps.save()
            with self.subTest(style=style):
                response = self.client.get(reverse("reports:payslip", args=[NOV, line.pk]))
                self.assertNotContains(response, "PF admin")
                self.assertNotContains(response, "EDLI")
                self.assertContains(response, "ESI")

    def test_pf_page_says_pf_is_off(self):
        page = self.client.get(reverse("reports:pf", args=[NOV]))
        self.assertContains(page, "PF is turned off for your business")
        self.assertNotContains(page, "Download PF ECR file")


class AdvanceFixesTest(DemoTestCase):
    def test_new_advance_after_the_sheet_is_uploaded_says_where_to_change_it(self):
        emp = self.employee("E002")
        response = self.client.post(reverse("advances:add"), {
            "employee": emp.pk, "date_given": "2026-11-05", "amount": "3000", "monthly_installment": "1500",
            "note": "Medical"}, follow=True)
        self.assertContains(response, "The salary sheet for November 2026 is already uploaded")
        line = PayrollLine.objects.get(run__company=self.srks, run__month=NOV, employee=emp)
        self.assertContains(response, reverse("payroll:line_edit", args=[NOV, line.pk]))
        self.assertContains(response, "to ₹1,500")

    def test_no_hint_when_the_month_already_has_it(self):
        adv = Advance.objects.filter(company=self.srks, status="OPEN").select_related("employee").first()
        line = PayrollLine.objects.get(run__company=self.srks, run__month=NOV, employee=adv.employee)
        response = self.client.post(reverse("advances:detail", args=[adv.pk]),
                                    {"monthly_installment": str(int(line.advance_deduction))}, follow=True)
        self.assertNotContains(response, "is already uploaded")
        response = self.client.post(reverse("advances:detail", args=[adv.pk]),
                                    {"monthly_installment": str(int(line.advance_deduction) + 500)}, follow=True)
        self.assertContains(response, "The salary sheet for November 2026 is already uploaded")


class AmountMessageTest(DemoTestCase):
    def test_too_big_uses_indian_commas(self):
        response = self.client.post(reverse("advances:add"), {
            "employee": self.employee("E002").pk, "date_given": "2026-11-05", "amount": "10000000",
            "monthly_installment": "1000", "note": "x"})
        self.assertContains(response, "more than ₹99,99,999")
