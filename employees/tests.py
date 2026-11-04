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
    "address": "Jayanagar", "date_of_joining": "2026-11-20", "department": "", "designation": "",
    "reports_to": "", "employment_type": "Permanent", "skill_category": "Unskilled", "nature_of_duties": "Packing",
    "basic": "11000", "da": "", "hra": "3000", "conveyance": "", "other_allowance": "", "pf_member": "on",
    "eps_choice": "AUTO", "uan": "", "name_as_per_uan": "", "esic_ip_no": "", "esi_override": "AUTO",
    "tax_regime": "NEW", "old_regime_deductions_annual": "", "payment_mode": "Cash", "bank_name": "",
    "bank_account_no": "", "ifsc": "", "pan": "abcpl 1234 q", "aadhaar": "", "aadhaar_consent": "",
}


class EmployeeFormTest(DemoTestCase):
    def form_data(self, **changes):
        data = dict(FORM, designation=str(self.employee("E001").designation_id))
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
        self.assertContains(page, "₹18000 → ₹19000 (+₹1000)")
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
        ws.append(new)
        update = list(example)
        update[0], update[1], update[2], update[8] = "E001", "Ramesh", "Kumar", "9900990099"
        ws.append(update)
        response = self.post(wb)
        self.assertRedirects(response, reverse("employees:list"), fetch_redirect_response=False)
        page = self.client.get(reverse("employees:list"))
        self.assertContains(page, "1 added, 1 updated.")
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
        self.assertContains(page, "0 added, 7 updated.")
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
