"""Drive-Aenderungsprotokoll und Kennzeichen (Fallgruppe C): der erste Lauf setzt nur einen Ausgangspunkt ohne
Rueckschau; neue Dateien in Objektordnern oder im Eingangsordner werden registriert und in die Pipeline gegeben,
Dateien ausserhalb bleiben unbeachtet, Ordneraenderungen erzeugen kein Dokument; Papierkorb, endgueltige
Loeschung und Inhaltsaenderung bekannter Dateien werden an der Verknuepfung vermerkt und als Konflikt vorgelegt,
nie lokal geloescht; ein ungueltiger Cursor wird zurueckgesetzt; appProperties werden idempotent gesetzt; bei
ausgeschaltetem Schalter laeuft der Task nicht."""

from __future__ import annotations

import hashlib

import pytest
from django.utils import timezone
from tests.integration.sync.conftest import pdf_bytes

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents.models import Document
from apps.drive.adapter import READ_METHODS, TransientError
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import drive_changes, hooks, operations, services
from apps.sync.models import (
    ExternalLink,
    LinkRole,
    LinkState,
    OperationKind,
    OperationStatus,
    SyncCursor,
    SyncOperation,
    SyncSystem,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def aenderungen(drive, objekt, eingang, admin_user):
    """Aenderungsprotokoll eingeschaltet; Objekt 623 mit Ordnerstruktur und Eingangsordner liegen in Drive."""
    store.set("sync.drive_changes_enabled", True, user=admin_user, reason="Test")
    return drive


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _cursor() -> SyncCursor | None:
    return services.get_cursor(SyncSystem.DRIVE, drive_changes.CURSOR_PAGE)


def _baseline() -> str:
    """Erster Lauf: setzt den Ausgangspunkt und liefert das Token."""
    result = drive_changes.poll(force=True)
    assert "baseline" in result, result
    return result["baseline"]


def _konflikte(doc, subtype: str):
    return ReviewCase.objects.filter(
        case_type=CaseType.SYNC_CONFLICT, case_subtype=subtype, document=doc, status=CaseStatus.OPEN
    )


def _protokoll_ereignisse(**after) -> list[AuditEvent]:
    """Audit-Ereignisse sync.drive_changes auf Systemebene, gefiltert nach Schluesseln in after_state."""
    rows = AuditEvent.objects.filter(action="sync.drive_changes", entity_type="sync").order_by("id")
    return [e for e in rows if all((e.after_state or {}).get(k) == v for k, v in after.items())]


def _registriere(drive, objekt, name: str = "Neu.pdf", data: bytes | None = None) -> tuple[Document, str]:
    """Ausgangspunkt setzen, neue Datei im Objektordner ablegen und ueber das Protokoll registrieren lassen."""
    _baseline()
    file_id = drive.add_file(objekt.drive_root_folder_id, name, data or pdf_bytes())
    result = drive_changes.poll(force=True)
    assert result["registered"] == 1, result
    return Document.objects.get(drive_file_id=file_id), file_id


def _verknuepfe(doc) -> ExternalLink:
    """Drive-Verknuepfung (System drive, Rolle original) fuer ein registriertes Dokument anlegen."""
    return ExternalLink.objects.create(
        document=doc,
        system=SyncSystem.DRIVE,
        role=LinkRole.ORIGINAL,
        external_id=doc.drive_file_id,
        checksum_md5=doc.drive_md5,
        state=LinkState.SYNCED,
    )


def test_erster_lauf_setzt_ausgangspunkt_ohne_rueckschau(aenderungen, objekt):
    drive = aenderungen
    altbestand = drive.add_file(objekt.drive_root_folder_id, "Altbestand.pdf", pdf_bytes())
    assert _cursor() is None
    drive_id = store.get("drive.root_drive_id", None)

    result = drive_changes.poll(force=True)
    token = result["baseline"]
    assert result == {"baseline": token} and token.isdigit()
    row = _cursor()
    assert row is not None and row.value == token
    assert row.meta["baseline"] is True and row.meta["drive_id"] == drive_id and row.meta["set_at"]
    assert ("start_page_token", (drive_id,)) in drive.ops
    assert "list_changes" not in [name for name, _ in drive.ops]
    assert len(_protokoll_ereignisse(baseline_cursor=token, drive_id=drive_id)) == 1
    letzter = services.get_cursor(SyncSystem.DRIVE, drive_changes.CURSOR_LAST)
    assert letzter is not None and letzter.meta == {"baseline": True}

    # zweiter Lauf: Bestand vor dem Ausgangspunkt wird nicht nachgezogen
    wieder = drive_changes.poll(force=True)
    assert wieder["registered"] == 0 and wieder["pages"] == 1 and wieder["cursor"] == token
    assert not Document.objects.filter(drive_file_id=altbestand).exists()
    assert not Document.objects.filter(object=objekt).exists()
    assert not ProcessingJob.objects.filter(object=objekt, job_type=JobType.DISCOVER).exists()
    assert _cursor().value == token


def test_neue_datei_im_objektordner_wird_registriert(aenderungen, objekt):
    drive = aenderungen
    token = _baseline()
    data = pdf_bytes()
    file_id = drive.add_file(objekt.drive_root_folder_id, "Neu.pdf", data)

    result = drive_changes.poll(force=True)
    assert result["registered"] == 1 and result["out_of_scope"] == 0 and result["folders"] == 0
    doc = Document.objects.get(drive_file_id=file_id)
    assert doc.object_id == objekt.pk and doc.source == "drive_existing" and doc.status == "registered"
    assert doc.drive_md5 == _md5(data) and doc.size_bytes == len(data)
    assert doc.current_name == "Neu.pdf" and doc.mime_type == "application/pdf" and doc.sha256 is None
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.DISCOVER)
    assert job.status == JobStatus.PENDING and job.object_id == objekt.pk
    assert job.payload == {"drive_file_id": file_id, "md5": _md5(data)}
    ereignis = AuditEvent.objects.get(action="sync.drive_changes", entity_type="document", entity_id=doc.pk)
    assert ereignis.object_id == objekt.pk
    assert ereignis.after_state["registered"] is True and ereignis.after_state["inbox"] is False
    assert ereignis.after_state["drive_file_id"] == file_id
    assert len(_protokoll_ereignisse(registered=1)) == 1
    row = _cursor()
    assert int(row.value) > int(token) and row.value == result["cursor"]
    assert not row.meta.get("baseline")

    # erneuter Lauf ohne Aenderungen: nichts Neues, Cursor bleibt
    wieder = drive_changes.poll(force=True)
    assert wieder["registered"] == 0 and wieder["unchanged_known"] == 0 and wieder["cursor"] == row.value
    assert Document.objects.filter(drive_file_id=file_id).count() == 1
    assert ProcessingJob.objects.filter(document=doc).count() == 1
    assert _cursor().value == row.value
    assert len(_protokoll_ereignisse(registered=1)) == 1


def test_neue_datei_im_eingangsordner_landet_im_eingangsobjekt(aenderungen, objekt, eingang):
    drive = aenderungen
    _baseline()
    eingangsordner = store.get("sync.inbox_folder_id")
    assert eingangsordner and eingang.is_system_inbox
    file_id = drive.add_file(eingangsordner, "Unbekannt.pdf", pdf_bytes(["Schreiben ohne Objektbezug"]))

    result = drive_changes.poll(force=True)
    assert result["registered"] == 1 and result["out_of_scope"] == 0
    doc = Document.objects.get(drive_file_id=file_id)
    assert doc.object_id == eingang.pk and doc.source == "drive_existing" and doc.status == "registered"
    assert not Document.objects.filter(object=objekt).exists()
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.DISCOVER)
    assert job.status == JobStatus.PENDING and job.object_id == eingang.pk
    ereignis = AuditEvent.objects.get(action="sync.drive_changes", entity_type="document", entity_id=doc.pk)
    assert ereignis.after_state["inbox"] is True and ereignis.object_id == eingang.pk


def test_datei_ausserhalb_der_objektordner_bleibt_unbeachtet(aenderungen, objekt):
    drive = aenderungen
    _baseline()
    direkt = drive.add_file(drive.root_id, "Fremd.pdf", pdf_bytes())
    fremder_ordner = drive.add_folder(drive.root_id, "Sonstiges")
    verschachtelt = drive.add_file(fremder_ordner, "Auch_fremd.pdf", pdf_bytes())
    ops_vorher = len(drive.ops)

    result = drive_changes.poll(force=True)
    assert result["out_of_scope"] == 2 and result["folders"] == 1 and result["registered"] == 0
    assert not Document.objects.filter(drive_file_id__in=[direkt, verschachtelt]).exists()
    assert not Document.objects.filter(object=objekt).exists()
    assert not ProcessingJob.objects.filter(job_type=JobType.DISCOVER).exists()
    assert not AuditEvent.objects.filter(action="sync.drive_changes", entity_type="document").exists()
    # Zuordnung ueber die Elternkette bis zur Wurzel, ohne Schreibzugriff
    neue_ops = drive.ops[ops_vorher:]
    assert ("get", (fremder_ordner,)) in neue_ops
    assert neue_ops and {name for name, _ in neue_ops} <= READ_METHODS


def test_ordneraenderung_erzeugt_kein_dokument(aenderungen, objekt):
    drive = aenderungen
    _baseline()
    protokoll_vorher = AuditEvent.objects.filter(action="sync.drive_changes").count()
    node = drive.create_folder(objekt.drive_root_folder_id, "07_Neu")
    unterordner = drive.create_folder(node.id, "Unterlagen")

    result = drive_changes.poll(force=True)
    assert result["folders"] == 2 and result["registered"] == 0 and result["out_of_scope"] == 0
    assert not Document.objects.filter(drive_file_id__in=[node.id, unterordner.id]).exists()
    assert not Document.objects.filter(object=objekt).exists()
    assert not ProcessingJob.objects.filter(object=objekt, job_type=JobType.DISCOVER).exists()
    assert AuditEvent.objects.filter(action="sync.drive_changes").count() == protokoll_vorher
    assert int(_cursor().value) == int(result["cursor"])


def test_papierkorb_in_drive_wird_als_konflikt_vorgelegt(aenderungen, objekt):
    drive = aenderungen
    doc, file_id = _registriere(drive, objekt)
    link = _verknuepfe(doc)
    drive.trash(file_id)
    ops_vorher = len(drive.ops)

    result = drive_changes.poll(force=True)
    assert result["trashed_known"] == 1 and result["trashed_unknown"] == 0 and result["registered"] == 0
    link.refresh_from_db()
    assert link.state == LinkState.TRASHED and "Papierkorb" in link.state_reason
    fall = _konflikte(doc, "drive_trashed").get()
    assert fall.object_id == objekt.pk and fall.priority == 60
    assert fall.context["drive_file_id"] == file_id and fall.context["name"] == "Neu.pdf"
    assert fall.context["time"]
    assert len(_protokoll_ereignisse(trashed_known=1)) == 1
    # nichts wird lokal geloescht, nichts in Drive endgueltig entfernt
    doc.refresh_from_db()
    assert doc.deleted_at is None and doc.status == "registered"
    assert ExternalLink.objects.filter(document=doc).count() == 1
    neue_ops = [name for name, _ in drive.ops[ops_vorher:]]
    assert neue_ops and set(neue_ops) <= READ_METHODS
    assert not any(name in ("trash", "delete", "delete_permanently") for name in neue_ops)
    assert drive.get(file_id).trashed is True

    # erneuter Lauf ohne neue Aenderung: kein zweiter Fall
    drive_changes.poll(force=True)
    assert _konflikte(doc, "drive_trashed").count() == 1


def test_endgueltig_entfernte_datei_wird_als_fehlend_vermerkt(aenderungen, objekt):
    drive = aenderungen
    doc, file_id = _registriere(drive, objekt)
    link = _verknuepfe(doc)
    fremd = drive.add_file(drive.root_id, "Fremd.pdf", pdf_bytes())
    drive.delete_permanently(file_id)
    drive.delete_permanently(fremd)

    result = drive_changes.poll(force=True)
    assert result["removed_known"] == 1 and result["removed_unknown"] == 1 and result["registered"] == 0
    link.refresh_from_db()
    assert link.state == LinkState.MISSING and "entfernt" in link.state_reason
    fall = _konflikte(doc, "drive_removed").get()
    assert fall.object_id == objekt.pk and fall.context["drive_file_id"] == file_id and fall.context["time"]
    assert len(_protokoll_ereignisse(removed_known=1)) == 1
    doc.refresh_from_db()
    assert doc.deleted_at is None and doc.status == "registered"
    assert ExternalLink.objects.filter(document=doc).count() == 1
    assert drive.get(file_id) is None
    assert not _konflikte(doc, "drive_trashed").exists()


def test_inhaltsaenderung_in_drive_wird_als_konflikt_vorgelegt(aenderungen, objekt):
    drive = aenderungen
    alt = pdf_bytes()
    doc, file_id = _registriere(drive, objekt, data=alt)
    link = _verknuepfe(doc)
    neu = pdf_bytes(["Objekt 623 Musterstadt, Musterstraße 49", "Korrigierte Rechnung Nr. 2026-0816"])
    assert _md5(neu) != _md5(alt)
    drive.set_content(file_id, neu)
    assert drive.revisions(file_id) == 2

    result = drive_changes.poll(force=True)
    assert result["changed_known"] == 1 and result["unchanged_known"] == 0 and result["registered"] == 0
    link.refresh_from_db()
    assert link.state == LinkState.CHANGED_REMOTE and link.checksum_md5 == _md5(neu)
    assert link.external_version == "rev-2"
    fall = _konflikte(doc, "drive_content_changed").get()
    assert fall.object_id == objekt.pk and fall.context["drive_file_id"] == file_id
    assert fall.context["old_md5"] == _md5(alt) and fall.context["new_md5"] == _md5(neu)
    assert fall.context["name"] == "Neu.pdf" and fall.context["version"] == "2"
    assert len(_protokoll_ereignisse(changed_known=1)) == 1
    # lokaler Stand bleibt unveraendert, bis der Konflikt entschieden ist; kein zweites Dokument
    doc.refresh_from_db()
    assert doc.drive_md5 == _md5(alt) and doc.deleted_at is None and doc.status == "registered"
    assert Document.objects.filter(drive_file_id=file_id).count() == 1
    assert ProcessingJob.objects.filter(document=doc).count() == 1
    assert "download" not in [name for name, _ in drive.ops]


def test_ungueltiger_cursor_wird_zurueckgesetzt(aenderungen, objekt):
    drive = aenderungen
    _baseline()
    drive_id = store.get("drive.root_drive_id", None)
    services.set_cursor(SyncSystem.DRIVE, drive_changes.CURSOR_PAGE, "999999", {"drive_id": drive_id})
    file_id = drive.add_file(objekt.drive_root_folder_id, "In_der_Luecke.pdf", pdf_bytes())

    result = drive_changes.poll(force=True)
    assert result == {"cursor_invalid": True}
    row = _cursor()
    assert row.value is None and row.meta["invalid_at"] and row.meta["drive_id"] == drive_id
    ereignisse = _protokoll_ereignisse(cursor_invalid=True)
    assert len(ereignisse) == 1 and "Bestandslauf" in ereignisse[0].after_state["hint"]
    letzter = services.get_cursor(SyncSystem.DRIVE, drive_changes.CURSOR_LAST)
    assert letzter.meta == {"cursor_invalid": True}
    assert not Document.objects.filter(drive_file_id=file_id).exists()
    assert [name for name, _ in drive.ops].count("list_changes") == 1

    # naechster Lauf: neuer Ausgangspunkt; die Luecke schliesst der Bestandslauf, nicht das Protokoll
    wieder = drive_changes.poll(force=True)
    assert "baseline" in wieder and wieder["baseline"].isdigit()
    row = _cursor()
    assert (
        row.value == wieder["baseline"] and row.meta["baseline"] is True and row.meta["drive_id"] == drive_id
    )
    assert len(_protokoll_ereignisse(baseline_cursor=wieder["baseline"])) == 1
    danach = drive_changes.poll(force=True)
    assert danach["registered"] == 0 and danach["cursor"] == wieder["baseline"]
    assert not Document.objects.filter(drive_file_id=file_id).exists()


def test_mehrseitiges_protokoll_setzt_nach_abbruch_ohne_verlust_fort(aenderungen, objekt, monkeypatch):
    """Der Cursor wird nach jeder verarbeiteten Seite gesichert. Bricht Drive zwischen zwei Seiten ab, bleibt
    der Cursor auf der letzten verarbeiteten Seite stehen; der naechste Lauf registriert den Rest, ohne eine Datei
    zu verlieren oder doppelt anzulegen."""
    drive = aenderungen
    start = _baseline()
    dateien = [
        drive.add_file(objekt.drive_root_folder_id, f"Rechnung_{n}.pdf", pdf_bytes([f"Rechnung {n}"]))
        for n in range(1, 6)
    ]
    original = drive.list_changes
    aufrufe = {"n": 0}

    def seitenweise(token, *, drive_id=None, page_size=1000):
        aufrufe["n"] += 1
        if aufrufe["n"] == 2:
            drive.inject("list_changes", TransientError("Drive: HTTP 503", status=503))
        return original(token, drive_id=drive_id, page_size=2)

    monkeypatch.setattr(drive, "list_changes", seitenweise)

    result = drive_changes.poll(force=True)
    assert result["error"].startswith("Drive: HTTP 503")
    assert result["registered"] == 2 and result["pages"] == 1 and "cursor" not in result
    assert Document.objects.filter(drive_file_id__in=dateien).count() == 2
    row = _cursor()
    zwischenstand = row.value
    assert int(zwischenstand) > int(start) and not row.meta.get("baseline")
    letzter = services.get_cursor(SyncSystem.DRIVE, drive_changes.CURSOR_LAST)
    assert "HTTP 503" in letzter.meta["error"]

    # Fortsetzung ab dem Zwischenstand: restliche drei Dateien, keine Dublette, Cursor am Ende des Protokolls
    wieder = drive_changes.poll(force=True)
    assert "error" not in wieder and wieder["registered"] == 3 and wieder["pages"] == 2
    assert Document.objects.filter(drive_file_id__in=dateien).count() == 5
    assert all(Document.objects.filter(drive_file_id=fid).count() == 1 for fid in dateien)
    assert ProcessingJob.objects.filter(object=objekt, job_type=JobType.DISCOVER).count() == 5
    assert AuditEvent.objects.filter(action="sync.drive_changes", entity_type="document").count() == 5
    assert len(_protokoll_ereignisse(registered=3)) == 1
    row = _cursor()
    assert row.value == wieder["cursor"] and int(row.value) > int(zwischenstand)
    protokollaufrufe = [args for name, args in drive.ops if name == "list_changes"]
    assert len(protokollaufrufe) == 4
    assert protokollaufrufe[0][0] == start
    assert protokollaufrufe[1][0] == zwischenstand == protokollaufrufe[2][0]

    # dritter Lauf: nichts mehr offen
    danach = drive_changes.poll(force=True)
    assert danach["registered"] == 0 and danach["pages"] == 1 and danach["cursor"] == row.value


def test_kennzeichen_werden_gesetzt_und_sind_idempotent(aenderungen, objekt, ops):
    drive = aenderungen
    data = pdf_bytes()
    file_id = drive.add_file(objekt.drive_root_folder_id, "Wartung_Heizung.pdf", data)
    doc = Document.objects.create(
        object=objekt,
        sha256=hashlib.sha256(data).hexdigest(),
        drive_md5=_md5(data),
        size_bytes=len(data),
        mime_type="application/pdf",
        original_name="Wartung_Heizung.pdf",
        current_name="Wartung_Heizung.pdf",
        source="drive_existing",
        drive_file_id=file_id,
        status="filed",
        first_seen_at=timezone.now(),
    )
    hooks.on_document_filed(doc)
    op = SyncOperation.objects.get(kind=OperationKind.DRIVE_SET_PROPS, document=doc)
    assert op.status == OperationStatus.PENDING and op.system == SyncSystem.DRIVE
    assert op.source_revision == file_id

    ops()
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE
    assert op.result["changed"] == ["document_id", "mhv_gen", "mhv_uuid", "object_id", "sha256"]
    erwartet = {
        "mhv_uuid": str(doc.uuid),
        "document_id": str(doc.pk),
        "object_id": str(objekt.pk),
        "mhv_gen": "1",
        "sha256": doc.sha256,
    }
    assert drive.get(file_id).app_properties == erwartet
    assert [args for name, args in drive.ops if name == "set_app_properties"] == [(file_id, erwartet)]
    link = ExternalLink.objects.get(document=doc, system=SyncSystem.DRIVE, role=LinkRole.ORIGINAL)
    assert link.external_id == file_id and link.state == LinkState.SYNCED
    assert link.state_reason == "Kennzeichen gesetzt" and link.last_synced_at is not None
    assert link.checksum_md5 == _md5(data) and link.checksum_sha256 == doc.sha256
    assert link.parent_external_id == objekt.drive_root_folder_id and link.size_bytes == len(data)
    assert link.synced_fields["props"] == erwartet and link.synced_fields["name"] == "Wartung_Heizung.pdf"
    assert AuditEvent.objects.filter(action="sync.drive_props_set", entity_id=doc.pk).count() == 1
    ereignis = AuditEvent.objects.get(action="sync.drive_props_set", entity_id=doc.pk)
    assert ereignis.after_state["props"] == sorted(erwartet)

    # zweiter Hook-Aufruf mit gleicher Quellrevision: keine zweite Operation
    hooks.on_document_filed(doc)
    assert SyncOperation.objects.filter(kind=OperationKind.DRIVE_SET_PROPS, document=doc).count() == 1
    assert ops() == []

    # erzwungener zweiter Lauf: Kennzeichen vorhanden, kein weiterer Schreibzugriff, Verknuepfung bleibt eine
    operations.retry_now(op)
    ergebnisse = ops()
    assert len(ergebnisse) == 1 and ergebnisse[0]["result"] == {"changed": []}
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result == {"changed": []}
    assert [name for name, _ in drive.ops].count("set_app_properties") == 1
    assert drive.get(file_id).app_properties == erwartet
    link.refresh_from_db()
    assert link.state == LinkState.SYNCED and link.state_reason == "Kennzeichen vorhanden"
    assert ExternalLink.objects.filter(document=doc, system=SyncSystem.DRIVE).count() == 1
    assert AuditEvent.objects.filter(action="sync.drive_props_set", entity_id=doc.pk).count() == 1


def test_schalter_aus_ueberspringt_den_task(aenderungen, objekt, admin_user):
    from apps.sync import tasks

    drive = aenderungen
    store.set("sync.drive_changes_enabled", False, user=admin_user, reason="Test")
    assert not drive_changes.enabled_and_ready()
    ops_vorher = len(drive.ops)

    result = tasks.drive_changes_task(force=True)
    assert result == {"skipped": "drive changes inaktiv"}
    assert _cursor() is None and services.get_cursor(SyncSystem.DRIVE, drive_changes.CURSOR_LAST) is None
    assert drive.ops[ops_vorher:] == []
    assert not AuditEvent.objects.filter(action="sync.drive_changes").exists()

    # Schalter an: derselbe Task setzt den Ausgangspunkt
    store.set("sync.drive_changes_enabled", True, user=admin_user, reason="Test")
    assert drive_changes.enabled_and_ready()
    result = tasks.drive_changes_task(force=True)
    assert "baseline" in result and _cursor().value == result["baseline"]
    assert [name for name, _ in drive.ops[ops_vorher:]] == ["start_page_token"]
