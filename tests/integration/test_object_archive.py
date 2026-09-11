"""Objekte archivieren und wiederherstellen (Fachentwurf D 1 Nr. 6: Soft-Delete, nichts wird physisch geloescht,
Drive bleibt unberuehrt). Entscheidung vom 11.09.2026."""

from __future__ import annotations

import pytest
from allauth.account.internal.flows import reauthentication
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.objects.models import ManagedObject, Unit
from apps.pipeline.models import ProcessingRun, RunStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def frisch_bestaetigt(monkeypatch):
    """Die Step-up-Pruefung von allauth gilt als erfuellt (zweiter Faktor gerade bestaetigt)."""
    monkeypatch.setattr(reauthentication, "did_recently_authenticate", lambda request: True)


@pytest.fixture
def objekt(seeded):
    obj = ManagedObject.objects.create(
        object_number="700",
        name="Musterstadt, Musterallee 5",
        street="Musterallee",
        house_number="5",
        city="Musterstadt",
        management_type="weg",
        status="active",
        is_test=True,
        drive_root_folder_id="drive-700",
        drive_root_folder_name="700 Musterstadt, Musterallee 5",
    )
    Unit.objects.create(object=obj, unit_label="WE 1", unit_label_normalized="WE1", unit_type="apartment")
    return obj


def test_archivieren_versteckt_das_objekt_und_laesst_daten_und_drive_unberuehrt(
    client_as, clerk_user, objekt, frisch_bestaetigt
):
    client = client_as(clerk_user)
    resp = client.post(
        reverse("object_archive", args=[objekt.pk]), {"reason": "Verwaltung beendet", "confirm": "on"}
    )
    assert resp.status_code == 302 and resp["Location"] == reverse("object_archive_list")
    objekt.refresh_from_db()
    assert objekt.deleted_at is not None and objekt.deleted_by == clerk_user
    assert objekt.delete_reason == "Verwaltung beendet" and objekt.status == "archived"
    # Daten bleiben: Einheit vorhanden, Drive-Verweis unveraendert
    assert Unit.objects.filter(object=objekt).count() == 1
    assert objekt.drive_root_folder_id == "drive-700"
    ev = AuditEvent.objects.get(action="object.archive", entity_id=objekt.pk)
    assert (
        ev.after_state["reason"] == "Verwaltung beendet"
        and ev.after_state["drive_root_folder_id"] == "drive-700"
    )
    # Nicht mehr in der Liste, nicht mehr in der Detailansicht, aber im Archiv
    liste = client.get(reverse("object_list")).content.decode()
    assert reverse("object_detail", args=[objekt.pk]) not in liste  # kein Eintrag mehr in der Tabelle
    assert client.get(reverse("object_detail", args=[objekt.pk])).status_code == 404
    assert "Verwaltung beendet" in client.get(reverse("object_archive_list")).content.decode()


def test_archivieren_ohne_step_up_wird_zur_bestaetigung_umgeleitet(client_as, clerk_user, objekt):
    client = client_as(clerk_user)
    resp = client.post(reverse("object_archive", args=[objekt.pk]), {"reason": "x", "confirm": "on"})
    assert resp.status_code == 302 and "reauthenticate" in resp["Location"]
    objekt.refresh_from_db()
    assert objekt.deleted_at is None


def test_archivieren_waehrend_offenem_lauf_wird_abgelehnt(client_as, clerk_user, objekt, frisch_bestaetigt):
    ProcessingRun.objects.create(object=objekt, run_type="incremental", status=RunStatus.PENDING)
    client = client_as(clerk_user)
    resp = client.post(
        reverse("object_archive", args=[objekt.pk]), {"reason": "x", "confirm": "on"}, follow=True
    )
    objekt.refresh_from_db()
    assert objekt.deleted_at is None
    assert any("offene" in m.message for m in resp.context["messages"])


def test_wiederherstellen_und_nummernkonflikt(client_as, clerk_user, objekt, frisch_bestaetigt):
    client = client_as(clerk_user)
    client.post(reverse("object_archive", args=[objekt.pk]), {"reason": "Test", "confirm": "on"})
    # Nummer wird neu vergeben: Wiederherstellung muss scheitern
    neu = ManagedObject.objects.create(
        object_number="0700", name="Neu", street="A", house_number="1", city="B", management_type="weg"
    )
    resp = client.post(reverse("object_restore", args=[objekt.pk]), follow=True)
    objekt.refresh_from_db()
    assert objekt.deleted_at is not None
    assert any("vergeben" in m.message for m in resp.context["messages"])
    # Nach Archivierung des Konkurrenten klappt es
    client.post(reverse("object_archive", args=[neu.pk]), {"reason": "Irrtum", "confirm": "on"})
    resp = client.post(reverse("object_restore", args=[objekt.pk]))
    assert resp.status_code == 302
    objekt.refresh_from_db()
    assert objekt.deleted_at is None and objekt.status == "active" and objekt.delete_reason is None
    assert AuditEvent.objects.filter(action="object.restore", entity_id=objekt.pk).exists()
