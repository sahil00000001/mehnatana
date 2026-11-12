"""Salary from the amount paid (minimum wage + incentive), minimum wages, and the switchable sheet checks."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from companies.models import MinimumWage
from core.test_qa import sheet_with
from core.testing import NOV, DemoTestCase
from payroll import service

RATES = dict(unskilled=D(14000), semi_skilled=D(15500), skilled=D(17000), highly_skilled=D(19000))


class AmountPaidCase(DemoTestCase):
    def setUp(self):
        super().setUp()
        self.srks.amount_paid_mode = "IN_HAND"
        self.srks.save()
        MinimumWage.objects.create(state=self.srks.state, effective_from=date(2026, 4, 1), **RATES)
        self.run_for().lines.filter(employee__emp_code="E001").update(increment=0)  # the demo has a ₹500 raise
        service.recompute_run(self.run_for())

    def amounts_url(self):
        return reverse("payroll:amounts", args=[NOV])

    def post_amounts(self, **by_code):
        lines = {l.code: l for l in self.run_for().lines.all()}
        data = {f"amount_{l.pk}": "" for l in lines.values()}
        for code, amount in by_code.items():
            data[f"amount_{lines[code].pk}"] = amount
        return self.client.post(self.amounts_url(), data)

    def line(self, code):
        return self.run_for().lines.get(employee__emp_code=code)


class FitToAmountTest(AmountPaidCase):
    def test_take_home_is_exactly_the_amount_paid(self):
        response = self.post_amounts(E001="25000", E002="18000")
        self.assertRedirects(response, self.amounts_url(), fetch_redirect_response=False)
        e1, e2 = self.line("E001"), self.line("E002")
        self.assertEqual((e1.net_pay, e2.net_pay), (D(25000), D(18000)))
        self.assertEqual(e1.rate_basic, D(14000))  # unskilled minimum wage
        self.assertEqual((e1.rate_hra, e1.rate_other), (D(0), D(0)))
        self.assertEqual(e1.earned_basic + e1.ot_pay + e1.incentive_pay, e1.gross)
        self.assertGreater(e1.incentive_pay, 0)
        self.assertEqual(e1.ee_esi, D(105))  # 0.75% of the minimum wage, not of the incentive
        self.assertEqual(e1.amount_paid_input, D(25000))
        self.assertEqual(e1.incentive, D(0))  # the input stays empty; the worked-out one is incentive_pay
        self.assertLess(e2.earned_basic, D(15500))  # semi-skilled, cut for the absent days
        self.assertEqual(self.line("E004").amount_paid_input, None)  # left empty: salary structure as before

    def test_gross_basis(self):
        self.srks.amount_paid_mode = "GROSS"
        self.srks.save()
        self.post_amounts(E001="30000")
        self.assertEqual(self.line("E001").gross, D(30000))

    def test_paid_less_than_the_minimum_wage(self):
        self.post_amounts(E003="9000")
        e3 = self.line("E003")
        self.assertEqual(e3.incentive_pay, D(0))
        self.assertGreater(e3.net_pay, D(9000))
        self.assertIn("PAID_BELOW_MINIMUM", e3.warnings)
        page = self.client.get(self.amounts_url())
        self.assertContains(page, "the owner must pay the difference")

    def test_amounts_are_kept_when_the_month_is_worked_out_again(self):
        self.post_amounts(E001="25000")
        service.recompute_run(self.run_for())
        self.assertEqual(self.line("E001").net_pay, D(25000))

    def test_switching_the_feature_off_goes_back_to_the_salary_structure(self):
        self.post_amounts(E001="25000")
        url = reverse("companies:payroll")
        from core.test_qa import same_form_back

        data = same_form_back(self.client.get(url))
        data["amount_paid_mode"] = "OFF"
        self.client.post(url, data)
        e1 = self.line("E001")
        self.assertIsNone(e1.amount_paid_input)
        self.assertNotEqual(e1.net_pay, D(25000))

    def test_new_minimum_wages_work_drafts_out_again(self):
        self.post_amounts(E001="25000")
        before = self.line("E001").earned_basic
        self.client.post(reverse("portal:minimum_wages"), {
            "state": self.srks.state, "effective_from": "01-04-2026", "unskilled": "15000", "semi_skilled": "15500",
            "skilled": "17000", "highly_skilled": "19000"})
        e1 = self.line("E001")
        self.assertEqual((before, e1.earned_basic, e1.net_pay), (D(14000), D(15000), D(25000)))


class AmountsPageTest(AmountPaidCase):
    def test_mistakes_save_nothing(self):
        page = self.post_amounts(E001="25,000.50", E002="abc")
        self.assertContains(page, "Use whole rupees, without paise.")
        self.assertContains(page, "Write an amount in rupees")
        self.assertContains(page, "Nothing was saved.")
        self.assertIsNone(self.line("E001").amount_paid_input)

    def test_incentive_and_amount_together_are_refused(self):
        line = self.line("E001")
        url = reverse("payroll:line_edit", args=[NOV, line.pk])
        from core.test_qa import same_form_back

        data = same_form_back(self.client.get(url))
        data.update(amount_paid="25000", incentive="500")
        self.assertContains(self.client.post(url, data), "Leave Incentive empty when Amount Paid is written")
        data.update(incentive="")
        self.client.post(url, data)
        self.assertEqual(self.line("E001").net_pay, D(25000))

    def test_an_increment_is_refused_and_an_incentive_replaced(self):
        self.run_for().lines.filter(employee__emp_code="E002").update(increment=700)
        self.run_for().lines.filter(employee__emp_code="E004").update(incentive=900)
        page = self.post_amounts(E002="18000", E004="90000")
        self.assertContains(page, "An increment of ₹700 is written for this month.")
        self.assertIsNone(self.line("E004").amount_paid_input)  # nothing saved
        self.run_for().lines.filter(employee__emp_code="E002").update(increment=0)
        self.post_amounts(E002="18000", E004="90000")
        e4 = self.line("E004")
        self.assertEqual((e4.incentive, e4.net_pay), (D(0), D(90000)))

    def test_no_minimum_wage_for_the_state(self):
        MinimumWage.objects.all().delete()
        page = self.post_amounts(E001="25000")
        self.assertContains(page, "There is no minimum wage for unskilled staff")
        self.assertIsNone(self.line("E001").amount_paid_input)

    def test_page_needs_the_feature_on_and_a_draft_month(self):
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        self.assertRedirects(self.client.get(self.amounts_url()), reverse("companies:payroll"),
                             fetch_redirect_response=False)
        self.srks.amount_paid_mode = "IN_HAND"
        self.srks.save()
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        self.assertRedirects(self.client.get(self.amounts_url()), reverse("payroll:month", args=[NOV]),
                             fetch_redirect_response=False)

    def test_month_page_button_and_tag(self):
        self.post_amounts(E001="25000")
        page = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertContains(page, "Salary from the amount paid")
        self.assertContains(page, "From amount paid ₹25,000")
        self.assertContains(page, "1 person is worked out from the amount paid.")


class SheetWithAmountTest(AmountPaidCase):
    def test_column_only_when_the_feature_is_on(self):
        headers = [c.value for c in load_workbook(BytesIO(self.client.get(
            reverse("payroll:sheet", args=[NOV])).content)).worksheets[0][1]]
        self.assertIn("Amount Paid ₹", headers)
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        headers = [c.value for c in load_workbook(BytesIO(self.client.get(
            reverse("payroll:sheet", args=[NOV])).content)).worksheets[0][1]]
        self.assertNotIn("Amount Paid ₹", headers)

    def test_upload_with_amount_paid(self):
        response = self.client.post(reverse("payroll:upload", args=[NOV]),
                                    {"file": sheet_with(self.client, {"E001": {"Amount Paid ₹": 26000}})})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.line("E001").net_pay, D(26000))


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
