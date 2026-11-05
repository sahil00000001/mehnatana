import mimetypes

from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils.translation import gettext as _

from .tenancy import company_get, owner_required, page


def not_found(request, exception=None):
    from .formatting import month_label
    from .tenancy import NoSalaryMonth

    if isinstance(exception, NoSalaryMonth) and getattr(request, "company", None) is not None:
        return render(request, "404.html", {
            "title": _("No salary for %(month)s yet") % {"month": month_label(exception.month)},
            "salary_month": exception.month,
        }, status=404)
    return render(request, "404.html", {"title": _("Page not found")}, status=404)


def csrf_failure(request, reason=""):
    return render(request, "403.html", {
        "title": _("Please try again"),
        "csrf": True,
    }, status=403)


def forbidden(request, exception=None):
    return render(request, "403.html", {"title": _("Not allowed")}, status=403)


@owner_required
def notices(request):
    from .notices import notices_for

    items = notices_for(request.company, include_fixes=True)
    groups = [
        (_("Needs attention now"), [n for n in items if n.level == "urgent"]),
        (_("Coming up"), [n for n in items if n.level == "soon"]),
        (_("Good to know"), [n for n in items if n.level == "info" and n.kind != "fix"]),
        (_("Things to fix in your records"), [n for n in items if n.kind == "fix"]),
    ]
    return page(request, "core/notices.html", _("Notices"),
                _("Payments due, people joining or leaving, and records to complete. Click a notice to act on it."),
                groups=[(title, rows) for title, rows in groups if rows])


@owner_required
def help_page(request):
    return page(request, "core/help.html", _("Help"),
                _("Short answers to common questions. Call us if you are stuck."))


def _send_private_file(fieldfile, download_name=None):
    if not fieldfile:
        raise Http404
    try:
        handle = fieldfile.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404
    content_type = mimetypes.guess_type(fieldfile.name)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type, as_attachment=bool(download_name),
                            filename=download_name or None)
    response["Cache-Control"] = "private, max-age=300"
    return response


@owner_required
def files_logo(request):
    """The company's own logo. Never served from a public URL."""
    return _send_private_file(request.company.logo)


@owner_required
def files_upload(request, run_id):
    """The monthly sheet that was uploaded for one of this company's months."""
    from payroll.models import PayrollRun

    run = company_get(request, PayrollRun, pk=run_id)
    name = f"{request.company.code}_salary_{run.month:%Y-%m}_uploaded.xlsx"
    return _send_private_file(run.uploaded_file, download_name=name)
