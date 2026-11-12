from django.core.management.base import BaseCommand, CommandError

from core.demo import create_demo, delete_company, demo_companies


class Command(BaseCommand):
    help = ("Create the two demo businesses (README §16) with their staff and a draft November 2026. "
            "They are opened with the one login, from All companies.")

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete the demo businesses first (SRKS and ANPS) and create them again.")
        parser.add_argument("--delete", action="store_true",
                            help="Delete the demo businesses (SRKS and ANPS) and stop.")

    def handle(self, *args, reset=False, delete=False, **options):
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
        result = create_demo()
        self.stdout.write(self.style.SUCCESS("Demo data created."))
        for code, company in result.items():
            self.stdout.write(f"  {company.trade_name} ({code})")
        self.stdout.write("Log in with the one login and press Open on All companies.")
        self.stdout.write(self.style.WARNING(
            "Demo businesses are for trying the software. Remove them with --delete before real use."))
