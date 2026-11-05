import os

from django.core.management.base import BaseCommand, CommandError

from core.demo import create_demo, delete_company, demo_companies


class Command(BaseCommand):
    help = ("Create the two demo businesses (README §16) with owner logins and a draft November 2026. "
            "If DEMO_PASSWORD is set, both owners get that password and it is not printed.")

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete the demo businesses first (SRKS and ANPS) and create them again.")
        parser.add_argument("--delete", action="store_true",
                            help="Delete the demo businesses (SRKS and ANPS) and stop.")
        parser.add_argument("--hide-passwords", action="store_true",
                            help="Do not print the random passwords (for public logs). Give the owners a password "
                                 "later with admin → Logins → Reset password.")

    def handle(self, *args, reset=False, delete=False, hide_passwords=False, **options):
        existing = list(demo_companies())
        if delete:
            for company in existing:
                delete_company(company)
            self.stdout.write(self.style.SUCCESS(
                "Deleted the demo businesses: " + ", ".join(c.trade_name for c in existing) + "."
                if existing else "There were no demo businesses to delete."))
            return
        if existing:
            if not reset:
                raise CommandError("Demo data already exists. Run with --reset to delete and create it again.")
            for company in existing:
                delete_company(company)
        fixed_password = os.environ.get("DEMO_PASSWORD", "")
        if fixed_password and len(fixed_password) < 10:
            raise CommandError("DEMO_PASSWORD must have at least 10 characters.")
        result = create_demo(password=fixed_password or None)
        self.stdout.write(self.style.SUCCESS("Demo data created."))
        self.stdout.write("")
        for code, (company, user, password) in result.items():
            self.stdout.write(f"  {company.trade_name} ({code})")
            self.stdout.write(f"    username: {user.username}")
            if fixed_password:
                shown = "the DEMO_PASSWORD you set"
            elif hide_passwords:
                shown = "not shown — use admin → Logins → Reset password"
            else:
                shown = password
            self.stdout.write(f"    password: {shown}")
        self.stdout.write("")
        if not fixed_password and not hide_passwords:
            self.stdout.write(self.style.WARNING("These passwords are shown only once."))
        self.stdout.write(self.style.WARNING(
            "Demo businesses are for trying the software. Remove them with --delete before real use."))
