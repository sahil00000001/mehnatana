"""Bugs found by the review of 13-11-2026, each with the check that it stays fixed."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from openpyxl import load_workbook

from core.test_qa import same_form_back
from core.testing import NOV, DemoTestCase
from employees.models import Employee
from payroll import engine, service
from payroll.models import PayrollRun
from payroll.tests.reference import ref_rates
from reports.views import _esi_rows


class TwoTabsTest(DemoTestCase):
    def test_forms_carry_the_company(self):
        page = self.client.get(reverse("companies:company"))
        self.assertContains(page, f'name="_company" value="{self.srks.pk}"')

    def test_a_form_for_another_company_saves_nothing(self):
        stale = self.srks.pk  # the page was opened while SRKS was open ...
        self.open_company(self.anps)  # ... then ANPS was opened in another tab
        with self.today_is(date(2026, 11, 30)):
            response = self.client.post(reverse("payroll:finalise", args=[NOV]), {"_company": stale}, follow=True)
        self.assertContains(response, "Nothing was saved. That page was for Shri Ram Kirana Stores")
        self.assertEqual(self.run_for(self.anps).status, PayrollRun.Status.DRAFT)
        self.client.post(reverse("companies:company"), {"_company": stale, "legal_name": "Wrong"})
        self.anps.refresh_from_db()
        self.assertNotEqual(self.anps.legal_name, "Wrong")

    def test_the_same_company_is_fine_and_all_companies_never_checks(self):
        with self.today_is(date(2026, 11, 30)):
            self.client.post(reverse("payroll:finalise", args=[NOV]), {"_company": self.srks.pk})
        self.assertEqual(self.run_for().status, PayrollRun.Status.FINALISED)
        response = self.client.post(reverse("portal:open", args=[self.anps.pk]), {"_company": 999999})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["company_id"], self.anps.pk)


class EmployeeUploadTest(DemoTestCase):
    def export(self):
        return load_workbook(BytesIO(self.client.get(reverse("employees:export")).content))

    def post(self, wb):
        out = BytesIO()
        wb.save(out)
        return self.client.post(reverse("employees:upload"), {"file": SimpleUploadedFile("staff.xlsx", out.getvalue())})

    @staticmethod
    def set_cell(wb, code, header, value):
        ws = wb.worksheets[0]
        col = [c.value for c in ws[1]].index(header) + 1
        row = next(r for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=1).value == code)
        ws.cell(row=row, column=col).value = value

    @staticmethod
    def drop_column(wb, header):
        ws = wb.worksheets[0]
        ws.delete_cols([c.value for c in ws[1]].index(header) + 1)

    def test_missing_department_and_reports_to_columns_keep_the_saved_values(self):
        before = {e.emp_code: (e.department_id, e.reports_to_id) for e in Employee.objects.filter(company=self.srks)}
        self.assertTrue(any(d for d, _r in before.values()) and any(r for _d, r in before.values()))
        wb = self.export()
        self.drop_column(wb, "Department")
        self.drop_column(wb, "Reports To (Emp Code)")
        self.post(wb)
        after = {e.emp_code: (e.department_id, e.reports_to_id) for e in Employee.objects.filter(company=self.srks)}
        self.assertEqual(before, after)

    def test_blank_tax_regime_keeps_the_old_regime_deductions(self):
        e7 = self.employee("E007")
        self.assertEqual(e7.tax_regime, "OLD")
        deductions = e7.old_regime_deductions_annual
        self.assertGreater(deductions, 0)
        wb = self.export()
        self.set_cell(wb, "E007", "Tax Regime (New/Old)", None)
        self.post(wb)
        e7.refresh_from_db()
        self.assertEqual((e7.tax_regime, e7.old_regime_deductions_annual), ("OLD", deductions))

    def test_negative_amounts_are_refused(self):
        wb = self.export()
        self.set_cell(wb, "E002", "HRA", -2000)
        self.assertContains(self.post(wb), "Cannot be a negative number.")

    def test_joining_date_cannot_move_past_a_paid_month(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        wb = self.export()
        self.set_cell(wb, "E001", "Date of Joining", "05-12-2026")
        self.assertContains(self.post(wb), "was already paid for November 2026 (finalised)")
        self.assertLess(self.employee("E001").date_of_joining, NOV)


class NoServerErrorsTest(DemoTestCase):
    def test_joining_after_the_last_working_day(self):
        Employee.objects.filter(company=self.srks, emp_code="E005").update(
            status="RELIEVED", date_of_leaving=date(2026, 11, 20), leaving_reason="Other")
        url = reverse("employees:edit", args=[self.employee("E005").pk])
        data = same_form_back(self.client.get(url))
        data["date_of_joining"] = "25-11-2026"
        page = self.client.post(url, data)
        self.assertContains(page, "The date of joining cannot be after the last working day (20-11-2026).")

    def test_relieve_someone_paid_by_bank_without_bank_details(self):
        Employee.objects.filter(company=self.srks, emp_code="E002").update(
            payment_mode="Bank", bank_name="", bank_account_no="", ifsc="")
        url = reverse("employees:relieve", args=[self.employee("E002").pk])
        data = same_form_back(self.client.get(url))
        data.update(date_of_leaving="30-11-2026", leaving_reason="Other")
        page = self.client.post(url, data)
        self.assertContains(page, "Bank name: Needed when salary is paid by bank transfer.")

    def test_impossible_month_on_reports(self):
        response = self.client.get(reverse("reports:index"), {"go": "payslips", "month": "1899-12"})
        self.assertLess(response.status_code, 500)


class LeaverTaxTest(SimpleTestCase):
    def tds(self, dol):
        emp = engine.Employee("E1", "Test", "M", date(1980, 1, 1), date(2020, 1, 1), dol)
        structure = engine.Structure(basic=D(110000))
        settings = engine.Settings(pf_enabled=False, esi_enabled=False, pt_enabled=False, tds_enabled=True)
        return engine.compute_line(2026, 11, emp, structure, structure, engine.MonthInput(present=D(30)), settings,
                                   ref_rates(), engine.YTD(gross=D(770000))).tds

    def test_tax_is_projected_only_until_the_last_working_month(self):
        self.assertLess(self.tds(date(2026, 12, 15)), self.tds(None))


class SmallFixesTest(DemoTestCase):
    def test_professional_tax_follows_a_corrected_state(self):
        self.assertEqual(self.srks.pt_state, self.srks.state)
        url = reverse("companies:company")
        data = same_form_back(self.client.get(url))
        data["state"] = "DL"
        data["pincode"] = "110001"
        self.client.post(url, data)
        self.srks.refresh_from_db()
        self.assertEqual((self.srks.state, self.srks.pt_state), ("DL", "DL"))

    def test_upi_staff_are_on_the_payment_list_not_the_bank_sheet(self):
        Employee.objects.filter(company=self.srks, emp_code="E002").update(payment_mode="UPI")
        service.recompute_run(self.run_for())
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        ws = load_workbook(BytesIO(self.client.get(reverse("reports:bank_sheet", args=[NOV])).content)).active
        self.assertNotIn("E002", [r[1] for r in ws.iter_rows(min_row=2, values_only=True)])
        self.assertContains(self.client.get(reverse("reports:cash_list", args=[NOV])), "Sunita Devi (UPI)")

    def test_new_joiner_message_says_what_to_do_once(self):
        url = reverse("employees:add")
        data = same_form_back(self.client.get(url))
        data.update(first_name="Asha", relation_name="Ravi", gender="F", date_of_birth="01-01-1995",
                    date_of_joining="10-11-2026", designation_name="Helper", basic="15000", payment_mode="Cash")
        page = self.client.post(url, data, follow=True)
        text = page.content.decode()
        self.assertIn("New in November 2026:", text)
        self.assertNotIn("Upload its sheet again", text)

    def test_esi_reason_codes_for_leavers(self):
        def line(reason):
            return SimpleNamespace(emp_snapshot={"esic_ip_no": "1", "dol": "15-11-2026", "leaving_reason": reason},
                                   paid_days=D(15), warnings=["LEFT_THIS_MONTH"], esi_wage=D(1000), name="X")

        self.assertEqual([r[4] for r in _esi_rows([line("Resigned"), line("Retired"), line("Deceased")])], [2, 3, 5])
