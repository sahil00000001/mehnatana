"""Letters (README §12 and the letters part of §17.3)."""
from datetime import date

from django.urls import reverse

from core.models import AuditLog
from core.testing import DemoTestCase
from employees.models import Employee

from .defaults import DEFAULT_TEXT
from .models import IssuedLetter, LetterTemplate, LetterType
from .render import get_template

PARTICULARS = [
    "Name of employee", "Date of birth", "Father&#x27;s / Mother&#x27;s name", "Aadhaar number (with consent)",
    "Labour Identification Number (LIN) of the establishment", "UAN / ESIC insurance number (if available)",
    "Designation", "Category of skill", "Date of joining", "Wages: Basic pay and Dearness Allowance",
    "Other allowances (including accommodation, if any)", "Avenue for achieving higher wages or higher position",
    "Social security (EPFO / ESIC) applicable", "Broad nature of duties", "Any other information",
]


class LettersTest(DemoTestCase):
    def new_url(self, slug="appointment"):
        return reverse("letters:new", args=[slug])

    def preview(self, emp, slug="appointment", when="2026-11-20"):
        return self.client.get(self.new_url(slug), {"employee": emp.pk, "date": when})

    def issue(self, emp, slug="appointment", when="2026-11-20", body=None):
        if body is None:
            body = self.preview(emp, slug, when).context["text"]
        response = self.client.post(self.new_url(slug), {"employee": emp.pk, "date": when, "body": body,
                                                          "action": "save"})
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.content[:500])
        return IssuedLetter.objects.latest("id")

    def relieve(self, code, left=date(2026, 11, 30), resigned=None):
        emp = self.employee(code)
        Employee.objects.filter(pk=emp.pk).update(status=Employee.Status.RELIEVED, date_of_leaving=left,
                                                  leaving_reason="Resigned", resignation_date=resigned)
        return self.employee(code)

    # ---------------------------------------------------------------- appointment
    def test_appointment_letter_for_sunita(self):
        e002 = self.employee("E002")
        response = self.preview(e002)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SRKS/APT/2026-27/0001")
        letter = self.issue(e002)
        html = letter.body_snapshot_html
        self.assertEqual(letter.ref_no, "SRKS/APT/2026-27/0001")
        self.assertEqual((letter.tax_year, letter.seq, letter.issue_date), (2026, 1, date(2026, 11, 20)))
        for label in PARTICULARS:
            self.assertIn(label, html)
        self.assertEqual(html.count('<td class="sl">'), 15)
        self.assertIn("Maternity benefits under the Code on Social Security, 2020 apply.", html)
        self.assertIn("XXXX XXXX 1234", html)
        self.assertNotIn("234567891234", html)
        self.assertIn("DEMO-LIN-0001", html)
        self.assertIn("Basic ₹ 9,000 + DA ₹ 0 per month", html)
        self.assertIn("EPF: Yes · ESI: Yes", html)
        self.assertIn("UAN: 101234567802 · ESIC IP: 1234567802", html)
        self.assertIn("Ms. Sunita Devi", html)
        self.assertIn("Ref: SRKS/APT/2026-27/0001", html)
        self.assertIn("Date: 20-11-2026", html)
        self.assertIn("by bank transfer", html)
        self.assertIn("Shri Ram Kirana Stores", html)  # letterhead
        self.assertNotIn("{{", html)
        self.assertIn("Phone: 080 2654 1234 · Email: shriramkirana@example.com", html)
        self.assertIn('<p>For Shri Ram Kirana Stores</p>\n<div class="letter-gap"></div>', html)
        self.assertTrue(AuditLog.objects.filter(action="letter.issue", object_id=str(letter.pk)).exists())

        second = self.issue(self.employee("E001"))
        self.assertEqual(second.ref_no, "SRKS/APT/2026-27/0002")
        self.assertNotIn("Maternity", second.body_snapshot_html)
        self.assertIn("Mr. Ramesh Kumar", second.body_snapshot_html)

    def test_new_tax_year_starts_again_at_0001(self):
        self.issue(self.employee("E002"))
        letter = self.issue(self.employee("E002"), when="2027-04-02")
        self.assertEqual(letter.ref_no, "SRKS/APT/2027-28/0001")

    def test_particulars_table_cannot_be_removed(self):
        body = self.preview(self.employee("E002")).context["text"].replace("{{ letter.particulars_table }}", "")
        letter = self.issue(self.employee("E002"), body=body)
        self.assertIn('class="print particulars"', letter.body_snapshot_html)
        self.assertIn("Any other information", letter.body_snapshot_html)

    def test_probation_line_only_when_set(self):
        text = self.preview(self.employee("E002")).context["text"]
        self.assertNotIn("probation", text.lower())
        self.assertIn("same town.\n2. Working hours", text)
        self.srks.probation_months = 6
        self.srks.save()
        text = self.preview(self.employee("E002")).context["text"]
        self.assertIn("you will be on probation for 6 months", text)

    def test_preview_textarea_keeps_ref_and_table_placeholders(self):
        text = self.preview(self.employee("E002")).context["text"]
        self.assertIn("{{ letter.ref_no }}", text)
        self.assertIn("{{ letter.particulars_table }}", text)
        self.assertIn("Dear Sunita Devi,", text)

    # ---------------------------------------------------------------- safety
    def test_unknown_placeholder_warns_and_stays_visible(self):
        template = get_template(self.srks, LetterType.APPOINTMENT)
        template.body = "Dear {{ emp.full_name }},\n\nYour nickname is {{ emp.nickname }}."
        template.save()
        response = self.preview(self.employee("E002"))
        self.assertEqual(response.context["warnings"], ["{{ emp.nickname }}"])
        self.assertContains(response, "are not known")
        self.assertContains(response, "Your nickname is {{ emp.nickname }}.")
        letter = self.issue(self.employee("E002"))
        self.assertIn("{{ emp.nickname }}", letter.body_snapshot_html)

    def test_owner_html_is_escaped(self):
        letter = self.issue(self.employee("E002"), body='Hello <script>alert("x")</script> & <b>bold</b>')
        html = letter.body_snapshot_html
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)

    def test_employee_values_are_escaped(self):
        e002 = self.employee("E002")
        Employee.objects.filter(pk=e002.pk).update(address="<img src=x onerror=alert(1)>")
        letter = self.issue(self.employee("E002"))
        self.assertNotIn("<img src=x", letter.body_snapshot_html)
        self.assertIn("&lt;img src=x", letter.body_snapshot_html)

    # ---------------------------------------------------------------- relieving / experience
    def test_relieving_and_experience_list_only_relieved_staff(self):
        self.relieve("E005", resigned=date(2026, 10, 15))
        for slug in ("relieving", "experience"):
            response = self.client.get(self.new_url(slug))
            choices = list(response.context["form"].fields["employee"].queryset.values_list("emp_code", flat=True))
            self.assertEqual(choices, ["E005"], slug)
            refused = self.preview(self.employee("E002"), slug)
            self.assertEqual(refused.status_code, 200)
            self.assertTrue(refused.context["form"].errors)
            self.assertNotIn("preview", refused.context)
        appointment = self.client.get(self.new_url("appointment")).context["form"].fields["employee"].queryset
        self.assertNotIn("E005", appointment.values_list("emp_code", flat=True))

    def test_relieving_and_experience_text(self):
        e005 = self.relieve("E005", resigned=date(2026, 10, 15))
        rel = self.issue(e005, "relieving", "2026-12-01")
        self.assertEqual(rel.ref_no, "SRKS/REL/2026-27/0001")
        self.assertIn("This refers to your resignation letter dated 15-10-2026.", rel.body_snapshot_html)
        self.assertIn("close of working hours on 30-11-2026", rel.body_snapshot_html)
        self.assertIn("from 01-08-2015 to 30-11-2026", rel.body_snapshot_html)
        exp = self.issue(e005, "experience", "2026-12-01")
        self.assertEqual(exp.ref_no, "SRKS/EXP/2026-27/0001")
        self.assertIn("(Father's name: Venkat Rao)", exp.body_snapshot_html)
        self.assertIn("During this period his work", exp.body_snapshot_html)
        self.assertIn("We wish him all the best.", exp.body_snapshot_html)
        self.assertNotIn("particulars", exp.body_snapshot_html)

    def test_no_resignation_line_when_not_resigned(self):
        e005 = self.relieve("E005")
        letter = self.issue(e005, "relieving", "2026-12-01")
        self.assertNotIn("resignation letter", letter.body_snapshot_html)
        self.assertNotIn("<p></p>", letter.body_snapshot_html)

    # ---------------------------------------------------------------- print view
    def test_print_view_one_and_two_copies(self):
        letter = self.issue(self.employee("E002"))
        url = reverse("letters:detail", args=[letter.pk])
        one = self.client.get(url)
        self.assertContains(one, "SRKS/APT/2026-27/0001")
        self.assertContains(one, "Page 1 of 1", count=1)
        self.assertNotContains(one, "OFFICE COPY")
        two = self.client.get(url, {"copies": "2"})
        self.assertContains(two, "OFFICE COPY")
        self.assertContains(two, "EMPLOYEE COPY")
        self.assertContains(two, "Page 1 of 1", count=2)

    def test_reprint_shows_the_stored_text(self):
        letter = self.issue(self.employee("E002"))
        Employee.objects.filter(pk=self.employee("E002").pk).update(first_name="Changed")
        response = self.client.get(reverse("letters:detail", args=[letter.pk]))
        self.assertContains(response, "Sunita Devi")
        self.assertNotContains(response, "Changed Devi")

    def test_index_lists_history(self):
        letter = self.issue(self.employee("E002"))
        response = self.client.get(reverse("letters:index"))
        self.assertContains(response, letter.ref_no)
        self.assertContains(response, reverse("letters:detail", args=[letter.pk]))

    def test_unknown_letter_type_is_404(self):
        self.assertEqual(self.client.get(self.new_url("warning")).status_code, 404)
        self.assertEqual(self.client.get(reverse("letters:template_edit", args=["warning"])).status_code, 404)

    # ---------------------------------------------------------------- isolation
    def test_other_company_is_404(self):
        a001 = self.employee("A001", self.anps)
        theirs = IssuedLetter.objects.create(
            company=self.anps, employee=a001, letter_type=LetterType.APPOINTMENT, ref_no="ANPS/APT/2026-27/0001",
            tax_year=2026, seq=1, issue_date=date(2026, 11, 20), body_snapshot_html="<p>secret</p>")
        self.assertEqual(self.client.get(reverse("letters:detail", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.preview(a001).status_code, 404)
        response = self.client.post(self.new_url(), {"employee": a001.pk, "date": "2026-11-20", "body": "x",
                                                     "action": "save"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(IssuedLetter.objects.filter(company=self.srks).count(), 0)
        self.assertNotContains(self.client.get(reverse("letters:index")), "ANPS/APT")
        self.assertEqual(self.client.get(self.new_url(), {"employee": "abc"}).status_code, 404)

    # ---------------------------------------------------------------- default text
    def test_edit_and_reset_default_text(self):
        url = reverse("letters:template_edit", args=["appointment"])
        self.assertContains(self.client.get(url), "{{ letter.particulars_table }}")
        self.client.post(url, {"action": "save", "body": "Custom text for {{ emp.full_name }}."})
        template = LetterTemplate.objects.get(company=self.srks, letter_type=LetterType.APPOINTMENT)
        self.assertEqual(template.body, "Custom text for {{ emp.full_name }}.")
        self.assertIn("Custom text for Sunita Devi.", self.preview(self.employee("E002")).context["text"])
        self.client.post(url, {"action": "reset"})
        template.refresh_from_db()
        self.assertEqual(template.body, DEFAULT_TEXT[LetterType.APPOINTMENT])
        self.assertEqual(AuditLog.objects.filter(action="letter.template_edit").count(), 2)

    def test_edit_page_warns_about_unknown_placeholders(self):
        url = reverse("letters:template_edit", args=["experience"])
        response = self.client.post(url, {"action": "save", "body": "Hello {{ emp.fullname }} {{ Emp.Name }}"})
        self.assertEqual(response.status_code, 302)
        response = self.client.get(url)
        self.assertEqual(response.context["warnings"], ["{{ emp.fullname }}", "{{ Emp.Name }}"])
