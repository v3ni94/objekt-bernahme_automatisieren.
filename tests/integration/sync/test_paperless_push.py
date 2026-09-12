"""Ausgang nach Paperless (Fallgruppe A): Upload in der Anwendung wird nach dem Hash uebertragen, die Aufgabe
verfolgt, die Verknuepfung mit Pruefsumme gespeichert; Dubletten werden verknuepft statt erneut hochgeladen;
ohne Schreibrecht (Modus, Pilotumfang) passiert nichts; Verbindungsabbruch fuehrt zu Wiederholung mit
UUID-Suche statt blindem Wiederholungs-Upload; nicht uebertragbare Dateien erhalten einen Indexbeleg,
Google-Dokumente eine Exportfassung."""

from __future__ import annotations

import hashlib

import pytest
from tests.integration.sync.conftest import pdf_bytes

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document
from apps.sync import config, hooks
from apps.sync.models import ExternalLink, LinkRole, LinkState, OperationKind, OperationStatus, SyncOperation
from apps.sync.paperless.errors import PaperlessUnavailable

pytestmark = pytest.mark.django_db


def _push_ops(doc):
    return SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PUSH, document__uuid=doc.uuid)


def _field_values(paperless, remote_id: int) -> dict[str, str]:
    names = {f["id"]: f["name"] for f in paperless.custom_fields.values()}
    return {names[cf["field"]]: cf["value"] for cf in paperless.documents[remote_id]["custom_fields"]}


def test_upload_wird_uebertragen_und_verknuepft(objekt, paperless, run_all, ops):
    data = pdf_bytes()
    doc, _ = ingest.ingest_upload(objekt, filename="Wartung_Heizung.pdf", data=data)
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.sha256 == hashlib.sha256(data).hexdigest()
    op = _push_ops(doc).get()
    assert op.status == OperationStatus.PENDING and op.source_revision == doc.sha256

    ops()  # Upload: Aufgabe in Paperless angelegt, Warten auf Ergebnis vorgemerkt
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result["task_id"]
    assert [c[0] for c in paperless.calls if c[0] == "post_document"] == ["post_document"]
    awaiting = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_AWAIT_TASK, document=doc)
    assert awaiting.status == OperationStatus.PENDING
    assert not ExternalLink.objects.filter(document=doc, system="paperless").exists()

    ops()  # Aufgabe noch PENDING: zurueckgestellt, kein zweiter Upload
    awaiting.refresh_from_db()
    assert awaiting.status == OperationStatus.PENDING and awaiting.attempt_count == 0
    assert [c[0] for c in paperless.calls].count("post_document") == 1

    paperless.process_tasks()
    ops()
    link = ExternalLink.objects.get(document=doc, system="paperless", role=LinkRole.ORIGINAL)
    remote_id = int(link.external_id)
    assert link.state == LinkState.SYNCED and link.checksum_sha256 == doc.sha256
    assert link.size_bytes == len(data) and link.last_synced_at is not None
    remote = paperless.documents[remote_id]
    tag_id = next(t["id"] for t in paperless.tags.values() if t["name"] == "MHV-Sync")
    assert tag_id in remote["tags"]
    values = _field_values(paperless, remote_id)
    assert values["MHV Dokument-UUID"] == str(doc.uuid)
    assert values["MHV Objekt"].startswith("623") and "Musterstadt" in values["MHV Objekt"]
    assert "Mustermann" not in str(values)  # keine Personendaten in Paperless-Feldern
    assert AuditEvent.objects.filter(action="sync.paperless_push", entity_id=doc.pk).exists()
    # Ablage in Drive ist inzwischen erfolgt: Drive-Link wird nachgezogen, danach ist alles unveraendert
    doc.refresh_from_db()
    if doc.drive_file_id:
        assert values["MHV Drive-Link"].endswith(f"{doc.drive_file_id}/view")
    assert not SyncOperation.objects.filter(
        status__in=[OperationStatus.PENDING, OperationStatus.FAILED, OperationStatus.BLOCKED]
    ).exists()


def test_dublette_in_paperless_wird_verknuepft_statt_hochgeladen(objekt, paperless, run_all, ops):
    data = pdf_bytes()
    vorhanden = paperless.add_document("Schon da", content=data, original_file_name="alt.pdf")
    doc, _ = ingest.ingest_upload(objekt, filename="Wartung_Heizung.pdf", data=data)
    run_all(objekt)
    ops()
    paperless.process_tasks()  # Paperless lehnt den Upload als Dublette ab
    ops()
    link = ExternalLink.objects.get(document__uuid=doc.uuid, system="paperless", role=LinkRole.ORIGINAL)
    assert int(link.external_id) == vorhanden and link.state == LinkState.SYNCED
    ereignis = (
        AuditEvent.objects.filter(action="sync.paperless_push", entity_id=doc.pk).order_by("id").first()
    )
    assert ereignis.after_state["linked_existing"] is True and "Dublette" in ereignis.after_state["reason"]
    assert len(paperless.documents) == 1
    # Metadaten werden an der vorhandenen Kopie nachgetragen, fremde Angaben bleiben
    assert paperless.documents[vorhanden]["title"] == "Schon da"
    assert _field_values(paperless, vorhanden)["MHV Dokument-UUID"] == str(doc.uuid)


def test_uuid_suche_verhindert_zweiten_upload(objekt, paperless, run_all, ops):
    data = pdf_bytes()
    doc, _ = ingest.ingest_upload(objekt, filename="Wartung_Heizung.pdf", data=data)
    run_all(objekt)
    doc.refresh_from_db()
    # Frueherer Upload mit unklarem Ausgang: Kopie mit der UUID liegt schon in Paperless, aber anderer Datei
    uuid_field = next(f["id"] for f in paperless.custom_fields.values() if f["name"] == "MHV Dokument-UUID")
    fruehere = paperless.add_document(
        "Frueher", content=b"anderer inhalt", custom_fields={uuid_field: str(doc.uuid)}
    )
    ops()
    assert "post_document" not in [c[0] for c in paperless.calls]
    link = ExternalLink.objects.get(document=doc, system="paperless")
    assert int(link.external_id) == fruehere and link.state == LinkState.SYNCED
    ereignis = (
        AuditEvent.objects.filter(action="sync.paperless_push", entity_id=doc.pk).order_by("id").first()
    )
    assert ereignis.after_state["linked_existing"] is True and "UUID" in ereignis.after_state["reason"]


def test_verbindungsabbruch_fuehrt_zu_wiederholung_mit_rueckstau(objekt, paperless, run_all, ops):
    doc, _ = ingest.ingest_upload(objekt, filename="Wartung_Heizung.pdf", data=pdf_bytes())
    run_all(objekt)
    paperless.inject("post_document", PaperlessUnavailable("Paperless POST: HTTP 503"))
    ops()
    op = _push_ops(doc).get()
    assert op.status == OperationStatus.PENDING and op.attempt_count == 1
    assert op.next_attempt_at is not None and "unklarem Ausgang" in (op.last_error or "")
    assert operations_not_due()
    ops()  # zweiter Versuch: zuerst UUID-Suche, dann Upload
    names = [c[0] for c in paperless.calls]
    assert names.index("find_by_custom_field") < len(names) - 1 and names.count("post_document") == 2
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE


def operations_not_due() -> bool:
    from apps.sync import operations

    return operations.run_pending() == []


def test_ohne_schreibrecht_keine_operation(objekt, anderes_objekt, paperless, run_all, admin_user):
    assert config.writes_allowed(objekt) and not config.writes_allowed(anderes_objekt)
    doc, _ = ingest.ingest_upload(anderes_objekt, filename="Fremd.pdf", data=pdf_bytes())
    run_all(anderes_objekt)
    assert not _push_ops(doc).exists()
    # nachtraeglich erzwungen: Operation blockiert mit Begruendung, kein Aufruf nach Paperless
    store.set("paperless.mode", "full", user=admin_user, reason="Test")
    doc.refresh_from_db()
    hooks.on_document_hashed(doc)
    store.set("paperless.mode", "pilot", user=admin_user, reason="Test")
    from tests.integration.sync.conftest import run_ops

    run_ops()
    op = _push_ops(doc).get()
    assert op.status == OperationStatus.BLOCKED and "Pilotumfang" in op.blocked_reason
    assert "post_document" not in [c[0] for c in paperless.calls]
    # readonly: auch fuer das Pilotobjekt kein Schreiben
    store.set("paperless.mode", "readonly", user=admin_user, reason="Test")
    assert not config.writes_allowed(objekt)


def test_nicht_uebertragbare_datei_erhaelt_indexbeleg(objekt, paperless, ops):
    from django.utils import timezone

    doc = Document.objects.create(
        object=objekt,
        sha256="c" * 64,
        size_bytes=12_345_678,
        mime_type="application/acad",
        original_name="Grundriss_EG.dwg",
        current_name="Grundriss_EG.dwg",
        source="drive_existing",
        drive_file_id="drive-dwg-1",
        status="hashed",
        first_seen_at=timezone.now(),
    )
    hooks.on_document_hashed(doc)
    ops()
    push = _push_ops(doc).get()
    assert push.status == OperationStatus.SKIPPED and "Indexbeleg" in push.result["skipped"]
    stub = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_INDEX_STUB, document=doc)
    assert stub.status == OperationStatus.DONE
    paperless.process_tasks()
    ops()
    link = ExternalLink.objects.get(document=doc, system="paperless")
    assert link.role == LinkRole.INDEX_STUB and link.state == LinkState.SYNCED
    remote = paperless.documents[int(link.external_id)]
    assert remote["_bytes"].startswith(b"%PDF") and "Grundriss_EG" in remote["title"]
    assert _field_values(paperless, remote["id"])["MHV Dokument-UUID"] == str(doc.uuid)
    assert not ExternalLink.objects.filter(document=doc, role=LinkRole.ORIGINAL).exists()


def test_google_dokument_erhaelt_exportfassung(objekt, paperless, drive, ops):
    from django.utils import timezone

    file_id = drive.add_file(
        objekt.drive_root_folder_id, "Protokoll Begehung", b"", "application/vnd.google-apps.document"
    )
    doc = Document.objects.create(
        object=objekt,
        sha256="d" * 64,
        size_bytes=0,
        mime_type="application/vnd.google-apps.document",
        original_name="Protokoll Begehung",
        current_name="Protokoll Begehung",
        source="drive_existing",
        drive_file_id=file_id,
        status="hashed",
        first_seen_at=timezone.now(),
    )
    hooks.on_document_hashed(doc)
    ops()
    snap = SyncOperation.objects.get(kind=OperationKind.DRIVE_EXPORT_SNAPSHOT, document=doc)
    assert snap.status == OperationStatus.DONE
    paperless.process_tasks()
    ops()
    link = ExternalLink.objects.get(document=doc, system="paperless")
    assert link.role == LinkRole.SNAPSHOT and link.external_version
    assert paperless.documents[int(link.external_id)]["_bytes"].startswith(b"%PDF")
    # Original in Drive unveraendert
    assert drive.get(file_id).mime_type == "application/vnd.google-apps.document"
    assert not any(c[0] in ("upload", "update_content", "delete", "trash") for c in drive.ops)
