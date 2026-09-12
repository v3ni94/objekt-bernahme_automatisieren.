"""Bestandslaeufe (Fallgruppe E, Importassistent): der Trockenlauf klassifiziert den Bestand von Paperless und
Drive seitenweise in ein Manifest, ohne Operationen, Dokumente oder Verknuepfungen anzulegen; der echte Lauf
reiht Uebernahmen und Uebertragungen als Operationen ein und registriert Drive-Dateien; Laeufe sind pausierbar,
fortsetzbar und abbrechbar, je Art laeuft hoechstens einer; ein Fehler bei einem Dokument bleibt als Zeile
sichtbar, ohne den Lauf zu stoppen; die Verwaltungsseiten sind dem Recht sync.manage vorbehalten."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from tests.integration.sync.conftest import pdf_bytes

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document
from apps.drive.adapter import READ_METHODS, SHORTCUT_MIME, DriveError
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.sync import inventory, services
from apps.sync.flows.paperless_pull import CURSOR_MODIFIED
from apps.sync.models import (
    Disposition,
    ExternalLink,
    InventoryItem,
    InventoryRun,
    InventoryStatus,
    LinkRole,
    LinkState,
    OperationKind,
    OperationStatus,
    SyncOperation,
    SyncSystem,
)
from apps.sync.operations import op_key
from apps.sync.paperless.errors import PaperlessError, PaperlessUnavailable

pytestmark = pytest.mark.django_db

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
LINES_FREMD = [
    "Schreiben ohne Objektbezug",
    "Alle Angaben in diesem Testdokument sind erfunden; keine realen Personen oder Konten.",
]
LINES_VERKNUEPFT = [
    "Objekt 623 Musterstadt, Musterstraße 49",
    "Protokoll der Begehung vom 01.09.2026 (Testdokument, alle Angaben erfunden)",
]
LINES_REGISTRIERT = [
    "Objekt 623 Musterstadt, Musterstraße 49",
    "Angebot Dachrinnenreinigung vom 02.09.2026 (Testdokument, alle Angaben erfunden)",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _items(run: InventoryRun) -> dict[str, InventoryItem]:
    """Manifestzeilen eines Laufs nach Quell-ID."""
    return {item.external_id: item for item in InventoryItem.objects.filter(run=run)}


def _bis_ende(run: InventoryRun, max_steps: int = 20) -> list[dict]:
    """Ruft step auf, bis der Lauf keine Fortsetzung mehr meldet; liefert die Schrittergebnisse."""
    ergebnisse: list[dict] = []
    while len(ergebnisse) < max_steps:
        ergebnis = inventory.step(run.pk)
        ergebnisse.append(ergebnis)
        if not ergebnis.get("continue"):
            break
    run.refresh_from_db()
    return ergebnisse


def _metadaten_aufrufe(paperless) -> list[int]:
    return [call[1][0] for call in paperless.calls if call[0] == "get_metadata"]


def _seitenaufrufe(paperless) -> list[tuple[int | None, int]]:
    return [(c[2]["page_size"], c[2]["start_page"]) for c in paperless.calls if c[0] == "iter_pages"]


def _lokales_dokument(objekt, name: str, data: bytes, **fields) -> Document:
    """Registriertes Dokument aus dem Drive-Bestand mit bekannter Pruefsumme (ohne Pipeline und ohne Hooks)."""
    return Document.objects.create(
        object=objekt,
        sha256=_sha256(data),
        drive_md5=_md5(data),
        size_bytes=len(data),
        mime_type="application/pdf",
        original_name=name,
        current_name=name,
        source="drive_existing",
        status="hashed",
        first_seen_at=timezone.now(),
        **fields,
    )


@pytest.fixture
def seitengroesse_zwei(monkeypatch):
    """Seitengroesse 2 je Schritt. Der Konfigurationskatalog erlaubt fuer sync.inventory_page_size mindestens 20,
    daher wird die Konfigurationsfunktion ersetzt statt der Wert gesetzt."""
    from apps.sync import config

    monkeypatch.setattr(config, "inventory_page_size", lambda: 2)
    return 2


@pytest.fixture
def drei_dokumente(objekt, paperless, eingang, run_all, seitengroesse_zwei):
    """Drei Dokumente in Paperless: (a) identisch mit einem lokal verarbeiteten Dokument in 623, (b) nur in
    Paperless, (c) bereits mit einem lokalen Dokument verknuepft. Seitengroesse 2, damit zwei Schritte noetig sind."""
    daten_a = pdf_bytes()
    doc_a, _ = ingest.ingest_upload(objekt, filename="Wartung_Heizung.pdf", data=daten_a)
    run_all(objekt)
    doc_a.refresh_from_db()
    assert doc_a.sha256 == _sha256(daten_a)
    pid_a = paperless.add_document(
        "Wartung Heizung", content=daten_a, original_file_name="Wartung_Heizung.pdf"
    )
    daten_b = pdf_bytes(LINES_FREMD)
    pid_b = paperless.add_document(
        "Nur in Paperless", content=daten_b, original_file_name="Nur_in_Paperless.pdf"
    )
    daten_c = pdf_bytes(LINES_VERKNUEPFT)
    doc_c = _lokales_dokument(objekt, "Protokoll_Begehung.pdf", daten_c, drive_file_id="drive-protokoll-1")
    pid_c = paperless.add_document(
        "Protokoll Begehung", content=daten_c, original_file_name="Protokoll_Begehung.pdf"
    )
    ExternalLink.objects.create(
        document=doc_c,
        system=SyncSystem.PAPERLESS,
        role=LinkRole.ORIGINAL,
        external_id=str(pid_c),
        checksum_sha256=doc_c.sha256,
        state=LinkState.SYNCED,
    )
    paperless.reset_calls()
    return SimpleNamespace(
        pid_a=pid_a,
        doc_a=doc_a,
        daten_a=daten_a,
        pid_b=pid_b,
        daten_b=daten_b,
        pid_c=pid_c,
        doc_c=doc_c,
    )


# ---------------------------------------------------------------- Paperless
def test_paperless_trockenlauf_klassifiziert_ohne_schreibwirkung(
    drei_dokumente, objekt, paperless, admin_user
):
    d = drei_dokumente
    services.set_cursor(SyncSystem.PAPERLESS, CURSOR_MODIFIED, "2026-09-01T00:00:00+00:00")
    operationen_vorher = SyncOperation.objects.count()
    dokumente_vorher = Document.objects.count()
    verknuepfungen_vorher = ExternalLink.objects.count()

    run = inventory.start("paperless", dry_run=True, user=admin_user)
    assert run.kind == SyncSystem.PAPERLESS and run.dry_run is True
    assert run.status == InventoryStatus.RUNNING and run.started_by_id == admin_user.pk
    assert run.cursor_before["paperless_modified"] == "2026-09-01T00:00:00+00:00"
    assert timezone.datetime.fromisoformat(run.cursor_before["started"]).tzinfo is not None
    assert run.counters == {"seen": 0, "steps": 0} and run.page_state == {}
    start = AuditEvent.objects.get(action="sync.inventory_start", entity_id=run.pk)
    assert start.entity_type == "sync_inventory_run" and start.user_id == admin_user.pk
    assert start.after_state["kind"] == "paperless" and start.after_state["dry_run"] is True

    erster = inventory.step(run.pk)
    assert erster["continue"] is True and erster["page"] == 1 and erster["count"] == 3
    run.refresh_from_db()
    assert run.status == InventoryStatus.RUNNING and run.finished_at is None
    assert run.page_state == {"next_page": 2, "count": 3}
    assert run.counters == {"seen": 2, "steps": 1}
    assert InventoryItem.objects.filter(run=run).count() == 2

    zweiter = inventory.step(run.pk)
    assert zweiter["continue"] is False and zweiter["page"] == 2
    run.refresh_from_db()
    assert run.status == InventoryStatus.DONE and run.finished_at is not None and run.error_message is None
    assert run.page_state["next_page"] is None
    assert run.counters["seen"] == 3 and run.counters["steps"] == 2 and run.counters["complete"] is True
    assert run.counters["dispositions"] == {"link_existing": 2, "import_new": 1}
    assert _seitenaufrufe(paperless) == [(2, 1), (2, 2)]
    assert inventory.step(run.pk) == {"continue": False, "status": "done"}

    items = _items(run)
    assert set(items) == {str(d.pid_a), str(d.pid_b), str(d.pid_c)}
    a, b, c = items[str(d.pid_a)], items[str(d.pid_b)], items[str(d.pid_c)]
    assert a.disposition == Disposition.LINK_EXISTING and a.document_id == d.doc_a.pk
    assert a.object_id == objekt.pk and a.details == {"reason": "identische Datei", "matches": [d.doc_a.pk]}
    assert a.checksum_sha256 == d.doc_a.sha256 and a.size_bytes == len(d.daten_a)
    assert a.name == "Wartung_Heizung.pdf" and a.mime_type == "application/pdf"
    assert a.remote_modified_at is not None
    assert b.disposition == Disposition.IMPORT_NEW and b.document_id is None and b.object_id is None
    assert b.details == {"reason": "nur in Paperless"} and b.checksum_sha256 == _sha256(d.daten_b)
    assert c.disposition == Disposition.LINK_EXISTING and c.document_id == d.doc_c.pk
    assert c.object_id == objekt.pk and c.details == {"reason": "bereits verknüpft"}
    # fuer die bereits verknuepfte Kopie werden keine Metadaten geholt
    assert c.checksum_sha256 is None and _metadaten_aufrufe(paperless) == [d.pid_a, d.pid_b]

    # Trockenlauf: keine Operation, kein Dokument, keine Verknuepfung, kein Schreibzugriff auf Paperless
    assert SyncOperation.objects.count() == operationen_vorher
    assert not SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PULL).exists()
    assert Document.objects.count() == dokumente_vorher
    assert ExternalLink.objects.count() == verknuepfungen_vorher
    assert not ExternalLink.objects.filter(document=d.doc_a, system=SyncSystem.PAPERLESS).exists()
    assert set(paperless.call_names()) <= {"iter_pages", "get_metadata"}

    assert inventory.summary(run) == {"link_existing": 2, "import_new": 1, "total": 3, "open": 0}
    zeilen = list(inventory.manifest_rows(run))
    assert len(zeilen) == 3
    assert [z.disposition for z in zeilen] == ["import_new", "link_existing", "link_existing"]
    ende = AuditEvent.objects.get(action="sync.inventory_done", entity_id=run.pk)
    assert ende.after_state["status"] == "done" and ende.after_state["complete"] is True
    assert ende.after_state["seen"] == 3 and ende.after_state["dispositions"]["import_new"] == 1


def test_paperless_echtlauf_uebernimmt_und_verknuepft(
    drei_dokumente, objekt, paperless, eingang, ops, admin_user
):
    d = drei_dokumente
    run = inventory.start("paperless", dry_run=False, user=admin_user)
    ergebnisse = _bis_ende(run)
    assert [e["continue"] for e in ergebnisse] == [True, False]
    assert run.status == InventoryStatus.DONE and run.dry_run is False

    pulls = list(SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PULL).order_by("id"))
    assert [p.payload for p in pulls] == [
        {"paperless_id": d.pid_a, "inventory_run_id": run.pk},
        {"paperless_id": d.pid_b, "inventory_run_id": run.pk},
    ]
    assert all(p.priority == 120 and p.status == OperationStatus.PENDING for p in pulls)
    assert all(p.source_system == SyncSystem.PAPERLESS and p.document_id is None for p in pulls)
    assert [p.op_key for p in pulls] == [
        op_key(OperationKind.PAPERLESS_PULL, pid, "inventory", run.pk) for pid in (d.pid_a, d.pid_b)
    ]
    items = _items(run)
    a, b, c = items[str(d.pid_a)], items[str(d.pid_b)], items[str(d.pid_c)]
    assert b.disposition == Disposition.IN_PROGRESS and b.operation_id == pulls[1].pk
    assert a.disposition == Disposition.LINK_EXISTING and a.operation_id == pulls[0].pk
    assert c.disposition == Disposition.LINK_EXISTING and c.operation_id is None
    assert inventory.summary(run)["open"] == 1 and inventory.summary(run)["in_progress"] == 1
    assert not Document.objects.filter(source="paperless").exists()
    assert "download" not in paperless.call_names()

    ops()
    for p in pulls:
        p.refresh_from_db()
    assert pulls[0].status == OperationStatus.DONE
    assert pulls[0].result == {"linked": d.doc_a.pk, "reason": "checksum"}
    assert pulls[1].status == OperationStatus.DONE and pulls[1].result["inbox"] is True
    assert inventory.refresh_dispositions(run) == 1

    neu = Document.objects.get(source="paperless")
    assert neu.object_id == eingang.pk and neu.object.is_system_inbox
    assert neu.original_name == "Nur_in_Paperless.pdf" and neu.status == "registered"
    assert pulls[1].result["document_id"] == neu.pk
    b.refresh_from_db()
    assert b.disposition == Disposition.IMPORT_NEW and b.target_external_id == str(neu.pk)
    link_neu = ExternalLink.objects.get(document=neu, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
    assert link_neu.external_id == str(d.pid_b) and link_neu.state == LinkState.LINKED
    assert link_neu.checksum_sha256 == _sha256(d.daten_b)
    # (a): identische Datei wird verknuepft, nicht erneut uebernommen
    link_a = ExternalLink.objects.get(document=d.doc_a, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
    assert link_a.external_id == str(d.pid_a) and link_a.state == LinkState.LINKED
    assert link_a.checksum_sha256 == d.doc_a.sha256
    ereignis = AuditEvent.objects.get(action="sync.paperless_pull", entity_id=d.doc_a.pk)
    assert ereignis.after_state["linked_existing"] is True and ereignis.after_state["paperless_id"] == d.pid_a
    assert Document.objects.filter(source="paperless").count() == 1
    assert [c[1][0] for c in paperless.calls if c[0] == "download"] == [d.pid_b]
    # (c): bereits verknuepft, bleibt unangetastet
    assert ExternalLink.objects.filter(system=SyncSystem.PAPERLESS, external_id=str(d.pid_c)).count() == 1
    assert (
        ExternalLink.objects.get(system=SyncSystem.PAPERLESS, external_id=str(d.pid_c)).document_id
        == d.doc_c.pk
    )
    assert d.pid_c not in [c[1][0] for c in paperless.calls if c[0] in ("get_document", "get_metadata")]
    a.refresh_from_db()
    assert a.disposition == Disposition.LINK_EXISTING
    assert inventory.summary(run)["open"] == 0
    assert inventory.refresh_dispositions(run) == 0


def test_pause_und_fortsetzung_ohne_doppelte_zeilen(drei_dokumente, paperless, admin_user):
    d = drei_dokumente
    run = inventory.start("paperless", dry_run=True, user=admin_user)
    assert inventory.step(run.pk)["continue"] is True

    inventory.pause(run, user=admin_user)
    run.refresh_from_db()
    assert run.status == InventoryStatus.PAUSED and run.finished_at is None
    aufrufe = len(paperless.calls)
    assert inventory.step(run.pk) == {"continue": False, "status": "paused"}
    assert len(paperless.calls) == aufrufe
    run.refresh_from_db()
    assert run.status == InventoryStatus.PAUSED and run.page_state["next_page"] == 2
    assert InventoryItem.objects.filter(run=run).count() == 2 and run.counters["seen"] == 2

    inventory.resume(run, user=admin_user)
    run.refresh_from_db()
    assert run.status == InventoryStatus.RUNNING and run.error_message is None
    ergebnisse = _bis_ende(run)
    assert [e["continue"] for e in ergebnisse] == [False]
    assert run.status == InventoryStatus.DONE and run.counters["seen"] == 3 and run.counters["steps"] == 2
    # Seite 1 wurde nach der Pause nicht erneut gelesen
    assert _seitenaufrufe(paperless) == [(2, 1), (2, 2)]

    items = InventoryItem.objects.filter(run=run)
    assert items.count() == 3
    assert {(i.system, i.external_id) for i in items} == {
        (SyncSystem.PAPERLESS, str(pid)) for pid in (d.pid_a, d.pid_b, d.pid_c)
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        InventoryItem.objects.create(run=run, system=SyncSystem.PAPERLESS, external_id=str(d.pid_a))
    for aktion in ("sync.inventory_pause", "sync.inventory_resume"):
        ereignis = AuditEvent.objects.get(action=aktion, entity_id=run.pk)
        assert ereignis.entity_type == "sync_inventory_run" and ereignis.user_id == admin_user.pk


def test_wiederholte_seite_erzeugt_keine_doppelten_zeilen(drei_dokumente, paperless, admin_user):
    """Geht die Fortschrittsbestaetigung verloren (Worker faellt nach der Seite aus), wird dieselbe Seite erneut
    verarbeitet; das Manifest fuehrt je Quell-ID nur eine Zeile."""
    d = drei_dokumente
    run = inventory.start("paperless", dry_run=True, user=admin_user)
    assert inventory.step(run.pk)["continue"] is True
    inventory.pause(run, user=admin_user)
    InventoryRun.objects.filter(pk=run.pk).update(page_state={"next_page": 1, "count": 3})
    inventory.resume(run, user=admin_user)
    ergebnisse = _bis_ende(run)
    assert [e["continue"] for e in ergebnisse] == [True, False]
    assert run.status == InventoryStatus.DONE
    assert _seitenaufrufe(paperless) == [(2, 1), (2, 1), (2, 2)]
    assert InventoryItem.objects.filter(run=run).count() == 3
    assert InventoryItem.objects.filter(run=run, external_id=str(d.pid_a)).count() == 1
    assert inventory.summary(run)["total"] == 3
    # der Zaehler zaehlt gesehene Eintraege, das Manifest bleibt die Wahrheit
    assert run.counters["seen"] == 5 and run.counters["dispositions"] == {"link_existing": 2, "import_new": 1}


def test_zweiter_lauf_derselben_art_wird_abgewiesen(objekt, paperless, admin_user):
    paperless.add_document("Einzeln", content=pdf_bytes(LINES_FREMD), original_file_name="Einzeln.pdf")
    run = inventory.start("paperless", dry_run=True, user=admin_user)
    with pytest.raises(inventory.InventoryError, match="bereits ein Bestandslauf"):
        inventory.start("paperless", dry_run=False, user=admin_user)
    assert InventoryRun.objects.filter(kind=SyncSystem.PAPERLESS).count() == 1
    assert AuditEvent.objects.filter(action="sync.inventory_start").count() == 1
    with pytest.raises(inventory.InventoryError, match="paperless oder drive"):
        inventory.start("unbekannt", user=admin_user)
    # eine andere Art laeuft unabhaengig davon
    drive_run = inventory.start("drive", dry_run=True, user=admin_user)
    assert drive_run.status == InventoryStatus.RUNNING and drive_run.kind == SyncSystem.DRIVE

    _bis_ende(run)
    assert run.status == InventoryStatus.DONE and InventoryItem.objects.filter(run=run).count() == 1
    # nach dem Abschluss ist ein neuer Lauf derselben Art moeglich
    zweiter = inventory.start("paperless", dry_run=True, user=admin_user)
    assert zweiter.pk != run.pk and zweiter.status == InventoryStatus.RUNNING
    assert InventoryRun.objects.filter(kind=SyncSystem.PAPERLESS).count() == 2


def test_abbruch_beendet_lauf_und_protokolliert(drei_dokumente, paperless, admin_user):
    run = inventory.start("paperless", dry_run=True, user=admin_user)
    assert inventory.step(run.pk)["continue"] is True

    inventory.abort(run, user=admin_user)
    run.refresh_from_db()
    assert run.status == InventoryStatus.ABORTED and run.finished_at is not None
    aufrufe = len(paperless.calls)
    assert inventory.step(run.pk) == {"continue": False, "status": "aborted"}
    assert len(paperless.calls) == aufrufe and InventoryItem.objects.filter(run=run).count() == 2
    # Fortsetzen belebt einen abgebrochenen Lauf nicht wieder
    inventory.resume(run, user=admin_user)
    run.refresh_from_db()
    assert run.status == InventoryStatus.ABORTED
    start = AuditEvent.objects.get(action="sync.inventory_start", entity_id=run.pk)
    ende = AuditEvent.objects.get(action="sync.inventory_done", entity_id=run.pk)
    assert start.user_id == ende.user_id == admin_user.pk
    assert ende.entity_type == "sync_inventory_run" and ende.after_state == {"aborted": True}
    assert start.id < ende.id

    # danach ist ein neuer Lauf derselben Art moeglich; ein abgeschlossener Lauf laesst sich nicht abbrechen
    zweiter = inventory.start("paperless", dry_run=True, user=admin_user)
    assert zweiter.pk != run.pk
    _bis_ende(zweiter)
    assert zweiter.status == InventoryStatus.DONE
    inventory.abort(zweiter, user=admin_user)
    zweiter.refresh_from_db()
    assert zweiter.status == InventoryStatus.DONE


def test_fehler_bei_einem_dokument_stoppt_den_lauf_nicht(objekt, paperless, admin_user):
    daten_normal = pdf_bytes(LINES_VERKNUEPFT)
    fehlerhaft = paperless.add_document(
        "Fehlerhaft", content=pdf_bytes(LINES_FREMD), original_file_name="Fehlerhaft.pdf"
    )
    normal = paperless.add_document("Normal", content=daten_normal, original_file_name="Normal.pdf")
    paperless.inject("get_metadata", PaperlessError("HTTP 500", status_code=500))

    run = inventory.start("paperless", dry_run=True, user=admin_user)
    ergebnisse = _bis_ende(run)
    assert len(ergebnisse) == 1 and run.status == InventoryStatus.DONE and run.error_message is None
    assert _metadaten_aufrufe(paperless) == [fehlerhaft, normal]
    items = _items(run)
    f, n = items[str(fehlerhaft)], items[str(normal)]
    assert f.disposition == Disposition.ERROR and f.error_message == "HTTP 500"
    assert f.name == "Fehlerhaft.pdf" and f.checksum_sha256 is None and f.document_id is None
    assert n.disposition == Disposition.IMPORT_NEW and n.checksum_sha256 == _sha256(daten_normal)
    assert n.error_message is None
    # der Fehler bleibt sichtbar: Lauf nicht vollstaendig, eine Zeile offen
    assert run.counters["seen"] == 2 and run.counters["complete"] is False
    assert run.counters["dispositions"] == {"error": 1, "import_new": 1}
    assert inventory.summary(run) == {"error": 1, "import_new": 1, "total": 2, "open": 1}
    ende = AuditEvent.objects.get(action="sync.inventory_done", entity_id=run.pk)
    assert ende.after_state["status"] == "done" and ende.after_state["complete"] is False
    assert not SyncOperation.objects.exists()


def test_fehler_der_seite_setzt_lauf_auf_fehlgeschlagen_und_ist_fortsetzbar(objekt, paperless, admin_user):
    pid = paperless.add_document("Einzeln", content=pdf_bytes(LINES_FREMD), original_file_name="Einzeln.pdf")
    paperless.inject("iter_pages", PaperlessUnavailable("Paperless GET: HTTP 503", status_code=503))
    run = inventory.start("paperless", dry_run=True, user=admin_user)

    ergebnis = inventory.step(run.pk)
    assert ergebnis["continue"] is False and "HTTP 503" in ergebnis["error"]
    run.refresh_from_db()
    assert run.status == InventoryStatus.FAILED and run.finished_at is not None
    assert run.error_message == "PaperlessUnavailable: Paperless GET: HTTP 503"
    assert run.counters["complete"] is False and not InventoryItem.objects.filter(run=run).exists()
    assert inventory.step(run.pk) == {"continue": False, "status": "failed"}
    fehlschlag = AuditEvent.objects.get(action="sync.inventory_done", entity_id=run.pk)
    assert fehlschlag.after_state["status"] == "failed"

    inventory.resume(run, user=admin_user)
    run.refresh_from_db()
    assert run.status == InventoryStatus.RUNNING and run.error_message is None
    ergebnisse = _bis_ende(run)
    assert [e["continue"] for e in ergebnisse] == [False]
    assert run.status == InventoryStatus.DONE and run.counters["complete"] is True
    assert _items(run)[str(pid)].disposition == Disposition.IMPORT_NEW
    assert AuditEvent.objects.filter(action="sync.inventory_done", entity_id=run.pk).count() == 2


def test_mehrere_lokale_treffer_ergeben_dublette(objekt, anderes_objekt, paperless, admin_user):
    """Liegt dieselbe Datei lokal mehrfach vor, ist keine eindeutige Verknuepfung moeglich: das Manifest fuehrt
    die Zeile als Dublette mit allen Treffern, das aelteste Dokument als Bezug."""
    daten = pdf_bytes(LINES_VERKNUEPFT)
    erstes = _lokales_dokument(objekt, "Protokoll.pdf", daten, drive_file_id="drive-dublette-1")
    zweites = _lokales_dokument(
        anderes_objekt, "Protokoll_Kopie.pdf", daten, drive_file_id="drive-dublette-2"
    )
    pid = paperless.add_document("Protokoll", content=daten, original_file_name="Protokoll.pdf")
    operationen_vorher = SyncOperation.objects.count()
    verknuepfungen_vorher = ExternalLink.objects.count()

    run = inventory.start("paperless", dry_run=True, user=admin_user)
    assert [e["continue"] for e in _bis_ende(run)] == [False]
    assert run.status == InventoryStatus.DONE and run.counters["complete"] is True
    item = _items(run)[str(pid)]
    assert item.disposition == Disposition.DUPLICATE and item.document_id == erstes.pk
    assert item.object_id == objekt.pk and item.checksum_sha256 == _sha256(daten)
    assert item.details == {"reason": "identische Datei", "matches": [erstes.pk, zweites.pk]}
    assert run.counters["dispositions"] == {"duplicate": 1}
    assert inventory.summary(run) == {"duplicate": 1, "total": 1, "open": 0}
    assert SyncOperation.objects.count() == operationen_vorher
    assert ExternalLink.objects.count() == verknuepfungen_vorher


# ---------------------------------------------------------------- Drive
@pytest.fixture
def drive_bestand(drive, objekt, eingang, paperless, seitengroesse_zwei):
    """Objektordner 623: neue PDF, registrierte PDF ohne Paperless-Verknuepfung, registrierte PDF mit
    Paperless-Verknuepfung, Google-Dokument, Verknuepfung; Eingangsordner: eine unbekannte PDF."""
    ordner = objekt.drive_root_folder_id
    daten_neu = pdf_bytes()
    neu = drive.add_file(ordner, "Neu.pdf", daten_neu)
    daten_registriert = pdf_bytes(LINES_REGISTRIERT)
    registriert = drive.add_file(ordner, "Registriert.pdf", daten_registriert)
    doc_registriert = _lokales_dokument(
        objekt, "Registriert.pdf", daten_registriert, drive_file_id=registriert
    )
    daten_verknuepft = pdf_bytes(LINES_VERKNUEPFT)
    verknuepft = drive.add_file(ordner, "Verknuepft.pdf", daten_verknuepft)
    doc_verknuepft = _lokales_dokument(objekt, "Verknuepft.pdf", daten_verknuepft, drive_file_id=verknuepft)
    pid_verknuepft = paperless.add_document("Verknuepft", content=daten_verknuepft)
    ExternalLink.objects.create(
        document=doc_verknuepft,
        system=SyncSystem.PAPERLESS,
        role=LinkRole.ORIGINAL,
        external_id=str(pid_verknuepft),
        checksum_sha256=doc_verknuepft.sha256,
        state=LinkState.SYNCED,
    )
    gdoc = drive.add_google_doc(ordner, "Protokoll Begehung")
    verweis = drive.add_shortcut(ordner, "Verweis auf Neu", neu)
    eingangsordner = store.get("sync.inbox_folder_id")
    assert eingangsordner and eingang.is_system_inbox
    daten_unbekannt = pdf_bytes(LINES_FREMD)
    unbekannt = drive.add_file(eingangsordner, "Unbekannt.pdf", daten_unbekannt)
    paperless.reset_calls()
    return SimpleNamespace(
        ordner=ordner,
        eingangsordner=eingangsordner,
        neu=neu,
        daten_neu=daten_neu,
        registriert=registriert,
        daten_registriert=daten_registriert,
        doc_registriert=doc_registriert,
        verknuepft=verknuepft,
        doc_verknuepft=doc_verknuepft,
        gdoc=gdoc,
        verweis=verweis,
        unbekannt=unbekannt,
        daten_unbekannt=daten_unbekannt,
    )


def test_drive_trockenlauf_klassifiziert_ohne_registrierung(
    drive_bestand, drive, objekt, eingang, admin_user
):
    b = drive_bestand
    dokumente_vorher = Document.objects.count()
    jobs_vorher = ProcessingJob.objects.count()
    operationen_vorher = SyncOperation.objects.count()
    drive_ops_vorher = len(drive.ops)
    assert services.get_cursor(SyncSystem.DRIVE, "page_token") is None

    run = inventory.start("drive", dry_run=True, user=admin_user)
    cursor = services.get_cursor(SyncSystem.DRIVE, "page_token")
    assert cursor is not None and cursor.value.isdigit() and cursor.meta["baseline"] is True
    assert run.cursor_before == {"drive_page_token": cursor.value}
    drive_id = store.get("drive.root_drive_id", None)
    assert ("start_page_token", (drive_id,)) in drive.ops[drive_ops_vorher:]
    assert run.status == InventoryStatus.RUNNING and run.page_state == {}

    erster = inventory.step(run.pk)
    assert erster == {"continue": True, "processed": 5}
    run.refresh_from_db()
    assert run.status == InventoryStatus.RUNNING and run.counters["seen"] == 5 and run.counters["steps"] == 1
    assert [r["object_id"] for r in run.page_state["roots"]] == [objekt.pk, eingang.pk]
    assert [r["folder_id"] for r in run.page_state["roots"]] == [b.ordner, b.eingangsordner]
    assert run.page_state["index"] == 1 and run.page_state["stack"]  # Unterordner von 623 stehen noch aus
    assert InventoryItem.objects.filter(run=run).count() == 5

    ergebnisse = _bis_ende(run)
    assert ergebnisse[-1] == {"continue": False}
    assert (
        run.status == InventoryStatus.DONE and run.counters["seen"] == 6 and run.counters["complete"] is True
    )
    assert run.page_state["index"] == 2 and run.page_state["stack"] == []

    items = _items(run)
    assert set(items) == {b.neu, b.registriert, b.verknuepft, b.gdoc, b.verweis, b.unbekannt}
    neu = items[b.neu]
    assert neu.disposition == Disposition.IMPORT_NEW and neu.details == {"reason": "nicht registriert"}
    assert neu.object_id == objekt.pk and neu.document_id is None and neu.parent_external_id == b.ordner
    assert neu.name == "Neu.pdf" and neu.mime_type == "application/pdf" and neu.size_bytes == len(b.daten_neu)
    assert neu.checksum_sha256 == _sha256(b.daten_neu) and neu.checksum_md5 == _md5(b.daten_neu)
    registriert = items[b.registriert]
    assert (
        registriert.disposition == Disposition.IMPORT_NEW and registriert.document_id == b.doc_registriert.pk
    )
    assert registriert.details == {"reason": "registriert, noch nicht in Paperless"}
    assert registriert.object_id == objekt.pk and registriert.operation_id is None
    verknuepft = items[b.verknuepft]
    assert (
        verknuepft.disposition == Disposition.LINK_EXISTING and verknuepft.document_id == b.doc_verknuepft.pk
    )
    assert verknuepft.details == {"reason": "registriert, in Paperless"}
    gdoc = items[b.gdoc]
    assert gdoc.disposition == Disposition.EXPORT_SNAPSHOT and gdoc.mime_type == GOOGLE_DOC_MIME
    assert gdoc.details["reason"].startswith("Google-Dokument") and gdoc.name == "Protokoll Begehung"
    assert gdoc.size_bytes is None and gdoc.checksum_md5 is None and gdoc.document_id is None
    verweis = items[b.verweis]
    assert verweis.disposition == Disposition.OUT_OF_SCOPE and verweis.details == {"reason": "Verknüpfung"}
    assert verweis.mime_type == SHORTCUT_MIME
    unbekannt = items[b.unbekannt]
    assert unbekannt.object_id == eingang.pk and unbekannt.disposition == Disposition.IMPORT_NEW
    assert unbekannt.parent_external_id == b.eingangsordner and unbekannt.document_id is None

    # Trockenlauf: nichts registriert, keine Jobs, keine Operationen, nur Lesezugriffe auf Drive
    assert Document.objects.count() == dokumente_vorher
    assert not Document.objects.filter(drive_file_id__in=[b.neu, b.gdoc, b.unbekannt]).exists()
    assert (
        ProcessingJob.objects.count() == jobs_vorher and SyncOperation.objects.count() == operationen_vorher
    )
    assert {name for name, _ in drive.ops[drive_ops_vorher:]} <= READ_METHODS
    assert inventory.summary(run) == {
        "import_new": 3,
        "link_existing": 1,
        "export_snapshot": 1,
        "out_of_scope": 1,
        "total": 6,
        "open": 0,
    }
    assert len(list(inventory.manifest_rows(run))) == 6


def test_drive_echtlauf_registriert_und_uebertraegt(
    drive_bestand, drive, objekt, eingang, paperless, ops, admin_user
):
    b = drive_bestand
    run = inventory.start("drive", dry_run=False, user=admin_user)
    _bis_ende(run)
    assert run.status == InventoryStatus.DONE and run.counters["seen"] == 6
    items = _items(run)

    # neue PDF: registriert und in die Pipeline gegeben
    doc_neu = Document.objects.get(drive_file_id=b.neu)
    assert doc_neu.object_id == objekt.pk and doc_neu.source == "drive_existing"
    assert doc_neu.status == "registered" and doc_neu.sha256 is None
    assert doc_neu.drive_md5 == _md5(b.daten_neu) and doc_neu.size_bytes == len(b.daten_neu)
    assert doc_neu.current_name == "Neu.pdf" and doc_neu.mime_type == "application/pdf"
    job = ProcessingJob.objects.get(document=doc_neu, job_type=JobType.DISCOVER)
    assert job.status == JobStatus.PENDING and job.object_id == objekt.pk
    assert job.payload == {"drive_file_id": b.neu, "md5": _md5(b.daten_neu)}
    assert items[b.neu].disposition == Disposition.IN_PROGRESS and items[b.neu].document_id == doc_neu.pk
    ereignis = AuditEvent.objects.get(
        action="sync.drive_changes", entity_type="document", entity_id=doc_neu.pk
    )
    assert ereignis.after_state["registered"] is True and ereignis.after_state["inbox"] is False
    # Google-Dokument: registriert, Exportfassung folgt nach der Verarbeitung
    doc_gdoc = Document.objects.get(drive_file_id=b.gdoc)
    assert doc_gdoc.mime_type == GOOGLE_DOC_MIME and doc_gdoc.object_id == objekt.pk
    assert ProcessingJob.objects.filter(document=doc_gdoc, job_type=JobType.DISCOVER).exists()
    assert items[b.gdoc].disposition == Disposition.IN_PROGRESS and items[b.gdoc].document_id == doc_gdoc.pk
    # Eingangsordner: Dokument des Eingangsobjekts
    doc_unbekannt = Document.objects.get(drive_file_id=b.unbekannt)
    assert doc_unbekannt.object_id == eingang.pk and doc_unbekannt.source == "drive_existing"
    assert items[b.unbekannt].object_id == eingang.pk
    assert items[b.unbekannt].disposition == Disposition.IN_PROGRESS
    assert items[b.unbekannt].document_id == doc_unbekannt.pk
    assert AuditEvent.objects.get(
        action="sync.drive_changes", entity_type="document", entity_id=doc_unbekannt.pk
    ).after_state["inbox"]
    # Verknuepfung: nicht registriert
    assert not Document.objects.filter(drive_file_id=b.verweis).exists()
    assert items[b.verweis].disposition == Disposition.OUT_OF_SCOPE
    # registrierte PDF mit Pruefsumme: Uebertragung nach Paperless eingereiht
    push = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_PUSH, document=b.doc_registriert)
    assert push.priority == 120 and push.status == OperationStatus.PENDING
    assert push.op_key == op_key(
        OperationKind.PAPERLESS_PUSH, b.doc_registriert.uuid, b.doc_registriert.sha256
    )
    assert push.source_system == SyncSystem.APP
    assert items[b.registriert].disposition == Disposition.IN_PROGRESS
    assert items[b.registriert].operation_id == push.pk
    # bereits verknuepfte PDF: keine Operation
    assert not SyncOperation.objects.filter(document=b.doc_verknuepft).exists()
    assert items[b.verknuepft].disposition == Disposition.LINK_EXISTING
    assert SyncOperation.objects.count() == 1
    assert Document.objects.filter(source="drive_existing").count() == 5
    assert inventory.summary(run)["open"] == 4

    # Uebertragung ausfuehren: Datei kommt aus Drive, landet in Paperless, Manifest wird nachgefuehrt
    ops()
    paperless.process_tasks()
    ops()
    push.refresh_from_db()
    assert push.status == OperationStatus.DONE and push.result["task_id"]
    assert any(name == "download" and args[0] == b.registriert for name, args in drive.ops)
    link = ExternalLink.objects.get(
        document=b.doc_registriert, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL
    )
    assert link.state == LinkState.SYNCED and link.checksum_sha256 == b.doc_registriert.sha256
    assert paperless.documents[int(link.external_id)]["_bytes"] == b.daten_registriert
    assert inventory.refresh_dispositions(run) == 1
    items[b.registriert].refresh_from_db()
    assert items[b.registriert].disposition == Disposition.IMPORT_NEW


def test_drive_umfang_beschraenkt_wurzeln_und_ordnerfehler_bleibt_sichtbar(
    drive_bestand, drive, objekt, anderes_objekt, eingang, admin_user
):
    """Mit Objektumfang liest der Lauf nur die Wurzeln der genannten Objekte, weder fremde Objekte noch den
    Eingangsordner; ein nicht lesbarer Ordner wird als Fehlerzeile gefuehrt, der Lauf laeuft weiter."""
    b = drive_bestand
    fremd = drive.add_file(anderes_objekt.drive_root_folder_id, "Fremd.pdf", pdf_bytes(LINES_FREMD))
    ab_start = len(drive.ops)
    run = inventory.start("drive", dry_run=True, scope={"object_numbers": ["623"]}, user=admin_user)
    assert run.scope == {"object_numbers": ["623"]}
    start = AuditEvent.objects.get(action="sync.inventory_start", entity_id=run.pk)
    assert start.after_state["scope"] == {"object_numbers": ["623"]}

    assert inventory.step(run.pk) == {"continue": True, "processed": 5}
    run.refresh_from_db()
    assert run.page_state["roots"] == [{"object_id": objekt.pk, "folder_id": b.ordner}]
    unterordner = {n.id for n in drive.list_children(b.ordner, folders_only=True)}
    assert unterordner and set(run.page_state["stack"]) <= unterordner

    drive.inject(
        "list_children", DriveError("Ordner nicht lesbar (Testfehler)", status=500, reason="backendError")
    )
    ab = len(drive.ops)
    ergebnisse = _bis_ende(run)
    assert ergebnisse[-1] == {"continue": False} and run.status == InventoryStatus.DONE
    assert run.error_message is None and run.finished_at is not None
    fehlordner = next(args[0] for name, args in drive.ops[ab:] if name == "list_children")
    assert fehlordner in unterordner

    items = _items(run)
    assert set(items) == {b.neu, b.registriert, b.verknuepft, b.gdoc, b.verweis, fehlordner}
    fehler = items[fehlordner]
    assert fehler.disposition == Disposition.ERROR and fehler.object_id == objekt.pk
    assert fehler.error_message == "Ordner nicht lesbar (Testfehler)" and fehler.document_id is None
    assert items[b.neu].disposition == Disposition.IMPORT_NEW
    assert run.counters["seen"] == 5 and run.counters["complete"] is False
    assert run.counters["dispositions"]["error"] == 1
    assert inventory.summary(run)["open"] == 1 and inventory.summary(run)["total"] == 6
    # fremdes Objekt und Eingangsordner liegen ausserhalb des Umfangs
    assert not Document.objects.filter(drive_file_id__in=[fremd, b.unbekannt]).exists()
    assert not any(
        args and args[0] in (anderes_objekt.drive_root_folder_id, b.eingangsordner)
        for name, args in drive.ops[ab_start:]
        if name == "list_children"
    )


# ---------------------------------------------------------------- Oberflaeche
def test_oberflaeche_startet_zeigt_und_schreitet(objekt, paperless, client_as, admin_user):
    pid = paperless.add_document(
        "Nur in Paperless", content=pdf_bytes(LINES_FREMD), original_file_name="Nur_in_Paperless.pdf"
    )
    client = client_as(admin_user)
    resp = client.post(reverse("sync_inventory_start"), {"art": "paperless"})
    run = InventoryRun.objects.get()
    assert resp.status_code == 302 and resp["Location"] == reverse("sync_inventory", args=[run.pk])
    assert run.kind == SyncSystem.PAPERLESS and run.dry_run is True and run.scope == {}
    assert run.status == InventoryStatus.RUNNING and run.started_by_id == admin_user.pk
    start = AuditEvent.objects.get(action="sync.inventory_start", entity_id=run.pk)
    assert start.user_id == admin_user.pk and start.request_id
    # ohne Celery laeuft kein Schritt von selbst
    assert not InventoryItem.objects.filter(run=run).exists()

    resp = client.get(reverse("sync_inventory", args=[run.pk]))
    assert resp.status_code == 200
    html = resp.content.decode()
    for _code, name in Disposition.choices:
        assert name in html
    assert "Trockenlauf" in html and "Einen Schritt ausführen" in html and "keine Zeilen" in html
    assert resp.context["summary"]["total"] == 0 and resp.context["can_step"] is True
    # ein zweiter Start derselben Art wird mit Hinweis auf die Verwaltungsseite abgewiesen
    resp = client.post(reverse("sync_inventory_start"), {"art": "paperless"})
    assert resp.status_code == 302 and resp["Location"] == reverse("sync_admin")
    assert InventoryRun.objects.count() == 1

    resp = client.post(reverse("sync_inventory_action", args=[run.pk]), {"action": "step"})
    assert resp.status_code == 302 and resp["Location"] == reverse("sync_inventory", args=[run.pk])
    run.refresh_from_db()
    assert run.status == InventoryStatus.DONE and run.counters["seen"] == 1
    item = InventoryItem.objects.get(run=run, external_id=str(pid))
    assert item.disposition == Disposition.IMPORT_NEW
    resp = client.get(reverse("sync_inventory", args=[run.pk]))
    assert resp.status_code == 200 and "Nur_in_Paperless.pdf" in resp.content.decode()
    assert resp.context["summary"] == {"import_new": 1, "total": 1, "open": 0}
    resp = client.get(reverse("sync_inventory", args=[run.pk]), {"stand": "link_existing"})
    assert (
        resp.status_code == 200 and resp.context["rows"] == [] and resp.context["filter"] == "link_existing"
    )
    assert client.post(reverse("sync_inventory_action", args=[run.pk]), {"action": "x"}).status_code == 400

    # echter Drive-Lauf mit Umfang; Pausieren, Fortsetzen und Abbrechen ueber die Oberflaeche
    resp = client.post(reverse("sync_inventory_start"), {"art": "drive", "echt": "1", "objekte": "623; 624"})
    assert resp.status_code == 302
    drive_run = InventoryRun.objects.get(kind=SyncSystem.DRIVE)
    assert drive_run.dry_run is False and drive_run.scope == {"object_numbers": ["623", "624"]}
    assert drive_run.cursor_before["drive_page_token"]
    for aktion, status in (("pause", "paused"), ("resume", "running"), ("abort", "aborted")):
        resp = client.post(reverse("sync_inventory_action", args=[drive_run.pk]), {"action": aktion})
        assert resp.status_code == 302
        drive_run.refresh_from_db()
        assert drive_run.status == status
    assert AuditEvent.objects.filter(action="sync.inventory_pause", entity_id=drive_run.pk).exists()
    assert not InventoryItem.objects.filter(run=drive_run).exists()


def test_sachbearbeiter_hat_keinen_zugriff_auf_bestandslaeufe(
    objekt, paperless, client_as, clerk_user, admin_user
):
    paperless.add_document("Einzeln", content=pdf_bytes(LINES_FREMD), original_file_name="Einzeln.pdf")
    run = inventory.start("paperless", dry_run=True, user=admin_user)
    client = client_as(clerk_user)
    assert client.post(reverse("sync_inventory_start"), {"art": "paperless"}).status_code == 403
    assert client.get(reverse("sync_inventory", args=[run.pk])).status_code == 403
    assert client.post(reverse("sync_inventory_action", args=[run.pk]), {"action": "step"}).status_code == 403
    assert client.get(reverse("sync_admin")).status_code == 403
    run.refresh_from_db()
    assert run.status == InventoryStatus.RUNNING and not InventoryItem.objects.filter(run=run).exists()
    assert InventoryRun.objects.count() == 1
    verweigert = AuditEvent.objects.filter(action="auth.denied", user_id=clerk_user.pk)
    assert verweigert.count() == 4
    assert {e.after_state["permission"] for e in verweigert} == {"sync.manage"}
