from django.urls import path

from . import views

urlpatterns = [
    path("objekte/<int:pk>/importe/", views.batch_list, name="import_list"),
    path("objekte/<int:pk>/importe/neu/", views.batch_upload, name="import_upload"),
    path("importe/<int:pk>/", views.batch_detail, name="import_batch"),
    path("importe/<int:pk>/neu-einlesen/", views.batch_reparse, name="import_reparse"),
    path("importe/<int:pk>/zuordnung/", views.batch_mapping, name="import_mapping"),
    path("importe/<int:pk>/vorschau/", views.batch_preview, name="import_preview"),
    path("importe/<int:pk>/uebernehmen/", views.batch_commit, name="import_commit"),
    path("importe/<int:pk>/protokoll/", views.batch_protocol, name="import_protocol"),
    path("importzeilen/<int:pk>/", views.row_decide, name="import_row"),
]
