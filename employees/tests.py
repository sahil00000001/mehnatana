from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from core.models import AuditLog
from core.testing import NOV, DemoTestCase

from .models import Employee, SalaryRevision

FORM = {
    "emp_code": "E008", "first_name": "Lakshmi", "surname": "N", "relation_type": "Father",
    "relation_name": "Narayan", "gender": "F", "date_of_birth": "1998-02-10", "mobile": "98450 11111", "email": "",
    "address": "Jayanagar", "date_of_joining": "2026-11-20", "department_name": "", "designation_name": "Packer",
    "reports_to": "", "employment_type": "Permanent", "skill_category": "Unskilled", "nature_of_duties": "Packing",
    "basic": "11000", "da": "", "hra": "3000", "conveyance": "", "other_allowance": "", "pf_member": "on",
    "eps_choice": "AUTO", "uan": "", "name_as_per_uan": "", "esic_ip_no": "", "esi_override": "AUTO",
    "tax_regime": "NEW", "old_regime_deductions_annual": "", "payment_mode": "Cash", "bank_name": "",
    "bank_account_no": "", "ifsc": "", "pan": "abcpl 1234 q", "aadhaar": "", "aadhaar_consent": "",
}


class EmployeeFormTest(DemoTestCase):
    def form_data(self, **changes):
        data = dict(FORM)
        data.update(changes)
        return data

    def test_add_employee_creates_first_salary_structure(self):
        response = self.client.post(reverse("employees:add"), self.form_data())
        emp = Employee.objects.get(company=self.srks, emp_code="E008")
        self.assertRedirects(response, reverse("employees:detail", args=[emp.pk]))
        self.assertEqual(emp.pan, "ABCPL1234Q")
        self.assertEqual(emp.mobile, "9845011111")
        self.assertTrue(emp.eps_member)  # code wages 11,000 within the ceiling, age under 58
        rev = emp.revisions.get()
        self.assertEqual((rev.effective_from, rev.basic, rev.hra), (NOV, D(11000), D(3000)))
        self.assertTrue(AuditLog.objects.filter(action="employee.create", object_id=str(emp.pk)).exists())

    def test_next_code_suggested(self):
        response = self.client.get(reverse("employees:add"))
        self.assertContains(response, 'value="E008"')

    def test_bank_needs_details(self):
        response = self.client.post(reverse("employees:add"), self.form_data(payment_mode="Bank"))
        self.assertContains(response, "Needed when salary is paid by bank transfer.")
        self.assertFalse(Employee.objects.filter(company=self.srks, emp_code="E008").exists())

    def test_aadhaar_needs_consent_and_is_masked(self):
        response = self.client.post(reverse("employees:add"), self.form_data(aadhaar="1234 5678 9012"))
        self.assertContains(response, "Tick the consent box")
        self.client.post(reverse("employees:add"), self.form_data(aadhaar="1234 5678 9012", aadhaar_consent="on"))
        emp = Employee.objects.get(company=self.srks, emp_code="E008")
        self.assertEqual(emp.aadhaar, "123456789012")
        page = self.client.get(reverse("employees:edit", args=[emp.pk]))
        self.assertNotContains(page, "123456789012")
        self.assertContains(page, "XXXX XXXX 9012")

    def test_hierarchy_loop_refused(self):
        arif, ramesh = self.employee("E003"), self.employee("E001")
        response = self.client.get(reverse("employees:edit", args=[arif.pk]))
        form = response.context["form"]
        data = {name: form[name].value() for name in form.fields}
        data = {k: ("on" if v is True else "" if v is None or v is False else v) for k, v in data.items()}
        data["reports_to"] = str(ramesh.pk)
        response = self.client.post(reverse("employees:edit", args=[arif.pk]), data)
        self.assertContains(response, "cannot report to someone who reports to him")
        arif.refresh_from_db()
        self.assertIsNone(arif.reports_to)

    def test_code_locked_after_finalising(self):
        self.finalise_november()
        response = self.client.get(reverse("employees:edit", args=[self.employee("E002").pk]))
        self.assertTrue(response.context["form"].fields["emp_code"].disabled)



class RelieveReviseTest(DemoTestCase):
    def test_relieve_and_undo(self):
        emp = self.employee("E005")
        response = self.client.post(reverse("employees:relieve", args=[emp.pk]), {
            "date_of_leaving": "2026-11-25", "leaving_reason": "Retired", "resignation_date": "", "exit_remarks": ""})
        self.assertRedirects(response, reverse("employees:detail", args=[emp.pk]), fetch_redirect_response=False)
        emp.refresh_from_db()
        self.assertEqual(emp.status, "RELIEVED")
        follow = self.client.get(reverse("employees:detail", args=[emp.pk]))
        self.assertContains(follow, "two working days")
        self.client.post(reverse("employees:undo_relieve", args=[emp.pk]))
        emp.refresh_from_db()
        self.assertEqual(emp.status, "ACTIVE")
        self.assertIsNone(emp.date_of_leaving)

    def test_resigned_needs_resignation_date(self):
        emp = self.employee("E005")
        response = self.client.post(reverse("employees:relieve", args=[emp.pk]), {
            "date_of_leaving": "2026-11-25", "leaving_reason": "Resigned"})
        self.assertContains(response, "Enter the date of the resignation letter.")

    def test_undo_blocked_after_a_later_finalised_month(self):
        from payroll import service

        emp = self.employee("E005")
        emp.status, emp.date_of_leaving, emp.leaving_reason = "RELIEVED", date(2026, 11, 30), "Retired"
        emp.save()
        self.finalise_november()
        dec = date(2026, 12, 1)
        ctx = service.RunContext(self.srks, dec)
        rows = {e.pk: service.RowInput(present=D(27), week_off_holiday=D(4)) for e in ctx.employees}
        with self.today_is(date(2027, 1, 5)):
            service.finalise(service.save_inputs(self.srks, dec, rows, self.srks_user), self.srks_user)
        response = self.client.post(reverse("employees:undo_relieve", args=[emp.pk]))
        self.assertContains(response, "This cannot be undone")
        emp.refresh_from_db()
        self.assertEqual(emp.status, "RELIEVED")

    def test_revise_salary(self):
        emp = self.employee("E002")
        response = self.client.get(reverse("employees:revise", args=[emp.pk]))
        self.assertContains(response, "November 2026")
        response = self.client.post(reverse("employees:revise", args=[emp.pk]), {
            "effective_month": "2026-12", "basic": "10000", "da": "0", "hra": "6000", "conveyance": "2000",
            "other_allowance": "1000", "reason": "Yearly increment"})
        self.assertRedirects(response, reverse("employees:detail", args=[emp.pk]), fetch_redirect_response=False)
        page = self.client.get(reverse("employees:detail", args=[emp.pk]))
        self.assertContains(page, "₹18,000 → ₹19,000 (+₹1,000)")
        self.assertEqual(emp.structure_on(date(2026, 12, 1)).basic, D(10000))
        self.assertEqual(emp.structure_on(NOV).basic, D(9000))

    def test_finalised_month_not_offered_for_revision(self):
        self.finalise_november()
        response = self.client.get(reverse("employees:revise", args=[self.employee("E002").pk]))
        choices = [c[0] for c in response.context["form"].fields["effective_month"].choices]
        self.assertNotIn("2026-11", choices)
        self.assertEqual(choices[0], "2026-12")


class UploadTest(DemoTestCase):
    def sample(self):
        response = self.client.get(reverse("employees:upload_sample"))
        self.assertEqual(response.status_code, 200)
        return load_workbook(BytesIO(response.content))

    def post(self, wb):
        out = BytesIO()
        wb.save(out)
        return self.client.post(reverse("employees:upload"), {"file": SimpleUploadedFile("staff.xlsx", out.getvalue())})

    def test_add_and_update_from_excel(self):
        wb = self.sample()
        ws = wb.worksheets[0]
        example = [c.value for c in ws[2]]
        self.assertEqual(example[0], "EXAMPLE")
        new = list(example)
        new[0], new[1], new[13] = "E008", "Mahesh", "E003"
        new[17], new[20], new[21] = "ABCPM1234Z", "101234567808", "1234567808"  # his own PAN / UAN / ESIC
        ws.append(new)
        update = list(example)
        update[0], update[1], update[2], update[8] = "E001", "Ramesh", "Kumar", "9900990099"
        ws.append(update)
        response = self.post(wb)
        self.assertRedirects(response, reverse("employees:list"), fetch_redirect_response=False)
        page = self.client.get(reverse("employees:list"))
        self.assertContains(page, "1 added, 1 updated")
        mahesh = Employee.objects.get(company=self.srks, emp_code="E008")
        self.assertEqual(mahesh.reports_to.emp_code, "E003")
        self.assertEqual(mahesh.revisions.get().basic, D(12000))
        self.assertEqual(self.employee("E001").mobile, "9900990099")
        self.assertEqual(self.employee("E001").revisions.count(), 1)  # salary not changed by upload

    def test_mistakes_save_nothing(self):
        wb = self.sample()
        ws = wb.worksheets[0]
        row = [c.value for c in ws[2]]
        row[0], row[17] = "E008", "BADPAN"
        ws.append(row)
        loop = [c.value for c in ws[2]]
        loop[0], loop[13] = "E003", "E001"  # E001 reports to E003 already
        ws.append(loop)
        response = self.post(wb)
        self.assertContains(response, "Nothing was saved")
        self.assertContains(response, "PAN must look like ABCDE1234F")
        self.assertContains(response, "cannot report to someone who reports to them")
        self.assertContains(response, "Download my sheet with mistakes marked")
        self.assertFalse(Employee.objects.filter(company=self.srks, emp_code="E008").exists())
        marked = self.client.get(reverse("employees:upload_errors"))
        self.assertEqual(marked.status_code, 200)

    def test_export_round_trip_keeps_everything(self):
        response = self.client.get(reverse("employees:export"))
        wb = load_workbook(BytesIO(response.content))
        self.assertNotIn("234567891234", [c.value for row in wb.worksheets[0].iter_rows() for c in row])
        response = self.post(wb)
        page = self.client.get(reverse("employees:list"))
        self.assertContains(page, "0 added, 0 updated, 7 already up to date.")
        self.assertEqual(self.employee("E002").aadhaar, "234567891234")  # empty Aadhaar cell keeps the number
        self.assertEqual(self.employee("E006").reports_to.emp_code, "E003")
        self.assertEqual(SalaryRevision.objects.filter(company=self.srks).count(), 7)

    def test_wrong_sheet_type(self):
        month_sheet = self.client.get(reverse("payroll:sheet", args=[NOV]))
        response = self.client.post(reverse("employees:upload"),
                                    {"file": SimpleUploadedFile("x.xlsx", month_sheet.content)})
        self.assertContains(response, "This is the monthly attendance sheet, but this page needs the employee list.")


class ListAndChartTest(DemoTestCase):
    def test_list_filters(self):
        page = self.client.get(reverse("employees:list"), {"q": "sunita"})
        self.assertContains(page, "Sunita Devi")
        self.assertNotContains(page, "Ramesh Kumar")
        page = self.client.get(reverse("employees:list"))
        self.assertContains(page, "18,000")  # E002 monthly gross

    def test_fix_filter(self):
        Employee.objects.filter(company=self.srks, emp_code="E005").update(uan="")
        page = self.client.get(reverse("employees:list"), {"fix": "no_uan"})
        self.assertContains(page, "Gopal Rao")
        self.assertNotContains(page, "Sunita Devi")

    def test_organisation_tree(self):
        page = self.client.get(reverse("employees:organisation"))
        content = page.content.decode()
        self.assertLess(content.index("Mohd. Arif"), content.index("Ramesh Kumar"))
        self.assertIn("Neha Joshi", content)


class SimpleUseTest(DemoTestCase):
    """Fewer steps for the owner: one question for salary changes, type-in designations, add another."""

    def export_with(self, changes):
        wb = load_workbook(BytesIO(self.client.get(reverse("employees:export")).content))
        ws = wb.worksheets[0]
        headers = {c.value: c.column for c in ws[1]}
        rows = {ws.cell(row=r, column=1).value: r for r in range(2, ws.max_row + 1)}
        for code, values in changes.items():
            for header, value in values.items():
                ws.cell(row=rows[code], column=headers[header]).value = value  # value=None would not clear it
        out = BytesIO()
        wb.save(out)
        return SimpleUploadedFile("staff.xlsx", out.getvalue())

    def test_salary_change_asks_once_and_updates(self):
        response = self.client.post(reverse("employees:upload"), {"file": self.export_with({"E002": {"Basic": 10000}})})
        self.assertContains(response, "1 person has a different salary in your file")
        self.assertContains(response, "Yes, save and change these salaries")
        self.assertEqual(self.employee("E002").revisions.count(), 1)  # nothing saved yet
        response = self.client.post(reverse("employees:upload_confirm"), {"salaries": "change", "month": "2026-11"})
        self.assertRedirects(response, reverse("employees:list"), fetch_redirect_response=False)
        e002 = self.employee("E002")
        self.assertEqual(e002.structure_on(NOV).basic, D(10000))
        self.assertEqual(e002.revisions.count(), 2)  # the old salary stays in the history
        line = self.run_for().lines.get(employee=e002)
        self.assertEqual(line.rate_basic, D(10000))  # the draft month follows at once
        page = self.client.get(reverse("employees:list"))
        self.assertContains(page, "New salary from November 2026 for 1 people.")

    def test_salary_change_can_be_skipped(self):
        self.client.post(reverse("employees:upload"), {"file": self.export_with({"E002": {"Basic": 10000}})})
        self.client.post(reverse("employees:upload_confirm"), {"salaries": "keep"})
        self.assertEqual(self.employee("E002").structure_on(NOV).basic, D(9000))

    def test_finalised_month_not_offered(self):
        self.finalise_november()
        response = self.client.post(reverse("employees:upload"), {"file": self.export_with({"E002": {"Basic": 10000}})})
        months = [m.strftime("%Y-%m") for m in response.context["months"]]
        self.assertNotIn("2026-11", months)
        self.client.post(reverse("employees:upload_confirm"), {"salaries": "change", "month": "2026-11"})
        self.assertEqual(self.employee("E002").structure_on(NOV).basic, D(9000))  # November is locked
        self.assertEqual(self.employee("E002").structure_on(date(2026, 12, 1)).basic, D(10000))

    def test_new_designation_typed_in(self):
        data = dict(FORM, designation_name="Store Keeper", department_name="godown")
        response = self.client.post(reverse("employees:add"), data)
        emp = Employee.objects.get(company=self.srks, emp_code="E008")
        self.assertRedirects(response, reverse("employees:detail", args=[emp.pk]))
        self.assertEqual(emp.designation.name, "Store Keeper")
        self.assertEqual(emp.department.name, "godown")
        data = dict(FORM, emp_code="E009", pan="", designation_name="store keeper")  # same name, other case
        self.client.post(reverse("employees:add"), data)
        self.assertEqual(Employee.objects.get(company=self.srks, emp_code="E009").designation, emp.designation)

    def test_save_and_add_another(self):
        response = self.client.post(reverse("employees:add"), dict(FORM, then="another"))
        self.assertRedirects(response, reverse("employees:add"))
        self.assertContains(self.client.get(reverse("employees:add")), 'value="E009"')

    def test_duplicate_uan_refused(self):
        response = self.client.post(reverse("employees:add"), dict(FORM, uan="101234567802"))
        self.assertContains(response, "This UAN already belongs to Sunita Devi (E002).")

    def test_first_day_shows_start_here(self):
        from accounts.models import User
        from companies.models import Company

        shop = Company.objects.create(code="NEWS", legal_name="New Shop", trade_name="New Shop",
                                      address_line1="1 Road", city="Pune", state="MH", pincode="411001",
                                      payroll_start_month=NOV)
        self.client.force_login(User.objects.create_user(username="9000000001", password="x-pass-123", company=shop))
        self.assertContains(self.client.get(reverse("home")), "Start here: add your employees")
        page = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertContains(page, "No one is paid in November 2026 yet. Add your employees first")
        response = self.client.post(reverse("employees:add"), dict(FORM, emp_code="E001", designation_name="Helper"))
        self.assertEqual(response.status_code, 302)  # first employee added without any setup page
