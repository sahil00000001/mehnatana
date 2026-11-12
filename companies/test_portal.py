"""One login, many companies: All companies, Add a company, Open, Switch off, and each screen on one company."""
from django.urls import reverse

from core.tenancy import SESSION_COMPANY
from core.testing import NOV, DemoTestCase
from employees.models import Employee

from .models import Company

NEW_COMPANY = {
    "code": "srvs", "trade_name": "Sri Venkatesh Stores", "legal_name": "Sri Venkatesh Stores LLP",
    "address_line1": "4, Gandhi Bazaar", "city": "Bengaluru", "state": "KA", "pincode": "560004",
    "payroll_start_month": "01-12-2026", "weekly_off_days": ["6"], "salary_divisor": "WORKING",
    "pf_enabled": "on", "pt_enabled": "on", "tds_enabled": "on",
}


class AllCompaniesTest(DemoTestCase):
    def test_lists_every_company_with_open_buttons(self):
        page = self.client.get(reverse("portal:list"))
        self.assertContains(page, "Shri Ram Kirana Stores")
        self.assertContains(page, "Annapurna Sweets")
        self.assertContains(page, reverse("portal:open", args=[self.anps.pk]))
        self.assertContains(page, "Six days a week, Monday to Saturday (Sunday off)")
        self.assertContains(page, "Open now")  # SRKS is open

    def test_open_switches_every_screen_to_that_company(self):
        response = self.client.post(reverse("portal:open", args=[self.anps.pk]))
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        employees = self.client.get(reverse("employees:list"))
        self.assertContains(employees, "Seema Patil")
        self.assertNotContains(employees, "Sunita Devi")
        self.assertContains(employees, '<span class="company-name">Annapurna Sweets</span>')
        # a link to the other company's row is not found
        srks_emp = Employee.objects.get(company=self.srks, emp_code="E002")
        self.assertEqual(self.client.get(reverse("employees:detail", args=[srks_emp.pk])).status_code, 404)

    def test_no_company_open_goes_to_all_companies_and_back(self):
        session = self.client.session
        del session[SESSION_COMPANY]
        session.save()
        self.assertRedirects(self.client.get(reverse("home")), reverse("portal:list"), fetch_redirect_response=False)
        response = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertRedirects(response, reverse("portal:list") + "?next=%2Fsalary%2F2026-11%2F",
                             fetch_redirect_response=False)
        page = self.client.get(response["Location"])
        self.assertContains(page, "Choose the company to work on first.")
        self.assertContains(page, 'name="next" value="/salary/2026-11/"')
        opened = self.client.post(reverse("portal:open", args=[self.srks.pk]), {"next": "/salary/2026-11/"})
        self.assertRedirects(opened, "/salary/2026-11/", fetch_redirect_response=False)
        evil = self.client.post(reverse("portal:open", args=[self.srks.pk]), {"next": "https://example.com/"})
        self.assertRedirects(evil, reverse("home"), fetch_redirect_response=False)

    def test_open_needs_post(self):
        self.assertEqual(self.client.get(reverse("portal:open", args=[self.anps.pk])).status_code, 405)

    def test_add_a_company_opens_it(self):
        page = self.client.get(reverse("portal:add"))
        self.assertContains(page, 'value="6" class="week-days" id="id_weekly_off_days_6" checked')
        response = self.client.post(reverse("portal:add"), NEW_COMPANY)
        self.assertRedirects(response, reverse("employees:list"), fetch_redirect_response=False)
        company = Company.objects.get(code="SRVS")
        self.assertEqual((company.weekly_off, company.salary_divisor, company.pt_state), ((6,), "WORKING", "KA"))
        self.assertEqual(self.client.session[SESSION_COMPANY], company.pk)
        self.assertContains(self.client.get(reverse("employees:list")), "Sri Venkatesh Stores")

    def test_add_refuses_a_used_code_and_a_week_without_working_days(self):
        data = dict(NEW_COMPANY, code="SRKS", weekly_off_days=[str(d) for d in range(7)])
        page = self.client.post(reverse("portal:add"), data)
        self.assertContains(page, "At least one day of the week must be a working day.")
        self.assertContains(page, "already exists")
        self.assertEqual(Company.objects.count(), 2)

    def test_switch_off_and_on(self):
        self.client.post(reverse("portal:switch", args=[self.srks.pk]))
        self.srks.refresh_from_db()
        self.assertFalse(self.srks.is_active)
        self.assertNotIn(SESSION_COMPANY, self.client.session)  # it was open: now nothing is open
        page = self.client.get(reverse("portal:list"))
        self.assertContains(page, "Switched off")
        response = self.client.post(reverse("portal:open", args=[self.srks.pk]), follow=True)
        self.assertContains(response, "is switched off. Switch it on first.")
        self.client.post(reverse("portal:switch", args=[self.srks.pk]))
        self.srks.refresh_from_db()
        self.assertTrue(self.srks.is_active)

    def test_switched_off_company_is_closed_on_the_next_page(self):
        Company.objects.filter(pk=self.srks.pk).update(is_active=False)
        self.assertRedirects(self.client.get(reverse("employees:list")),
                             reverse("portal:list") + "?next=%2Femployees%2F", fetch_redirect_response=False)

    def test_header_and_menu(self):
        home = self.client.get(reverse("home"))
        self.assertContains(home, "Change company")
        self.assertContains(home, ">All companies</a>")
        self.assertContains(home, "<title>Home — PayCore</title>")
        session = self.client.session
        del session[SESSION_COMPANY]
        session.save()
        portal = self.client.get(reverse("portal:list"))
        self.assertContains(portal, ">Add a company</a>")
        self.assertNotContains(portal, ">Monthly Salary</a>")

    def test_help_and_password_work_without_a_company(self):
        session = self.client.session
        del session[SESSION_COMPANY]
        session.save()
        self.assertEqual(self.client.get(reverse("help")).status_code, 200)
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 200)


class WorkingWeekSettingsTest(DemoTestCase):
    def test_payroll_rules_save_weekly_offs(self):
        from core.test_qa import same_form_back

        url = reverse("companies:payroll")
        data = same_form_back(self.client.get(url))
        data["weekly_off_days"] = ["5", "6"]
        data["salary_divisor"] = "WORKING"
        self.client.post(url, data)
        self.srks.refresh_from_db()
        self.assertEqual((self.srks.weekly_off_days, self.srks.salary_divisor), ("56", "WORKING"))
        self.assertEqual(self.srks.working_week_label,
                         "Five days a week, Monday to Friday (Saturday and Sunday off)")
