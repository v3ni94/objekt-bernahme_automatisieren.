from django.urls import path

from . import views

urlpatterns = [
    path("nutzer/", views.user_list, name="user_list"),
    path("nutzer/neu/", views.user_create, name="user_create"),
    path("nutzer/<int:pk>/status/", views.user_toggle, name="user_toggle"),
    path("nutzer/<int:pk>/rolle/", views.user_role, name="user_role"),
    path("nutzer/<int:pk>/totp-zuruecksetzen/", views.user_reset_totp, name="user_reset_totp"),
    path("nutzer/<int:pk>/entsperren/", views.user_unlock, name="user_unlock"),
]
