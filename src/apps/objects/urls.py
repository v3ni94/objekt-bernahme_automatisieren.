from django.urls import path

from . import views

urlpatterns = [
    path("objekte/", views.object_list, name="object_list"),
    path("objekte/neu/", views.object_create, name="object_create"),
    path("objekte/<int:pk>/", views.object_detail, name="object_detail"),
    path("objekte/<int:pk>/bearbeiten/", views.object_edit, name="object_edit"),
    path("objekte/archiv/", views.object_archive_list, name="object_archive_list"),
    path("objekte/<int:pk>/archivieren/", views.object_archive, name="object_archive"),
    path("objekte/<int:pk>/wiederherstellen/", views.object_restore, name="object_restore"),
    path("objekte/<int:pk>/akten/anlegen/", views.object_unit_files, name="object_unit_files"),
    path("objekte/<int:pk>/ordner/festlegen/", views.object_set_root, name="object_set_root"),
    path("objekte/<int:pk>/einheiten/neu/", views.unit_create, name="unit_create"),
    path("einheiten/<int:pk>/bearbeiten/", views.unit_edit, name="unit_edit"),
]
