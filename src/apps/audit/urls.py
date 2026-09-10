from django.urls import path

from . import views

urlpatterns = [
    path("protokoll/", views.audit_list, name="audit_list"),
    path("protokoll/export.csv", views.audit_export, name="audit_export"),
]
