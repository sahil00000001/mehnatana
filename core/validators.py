from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

pan_validator = RegexValidator(r"^[A-Z]{5}[0-9]{4}[A-Z]$", _("PAN must look like ABCDE1234F (5 letters, 4 digits, 1 letter)."))
tan_validator = RegexValidator(r"^[A-Z]{4}[0-9]{5}[A-Z]$", _("TAN must look like BLRS12345A (4 letters, 5 digits, 1 letter)."))
gstin_validator = RegexValidator(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", _("GSTIN must have 15 characters, like 29ABCDE1234F1Z5."))
uan_validator = RegexValidator(r"^[0-9]{12}$", _("UAN must be exactly 12 digits."))
esic_validator = RegexValidator(r"^[0-9]{10}$", _("ESIC IP number must be exactly 10 digits."))
aadhaar_validator = RegexValidator(r"^[0-9]{12}$", _("Aadhaar number must be exactly 12 digits."))
ifsc_validator = RegexValidator(r"^[A-Z]{4}0[A-Z0-9]{6}$", _("IFSC must look like SBIN0001234 (4 letters, a zero, then 6 letters or digits)."))
mobile_validator = RegexValidator(r"^[6-9][0-9]{9}$", _("Mobile number must be 10 digits and start with 6, 7, 8 or 9."))
pincode_validator = RegexValidator(r"^[0-9]{6}$", _("PIN code must be 6 digits."))
company_code_validator = RegexValidator(r"^[A-Z]{3,6}$", _("Use 3 to 6 capital letters, like SRKS."))


def digits_only(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def upper_no_spaces(value):
    return "".join(str(value or "").split()).upper()
