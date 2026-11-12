"""Working days: weekly offs per company, salary per working day of each month, and the attendance sheet."""
from datetime import date
from decimal import Decimal as D
from io import BytesIO

from django.test import SimpleTestCase
from openpyxl import load_workbook

from core.testing import DemoTestCase
from excel_io import builders
from excel_io.parsers import parse_monthly
from payroll import engine
from payroll.service import RunContext

from .reference import ref_rates

SIX_DAY = (6,)
FIVE_DAY = (5, 6)
ONLY_PAY = dict(pf_enabled=False, esi_enabled=False, pt_enabled=False, tds_enabled=False)


def basic(year, month, divisor="WORKING", weekly_off=SIX_DAY, doj=date(2020, 1, 1), dol=None, **days):
    emp = engine.Employee("E1", "Test", "M", date(1990, 1, 1), doj, dol)
    structure = engine.Structure(basic=D(24000))
    settings = engine.Settings(salary_divisor=divisor, weekly_off=weekly_off, **ONLY_PAY)
    inp = engine.MonthInput(**{k: D(v) for k, v in days.items()})
    return engine.compute_line(year, month, emp, structure, structure, inp, settings, ref_rates(), engine.YTD()).basic


class WorkingDaysCountTest(SimpleTestCase):
    def test_each_month_has_its_own_count(self):
        # 2026: February 4 Sundays, March 5, April 4
        self.assertEqual([engine.working_days(2026, m, SIX_DAY) for m in (2, 3, 4)], [24, 26, 26])
        self.assertEqual([engine.working_days(2026, m, FIVE_DAY) for m in (2, 3, 4)], [20, 22, 22])
        self.assertEqual(engine.working_days(2026, 2, ()), 28)

    def test_joiners_and_leavers_count_only_their_days(self):
        # joined Monday 16 February 2026: 16-28 Feb is 13 days with one Sunday
        self.assertEqual(engine.working_days_employed(2026, 2, date(2026, 2, 16), None, SIX_DAY), 12)
        # left on Saturday 14 March 2026: 1-14 March has two Sundays
        self.assertEqual(engine.working_days_employed(2026, 3, date(2020, 1, 1), date(2026, 3, 14), SIX_DAY), 12)
        self.assertEqual(engine.working_days_employed(2026, 3, date(2026, 4, 1), None, SIX_DAY), 0)


class SalaryPerWorkingDayTest(SimpleTestCase):
    def test_full_attendance_is_full_salary(self):
        self.assertEqual(basic(2026, 2, present=24, week_off_holiday=4), D(24000))
        self.assertEqual(basic(2026, 3, present=26, week_off_holiday=5), D(24000))

    def test_absent_days_are_cut_by_working_days_of_that_month(self):
        self.assertEqual(basic(2026, 2, present=22, week_off_holiday=4), D(22000))  # 24000 x 22/24
        self.assertEqual(basic(2026, 3, present=24, week_off_holiday=5), D(22154))  # 24000 x 24/26
        self.assertEqual(basic(2026, 2, divisor="CALENDAR", present=22, week_off_holiday=4), D(22286))  # x 26/28

    def test_joiner_is_paid_for_working_days_after_joining(self):
        # 12 of 24 working days, whether or not the Sundays before joining were written as weekly offs
        self.assertEqual(basic(2026, 2, doj=date(2026, 2, 16), present=12, week_off_holiday=1), D(12000))
        self.assertEqual(basic(2026, 2, doj=date(2026, 2, 16), present=12, week_off_holiday=3), D(12000))
        self.assertEqual(basic(2026, 2, doj=date(2026, 2, 16), present=10, week_off_holiday=1), D(10000))

    def test_five_day_week(self):
        self.assertEqual(basic(2026, 2, weekly_off=FIVE_DAY, present=19, week_off_holiday=8), D(22800))  # x 19/20


class AttendanceSheetTest(DemoTestCase):
    DEC = date(2026, 12, 1)

    def workbook(self):
        """The December sheet as it comes back from a download."""
        out = BytesIO()
        builders.monthly_workbook(RunContext(self.srks, self.DEC)).save(out)
        return load_workbook(BytesIO(out.getvalue()))

    def test_details_working_days_and_weekly_offs_are_filled_in(self):
        ws = self.workbook().worksheets[0]
        headers = [c.value for c in ws[1]]
        for header in ("Date of Joining", "PAN", "UAN (PF No.)", "ESI No.", "Working Days"):
            self.assertIn(header, headers)
        col = {h: i for i, h in enumerate(headers)}
        rows = {r[0].value: r for r in ws.iter_rows(min_row=2)}
        emp = self.employee("E002")
        row = rows["E002"]
        self.assertEqual(row[col["PAN"]].value, emp.pan)
        self.assertEqual(row[col["UAN (PF No.)"]].value, emp.uan)
        self.assertEqual(row[col["ESI No."]].value, emp.esic_ip_no)
        self.assertEqual(row[col["Date of Joining"]].value.date(), emp.date_of_joining)
        self.assertEqual(row[col["Working Days"]].value, 27)  # December 2026: 31 days, 4 Sundays
        self.assertEqual(row[col["Weekly Off + Holidays"]].value, 4)
        self.assertIsNone(row[col["Days Present"]].value)  # attendance is never guessed

    def _upload(self, wb):
        out = BytesIO()
        wb.save(out)
        return parse_monthly(out.getvalue(), RunContext(self.srks, self.DEC))

    def test_untouched_sheet_is_still_refused(self):
        _rows, _errors, file_errors = self._upload(self.workbook())
        self.assertTrue(any("No days are filled in this sheet" in e for e in file_errors))

    def test_a_sheet_without_the_reading_columns_still_uploads(self):
        wb = self.workbook()
        ws = wb.worksheets[0]
        headers = [c.value for c in ws[1]]
        present = headers.index("Days Present") + 1
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=present).value = 27
        for header in reversed(["Date of Joining", "PAN", "UAN (PF No.)", "ESI No.", "Working Days"]):
            ws.delete_cols(headers.index(header) + 1)
        rows, errors, file_errors = self._upload(wb)
        self.assertEqual((errors, file_errors), ([], []))
        self.assertEqual(len(rows), len(RunContext(self.srks, self.DEC).employees))

    def test_line_keeps_working_days_of_the_month(self):
        line = self.run_for().lines.first()
        self.assertEqual(line.working_days, 25)  # November 2026: 30 days, 5 Sundays
