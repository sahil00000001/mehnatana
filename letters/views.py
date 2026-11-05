from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.audit import audit
from core.tenancy import company_get, owner_required, page
from employees.models import Employee

from .defaults import DEFAULT_TEXT
from .forms import ChooseForm, TemplateForm
from .models import IssuedLetter, LetterType
from .render import (
    PLACEHOLDERS, SLUG_OF, SLUGS, build_values, get_template, issue_letter, letter_html,
    next_reference, text_for_editing, unknown_placeholders,
)

FOR_WHOM = {
    LetterType.APPOINTMENT: gettext_lazy("Every new employee (compulsory under the OSH Code)"),
    LetterType.RELIEVING: gettext_lazy("Staff who have left"),
    LetterType.EXPERIENCE: gettext_lazy("Staff who have left"),
}


def _letter_type(slug):
    try:
        return SLUGS[slug]
    except KeyError:
        raise Http404


def _employee(request, pk):
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        raise Http404
    return company_get(request, Employee.objects.select_related("designation", "department"), pk=pk)


@owner_required
def index(request):
    types = [{"slug": SLUG_OF[value], "label": label, "for_whom": FOR_WHOM[value]}
             for value, label in LetterType.choices]
    letters = IssuedLetter.objects.filter(company=request.company).select_related("employee")
    return page(request, "letters/index.html", _("Letters"),
                _("Choose the letter you want to write. Letters already issued are listed below for reprinting."),
                types=types, letters=letters)


@owner_required
def new(request, letter_type):
    ltype = _letter_type(letter_type)
    company = request.company
    label = LetterType(ltype).label
    data = request.POST if request.method == "POST" else request.GET
    today = timezone.localdate()

    if "employee" not in data:
        form = ChooseForm(company, ltype, initial={"date": today})
        return page(request, "letters/choose.html", label,
                    _("Choose the employee and the date of the letter, then press Show the letter."),
                    form=form, slug=letter_type, letter_label=label)

    if data.get("employee"):
        _employee(request, data.get("employee"))  # another company's employee -> 404
    form = ChooseForm(company, ltype, data={"employee": data.get("employee"), "date": data.get("date") or today})
    if not form.is_valid():
        return page(request, "letters/choose.html", label,
                    _("Choose the employee and the date of the letter, then press Show the letter."),
                    form=form, slug=letter_type, letter_label=label)
    emp = _employee(request, form.cleaned_data["employee"].pk)
    issue_date = form.cleaned_data["date"]

    template = get_template(company, ltype)
    values = build_values(company, emp, issue_date)
    text = request.POST.get("body", "") if request.method == "POST" else ""
    if not text.strip():
        text = text_for_editing(template.body, values)

    if request.method == "POST" and request.POST.get("action") == "save":
        letter, created = issue_letter(request, company, emp, ltype, issue_date, text)
        if created:
            messages.success(request, _("Letter %(ref)s is saved. Print it now.") % {"ref": letter.ref_no})
        else:  # Save pressed twice with the same text
            messages.info(request, _("This letter was already saved as %(ref)s.") % {"ref": letter.ref_no})
        return redirect("letters:detail", pk=letter.pk)

    _year, _seq, next_ref = next_reference(company, ltype, issue_date)
    preview, warnings = letter_html(company, emp, ltype, issue_date, text, next_ref)
    early = ltype != LetterType.APPOINTMENT and emp.date_of_leaving and issue_date < emp.date_of_leaving
    return page(request, "letters/preview.html", label,
                _("Check the letter. Change the text below if needed, then press Save and print."),
                emp=emp, issue_date=issue_date, text=text, preview=preview, warnings=warnings,
                next_ref=next_ref, slug=letter_type, letter_label=label,
                before_last_day=emp.date_of_leaving if early else None)


@owner_required
def detail(request, pk):
    letter = company_get(request, IssuedLetter.objects.select_related("employee"), pk=pk)
    two = request.GET.get("copies") == "2"
    copies = [_("OFFICE COPY"), _("EMPLOYEE COPY")] if two else [""]
    return render(request, "letters/print.html", {
        "title": letter.ref_no, "letter": letter, "copies": copies, "two": two,
        "back_url": reverse("letters:index"),
    })


@owner_required
def template_edit(request, letter_type):
    ltype = _letter_type(letter_type)
    template = get_template(request.company, ltype)
    label = LetterType(ltype).label
    if request.method == "POST":
        before = template.body
        if request.POST.get("action") == "reset":
            template.body = DEFAULT_TEXT[ltype]
            template.save()
            audit(request, "letter.template_edit", template, {"letter_type": ltype, "reset": True, "before": before})
            messages.success(request, _("The original text is back."))
            return redirect("letters:template_edit", letter_type=letter_type)
        form = TemplateForm(request.POST)
        if form.is_valid():
            template.body = form.cleaned_data["body"].replace("\r\n", "\n")
            template.save()
            audit(request, "letter.template_edit", template,
                  {"letter_type": ltype, "before": before, "after": template.body})
            messages.success(request, _("The default text is saved. New letters will use it."))
            return redirect("letters:template_edit", letter_type=letter_type)
    else:
        form = TemplateForm(initial={"body": template.body})
    warnings = unknown_placeholders(form["body"].value() or "")
    asking_reset = request.method == "GET" and request.GET.get("reset") == "ask"
    return page(request, "letters/template_edit.html", _("Default text: %(label)s") % {"label": label},
                _("Change the words and press Save. Use only the {{ placeholders }} listed below."),
                form=form, placeholders=PLACEHOLDERS, warnings=warnings, slug=letter_type, letter_label=label,
                is_appointment=ltype == LetterType.APPOINTMENT, asking_reset=asking_reset)
