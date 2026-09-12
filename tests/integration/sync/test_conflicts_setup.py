"""Verbindungseinrichtung und Konfliktaufloesung (Fallgruppe D): setup.check legt Tag und Felder nur mit
Schreibrecht an, meldet Verbindungsfehler als Befund und behaelt die zuletzt bekannte Kennzeichnung; Konflikte
werden nur durch eine ausdrueckliche, fuer die Konfliktart zulaessige Entscheidung aufgeloest, protokolliert und
wirken auf Verknuepfung oder Operation. Loeschungen werden nie gespiegelt, eine bestaetigte Loeschung (Tombstone)
verhindert die erneute Uebernahme."""

from __future__ import annotations

import hashlib

import pytest
from django.conf import settings
from django.utils import timezone
from tests.integration.sync.conftest import pdf_bytes

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents.models import Document
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import config, services
from apps.sync.flows import common, conflicts, paperless_pull
from apps.sync.models import (
    AssignmentExample,
    ExternalLink,
    LinkRole,
    LinkState,
    OperationKind,
    OperationStatus,
    SyncCursor,
    SyncOperation,
    SyncSystem,
)
from apps.sync.operations import op_key
from apps.sync.paperless import setup
from apps.sync.paperless.errors import PaperlessUnavailable
from apps.sync.paperless.fake import FakePaperless

pytestmark = pytest.mark.django_db

FELDSCHLUESSEL = ("uuid", "object", "status", "drive")


def _dokument(obj, name: str = "Wartung_Heizung.pdf", data: bytes | None = None) -> Document:
    data = data if data is not None else pdf_bytes()
    return Document.objects.create(
        object=obj,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        mime_type="application/pdf",
        original_name=name,
        current_name=name,
        source="upload",
        status="hashed",
        first_seen_at=timezone.now(),
    )


def _verknuepfung(doc, remote_id: int, *, state: str, checksum: str | None) -> ExternalLink:
    return common.upsert_link(
        doc,
        system=SyncSystem.PAPERLESS,
        external_id=str(remote_id),
        checksum_sha256=checksum,
        state=state,
        state_reason="Testaufbau",
    )


def _erzeugte(fake: FakePaperless) -> list[tuple]:
    return [c for c in fake.calls if c[0].startswith("create_")]


def _verbindungsbefund() -> dict:
    return SyncCursor.objects.get(system=SyncSystem.PAPERLESS, name=services.CONNECTION_CURSOR).meta


def _schluessel(case: ReviewCase) -> list[str]:
    return [k for k, _ in conflicts.choices_for(case)]


# --- 9. Verbindungseinrichtung -----------------------------------------------------------------------------


def test_check_legt_im_modus_readonly_nichts_an(paperless, admin_user, monkeypatch):
    leer = FakePaperless()
    monkeypatch.setattr(services, "get_client", lambda: leer)
    store.set("paperless.mode", "readonly", user=admin_user, reason="Test")
    einrichtungen_vorher = AuditEvent.objects.filter(action="sync.paperless_setup").count()
    names = config.field_names()

    state = setup.check(user=admin_user, create=True)
    assert state.ok is False and "unvollständig" in state.message
    assert set(state.missing) == {names.tag, names.uuid, names.object, names.status, names.drive}
    assert state.field_ids == {} and state.tag_id is None
    assert state.server_version == leer.server_version and state.api_version == leer.api_version
    assert _erzeugte(leer) == [] and leer.tags == {} and leer.custom_fields == {}
    assert leer.call_names() == ["server_info", "list_tags", "list_custom_fields"]
    befund = _verbindungsbefund()
    assert befund["ok"] is False and set(befund["missing"]) == set(state.missing)
    assert befund["field_ids"] == {} and befund["tag_id"] is None
    ereignis = AuditEvent.objects.filter(action="sync.paperless_check").order_by("-id").first()
    assert ereignis is not None and ereignis.user_id == admin_user.pk
    assert ereignis.after_state["ok"] is False and ereignis.after_state["created"] == []
    assert set(ereignis.after_state["missing"]) == set(state.missing)
    assert AuditEvent.objects.filter(action="sync.paperless_setup").count() == einrichtungen_vorher
    # ohne create wird auch im Modus pilot nichts angelegt
    store.set("paperless.mode", "pilot", user=admin_user, reason="Test")
    leer.reset_calls()
    state = setup.check(user=admin_user, create=False)
    assert state.ok is False and _erzeugte(leer) == [] and leer.tags == {}


def test_check_richtet_im_modus_pilot_tag_und_felder_ein(paperless, admin_user, monkeypatch):
    leer = FakePaperless()
    monkeypatch.setattr(services, "get_client", lambda: leer)
    assert config.mode() == "pilot"
    einrichtungen_vorher = AuditEvent.objects.filter(action="sync.paperless_setup").count()
    pruefungen_vorher = AuditEvent.objects.filter(action="sync.paperless_check").count()
    names = config.field_names()

    state = setup.check(user=admin_user, create=True)
    assert state.ok is True and state.missing == [] and state.message == "Verbindung in Ordnung"
    assert [t["name"] for t in leer.tags.values()] == [names.tag]
    assert {f["name"]: f["data_type"] for f in leer.custom_fields.values()} == {
        names.uuid: "string",
        names.object: "string",
        names.status: "string",
        names.drive: "url",
    }
    assert [c[1] for c in leer.calls if c[0] == "create_tag"] == [(names.tag,)]
    assert [c[1] for c in leer.calls if c[0] == "create_custom_field"] == [
        (names.uuid, "string"),
        (names.object, "string"),
        (names.status, "string"),
        (names.drive, "url"),
    ]
    ids_je_name = {f["name"]: f["id"] for f in leer.custom_fields.values()}
    erwartet = {
        "uuid": ids_je_name[names.uuid],
        "object": ids_je_name[names.object],
        "status": ids_je_name[names.status],
        "drive": ids_je_name[names.drive],
    }
    assert state.field_ids == erwartet and state.tag_id == next(iter(leer.tags))
    cursor = SyncCursor.objects.get(system=SyncSystem.PAPERLESS, name=services.CONNECTION_CURSOR)
    befund = cursor.meta
    assert cursor.value == leer.server_version and befund["server_version"] == leer.server_version
    assert befund["api_version"] == leer.api_version
    assert befund["field_ids"] == erwartet and befund["tag_id"] == state.tag_id
    assert befund["ok"] is True and befund["missing"] == [] and befund["checked_at"]
    assert befund["features"]["custom_fields"] is True
    assert services.connection_meta()["field_ids"] == erwartet
    assert setup.state_from_cursor().field_ids == erwartet
    assert AuditEvent.objects.filter(action="sync.paperless_setup").count() == einrichtungen_vorher + 1
    ereignis = AuditEvent.objects.filter(action="sync.paperless_setup").order_by("-id").first()
    assert ereignis.user_id == admin_user.pk and ereignis.after_state["ok"] is True
    assert ereignis.after_state["created"] == [
        f"tag:{names.tag}",
        f"field:{names.uuid}",
        f"field:{names.object}",
        f"field:{names.status}",
        f"field:{names.drive}",
    ]
    assert ereignis.after_state["server_version"] == leer.server_version

    # zweiter Lauf: nichts doppelt, gleiche IDs, Befund als Pruefung protokolliert
    leer.reset_calls()
    state2 = setup.check(user=admin_user, create=True)
    assert state2.ok is True and state2.field_ids == erwartet and state2.tag_id == state.tag_id
    assert leer.call_names() == ["server_info", "list_tags", "list_custom_fields"]
    assert len(leer.tags) == 1 and len(leer.custom_fields) == 4
    assert _verbindungsbefund()["field_ids"] == erwartet
    assert AuditEvent.objects.filter(action="sync.paperless_setup").count() == einrichtungen_vorher + 1
    assert AuditEvent.objects.filter(action="sync.paperless_check").count() == pruefungen_vorher + 1
    letzte = AuditEvent.objects.filter(action="sync.paperless_check").order_by("-id").first()
    assert letzte.after_state == {
        "ok": True,
        "server_version": leer.server_version,
        "api_version": leer.api_version,
        "created": [],
        "missing": [],
    }
    # vorhandene Kennzeichnung mit abweichender Schreibweise wird erkannt, nicht neu angelegt
    anderer = FakePaperless()
    anderer.create_tag(names.tag.upper())
    for key, name in zip(FELDSCHLUESSEL, (names.uuid, names.object, names.status, names.drive), strict=True):
        anderer.create_custom_field(f" {name.lower()} ", setup.FIELD_TYPES[key])
    anderer.reset_calls()
    monkeypatch.setattr(services, "get_client", lambda: anderer)
    state3 = setup.check(user=admin_user, create=True)
    assert state3.ok is True and _erzeugte(anderer) == [] and len(anderer.custom_fields) == 4


def test_check_meldet_verbindungsfehler_ohne_absturz(paperless, admin_user):
    vorher = services.connection_meta()
    assert vorher["ok"] is True and vorher["field_ids"] and vorher["tag_id"]
    pruefungen_vorher = AuditEvent.objects.filter(action="sync.paperless_check").count()
    paperless.inject("server_info", PaperlessUnavailable("Paperless GET /api/ui_settings/: HTTP 503"))

    state = setup.check(user=admin_user, create=True)
    assert state.ok is False and state.message.startswith("Verbindung fehlgeschlagen: PaperlessUnavailable")
    assert "HTTP 503" in state.message
    assert paperless.call_names() == ["server_info"] and _erzeugte(paperless) == []
    befund = _verbindungsbefund()
    assert befund["ok"] is False and befund["message"] == state.message
    # zuletzt bekannte Kennzeichnung bleibt fuer die laufenden Operationen erhalten
    assert befund["field_ids"] == vorher["field_ids"] and befund["tag_id"] == vorher["tag_id"]
    assert state.field_ids == vorher["field_ids"] and state.tag_id == vorher["tag_id"]
    assert setup.state_from_cursor().ok is False
    ereignis = AuditEvent.objects.filter(action="sync.paperless_check").order_by("-id").first()
    assert ereignis.user_id == admin_user.pk and ereignis.after_state["ok"] is False
    assert "PaperlessUnavailable" in ereignis.after_state["error"]
    assert AuditEvent.objects.filter(action="sync.paperless_check").count() == pruefungen_vorher + 1

    # naechster Versuch ohne Stoerung: wieder in Ordnung, nichts wird neu angelegt
    paperless.reset_calls()
    state2 = setup.check(user=admin_user, create=True)
    assert state2.ok is True and state2.field_ids == vorher["field_ids"]
    assert _erzeugte(paperless) == [] and _verbindungsbefund()["ok"] is True


def test_check_ohne_konfiguration(paperless, admin_user, monkeypatch):
    pruefungen_vorher = AuditEvent.objects.count()
    monkeypatch.setattr(services, "get_client", lambda: None)
    state = setup.check(user=admin_user, create=True)
    assert state.ok is False and "nicht konfiguriert" in state.message
    befund = _verbindungsbefund()
    assert befund["ok"] is False and "nicht konfiguriert" in befund["message"]
    assert AuditEvent.objects.count() == pruefungen_vorher
    # ohne Token liefert auch die echte Fabrik keinen Client
    monkeypatch.setitem(settings.OBJEKTAKTE, "PAPERLESS_TOKEN", "")
    assert config.configured() is False and config.active() is False


# --- 10. Inhaltskonflikt: lokalen Stand behalten -----------------------------------------------------------


def test_keep_local_uebernimmt_neue_pruefsumme_der_gegenseite(objekt, paperless, admin_user):
    doc = _dokument(objekt)
    alt = doc.sha256
    remote_id = paperless.add_document("Wartung Heizung", content=pdf_bytes())
    link = _verknuepfung(doc, remote_id, state=LinkState.CHANGED_REMOTE, checksum=alt)
    neu = "ab" * 32
    case = common.conflict(
        doc,
        "content_changed_remote",
        "x",
        {
            "paperless_id": str(remote_id),
            "local_sha256": doc.sha256,
            "known_remote_checksum": alt,
            "new_remote_checksum": neu,
        },
    )
    assert case is not None and case.status == CaseStatus.OPEN and case.object_id == objekt.pk
    assert case.case_type == CaseType.SYNC_CONFLICT and case.case_subtype == "content_changed_remote"
    assert conflicts.choices_for(case) == [("keep_local", conflicts.CHOICES["keep_local"])]
    # derselbe Schluessel legt keinen zweiten offenen Fall an
    assert common.conflict(doc, "content_changed_remote", "x", {}) is None

    # unzulaessige Wahl: unbekannt oder fuer diese Konfliktart nicht angeboten
    with pytest.raises(conflicts.ConflictError, match="unbekannte Entscheidung"):
        conflicts.resolve(case, "unsinn", user=admin_user)
    with pytest.raises(conflicts.ConflictError, match="nicht zulässig"):
        conflicts.resolve(case, "tombstone", user=admin_user)
    with pytest.raises(conflicts.ConflictError, match="nicht zulässig"):
        conflicts.resolve(case, "reupload", user=admin_user)
    case.refresh_from_db()
    link.refresh_from_db()
    assert case.status == CaseStatus.OPEN and link.state == LinkState.CHANGED_REMOTE
    assert link.checksum_sha256 == alt and link.tombstone_at is None
    assert not AuditEvent.objects.filter(action__in=["sync.conflict_resolved", "sync.tombstone"]).exists()
    assert not SyncOperation.objects.exists()

    ergebnis = conflicts.resolve(case, "keep_local", user=admin_user, reason="lokale Fassung ist maßgeblich")
    assert ergebnis == {"choice": "keep_local"}
    link.refresh_from_db()
    assert link.state == LinkState.SYNCED and link.checksum_sha256 == neu
    assert link.synced_fields["accepted_remote_checksum"] == neu
    assert link.last_synced_at is not None and link.state_reason == "Konflikt: lokaler Stand behalten"
    assert link.external_id == str(remote_id) and link.document_id == doc.pk
    case.refresh_from_db()
    assert case.status == CaseStatus.RESOLVED and case.resolved_by_id == admin_user.pk
    assert case.resolved_at is not None
    assert case.resolution == {
        "action": "resolve_conflict",
        "choice": "keep_local",
        "reason": "lokale Fassung ist maßgeblich",
    }
    ereignis = AuditEvent.objects.get(action="sync.conflict_resolved", entity_id=case.pk)
    assert ereignis.entity_type == "review_case" and ereignis.object_id == objekt.pk
    assert ereignis.user_id == admin_user.pk
    assert ereignis.after_state == {
        "subtype": "content_changed_remote",
        "choice": "keep_local",
        "reason": "lokale Fassung ist maßgeblich",
    }
    # lokales Dokument unveraendert, kein Zugriff auf Paperless, keine Operation
    doc.refresh_from_db()
    assert doc.sha256 == alt and doc.deleted_at is None and doc.status == "hashed"
    assert paperless.calls == [] and not SyncOperation.objects.exists()
    # bereits erledigt
    with pytest.raises(conflicts.ConflictError, match="bereits erledigt"):
        conflicts.resolve(case, "keep_local", user=admin_user)
    assert AuditEvent.objects.filter(action="sync.conflict_resolved").count() == 1


def test_keep_local_bei_loeschung_behaelt_zustand_missing(objekt, paperless, admin_user):
    doc = _dokument(objekt)
    link = _verknuepfung(doc, 4711, state=LinkState.MISSING, checksum=doc.sha256)
    case = common.conflict(doc, "deleted_remote", "missing", {"paperless_id": "4711"})
    conflicts.resolve(case, "keep_local", user=admin_user, reason="Kopie wird nicht mehr gebraucht")
    link.refresh_from_db()
    assert link.state == LinkState.MISSING and link.last_synced_at is not None
    assert link.state_reason == "Konflikt gesehen, lokal behalten" and link.tombstone_at is None
    case.refresh_from_db()
    assert case.status == CaseStatus.RESOLVED and case.resolution["choice"] == "keep_local"
    doc.refresh_from_db()
    assert doc.deleted_at is None and paperless.calls == []


# --- 11. Loeschung bestaetigen: Tombstone ------------------------------------------------------------------


def test_tombstone_verhindert_erneute_uebernahme(objekt, paperless, admin_user, ops):
    data = pdf_bytes()
    doc = _dokument(objekt, data=data)
    remote_id = paperless.add_document("Wartung Heizung", content=data)
    _verknuepfung(doc, remote_id, state=LinkState.LINKED, checksum=doc.sha256)
    paperless.delete_document(remote_id)
    op, _ = paperless_pull.enqueue_from_webhook(remote_id)
    ops()
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result["missing"] is True
    link = ExternalLink.objects.get(document=doc, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
    assert link.state == LinkState.MISSING
    case = ReviewCase.objects.get(
        case_type=CaseType.SYNC_CONFLICT, case_subtype="deleted_remote", document=doc
    )
    assert _schluessel(case) == ["keep_local", "tombstone", "reupload"]

    ergebnis = conflicts.resolve(case, "tombstone", user=admin_user, reason="in Paperless bewusst gelöscht")
    assert ergebnis == {"choice": "tombstone"}
    link.refresh_from_db()
    assert link.state == LinkState.TOMBSTONE and link.tombstone_at is not None
    assert link.tombstone_by_id == admin_user.pk and link.state_reason == "in Paperless bewusst gelöscht"
    assert link.document_id == doc.pk and link.external_id == str(remote_id)
    ereignis = AuditEvent.objects.get(action="sync.tombstone", entity_id=doc.pk)
    assert ereignis.entity_type == "document" and ereignis.object_id == objekt.pk
    assert ereignis.user_id == admin_user.pk
    assert ereignis.after_state == {
        "system": "paperless",
        "external_id": str(remote_id),
        "reason": "in Paperless bewusst gelöscht",
    }
    abschluss = AuditEvent.objects.get(action="sync.conflict_resolved", entity_id=case.pk)
    assert (
        abschluss.after_state["choice"] == "tombstone"
        and abschluss.after_state["subtype"] == "deleted_remote"
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.RESOLVED and case.resolved_by_id == admin_user.pk

    # Die Kopie erscheint unter derselben ID wieder (in Paperless wiederhergestellt): weder ein weiterer Webhook
    # noch der regelmaessige Abgleich uebernehmen sie
    wieder = paperless.add_document("Wartung Heizung (wiederhergestellt)", content=data)
    paperless.documents[remote_id] = paperless.documents.pop(wieder)
    paperless.documents[remote_id]["id"] = remote_id
    paperless.reset_calls()
    dokumente_vorher = Document.objects.count()
    op2, created = paperless_pull.enqueue_from_webhook(remote_id, event="wiederhergestellt")
    assert created
    ops()
    op2.refresh_from_db()
    assert op2.status == OperationStatus.SKIPPED and "Löschung bestätigt" in op2.result["skipped"]
    assert "get_document" not in paperless.call_names() and "download" not in paperless.call_names()
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ergebnisse = ops()
    assert ergebnisse and all("skipped" in e for e in ergebnisse)
    assert "download" not in paperless.call_names()
    assert Document.objects.count() == dokumente_vorher
    link.refresh_from_db()
    assert link.state == LinkState.TOMBSTONE and link.document_id == doc.pk
    assert not ReviewCase.objects.filter(case_type=CaseType.SYNC_CONFLICT, status=CaseStatus.OPEN).exists()
    # Dieselbe Datei wird unter neuer ID erneut nach Paperless geladen: ebenfalls keine Uebernahme, die
    # Loeschmarkierung bleibt auf der bekannten Kopie, nichts wird geladen
    erneut = paperless.add_document("Wartung Heizung (erneut)", content=data)
    paperless.reset_calls()
    op3, created = paperless_pull.enqueue_from_webhook(erneut, event="neu")
    assert created
    ops()
    op3.refresh_from_db()
    assert op3.status == OperationStatus.DONE and "download" not in paperless.call_names()
    assert Document.objects.count() == dokumente_vorher
    assert not Document.objects.filter(source="paperless").exists()
    link.refresh_from_db()
    assert link.state == LinkState.TOMBSTONE and link.external_id == str(remote_id)
    assert link.tombstone_at is not None and link.document_id == doc.pk
    assert ExternalLink.objects.filter(document=doc, system=SyncSystem.PAPERLESS).count() == 1
    # das lokale Dokument wird nie geloescht, Paperless nie beschrieben
    doc.refresh_from_db()
    assert doc.deleted_at is None and doc.status == "hashed" and doc.object_id == objekt.pk
    assert remote_id in paperless.documents and erneut in paperless.documents
    assert not any(c[0] in ("bulk_edit", "patch_document", "delete_document") for c in paperless.calls)


# --- 12. Objektkonflikt: Zuordnung uebernehmen oder zurueckschreiben --------------------------------------


def test_apply_remote_object_uebernimmt_dokument_in_das_zielobjekt(
    objekt, anderes_objekt, paperless, admin_user
):
    doc = _dokument(objekt)
    remote_id = paperless.add_document("Rechnung Heizung", content=pdf_bytes())
    link = _verknuepfung(doc, remote_id, state=LinkState.CONFLICT, checksum=doc.sha256)
    uuid_vorher = doc.uuid
    case = common.conflict(
        doc,
        "object_changed_remote",
        str(anderes_objekt.pk),
        {
            "paperless_id": str(remote_id),
            "local_object_id": objekt.pk,
            "remote_object_id": anderes_objekt.pk,
        },
    )
    assert _schluessel(case) == ["apply_remote_object", "push_local_object"]
    # das Zielobjekt liegt ausserhalb des Pilotumfangs: die Uebernahme haengt nicht am Schreibrecht
    assert config.writes_allowed(objekt) and not config.writes_allowed(anderes_objekt)

    ergebnis = conflicts.resolve(case, "apply_remote_object", user=admin_user, reason="im DMS entschieden")
    assert ergebnis["choice"] == "apply_remote_object"
    neu = Document.objects.get(pk=ergebnis["new_document_id"])
    assert neu.object_id == anderes_objekt.pk and neu.uuid == uuid_vorher and neu.source == "moved_in"
    assert neu.sha256 == doc.sha256 and neu.current_name == doc.current_name and neu.deleted_at is None
    doc.refresh_from_db()
    assert doc.status == "moved_out" and doc.uuid != uuid_vorher and doc.duplicate_of_id == neu.pk
    assert doc.deleted_at is None and doc.object_id == objekt.pk
    assert Document.objects.filter(uuid=uuid_vorher).count() == 1
    link.refresh_from_db()
    assert link.document_id == neu.pk and link.external_id == str(remote_id)
    assert link.state == LinkState.SYNCED and link.state_reason == "Objektzuordnung aus Paperless übernommen"
    assert ExternalLink.objects.filter(document=doc).count() == 0
    beispiel = AssignmentExample.objects.get(document=neu)
    assert beispiel.kind == "correct" and beispiel.source == "human"
    assert beispiel.target_object_id == anderes_objekt.pk and beispiel.previous_object_id == objekt.pk
    assert beispiel.decided_by_id == admin_user.pk
    assert AuditEvent.objects.filter(
        action="inbox.assign", entity_id=neu.pk, object_id=anderes_objekt.pk
    ).exists()
    assert (
        AuditEvent.objects.filter(action="review.transfer_object", entity_id__in=[doc.pk, neu.pk]).count()
        == 2
    )
    case.refresh_from_db()
    assert case.status == CaseStatus.RESOLVED and case.resolved_by_id == admin_user.pk
    assert case.resolution["new_document_id"] == neu.pk and case.resolution["reason"] == "im DMS entschieden"
    ereignis = AuditEvent.objects.get(action="sync.conflict_resolved", entity_id=case.pk)
    assert ereignis.after_state["choice"] == "apply_remote_object"
    assert ereignis.after_state["new_document_id"] == neu.pk
    # kein Schreibzugriff auf Paperless: das Zielobjekt ist nicht im Pilotumfang
    assert not SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PUSH_META).exists()
    assert paperless.calls == []

    # Zielobjekt aus Paperless nicht vorhanden: Fehler, Fall bleibt offen
    case2 = common.conflict(neu, "object_changed_remote", "999999", {"remote_object_id": 999999})
    with pytest.raises(conflicts.ConflictError, match="nicht gefunden"):
        conflicts.resolve(case2, "apply_remote_object", user=admin_user)
    case2.refresh_from_db()
    assert case2.status == CaseStatus.OPEN
    neu.refresh_from_db()
    assert neu.status != "moved_out" and Document.objects.filter(uuid=uuid_vorher).count() == 1


def test_push_local_object_schreibt_eigene_zuordnung_zurueck(
    objekt, anderes_objekt, paperless, admin_user, ops
):
    doc = _dokument(objekt)
    remote_id = paperless.add_document("Rechnung Heizung", content=pdf_bytes())
    link = _verknuepfung(doc, remote_id, state=LinkState.CONFLICT, checksum=doc.sha256)
    ExternalLink.objects.filter(pk=link.pk).update(
        synced_fields={"title": "Rechnung Heizung", "app_fields": {"object": "624, Beispielhausen"}}
    )
    case = common.conflict(
        doc,
        "object_changed_remote",
        str(anderes_objekt.pk),
        {"paperless_id": str(remote_id), "local_object_id": objekt.pk, "remote_object_id": anderes_objekt.pk},
    )
    ergebnis = conflicts.resolve(case, "push_local_object", user=admin_user)
    assert ergebnis == {"choice": "push_local_object"}
    link.refresh_from_db()
    assert link.state == LinkState.SYNCED and link.state_reason == "eigene Zuordnung zurückgeschrieben"
    assert link.synced_fields == {"title": "Rechnung Heizung", "app_fields": {}}
    op = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_PUSH_META)
    assert op.status == OperationStatus.PENDING and op.priority == 50 and op.document_id == doc.pk
    assert op.op_key == op_key(OperationKind.PAPERLESS_PUSH_META, doc.uuid, "conflict", case.pk)
    assert op.system == SyncSystem.PAPERLESS and op.source_system == SyncSystem.APP
    doc.refresh_from_db()
    assert doc.object_id == objekt.pk and doc.status == "hashed"
    case.refresh_from_db()
    assert case.status == CaseStatus.RESOLVED
    assert AuditEvent.objects.get(action="sync.conflict_resolved", entity_id=case.pk).after_state[
        "choice"
    ] == ("push_local_object")

    # die Operation schreibt den eigenen Objektbezug nach Paperless
    ops()
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result["paperless_id"] == str(remote_id)
    field_ids = services.connection_meta()["field_ids"]
    werte = {cf["field"]: cf["value"] for cf in paperless.documents[remote_id]["custom_fields"]}
    assert werte[field_ids["object"]].startswith("623") and "Musterstadt" in werte[field_ids["object"]]
    assert werte[field_ids["uuid"]] == str(doc.uuid)
    assert services.connection_meta()["tag_id"] in paperless.documents[remote_id]["tags"]
    link.refresh_from_db()
    assert link.synced_fields["app_fields"]["object"].startswith("623")

    # ausserhalb des Pilotumfangs ist das Zurueckschreiben nicht erlaubt: Fehler, kein Auftrag, Fall offen
    fremd = _dokument(anderes_objekt, name="Fremd.pdf", data=pdf_bytes(["Objekt 624", "Beispielweg 7"]))
    fremd_id = paperless.add_document("Fremd", content=pdf_bytes(["Objekt 624", "Beispielweg 7"]))
    _verknuepfung(fremd, fremd_id, state=LinkState.CONFLICT, checksum=fremd.sha256)
    case2 = common.conflict(fremd, "object_changed_remote", str(objekt.pk), {"remote_object_id": objekt.pk})
    with pytest.raises(conflicts.ConflictError, match="nicht erlaubt"):
        conflicts.resolve(case2, "push_local_object", user=admin_user)
    case2.refresh_from_db()
    assert case2.status == CaseStatus.OPEN
    assert SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PUSH_META).count() == 1


def test_choices_for_liefert_je_konfliktart_die_zulaessigen_entscheidungen():
    erwartet = {
        "deleted_remote": ["keep_local", "tombstone", "reupload"],
        "trashed_remote": ["keep_local", "tombstone"],
        "drive_removed": ["keep_local", "tombstone"],
        "drive_trashed": ["keep_local", "tombstone"],
        "object_changed_remote": ["apply_remote_object", "push_local_object"],
        "content_changed_remote": ["keep_local"],
        "drive_content_changed": ["keep_local"],
        "checksum_mismatch": ["keep_local", "reupload"],
        "unbekannt": ["keep_local"],
        None: ["keep_local"],
    }
    for subtype, schluessel in erwartet.items():
        auswahl = conflicts.choices_for(ReviewCase(case_type=CaseType.SYNC_CONFLICT, case_subtype=subtype))
        assert [k for k, _ in auswahl] == schluessel, subtype
        assert [label for _, label in auswahl] == [conflicts.CHOICES[k] for k in schluessel]


# --- 13. Pruefsummenabweichung: erneut uebertragen ---------------------------------------------------------


def test_reupload_entfernt_verknuepfung_und_reiht_upload_ein(objekt, paperless, admin_user):
    doc = _dokument(objekt)
    remote_id = paperless.add_document("Wartung Heizung", content=b"abweichender Inhalt in Paperless")
    entfernt = paperless.documents[remote_id]["checksum"]
    link = _verknuepfung(doc, remote_id, state=LinkState.SYNCED, checksum=entfernt)
    assert entfernt != doc.sha256
    case = common.conflict(
        doc, "checksum_mismatch", str(remote_id), {"local": doc.sha256, "remote": entfernt}
    )
    assert _schluessel(case) == ["keep_local", "reupload"]

    ergebnis = conflicts.resolve(case, "reupload", user=admin_user, reason="Datei in Paperless beschädigt")
    assert ergebnis == {"choice": "reupload"}
    assert not ExternalLink.objects.filter(pk=link.pk).exists()
    assert not ExternalLink.objects.filter(document=doc, system=SyncSystem.PAPERLESS).exists()
    op = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_PUSH, document=doc)
    assert op.status == OperationStatus.PENDING and op.priority == 50
    assert op.op_key == op_key(OperationKind.PAPERLESS_PUSH, doc.uuid, doc.sha256, "again", case.pk)
    assert op.system == SyncSystem.PAPERLESS and op.source_system == SyncSystem.APP
    assert SyncOperation.objects.count() == 1
    case.refresh_from_db()
    assert case.status == CaseStatus.RESOLVED and case.resolution["choice"] == "reupload"
    ereignis = AuditEvent.objects.get(action="sync.conflict_resolved", entity_id=case.pk)
    assert (
        ereignis.after_state["choice"] == "reupload"
        and ereignis.after_state["subtype"] == "checksum_mismatch"
    )
    # Paperless-Kopie und lokales Dokument bleiben unangetastet
    assert remote_id in paperless.documents and paperless.calls == []
    doc.refresh_from_db()
    assert doc.deleted_at is None and doc.sha256 != entfernt
