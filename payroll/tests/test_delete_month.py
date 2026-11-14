"""manage.py delete_month: a salary month removed completely so it can be done again."""
from datetime import date
from decimal import Decimal as D
from io import StringIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import reverse

from advances.models import Advance, AdvanceRecovery
from core.models import AuditLog, StoredFile
from core.test_storage import DATABASE_FILES
from core.testing import NOV, DemoTestCase
from employees.models import SalaryRevision
from payroll.models import PayrollLine, PayrollRun

DEC = date(2026, 12, 1)


@override_settings(STORAGES=DATABASE_FILES)
class DeleteMonthTest(DemoTestCase):
    def setUp(self):
        super().setUp()
        with self.today_is(date(2026, 11, 30)):
            self.finalise_november()  # E001: 1,000 advance recovered and a 500 increment
        run = self.run_for()
        run.uploaded_file.save("SRKS_salary_2026-11_new.xlsx", ContentFile(b"new"))
        default_storage.save("private/uploads/SRKS/2026-11/SRKS_salary_2026-11_old.xlsx", ContentFile(b"old"))
        default_storage.save("private/uploads/SRKS/2026-12/keep.xlsx", ContentFile(b"december"))
        default_storage.save("private/uploads/ANPS/2026-11/keep.xlsx", ContentFile(b"other company"))

    def advance(self):
        return Advance.objects.get(employee=self.employee("E001"))

    def delete(self, *args):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("delete_month", *args, stdout=out)
        return out.getvalue()

    def test_month_is_removed_and_finalising_taken_back(self):
        self.assertEqual(self.advance().outstanding, D(4000))
        output = self.delete("2026-11", "--company", "srks")

        self.assertIn("SRKS: November 2026 deleted", output)
        self.assertFalse(PayrollRun.objects.filter(company=self.srks, month=NOV).exists())
        self.assertFalse(PayrollLine.objects.filter(company=self.srks).exists())
        self.assertFalse(AdvanceRecovery.objects.exists())
        advance = self.advance()
        self.assertEqual((advance.outstanding, advance.status), (D(5000), Advance.Status.OPEN))
        self.assertFalse(SalaryRevision.objects.filter(source=SalaryRevision.Source.MONTHLY_SHEET).exists())
        self.assertEqual(self.employee("E001").revisions.first().gross, D(12000))  # the 500 increment is gone
        self.assertEqual(sorted(StoredFile.objects.values_list("name", flat=True)),
                         ["private/uploads/ANPS/2026-11/keep.xlsx", "private/uploads/SRKS/2026-12/keep.xlsx"])
        entry = AuditLog.objects.get(action="payroll.delete_month")
        self.assertEqual((entry.company, entry.details["lines"], entry.details["recoveries"]), (self.srks, 7, 1))
        # the other company is untouched, and the month can be started again
        self.assertTrue(self.run_for(self.anps).lines.exists())
        self.assertEqual(self.client.get(reverse("payroll:month", args=[NOV])).status_code, 200)

    def test_every_company_by_default(self):
        self.delete("2026-11")
        self.assertFalse(PayrollRun.objects.exists())
        self.assertFalse(PayrollLine.objects.exists())

    def test_dry_run_changes_nothing(self):
        output = self.delete("2026-11", "--dry-run")
        self.assertIn("SRKS Shri Ram Kirana Stores: Finalised, 7 employees, 1 advance recoveries (₹1,000), "
                      "1 increments, 2 uploaded sheets", output)
        self.assertIn("Dry run: nothing was deleted.", output)
        self.assertEqual(PayrollRun.objects.count(), 2)
        self.assertEqual(AdvanceRecovery.objects.count(), 1)
        self.assertEqual(StoredFile.objects.count(), 4)

    def test_a_later_finalised_month_stops_everything(self):
        PayrollRun.objects.create(company=self.srks, month=DEC, status=PayrollRun.Status.FINALISED)
        with self.assertRaisesMessage(CommandError, "Nothing was deleted"):
            self.delete("2026-11")
        self.assertEqual(PayrollRun.objects.filter(month=NOV).count(), 2)
        self.assertEqual(AdvanceRecovery.objects.count(), 1)

    def test_bad_month_or_company(self):
        with self.assertRaisesMessage(CommandError, "YYYY-MM"):
            self.delete("Oct-2026")
        with self.assertRaisesMessage(CommandError, "No company with code XYZ"):
            self.delete("2026-11", "--company", "XYZ")
