from django.urls import path

from . import views

urlpatterns = [
    path("objekte/<int:pk>/vollstaendigkeit/", views.completeness_view, name="completeness"),
    path("vollstaendigkeit/<int:pk>/uebersteuern/", views.finding_override, name="finding_override"),
    path("objekte/<int:pk>/nachforderungen/", views.request_list, name="request_list"),
    path("nachforderungen/<int:pk>/", views.request_detail, name="request_detail"),
    path("nachforderungen/<int:pk>/freigeben/", views.request_approve, name="request_approve"),
    path("nachforderungen/<int:pk>/datei/<str:fmt>/", views.request_file, name="request_file"),
]
