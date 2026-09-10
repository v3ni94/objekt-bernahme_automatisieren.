from django.urls import path

from . import views

urlpatterns = [
    path("objekte/<int:pk>/listen/", views.list_overview, name="list_overview"),
    path("objekte/<int:pk>/listen/<str:list_type>/<str:fmt>/", views.list_download, name="list_download"),
]
