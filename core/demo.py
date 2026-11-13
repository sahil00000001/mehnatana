"""
Demo data (README §16): Shri Ram Kirana Stores (SRKS, Bengaluru) and
Annapurna Sweets (ANPS, Pune), each with a draft November 2026 salary month.
Used by `python manage.py seed_demo` and by the tests.
"""
from datetime import date
from decimal import Decimal as D

from django.db import transaction

from advances.models import Advance, AdvanceRecovery
from companies.models import Company
from core.models import AuditLog, StoredFile
from employees.models import Department, Designation, Employee, OpeningBalance, SalaryRevision
from letters.models import IssuedLetter, LetterTemplate
from payroll import service
from payroll.models import PayrollLine, PayrollRun

NOV_2026 = date(2026, 11, 1)

SRKS_STAFF = [
    # code, first, surname, gender, dob, doj, designation, department, reports_to, relation, relation name,
    # structure (basic, da, hra, conv, other), extra fields, November inputs
    ("E001", "Ramesh", "Kumar", "M", date(1995, 6, 15), date(2022, 4, 1), "Helper", "Store", "E003",
     "Father", "Suresh Kumar", (12000, 0, 0, 0, 0), {},
     dict(present=26, week_off_holiday=4, ot_hours=10, increment=500, advance=1000)),
    ("E002", "Sunita", "Devi", "F", date(1990, 2, 20), date(2021, 7, 1), "Cashier", "Store", "E003",
     "Father", "Ram Lal", (9000, 0, 6000, 2000, 1000),
     {"pan": "ABCPD1234E", "aadhaar": "234567891234", "aadhaar_consent": True, "skill_category": "Semi-skilled",
      "nature_of_duties": "Billing at the counter, handling cash and keeping the daily cash book."},
     dict(present=22, paid_leave=1, week_off_holiday=4, lop=3)),
    ("E003", "Mohd.", "Arif", "M", date(1985, 11, 5), date(2018, 1, 10), "Store Manager", "Store", None,
     "Father", "Mohd. Yusuf", (20000, 0, 10000, 0, 10000), {"skill_category": "Highly skilled"},
     dict(present=26, week_off_holiday=4)),
    ("E004", "Priya", "Sharma", "F", date(1992, 8, 30), date(2026, 4, 1), "Accountant", "Accounts", "E007",
     "Father", "Anil Sharma", (60000, 0, 30000, 0, 20000),
     {"eps_member": False, "pan": "ABCPS1234F", "skill_category": "Highly skilled"},
     dict(present=26, week_off_holiday=4)),
    ("E005", "Gopal", "Rao", "M", date(1967, 5, 10), date(2015, 8, 1), "Senior Salesman", "Store", "E003",
     "Father", "Venkat Rao", (14000, 0, 4000, 0, 2000), {"skill_category": "Skilled"},
     dict(present=26, week_off_holiday=4)),
    ("E006", "Kiran", "B", "M", date(2003, 1, 25), date(2026, 11, 16), "Delivery Boy", "Delivery", "E003",
     "Father", "Basavaraj", (8000, 0, 6000, 4000, 0), {"payment_mode": "Cash"},
     dict(present=13, week_off_holiday=2)),
    ("E007", "Neha", "Joshi", "F", date(1988, 3, 12), date(2024, 6, 1), "Branch Manager", "Store", None,
     "Father", "Vinod Joshi", (50000, 0, 25000, 0, 15000),
     {"eps_member": False, "tax_regime": "OLD", "old_regime_deductions_annual": D(200000), "pan": "ABCPJ1234K",
      "skill_category": "Highly skilled"},
     dict(present=26, week_off_holiday=4)),
]
SRKS_ESI_COVERED = {"E001", "E002", "E005", "E006"}
SRKS_OPENING = {"E004": (770000, 27300, 1400), "E007": (630000, 47320, 1400)}

ANPS_STAFF = [
    ("A001", "Seema", "Patil", "F", date(1991, 4, 12), date(2023, 6, 1), "Sweet Maker", "Kitchen", None,
     "Father", "Sunil Patil", (15000, 0, 0, 0, 0), {}, dict(present=26, week_off_holiday=4)),
    ("A002", "Rahul", "More", "M", date(1996, 9, 3), date(2024, 1, 15), "Counter Salesman", "Counter", None,
     "Father", "Prakash More", (9500, 0, 0, 0, 0), {}, dict(present=26, week_off_holiday=4)),
]


def _staff(company, rows, uan_prefix, esi_prefix, esi_covered, pf_on):
    depts, desigs, people = {}, {}, {}
    for (code, first, surname, gender, dob, doj, designation, department, _boss, rel, rel_name,
         structure, extra, _inputs) in rows:
        if department not in depts:
            depts[department] = Department.objects.create(company=company, name=department)
        if designation not in desigs:
            desigs[designation] = Designation.objects.create(company=company, name=designation)
        n = code[-2:]
        fields = dict(
            company=company, emp_code=code, first_name=first, surname=surname, gender=gender,
            date_of_birth=dob, date_of_joining=doj, designation=desigs[designation], department=depts[department],
            relation_type=rel, relation_name=rel_name, mobile=f"98450{code[-3:].zfill(3)}12"[:10],
            address=f"{int(n) * 7}, {'4th Cross, Jayanagar, Bengaluru 560041' if company.code == 'SRKS' else 'Kothrud, Pune 411038'}",
            uan=f"{uan_prefix}{n}" if pf_on else "", pf_member=pf_on, eps_member=pf_on,
            esic_ip_no=f"{esi_prefix}{n}" if code in esi_covered else "",
            payment_mode="Bank", bank_name="State Bank of India", bank_account_no=f"12345678{n}",
            ifsc="SBIN0001234", nature_of_duties="Work at the shop as assigned by the manager.",
        )
        fields.update(extra)
        if fields["payment_mode"] != "Bank":
            fields.update(bank_name="", bank_account_no="", ifsc="")
        emp = Employee(**fields)
        emp.full_clean(exclude=["reports_to"])
        emp.save()
        basic, da, hra, conv, other = structure
        SalaryRevision.objects.create(
            company=company, employee=emp, effective_from=max(company.payroll_start_month, date(doj.year, doj.month, 1)),
            basic=basic, da=da, hra=hra, conveyance=conv, other_allowance=other,
            source=SalaryRevision.Source.OPENING, reason="Salary when starting to use the app",
        )
        people[code] = emp
    for row in rows:
        code, boss = row[0], row[8]
        if boss:
            people[code].reports_to = people[boss]
            people[code].save(update_fields=["reports_to"])
    return people


def _inputs(rows, people):
    out = {}
    for row in rows:
        values = {k: D(v) for k, v in row[13].items()}
        out[people[row[0]].pk] = service.RowInput(**values)
    return out


DEMO_CODES = ("SRKS", "ANPS")


def demo_companies():
    """The demo businesses: their code and the DEMO- labour number together, so a real business is never touched."""
    return Company.objects.filter(code__in=DEMO_CODES, lin__startswith="DEMO-LIN-")


@transaction.atomic
def create_demo():
    """Create both demo companies with staff and a draft November 2026. Returns {code: company}.

    There are no business logins: the one login opens every company.
    """
    srks = Company.objects.create(
        code="SRKS", legal_name="Shri Ram Kirana Stores", trade_name="Shri Ram Kirana Stores",
        address_line1="12, Main Road, Jayanagar", city="Bengaluru", state="KA", pincode="560041",
        phone="080 2654 1234", email="shriramkirana@example.com", lin="DEMO-LIN-0001",
        pf_establishment_code="KNBNG0012345000", esic_employer_code="53000123450001001",
        signatory_name="Ram Prasad Gupta", signatory_designation="Proprietor",
        payroll_start_month=NOV_2026, pf_enabled=True, pf_employee_rate=D("12.00"), pf_restrict_to_ceiling=True,
        esi_enabled=True, pt_enabled=True, pt_state="KA", tds_enabled=True, salary_divisor="CALENDAR",
        ot_multiplier=D(2), ot_days_divisor=26, ot_hours_per_day=D(8),
        amount_paid_mode="OFF",  # the README demo pays each salary head as entered
    )
    srks_people = _staff(srks, SRKS_STAFF, "1012345678", "12345678", SRKS_ESI_COVERED, pf_on=True)
    for code, (gross, tds, pt) in SRKS_OPENING.items():
        OpeningBalance.objects.create(company=srks, employee=srks_people[code], tax_year=2026,
                                      ytd_gross=gross, ytd_tds=tds, ytd_pt=pt)
    Advance.objects.create(company=srks, employee=srks_people["E001"], date_given=date(2026, 10, 10),
                           amount=5000, monthly_installment=1000, note="Festival advance")

    anps = Company.objects.create(
        code="ANPS", legal_name="Annapurna Sweets", trade_name="Annapurna Sweets",
        address_line1="45, Karve Road, Kothrud", city="Pune", state="MH", pincode="411038",
        phone="020 2543 9876", email="annapurna@example.com", lin="DEMO-LIN-0002",
        signatory_name="Sushila Kulkarni", signatory_designation="Partner",
        payroll_start_month=NOV_2026, pf_enabled=False, esi_enabled=True, pt_enabled=True, pt_state="MH",
        tds_enabled=True, amount_paid_mode="OFF",
    )
    anps_people = _staff(anps, ANPS_STAFF, "", "22345678", {"A001", "A002"}, pf_on=False)

    service.save_inputs(srks, NOV_2026, _inputs(SRKS_STAFF, srks_people), None)
    service.save_inputs(anps, NOV_2026, _inputs(ANPS_STAFF, anps_people), None)
    return {"SRKS": srks, "ANPS": anps}


@transaction.atomic
def delete_company(company):
    """Remove every row of one company (used only to reset demo data)."""
    AdvanceRecovery.objects.filter(company=company).delete()
    IssuedLetter.objects.filter(company=company).delete()
    LetterTemplate.objects.filter(company=company).delete()
    SalaryRevision.objects.filter(company=company).delete()
    PayrollLine.objects.filter(company=company).delete()
    PayrollRun.objects.filter(company=company).delete()
    Advance.objects.filter(company=company).delete()
    OpeningBalance.objects.filter(company=company).delete()
    Employee.objects.filter(company=company).update(reports_to=None)
    Employee.objects.filter(company=company).delete()
    Department.objects.filter(company=company).delete()
    Designation.objects.filter(company=company).delete()
    AuditLog.objects.filter(company=company).delete()
    StoredFile.objects.filter(name__startswith=f"private/uploads/{company.code}/").delete()
    if company.logo:
        company.logo.delete(save=False)
    company.delete()
