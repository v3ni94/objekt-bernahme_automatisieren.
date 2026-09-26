"""Eingang aus Paperless (Fallgruppe B): neue Dokumente werden per Abgleich (modified-Cursor) oder Webhook in das
Eingangsobjekt oder direkt in das Objekt aus dem Feld Objekt uebernommen und durchlaufen die Pipeline bis zur
Objektzuordnung; identische Dateien (Pruefsumme oder UUID-Feld) werden nur verknuepft, eine zweite Kopie wird als
Konfliktfall gemeldet statt still umgehaengt; Inhalts- und Objektfeldaenderungen sowie Loeschungen in Paperless
erzeugen Konfliktfaelle oder Vorschlaege, nie eine lokale Aenderung oder Loeschung; der Webhook prueft das
gemeinsame Geheimnis, merkt genau eine Operation vor und nimmt den Body nie als Wahrheit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone
from tests.integration.sync.conftest import WEBHOOK_TOKEN, pdf_bytes

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document
from apps.pipeline import storage
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import services
from apps.sync.flows import assign, conflicts, paperless_pull
from apps.sync.models import (
    ExternalLink,
    LinkRole,
    LinkState,
    OperationKind,
    OperationStatus,
    SyncOperation,
    SyncSystem,
)

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/webhooks/paperless/"
FREMDER_TOKEN = "falscher-testwert-nicht-echt"  # Testwert, kein Geheimnis
# Dokument mit derselben Objektbezugszeile, aber eindeutiger Kategorie der Stammakte (Regel Energieausweis)
LINES_EINGANG = [
    "Objekt 623 Musterstadt, Musterstraße 49",
    "Energieausweis für das Wohngebäude Musterstraße 49, 12345 Musterstadt",
    "Ausstellungsdatum 03.09.2026, gültig bis 02.09.2036",
    "Alle Angaben in diesem Testdokument sind erfunden; keine realen Personen oder Konten.",
]


def _pull_ops():
    return SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PULL)


def _link(remote_id: int) -> ExternalLink:
    return ExternalLink.objects.get(
        system=SyncSystem.PAPERLESS, external_id=str(remote_id), role=LinkRole.ORIGINAL
    )


def _importieren(paperless, ops, data: bytes, **kwargs) -> tuple[int, Document]:
    """Legt ein Dokument in Paperless an, gleicht ab und liefert Paperless-ID und lokales Dokument."""
    remote_id = paperless.add_document(kwargs.pop("title", "Wartung Heizung"), content=data, **kwargs)
    paperless_pull.poll(force=True)
    ops()
    return remote_id, _link(remote_id).document


def _webhook(client, body, **headers):
    return client.post(WEBHOOK_URL, data=json.dumps(body), content_type="application/json", headers=headers)


def test_neues_dokument_wird_in_den_eingang_uebernommen(objekt, paperless, eingang, run_all, ops):
    data = pdf_bytes()
    remote_id = paperless.add_document(
        "Wartung Heizung", content=data, original_file_name="Energieausweis_Musterstrasse_49.pdf"
    )
    ergebnis = paperless_pull.poll(force=True)
    assert ergebnis["enqueued"] == 1
    op = _pull_ops().get()
    assert op.status == OperationStatus.PENDING and op.payload["paperless_id"] == remote_id

    ops()
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result["inbox"] is True
    doc = Document.objects.get(source="paperless")
    assert doc.object_id == eingang.pk and doc.object.is_system_inbox
    assert doc.status == "registered" and doc.original_name == "Energieausweis_Musterstrasse_49.pdf"
    assert doc.size_bytes == len(data) and doc.mime_type == "application/pdf"
    link = ExternalLink.objects.get(document=doc, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
    assert link.external_id == str(remote_id) and link.state == LinkState.LINKED
    assert link.checksum_sha256 == hashlib.sha256(data).hexdigest()
    ereignis = AuditEvent.objects.get(action="sync.paperless_pull", entity_id=doc.pk)
    assert ereignis.after_state["inbox"] is True and ereignis.object_id == eingang.pk
    # Datei liegt im Transitverzeichnis des Eingangsobjekts (storage.upload_dir(eingang.pk))
    pfad = Path(doc.source_path)
    assert pfad.exists() and pfad.read_bytes() == data
    assert pfad.parent.parent == storage.data_dir() / "transit" / str(eingang.pk) / "incoming"
    assert paperless.call_names().count("download") == 1

    # zweiter Abgleich: Cursor steht auf dem Stand des Dokuments, keine zweite Operation
    cursor = services.get_cursor(SyncSystem.PAPERLESS, paperless_pull.CURSOR_MODIFIED)
    assert cursor.value == paperless.documents[remote_id]["modified"]
    assert paperless_pull.poll(force=True)["enqueued"] == 0
    assert _pull_ops().count() == 1

    # Pipeline im Eingangsobjekt bis zur Objektzuordnung
    uuid_vorher = doc.uuid
    run_all(eingang)
    doc.refresh_from_db()
    assert doc.status != "registered" and doc.sha256 == link.checksum_sha256
    job = ProcessingJob.objects.get(job_type=JobType.ASSIGN_OBJECT, object=eingang)
    assert job.status == JobStatus.DONE
    uebernommen = Document.objects.filter(object=objekt, uuid=uuid_vorher).first()
    if uebernommen is not None:
        # automatische Uebernahme in Objekt 623: UUID und Paperless-Verknuepfung wandern mit
        assert doc.status == "moved_out" and uebernommen.source == "moved_in"
        fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=uebernommen)
        assert fall.case_subtype == "auto" and fall.status == CaseStatus.RESOLVED
        assert fall.candidates[0]["object_number"] == "623"
        assert _link(remote_id).document_id == uebernommen.pk
        assert AuditEvent.objects.filter(action="inbox.assign_auto", entity_id=uebernommen.pk).exists()
        assert not ReviewCase.objects.filter(
            case_type=CaseType.OBJECT_ASSIGNMENT, status=CaseStatus.OPEN
        ).exists()
    else:
        # Vorschlag im Dokumenteneingang: Objekt 623 an erster Stelle, Dokument bleibt im Eingang
        assert doc.object_id == eingang.pk
        fall = ReviewCase.objects.get(
            case_type=CaseType.OBJECT_ASSIGNMENT, document=doc, status=CaseStatus.OPEN
        )
        assert fall.case_subtype == "proposal" and fall.object_id == eingang.pk
        assert fall.candidates[0]["object_id"] == objekt.pk
        assert fall.candidates[0]["object_number"] == "623"
        assert fall.proposed_action == {"action": "assign_object", "object_id": objekt.pk}
        assert "Objekt 623 Musterstadt, Musterstraße 49" in assign.document_text(doc)


def test_feld_objekt_fuehrt_direkt_in_das_objekt(objekt, paperless, eingang, run_all, ops):
    field_ids = services.connection_meta()["field_ids"]
    data = pdf_bytes()
    remote_id = paperless.add_document(
        "Rechnung Heizung",
        content=data,
        custom_fields={field_ids["object"]: "623"},
        original_file_name="Rechnung_Heizung.pdf",
    )
    paperless_pull.poll(force=True)
    ops()
    op = _pull_ops().get()
    assert op.status == OperationStatus.DONE
    assert op.result["object_id"] == objekt.pk and op.result["inbox"] is False
    doc = Document.objects.get(source="paperless")
    assert doc.object_id == objekt.pk and doc.status == "registered"
    assert not Document.objects.filter(object=eingang).exists()
    link = _link(remote_id)
    assert link.document_id == doc.pk and link.synced_fields["object_field"] == "623"
    assert link.checksum_sha256 == hashlib.sha256(data).hexdigest()
    ereignis = AuditEvent.objects.get(action="sync.paperless_pull", entity_id=doc.pk)
    assert ereignis.after_state["inbox"] is False and ereignis.object_id == objekt.pk
    assert Path(doc.source_path).parent.parent == storage.data_dir() / "transit" / str(objekt.pk) / "incoming"

    # Pipeline laeuft im Objekt 623 weiter: keine Zuordnung noetig, kein Rueck-Upload nach Paperless
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.object_id == objekt.pk and doc.status not in ("registered", "moved_out", "error")
    assert not ProcessingJob.objects.filter(job_type=JobType.ASSIGN_OBJECT).exists()
    assert not ReviewCase.objects.filter(case_type=CaseType.OBJECT_ASSIGNMENT).exists()
    assert not SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PUSH).exists()
    assert "post_document" not in paperless.call_names()


def test_lokal_vorhandene_datei_wird_nur_verknuepft(objekt, paperless, eingang, run_all, ops):
    data = pdf_bytes()
    doc, _ = ingest.ingest_upload(objekt, filename="Wartung_Heizung.pdf", data=data)
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.sha256 == hashlib.sha256(data).hexdigest()
    anzahl = Document.objects.count()

    remote_id = paperless.add_document("Wartung Heizung (Scan)", content=data)
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ops()
    op = _pull_ops().get()
    assert op.status == OperationStatus.DONE and op.result == {"linked": doc.pk, "reason": "checksum"}
    link = _link(remote_id)
    # die Uebertragung aus dem lokalen Upload laeuft im selben Lauf (post_document, Aufgabe bleibt PENDING);
    # die Verknuepfung stammt aus dem Pull ueber die Pruefsumme und ist noch nicht abgeglichen (linked)
    assert link.document_id == doc.pk and link.state == LinkState.LINKED
    assert link.checksum_sha256 == doc.sha256 and "Prüfsumme" in (link.state_reason or "")
    assert paperless.call_names().count("post_document") == 1
    assert ExternalLink.objects.filter(system=SyncSystem.PAPERLESS).count() == 1
    assert Document.objects.count() == anzahl
    assert not Document.objects.filter(source="paperless").exists()
    assert "download" not in paperless.call_names()
    ereignis = AuditEvent.objects.get(action="sync.paperless_pull", entity_id=doc.pk)
    assert ereignis.after_state["linked_existing"] is True and ereignis.after_state["ambiguous"] is False


def test_inhaltsaenderung_in_paperless_erzeugt_konflikt(objekt, paperless, eingang, run_all, ops):
    data = pdf_bytes()
    remote_id, doc = _importieren(paperless, ops, data)
    run_all(eingang, job_types=[JobType.HASH])  # nur Hash, damit sha256 gesetzt ist
    doc.refresh_from_db()
    assert doc.status == "hashed" and doc.sha256 == hashlib.sha256(data).hexdigest()
    link_vorher = _link(remote_id)
    assert link_vorher.state == LinkState.LINKED

    neu = pdf_bytes(["Objekt 623 Musterstadt, Musterstraße 49", "Korrigierte Fassung der Rechnung"])
    paperless.set_content(remote_id, neu)
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ops()
    op = _pull_ops().order_by("-id").first()
    assert op.status == OperationStatus.DONE and op.result["conflict"] == "content_changed_remote"

    fall = ReviewCase.objects.get(case_type=CaseType.SYNC_CONFLICT, document=doc)
    assert fall.case_subtype == "content_changed_remote" and fall.status == CaseStatus.OPEN
    assert fall.context["new_remote_checksum"] == hashlib.sha256(neu).hexdigest()
    assert fall.context["known_remote_checksum"] == link_vorher.checksum_sha256
    assert fall.context["local_sha256"] == doc.sha256 and fall.pk == op.result["case_id"]
    link = _link(remote_id)
    assert link.state == LinkState.CHANGED_REMOTE and link.checksum_sha256 == link_vorher.checksum_sha256
    # lokales Dokument unveraendert, kein zweiter Download
    doc.refresh_from_db()
    assert doc.sha256 == hashlib.sha256(data).hexdigest() and doc.status == "hashed"
    assert Path(doc.source_path).read_bytes() == data
    assert paperless.call_names().count("download") == 1


def test_loeschung_in_paperless_erzeugt_konflikt_ohne_lokale_loeschung(objekt, paperless, eingang, ops):
    remote_id, doc = _importieren(paperless, ops, pdf_bytes())
    paperless.delete_document(remote_id)

    op, created = paperless_pull.enqueue_from_webhook(remote_id)
    assert created and op.priority == 50 and op.payload == {"paperless_id": remote_id, "event": "webhook"}
    ops()
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result["missing"] is True
    link = _link(remote_id)
    assert link.state == LinkState.MISSING and link.document_id == doc.pk
    fall = ReviewCase.objects.get(case_type=CaseType.SYNC_CONFLICT, document=doc)
    assert fall.case_subtype == "deleted_remote" and fall.status == CaseStatus.OPEN
    assert fall.context == {"paperless_id": str(remote_id)}
    doc.refresh_from_db()
    assert doc.deleted_at is None and doc.status == "registered"
    assert Path(doc.source_path).exists()
    assert "delete" not in "".join(paperless.call_names())

    # zweiter Lauf (weiteres Webhook-Ereignis): kein zweiter offener Fall (batch_key)
    op2, created2 = paperless_pull.enqueue_from_webhook(remote_id, event="deleted")
    assert created2 and op2.pk != op.pk
    ops()
    op2.refresh_from_db()
    assert op2.status == OperationStatus.DONE and op2.result["missing"] is True
    assert (
        ReviewCase.objects.filter(
            case_type=CaseType.SYNC_CONFLICT, case_subtype="deleted_remote", document=doc
        ).count()
        == 1
    )
    assert Document.objects.filter(pk=doc.pk, deleted_at__isnull=True).exists()
    # der regelmaessige Abgleich sieht das geloeschte Dokument nicht mehr
    assert paperless_pull.poll(force=True)["enqueued"] == 0


def test_abgeschaltete_uebernahme_ueberspringt_neue_dokumente(objekt, paperless, eingang, ops, admin_user):
    store.set("paperless.import_new_documents", False, user=admin_user, reason="Test")
    remote_id = paperless.add_document("Neu in Paperless", content=pdf_bytes())
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ops()
    op = _pull_ops().get()
    assert op.status == OperationStatus.SKIPPED and "abgeschaltet" in op.result["skipped"]
    assert op.payload["paperless_id"] == remote_id
    assert not Document.objects.filter(source="paperless").exists()
    assert not ExternalLink.objects.filter(system=SyncSystem.PAPERLESS).exists()
    assert "download" not in paperless.call_names()
    assert not AuditEvent.objects.filter(action="sync.paperless_pull").exists()


def test_webhook_prueft_token_und_merkt_genau_eine_operation_vor(
    objekt, paperless, eingang, client, ops, monkeypatch
):
    remote_id = paperless.add_document("Per Webhook gemeldet", content=pdf_bytes())

    # ohne Token: abgewiesen und protokolliert
    antwort = _webhook(client, {"doc_id": remote_id})
    assert antwort.status_code == 401 and antwort.json()["accepted"] is False
    verweigert = AuditEvent.objects.filter(action="auth.denied", entity_type="webhook")
    assert verweigert.count() == 1 and verweigert.get().after_state["path"] == WEBHOOK_URL
    # falsches Token
    antwort = _webhook(client, {"doc_id": remote_id}, **{"X-MHV-Webhook-Token": FREMDER_TOKEN})
    assert antwort.status_code == 401 and verweigert.count() == 2
    assert not _pull_ops().exists()

    # richtiges Token: eine Operation; dieselbe Meldung erneut legt keine zweite an
    fest = timezone.now()
    with monkeypatch.context() as m:
        m.setattr(timezone, "now", lambda: fest)
        antwort = _webhook(client, {"doc_id": remote_id}, **{"X-MHV-Webhook-Token": WEBHOOK_TOKEN})
        assert antwort.status_code == 202
        body = antwort.json()
        assert body["accepted"] is True and body["created"] is True
        op = SyncOperation.objects.get(pk=body["operation"])
        assert op.kind == OperationKind.PAPERLESS_PULL and op.status == OperationStatus.PENDING
        assert op.payload["paperless_id"] == remote_id and op.priority == 50
        assert AuditEvent.objects.get(action="sync.webhook", entity_id=op.pk).after_state["created"] is True
        antwort = _webhook(client, {"doc_id": remote_id}, **{"X-MHV-Webhook-Token": WEBHOOK_TOKEN})
        assert antwort.status_code == 202
        assert antwort.json() == {"accepted": True, "operation": op.pk, "created": False}
        assert _pull_ops().count() == 1
    # Bearer-Schreibweise wird ebenfalls angenommen; solange die Uebernahme wartet, verweist auch ein anderes
    # Ereignis auf dieselbe Operation (13.09.2026: kein zweites Pull fuer ein wartendes Dokument)
    antwort = _webhook(
        client, {"doc_id": remote_id, "event": "updated"}, Authorization=f"Bearer {WEBHOOK_TOKEN}"
    )
    assert antwort.status_code == 202
    assert antwort.json() == {"accepted": True, "operation": op.pk, "created": False}
    assert _pull_ops().count() == 1

    # ohne doc_id oder mit ungueltigem JSON: 400, keine Operation
    assert _webhook(client, {"foo": "bar"}, **{"X-MHV-Webhook-Token": WEBHOOK_TOKEN}).status_code == 400
    kaputt = client.post(
        WEBHOOK_URL,
        data="{kein json",
        content_type="application/json",
        headers={"X-MHV-Webhook-Token": WEBHOOK_TOKEN},
    )
    assert kaputt.status_code == 400
    # doc_url wird auf die ID reduziert
    antwort = _webhook(
        client,
        {"doc_url": "https://paperless.example.test/documents/5/"},
        **{"X-MHV-Webhook-Token": WEBHOOK_TOKEN},
    )
    assert antwort.status_code == 202
    op_url = SyncOperation.objects.get(pk=antwort.json()["operation"])
    assert op_url.payload["paperless_id"] == 5
    # Body ist keine Wahrheit: Angaben zu einem nicht vorhandenen Dokument werden nicht uebernommen
    antwort = _webhook(
        client,
        {"doc_id": 999, "title": "Erfunden", "checksum": "0" * 64, "original_file_name": "erfunden.pdf"},
        **{"X-MHV-Webhook-Token": WEBHOOK_TOKEN},
    )
    assert antwort.status_code == 202
    op_fremd = SyncOperation.objects.get(pk=antwort.json()["operation"])
    assert op_fremd.payload == {"paperless_id": 999, "event": "webhook"}

    ops()
    op.refresh_from_db()
    op_url.refresh_from_db()
    op_fremd.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result["inbox"] is True
    assert op_url.status == OperationStatus.SKIPPED and "nicht" in op_url.result["skipped"]
    assert op_fremd.status == OperationStatus.SKIPPED and "nicht" in op_fremd.result["skipped"]
    assert not SyncOperation.objects.filter(status=OperationStatus.PENDING).exists()
    # nach dem Abschluss zaehlt ein neues Ereignis wieder und laeuft ohne Konflikt durch
    antwort = _webhook(
        client, {"doc_id": remote_id, "event": "updated"}, Authorization=f"Bearer {WEBHOOK_TOKEN}"
    )
    assert antwort.status_code == 202 and antwort.json()["created"] is True
    op_update = SyncOperation.objects.get(pk=antwort.json()["operation"])
    assert op_update.payload == {"paperless_id": remote_id, "event": "updated"}
    ops()
    op_update.refresh_from_db()
    assert op_update.status == OperationStatus.DONE and "conflict" not in op_update.result
    docs = Document.objects.filter(source="paperless")
    assert docs.count() == 1 and docs.get().object_id == eingang.pk
    link = _link(remote_id)
    assert link.document_id == docs.get().pk and link.state == LinkState.SYNCED
    assert not Document.objects.filter(original_name="erfunden.pdf").exists()
    assert not ExternalLink.objects.filter(external_id__in=["5", "999"]).exists()
    # jede Operation liest das Dokument selbst ueber die API; Angaben aus dem Body werden nie uebernommen
    assert [c[1][0] for c in paperless.calls if c[0] == "get_document"] == [remote_id, 5, 999, remote_id]
    assert paperless.call_names().count("download") == 1


def test_webhook_nimmt_zeichenketten_und_zahlen_als_body_an(objekt, paperless, eingang, client):
    """14.09.2026: Paperless-Workflows senden je nach Einstellung keinen JSON-Gegenstand, sondern einen JSON-String
    (die Dokument-ID oder erneut kodiertes JSON) oder eine nackte Zahl. Frueher: AttributeError und HTTP 500 im
    Sekundentakt; jetzt werden diese Formen gelesen, Unlesbares ergibt 400."""
    remote_id = paperless.add_document("Als Zeichenkette gemeldet", content=pdf_bytes())
    kopf = {"X-MHV-Webhook-Token": WEBHOOK_TOKEN}

    def roh(body: str):
        return client.post(WEBHOOK_URL, data=body, content_type="application/json", headers=kopf)

    # JSON-String mit der ID
    antwort = roh(json.dumps(str(remote_id)))
    assert antwort.status_code == 202 and antwort.json()["created"] is True
    op = SyncOperation.objects.get(pk=antwort.json()["operation"])
    assert op.payload["paperless_id"] == remote_id and _pull_ops().count() == 1
    # doppelt kodiertes JSON und nackte Zahl verweisen auf dieselbe wartende Operation
    antwort = roh(json.dumps(json.dumps({"doc_id": remote_id, "event": "updated"})))
    assert antwort.status_code == 202 and antwort.json() == {
        "accepted": True,
        "operation": op.pk,
        "created": False,
    }
    antwort = roh(str(remote_id))
    assert antwort.status_code == 202 and antwort.json()["created"] is False
    # String mit Dokument-URL
    antwort = roh(json.dumps(f"https://paperless.example/api/documents/{remote_id}/"))
    assert antwort.status_code == 202 and antwort.json()["created"] is False
    assert _pull_ops().count() == 1
    # Unbrauchbares: 400 statt 500, keine Operation
    for body in (
        json.dumps("Wartung Heizung"),
        json.dumps([1, 2]),
        json.dumps(True),
        json.dumps(None),
        "null",
    ):
        assert roh(body).status_code == 400, body
    assert _pull_ops().count() == 1


def test_webhook_abgeschaltet_oder_anbindung_inaktiv_legt_nichts_an(objekt, paperless, client, admin_user):
    store.set("paperless.webhook_enabled", False, user=admin_user, reason="Test")
    remote_id = paperless.add_document("Nicht angenommen", content=pdf_bytes())
    antwort = _webhook(client, {"doc_id": remote_id}, **{"X-MHV-Webhook-Token": WEBHOOK_TOKEN})
    assert antwort.status_code == 404
    assert not _pull_ops().exists()
    assert not AuditEvent.objects.filter(action__in=["sync.webhook", "auth.denied"]).exists()

    # Webhook an, Hauptschalter der Anbindung aus: angenommen, aber ohne Operation und ohne Protokolleintrag
    store.set("paperless.webhook_enabled", True, user=admin_user, reason="Test")
    store.set("paperless.enabled", False, user=admin_user, reason="Test")
    antwort = _webhook(client, {"doc_id": remote_id}, **{"X-MHV-Webhook-Token": WEBHOOK_TOKEN})
    assert antwort.status_code == 202
    assert antwort.json() == {"accepted": True, "ignored": "paperless inaktiv"}
    assert not _pull_ops().exists()
    assert not AuditEvent.objects.filter(action="sync.webhook").exists()
    # das Geheimnis wird vor dem Hauptschalter geprueft
    antwort = _webhook(client, {"doc_id": remote_id})
    assert antwort.status_code == 401
    assert AuditEvent.objects.filter(action="auth.denied", entity_type="webhook").count() == 1


def test_objektfeld_geaendert_erzeugt_konflikt_im_objekt_und_vorschlag_im_eingang(
    objekt, anderes_objekt, paperless, eingang, ops
):
    field_ids = services.connection_meta()["field_ids"]
    im_objekt, doc_objekt = _importieren(
        paperless, ops, pdf_bytes(), title="Rechnung 623", custom_fields={field_ids["object"]: "623"}
    )
    im_eingang, doc_eingang = _importieren(paperless, ops, pdf_bytes(LINES_EINGANG), title="Unklar")
    assert doc_objekt.object_id == objekt.pk and doc_eingang.object_id == eingang.pk
    assert paperless.call_names().count("download") == 2

    # Feld Objekt in Paperless: am Dokument des Objekts 623 auf 624 umgestellt, am Eingangsdokument auf 623 gesetzt
    paperless.set_custom_field_values(im_objekt, {field_ids["object"]: "624"})
    paperless.set_custom_field_values(im_eingang, {field_ids["object"]: "623"})
    aufrufe_vorher = len(paperless.calls)
    assert paperless_pull.poll(force=True)["enqueued"] == 2
    ops()
    ergebnisse = {op.payload["paperless_id"]: op for op in _pull_ops().order_by("-id")[:2]}
    assert all(op.status == OperationStatus.DONE for op in ergebnisse.values())

    # Objekt 623: Konflikt object_changed_remote, keine Uebernahme in das andere Objekt
    op_objekt = ergebnisse[im_objekt]
    assert op_objekt.result["conflict"] == "object_changed_remote"
    fall = ReviewCase.objects.get(case_type=CaseType.SYNC_CONFLICT, document=doc_objekt)
    assert fall.case_subtype == "object_changed_remote" and fall.status == CaseStatus.OPEN
    assert fall.pk == op_objekt.result["case_id"] and fall.object_id == objekt.pk
    assert fall.context == {
        "paperless_id": str(im_objekt),
        "local_object_id": objekt.pk,
        "remote_object_id": anderes_objekt.pk,
    }
    assert [k for k, _ in conflicts.choices_for(fall)] == ["apply_remote_object", "push_local_object"]
    link_objekt = _link(im_objekt)
    assert link_objekt.state == LinkState.CONFLICT and link_objekt.state_reason == "object_changed_remote"
    assert link_objekt.synced_fields["object_field"] == "623"
    doc_objekt.refresh_from_db()
    assert doc_objekt.object_id == objekt.pk and doc_objekt.status == "registered"
    assert not Document.objects.filter(object=anderes_objekt).exists()

    # Eingang: Zuordnung aus Paperless als Vorschlag from_paperless, Dokument bleibt im Eingang
    op_eingang = ergebnisse[im_eingang]
    assert op_eingang.result == {"paperless_id": str(im_eingang), "proposal": objekt.pk}
    vorschlag = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc_eingang)
    assert vorschlag.case_subtype == "from_paperless" and vorschlag.status == CaseStatus.OPEN
    assert vorschlag.object_id == eingang.pk
    assert vorschlag.candidates == [
        {
            "object_id": objekt.pk,
            "object_number": "623",
            "score": 0.9,
            "evidence": [{"kind": "paperless_field", "text": "Feld Objekt in Paperless"}],
        }
    ]
    assert vorschlag.proposed_action == {"action": "assign_object", "object_id": objekt.pk}
    assert vorschlag.context == {
        "source": "paperless",
        "paperless_id": str(im_eingang),
        "remote_object_id": objekt.pk,
    }
    link_eingang = _link(im_eingang)
    assert link_eingang.state == LinkState.SYNCED and link_eingang.synced_fields["title"] == "Unklar"
    doc_eingang.refresh_from_db()
    assert doc_eingang.object_id == eingang.pk
    # nur lesende Aufrufe, kein zweiter Download, nichts nach Paperless geschrieben
    assert set(paperless.call_names()[aufrufe_vorher:]) <= {
        "list_documents",
        "iter_pages",
        "get_document",
        "get_metadata",
    }

    # erneute Meldung derselben Aenderung: kein zweiter offener Fall je Schluessel
    paperless.set_custom_field_values(im_objekt, {field_ids["object"]: "624"})
    paperless.set_custom_field_values(im_eingang, {field_ids["object"]: "623"})
    assert paperless_pull.poll(force=True)["enqueued"] == 2
    ops()
    assert ReviewCase.objects.filter(case_type=CaseType.SYNC_CONFLICT, document=doc_objekt).count() == 1
    assert ReviewCase.objects.filter(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc_eingang).count() == 1
    assert not SyncOperation.objects.filter(status=OperationStatus.PENDING).exists()


def test_uuid_feld_verknuepft_ohne_download_und_meldet_zweite_kopie(objekt, paperless, eingang, ops):
    field_ids = services.connection_meta()["field_ids"]
    doc = Document.objects.create(
        object=objekt,
        sha256="a" * 64,
        size_bytes=100,
        mime_type="application/pdf",
        original_name="Aus_der_Anwendung.pdf",
        current_name="Aus_der_Anwendung.pdf",
        source="upload",
        status="hashed",
        first_seen_at=timezone.now(),
    )
    anzahl = Document.objects.count()
    inhalt = b"abweichender Inhalt der Paperless-Kopie (Testwert)"
    erste = paperless.add_document(
        "Kopie mit UUID", content=inhalt, custom_fields={field_ids["uuid"]: str(doc.uuid)}
    )
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ops()
    op = _pull_ops().get()
    assert op.status == OperationStatus.DONE and op.result == {"linked": doc.pk, "reason": "uuid"}
    link = _link(erste)
    assert link.document_id == doc.pk and link.state == LinkState.LINKED and link.state_reason == "UUID-Feld"
    assert link.checksum_sha256 == hashlib.sha256(inhalt).hexdigest()
    ereignis = AuditEvent.objects.get(action="sync.paperless_pull", entity_id=doc.pk)
    assert ereignis.after_state == {"paperless_id": erste, "linked_existing": True, "reason": "uuid"}
    assert "download" not in paperless.call_names() and Document.objects.count() == anzahl

    # zweite Kopie mit derselben UUID: Verknuepfung bleibt auf der ersten, Konfliktfall duplicate_remote
    zweite = paperless.add_document(
        "Zweite Kopie mit UUID", content=inhalt, custom_fields={field_ids["uuid"]: str(doc.uuid)}
    )
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ops()
    op2 = _pull_ops().order_by("-id").first()
    assert op2.status == OperationStatus.DONE and op2.result["duplicate_remote"] is True
    assert op2.result["document_id"] == doc.pk and op2.result["linked_paperless_id"] == str(erste)
    link.refresh_from_db()
    assert link.external_id == str(erste) and link.state == LinkState.LINKED
    assert ExternalLink.objects.filter(system=SyncSystem.PAPERLESS).count() == 1
    fall = ReviewCase.objects.get(case_type=CaseType.SYNC_CONFLICT, document=doc)
    assert fall.case_subtype == "duplicate_remote" and fall.status == CaseStatus.OPEN
    assert fall.pk == op2.result["case_id"] and fall.object_id == objekt.pk
    assert fall.context == {
        "paperless_id": str(zweite),
        "linked_paperless_id": str(erste),
        "match": "uuid",
        "title": "Zweite Kopie mit UUID",
    }
    ereignisse = list(
        AuditEvent.objects.filter(action="sync.paperless_pull", entity_id=doc.pk).order_by("id")
    )
    assert [e.after_state.get("duplicate_remote") for e in ereignisse] == [None, True]
    dublette = ereignisse[-1]
    assert dublette.after_state["paperless_id"] == zweite and dublette.after_state["match"] == "uuid"
    assert "download" not in paperless.call_names() and Document.objects.count() == anzahl
    assert not any(c[0] in ("bulk_edit", "patch_document", "delete_document") for c in paperless.calls)


def test_zweite_identische_kopie_haengt_verknuepfung_nicht_um(objekt, paperless, eingang, run_all, ops):
    field_ids = services.connection_meta()["field_ids"]
    data = pdf_bytes()
    erste, doc = _importieren(paperless, ops, data, custom_fields={field_ids["object"]: "623"})
    run_all(objekt, job_types=[JobType.HASH])  # Pruefsumme lokal bekannt, Quelle paperless: kein Rueck-Upload
    doc.refresh_from_db()
    assert doc.object_id == objekt.pk and doc.sha256 == hashlib.sha256(data).hexdigest()
    assert not SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PUSH).exists()
    anzahl = Document.objects.count()

    zweite = paperless.add_document("Zweite identische Kopie", content=data)
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ops()
    op = _pull_ops().order_by("-id").first()
    assert op.status == OperationStatus.DONE
    assert op.result == {
        "duplicate_remote": True,
        "paperless_id": zweite,
        "document_id": doc.pk,
        "linked_paperless_id": str(erste),
        "case_id": ReviewCase.objects.get(case_type=CaseType.SYNC_CONFLICT, document=doc).pk,
    }
    link = _link(erste)
    assert link.document_id == doc.pk and link.state == LinkState.LINKED
    assert link.state_reason == "aus Paperless übernommen" and link.synced_fields["object_field"] == "623"
    assert not ExternalLink.objects.filter(external_id=str(zweite)).exists()
    fall = ReviewCase.objects.get(case_type=CaseType.SYNC_CONFLICT, document=doc)
    assert fall.case_subtype == "duplicate_remote" and fall.status == CaseStatus.OPEN
    assert fall.context["match"] == "checksum" and fall.context["paperless_id"] == str(zweite)
    assert [k for k, _ in conflicts.choices_for(fall)] == ["keep_local"]
    ereignisse = list(
        AuditEvent.objects.filter(action="sync.paperless_pull", entity_id=doc.pk).order_by("id")
    )
    assert [e.after_state.get("duplicate_remote") for e in ereignisse] == [None, True]
    assert ereignisse[-1].after_state["linked_paperless_id"] == str(erste)
    assert ereignisse[-1].object_id == objekt.pk
    assert Document.objects.count() == anzahl and paperless.call_names().count("download") == 1

    # erneute Meldung der zweiten Kopie (Titel geaendert): kein zweiter offener Fall, weiterhin kein Download
    paperless.patch_document(zweite, title="Zweite identische Kopie, umbenannt")
    assert paperless_pull.poll(force=True)["enqueued"] == 1
    ops()
    op2 = _pull_ops().order_by("-id").first()
    assert op2.status == OperationStatus.DONE and op2.result["case_id"] is None
    assert ReviewCase.objects.filter(case_type=CaseType.SYNC_CONFLICT, document=doc).count() == 1
    assert _link(erste).external_id == str(erste) and paperless.call_names().count("download") == 1
    assert Document.objects.count() == anzahl


def test_papierkorb_in_paperless_erzeugt_konflikt_ohne_lokale_loeschung(objekt, paperless, eingang, ops):
    data = pdf_bytes()
    remote_id, doc = _importieren(paperless, ops, data)
    paperless.trash_document(remote_id)
    paperless_pull.enqueue_from_webhook(remote_id, event="trash")
    ops()
    link = _link(remote_id)
    assert link.state == LinkState.TRASHED and "Papierkorb" in (link.state_reason or "")
    fall = ReviewCase.objects.get(
        case_type=CaseType.SYNC_CONFLICT, case_subtype="trashed_remote", document=doc
    )
    assert fall.status == CaseStatus.OPEN and fall.context["paperless_id"] == str(remote_id)
    doc.refresh_from_db()
    assert doc.deleted_at is None and Path(doc.source_path).exists()
    # zweite Meldung: kein zweiter Fall, kein Download
    downloads = paperless.call_names().count("download")
    paperless_pull.enqueue_from_webhook(remote_id, event="trash2")
    ops()
    assert ReviewCase.objects.filter(case_subtype="trashed_remote", document=doc).count() == 1
    assert paperless.call_names().count("download") == downloads
    # Wiederherstellung in Paperless: bekannter Stand, keine erneute Uebernahme, Zustand wieder abgeglichen
    paperless.restore_document(remote_id)
    paperless_pull.enqueue_from_webhook(remote_id, event="restore")
    ops()
    link.refresh_from_db()
    assert link.state == LinkState.SYNCED and Document.objects.filter(source="paperless").count() == 1


def test_schutz_nur_mit_objektfeld_uebernehmen(objekt, paperless, eingang, ops, admin_user, client_as):
    """Produktionsstandard: ohne Feld MHV Objekt bleibt ein neues Paperless-Dokument in Paperless (kein Eingang),
    mit Feld wird es direkt in das Objekt uebernommen."""
    store.set("paperless.import_only_with_object", True, user=admin_user, reason="Test")
    ohne = paperless.add_document(
        "Ohne Bezug", content=pdf_bytes(LINES_EINGANG), original_file_name="ohne.pdf"
    )
    feld = services.connection_meta()["field_ids"]["object"]
    mit = paperless.add_document(
        "Mit Bezug",
        content=pdf_bytes(),
        custom_fields={feld: objekt.object_number},
        original_file_name="mit.pdf",
    )
    paperless_pull.poll(force=True)
    ops()
    ops_ohne = _pull_ops().get(payload__paperless_id=ohne)
    assert ops_ohne.status == OperationStatus.SKIPPED and "MHV Objekt" in ops_ohne.result["skipped"]
    assert not Document.objects.filter(source="paperless", object=eingang).exists()
    doc = _link(mit).document
    assert doc.object_id == objekt.pk and doc.source == "paperless"
    assert paperless.call_names().count("download") == 1
    # Cursor von Hand auf jetzt: aeltere Aenderungen werden nicht mehr geprueft, Protokoll vorhanden
    client = client_as(admin_user)
    resp = client.post(reverse("sync_cursor_now"))
    assert resp.status_code == 302
    cursor = services.get_cursor(SyncSystem.PAPERLESS, paperless_pull.CURSOR_MODIFIED)
    assert cursor.value and cursor.meta["reason"] == "Altbestand übersprungen"
    paperless.add_document("Alt", content=b"alt", created="2024-01-01")
    assert AuditEvent.objects.filter(action="sync.cursor_set", user_id=admin_user.pk).exists()


def test_wartende_uebernahme_wird_nicht_doppelt_eingereiht(objekt, paperless, ops, admin_user):
    """Webhook und Abgleich reihen fuer ein Dokument mit wartender Uebernahme keine zweite Operation ein; nach dem
    Abschluss der Operation zaehlt ein neues Ereignis wieder (Massenbearbeitung 13.09.2026)."""
    from apps.sync.flows.paperless_pull import enqueue_from_webhook, pending_pull, poll

    pid = paperless.add_document("Rechnung", pdf_bytes(["Rechnung Objekt 623"]), original_file_name="R.pdf")
    op1, created1 = enqueue_from_webhook(pid, event="added")
    op2, created2 = enqueue_from_webhook(pid, event="updated")
    assert created1 is True and created2 is False and op2.pk == op1.pk
    assert pending_pull(pid).pk == op1.pk
    services.set_cursor(SyncSystem.PAPERLESS, "modified_cursor", "2000-01-01T00:00:00+00:00")
    poll(force=True)
    assert SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PULL).count() == 1
    ops()
    op1.refresh_from_db()
    assert op1.status == OperationStatus.DONE and pending_pull(pid) is None
    op3, created3 = enqueue_from_webhook(pid, event="updated")
    assert created3 is True and op3.pk != op1.pk


EML_BYTES = b"From: absender@example.test\r\nSubject: Testnachricht\r\n\r\nInhalt ohne echte Personen.\r\n"


HTML_BYTES = b"<html><body><p>Seite ohne echte Personen.</p></body></html>"
# Office-Altformat: OLE-Vorspann (office_legacy); seit dem 26.09.2026 wandelt LibreOffice im Worker es in PDF,
# in diesen Tests fehlt LibreOffice (Fixture ohne_soffice), die Kette legt den Fall mit Notiz an
DOC_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(512)
# Praesentation im OpenDocument-Format: kennt die Formatweiche nicht (kein Impress im Worker-Image)
ODP_BYTES = b"PK\x03\x04" + bytes(64)
ODP_MIME = "application/vnd.oasis.opendocument.presentation"


@pytest.fixture
def ohne_soffice(monkeypatch, tmp_path):
    monkeypatch.setenv("SOFFICE_BIN", str(tmp_path / "soffice-fehlt"))


def test_nicht_verarbeitbares_original_kommt_als_archivfassung(
    paperless, eingang, ops, run_all, ohne_soffice
):
    """Ein Format, das die Verarbeitung nicht liest (hier .odp): hat Paperless eine PDF-Archivfassung, laedt die
    Uebernahme diese statt des Originals (25.09.2026, 6.527 Faelle "nicht unterstuetztes Format" im Bestand).
    E-Mails (.eml, .msg) liest die Kette seit dem 25.09.2026 selbst, HTML und Office-Altformate (.doc ueber
    LibreOffice) seit dem 26.09.2026: sie kommen als Original, auch wenn eine Archivfassung existiert."""
    archiv = pdf_bytes()
    remote_id = paperless.add_document(
        "Vortrag",
        content=ODP_BYTES,
        original_file_name="Vortrag.odp",
        mime_type=ODP_MIME,
        archive_content=archiv,
    )
    paperless_pull.poll(force=True)
    ops()
    link = _link(remote_id)
    doc = link.document
    assert doc.current_name == "Vortrag.pdf" and doc.original_name == "Vortrag.odp"
    assert doc.mime_type == "application/pdf" and doc.size_bytes == len(archiv)
    assert Path(doc.source_path).read_bytes() == archiv
    assert link.mime_type == "application/pdf" and link.synced_fields["variant"] == "archive"
    downloads = [c for c in paperless.calls if c[0] == "download"]
    assert downloads and downloads[-1][2] == {"original": False}
    # Office-Altformat (26.09.2026): Original trotz Archivfassung, die Kette wandelt selbst
    remote_id = paperless.add_document(
        "Schreiben",
        content=DOC_BYTES,
        original_file_name="Schreiben.doc",
        mime_type="application/msword",
        archive_content=pdf_bytes(),
    )
    paperless_pull.poll(force=True)
    ops()
    link = _link(remote_id)
    assert link.document.current_name == "Schreiben.doc" and link.synced_fields["variant"] == "original"
    assert [c for c in paperless.calls if c[0] == "download"][-1][2] == {"original": True}
    # Ohne Archivfassung bleibt es beim Original (und spaeter beim Fall nicht unterstuetztes Format)
    remote_id = paperless.add_document(
        "Schreiben 2", content=DOC_BYTES, original_file_name="Schreiben_2.doc", mime_type="application/msword"
    )
    paperless_pull.poll(force=True)
    ops()
    link = _link(remote_id)
    assert link.document.current_name == "Schreiben_2.doc" and link.synced_fields["variant"] == "original"
    assert [c for c in paperless.calls if c[0] == "download"][-1][2] == {"original": True}
    # E-Mail: Original, obwohl Paperless eine Archivfassung hat; die Kette liest sie als Textseite
    remote_id = paperless.add_document(
        "Nachricht",
        content=EML_BYTES,
        original_file_name="Nachricht.eml",
        mime_type="message/rfc822",
        archive_content=pdf_bytes(),
    )
    paperless_pull.poll(force=True)
    ops()
    link = _link(remote_id)
    mail = link.document
    assert mail.current_name == "Nachricht.eml" and link.synced_fields["variant"] == "original"
    run_all(eingang)
    mail.refresh_from_db()
    assert mail.page_count == 1 and mail.origin_kind == "digital"
    assert not ReviewCase.objects.filter(document=mail, case_subtype="unsupported_format").exists()
    # HTML (26.09.2026): ebenfalls Original, die Kette liest es als Textseite
    remote_id = paperless.add_document(
        "Seite",
        content=HTML_BYTES,
        original_file_name="Seite.html",
        mime_type="text/html",
        archive_content=pdf_bytes(),
    )
    paperless_pull.poll(force=True)
    ops()
    link = _link(remote_id)
    seite = link.document
    assert seite.current_name == "Seite.html" and link.synced_fields["variant"] == "original"
    run_all(eingang)
    seite.refresh_from_db()
    assert seite.page_count == 1 and seite.origin_kind == "digital"
    assert not ReviewCase.objects.filter(document=seite, case_subtype="unsupported_format").exists()


def test_archivfassung_befehl_stellt_bestand_um(paperless, eingang, ops, run_all, ohne_soffice):
    """Bestand: Dokument mit Fall nicht unterstuetztes Format (hier .doc ohne LibreOffice, Umwandlung nicht
    verfuegbar) wird per Befehl auf die spaeter vorhandene Archivfassung umgestellt, Fall erledigt, Dokument
    laeuft von vorn durch die Kette."""
    from io import StringIO

    from django.core.management import call_command

    remote_id = paperless.add_document(
        "Schreiben", content=DOC_BYTES, original_file_name="Schreiben.doc", mime_type="application/msword"
    )
    paperless_pull.poll(force=True)
    ops()
    run_all(eingang)
    doc = _link(remote_id).document
    doc.refresh_from_db()
    fall = ReviewCase.objects.get(document=doc, case_subtype="unsupported_format", status=CaseStatus.OPEN)
    assert doc.status == "review" and doc.current_name == "Schreiben.doc"
    assert fall.context["kind"] == "unsupported" and fall.context["content_checked"] is True
    assert fall.context["note"] == "Umwandlung nicht verfügbar"
    out = StringIO()
    call_command("paperless_archivfassung", stdout=out)
    assert "keine Archivfassung in Paperless 1" in out.getvalue() and "Vorschau" in out.getvalue()
    archiv = pdf_bytes()
    paperless.set_archive(remote_id, archiv)
    out = StringIO()
    call_command("paperless_archivfassung", stdout=out)
    assert "Archivfassung vorhanden 1" in out.getvalue()
    doc.refresh_from_db()
    assert doc.status == "review"  # Vorschau aendert nichts
    out = StringIO()
    call_command("paperless_archivfassung", "--echt", stdout=out)
    assert "umgestellt 1" in out.getvalue()
    doc.refresh_from_db()
    fall.refresh_from_db()
    assert doc.status == "registered" and doc.current_name == "Schreiben.pdf" and doc.sha256 is None
    assert doc.mime_type == "application/pdf" and Path(doc.source_path).read_bytes() == archiv
    assert fall.status == CaseStatus.RESOLVED and fall.resolution["action"] == "reprocess_archive"
    assert _link(remote_id).synced_fields["variant"] == "archive"
    assert AuditEvent.objects.filter(action="sync.paperless_archive", entity_id=doc.pk).exists()
    ingest.ensure_run(eingang, documents=[doc])
    run_all(eingang)
    doc.refresh_from_db()
    assert doc.page_count == 1 and doc.sha256
    assert not ReviewCase.objects.filter(
        document=doc, case_subtype="unsupported_format", status=CaseStatus.OPEN
    ).exists()
    # Zweiter Aufruf findet nichts mehr
    out = StringIO()
    call_command("paperless_archivfassung", "--echt", stdout=out)
    assert "keine offenen Faelle" in out.getvalue()


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # noqa: S324 (Drive-Pruefsumme, keine Sicherheit)


def _bestandsdatei(objekt, drive, name: str, data: bytes, mime: str):
    file_id = drive.add_file(objekt.drive_root_folder_id, name, data, mime)
    return Document.objects.create(
        object=objekt,
        drive_md5=_md5(data),
        size_bytes=len(data),
        mime_type=mime,
        original_name=name,
        current_name=name,
        source="drive_existing",
        drive_file_id=file_id,
        source_path=f"Objekt/{name}",
        status="registered",
        first_seen_at=timezone.now(),
    )


def test_archivfassung_auch_fuer_bestandsdatei_aus_drive(
    objekt, paperless, drive, eingang, ops, run_all, ohne_soffice
):
    """Server 25.09.2026: 6.527 Faelle, der Befehl fand 3. Die Uebernahme verknuepft ein Paperless-Dokument, dessen
    Datei schon als Drive-Bestand registriert ist, nur per Pruefsumme; das Dokument behaelt die Quelle Drive-Bestand.
    Der Befehl waehlt deshalb ueber die Verknuepfung, die Kette liest danach die lokale PDF-Fassung statt die
    E-Mail erneut aus Drive zu laden; die Drive-Datei bleibt unveraendert. Faelle ohne Verknuepfung erscheinen nach
    Quelle und Endung in der Vorschau."""
    from io import StringIO

    from django.core.management import call_command

    doc = _bestandsdatei(objekt, drive, "Schreiben.doc", DOC_BYTES, "application/msword")
    ohne = _bestandsdatei(objekt, drive, "Aufnahme.mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(64), "video/mp4")
    ingest.ensure_run(objekt, documents=[doc, ohne])
    run_all(objekt)
    doc.refresh_from_db()
    ohne.refresh_from_db()
    assert doc.status == "review" and ohne.status == "review"
    assert doc.sha256 == hashlib.sha256(DOC_BYTES).hexdigest()
    fall = ReviewCase.objects.get(document=doc, case_subtype="unsupported_format", status=CaseStatus.OPEN)
    remote_id = paperless.add_document(
        "Schreiben", content=DOC_BYTES, original_file_name="Schreiben.doc", mime_type="application/msword"
    )
    paperless_pull.poll(force=True)
    ops()
    assert (
        _link(remote_id).document_id == doc.pk
        and Document.objects.filter(deleted_at__isnull=True).count() == 2
    )
    doc.refresh_from_db()
    assert doc.source == "drive_existing"
    archiv = pdf_bytes()
    paperless.set_archive(remote_id, archiv)
    out = StringIO()
    call_command("paperless_archivfassung", stdout=out)
    text = out.getvalue()
    assert "Offene Faelle: 2" in text and "Archivfassung vorhanden 1" in text
    assert "Ohne Paperless-Verknuepfung nach Quelle: Drive-Bestand 1" in text
    assert "Ohne Paperless-Verknuepfung nach Endung: .mp4 1" in text
    assert "Aufnahme" not in text  # keine Dateinamen in der Ausgabe
    out = StringIO()
    call_command("paperless_archivfassung", "--objekt", objekt.object_number, "--echt", stdout=out)
    assert "umgestellt 1" in out.getvalue()
    doc.refresh_from_db()
    fall.refresh_from_db()
    assert (
        doc.source == "drive_existing" and doc.status == "registered" and doc.current_name == "Schreiben.pdf"
    )
    assert Path(doc.source_path).is_absolute() and Path(doc.source_path).read_bytes() == archiv
    assert fall.status == CaseStatus.RESOLVED
    ingest.ensure_run(objekt, documents=[doc])
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.page_count == 1 and doc.sha256 == hashlib.sha256(archiv).hexdigest()
    assert (
        doc.status not in ("registered", "review", "error")
        or not ReviewCase.objects.filter(
            document=doc, case_subtype="unsupported_format", status=CaseStatus.OPEN
        ).exists()
    )
    # Drive-Datei unveraendert: das Altformat bleibt, nur die lokale Arbeitsfassung ist das PDF
    ziel = Path(doc.source_path).parent / "drive-kontrolle.bin"
    drive.download(doc.drive_file_id, ziel)
    assert ziel.read_bytes() == DOC_BYTES and drive.get(doc.drive_file_id).name == "Schreiben.doc"
    ohne.refresh_from_db()
    assert ohne.status == "review"  # ohne Verknuepfung unveraendert


def test_archivfassung_fuer_geprueften_inhalt_trotz_bekanntem_mime(
    objekt, paperless, drive, eingang, ops, run_all
):
    """Rechnung.tmp mit MIME-Typ application/pdf aus Drive, Inhalt unbekannt (26.09.2026): die Formatweiche hat
    den Inhalt geprueft (content_checked), der MIME-Typ zaehlt nicht mehr. Der Befehl darf den Fall nicht als
    "Format inzwischen verarbeitbar" ueberspringen, sondern stellt ihn auf die Archivfassung um."""
    from io import StringIO

    from django.core.management import call_command

    daten = bytes(range(256)) * 4
    doc = _bestandsdatei(objekt, drive, "Rechnung.tmp", daten, "application/pdf")
    ingest.ensure_run(objekt, documents=[doc])
    run_all(objekt)
    doc.refresh_from_db()
    fall = ReviewCase.objects.get(document=doc, case_subtype="unsupported_format", status=CaseStatus.OPEN)
    assert doc.current_name == "Rechnung.tmp" and fall.context["content_checked"] is True
    remote_id = paperless.add_document(
        "Rechnung", content=daten, original_file_name="Rechnung.tmp", mime_type="application/pdf"
    )
    paperless_pull.poll(force=True)
    ops()
    assert _link(remote_id).document_id == doc.pk
    archiv = pdf_bytes()
    paperless.set_archive(remote_id, archiv)
    out = StringIO()
    call_command("paperless_archivfassung", stdout=out)
    text = out.getvalue()
    assert "Archivfassung vorhanden 1" in text and "Format inzwischen verarbeitbar" not in text
    out = StringIO()
    call_command("paperless_archivfassung", "--objekt", objekt.object_number, "--echt", stdout=out)
    assert "umgestellt 1" in out.getvalue()
    doc.refresh_from_db()
    fall.refresh_from_db()
    assert (
        doc.status == "registered"
        and doc.current_name == "Rechnung.pdf"
        and fall.status == CaseStatus.RESOLVED
    )
