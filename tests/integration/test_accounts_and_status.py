import json

import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditEvent

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_rollen_und_rechte_aus_seed(seeded):
    admin = Role.objects.get(code="admin")
    clerk = Role.objects.get(code="sachbearbeiter")
    assert admin.has_perm("settings.write") and not clerk.has_perm("settings.write")
    assert clerk.has_perm("review.decide") and clerk.has_perm("owner_files.read")


def test_email_eindeutig_nur_fuer_aktive(seeded):
    from django.db import IntegrityError, transaction
    from django.utils import timezone

    role = Role.objects.get(code="sachbearbeiter")
    u1 = User.objects.create_user("doppelt@example.test", "Startpasswort-12x", role=role)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("doppelt@example.test", "Startpasswort-12x", role=role)
    u1.deleted_at = timezone.now()
    u1.save(update_fields=["deleted_at"])
    u2 = User.objects.create_user("doppelt@example.test", "Startpasswort-12x", role=role)
    assert u2.pk != u1.pk and User.objects.get_by_natural_key("doppelt@example.test") == u2


def test_ohne_totp_wird_auf_einrichtung_umgeleitet(client, admin_user):
    client.force_login(admin_user)
    r = client.get(reverse("status_page"))
    assert r.status_code == 302 and r["Location"].startswith(reverse("mfa_activate_totp"))


def test_sachbearbeiter_ohne_recht_bekommt_403_mit_audit(client_as, clerk_user):
    c = client_as(clerk_user)
    r = c.get(reverse("user_list"))
    assert r.status_code == 403
    e = AuditEvent.objects.filter(action="auth.denied").latest("id")
    assert e.user_id == clerk_user.pk and e.after_state["permission"] == "users.manage"


def test_admin_sieht_konfiguration_und_statusseite(client_as, admin_user):
    c = client_as(admin_user)
    assert c.get(reverse("settings_list")).status_code == 200
    r = c.get(reverse("status_page"))
    assert r.status_code == 200 and b"Status" in r.content


def test_healthz_und_readyz(client, admin_user):
    assert client.get("/healthz/").content == b"ok"
    assert client.get("/readyz/").status_code == 401
    r = client.get("/readyz/", HTTP_AUTHORIZATION="Bearer test-readyz-token")
    body = json.loads(r.content)
    assert body["database"]["ok"] is True and body["schema"]["ok"] is True
    assert "disk" in body and "oauth" in body


def test_login_fehlversuche_sperren_konto(client, clerk_user, settings):
    settings.LOGIN_MAX_ATTEMPTS = 3
    for _ in range(3):
        client.post(reverse("account_login"), {"login": clerk_user.email, "password": "falsch"})
    clerk_user.refresh_from_db()
    assert clerk_user.is_locked
    assert AuditEvent.objects.filter(action="auth.login_failed").count() >= 3
    r = client.post(reverse("account_login"), {"login": clerk_user.email, "password": "Startpasswort-12x"})
    assert r.status_code == 200 and b"gesperrt" in r.content


def test_login_mit_richtigem_passwort_setzt_zaehler_zurueck(client, clerk_user):
    User.objects.filter(pk=clerk_user.pk).update(failed_login_count=2)
    r = client.post(reverse("account_login"), {"login": clerk_user.email, "password": "Startpasswort-12x"})
    assert r.status_code in (302, 200)
    clerk_user.refresh_from_db()
    assert clerk_user.failed_login_count == 0
    assert AuditEvent.objects.filter(action="auth.login", user_id=clerk_user.pk).exists()


def test_request_id_header_wird_gesetzt(client):
    r = client.get("/healthz/", HTTP_X_REQUEST_ID="abc-123")
    assert r["X-Request-ID"] == "abc-123"
    assert len(client.get("/healthz/")["X-Request-ID"]) == 36
