"""Files kept in the database (FILE_STORAGE=database, used on Vercel) work like the media folder."""
import os
from io import BytesIO, StringIO
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import User
from core.models import StoredFile
from core.testing import NOV, DemoTestCase

DATABASE_FILES = {
    "default": {"BACKEND": "core.storage.DatabaseStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}


@override_settings(STORAGES=DATABASE_FILES)
class DatabaseStorageTest(DemoTestCase):
    def sheet(self, changes=None):
        wb = load_workbook(BytesIO(self.client.get(reverse("payroll:sheet", args=[NOV])).content))
        ws = wb.worksheets[0]
        headers = {c.value: c.column for c in ws[1]}
        for row, values in (changes or {}).items():
            for header, value in values.items():
                ws.cell(row=row, column=headers[header]).value = value
        out = BytesIO()
        wb.save(out)
        return SimpleUploadedFile("sheet.xlsx", out.getvalue())

    def test_uploaded_sheet_is_kept_and_downloadable(self):
        self.client.post(reverse("payroll:upload", args=[NOV]), {"file": self.sheet()})
        run = self.run_for()
        self.assertTrue(StoredFile.objects.filter(name=run.uploaded_file.name).exists())
        response = self.client.get(reverse("files_upload", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content).startswith(b"PK"))  # an .xlsx (zip) file

    def test_mistakes_marked_file(self):
        self.client.post(reverse("payroll:upload", args=[NOV]), {"file": self.sheet({3: {"Days Present": 29}})})
        response = self.client.get(reverse("payroll:errors", args=[NOV]))
        self.assertEqual(response.status_code, 200)

    def test_logo(self):
        from core.test_qa import same_form_back

        url = reverse("companies:company")
        data = same_form_back(self.client.get(url))
        data.pop("logo", None)
        data["logo"] = SimpleUploadedFile("logo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        self.client.post(url, data)
        response = self.client.get(reverse("files_logo"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))

    def test_no_public_url(self):
        from django.core.files.storage import default_storage

        with self.assertRaises(NotImplementedError):
            default_storage.url("anything.xlsx")


class EnsureAdminTest(TestCase):
    def test_created_once_and_never_changed(self):
        env = {"DJANGO_SUPERUSER_USERNAME": "platformadmin", "DJANGO_SUPERUSER_PASSWORD": "a-long-pass-123456"}
        with mock.patch.dict(os.environ, env):
            call_command("ensure_admin", stdout=StringIO())
            user = User.objects.get(username="platformadmin")
            self.assertTrue(user.is_superuser and user.check_password("a-long-pass-123456"))
        with mock.patch.dict(os.environ, {**env, "DJANGO_SUPERUSER_PASSWORD": "another-pass-98765"}):
            call_command("ensure_admin", stdout=StringIO())
        user.refresh_from_db()
        self.assertTrue(user.check_password("a-long-pass-123456"))
