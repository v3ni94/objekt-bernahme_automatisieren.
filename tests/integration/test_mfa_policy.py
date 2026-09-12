"""Zweiter Faktor je Rolle und vertrauenswuerdiges Geraet (Entscheidung 12.09.2026).

Pflicht nur fuer Rollen aus security.mfa_required_roles (Vorgabe admin); andere Rollen melden sich mit Passwort an
und koennen den zweiten Faktor freiwillig einrichten. Nach erfolgreichem TOTP kann der Browser fuer MFA_TRUST_DAYS
Tage gemerkt werden, danach entfaellt die Codeabfrage auf diesem Geraet.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import format_hotp_value, hotp_value, yield_hotp_counters_from_time
from django.urls import reverse

from apps.config import store

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PASSWORT = "Startpasswort-12x"
SECRET = "JBSWY3DPEHPK3PXP"  # Klartextgeheimnis der Fixture totp_for


def aktueller_code() -> str:
    return format_hotp_value(hotp_value(SECRET, next(yield_hotp_counters_from_time())))


def anmelden(client, user):
    return client.post(reverse("account_login"), {"login": user.email, "password": PASSWORT})


def test_sachbearbeiter_ohne_totp_kommt_ohne_umleitung_durch(client, clerk_user):
    client.force_login(clerk_user)
    r = client.get(reverse("object_list"))
    assert r.status_code == 200


def test_admin_ohne_totp_wird_auf_einrichtung_geleitet(client, admin_user):
    client.force_login(admin_user)
    r = client.get(reverse("object_list"))
    assert r.status_code == 302 and r["Location"].startswith(reverse("mfa_activate_totp"))


def test_pflichtrollen_kommen_aus_dem_katalog(client, clerk_user, admin_user):
    store.set("security.mfa_required_roles", ["admin", "sachbearbeiter"], user=admin_user, reason="Test")
    client.force_login(clerk_user)
    r = client.get(reverse("object_list"))
    assert r.status_code == 302 and r["Location"].startswith(reverse("mfa_activate_totp"))
    store.set("security.mfa_required_roles", [], user=admin_user, reason="Test")
    client.force_login(admin_user)
    assert client.get(reverse("object_list")).status_code == 200


def test_login_ohne_totp_fuer_sachbearbeiter_direkt(client, clerk_user):
    r = anmelden(client, clerk_user)
    assert r.status_code == 302 and r["Location"] == "/"
    assert client.get(reverse("object_list")).status_code == 200


def test_geraet_merken_ueberspringt_totp_fuer_90_tage(client, clerk_user, totp_for, settings):
    totp_for(clerk_user)
    r = anmelden(client, clerk_user)
    assert r.status_code == 302 and r["Location"].startswith(reverse("mfa_authenticate"))
    r = client.post(reverse("mfa_authenticate"), {"code": aktueller_code()})
    assert r.status_code == 302 and r["Location"].startswith(reverse("mfa_trust")), r["Location"]
    seite = client.get(reverse("mfa_trust"))
    assert seite.status_code == 200 and "Für 90 Tage merken".encode() in seite.content
    r = client.post(reverse("mfa_trust"), {"action": "trust"})
    assert r.status_code == 302 and r["Location"] == "/"
    cookie = client.cookies.get("mfa_trusted")
    assert cookie is not None and int(cookie["max-age"]) == int(timedelta(days=90).total_seconds())
    assert cookie["httponly"]
    assert client.get(reverse("object_list")).status_code == 200

    client.post(reverse("account_logout"))  # echte Abmeldung; client.logout() wuerde alle Cookies loeschen
    r = anmelden(client, clerk_user)
    assert r.status_code == 302 and r["Location"] == "/", (
        "gemerktes Geraet muss die Codeabfrage ueberspringen"
    )
    assert client.get(reverse("object_list")).status_code == 200


def test_nicht_merken_fragt_beim_naechsten_login_erneut(client, clerk_user, totp_for):
    totp_for(clerk_user)
    anmelden(client, clerk_user)
    client.post(reverse("mfa_authenticate"), {"code": aktueller_code()})
    r = client.post(reverse("mfa_trust"), {"action": "skip"})
    assert r.status_code == 302 and r["Location"] == "/"
    assert client.cookies.get("mfa_trusted") is None
    client.post(reverse("account_logout"))  # echte Abmeldung; client.logout() wuerde alle Cookies loeschen
    r = anmelden(client, clerk_user)
    assert r["Location"].startswith(reverse("mfa_authenticate"))


def test_totp_zuruecksetzen_macht_gemerktes_geraet_ungueltig(client, clerk_user, totp_for):
    totp_for(clerk_user)
    anmelden(client, clerk_user)
    client.post(reverse("mfa_authenticate"), {"code": aktueller_code()})
    client.post(reverse("mfa_trust"), {"action": "trust"})
    client.post(reverse("account_logout"))  # echte Abmeldung; client.logout() wuerde alle Cookies loeschen
    # Admin setzt zurueck, Nutzer richtet neu ein: anderer Authenticator, anderer Fingerabdruck
    Authenticator.objects.filter(user=clerk_user).delete()
    totp_for(clerk_user)
    r = anmelden(client, clerk_user)
    assert r["Location"].startswith(reverse("mfa_authenticate"))


def test_nutzerliste_zeigt_pflicht_und_freiwillig(client_as, admin_user, clerk_user):
    c = client_as(admin_user)
    r = c.get(reverse("user_list"))
    inhalt = r.content.decode()
    assert "nicht eingerichtet, freiwillig" in inhalt
    assert "offen, Pflicht" not in inhalt  # Admin hat den Faktor eingerichtet (client_as)
