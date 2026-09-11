"""Objektanlage erzeugt den Drive-Ordner unmittelbar (CR 2, Entscheidung 11.09.2026).

Geprueft wird das Einreihen des Ausfuehrungslaufs, das Verhalten ohne Google-Verbindung und der Schalter
drive.create_folders_on_object_create. Der Abgleich selbst ist in test_drive_reconcile.py abgedeckt.
"""

from __future__ import annotations

import pytest

from apps.audit.models import AuditEvent
from apps.config import store
from apps.drive import oauth, tasks
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db


def _object_data(**over):
    data = {
        "object_number": "631",
        "name": "Musterstadt, Beispielweg 3",
        "street": "Beispielweg",
        "house_number": "3",
        "postal_code": "12345",
        "city": "Musterstadt",
        "management_type": "weg",
        "status": "new",
        "fiscal_year_start_month": 1,
        "is_test": "on",
    }
    data.update(over)
    return data


@pytest.fixture
def eingereiht(monkeypatch):
    """Faengt das Einreihen des Abgleichs ab, damit im Test kein Broker und kein Drive nötig ist."""
    calls: list[tuple] = []
    monkeypatch.setattr(tasks.reconcile_object_task, "delay", lambda *a, **kw: calls.append((a, kw)))
    return calls


@pytest.fixture
def verbunden(monkeypatch, admin_user):
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    store.set("drive.root_folder_id", "root-test", user=admin_user, reason="Test")


def test_ordner_wird_mit_dem_objekt_eingereiht(client_as, clerk_user, verbunden, eingereiht):
    client = client_as(clerk_user)
    resp = client.post("/objekte/neu/", _object_data(), follow=True)
    assert resp.status_code == 200
    obj = ManagedObject.objects.get(object_number="631")
    assert eingereiht == [((obj.pk, False, clerk_user.pk, "object_create"), {})]
    assert AuditEvent.objects.filter(action="drive.reconcile", entity_id=obj.pk).exists()
    texte = [m.message for m in resp.context["messages"]]
    assert any("Objektordner" in t for t in texte)


def test_ohne_verbindung_wird_das_objekt_dennoch_angelegt(client_as, clerk_user, monkeypatch, eingereiht):
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "not_connected", "ok": None})
    client = client_as(clerk_user)
    resp = client.post("/objekte/neu/", _object_data(object_number="632"), follow=True)
    assert ManagedObject.objects.filter(object_number="632").exists()
    assert eingereiht == []
    assert not AuditEvent.objects.filter(action="drive.reconcile").exists()
    assert any("ohne google-verbindung" in m.message.lower() for m in resp.context["messages"])


def test_ohne_wurzelordner_wird_nichts_eingereiht(client_as, clerk_user, monkeypatch, eingereiht):
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    client = client_as(clerk_user)
    resp = client.post("/objekte/neu/", _object_data(object_number="633"), follow=True)
    assert ManagedObject.objects.filter(object_number="633").exists()
    assert eingereiht == []
    assert any("Wurzelordner" in m.message for m in resp.context["messages"])


def test_schalter_aus_haelt_die_ordneranlage_zurueck(
    client_as, clerk_user, admin_user, verbunden, eingereiht
):
    store.set("drive.create_folders_on_object_create", False, user=admin_user, reason="Test")
    client = client_as(clerk_user)
    resp = client.post("/objekte/neu/", _object_data(object_number="634"), follow=True)
    assert ManagedObject.objects.filter(object_number="634").exists()
    assert eingereiht == []
    assert not any("Objektordner" in m.message for m in resp.context["messages"])
