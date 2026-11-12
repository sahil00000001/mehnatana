"""Isolation (README §2.1, §17.3) and a smoke test that every screen opens."""
from datetime import date
from decimal import Decimal as D

from django.urls import reverse

from advances.models import Advance
from core.testing import NOV, DemoTestCase
from employees.models import Department, Designation, Employee
from payroll.models import PayrollRun


class IsolationTest(DemoTestCase):
    """Logged in as the SRKS owner, every URL pointing at ANPS data gives 404."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.seema = Employee.objects.get(company=cls.anps, emp_code="A001")
        cls.anps_advance = Advance.objects.create(company=cls.anps, employee=cls.seema, date_given=date(2026, 11, 2),
                                                  amount=3000, monthly_installment=1000)
        cls.anps_run = PayrollRun.objects.get(company=cls.anps, month=NOV)
        cls.anps_line = cls.anps_run.lines.get(employee=cls.seema)

    def anps_urls(self):
        e, line = self.seema.pk, self.anps_line.pk
        return [
            reverse("employees:detail", args=[e]), reverse("employees:edit", args=[e]),
            reverse("employees:relieve", args=[e]), reverse("employees:undo_relieve", args=[e]),
            reverse("employees:revise", args=[e]),
            reverse("employees:department_edit", args=[Department.objects.get(company=self.anps, name="Kitchen").pk]),
            reverse("employees:designation_edit", args=[Designation.objects.get(company=self.anps, name="Sweet Maker").pk]),
            reverse("advances:detail", args=[self.anps_advance.pk]),
            reverse("advances:add") + f"?employee={e}",
            reverse("payroll:line_edit", args=[NOV, line]),
            reverse("companies:opening_balance_edit", args=[e]),
            reverse("files_upload", args=[self.anps_run.pk]),
            reverse("reports:payslip", args=[NOV, line]),
            reverse("letters:new", args=["appointment"]) + f"?employee={e}",
        ]

    def test_get_gives_404(self):
        for url in self.anps_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_post_gives_404_and_changes_nothing(self):
        for url in self.anps_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"first_name": "Hacked", "present": "1", "employee": self.seema.pk}).status_code, 404)
        self.seema.refresh_from_db()
        self.assertEqual(self.seema.first_name, "Seema")
        self.assertEqual(self.anps_advance.monthly_installment, D(1000))

    def test_lists_never_show_other_company(self):
        for url in [reverse("employees:list") + "?status=ALL", reverse("employees:list") + "?q=Seema",
                    reverse("advances:list") + "?status=ALL", reverse("payroll:month", args=[NOV]),
                    reverse("employees:organisation"), reverse("companies:opening_balances"),
                    reverse("letters:index"), reverse("home"), reverse("employees:export")]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"Seema Patil", response.content)
                self.assertNotIn(b"Annapurna", response.content)

    def test_form_choices_only_own_company(self):
        form = self.client.get(reverse("employees:add")).context["form"]
        self.assertFalse(form.fields["reports_to"].queryset.filter(company=self.anps).exists())
        self.assertNotIn("Sweet Maker", form.fields["designation_name"].widget.options)
        self.assertIn("Cashier", form.fields["designation_name"].widget.options)
        response = self.client.get(reverse("advances:add"))
        self.assertFalse(response.context["form"].fields["employee"].queryset.filter(company=self.anps).exists())

    def test_other_company_sheet_rejected(self):
        self.open_company(self.anps)
        anps_sheet = self.client.get(reverse("payroll:sheet", args=[NOV])).content
        self.open_company(self.srks)
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(reverse("payroll:upload", args=[NOV]),
                                    {"file": SimpleUploadedFile("a.xlsx", anps_sheet)})
        self.assertContains(response, "belongs to company ANPS")


class SmokeTest(DemoTestCase):
    """Every owner screen opens."""

    def test_pages_open(self):
        e = self.employee("E002").pk
        line = self.run_for().lines.get(employee__emp_code="E002").pk
        urls = [
            "home", "employees:list", "employees:add", "employees:upload", "employees:organisation",
            "employees:departments", "employees:designations", "payroll:months", "advances:list", "advances:add",
            "letters:index", "reports:index", "companies:index", "companies:company", "companies:payroll",
            "companies:opening_balances", "help", "password_change",
        ]
        for name in urls:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for url in [reverse("employees:detail", args=[e]), reverse("employees:edit", args=[e]),
                    reverse("employees:revise", args=[e]), reverse("employees:relieve", args=[e]),
                    reverse("payroll:month", args=[NOV]), reverse("payroll:line_edit", args=[NOV, line]),
                    reverse("payroll:finalise", args=[NOV]), reverse("payroll:month", args=[date(2026, 12, 1)])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_downloads(self):
        for url in [reverse("employees:upload_sample"), reverse("employees:export"),
                    reverse("payroll:sheet", args=[NOV]), reverse("payroll:sheet", args=[NOV]) + "?sample=1",
                    reverse("payroll:export", args=[NOV]), reverse("companies:opening_sample")]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response["Content-Type"].startswith("application/vnd.openxmlformats"))

    def test_month_before_start_is_404(self):
        self.assertEqual(self.client.get(reverse("payroll:month", args=[date(2026, 10, 1)])).status_code, 404)

    def test_menu_marks_current_page(self):
        response = self.client.get(reverse("employees:list"))
        self.assertContains(response, 'class="current" aria-current="page">Employees</a>')


class AdvanceTest(DemoTestCase):
    def test_give_advance_and_change_instalment(self):
        e = self.employee("E002")
        response = self.client.post(reverse("advances:add"), {"employee": e.pk, "date_given": "2026-11-03",
                                                              "amount": "3000", "monthly_installment": "500",
                                                              "note": ""})
        adv = Advance.objects.get(company=self.srks, employee=e)
        self.assertRedirects(response, reverse("advances:detail", args=[adv.pk]), fetch_redirect_response=False)
        self.client.post(reverse("advances:detail", args=[adv.pk]), {"monthly_installment": "750"})
        adv.refresh_from_db()
        self.assertEqual(adv.monthly_installment, D(750))
        response = self.client.post(reverse("advances:detail", args=[adv.pk]), {"monthly_installment": "9000"})
        self.assertContains(response, "cannot be more than what is still to be recovered")

    def test_instalment_more_than_amount(self):
        response = self.client.post(reverse("advances:add"), {"employee": self.employee("E002").pk,
                                                              "date_given": "03-11-2026", "amount": "1000",
                                                              "monthly_installment": "2000"})
        self.assertContains(response, "cannot be more than the advance")

    def test_fix_filter_no_instalment(self):
        Advance.objects.create(company=self.srks, employee=self.employee("E003"), date_given=date(2026, 11, 1),
                               amount=2000, monthly_installment=0)
        response = self.client.get(reverse("advances:list"), {"fix": "no_instalment"})
        self.assertContains(response, "Mohd. Arif")
        self.assertNotContains(response, "Ramesh Kumar")


class SettingsTest(DemoTestCase):
    def test_payroll_rules_recalculate_drafts(self):
        form = self.client.get(reverse("companies:payroll")).context["form"]
        data = {name: form[name].value() for name in form.fields if not form.fields[name].disabled}
        data = {k: ("on" if v is True else "" if v is None or v is False else v) for k, v in data.items()}
        data["salary_divisor"] = "FIXED_26"
        response = self.client.post(reverse("companies:payroll"), data)
        self.assertEqual(response.status_code, 302, response.context and response.context["form"].errors)
        line = self.run_for().lines.get(employee__emp_code="E002")
        self.assertEqual(line.net_pay, D(14795))  # README §17.2 FIXED_26 case

    def test_start_month_locked_after_first_upload(self):
        form = self.client.get(reverse("companies:payroll")).context["form"]
        self.assertTrue(form.fields["payroll_start_month"].disabled)

    def test_company_details(self):
        form = self.client.get(reverse("companies:company")).context["form"]
        data = {name: form[name].value() or "" for name in form.fields if name != "logo"}
        data["signatory_name"] = "R. P. Gupta"
        self.client.post(reverse("companies:company"), data)
        self.srks.refresh_from_db()
        self.assertEqual(self.srks.signatory_name, "R. P. Gupta")

    def test_bad_logo_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        form = self.client.get(reverse("companies:company")).context["form"]
        data = {name: form[name].value() or "" for name in form.fields if name != "logo"}
        data["logo"] = SimpleUploadedFile("logo.png", b"not a picture")
        response = self.client.post(reverse("companies:company"), data)
        self.assertContains(response, "must be a PNG or JPG picture")

    def test_opening_balance_changes_tds(self):
        e = self.employee("E003")
        self.client.post(reverse("companies:opening_balance_edit", args=[e.pk]) + "?year=2026",
                         {"ytd_gross": "1500000", "ytd_tds": "0", "ytd_pt": "1400"})
        line = self.run_for().lines.get(employee__emp_code="E003")
        self.assertGreater(line.tds, 0)
