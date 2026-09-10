from django.urls import path

from . import views

urlpatterns = [
    path("eigentuemer/", views.owner_list, name="owner_list"),
    path("eigentuemer/neu/", views.owner_create, name="owner_create"),
    path("eigentuemer/<int:pk>/bearbeiten/", views.owner_edit, name="owner_edit"),
    path("objekte/<int:pk>/zuordnungen/neu/", views.assignment_create, name="assignment_create"),
    path("zuordnungen/<int:pk>/beenden/", views.assignment_end, name="assignment_end"),
]
