from django.urls import path

from . import views

urlpatterns = [
    path("objekte/<int:pk>/dokumente/", views.document_list, name="document_list"),
    path("objekte/<int:pk>/dokumente/hochladen/", views.document_upload, name="document_upload"),
    path("objekte/<int:pk>/verarbeitung/starten/", views.processing_start, name="processing_start"),
    path("dokumente/<int:pk>/", views.document_detail, name="document_detail"),
    path("dokumente/<int:pk>/seite/<int:page_no>.jpg", views.document_preview, name="document_preview"),
]
