"""URL-Struktur. Deutsche Pfade fuer Anwender, technische Endpunkte englisch (healthz, readyz)."""

from django.urls import include, path

urlpatterns = [
    path("", include("apps.ui.urls")),
    path("konto/", include("allauth.urls")),
    path("", include("apps.status.urls")),
    path("", include("apps.objects.urls")),
    path("", include("apps.parties.urls")),
    path("", include("apps.imports.urls")),
    path("", include("apps.drive.urls")),
    path("", include("apps.documents.urls")),
    path("", include("apps.review.urls")),
    path("", include("apps.requirements.urls")),
    path("verwaltung/", include("apps.config.urls")),
    path("verwaltung/", include("apps.audit.urls")),
    path("verwaltung/", include("apps.accounts.urls")),
]
