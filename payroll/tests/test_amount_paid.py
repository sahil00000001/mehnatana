"""Gross salary split: the gross for the month is paid exactly; Basic = minimum wage for the paid days (days
reduced if needed), the rest incentive. The Gross salaries page (paid days + gross from the owner's list),
minimum wages, and the switchable sheet checks."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from companies.models import MinimumWage
from core.test_qa import same_form_back, sheet_with
from core.testing import NOV, DemoTestCase
from employees.models import Employee, SalaryRevision
from payroll import service
from payroll.models import PayrollRun

RATES = dict(unskilled=D(14000), semi_skilled=D(15500), skilled=D(17000), highly_skilled=D(19000))
DEC = date(2026, 12, 1)


class SplitCase(DemoTestCase):
    """SRKS with 'Gross salary split' on and Karnataka minimum wages (sample figures)."""

    def setUp(self):
        super().setUp()
        self.srks.amount_paid_mode = "GROSS"
        self.srks.save()
        MinimumWage.objects.create(state=self.srks.state, effective_from=date(2026, 4, 1), **RATES)
        service.recompute_run(self.run_for())

    def line(self, code, month=NOV):
        return self.run_for(month=month).lines.get(employee__emp_code=code)


class GrossSplitTest(SplitCase):
    def test_gross_is_paid_exactly_with_basic_at_the_minimum_wage(self):
        e2 = self.line("E002")  # gross 18,000, semi-skilled 15,500, 27 of 30 days paid
        self.assertEqual((e2.earned_basic, e2.incentive_pay, e2.gross), (D(13950), D(4050), D(18000)))
        self.assertEqual((e2.rate_basic, e2.rate_hra, e2.rate_other), (D(15500), D(0), D(0)))
        self.assertEqual(e2.ee_esi, D(105))  # 0.75% of Basic, not of the incentive
        self.assertEqual(e2.ee_pf, D(1674))  # 12% of Basic
        self.assertEqual(e2.net_pay, e2.gross - e2.total_deductions)
        e5 = self.line("E005")  # gross 20,000, skilled 17,000, full month
        self.assertEqual((e5.earned_basic, e5.incentive_pay, e5.gross), (D(17000), D(3000), D(20000)))

    def test_days_are_reduced_when_basic_would_be_more_than_the_gross(self):
        e1 = self.line("E001")  # gross 12,000 + 500 increment; unskilled 14,000 for 30 days is more
        self.assertEqual((e1.paid_days, e1.days_given, e1.present, e1.lop), (D(26), D(30), D(22), D(4)))
        self.assertEqual((e1.earned_basic, e1.incentive_pay), (D(12133), D(367)))  # 14,000 x 26/30
        self.assertEqual(e1.gross, D(12500) + e1.ot_pay)
        self.assertIn("DAYS_REDUCED", e1.warnings)
        page = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertContains(page, "Paid days reduced from 30 to 26")
        service.recompute_run(self.run_for())  # working it out again keeps the same result
        self.assertEqual((self.line("E001").paid_days, self.line("E001").days_given), (D(26), D(30)))

    def test_minimum_wage_needed(self):
        MinimumWage.objects.all().delete()
        page = self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(self.client, {})})
        self.assertContains(page, "There is no minimum wage for")

    def test_switching_off_pays_the_salary_heads_again(self):
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        service.recompute_run(self.run_for())
        e2 = self.line("E002")
        self.assertEqual(e2.incentive_pay, D(0))
        self.assertEqual(e2.gross, D(16200))  # 18,000 x 27/30 from the salary heads

    def test_an_increment_raises_the_persons_own_salary(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        raised = SalaryRevision.objects.get(employee=self.employee("E001"), payroll_run=self.run_for())
        self.assertEqual(raised.gross, D(12500))  # 12,000 + 500, not the minimum wage split

    def test_new_minimum_wages_work_drafts_out_again(self):
        self.client.post(reverse("portal:minimum_wages"), dict(
            state=self.srks.state, effective_from="01-04-2026", unskilled="14000", semi_skilled="16000",
            skilled="17000", highly_skilled="19000"))
        e2 = self.line("E002")
        self.assertEqual((e2.earned_basic, e2.gross), (D(14400), D(18000)))  # same gross, more of it Basic

    def test_sheet_days_split_the_gross_but_do_not_cut_it(self):
        headers = [c.value for c in load_workbook(BytesIO(self.client.get(
            reverse("payroll:sheet", args=[NOV])).content)).worksheets[0][1]]
        self.assertNotIn("Amount Paid ₹", headers)
        self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(
            self.client, {"E005": {"Days Present": 21, "Absent / LOP Days": 5}})})
        e5 = self.line("E005")
        self.assertEqual((e5.paid_days, e5.earned_basic, e5.gross), (D(25), D(14167), D(20000)))


class GrossSalariesPageTest(SplitCase):
    def url(self, month=NOV):
        return reverse("payroll:amounts", args=[month])

    def post(self, month=NOV, **fields):
        data = {}
        for e in service.employees_for_month(self.srks, month):
            current = e.structure_on(month)
            data[f"gross_{e.pk}"] = f"{current.gross:.0f}" if current else ""
            data[f"days_{e.pk}"] = ""
        for name, value in fields.items():
            kind, code = name.split("_", 1)
            data[f"{kind}_{self.employee(code).pk}"] = value
        return self.client.post(self.url(month), data)

    def test_the_owners_list_is_matched(self):
        """The list of 'P days' and 'gross salary @month' from the screenshot of 14-11-2026 (Delhi rates)."""
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        Employee.objects.filter(company=self.srks).update(skill_category="Unskilled")
        MinimumWage.objects.update(unskilled=D(20358))
        page = self.client.get(self.url(DEC))
        self.assertContains(page, "Save and start the month")
        self.assertContains(self.client.get(reverse("payroll:month", args=[DEC])), "Paid days and gross salaries")
        response = self.post(DEC, days_E001="31", gross_E001="25000", days_E002="31", gross_E002="18000",
                             days_E003="31", gross_E003="13000", days_E004="30", gross_E004="17000",
                             days_E005="26", gross_E005="16000")
        self.assertRedirects(response, reverse("payroll:month", args=[DEC]) + "#review",
                             fetch_redirect_response=False)
        expected = {"E001": (31, 20358, 4642), "E002": (27, 17731, 269), "E003": (19, 12477, 523),
                    "E004": (25, 16418, 582), "E005": (24, 15761, 239)}
        gross = {"E001": 25000, "E002": 18000, "E003": 13000, "E004": 17000, "E005": 16000}
        for code, (days, basic, incentive) in expected.items():
            line = self.line(code, DEC)
            self.assertEqual((line.paid_days, line.earned_basic, line.incentive_pay, line.gross),
                             (D(days), D(basic), D(incentive), D(gross[code])), code)
            self.assertEqual(line.net_pay, line.gross - line.total_deductions)
            self.assertEqual(line.present + line.paid_leave + line.week_off_holiday, D(days))  # registers add up
        self.assertEqual(self.line("E001", DEC).days_given, None)  # not reduced
        self.assertEqual(self.line("E003", DEC).days_given, D(31))
        self.assertEqual(SalaryRevision.objects.filter(employee=self.employee("E003"), effective_from=DEC).get().gross,
                         D(13000))  # the gross is kept from December on
        page = self.client.get(self.url(DEC))
        self.assertContains(page, "Save and work out")
        self.assertContains(page, 'name="days_%s" value="31"' % self.employee("E003").pk)  # the days as written
        self.assertContains(page, "Paid days shown as 19 (not 31)")

    def test_no_days_means_no_pay_and_typed_incentives_are_replaced(self):
        self.run_for().lines.filter(employee__emp_code="E005").update(incentive=2590)  # typed by hand earlier
        self.post(days_E002="0", gross_E005="20000")
        e2, e5 = self.line("E002"), self.line("E005")
        self.assertEqual((e2.paid_days, e2.gross, e2.net_pay), (D(0), D(0), D(0)))
        self.assertEqual(self.employee("E002").structure_on(NOV).gross, D(18000))  # salary not touched
        self.assertEqual((e5.incentive, e5.gross), (D(0), D(20000)))

    def test_started_month_change_days_and_gross(self):
        page = self.client.get(self.url())
        self.assertContains(page, "Gross salaries — November 2026")
        self.assertContains(page, 'value="20000"')  # E005's gross, from the salary structure
        self.post(gross_E005="22000", days_E005="28")
        e5 = self.line("E005")
        self.assertEqual((e5.paid_days, e5.gross), (D(28), D(22000)))
        self.assertEqual(self.employee("E005").structure_on(NOV).gross, D(22000))

    def test_mistakes_save_nothing(self):
        page = self.post(gross_E005="abc", days_E001="1.3", days_E002="99")
        self.assertContains(page, "Write an amount in rupees")
        self.assertContains(page, "Use whole or half days only")
        self.assertContains(page, "Nothing was saved.")
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        page = self.post(DEC, days_E002="99")
        self.assertContains(page, "cannot be more than the 31 days employed")
        self.assertFalse(PayrollRun.objects.filter(company=self.srks, month=DEC).exists())

    def test_page_needs_the_split_on_and_months_in_order(self):
        response = self.client.get(self.url(DEC))
        self.assertRedirects(response, reverse("payroll:month", args=[DEC]), fetch_redirect_response=False)
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        self.assertRedirects(self.client.get(self.url()), reverse("companies:payroll"),
                             fetch_redirect_response=False)

    def test_employee_forms_ask_one_gross_salary(self):
        page = self.client.get(reverse("employees:add"))
        self.assertContains(page, "Gross salary per month ₹")
        self.assertNotContains(page, 'name="hra"')
        url = reverse("employees:revise", args=[self.employee("E005").pk])
        form_page = self.client.get(url)
        self.assertContains(form_page, "Gross salary per month ₹")
        data = same_form_back(form_page)
        self.assertEqual(data["basic"], "20000")
        data.update(basic="24000", reason="Yearly raise", effective_month="2026-12")
        self.client.post(url, data)
        rev = SalaryRevision.objects.filter(employee=self.employee("E005")).order_by("-id").first()
        self.assertEqual((rev.basic, rev.hra, rev.gross), (D(24000), D(0), D(24000)))


class MinimumWagesPageTest(DemoTestCase):
    url = reverse("portal:minimum_wages")

    def test_add_change_and_remove(self):
        data = {"state": "DL", "effective_from": "15-04-2026", "unskilled": "18456", "semi_skilled": "20371",
                "skilled": "22411", "highly_skilled": ""}
        self.assertRedirects(self.client.post(self.url, data), self.url, fetch_redirect_response=False)
        row = MinimumWage.objects.get()
        self.assertEqual((row.effective_from, row.unskilled, row.highly_skilled), (date(2026, 4, 1), D(18456), None))
        self.client.post(self.url, dict(data, unskilled="18500"))  # same state and month: replaced
        self.assertEqual(MinimumWage.objects.get().unskilled, D(18500))
        page = self.client.get(self.url)
        self.assertContains(page, "18,500")
        self.client.post(self.url, {"delete": row.pk})
        self.assertFalse(MinimumWage.objects.exists())

    def test_at_least_one_amount(self):
        page = self.client.post(self.url, {"state": "DL", "effective_from": "01-04-2026"})
        self.assertContains(page, "Write at least one minimum wage.")

    def test_in_force_picks_the_latest_before_the_month(self):
        MinimumWage.objects.create(state="DL", effective_from=date(2026, 4, 1), unskilled=D(18000))
        MinimumWage.objects.create(state="DL", effective_from=date(2026, 10, 1), unskilled=D(18500))
        self.assertEqual(MinimumWage.in_force("DL", date(2026, 9, 1)).unskilled, D(18000))
        self.assertEqual(MinimumWage.in_force("DL", NOV).unskilled, D(18500))
        self.assertIsNone(MinimumWage.in_force("DL", date(2026, 3, 1)))


class SheetChecksTest(DemoTestCase):
    url = reverse("companies:checks")

    def save_checks(self, **overrides):
        data = {"days_filled_on": "on", "other_reason_on": "on", "ot_limit_on": "on", "ot_limit_limit": "300",
                "paid_leave_limit_limit": "4"}
        data.update(overrides)
        return self.client.post(self.url, {k: v for k, v in data.items() if v is not None})

    def upload(self, changes):
        return self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(self.client, changes)})

    def test_page_lists_the_checks(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Stop when overtime is more than this many hours in a month")
        self.assertContains(page, "Always checked")

    def test_overtime_limit_is_a_setting(self):
        self.assertContains(self.upload({"E001": {"OT Hours": 150}}), "", status_code=302)
        self.save_checks(ot_limit_limit="100")
        self.assertContains(self.upload({"E001": {"OT Hours": 150}}), "More than 100 overtime hours")
        self.save_checks(ot_limit_on=None)
        self.assertEqual(self.upload({"E001": {"OT Hours": 150}}).status_code, 302)

    def test_bad_limit_saves_nothing(self):
        page = self.save_checks(ot_limit_limit="abc")
        self.assertContains(page, "Write a number above 0")
        self.srks.refresh_from_db()
        self.assertEqual(self.srks.sheet_checks, {})

    def test_extra_checks(self):
        self.save_checks(present_le_working_on="on", paid_leave_limit_on="on", paid_leave_limit_limit="2")
        page = self.upload({"E001": {"Days Present": 30, "Weekly Off + Holidays": 0}})
        self.assertContains(page, "more than the working days")
        page = self.upload({"E001": {"Paid Leave Days": 3, "Days Present": 22}})
        self.assertContains(page, "More than 2 days of paid leave")

    def test_minimum_wage_check(self):
        MinimumWage.objects.create(state=self.srks.state, effective_from=date(2026, 4, 1), **RATES)
        self.save_checks(min_wage_on="on")
        page = self.upload({})
        self.assertContains(page, "is below the minimum wage for the days paid")  # E005 and E006 earn less

    def test_empty_rows_can_be_allowed(self):
        page = self.upload({"E001": {"Days Present": None, "Paid Leave Days": None, "Absent / LOP Days": None}})
        self.assertContains(page, "The days are empty.")
        self.save_checks(days_filled_on=None)
        self.upload({"E001": {"Days Present": None, "Paid Leave Days": None, "Absent / LOP Days": None}})
        e1 = self.run_for().lines.get(employee__emp_code="E001")
        self.assertEqual((e1.paid_days, e1.earned_basic), (D(0), D(0)))  # overtime written in the sheet stays

    def test_below_minimum_wage_note_without_the_check(self):
        MinimumWage.objects.create(state=self.srks.state, effective_from=date(2026, 4, 1), **RATES)
        service.recompute_run(self.run_for())
        self.assertIn("BELOW_MINIMUM_WAGE", self.run_for().lines.get(employee__emp_code="E005").warnings)


class SimplerScreensTest(DemoTestCase):
    def test_month_table_has_few_columns_unless_asked(self):
        url = reverse("payroll:month", args=[NOV])
        page = self.client.get(url)
        self.assertContains(page, ">Deductions</th>")
        self.assertNotContains(page, "Employer PF+EDLI+admin")
        self.assertContains(self.client.get(url, {"full": "1"}), "Employer PF+EDLI+admin")

    def test_short_menu_with_notice_count_on_home(self):
        page = self.client.get(reverse("employees:list"))
        self.assertNotContains(page, ">Notices</a>")
        self.assertContains(page, reverse("advances:list"))
        self.assertContains(page, reverse("letters:index"))
