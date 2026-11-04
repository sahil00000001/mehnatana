from django.urls import path

from . import views

app_name = "letters"

# <letter_type> is "appointment", "relieving" or "experience"
urlpatterns = [
    path("", views.index, name="index"),
    path("new/<str:letter_type>/", views.new, name="new"),
    path("<int:pk>/", views.detail, name="detail"),
    path("templates/<str:letter_type>/", views.template_edit, name="template_edit"),
]
