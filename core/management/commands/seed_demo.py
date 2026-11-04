from django.core.management.base import BaseCommand, CommandError

from companies.models import Company
from core.demo import create_demo, delete_company


class Command(BaseCommand):
    help = "Create the two demo businesses (README §16) with owner logins and a draft November 2026."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete the demo businesses first (SRKS and ANPS) and create them again.")

    def handle(self, *args, reset=False, **options):
        existing = Company.objects.filter(code__in=["SRKS", "ANPS"])
        if existing.exists():
            if not reset:
                raise CommandError("Demo data already exists. Run with --reset to delete and create it again.")
            for company in existing:
                delete_company(company)
        result = create_demo()
        self.stdout.write(self.style.SUCCESS("Demo data created."))
        self.stdout.write("")
        for code, (company, user, password) in result.items():
            self.stdout.write(f"  {company.trade_name} ({code})")
            self.stdout.write(f"    username: {user.username}")
            self.stdout.write(f"    password: {password}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(
            "These passwords are shown only once and are for local testing only. "
            "Never use demo data on a live server."))
