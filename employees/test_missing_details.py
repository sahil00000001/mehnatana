"""People brought over from an older payroll system without a date of birth or father's / spouse's name."""
from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase
from django.urls import reverse

from core.test_qa import same_form_back
from core.testing import NOV, DemoTestCase
from payroll import engine
from payroll.tests.reference import ref_rates
from reports.fixes import fix_querysets

from .models import Employee
from .rules import default_eps_member


class EngineWithoutBirthDateTest(SimpleTestCase):
    def line(self, dob):
        emp = engine.Employee("E1", "Test", "M", dob, date(2020, 1, 1), None, eps_member=True)
        structure = engine.Structure(basic=D(15000))
        settings = engine.Settings(pf_enabled=True, esi_enabled=False, pt_enabled=False, tds_enabled=True)
        return engine.compute_line(2026, 11, emp, structure, structure, engine.MonthInput(present=D(30)),
                                   settings, ref_rates(), engine.YTD())

    def test_pension_follows_the_tick_and_tax_uses_normal_slabs(self):
        unknown, young = self.line(None), self.line(date(1990, 1, 1))
        self.assertEqual(unknown.eps_wage, D(15000))
        self.assertNotIn("EPS_STOPPED_AGE_58", unknown.warnings)
        self.assertEqual((unknown.net_pay, unknown.tds), (young.net_pay, young.tds))


class MissingDetailsTest(DemoTestCase):
    def setUp(self):
        super().setUp()
        Employee.objects.filter(company=self.srks, emp_code="E002").update(date_of_birth=None)
        Employee.objects.filter(company=self.srks, emp_code="E003").update(relation_name="")

    def test_listed_under_things_to_fix_and_salary_still_works(self):
        fixes = fix_querysets(self.srks, today=date(2026, 11, 5))
        self.assertEqual(list(fixes["missing_details"][1].values_list("emp_code", flat=True)), ["E002", "E003"])
        self.assertNotIn(self.employee("E002").pk, fixes["turning_58"][1].values_list("pk", flat=True))
        listed = self.client.get(reverse("employees:list"), {"fix": "missing_details"})
        self.assertContains(listed, "Staff without date of birth or father")
        self.assertEqual(self.client.get(reverse("payroll:month", args=[NOV])).status_code, 200)

    def test_pension_rule_without_birth_date(self):
        self.assertTrue(default_eps_member(None, date(2026, 11, 1), engine.Structure(basic=D(15000))))

    def test_employee_page_asks_for_them(self):
        page = self.client.get(reverse("employees:detail", args=[self.employee("E002").pk]))
        self.assertContains(page, "Not given yet — please add it (Edit)")

    def test_the_form_still_asks_for_both(self):
        url = reverse("employees:edit", args=[self.employee("E002").pk])
        data = same_form_back(self.client.get(url))
        self.assertEqual(data["date_of_birth"], "")
        page = self.client.post(url, dict(data, relation_name=""))
        self.assertEqual(page.status_code, 200)
        form = page.context["form"]
        self.assertIn("date_of_birth", form.errors)
        self.assertIn("relation_name", form.errors)
        self.client.post(url, dict(data, date_of_birth="10-02-1990"))
        self.assertEqual(self.employee("E002").date_of_birth, date(1990, 2, 10))
