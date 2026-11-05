"""
Letters from plain-text templates (README §12).

The owner's text is plain text, never a Django template: a small whitelist
function replaces {{ placeholders }} and HTML-escapes everything (the
owner's words and the values). Unknown placeholders stay visible and are
reported as warnings. The appointment particulars table is made by code
and is always included.
"""
import re
from datetime import timedelta
from html import escape

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from companies.models import Company
from core.audit import audit
from core.formatting import amount_in_words, fmt_date, indian_format, mask_aadhaar, month_start, tax_year_label, tax_year_of
from employees.models import Employee

from .defaults import DEFAULT_TEXT
from .models import REF_CODES, IssuedLetter, LetterTemplate, LetterType

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_.]+)\s*\}\}")
ANY_BRACES_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
HALF_CLOSED_RE = re.compile(r"\{\{[^{}]*\}(?!\})")
TABLE_RE = re.compile(r"\{\{\s*letter\.particulars_table\s*\}\}")
TABLE_KEY = "letter.particulars_table"
REF_KEY = "letter.ref_no"
LINE_KEYS = {"letter.probation_line", "letter.resignation_line"}

# name -> what it prints (shown on the "Edit default text" page)
PLACEHOLDERS = [
    ("company.legal_name", gettext_lazy("Legal name of the business")),
    ("company.trade_name", gettext_lazy("Shop name printed on letters")),
    ("company.address", gettext_lazy("Full address")),
    ("company.city", gettext_lazy("City")),
    ("company.phone", gettext_lazy("Phone")),
    ("company.email", gettext_lazy("Email")),
    ("company.lin", gettext_lazy("Labour Identification Number")),
    ("company.signatory_name", gettext_lazy("Person who signs")),
    ("company.signatory_designation", gettext_lazy("Post of the person who signs")),
    ("emp.title", gettext_lazy("Mr. / Ms. / Mx.")),
    ("emp.full_name", gettext_lazy("Employee's full name")),
    ("emp.code", gettext_lazy("Employee code")),
    ("emp.relation_label", gettext_lazy("\"Father's name\", \"Mother's name\" or \"Spouse's name\"")),
    ("emp.relation_name", gettext_lazy("Father's / mother's / spouse's name")),
    ("emp.dob", gettext_lazy("Date of birth")),
    ("emp.doj", gettext_lazy("Date of joining")),
    ("emp.dol", gettext_lazy("Last working day")),
    ("emp.designation", gettext_lazy("Designation")),
    ("emp.department", gettext_lazy("Department")),
    ("emp.address", gettext_lazy("Employee's address")),
    ("emp.employment_type", gettext_lazy("Permanent, Probation, Fixed-term ...")),
    ("emp.skill_category", gettext_lazy("Unskilled, Semi-skilled, Skilled ...")),
    ("emp.nature_of_duties", gettext_lazy("Nature of duties")),
    ("emp.payment_mode", gettext_lazy("How salary is paid, e.g. bank transfer")),
    ("emp.he_she", gettext_lazy("he / she / they")),
    ("emp.his_her", gettext_lazy("his / her / their")),
    ("emp.him_her", gettext_lazy("him / her / them")),
    ("salary.basic", gettext_lazy("Basic per month")),
    ("salary.da", gettext_lazy("DA per month")),
    ("salary.hra", gettext_lazy("HRA per month")),
    ("salary.conveyance", gettext_lazy("Conveyance per month")),
    ("salary.other", gettext_lazy("Other allowance per month")),
    ("salary.gross", gettext_lazy("Total monthly salary")),
    ("salary.gross_words", gettext_lazy("Total monthly salary in words")),
    ("letter.ref_no", gettext_lazy("Reference number (given when you save)")),
    ("letter.date", gettext_lazy("Date of the letter")),
    ("letter.notice_days", gettext_lazy("Notice period in days")),
    ("letter.probation_line", gettext_lazy("A line about probation, or nothing if there is no probation")),
    ("letter.resignation_line", gettext_lazy("A line about the resignation letter, or nothing")),
    ("letter.particulars_table", gettext_lazy("Table of the 15 particulars (appointment letter; always printed)")),
]
KNOWN = {name for name, _desc in PLACEHOLDERS}

PRONOUNS = {"M": ("he", "his", "him"), "F": ("she", "her", "her"), "O": ("they", "their", "them")}
RELATION_LABELS = {"Father": "Father's name", "Mother": "Mother's name", "Spouse": "Spouse's name"}
PAYMENT_WORDS = {"Bank": "bank transfer", "Cash": "cash", "Cheque": "cheque", "UPI": "UPI"}
SLUGS = {"appointment": LetterType.APPOINTMENT, "relieving": LetterType.RELIEVING,
         "experience": LetterType.EXPERIENCE}
SLUG_OF = {value: slug for slug, value in SLUGS.items()}


# ---------------------------------------------------------------- templates and employees
def get_template(company, letter_type):
    template, _created = LetterTemplate.objects.get_or_create(
        company=company, letter_type=letter_type, defaults={"body": DEFAULT_TEXT[letter_type]})
    return template


def eligible_employees(company, letter_type):
    """Appointment letters for active staff; relieving and experience letters for relieved staff."""
    qs = Employee.objects.filter(company=company).select_related("designation").order_by("emp_code")
    if letter_type == LetterType.APPOINTMENT:
        return qs.filter(status=Employee.Status.ACTIVE)
    return qs.filter(status=Employee.Status.RELIEVED)


# ---------------------------------------------------------------- values
def probation_line(company):
    months = company.probation_months
    if not months:
        return ""
    unit = _("month") if months == 1 else _("months")
    return _("Probation: you will be on probation for %(n)s %(unit)s from the date of joining. "
             "Your confirmation will be given in writing.") % {"n": months, "unit": unit}


def resignation_line(emp):
    if not emp.resignation_date or emp.leaving_reason != Employee.LeavingReason.RESIGNED:
        return ""
    return _("This refers to your resignation letter dated %(date)s.") % {"date": fmt_date(emp.resignation_date)}


def salary_figures(emp, letter_date):
    rev = emp.structure_on(letter_date)
    if rev is None:
        return {"basic": 0, "da": 0, "hra": 0, "conveyance": 0, "other": 0, "gross": 0}
    return {"basic": rev.basic, "da": rev.da, "hra": rev.hra, "conveyance": rev.conveyance,
            "other": rev.other_allowance, "gross": rev.gross}


def build_values(company, emp, letter_date, ref_no=""):
    """Every placeholder except the particulars table, as plain (unescaped) text."""
    he, his, him = PRONOUNS.get(emp.gender, PRONOUNS["O"])
    pay = salary_figures(emp, letter_date)
    values = {
        "company.legal_name": company.legal_name,
        "company.trade_name": company.trade_name,
        "company.address": company.address,
        "company.city": company.city,
        "company.phone": company.phone,
        "company.email": company.email,
        "company.lin": company.lin,
        "company.signatory_name": company.signatory_name,
        "company.signatory_designation": company.signatory_designation,
        "emp.title": emp.title,
        "emp.full_name": emp.full_name,
        "emp.code": emp.emp_code,
        "emp.relation_label": RELATION_LABELS.get(emp.relation_type, "Father's name"),
        "emp.relation_name": emp.relation_name,
        "emp.dob": fmt_date(emp.date_of_birth),
        "emp.doj": fmt_date(emp.date_of_joining),
        "emp.dol": fmt_date(emp.date_of_leaving),
        "emp.designation": emp.designation.name if emp.designation_id else "",
        "emp.department": emp.department.name if emp.department_id else "",
        "emp.address": emp.address,
        "emp.employment_type": emp.get_employment_type_display(),
        "emp.skill_category": emp.get_skill_category_display(),
        "emp.nature_of_duties": emp.nature_of_duties,
        "emp.payment_mode": PAYMENT_WORDS.get(emp.payment_mode, emp.payment_mode),
        "emp.he_she": he,
        "emp.his_her": his,
        "emp.him_her": him,
        "salary.basic": indian_format(pay["basic"]),
        "salary.da": indian_format(pay["da"]),
        "salary.hra": indian_format(pay["hra"]),
        "salary.conveyance": indian_format(pay["conveyance"]),
        "salary.other": indian_format(pay["other"]),
        "salary.gross": indian_format(pay["gross"]),
        "salary.gross_words": amount_in_words(pay["gross"]),
        "letter.ref_no": ref_no,
        "letter.date": fmt_date(letter_date),
        "letter.notice_days": str(company.notice_period_days),
        "letter.probation_line": probation_line(company),
        "letter.resignation_line": resignation_line(emp),
    }
    return {k: str(v or "") for k, v in values.items()}


def _yes_no(flag):
    return _("Yes") if flag else _("No")


def esi_applies(company, emp, letter_date):
    from payroll.service import esi_covered_now
    from statutory.rates import MissingRate, rates_for_month

    month = month_start(letter_date)
    try:
        return esi_covered_now(emp, company, rates_for_month(month), month)
    except MissingRate:
        return False


def particulars_rows(company, emp, letter_date):
    """The 15 particulars of an appointment letter (OSH Code s.6, README §12.3)."""
    pay = salary_figures(emp, letter_date)
    ids = []
    if emp.uan:
        ids.append(_("UAN: %(uan)s") % {"uan": emp.uan})
    if emp.esic_ip_no:
        ids.append(_("ESIC IP: %(ip)s") % {"ip": emp.esic_ip_no})
    allowances = []
    for label, key in ((_("HRA"), "hra"), (_("Conveyance"), "conveyance"), (_("Other allowance"), "other")):
        if pay[key]:
            allowances.append(f"{label} ₹ {indian_format(pay[key])}")
    other_info = _("Nature of employment: %(type)s.") % {"type": emp.get_employment_type_display()}
    if emp.gender == "F":
        other_info += " " + _("Maternity benefits under the Code on Social Security, 2020 apply.")
    epf = company.pf_enabled and emp.pf_member
    rows = [
        (_("Name of employee"), emp.full_name),
        (_("Date of birth"), fmt_date(emp.date_of_birth)),
        (_("Father's / Mother's name"), emp.relation_name),
        (_("Aadhaar number (with consent)"), mask_aadhaar(emp.aadhaar) if emp.aadhaar else _("Not given")),
        (_("Labour Identification Number (LIN) of the establishment"), company.lin or _("Not given")),
        (_("UAN / ESIC insurance number (if available)"), " · ".join(ids) or _("Not available yet")),
        (_("Designation"), emp.designation.name if emp.designation_id else ""),
        (_("Category of skill"), emp.get_skill_category_display()),
        (_("Date of joining"), fmt_date(emp.date_of_joining)),
        (_("Wages: Basic pay and Dearness Allowance"),
         _("Basic ₹ %(basic)s + DA ₹ %(da)s per month") % {"basic": indian_format(pay["basic"]),
                                                           "da": indian_format(pay["da"])}),
        (_("Other allowances (including accommodation, if any)"),
         (" · ".join(allowances) + " " + _("per month")) if allowances else _("None")),
        (_("Avenue for achieving higher wages or higher position"), company.higher_wages_avenue),
        (_("Social security (EPFO / ESIC) applicable"),
         _("EPF: %(epf)s · ESI: %(esi)s") % {"epf": _yes_no(epf), "esi": _yes_no(esi_applies(company, emp, letter_date))}),
        (_("Broad nature of duties"), emp.nature_of_duties or _("As assigned by the employer.")),
        (_("Any other information"), other_info),
    ]
    return [(n, label, value) for n, (label, value) in enumerate(rows, start=1)]


def particulars_html(company, emp, letter_date):
    return render_to_string("letters/_particulars.html", {"rows": particulars_rows(company, emp, letter_date)})


# ---------------------------------------------------------------- text -> html
def _normalise(text):
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _drop_empty_line_placeholders(text, values):
    """A line that holds only a 'line' placeholder with nothing to say disappears."""
    lines = text.split("\n")
    kept, skip_next_blank = [], False
    for i, line in enumerate(lines):
        if skip_next_blank and not line.strip():
            skip_next_blank = False
            continue
        skip_next_blank = False
        match = PLACEHOLDER_RE.fullmatch(line.strip())
        if match and match.group(1) in LINE_KEYS and not values.get(match.group(1)):
            # a paragraph of its own: also drop one of the blank lines around it
            following_blank = i + 1 < len(lines) and not lines[i + 1].strip()
            skip_next_blank = bool(kept) and not kept[-1].strip() and following_blank
            continue
        kept.append(line)
    return "\n".join(kept)


def _fill(text, values, html, skip=()):
    out, pos = [], 0
    for match in PLACEHOLDER_RE.finditer(text):
        before = text[pos:match.start()]
        key = match.group(1)
        value = values[key] if key in values and key not in skip else match.group(0)
        out.append(escape(before, quote=False) if html else before)
        out.append(escape(value, quote=False) if html else value)
        pos = match.end()
    rest = text[pos:]
    out.append(escape(rest, quote=False) if html else rest)
    return "".join(out)


def unknown_placeholders(text):
    """Every {{ ... }} that is not on the placeholder list, as typed."""
    found = []
    for match in ANY_BRACES_RE.finditer(_normalise(text)):
        known = PLACEHOLDER_RE.fullmatch(match.group(0))
        if not known or known.group(1) not in KNOWN:
            shown = "{{ " + " ".join(match.group(1).split()) + " }}"
            if shown not in found:
                found.append(shown)
    for match in HALF_CLOSED_RE.finditer(_normalise(text)):  # {{ emp.full_name }  (one closing brace)
        if match.group(0) not in found:
            found.append(match.group(0))
    return found


def text_for_editing(text, values):
    """The template with every placeholder filled in except the reference number and the table."""
    text = _drop_empty_line_placeholders(_normalise(text), values)
    return _fill(text, values, html=False, skip=(REF_KEY, TABLE_KEY))


def render_body(text, values, table_html=""):
    """Plain text -> safe HTML paragraphs. Returns (html, unknown placeholders).

    When table_html is given (appointment letters) the table is always printed:
    where {{ letter.particulars_table }} stands, or else after the paragraph that
    mentions "the following terms", else before the first numbered term, else at the end.
    """
    text = _drop_empty_line_placeholders(_normalise(text), values)
    warnings = unknown_placeholders(text)
    blocks, table_done = [], False
    # Separators are kept: each extra blank line becomes vertical space (room to sign).
    parts = re.split(r"(\n(?:[ \t]*\n)+)", text.strip("\n"))
    for index, paragraph in enumerate(parts):
        if index % 2:
            extra = paragraph.count("\n") - 2
            if blocks and extra > 0:
                blocks.extend([("gap", "")] * extra)
            continue
        if not paragraph.strip():
            continue
        pieces = TABLE_RE.split(paragraph)
        for i, piece in enumerate(pieces):
            if i > 0 and table_html and not table_done:
                blocks.append(("table", table_html))
                table_done = True
            piece = piece.strip("\n")
            if piece.strip():
                blocks.append(("p", piece))
    if table_html and not table_done:
        where = len(blocks)
        for i, (kind, piece) in enumerate(blocks):
            if kind == "p" and "following terms" in piece.lower():
                where = i + 1
                break
        else:
            for i, (kind, piece) in enumerate(blocks):
                if kind == "p" and re.match(r"\s*1\.", piece):
                    where = i
                    break
        blocks.insert(where, ("table", table_html))
    html = []
    for kind, piece in blocks:
        if kind == "table":
            html.append(piece)
        elif kind == "gap":
            html.append('<div class="letter-gap"></div>')
        else:
            html.append(f"<p>{_fill(piece, values, html=True, skip=(TABLE_KEY,))}</p>")
    return "\n".join(html), warnings


def letterhead_html(company):
    return render_to_string("letters/_letterhead.html", {"company": company})


def letter_html(company, emp, letter_type, letter_date, text, ref_no):
    """Letterhead + body exactly as it will be printed. Returns (html, warnings)."""
    values = build_values(company, emp, letter_date, ref_no)
    table = particulars_html(company, emp, letter_date) if letter_type == LetterType.APPOINTMENT else ""
    body, warnings = render_body(text, values, table)
    return f'{letterhead_html(company)}\n<div class="letter-body">\n{body}\n</div>', warnings


# ---------------------------------------------------------------- reference numbers and issuing
def next_reference(company, letter_type, issue_date):
    """(tax year, running number, 'SRKS/APT/2026-27/0001') for the next letter of this type."""
    year = tax_year_of(issue_date)
    last = (IssuedLetter.objects.filter(company=company, letter_type=letter_type, tax_year=year)
            .aggregate(m=Max("seq"))["m"] or 0)
    seq = last + 1
    return year, seq, f"{company.code}/{REF_CODES[letter_type]}/{tax_year_label(year)}/{seq:04d}"


def same_letter_just_saved(company, emp, letter_type, issue_date, text):
    """A letter with exactly this text saved in the last 10 minutes (Save pressed twice), or None.

    A corrected text is a new letter, so the comparison is on the printed letter, not only the person and date.
    """
    recent = IssuedLetter.objects.filter(company=company, employee=emp, letter_type=letter_type, issue_date=issue_date,
                                         created_at__gte=timezone.now() - timedelta(minutes=10))
    for old in recent:
        if letter_html(company, emp, letter_type, issue_date, text, old.ref_no)[0] == old.body_snapshot_html:
            return old
    return None


def issue_letter(request, company, emp, letter_type, issue_date, text):
    """Save and number a letter. Returns (letter, created); created is False when Save was pressed twice."""
    for _attempt in range(3):
        try:
            with transaction.atomic():
                Company.objects.select_for_update().filter(pk=company.pk).first()  # one number at a time
                old = same_letter_just_saved(company, emp, letter_type, issue_date, text)
                if old:
                    return old, False
                year, seq, ref_no = next_reference(company, letter_type, issue_date)
                html, _warnings = letter_html(company, emp, letter_type, issue_date, text, ref_no)
                letter = IssuedLetter.objects.create(
                    company=company, employee=emp, letter_type=letter_type, ref_no=ref_no, tax_year=year, seq=seq,
                    issue_date=issue_date, body_snapshot_html=html, issued_by=request.user, created_by=request.user,
                )
                audit(request, "letter.issue", letter, {"ref_no": ref_no, "employee": emp.emp_code,
                                                        "letter_type": letter_type,
                                                        "issue_date": str(issue_date)})
                return letter, True
        except IntegrityError:
            continue
    raise IntegrityError("Could not give the letter a reference number. Please try again.")
