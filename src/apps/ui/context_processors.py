import os

from django.conf import settings

# Zuordnung der URL-Praefixe zu den Bereichen der Hauptnavigation (aktiver Eintrag im Kopf)
NAV_SECTIONS = (
    ("/objekte/", "objekte"),
    ("/einheiten/", "objekte"),
    ("/dokumente/", "objekte"),
    ("/abgleiche/", "objekte"),
    ("/importe/", "objekte"),
    ("/eigentuemer/", "eigentuemer"),
    ("/zuordnungen/", "eigentuemer"),
    ("/review/", "review"),
    ("/suche/", "suche"),
    ("/berichte/", "berichte"),
    ("/status/", "status"),
    ("/verwaltung/protokoll/", "protokoll"),
    ("/verwaltung/konfiguration/", "konfiguration"),
    ("/verwaltung/nutzer/", "nutzer"),
    ("/verwaltung/altbestand/", "altbestand"),
    ("/verwaltung/drive/", "drive"),
    ("/konto/", "konto"),
)


def nav_section(path: str) -> str | None:
    for prefix, section in NAV_SECTIONS:
        if path.startswith(prefix):
            return section
    return None


def app_context(request):
    return {
        "APP_NAME": "Objektübernahme",
        "APP_OWNER": "Hausverwaltung Müller GmbH",
        "IMAGE_TAG": os.environ.get("IMAGE_TAG", "dev"),
        "NAV_ACTIVE": nav_section(getattr(request, "path", "") or ""),
        "MFA_TRUST_DAYS": settings.MFA_TRUST_COOKIE_AGE.days if settings.MFA_TRUST_ENABLED else 0,
    }
