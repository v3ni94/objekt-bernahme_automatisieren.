from django.urls import path

from . import views

urlpatterns = [
    path("berichte/", views.overview, name="report_overview"),
    path("berichte/objekt/<int:pk>/", views.object_report, name="report_object"),
]
