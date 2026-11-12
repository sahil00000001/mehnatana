"""Shared test base: the README §16 demo businesses, with the one login and SRKS open."""
from contextlib import contextmanager
from datetime import date
from unittest import mock

from django.test import TestCase

from accounts.models import User
from core.demo import create_demo
from core.tenancy import SESSION_COMPANY
from employees.models import Employee
from payroll import service
from payroll.models import PayrollRun

NOV = date(2026, 11, 1)


class DemoTestCase(TestCase):
    """SRKS (7 staff, draft November 2026) and ANPS (2 staff). self.client is logged in with the one
    login and has SRKS open; use open_company() to work on ANPS."""

    @classmethod
    def setUpTestData(cls):
        demo = create_demo()
        cls.srks, cls.anps = demo["SRKS"], demo["ANPS"]
        cls.user = User.objects.create_superuser("manager", password="manager-test-pass-1")
        cls.srks_user = cls.anps_user = cls.user  # who finalised or reopened a month in service calls

    def setUp(self):
        self.client.force_login(self.user)
        self.open_company(self.srks)

    def open_company(self, company):
        session = self.client.session
        session[SESSION_COMPANY] = company.pk
        session.save()

    def run_for(self, company=None, month=NOV):
        return PayrollRun.objects.get(company=company or self.srks, month=month)

    def finalise_november(self, company=None):
        return service.finalise(self.run_for(company or self.srks), self.user)

    @staticmethod
    @contextmanager
    def today_is(day):
        """Pretend today is `day` (a month can be finalised only after it has started)."""
        with mock.patch("django.utils.timezone.localdate", return_value=day):
            yield

    def employee(self, code, company=None):
        return Employee.objects.get(company=company or self.srks, emp_code=code)
