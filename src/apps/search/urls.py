from django.urls import path

from . import views

urlpatterns = [path("suche/", views.search_page, name="search")]
