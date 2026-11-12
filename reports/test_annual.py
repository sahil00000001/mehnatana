"""Attendance register, salary payments (paid / pending) and the annual salary report."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from core.testing import NOV, DemoTestCase


class AttendanceRegisterTest(DemoTestCase):
    def test_register_shows_employee_details_from_the_records(self):
        page = self.client.get(reverse("reports:attendance", args=[NOV]))
        emp = self.employee("E002")
        for text in ("Attendance Register — November 2026 — Shri Ram Kirana Stores", "Emp No.", "UAN (PF No.)",
                     "ESI No.", "Date of joining", emp.pan, emp.uan, emp.esic_ip_no, "01-07-2021",
                     "Days in the month: 30 · Working days: 25"):
            self.assertContains(page, text)
        self.assertContains(page, "DRAFT — not final")
        self.assertNotContains(page, "Seema Patil")  # the other company

    def test_excel_after_finalising(self):
        url = reverse("reports:attendance_xlsx", args=[NOV])
        self.assertRedirects(self.client.get(url), reverse("payroll:month", args=[NOV]),
                             fetch_redirect_response=False)
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        ws = load_workbook(BytesIO(self.client.get(url).content)).active
        headers = [c.value for c in ws[1]]
        self.assertEqual(headers[:9], ["Sl", "Emp No.", "Name", "Designation", "Date of Joining", "PAN",
                                       "UAN (PF No.)", "ESI No.", "Working Days"])
        rows = {r[1]: r for r in ws.iter_rows(min_row=2, values_only=True)}
        emp = self.employee("E002")
        self.assertEqual(rows["E002"][5:8], (emp.pan, emp.uan, emp.esic_ip_no))

    def test_links_from_month_and_reports(self):
        self.assertContains(self.client.get(reverse("payroll:month", args=[NOV])),
                            "Preview attendance register (draft)")
        response = self.client.get(reverse("reports:index"), {"go": "attendance", "month": "2026-11"})
        self.assertRedirects(response, reverse("reports:attendance", args=[NOV]), fetch_redirect_response=False)


class SalaryPaymentsTest(DemoTestCase):
    def test_draft_month_cannot_be_marked_paid(self):
        response = self.client.get(reverse("payroll:payments", args=[NOV]))
        self.assertRedirects(response, reverse("payroll:month", args=[NOV]), fetch_redirect_response=False)

    def test_mark_paid_and_undo(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
        url = reverse("payroll:payments", args=[NOV])
        lines = {l.code: l for l in self.run_for().lines.all()}
        e1, e2 = lines["E001"], lines["E002"]
        with self.today_is(date(2026, 12, 1)):
            page = self.client.get(url)
            self.assertContains(page, "0 of 7 people are marked as paid.")
            response = self.client.post(url, {"action": "paid", "line": [e1.pk, e2.pk], "paid_on": "01-12-2026"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        e1.refresh_from_db()
        self.assertEqual((e1.paid_amount, e1.paid_on), (e1.net_pay, date(2026, 12, 1)))
        self.assertTrue(e1.is_paid)
        month = self.client.get(reverse("payroll:month", args=[NOV]))
        self.assertContains(month, "2 of 7 marked as paid.")
        self.client.post(url, {"action": "unpaid", "line": [e1.pk]})
        e1.refresh_from_db()
        self.assertEqual((e1.paid_amount, e1.paid_on), (D("0"), None))

    def test_future_date_and_nothing_ticked_are_refused(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
            url = reverse("payroll:payments", args=[NOV])
            line = self.run_for().lines.first()
            page = self.client.post(url, {"action": "paid", "line": [line.pk], "paid_on": "05-12-2026"})
            self.assertContains(page, "The payment date cannot be in the future.")
            page = self.client.post(url, {"action": "paid", "paid_on": "30-11-2026"}, follow=True)
            self.assertContains(page, "Tick at least one person first.")
        line.refresh_from_db()
        self.assertIsNone(line.paid_on)

    def test_other_company_lines_are_ignored(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
            self.finalise_november(self.anps)
            anps_line = self.run_for(self.anps).lines.first()
            self.client.post(reverse("payroll:payments", args=[NOV]),
                             {"action": "paid", "line": [anps_line.pk], "paid_on": "30-11-2026"})
        anps_line.refresh_from_db()
        self.assertIsNone(anps_line.paid_on)


class AnnualReportTest(DemoTestCase):
    url = reverse("reports:annual", args=[2026])

    def test_draft_month_is_payable_and_pending(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Annual salary report — 2026-27")
        self.assertContains(page, "Draft months are included and can still change:")
        net = sum(l.net_pay for l in self.run_for().lines.all())
        self.assertEqual(page.context["totals"]["net_pay"], net)
        self.assertEqual(page.context["totals"]["pending"], net)
        rows = {p["code"]: p for p in page.context["rows"]}
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows["E002"]["working_days"], 25)
        self.assertNotContains(page, "Seema Patil")

    def test_paid_to_date_and_pending_after_payments(self):
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()
            line = self.run_for().lines.get(employee__emp_code="E002")
            self.client.post(reverse("payroll:payments", args=[NOV]),
                             {"action": "paid", "line": [line.pk], "paid_on": "30-11-2026"})
        page = self.client.get(self.url)
        totals = page.context["totals"]
        self.assertEqual(totals["paid_amount"], line.net_pay)
        self.assertEqual(totals["pending"], totals["net_pay"] - line.net_pay)
        rows = {p["code"]: p for p in page.context["rows"]}
        self.assertEqual(rows["E002"]["pending"], D("0"))
        self.assertNotContains(page, "Draft months are included")

    def test_month_by_month_views_and_excel(self):
        for show in ("net", "gross", "paid", "days"):
            self.assertEqual(self.client.get(self.url, {"show": show}).status_code, 200)
        wb = load_workbook(BytesIO(self.client.get(self.url, {"format": "xlsx"}).content))
        self.assertEqual(wb.sheetnames, ["Summary 2026-27", "Net pay by month", "Gross by month", "Paid by month",
                                         "Paid days by month", "Company by month"])
        summary = list(wb["Summary 2026-27"].iter_rows(values_only=True))
        self.assertEqual(summary[0][:3], ("Emp No.", "Name", "Date of Joining"))
        self.assertEqual(len(summary), 1 + 7 + 1)  # header, 7 people, total

    def test_empty_year_and_index_link(self):
        self.assertContains(self.client.get(reverse("reports:annual", args=[2025])),
                            "No salary month in this financial year yet.")
        response = self.client.get(reverse("reports:index"), {"go": "annual", "tax_year": "2026"})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
