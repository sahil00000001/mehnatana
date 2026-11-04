from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import CompanyOwned


class LetterType(models.TextChoices):
    APPOINTMENT = "APPOINTMENT", _("Appointment letter")
    RELIEVING = "RELIEVING", _("Relieving letter")
    EXPERIENCE = "EXPERIENCE", _("Experience letter")


# Used in reference numbers: SRKS/APT/2026-27/0001
REF_CODES = {
    LetterType.APPOINTMENT: "APT",
    LetterType.RELIEVING: "REL",
    LetterType.EXPERIENCE: "EXP",
}


class LetterTemplate(CompanyOwned):
    """The company's default text for one letter type (plain text with {{ placeholders }})."""

    letter_type = models.CharField(max_length=12, choices=LetterType.choices)
    body = models.TextField()

    class Meta:
        db_table = "mtn_letter_template"
        constraints = [models.UniqueConstraint(fields=["company", "letter_type"], name="mtn_letter_template_unique")]

    def __str__(self):
        return f"{self.company.code} {self.get_letter_type_display()}"


class IssuedLetter(CompanyOwned):
    """A letter as it was issued. Reprinting shows exactly this text."""

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="letters")
    letter_type = models.CharField(max_length=12, choices=LetterType.choices)
    ref_no = models.CharField(max_length=40)
    tax_year = models.PositiveSmallIntegerField()
    seq = models.PositiveIntegerField()
    issue_date = models.DateField()
    body_snapshot_html = models.TextField()
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    class Meta:
        db_table = "mtn_issued_letter"
        ordering = ["-issue_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["company", "letter_type", "tax_year", "seq"],
                                    name="mtn_issued_letter_seq_unique"),
        ]

    def __str__(self):
        return self.ref_no
