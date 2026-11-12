"""Create the one login (it opens every company) from environment variables if it does not exist yet.

Used by the deployment job: DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_PASSWORD and
(optionally) DJANGO_SUPERUSER_EMAIL. An existing login is never changed.
"""
import os

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create the one login (a superuser) from DJANGO_SUPERUSER_* environment variables, once."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        if not username:
            self.stdout.write("DJANGO_SUPERUSER_USERNAME is not set: no login created.")
            return
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Login '{username}' already exists: left unchanged.")
            return
        if len(password) < 12:
            raise CommandError("DJANGO_SUPERUSER_PASSWORD must have at least 12 characters.")
        User.objects.create_superuser(username=username, password=password,
                                      email=os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip())
        self.stdout.write(self.style.SUCCESS(f"Login '{username}' created."))
