from django.urls import path

from . import portal

app_name = "portal"

urlpatterns = [
    path("", portal.company_list, name="list"),
    path("add/", portal.company_add, name="add"),
    path("minimum-wages/", portal.minimum_wages, name="minimum_wages"),
    path("<int:company_id>/open/", portal.company_open, name="open"),
    path("<int:company_id>/switch/", portal.company_switch, name="switch"),
]
