"""seed_demo: the two demo businesses without any business login, and deleting only the demo businesses."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import User
from companies.models import Company


def run(*args):
    out = StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


class SeedDemoTest(TestCase):
    def test_creates_companies_without_logins(self):
        output = run()
        self.assertIn("Shri Ram Kirana Stores (SRKS)", output)
        self.assertIn("Annapurna Sweets (ANPS)", output)
        self.assertNotIn("password", output.lower())
        self.assertEqual(Company.objects.count(), 2)
        self.assertFalse(User.objects.exists())

    def test_reset_and_delete(self):
        run()
        with self.assertRaises(CommandError):
            run()  # already there
        run("--reset")
        self.assertEqual(Company.objects.count(), 2)
        output = run("--delete")
        self.assertIn("Deleted the demo businesses:", output)
        self.assertIn("Shri Ram Kirana Stores", output)
        self.assertIn("Annapurna Sweets", output)
        self.assertFalse(Company.objects.exists())
        self.assertIn("There were no demo businesses to delete.", run("--delete"))

    def test_a_real_business_with_a_demo_code_is_never_deleted(self):
        run()
        Company.objects.filter(code="SRKS").update(lin="KA/REAL/123")  # a real business, not the demo
        run("--delete")
        self.assertEqual(list(Company.objects.values_list("code", flat=True)), ["SRKS"])
