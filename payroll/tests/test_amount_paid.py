"""Minimum-wage split, worked out two ways: by days present (the monthly salary in the profile is paid for the
days: Basic at the minimum wage, the rest incentive) and by take-home (the take-home pay is exactly the amount the
owner pays: Basic at the minimum wage for the paid days, fixed ESI, the incentive makes up the rest). The two entry
pages with figures as you type, minimum wages, the switchable sheet checks, and inactive staff."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from companies.models import MinimumWage
from core.test_qa import same_form_back, sheet_with
from core.testing import NOV, DemoTestCase
from employees.models import Employee, SalaryRevision
from payroll import engine, service
from payroll.models import PayrollRun

RATES = dict(unskilled=D(14000), semi_skilled=D(15500), skilled=D(17000), highly_skilled=D(19000))
DEC = date(2026, 12, 1)


class SplitCase(DemoTestCase):
    """SRKS with the minimum-wage split on and Karnataka minimum wages (sample figures)."""

    def setUp(self):
        super().setUp()
        self.srks.amount_paid_mode = "GROSS"
        self.srks.save()
        MinimumWage.objects.filter(state=self.srks.state, effective_from=date(2026, 10, 1)).delete()
        MinimumWage.objects.create(state=self.srks.state, effective_from=date(2026, 4, 1), **RATES)
        service.recompute_run(self.run_for())

    def line(self, code, month=NOV):
        return self.run_for(month=month).lines.get(employee__emp_code=code)


class ByDaysTest(SplitCase):
    """No take-home written (the sheet, or the By days present page)."""

    def test_the_profile_salary_is_paid_for_the_days(self):
        e2 = self.line("E002")  # 18,000 a month, semi-skilled 15,500, 27 of 30 days paid
        self.assertEqual((e2.gross, e2.earned_basic, e2.incentive_pay), (D(16200), D(13950), D(2250)))
        self.assertEqual((e2.rate_basic, e2.rate_hra), (D(15500), D(0)))
        self.assertEqual((e2.ee_esi, e2.ee_pf, e2.net_pay), (D(105), D(1674), D(14421)))  # on Basic only
        self.assertIsNone(e2.take_home)

    def test_a_salary_below_the_minimum_wage_still_gets_it(self):
        e1 = self.line("E001")  # 12,000 + 500 raise, unskilled 14,000
        self.assertIn("SALARY_BELOW_MINIMUM_WAGE", e1.warnings)
        self.assertEqual(e1.rate_basic, D(14000))
        self.assertEqual(e1.incentive_pay, e1.incentive)  # nothing above Basic but the one-time incentive

    def test_minimum_wage_needed(self):
        MinimumWage.objects.all().delete()
        page = self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(self.client, {})})
        self.assertContains(page, "There is no minimum wage for")

    def test_switching_off_pays_the_salary_heads_again(self):
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        service.recompute_run(self.run_for())
        e2 = self.line("E002")
        self.assertEqual((e2.incentive_pay, e2.gross), (D(0), D(16200)))  # 18,000 x 27/30 from the salary heads

    def test_an_increment_raises_the_persons_own_salary(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        raised = SalaryRevision.objects.get(employee=self.employee("E001"), payroll_run=self.run_for())
        self.assertEqual(raised.gross, D(12500))  # 12,000 + 500, not the minimum wage split

    def test_new_minimum_wages_work_drafts_out_again(self):
        self.client.post(reverse("portal:minimum_wages"), dict(
            state=self.srks.state, effective_from="01-10-2026", unskilled="14000", semi_skilled="16000",
            skilled="17000", highly_skilled="19000"))
        e2 = self.line("E002")
        self.assertEqual((e2.earned_basic, e2.gross, e2.net_pay), (D(14400), D(16200), D(14364)))  # more Basic

    def test_sheet_days_are_the_days_paid(self):
        headers = [c.value for c in load_workbook(BytesIO(self.client.get(
            reverse("payroll:sheet", args=[NOV])).content)).worksheets[0][1]]
        self.assertNotIn("Amount Paid ₹", headers)
        self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(
            self.client, {"E005": {"Days Present": 21, "Absent / LOP Days": 5}})})
        e5 = self.line("E005")  # 20,000 a month, skilled 17,000
        self.assertEqual((e5.paid_days, e5.earned_basic, e5.gross, e5.net_pay),
                         (D(25), D(14167), D(16667), D(14860)))


class TakeHomePageTest(SplitCase):
    def url(self, month=NOV):
        return reverse("payroll:amounts", args=[month])

    def post(self, month=NOV, **fields):
        data = {}
        for e in service.employees_for_month(self.srks, month):
            data[f"pay_{e.pk}"] = "25000"  # above everyone's minimum
        for name, value in fields.items():
            kind, code = name.split("_", 1)
            data[f"{kind}_{self.employee(code).pk}"] = value
        return self.client.post(self.url(month), data)

    def set_up_owner_list(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        self.srks.pt_enabled = False
        self.srks.tds_enabled = False
        self.srks.esi_enabled = True
        self.srks.save()
        Employee.objects.filter(company=self.srks).update(skill_category="Unskilled", esi_override="AUTO",
                                                         pf_member=False, eps_member=False)
        Employee.objects.filter(company=self.srks, emp_code__in=["E001", "E003"]).update(pf_member=True)
        MinimumWage.objects.update(unskilled=D(20358))

    def test_the_owners_list(self):
        """A 31-day month, unskilled minimum wage Rs 20,358, fixed ESI and no PF for E002."""
        self.set_up_owner_list()
        self.assertContains(self.client.get(reverse("payroll:month", args=[DEC])), "Calculate by take-home")
        page = self.client.get(self.url(DEC))
        self.assertContains(page, "Paid days (calculated)")
        self.assertNotContains(page, 'name="days_')
        self.post(DEC, pay_E002="12000", days_E002="31")
        e2 = self.line("E002", DEC)
        self.assertEqual((e2.paid_days, e2.earned_basic, e2.ee_esi, e2.ee_pf, e2.incentive_pay, e2.net_pay),
                         (D(18), D(11821), D(158), D(0), D(337), D(12000)))
        self.assertIn("DAYS_REDUCED", e2.warnings)
        self.assertContains(self.client.get(self.url(DEC)), "Paid days set to 18 of 31")
        self.assertContains(self.client.get(reverse("payroll:month", args=[DEC])),
                            "calculated days, not an attendance record")
        self.assertEqual(self.employee("E002").structure_on(DEC).gross, D(18000))

    def test_pf_is_calculated_on_basic_and_days_fit_net_amount(self):
        self.set_up_owner_list()
        # E001 is PF-covered in the owner-list setup; its PF stays on Basic even if incentive is large.
        self.post(DEC, pay_E001="12000")
        e1 = self.line("E001", DEC)
        self.assertEqual((e1.paid_days, e1.earned_basic, e1.ee_esi, e1.ee_pf, e1.net_pay),
                         (D(21), D(13791), D(158), D(1655), D(12000)))
        self.post(DEC, pay_E001="40000")
        self.assertEqual(self.line("E001", DEC).ee_pf, D(2443))

    def test_basic_setting_never_below_the_minimum_wage(self):
        settings = self.client.get(reverse("companies:payroll"))
        self.assertNotContains(settings, "Basic per month, take-home method")
        self.assertNotContains(settings, "ESI per person, take-home method")
        self.srks.take_home_basic = D(20358)
        self.srks.save()
        Employee.objects.filter(pk=self.employee("E002").pk).update(skill_category="Semi-skilled")
        self.post(pay_E002="25000")
        e2 = self.line("E002")  # the legacy company Basic setting is ignored
        self.assertEqual((e2.paid_days, e2.earned_basic, e2.net_pay), (D(30), D(15500), D(25000)))
        self.srks.take_home_basic = D(10000)
        self.srks.save()
        self.post(pay_E002="25000")
        self.assertEqual(self.line("E002").earned_basic, D(15500))

    def test_basic_for_the_days_written(self):
        Employee.objects.filter(pk=self.employee("E005").pk).update(date_of_joining=date(2026, 11, 16))
        self.post(pay_E005="9000")
        e5 = self.line("E005")  # skilled 17,000; employed 15 of 30 days
        self.assertEqual((e5.paid_days, e5.earned_basic, e5.net_pay), (D(15), D(8500), D(9000)))

    def test_started_month_change_days_and_take_home(self):
        page = self.client.get(self.url())
        self.assertContains(page, "By take-home — November 2026")
        self.assertContains(page, "js/entry.js")  # the figures change as you type
        self.post(pay_E005="22000")
        e5 = self.line("E005")
        self.assertEqual((e5.paid_days, e5.net_pay, e5.take_home, e5.ee_esi), (D(30), D(22000), D(22000), D(158)))
        self.assertEqual(e5.earned_basic, D(17000))
        self.assertEqual(self.employee("E005").structure_on(NOV).gross, D(20000))  # monthly salary not changed

    def test_empty_esi_setting_still_uses_fixed_amount(self):
        self.srks.take_home_esi = None
        self.srks.save()
        self.post(pay_E005="22000")
        e5 = self.line("E005")
        self.assertEqual(e5.net_pay, D(22000))
        self.assertEqual(e5.ee_esi, D(158))

    def test_next_month_starts_from_last_months_amounts(self):
        self.post(pay_E005="22000")
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        self.assertContains(self.client.get(self.url(DEC)), 'name="pay_%s" value="22000"' % self.employee("E005").pk)

    def test_no_days_means_no_pay_and_typed_incentives_are_replaced(self):
        self.run_for().lines.filter(employee__emp_code="E005").update(incentive=2590)  # typed by hand earlier
        self.post(pay_E002="0", pay_E005="20000")
        e2, e5 = self.line("E002"), self.line("E005")
        self.assertEqual((e2.paid_days, e2.gross, e2.net_pay), (D(0), D(0), D(0)))
        self.assertEqual((e5.incentive, e5.net_pay), (D(0), D(20000)))

    def test_mistakes_save_nothing(self):
        page = self.post(pay_E005="abc", days_E001="1.3", days_E002="99")
        self.assertContains(page, "Write an amount in rupees")
        self.assertNotContains(page, "Use whole or half days only")
        self.assertContains(page, "Nothing was saved.")
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        page = self.post(DEC, pay_E002="")
        self.assertContains(page, "Write the take-home amount.")
        self.assertFalse(PayrollRun.objects.filter(company=self.srks, month=DEC).exists())

    def test_page_needs_the_setting_on_and_months_in_order(self):
        response = self.client.get(self.url(DEC))
        self.assertRedirects(response, reverse("payroll:month", args=[DEC]), fetch_redirect_response=False)
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        self.assertRedirects(self.client.get(self.url()), reverse("companies:payroll"),
                             fetch_redirect_response=False)

    def test_employee_forms_ask_one_monthly_salary(self):
        page = self.client.get(reverse("employees:add"))
        self.assertContains(page, "Monthly salary ₹")
        self.assertNotContains(page, 'name="hra"')
        url = reverse("employees:revise", args=[self.employee("E005").pk])
        form_page = self.client.get(url)
        self.assertContains(form_page, "Monthly salary ₹")
        data = same_form_back(form_page)
        self.assertEqual(data["basic"], "20000")
        data.update(basic="24000", reason="Yearly raise", effective_month="2026-12")
        self.client.post(url, data)
        rev = SalaryRevision.objects.filter(employee=self.employee("E005")).order_by("-id").first()
        self.assertEqual((rev.basic, rev.hra, rev.gross), (D(24000), D(0), D(24000)))


class ByDaysPageTest(SplitCase):
    def url(self, month=NOV):
        return reverse("payroll:by_days", args=[month])

    def post(self, month=NOV, **days):
        data = {f"days_{e.pk}": "" for e in service.employees_for_month(self.srks, month)}
        for code, value in days.items():
            data[f"days_{self.employee(code).pk}"] = value
        return self.client.post(self.url(month), data)

    def test_month_page_offers_both_ways(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        page = self.client.get(reverse("payroll:month", args=[DEC]))
        self.assertContains(page, "Calculate by days")
        self.assertContains(page, "Calculate by take-home")
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        page = self.client.get(reverse("payroll:month", args=[DEC]))
        self.assertContains(page, "Calculate by days")
        self.assertNotContains(page, "Calculate by take-home")

    def test_days_present_pay_the_profile_salary(self):
        page = self.client.get(self.url())
        self.assertContains(page, "By days present — November 2026")
        self.assertContains(page, "18,000")  # E002's monthly salary, from the profile
        response = self.post(E002="15")
        self.assertRedirects(response, reverse("payroll:month", args=[NOV]) + "#review",
                             fetch_redirect_response=False)
        e2 = self.line("E002")
        self.assertEqual((e2.paid_days, e2.gross, e2.earned_basic, e2.incentive_pay),
                         (D(15), D(9000), D(7750), D(1250)))
        self.assertIsNone(e2.take_home)

    def test_the_two_ways_do_not_mix(self):
        data = {}
        for e in service.employees_for_month(self.srks, NOV):
            data[f"days_{e.pk}"], data[f"pay_{e.pk}"] = "", "25000"
        self.client.post(reverse("payroll:amounts", args=[NOV]), data)
        self.assertEqual((self.line("E002").net_pay, self.line("E002").take_home), (D(25000), D(25000)))
        self.post(E002="27")  # by days present, the same days: the profile salary again
        e2 = self.line("E002")
        self.assertIsNone(e2.take_home)
        self.assertEqual((e2.gross, e2.net_pay, e2.ee_esi), (D(16200), D(14421), D(105)))

    def test_works_with_the_split_off(self):
        self.srks.amount_paid_mode = "OFF"
        self.srks.save()
        self.post(E002="15")
        e2 = self.line("E002")
        self.assertEqual((e2.gross, e2.incentive_pay), (D(9000), D(0)))


class LivePreviewTest(SplitCase):
    def preview(self, **params):
        return self.client.get(reverse("payroll:preview", args=[NOV]), params)

    def test_figures_as_typed_and_nothing_saved(self):
        e2 = self.employee("E002")
        before = self.line("E002").net_pay
        data = self.preview(method="take-home", emp=e2.pk, days="27", pay="16000").json()
        self.assertTrue(data["ok"])
        self.assertEqual((data["figures"]["net_pay"], data["figures"]["ee_esi"]), (16000, 158))
        self.assertEqual(data["figures"]["paid_days"], "30")
        self.assertEqual(data["figures"]["gross"], data["figures"]["basic"] + data["figures"]["incentive"])
        data = self.preview(method="days", emp=e2.pk, days="15").json()
        self.assertEqual((data["figures"]["gross"], data["figures"]["incentive"]), (9000, 1250))
        self.assertEqual(self.line("E002").net_pay, before)  # nothing saved

    def test_auto_days_below_full_month_and_mistakes(self):
        e2 = self.employee("E002")
        data = self.preview(method="take-home", emp=e2.pk, days="30", pay="5000").json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["figures"]["paid_days"], "11")
        data = self.preview(method="take-home", emp=e2.pk, days="1.3", pay="abc").json()
        self.assertFalse(data["ok"])
        self.assertTrue(any("amount in rupees" in e for e in data["errors"]))
        self.assertFalse(self.preview(method="days", emp=e2.pk, days="45").json()["ok"])

    def test_only_this_companys_people(self):
        self.assertEqual(self.preview(method="days", emp="999999", days="3").status_code, 404)
        self.assertEqual(self.preview(method="other", emp=self.employee("E002").pk).status_code, 404)
        stranger = Employee.objects.filter(company=self.anps).first()
        self.assertEqual(self.preview(method="days", emp=stranger.pk, days="3").status_code, 404)


class InactiveTest(DemoTestCase):
    def test_left_out_and_back_again(self):
        e5 = self.employee("E005")
        url = reverse("employees:set_active", args=[e5.pk])
        detail = reverse("employees:detail", args=[e5.pk])
        self.assertContains(self.client.get(detail), "Make inactive")
        self.client.post(url, {"active": "0", "month": "2026-11"})
        e5.refresh_from_db()
        self.assertEqual((e5.status, e5.inactive_from), ("INACTIVE", NOV))
        self.assertNotIn(e5, service.employees_for_month(self.srks, NOV))
        self.assertFalse(self.run_for().lines.filter(employee=e5).exists())  # dropped from the draft month
        self.assertEqual(service.finalise_blockers(self.run_for(), today=date(2026, 11, 30)), [])
        self.assertContains(self.client.get(detail), "Make active again")
        self.assertContains(self.client.get(reverse("employees:list"), {"status": "INACTIVE"}), detail)
        self.assertNotContains(self.client.get(reverse("employees:list")), detail)
        page = self.client.post(url, {"active": "1"}, follow=True)
        e5.refresh_from_db()
        self.assertEqual((e5.status, e5.inactive_from), ("ACTIVE", None))
        self.assertIn(e5, service.employees_for_month(self.srks, NOV))
        self.assertContains(page, "New in November 2026")  # the draft month needs their days

    def test_from_a_month_not_finalised(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        e5 = self.employee("E005")
        url = reverse("employees:set_active", args=[e5.pk])
        self.client.post(url, {"active": "0", "month": "2026-11"})
        e5.refresh_from_db()
        self.assertEqual(e5.status, "ACTIVE")
        self.client.post(url, {"active": "0", "month": "2026-12"})
        e5.refresh_from_db()
        self.assertEqual((e5.status, e5.inactive_from), ("INACTIVE", DEC))
        self.assertIn(e5, service.employees_for_month(self.srks, NOV))  # still paid in November
        self.assertNotIn(e5, service.employees_for_month(self.srks, DEC))
        self.assertEqual(self.client.get(url).status_code, 405)


class MinimumWagesPageTest(DemoTestCase):
    url = reverse("portal:minimum_wages")

    def test_add_change_and_remove(self):
        data = {"state": "DL", "effective_from": "15-04-2026", "unskilled": "18456", "semi_skilled": "20371",
                "skilled": "22411", "highly_skilled": ""}
        self.assertRedirects(self.client.post(self.url, data), self.url, fetch_redirect_response=False)
        row = MinimumWage.objects.get(state="DL", effective_from=date(2026, 4, 1))
        self.assertEqual((row.effective_from, row.unskilled, row.highly_skilled), (date(2026, 4, 1), D(18456), None))
        self.client.post(self.url, dict(data, unskilled="18500"))  # same state and month: replaced
        self.assertEqual(MinimumWage.objects.get(state="DL", effective_from=date(2026, 4, 1)).unskilled, D(18500))
        page = self.client.get(self.url)
        self.assertContains(page, "18,500")
        self.client.post(self.url, {"delete": row.pk})
        self.assertFalse(MinimumWage.objects.filter(state="DL", effective_from=date(2026, 4, 1)).exists())

    def test_at_least_one_amount(self):
        page = self.client.post(self.url, {"state": "DL", "effective_from": "01-04-2026"})
        self.assertContains(page, "Write at least one minimum wage.")

    def test_in_force_picks_the_latest_before_the_month(self):
        MinimumWage.objects.create(state="DL", effective_from=date(2026, 4, 1), unskilled=D(18000))
        MinimumWage.objects.filter(state="DL", effective_from=date(2026, 10, 1)).update(unskilled=D(18500))
        self.assertEqual(MinimumWage.in_force("DL", date(2026, 9, 1)).unskilled, D(18000))
        self.assertEqual(MinimumWage.in_force("DL", NOV).unskilled, D(18500))
        self.assertIsNone(MinimumWage.in_force("DL", date(2026, 3, 1)))

    def test_october_defaults_apply_to_every_state_and_can_be_changed(self):
        from companies.states import STATE_CHOICES

        for state, _ in STATE_CHOICES:
            wages = MinimumWage.in_force(state, date(2026, 10, 1))
            self.assertIsNotNone(wages, state)
            self.assertEqual((wages.unskilled, wages.semi_skilled, wages.skilled, wages.highly_skilled),
                             (D(20358), D(22568), D(22411), None), state)

        data = {"state": "DL", "effective_from": "01-10-2026", "unskilled": "21000", "semi_skilled": "22568",
                "skilled": "22411", "highly_skilled": ""}
        self.assertRedirects(self.client.post(self.url, data), self.url, fetch_redirect_response=False)
        self.assertEqual(MinimumWage.in_force("DL", NOV).unskilled, D(21000))
        self.assertEqual(MinimumWage.in_force("KA", NOV).unskilled, D(20358))


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
