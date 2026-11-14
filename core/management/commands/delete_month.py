from datetime import date

from django.core.management.base import BaseCommand, CommandError

from companies.models import Company
from core.formatting import indian_format
from payroll import service
from payroll.models import PayrollRun


def parse_month(value):
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except ValueError:
        raise CommandError(f"Write the month as YYYY-MM, for example 2026-10 (got {value!r}).")


class Command(BaseCommand):
    help = ("Delete one salary month for every company (or for the companies named), as if it was never started, "
            "so it can be done again: its employee lines, payments marked, advance recoveries (the advances are "
            "owed again), increments from its sheet and its uploaded sheets. Employees, advances and settings stay.")

    def add_arguments(self, parser):
        parser.add_argument("month", help="The month as YYYY-MM, for example 2026-10.")
        parser.add_argument("--company", action="append", default=[], metavar="CODE",
                            help="Only this company (repeat for more). Default: every company.")
        parser.add_argument("--dry-run", action="store_true", help="Only list what would be deleted.")

    def handle(self, *args, month, company, dry_run, **options):
        month = parse_month(month)
        companies = Company.objects.order_by("code")
        if company:
            codes = [c.upper() for c in company]
            companies = companies.filter(code__in=codes)
            unknown = sorted(set(codes) - set(companies.values_list("code", flat=True)))
            if unknown:
                raise CommandError(f"No company with code {', '.join(unknown)}.")
        runs = list(PayrollRun.objects.filter(company__in=companies, month=month).select_related("company")
                    .order_by("company__code"))
        found = {run.company_id for run in runs}
        label = runs[0].label if runs else f"{month:%m-%Y}"

        self.stdout.write(f"{'Would delete' if dry_run else 'Deleting'} {label} for {len(runs)} of "
                          f"{companies.count()} companies.")
        for c in companies:
            if c.pk not in found:
                self.stdout.write(f"  {c.code} {c.trade_name}: no {label} month")
        blocked = []
        for run in runs:
            s = service.delete_run_summary(run)
            self.stdout.write(
                f"  {s['company']} {run.company.trade_name}: {s['status']}, {s['lines']} employees, "
                f"{s['recoveries']} advance recoveries (₹{indian_format(s['recovered'])}), "
                f"{s['increments']} increments, {s['files']} uploaded sheets, "
                f"{s['later_drafts']} later draft months to work out again")
            for problem in service.delete_run_blockers(run):
                blocked.append(run.company.code)
                self.stdout.write(self.style.ERROR(f"    cannot delete: {problem}"))
        if blocked:
            raise CommandError(f"Nothing was deleted. Reopen the later finalised months of {', '.join(blocked)} "
                               "first, or leave those companies out with --company.")
        if dry_run or not runs:
            self.stdout.write("Dry run: nothing was deleted." if dry_run else "Nothing to delete.")
            return

        for run in runs:
            code = run.company.code
            _summary, problems = service.delete_run(run)
            self.stdout.write(self.style.SUCCESS(f"  {code}: {label} deleted"))
            for later, problem in problems:
                self.stdout.write(self.style.WARNING(f"    {later} could not be worked out again: {problem}"))
        self.stdout.write(self.style.SUCCESS(f"Done. {label} can now be started again."))
