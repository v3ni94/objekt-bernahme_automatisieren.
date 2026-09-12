from django.urls import path

from apps.sync import views

urlpatterns = [
    path("webhooks/paperless/", views.webhook_paperless, name="webhook_paperless"),
    path("verwaltung/sync/", views.sync_admin, name="sync_admin"),
    path("verwaltung/sync/pruefen/", views.sync_check, name="sync_check"),
    path("verwaltung/sync/eingang-einrichten/", views.sync_inbox_setup, name="sync_inbox_setup"),
    path("verwaltung/sync/jetzt/", views.sync_run_now, name="sync_run_now"),
    path("verwaltung/sync/operationen/<int:pk>/", views.sync_operation_action, name="sync_operation_action"),
    path("verwaltung/sync/bestand/starten/", views.inventory_start, name="sync_inventory_start"),
    path("verwaltung/sync/bestand/<int:pk>/", views.inventory_detail, name="sync_inventory"),
    path("verwaltung/sync/bestand/<int:pk>/aktion/", views.inventory_action, name="sync_inventory_action"),
    path("eingang/", views.inbox_list, name="inbox_list"),
    path("eingang/objekte.json", views.objects_json, name="inbox_objects_json"),
]
