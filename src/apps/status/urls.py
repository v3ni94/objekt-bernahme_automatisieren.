from django.urls import path

from . import views

urlpatterns = [
    path("healthz/", views.healthz, name="healthz"),
    path("readyz/", views.readyz, name="readyz"),
    path("status/", views.status_page, name="status_page"),
]
