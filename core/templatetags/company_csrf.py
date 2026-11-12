"""
{% csrf_token %} that also says which company the page was for.

Every POST form already has {% csrf_token %}, so this one built-in tag (TEMPLATES OPTIONS "builtins")
puts the open company into every form. core.middleware refuses a POST whose company is not the one open
now: with two tabs, a form left open for one company can never save into another company.
"""
from django import template
from django.template.defaulttags import CsrfTokenNode
from django.utils.html import format_html

register = template.Library()

FIELD = "_company"


class CompanyCsrfTokenNode(CsrfTokenNode):
    def render(self, context):
        html = super().render(context)
        company = getattr(context.get("request"), "company", None)
        if html and company is not None:
            html += format_html('<input type="hidden" name="{}" value="{}">', FIELD, company.pk)
        return html


@register.tag
def csrf_token(parser, token):
    return CompanyCsrfTokenNode()
