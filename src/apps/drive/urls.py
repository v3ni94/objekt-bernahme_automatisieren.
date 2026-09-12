from django.urls import path

from . import views

urlpatterns = [
    path("auth/google/start/", views.connect_start, name="drive_connect_start"),
    path("auth/google/callback", views.connect_callback, name="drive_connect_callback"),
    path("verwaltung/drive/", views.drive_admin, name="drive_admin"),
    path("verwaltung/drive/wurzel/aufloesen/", views.resolve_root, name="drive_resolve_root"),
    path("verwaltung/drive/wurzel/bestaetigen/", views.confirm_root, name="drive_confirm_root"),
    path("objekte/<int:pk>/abgleich/", views.object_reconcile, name="object_reconcile"),
    path("objekte/<int:pk>/abgleiche/", views.sync_run_list, name="sync_run_list"),
    path("objekte/<int:pk>/uebernahme/", views.object_takeover, name="object_takeover"),
    path("verwaltung/altbestand/", views.takeover_sources, name="takeover_sources"),
    path("verwaltung/altbestand/aufnehmen/", views.takeover_source_add, name="takeover_source_add"),
    path("verwaltung/altbestand/aufloesen/", views.takeover_source_resolve, name="takeover_source_resolve"),
    path(
        "verwaltung/altbestand/aktualisieren/", views.takeover_source_refresh, name="takeover_source_refresh"
    ),
    path(
        "verwaltung/altbestand/<int:pk>/aufarbeiten/", views.takeover_source_run, name="takeover_source_run"
    ),
    path(
        "verwaltung/altbestand/<int:pk>/entfernen/",
        views.takeover_source_remove,
        name="takeover_source_remove",
    ),
    path("abgleiche/<int:pk>/", views.sync_run_detail, name="sync_run_detail"),
    path("abgleiche/<int:pk>.json", views.sync_run_json, name="sync_run_json"),
    path("abgleiche/<int:pk>/export/<str:fmt>/", views.sync_run_export, name="sync_run_export"),
]
