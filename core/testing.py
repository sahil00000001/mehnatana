"""Shared test base: the README §16 demo businesses, logged in as the SRKS owner."""
from datetime import date

from django.test import TestCase

from core.demo import create_demo
from employees.models import Employee
from payroll import service
from payroll.models import PayrollRun

NOV = date(2026, 11, 1)


class DemoTestCase(TestCase):
    """SRKS (7 staff, draft November 2026) and ANPS (2 staff). self.client is logged in as SRKS."""

    @classmethod
    def setUpTestData(cls):
        demo = create_demo()
        cls.srks, cls.srks_user, cls.srks_password = demo["SRKS"]
        cls.anps, cls.anps_user, cls.anps_password = demo["ANPS"]

    def setUp(self):
        self.client.force_login(self.srks_user)

    def run_for(self, company=None, month=NOV):
        return PayrollRun.objects.get(company=company or self.srks, month=month)

    def finalise_november(self, company=None):
        company = company or self.srks
        user = self.srks_user if company == self.srks else self.anps_user
        return service.finalise(self.run_for(company), user)

    def employee(self, code, company=None):
        return Employee.objects.get(company=company or self.srks, emp_code=code)
