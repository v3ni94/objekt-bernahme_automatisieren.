"""Speicherpfade in Paperless als Quelle der Objektzuordnung (Anforderung 13.09.2026): Objektnummer aus dem
fuehrenden Zahlenblock, Uebersicht mit Zuordnung zu aktiven Objekten, Vorschau je Pfad, Befuellung des Feldes
MHV Objekt in Paketen ohne Ueberschreiben, Start des objektbezogenen Bestandslaufs, Stand je Pfad, Rechte."""

from __future__ import annotations

import pytest
from django.urls import reverse
from tests.integration.sync.conftest import pdf_bytes

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents.models import Document
from apps.sync import inventory, services, storage_paths
from apps.sync.models import InventoryRun, InventoryStatus, OperationKind, SyncOperation
from apps.sync.paperless.errors import PaperlessUnavailable

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("name", "erwartet"),
    [
        ("82 – Shalomweg 3, Hückelhoven", "82"),
        ("082 Ratheim, Shalomweg 3", "82"),
        ("623_Musterstadt", "623"),
        ("  7 Beispielweg", "7"),
        ("2024 Jahresabschluss", "2024"),
        ("Holding/Steuer 2024", None),
        ("Privat", None),
        ("", None),
        (None, None),
    ],
)
def test_objektnummer_aus_pfadname(name, erwartet):
    assert storage_paths.derive_object_number(name) == erwartet


def _feld_id() -> int:
    return int(services.connection_meta()["field_ids"]["object"])


@pytest.fixture
def pfade(objekt, anderes_objekt, paperless, admin_user):
    """Drei Speicherpfade: 623 (fuenf Dokumente: drei ohne Feld, eines mit 623, eines mit 999), 624 (ein Dokument),
    Holding ohne Nummer (ein Dokument). Produktionsstandard import_only_with_object an, Modus full."""
    store.set("paperless.import_only_with_object", True, user=admin_user, reason="Test")
    store.set("paperless.mode", "full", user=admin_user, reason="Test")
    fid = _feld_id()
    sp_623 = paperless.create_storage_path("623 – Musterstadt, Musterstraße 49")["id"]
    sp_624 = paperless.create_storage_path("0624 Beispielhausen")["id"]
    sp_holding = paperless.create_storage_path("Holding und Steuer")["id"]
    ohne = [
        paperless.add_document(f"Rechnung {i}", pdf_bytes([f"Rechnung {i} Objekt 623"]), storage_path=sp_623)
        for i in range(3)
    ]
    gleich = paperless.add_document(
        "Schon gesetzt", pdf_bytes(["Schon gesetzt"]), storage_path=sp_623, custom_fields={fid: "623"}
    )
    fremd = paperless.add_document(
        "Fremder Wert", pdf_bytes(["Fremder Wert"]), storage_path=sp_623, custom_fields={fid: "999"}
    )
    paperless.add_document("Beispielhausen", pdf_bytes(["624"]), storage_path=sp_624)
    paperless.add_document("Bilanz", pdf_bytes(["Bilanz"]), storage_path=sp_holding)
    paperless.reset_calls()
    return {
        "sp_623": sp_623,
        "sp_624": sp_624,
        "sp_holding": sp_holding,
        "ohne": ohne,
        "gleich": gleich,
        "fremd": fremd,
        "fid": fid,
    }


def test_uebersicht_ordnet_pfade_objekten_zu_und_puffert(pfade, objekt, anderes_objekt, paperless):
    rows, read_at = storage_paths.overview()
    assert read_at is not None
    by_name = {r.name: r for r in rows}
    r623 = by_name["623 – Musterstadt, Musterstraße 49"]
    assert r623.number == "623" and r623.object == objekt and r623.document_count == 5 and r623.matched
    r624 = by_name["0624 Beispielhausen"]
    assert r624.number == "624" and r624.object == anderes_objekt and r624.document_count == 1
    holding = by_name["Holding und Steuer"]
    assert holding.number is None and holding.object is None and not holding.matched
    # zugeordnete Pfade zuerst, nach Nummer
    assert [r.name for r in rows][:2] == ["623 – Musterstadt, Musterstraße 49", "0624 Beispielhausen"]
    assert paperless.call_names().count("list_storage_paths") == 1
    # zweiter Aufruf kommt aus dem Zwischenspeicher, neu lesen liest erneut
    storage_paths.overview()
    assert paperless.call_names().count("list_storage_paths") == 1
    storage_paths.overview(refresh=True)
    assert paperless.call_names().count("list_storage_paths") == 2


def test_vorschau_teilt_dokumente_ein_ohne_zu_schreiben(pfade, objekt, paperless):
    plan = storage_paths.plan(pfade["sp_623"])
    assert plan.object == objekt and plan.number == "623" and plan.total == 5
    assert sorted(plan.missing) == sorted(pfade["ohne"])
    assert plan.same == [pfade["gleich"]]
    assert plan.other == {pfade["fremd"]: "999"}
    assert "bulk_edit" not in paperless.call_names()
    listen = [c for c in paperless.calls if c[0] == "iter_pages"]
    assert all(c[2]["storage_path"] == pfade["sp_623"] for c in listen)
    with pytest.raises(storage_paths.StoragePathError, match="beginnt nicht mit einer Objektnummer"):
        storage_paths.plan(pfade["sp_holding"])


def test_befuellung_setzt_nur_fehlende_werte_in_paketen_und_startet_bestandslauf(
    pfade, objekt, paperless, admin_user, monkeypatch
):
    monkeypatch.setattr(storage_paths, "CHUNK", 2)
    fid = pfade["fid"]
    result = storage_paths.fill(pfade["sp_623"], user=admin_user)
    assert result["status"] == "done" and result["set"] == 3 and result["same"] == 1 and result["other"] == 1
    assert result["other_ids"] == [pfade["fremd"]] and result["object_number"] == "623"
    # drei fehlende Werte in zwei Paketen (2 + 1), nichts anderes geschrieben
    bulk = [c for c in paperless.calls if c[0] == "bulk_edit"]
    assert [len(c[1][0]) for c in bulk] == [2, 1]
    assert all(c[1][1] == "modify_custom_fields" for c in bulk)
    assert all(c[1][2] == {"add_custom_fields": {str(fid): "623"}, "remove_custom_fields": []} for c in bulk)
    assert sorted(i for c in bulk for i in c[1][0]) == sorted(pfade["ohne"])
    for pid in pfade["ohne"]:
        felder = {int(f["field"]): f["value"] for f in paperless.get_document(pid)["custom_fields"]}
        assert felder[fid] == "623"
    fremd = {int(f["field"]): f["value"] for f in paperless.get_document(pfade["fremd"])["custom_fields"]}
    assert fremd[fid] == "999"
    # Bestandslauf nur fuer 623, echt
    run = InventoryRun.objects.get(pk=result["inventory_run_id"])
    assert run.dry_run is False and run.scope == {"object_numbers": ["623"]}
    assert run.status == InventoryStatus.RUNNING
    # Stand je Pfad und Protokoll
    stand = storage_paths.states()[pfade["sp_623"]]
    assert stand["status"] == "done" and stand["set"] == 3 and stand["inventory_run_id"] == run.pk
    ereignis = AuditEvent.objects.get(action="sync.paperless_field_fill")
    assert ereignis.after_state["set"] == 3 and ereignis.object_id == objekt.pk
    # zweite Befuellung: nichts mehr zu setzen, zweiter Bestandslauf wird abgewiesen, weil einer laeuft
    paperless.reset_calls()
    wieder = storage_paths.fill(pfade["sp_623"], user=admin_user)
    assert wieder["set"] == 0 and wieder["same"] == 4 and "bulk_edit" not in paperless.call_names()
    assert "inventory_error" in wieder and "bereits ein Bestandslauf" in wieder["inventory_error"]


def test_bestandslauf_nach_befuellung_uebernimmt_die_dokumente(pfade, objekt, paperless, ops, admin_user):
    result = storage_paths.fill(pfade["sp_623"], user=admin_user)
    run = InventoryRun.objects.get(pk=result["inventory_run_id"])
    while inventory.step(run.pk).get("continue"):
        pass
    run.refresh_from_db()
    assert run.status == InventoryStatus.DONE
    pulls = SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PULL)
    assert sorted(p.payload["paperless_id"] for p in pulls) == sorted(pfade["ohne"] + [pfade["gleich"]])
    ops()
    assert Document.objects.filter(source="paperless", object=objekt).count() == 4
    assert not Document.objects.filter(source="paperless").exclude(object=objekt).exists()


def test_befuellung_bricht_bei_paperless_fehler_ab_und_merkt_den_stand(
    pfade, paperless, admin_user, monkeypatch
):
    monkeypatch.setattr(storage_paths, "CHUNK", 1)
    paperless.inject("bulk_edit", PaperlessUnavailable("Paperless POST: HTTP 503", status_code=503), times=1)
    # erster Aufruf faellt, danach geht es weiter: der Fehler kommt beim ersten Paket
    with pytest.raises(storage_paths.StoragePathError, match="Abbruch nach 0 von 3"):
        storage_paths.fill(pfade["sp_623"], user=admin_user)
    stand = storage_paths.states()[pfade["sp_623"]]
    assert stand["status"] == "failed" and stand["set"] == 0 and "503" in stand["error"]
    assert not InventoryRun.objects.exists()
    assert AuditEvent.objects.get(action="sync.paperless_field_fill").after_state["status"] == "failed"
    # Wiederholung setzt die restlichen drei
    ergebnis = storage_paths.fill(pfade["sp_623"], user=admin_user)
    assert ergebnis["status"] == "done" and ergebnis["set"] == 3


def test_befuellung_verweigert_ohne_hauptschalter_objekt_oder_freigabe(pfade, paperless, admin_user):
    store.set("paperless.mode", "pilot", user=admin_user, reason="Test")
    store.set("paperless.pilot_object_numbers", ["624"], user=admin_user, reason="Test")
    with pytest.raises(storage_paths.StoragePathError, match="außerhalb des Modus"):
        storage_paths.fill(pfade["sp_623"], user=admin_user)
    store.set("paperless.mode", "full", user=admin_user, reason="Test")
    with pytest.raises(storage_paths.StoragePathError, match="Kein aktives Objekt"):
        storage_paths.plan(paperless.create_storage_path("999 Unbekannt")["id"])
    store.set("paperless.enabled", False, user=admin_user, reason="Test")
    with pytest.raises(storage_paths.StoragePathError, match="Hauptschalter"):
        storage_paths.fill(pfade["sp_623"], user=admin_user)
    assert "bulk_edit" not in paperless.call_names()


def test_oberflaeche_uebersicht_vorschau_und_befuellung(
    pfade, objekt, paperless, client_as, admin_user, clerk_user
):
    c = client_as(admin_user)
    resp = c.get(reverse("sync_storage_paths"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "623 – Musterstadt, Musterstraße 49" in html and "Holding und Steuer" in html
    assert "ohne Objektnummer" in html and "2 einem Objekt zugeordnet" in html
    resp = c.get(reverse("sync_storage_path", args=[pfade["sp_623"]]))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Feld setzen und übernehmen" in html and "999" in html
    resp = c.post(reverse("sync_storage_path", args=[pfade["sp_623"]]), {"aktion": "fuellen"})
    assert resp.status_code == 302 and resp["Location"] == reverse("sync_storage_paths")
    assert storage_paths.states()[pfade["sp_623"]]["set"] == 3
    resp = c.post(reverse("sync_storage_paths"), {"aktion": "neu_lesen"})
    assert resp.status_code == 302
    # Sachbearbeiter ohne sync.manage
    s = client_as(clerk_user)
    assert s.get(reverse("sync_storage_paths")).status_code in (302, 403)
    assert s.post(
        reverse("sync_storage_path", args=[pfade["sp_623"]]), {"aktion": "fuellen"}
    ).status_code in (
        302,
        403,
    )
