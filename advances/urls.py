from django.urls import path

from . import views

app_name = "advances"

urlpatterns = [
    path("", views.advance_list, name="list"),
    path("add/", views.advance_add, name="add"),
    path("<int:pk>/", views.advance_detail, name="detail"),
]
