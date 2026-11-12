"""Notice board: due dates with days left or late, months not finalised, staff leaving, probation ending."""
from datetime import date
from unittest import mock

from django.urls import reverse

from core.notices import needs_attention, notices_for
from core.testing import DemoTestCase


class NoticesTest(DemoTestCase):
    def test_pf_due_soon_shows_everywhere(self):
        self.finalise_november()
        with self.today_is(date(2026, 12, 13)):
            items = needs_attention(notices_for(self.srks))
            texts = [f"{n.text} — {n.detail}" for n in items]
            self.assertIn("Deposit PF for November 2026 — due in 2 days", texts)
            page = self.client.get(reverse("employees:list"))
        self.assertContains(page, 'class="notice-strip urgent"')
        self.assertContains(page, "See all notices")
        self.assertContains(page, '<span class="count"')

    def test_late_payment_is_marked_late(self):
        with self.today_is(date(2026, 12, 9)):
            items = notices_for(self.srks)
        salary = next(n for n in items if n.text == "Pay November 2026 salary")
        self.assertEqual((salary.level, salary.detail), ("urgent", "2 days late"))

    def test_month_not_finalised_after_it_ends(self):
        with self.today_is(date(2026, 12, 3)):
            items = needs_attention(notices_for(self.srks))
        self.assertTrue(any(n.text == "November 2026 salary is not finalised yet" for n in items))

    def test_quiet_when_nothing_is_due(self):
        page = self.client.get(reverse("employees:list"))  # 5 November: dues are a month away
        self.assertNotContains(page, "notice-strip")
        self.assertNotContains(page, '<span class="count"')

    def test_staff_leaving_and_probation(self):
        from employees.models import Employee

        Employee.objects.filter(company=self.srks, emp_code="E005").update(
            status="RELIEVED", date_of_leaving=date(2026, 11, 12), leaving_reason="Retired")
        Employee.objects.filter(company=self.srks, emp_code="E006").update(employment_type="Probation")
        self.srks.probation_months = 1
        self.srks.save()
        texts = [n.text for n in notices_for(self.srks)]
        self.assertIn("Gopal Rao's last working day is 12-11-2026", texts)
        self.assertNotIn("Kiran B's probation ends on 16-12-2026", texts)  # more than 30 days away
        with self.today_is(date(2026, 12, 1)):
            texts = [n.text for n in notices_for(self.srks)]
        self.assertIn("Kiran B's probation ends on 16-12-2026", texts)

    def test_notices_page_lists_everything(self):
        response = self.client.get(reverse("notices"))
        self.assertContains(response, "Pay November 2026 salary")
        self.assertContains(response, "Things to fix in your records")
        self.assertContains(response, "Active staff with no appointment letter issued (7)")
        home = self.client.get(reverse("home"))
        self.assertContains(home, "Notice board")
        self.assertContains(home, "Deposit PF for November 2026")

    def test_a_notice_problem_never_breaks_a_page(self):
        with mock.patch("core.notices.notices_for", side_effect=RuntimeError("boom")):
            with self.assertLogs("core.context_processors", level="ERROR"):
                response = self.client.get(reverse("employees:list"))
        self.assertEqual(response.status_code, 200)

    def test_menu_and_breadcrumbs(self):
        with self.today_is(date(2026, 11, 5)):
            response = self.client.get(reverse("employees:detail", args=[self.employee("E002").pk]))
        self.assertContains(response, 'class="crumbs no-print"')
        self.assertContains(response, ">Employees</a> › <span>Sunita Devi</span>")
        self.assertContains(response, "Thursday, 5 November 2026")
        self.assertContains(response, '</a> <span class="sep" aria-hidden="true">·</span> <a')  # menu can wrap
