"""Regression tests for the problems found by testing the app like a shop owner (and like an attacker)."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from advances.models import Advance
from core.testing import NOV, DemoTestCase
from employees.models import Employee, OpeningBalance
from letters.models import IssuedLetter
from payroll import engine, service
from payroll.models import PayrollRun

DEC = date(2026, 12, 1)


def same_form_back(response):
    """What a browser sends when the owner opens a form and presses Save without changing anything."""
    form = response.context["form"]
    data = {}
    for name, field in form.fields.items():
        if field.disabled:
            continue
        value = form[name].value()
        if isinstance(field.widget, forms.CheckboxInput):
            if value:
                data[name] = "on"
            continue
        if isinstance(field, forms.ChoiceField) and value in (None, "") and field.choices:
            value = next(iter(field.choices))[0]  # a drop-down sends its first option
        shown = field.widget.format_value(value)
        data[name] = "" if shown is None else shown
    return data


def sheet_with(client, changes, month=NOV):
    wb = load_workbook(BytesIO(client.get(reverse("payroll:sheet", args=[month])).content))
    ws = wb.worksheets[0]
    headers = {c.value: c.column for c in ws[1]}
    rows = {ws.cell(row=r, column=1).value: r for r in range(2, ws.max_row + 1)}
    for code, values in changes.items():
        for header, value in values.items():
            ws.cell(row=rows[code], column=headers[header]).value = value
    out = BytesIO()
    wb.save(out)
    return SimpleUploadedFile("sheet.xlsx", out.getvalue())


class FormsSaveTheirOwnValuesTest(DemoTestCase):
    """Opening a form and pressing Save must work (amounts were shown as 10000.00 and then refused)."""

    def assert_saves(self, url, extra=None):
        data = same_form_back(self.client.get(url))
        data.update(extra or {})
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.context and response.context["form"].errors)

    def test_edit_employee(self):
        self.assert_saves(reverse("employees:edit", args=[self.employee("E007").pk]))

    def test_revise_salary(self):
        self.assert_saves(reverse("employees:revise", args=[self.employee("E002").pk]), {"reason": "Same again"})

    def test_edit_salary_row(self):
        line = self.run_for().lines.get(employee__emp_code="E001")
        self.assert_saves(reverse("payroll:line_edit", args=[NOV, line.pk]))

    def test_opening_balance(self):
        self.assert_saves(reverse("companies:opening_balance_edit", args=[self.employee("E004").pk]) + "?year=2026")
        self.assertEqual(OpeningBalance.objects.get(employee__emp_code="E004", tax_year=2026).ytd_gross, D(770000))

    def test_advance_instalment(self):
        adv = Advance.objects.get(company=self.srks)
        self.assert_saves(reverse("advances:detail", args=[adv.pk]))


class TypingMistakesTest(DemoTestCase):
    """A huge number typed by mistake is refused, and the month keeps working."""

    def test_huge_incentive_in_sheet(self):
        response = self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(
            self.client, {"E001": {"Incentive / Bonus ₹": 9999999999}})})
        self.assertContains(response, "This looks too big")
        self.assertEqual(self.client.get(reverse("payroll:month", args=[NOV])).status_code, 200)

    def test_huge_ot_hours_in_sheet(self):
        response = self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(
            self.client, {"E001": {"OT Hours": 12345}})})
        self.assertContains(response, "overtime hours in a month? Please check.")

    def test_huge_amount_in_forms(self):
        line = self.run_for().lines.get(employee__emp_code="E001")
        url = reverse("payroll:line_edit", args=[NOV, line.pk])
        data = same_form_back(self.client.get(url))
        data["incentive"] = "99999999999"
        self.assertContains(self.client.post(url, data), "This looks too big")
        url = reverse("employees:revise", args=[self.employee("E002").pk])
        data = same_form_back(self.client.get(url))
        data.update(basic="999999999999", reason="typo")
        self.assertContains(self.client.post(url, data), "This looks too big")

    def test_overtime_settings_cannot_be_zero_or_negative(self):
        url = reverse("companies:payroll")
        data = same_form_back(self.client.get(url))
        data.update(ot_days_divisor="0", ot_multiplier="-2")
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.srks.refresh_from_db()
        self.assertEqual(self.srks.ot_days_divisor, 26)

    def test_other_reason_too_long(self):
        response = self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(
            self.client, {"E003": {"Other Deduction ₹": 100, "Other Deduction Reason": "x" * 300}})})
        self.assertContains(response, "at most 200 letters")


class LockedMonthsStayTrueTest(DemoTestCase):
    def test_finalise_page_shows_what_gets_locked(self):
        Employee.objects.filter(company=self.srks, emp_code="E005").update(pf_member=False)  # changed behind its back
        response = self.client.get(reverse("payroll:finalise", args=[NOV]))
        confirmed = response.context["totals"]["net_pay"]
        self.assertNotEqual(confirmed, D(272286))
        self.client.post(reverse("payroll:finalise", args=[NOV]))
        self.assertEqual(self.run_for().totals()["net_pay"], confirmed)

    def test_new_staff_must_be_in_the_sheet_before_finalising(self):
        from employees.models import SalaryRevision

        new = Employee.objects.create(company=self.srks, emp_code="E008", first_name="Lakshmi", relation_name="N",
                                      gender="F", date_of_birth=date(1998, 1, 1), date_of_joining=date(2026, 11, 20),
                                      designation=self.employee("E001").designation, payment_mode="Cash")
        SalaryRevision.objects.create(company=self.srks, employee=new, effective_from=NOV, basic=10000)
        response = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertContains(response, "These people are not in this month&#x27;s sheet yet: E008 Lakshmi")

    def test_future_month_cannot_be_finalised(self):
        self.finalise_november()
        ctx = service.RunContext(self.srks, DEC)
        run = service.save_inputs(self.srks, DEC, {e.pk: service.RowInput(present=D(27), week_off_holiday=D(4))
                                                  for e in ctx.employees}, self.srks_user)
        with self.assertRaisesMessage(service.PayrollError, "December 2026 has not started yet"):
            service.finalise(run, self.srks_user)

    def test_joining_date_cannot_move_past_a_paid_month(self):
        self.finalise_november()
        url = reverse("employees:edit", args=[self.employee("E003").pk])
        data = same_form_back(self.client.get(url))
        data["date_of_joining"] = "2027-01-15"
        self.assertContains(self.client.post(url, data), "was already paid for November 2026")

    def test_relieving_cannot_contradict_a_paid_month(self):
        self.finalise_november()
        url = reverse("employees:relieve", args=[self.employee("E003").pk])
        response = self.client.post(url, {"date_of_leaving": "2026-11-10", "leaving_reason": "Terminated"})
        self.assertContains(response, "is finalised and paid Mohd. for 30 days")
        response = self.client.post(url, {"date_of_leaving": "2026-11-30", "leaving_reason": "Terminated"})
        self.assertEqual(response.status_code, 302)

    def test_joining_in_a_finalised_month_is_explained(self):
        from employees.tests import FORM

        self.finalise_november()
        response = self.client.post(reverse("employees:add"), dict(FORM), follow=True)
        self.assertContains(response, "November 2026 is already finalised, so Lakshmi N will be paid from December 2026")


class MoneyRulesTest(DemoTestCase):
    def test_leaver_tds_settled_on_actual_pay(self):
        emp = engine.Employee("E004", "Priya", "F", date(1992, 8, 30), date(2026, 4, 1), dol=date(2026, 11, 30),
                              eps_member=False)
        s = engine.Structure(D(60000), hra=D(30000), other=D(20000))
        rates = service.rates_for_month(NOV).rates
        line = engine.compute_line(2026, 11, emp, s, s, engine.MonthInput(D(26), week_off_holiday=D(4)),
                                   engine.Settings(), rates, engine.YTD(D(770000), D(27300), D(1400)))
        self.assertEqual(line.tds, D(0))  # 8,80,000 for the year is below the tax line; 27,300 already cut

    def test_advance_not_recovered_before_it_is_given(self):
        Advance.objects.create(company=self.srks, employee=self.employee("E002"), date_given=date(2026, 12, 10),
                               amount=3000, monthly_installment=1500)
        ctx = service.RunContext(self.srks, NOV)
        self.assertEqual(ctx.suggested_advance(self.employee("E002")), D(0))
        self.assertEqual(service.RunContext(self.srks, DEC).suggested_advance(self.employee("E002")), D(1500))

    def test_future_advance_refused_in_form(self):
        response = self.client.post(reverse("advances:add"), {"employee": self.employee("E002").pk,
                                                              "date_given": "2030-01-01", "amount": "1000",
                                                              "monthly_installment": "500"})
        self.assertContains(response, "This date is in the future")

    def test_doubtful_values_get_notes(self):
        self.client.post(reverse("payroll:upload", args=[NOV]), {"file": sheet_with(self.client, {
            "E003": {"OT Hours": 120}, "E007": {"Weekly Off + Holidays": None}})})
        lines = {l.code: l for l in self.run_for().lines.all()}
        self.assertIn("OT_HOURS_HIGH", lines["E003"].warnings)
        self.assertIn("WEEK_OFF_EMPTY", lines["E007"].warnings)


class SheetsReadCarefullyTest(DemoTestCase):
    def test_row_without_code_is_pointed_out(self):
        upload = sheet_with(self.client, {})
        wb = load_workbook(BytesIO(upload.read()))
        ws = wb.worksheets[0]
        ws.append([None, "Someone", "Helper", 30, 26, 0, 4])
        out = BytesIO()
        wb.save(out)
        response = self.client.post(reverse("payroll:upload", args=[NOV]),
                                    {"file": SimpleUploadedFile("s.xlsx", out.getvalue())})
        self.assertContains(response, "This row has no Emp Code")

    def test_small_letter_codes_are_fine(self):
        wb = load_workbook(BytesIO(sheet_with(self.client, {}).read()))
        ws = wb.worksheets[0]
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=1).value = f" {ws.cell(row=r, column=1).value.lower()} "
        out = BytesIO()
        wb.save(out)
        response = self.client.post(reverse("payroll:upload", args=[NOV]),
                                    {"file": SimpleUploadedFile("s.xlsx", out.getvalue())})
        self.assertEqual(response.status_code, 302)

    def test_deleted_column_in_monthly_sheet_is_refused(self):
        wb = load_workbook(BytesIO(sheet_with(self.client, {}).read()))
        ws = wb.worksheets[0]
        col = [c.column for c in ws[1] if c.value == "Advance Deduction ₹"][0]
        ws.delete_cols(col)
        out = BytesIO()
        wb.save(out)
        response = self.client.post(reverse("payroll:upload", args=[NOV]),
                                    {"file": SimpleUploadedFile("s.xlsx", out.getvalue())})
        self.assertContains(response, "These columns are missing: Advance Deduction ₹")

    def test_employee_list_with_a_column_removed_keeps_that_detail(self):
        wb = load_workbook(BytesIO(self.client.get(reverse("employees:export")).content))
        ws = wb.worksheets[0]
        col = [c.column for c in ws[1] if c.value == "PAN"][0]
        ws.delete_cols(col)
        out = BytesIO()
        wb.save(out)
        self.client.post(reverse("employees:upload"), {"file": SimpleUploadedFile("e.xlsx", out.getvalue())})
        self.assertEqual(self.employee("E002").pan, "ABCPD1234E")

    def test_bad_values_do_not_crash(self):
        wb = load_workbook(BytesIO(self.client.get(reverse("employees:upload_sample")).content))
        ws = wb.worksheets[0]
        row = [c.value for c in ws[2]]
        row[0], row[6] = "E008", 1e15  # date of birth as a giant number
        ws.append(row)
        out = BytesIO()
        wb.save(out)
        response = self.client.post(reverse("employees:upload"), {"file": SimpleUploadedFile("e.xlsx", out.getvalue())})
        self.assertContains(response, "is not a date")

    def test_excel_downloads_never_contain_formulas(self):
        Employee.objects.filter(company=self.srks, emp_code="E001").update(first_name='=HYPERLINK("http://x","y")')
        wb = load_workbook(BytesIO(self.client.get(reverse("employees:export")).content))
        cell = wb.worksheets[0]["B2"]
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(str(cell.value).startswith("=HYPERLINK"))


class OddRequestsTest(DemoTestCase):
    def test_bad_urls_do_not_crash(self):
        for url in ["/reports/?go=tds&tax_year=99999", "/reports/tds/9999-00/", "/salary/0000-01/",
                    "/employees/?dept=99999999999999999999999"]:
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (200, 302, 404))
        response = self.client.post(reverse("companies:opening_balance_edit", args=[self.employee("E004").pk])
                                    + "?year=-5", {"ytd_gross": "1"})
        self.assertIn(response.status_code, (200, 302))

    def test_letter_saved_once_on_double_click(self):
        url = reverse("letters:new", args=["appointment"])
        data = {"employee": self.employee("E002").pk, "date": "2026-11-20", "action": "save", "body": ""}
        self.client.post(url, data)
        self.client.post(url, data)
        self.assertEqual(IssuedLetter.objects.filter(company=self.srks).count(), 1)

    def test_no_pf_buttons_for_a_shop_without_pf(self):
        self.client.force_login(self.anps_user)
        self.finalise_november(self.anps)
        page = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertNotContains(page, "PF ECR file")
        self.assertContains(page, "ESI upload file")

    def test_reopen_link_only_for_latest_month(self):
        self.finalise_november()
        self.assertContains(self.client.get(reverse("payroll:month", args=[NOV])), "Reopen this month")

    def test_logo_saved_by_what_it_really_is(self):
        url = reverse("companies:company")
        data = same_form_back(self.client.get(url))
        data.pop("logo", None)
        data["logo"] = SimpleUploadedFile("logo.html", b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        self.client.post(url, data)
        self.srks.refresh_from_db()
        self.assertTrue(self.srks.logo.name.endswith(".png"))

    def test_mobile_with_country_code(self):
        url = reverse("employees:edit", args=[self.employee("E002").pk])
        data = same_form_back(self.client.get(url))
        data["mobile"] = "+91 98450 12345"
        self.client.post(url, data)
        self.assertEqual(self.employee("E002").mobile, "9845012345")

    def test_devanagari_name_as_per_uan(self):
        self.assertEqual(Employee.uan_name_from("रमेश कुमार"), "रमेश कुमार")

    def test_owner_login_when_usernames_are_taken(self):
        from accounts.models import User
        from companies.admin import CompanyAdmin
        from companies.models import Company
        from django.contrib import admin as django_admin
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        shop = Company.objects.create(code="TAKEN", legal_name="T", trade_name="T", address_line1="1", city="P",
                                      state="MH", pincode="411001", phone="9876500001")
        User.objects.create_user(username="taken", password="x-pass-123")
        request = RequestFactory().post("/admin/")
        request.user = User.objects.create_superuser("platform", password="admin-pass-1")
        request.session = {}
        request._messages = FallbackStorage(request)
        CompanyAdmin(Company, django_admin.site).create_owner_login(request, Company.objects.filter(pk=shop.pk))
        self.assertEqual(shop.users.get().username, "taken2")
