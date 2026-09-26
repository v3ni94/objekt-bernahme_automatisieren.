from django.urls import path

from . import views

urlpatterns = [
    path("objects/", views.object_list, name="crm_api_objects"),
    path("objects/<str:number>/", views.object_detail, name="crm_api_object"),
    path("objects/<str:number>/documents/", views.object_documents, name="crm_api_object_documents"),
    path("objects/<str:number>/owners/", views.object_owners, name="crm_api_object_owners"),
    path("objects/<str:number>/tenants/", views.object_tenants, name="crm_api_object_tenants"),
    path("documents/<int:pk>/", views.document_detail, name="crm_api_document"),
]
