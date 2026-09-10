from django.urls import path

from . import views

urlpatterns = [
    path("objekte/", views.object_list, name="object_list"),
    path("objekte/neu/", views.object_create, name="object_create"),
    path("objekte/<int:pk>/", views.object_detail, name="object_detail"),
    path("objekte/<int:pk>/bearbeiten/", views.object_edit, name="object_edit"),
    path("objekte/<int:pk>/einheiten/neu/", views.unit_create, name="unit_create"),
    path("einheiten/<int:pk>/bearbeiten/", views.unit_edit, name="unit_edit"),
]
