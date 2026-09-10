from django.urls import path

from . import views

urlpatterns = [
    path("konfiguration/", views.settings_list, name="settings_list"),
    path("konfiguration/<str:key>/", views.setting_edit, name="setting_edit"),
]
