"""seed_demo: a fixed password that is never printed, and deleting only the demo businesses."""
import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import User
from companies.models import Company


def run(*args, password=None):
    out = StringIO()
    env = {"DEMO_PASSWORD": password} if password else {}
    with mock.patch.dict(os.environ, env):
        if not password:
            os.environ.pop("DEMO_PASSWORD", None)
        call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


class SeedDemoTest(TestCase):
    def test_fixed_password_is_used_and_never_printed(self):
        output = run(password="Demo-test-pass-123")
        self.assertNotIn("Demo-test-pass-123", output)
        self.assertIn("username: 9876500001", output)
        self.assertIn("the DEMO_PASSWORD you set", output)
        for username in ("9876500001", "9876500002"):
            self.assertTrue(User.objects.get(username=username).check_password("Demo-test-pass-123"))

    def test_hidden_random_passwords_for_public_logs(self):
        output = run("--hide-passwords")
        self.assertIn("not shown — use admin → Logins → Reset password", output)
        self.assertNotIn("shown only once", output)
        owner = User.objects.get(username="9876500001")
        self.assertTrue(owner.has_usable_password())

    def test_short_password_is_refused(self):
        with self.assertRaises(CommandError):
            run(password="short")
        self.assertFalse(Company.objects.exists())

    def test_reset_and_delete(self):
        run(password="Demo-test-pass-123")
        with self.assertRaises(CommandError):
            run(password="Demo-test-pass-123")  # already there
        run("--reset", password="Demo-test-pass-123")
        self.assertEqual(Company.objects.count(), 2)
        output = run("--delete")
        self.assertIn("Deleted the demo businesses:", output)
        self.assertIn("Shri Ram Kirana Stores", output)
        self.assertIn("Annapurna Sweets", output)
        self.assertFalse(Company.objects.exists())
        self.assertFalse(User.objects.filter(username__in=["9876500001", "9876500002"]).exists())
        self.assertIn("There were no demo businesses to delete.", run("--delete"))

    def test_a_real_business_with_a_demo_code_is_never_deleted(self):
        run(password="Demo-test-pass-123")
        Company.objects.filter(code="SRKS").update(lin="KA/REAL/123")  # a real business, not the demo
        run("--delete")
        self.assertEqual(list(Company.objects.values_list("code", flat=True)), ["SRKS"])
