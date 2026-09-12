"""Jede Hauptseite muss ohne Serverfehler rendern (Auslöser 11.09.2026: Protokollseite mit fehlendem
{% load perms %} und Statusseite mit nicht lesbarer status.json fielen erst auf dem Server auf)."""

from __future__ import annotations

import pytest
from allauth.account.internal.flows import reauthentication
from django.urls import reverse

pytestmark = pytest.mark.django_db

SEITEN = [
    "home",
    "object_list",
    "object_create",
    "object_archive_list",
    "owner_list",
    "review_list",
    "search",
    "report_overview",
    "status_page",
    "audit_list",
    "settings_list",
    "user_list",
    "drive_admin",
    "takeover_sources",
    "sync_admin",
    "inbox_list",
]


@pytest.fixture
def als_admin(client_as, admin_user, monkeypatch):
    monkeypatch.setattr(reauthentication, "did_recently_authenticate", lambda request: True)
    return client_as(admin_user)


@pytest.mark.parametrize("name", SEITEN)
def test_seite_rendert_als_admin(als_admin, name):
    resp = als_admin.get(reverse(name))
    assert resp.status_code in (200, 302), f"{name}: {resp.status_code}"


def test_protokoll_rendert_auch_fuer_sachbearbeiter(client_as, clerk_user):
    resp = client_as(clerk_user).get(reverse("audit_list"))
    assert resp.status_code == 200


def test_statusseite_ohne_lesbare_statusdatei(als_admin, monkeypatch, tmp_path):
    """Verzeichnis der Sicherung nicht betretbar: Befund auf der Seite, kein Serverfehler."""
    from django.conf import settings

    from apps.status import checks

    gesperrt = tmp_path / "data"
    (gesperrt / "backup").mkdir(parents=True)
    (gesperrt / "backup" / "status.json").write_text("{}", encoding="utf-8")
    (gesperrt / "backup").chmod(0o000)
    monkeypatch.setitem(settings.OBJEKTAKTE, "DATA_DIR", gesperrt)
    try:
        status = checks.backup_status()
        if status.get("error") is None:  # als root im Container greift die Sperre nicht
            pytest.skip("Rechtesperre ohne Wirkung (root)")
        assert status["ok"] is False and status["status"] == "status.json nicht lesbar"
        assert als_admin.get(reverse("status_page")).status_code == 200
    finally:
        (gesperrt / "backup").chmod(0o700)
