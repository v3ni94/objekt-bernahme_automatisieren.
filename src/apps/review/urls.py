from django.urls import path

from . import views

urlpatterns = [
    path("review/", views.case_list, name="review_list"),
    path("review/<int:pk>/", views.case_detail, name="review_detail"),
    path("review/<int:pk>/aktion/", views.case_action, name="review_action"),
    path("review/sammel/", views.bulk_view, name="review_bulk"),
    path("review/sammel/ausfuehren/", views.bulk_execute_view, name="review_bulk_execute"),
    path("review/sammel/status/<str:bulk_key>/", views.bulk_status, name="review_bulk_status"),
    path("review/sichten/", views.saved_filter, name="review_saved_filter"),
    path("review/sichten/<int:pk>/", views.apply_saved_filter, name="review_apply_filter"),
    path("review/objekte/<int:pk>/eigentuemer.json", views.owner_search, name="review_owner_search"),
    path("review/objekte/<int:pk>/mieter.json", views.tenant_search, name="review_tenant_search"),
    path("review/objekte/<int:pk>/einheiten.json", views.units_json, name="review_units"),
]
