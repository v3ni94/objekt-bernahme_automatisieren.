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
    # Bearer-Schreibweise wird ebenfalls angenommen; ein anderes Ereignis ergibt eine eigene Operation
    antwort = _webhook(
        client, {"doc_id": remote_id, "event": "updated"}, Authorization=f"Bearer {WEBHOOK_TOKEN}"
    )
    assert antwort.status_code == 202 and antwort.json()["created"] is True
    op_update = SyncOperation.objects.get(pk=antwort.json()["operation"])
    assert op_update.payload == {"paperless_id": remote_id, "event": "updated"}

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
    op_update.refresh_from_db()
    op_url.refresh_from_db()
    op_fremd.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.result["inbox"] is True
    assert op_update.status == OperationStatus.DONE and "conflict" not in op_update.result
    assert op_url.status == OperationStatus.SKIPPED and "nicht" in op_url.result["skipped"]
    assert op_fremd.status == OperationStatus.SKIPPED and "nicht" in op_fremd.result["skipped"]
    assert not SyncOperation.objects.filter(status=OperationStatus.PENDING).exists()
    docs = Document.objects.filter(source="paperless")
    assert docs.count() == 1 and docs.get().object_id == eingang.pk
    link = _link(remote_id)
    assert link.document_id == docs.get().pk and link.state == LinkState.SYNCED
    assert not Document.objects.filter(original_name="erfunden.pdf").exists()
    assert not ExternalLink.objects.filter(external_id__in=["5", "999"]).exists()
    # jede Operation liest das Dokument selbst ueber die API; Angaben aus dem Body werden nie uebernommen
    assert [c[1][0] for c in paperless.calls if c[0] == "get_document"] == [remote_id, remote_id, 5, 999]
    assert paperless.call_names().count("download") == 1


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
