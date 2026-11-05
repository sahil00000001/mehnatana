"""README §17.3: Excel round trip, upload errors, finalise/reopen, snapshot."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from advances.models import Advance, AdvanceRecovery
from core.testing import NOV, DemoTestCase
from employees.models import SalaryRevision
from payroll import service
from payroll.models import PayrollLine, PayrollRun
from statutory.models import StatutoryRate

from .reference import GOLDEN, GOLDEN_FIELDS

INPUTS = {  # README §16.1 November 2026 sheet inputs: column header -> value
    "E001": {"Days Present": 26, "Weekly Off + Holidays": 4, "OT Hours": 10, "Increment ₹ per month": 500,
             "Advance Deduction ₹": 1000},
    "E002": {"Days Present": 22, "Paid Leave Days": 1, "Weekly Off + Holidays": 4, "Absent / LOP Days": 3},
    "E003": {"Days Present": 26, "Weekly Off + Holidays": 4},
    "E004": {"Days Present": 26, "Weekly Off + Holidays": 4},
    "E005": {"Days Present": 26, "Weekly Off + Holidays": 4},
    "E006": {"Days Present": 13, "Weekly Off + Holidays": 2},
    "E007": {"Days Present": 26, "Weekly Off + Holidays": 4},
}


class MonthTestCase(DemoTestCase):
    def clear_november(self):
        run = self.run_for()
        run.lines.all().delete()
        run.delete()

    def download_sheet(self):
        response = self.client.get(reverse("payroll:sheet", args=[NOV]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("SRKS_salary_2026-11.xlsx", response["Content-Disposition"])
        return load_workbook(BytesIO(response.content))

    @staticmethod
    def fill(wb, inputs):
        ws = wb.worksheets[0]
        headers = {cell.value: cell.column for cell in ws[1]}
        rows = {ws.cell(row=r, column=1).value: r for r in range(2, ws.max_row + 1)}
        for code, values in inputs.items():
            for header, value in values.items():
                ws.cell(row=rows[code], column=headers[header]).value = value  # value=None would not clear it
        return wb

    @staticmethod
    def as_upload(wb, name="november.xlsx"):
        out = BytesIO()
        wb.save(out)
        return SimpleUploadedFile(name, out.getvalue())

    def upload(self, wb, name="november.xlsx"):
        return self.client.post(reverse("payroll:upload", args=[NOV]), {"file": self.as_upload(wb, name)})


class RoundTripTest(MonthTestCase):
    def test_download_fill_upload_gives_golden_numbers(self):
        self.clear_november()
        wb = self.download_sheet()
        ws = wb.worksheets[0]
        self.assertEqual([ws.cell(row=r, column=1).value for r in range(2, 9)],
                         ["E001", "E002", "E003", "E004", "E005", "E006", "E007"])
        headers = {cell.value: cell.column for cell in ws[1]}
        self.assertEqual(ws.cell(row=2, column=headers["Advance Deduction ₹"]).value, 1000)  # suggested instalment
        self.assertEqual(ws.cell(row=2, column=headers["Days in Month"]).value, 30)
        self.assertEqual(wb["_meta"].sheet_state, "hidden")
        self.assertIn("How to fill", wb.sheetnames)

        response = self.upload(self.fill(wb, INPUTS))
        self.assertRedirects(response, reverse("payroll:month", args=[NOV]) + "#review")
        run = self.run_for()
        self.assertTrue(run.uploaded_file)
        lines = {l.code: l for l in run.lines.all()}
        for code, expected in GOLDEN.items():
            for field, value in zip(GOLDEN_FIELDS, expected):
                engine_name = {"lop": "lop", "advance": "advance"}.get(field, field)
                with self.subTest(code=code, field=field):
                    self.assertEqual(D(getattr(lines[code], engine_name)), D(value))
        self.assertEqual(run.pf_admin_establishment, D(586))
        self.assertEqual(run.pf_admin_topup, D(0))

        page = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertContains(page, "2,72,286")
        self.assertContains(page, "3,15,481")

    def test_forgiving_values(self):
        self.clear_november()
        wb = self.download_sheet()
        inputs = {code: dict(values) for code, values in INPUTS.items()}
        inputs["E001"]["Advance Deduction ₹"] = "₹1,000"
        inputs["E002"]["Days Present"] = " 22 "
        inputs["E003"]["Days Present"] = "26.0"
        self.upload(self.fill(wb, inputs))
        self.assertEqual(self.run_for().lines.get(employee__emp_code="E001").net_pay, D(11108))

    def test_reupload_replaces_inputs(self):
        wb = self.download_sheet()  # pre-filled with the draft's inputs
        ws = wb.worksheets[0]
        headers = {cell.value: cell.column for cell in ws[1]}
        self.assertEqual(ws.cell(row=3, column=headers["Absent / LOP Days"]).value, 3)
        self.fill(wb, {"E003": {"Incentive / Bonus ₹": 2000}})
        self.upload(wb)
        line = self.run_for().lines.get(employee__emp_code="E003")
        self.assertEqual(line.incentive_pay, D(2000))
        self.assertEqual(line.gross, D(42000))


class UploadErrorsTest(MonthTestCase):
    def assert_rejected(self, wb, text, name="november.xlsx"):
        before = list(PayrollLine.objects.filter(run=self.run_for()).values_list("pk", "net_pay"))
        response = self.upload(wb, name)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing was saved")
        self.assertContains(response, text)
        after = list(PayrollLine.objects.filter(run=self.run_for()).values_list("pk", "net_pay"))
        self.assertEqual(before, after)
        return response

    def test_unknown_code(self):
        wb = self.download_sheet()
        wb.worksheets[0].append(["E099", "Nobody", "", 30, 30])
        self.assert_rejected(wb, "Unknown Emp Code E099")

    def test_duplicate_code(self):
        wb = self.download_sheet()
        ws = wb.worksheets[0]
        ws.append([c.value for c in ws[2]])
        self.assert_rejected(wb, "is written twice")

    def test_missing_employee(self):
        wb = self.download_sheet()
        wb.worksheets[0].delete_rows(8)  # E007
        self.assert_rejected(wb, "These employees are missing from the sheet: E007 Neha Joshi")

    def test_wrong_month(self):
        wb = self.download_sheet()
        for row in wb["_meta"].iter_rows():
            if row[0].value == "month":
                row[1].value = "2026-10"
        self.assert_rejected(wb, "This sheet is for October 2026, but you are uploading for November 2026.")

    def test_days_do_not_add_up(self):
        wb = self.fill(self.download_sheet(), {"E002": {"Days Present": 28, "Paid Leave Days": 0,
                                                        "Absent / LOP Days": None}})
        response = self.assert_rejected(wb, "Days do not add up")
        self.assertContains(response, "Download my sheet with mistakes marked")
        marked = self.client.get(reverse("payroll:errors", args=[NOV]))
        self.assertEqual(marked.status_code, 200)
        ws = load_workbook(BytesIO(marked.content)).worksheets[0]
        self.assertEqual(ws.cell(row=1, column=ws.max_column).value, "Errors")
        self.assertIn("Days do not add up", ws.cell(row=3, column=ws.max_column).value)

    def test_advance_more_than_outstanding(self):
        wb = self.fill(self.download_sheet(), {"E001": {"Advance Deduction ₹": 6000}})
        self.assert_rejected(wb, "more than the advance still to be recovered")

    def test_joined_mid_month_paid_days(self):
        wb = self.fill(self.download_sheet(), {"E006": {"Days Present": 26, "Weekly Off + Holidays": 4}})
        self.assert_rejected(wb, "more than the days employed")

    def test_other_deduction_needs_reason(self):
        wb = self.fill(self.download_sheet(), {"E003": {"Other Deduction ₹": 300}})
        self.assert_rejected(wb, "Write the reason for the other deduction.")

    def test_not_xlsx(self):
        response = self.client.post(reverse("payroll:upload", args=[NOV]),
                                    {"file": SimpleUploadedFile("sheet.xls", b"not excel")})
        self.assertContains(response, "Please save the file as Excel Workbook (.xlsx) and upload again.")


class FinaliseReopenTest(MonthTestCase):
    def outstanding(self):
        return Advance.objects.get(company=self.srks, employee__emp_code="E001").outstanding

    def test_finalise_and_reopen(self):
        self.assertEqual(self.outstanding(), D(5000))
        response = self.client.post(reverse("payroll:finalise", args=[NOV]))
        self.assertRedirects(response, reverse("payroll:month", args=[NOV]) + "#documents",
                             fetch_redirect_response=False)
        run = self.run_for()
        self.assertTrue(run.is_final)
        recoveries = AdvanceRecovery.objects.filter(payroll_line__run=run)
        self.assertEqual([(r.advance.employee.emp_code, r.amount) for r in recoveries], [("E001", D(1000))])
        revisions = SalaryRevision.objects.filter(payroll_run=run)
        self.assertEqual([(r.employee.emp_code, r.basic, r.effective_from) for r in revisions],
                         [("E001", D(12500), NOV)])
        self.assertEqual(self.outstanding(), D(4000))
        self.assertEqual(run.rates_snapshot["values"]["PF_WAGE_CEILING"], "25000")

        response = self.client.post(reverse("payroll:reopen", args=[NOV]), {"reason": "Wrong OT for Ramesh"})
        run.refresh_from_db()
        self.assertFalse(run.is_final)
        self.assertEqual(run.revision_no, 1)
        self.assertFalse(AdvanceRecovery.objects.filter(payroll_line__run=run).exists())
        self.assertFalse(SalaryRevision.objects.filter(payroll_run=run).exists())
        self.assertEqual(self.outstanding(), D(5000))
        # the draft still calculates the same after reopening
        self.assertEqual(run.lines.get(employee__emp_code="E001").net_pay, D(11108))

    def test_reopen_needs_reason(self):
        self.finalise_november()
        response = self.client.post(reverse("payroll:reopen", args=[NOV]), {"reason": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.run_for().is_final)

    def test_months_in_order(self):
        december = PayrollRun.objects.create(company=self.srks, month=date(2026, 12, 1))
        with self.assertRaises(service.PayrollError):
            service.finalise(december, self.srks_user)
        self.assertIn("Finalise November 2026 first", service.upload_blocker(self.srks, date(2026, 12, 1)))
        response = self.client.get(reverse("payroll:month", args=[date(2026, 12, 1)]))
        self.assertContains(response, "Finalise November 2026 first")

    def test_only_latest_finalised_month_reopens(self):
        self.finalise_november()
        dec = date(2026, 12, 1)
        ctx = service.RunContext(self.srks, dec)
        rows = {e.pk: service.RowInput(present=D(27), week_off_holiday=D(4)) for e in ctx.employees}
        with self.today_is(date(2027, 1, 5)):
            service.finalise(service.save_inputs(self.srks, dec, rows, self.srks_user), self.srks_user)
        with self.assertRaisesMessage(service.PayrollError, "Only the latest finalised month"):
            service.reopen(self.run_for(), self.srks_user, "test")
        # December uses the increment saved by November: E001 Basic 12,500
        dec_line = PayrollRun.objects.get(company=self.srks, month=dec).lines.get(employee__emp_code="E001")
        self.assertEqual(dec_line.rate_basic, D(12500))
        # December TDS for E004 uses November as year-to-date
        self.assertEqual(PayrollRun.objects.get(company=self.srks, month=dec)
                         .lines.get(employee__emp_code="E004").tds, D(3900))


class SnapshotTest(MonthTestCase):
    def test_finalised_lines_do_not_change(self):
        self.finalise_november()
        before = {l.code: (l.gross, l.net_pay, l.epf_wage, l.employer_cost) for l in self.run_for().lines.all()}
        StatutoryRate.objects.create(key="PF_WAGE_CEILING", value=D(30000), effective_from=date(2026, 11, 1))
        SalaryRevision.objects.create(company=self.srks, employee=self.employee("E002"), effective_from=NOV,
                                      basic=20000, hra=8000)
        after = {l.code: (l.gross, l.net_pay, l.epf_wage, l.employer_cost) for l in self.run_for().lines.all()}
        self.assertEqual(before, after)
        with self.assertRaises(service.PayrollError):
            service.recompute_run(self.run_for())
        page = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertContains(page, "2,72,286")


class LineEditTest(MonthTestCase):
    def test_edit_one_row(self):
        line = self.run_for().lines.get(employee__emp_code="E003")
        url = reverse("payroll:line_edit", args=[NOV, line.pk])
        data = {"present": "26", "paid_leave": "", "week_off_holiday": "4", "lop": "", "ot_hours": "",
                "ot_amount": "", "incentive": "1000", "increment": "", "advance": "", "other_deduction": "",
                "other_reason": "", "tds_override": "", "remarks": "Diwali"}
        response = self.client.post(url, data)
        self.assertRedirects(response, reverse("payroll:month", args=[NOV]) + "#review")
        line.refresh_from_db()
        self.assertEqual(line.gross, D(41000))
        self.assertEqual(line.remarks, "Diwali")

    def test_edit_shows_mistakes(self):
        line = self.run_for().lines.get(employee__emp_code="E003")
        url = reverse("payroll:line_edit", args=[NOV, line.pk])
        response = self.client.post(url, {"present": "29", "week_off_holiday": "4"})
        self.assertContains(response, "Days do not add up")

    def test_cannot_edit_finalised(self):
        self.finalise_november()
        line = self.run_for().lines.get(employee__emp_code="E003")
        response = self.client.post(reverse("payroll:line_edit", args=[NOV, line.pk]), {"present": "26"})
        self.assertRedirects(response, reverse("payroll:month", args=[NOV]))


class FriendlySheetTest(MonthTestCase):
    """The sheet is read the way a busy owner fills it, and mistakes are explained."""

    def test_untouched_sheet_is_refused_with_one_message(self):
        self.clear_november()
        response = self.client.post(reverse("payroll:upload", args=[NOV]), {"file": self.as_upload(self.download_sheet())})
        self.assertContains(response, "No days are filled in this sheet.")
        self.assertNotContains(response, "The days are empty.")
        self.assertFalse(PayrollRun.objects.filter(company=self.srks, month=NOV).exists())

    def test_one_empty_row_is_pointed_out(self):
        wb = self.fill(self.download_sheet(), {"E003": {"Days Present": None, "Weekly Off + Holidays": None}})
        response = self.upload(wb)
        self.assertContains(response, "The days are empty.")

    def test_heading_written_differently_still_read(self):
        wb = self.download_sheet()
        ws = wb.worksheets[0]
        for cell in ws[1]:
            if cell.value == "Incentive / Bonus ₹":
                cell.value = "incentive/bonus"
        self.fill(wb, {"E003": {"incentive/bonus": 2000}})
        self.upload(wb)
        self.assertEqual(self.run_for().lines.get(employee__emp_code="E003").incentive_pay, D(2000))

    def test_unknown_column_with_numbers_is_refused(self):
        wb = self.download_sheet()
        ws = wb.worksheets[0]
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value="Bonus")
        ws.cell(row=2, column=col, value=500)
        response = self.upload(wb)
        self.assertContains(response, "We do not know the column &#x27;Bonus&#x27;")

    def test_empty_extra_column_is_fine(self):
        wb = self.download_sheet()
        ws = wb.worksheets[0]
        ws.cell(row=1, column=ws.max_column + 1, value="My notes")
        response = self.upload(wb)
        self.assertEqual(response.status_code, 302)
