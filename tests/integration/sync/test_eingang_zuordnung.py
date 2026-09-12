"""Objektzuordnung aus dem Dokumenteneingang (Fallgruppen Objektzuordnung, Lernen, Oberflaeche): eindeutiger
Objektbezug fuehrt zur automatischen Uebernahme, Schwellen erzwingen Vorschlag oder Automatik, Text ohne oder mit
widerspruechlichem Bezug bleibt im Eingang; die Datei wird in den Eingangsordner gespiegelt (idempotent) und bei der
Zuordnung verschoben, nie kopiert; manuelle Zuordnung und Ablehnung im Review Center erzeugen Lernbeispiele und
Audit; Regeln entstehen erst aus zwei Bestaetigungen verschiedener Dokumente; Seiten Dokumenteneingang,
Review-Detail und Verwaltung rendern mit Rechtepruefung."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from tests.integration.sync.conftest import LINES_623, pdf_bytes

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import operations, services
from apps.sync.assignment.learning import active_rules, deactivate_rule, derive_rules
from apps.sync.flows import assign, common, conflicts
from apps.sync.models import (
    AssignmentExample,
    AssignmentRule,
    ExternalLink,
    LinkRole,
    LinkState,
    OperationKind,
    OperationStatus,
    SyncOperation,
    SyncSystem,
)

pytestmark = pytest.mark.django_db

KORRESPONDENT = "Stadtwerke Beispiel GmbH"
ERFUNDEN = "Alle Angaben in diesem Testdokument sind erfunden; keine realen Personen oder Konten."
# Alle Eingangstexte tragen einen Energieausweis-Bezug (Regel R-02-ENERGIE-001, Vertrauen 1,0): im Eingangsobjekt
# legt die Entscheidung keine Faelle an, und ein Text ohne eindeutige Kategorie (Ablage 06) bricht dort im
# Entscheidungsjob ab (Pruefung "Ablage in 06 ohne Review-Fall" in classification.decide.persist). Der Standardtext
# LINES_623 (Wartungsrechnung) erreicht die Objektzuordnung deshalb nicht; seine Objektbezugszeile bleibt erhalten.
LINES_BEZUG_623 = [
    LINES_623[0],
    "Energieausweis für das Wohngebäude Musterstraße 49, 12345 Musterstadt",
    "Ausstellungsdatum 03.09.2026, gültig bis 02.09.2036",
    ERFUNDEN,
]
LINES_OHNE_BEZUG = [
    "Allgemeines Schreiben ohne Adresse",
    "Hinweise zum Energieausweis nach dem Gebäudeenergiegesetz",
    "Sehr geehrte Damen und Herren, wir informieren Sie über geänderte Ausstellungsregeln.",
    "Mit freundlichen Grüßen",
    ERFUNDEN,
]
LINES_WIDERSPRUCH = [
    "Energieausweis für zwei Wohngebäude",
    "Leistungsort Musterstraße 49, 12345 Musterstadt",
    "Leistungsort Beispielweg 7, 54321 Beispielhausen",
    "Ausstellungsdatum 03.09.2026, gültig bis 02.09.2036",
    ERFUNDEN,
]
# nur eine neutrale Adresse ohne Ort und PLZ: ein Kandidat, Bewertung unter der Standardschwelle 0,85
LINES_SCHWACH = [
    "Energieausweis, Schreiben zur Musterstraße 49",
    "Bitte um Rückmeldung bis zum Monatsende.",
    ERFUNDEN,
]


def lines_stadtwerke(nr: str) -> list[str]:
    return [
        KORRESPONDENT,
        "Energieausweis für das Wohngebäude, Ausfertigung",
        f"Vorgangsnummer {nr}",
        "Kundennummer 998877",
        "Lieferstelle Musterstraße 49, 12345 Musterstadt",
        ERFUNDEN,
    ]


LINES_NUR_KUNDENNUMMER = [
    KORRESPONDENT,
    "Energieausweis ohne Angabe des Gebäudes",
    "Kundennummer 998877",
    "Betrag 120,00 EUR",
    ERFUNDEN,
]


def _hochladen(eingang, lines, filename="Schreiben.pdf") -> Document:
    doc, _ = ingest.ingest_upload(eingang, filename=filename, data=pdf_bytes(lines))
    return doc


def _zuordnungsjob(eingang, doc) -> ProcessingJob:
    return ProcessingJob.objects.get(job_type=JobType.ASSIGN_OBJECT, object=eingang, document=doc)


def _offener_fall(doc) -> ReviewCase:
    return ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc, status=CaseStatus.OPEN)


def _dateien(drive, folder_id):
    return [c for c in drive.list_children(folder_id) if not c.is_folder]


def _schreibzugriffe(drive, ab: int = 0) -> list[tuple[str, tuple]]:
    return [o for o in drive.ops[ab:] if o[0] in ("move", "copy", "upload")]


def _feldwerte(paperless, remote_id: int) -> dict[str, str]:
    names = {f["id"]: f["name"] for f in paperless.custom_fields.values()}
    return {names[cf["field"]]: cf["value"] for cf in paperless.documents[remote_id]["custom_fields"]}


def _korrespondent(doc, external_id: str) -> ExternalLink:
    """Paperless-Verknuepfung mit Korrespondent, wie sie der Abgleich in synced_fields hinterlaesst."""
    return ExternalLink.objects.create(
        document=doc,
        system=SyncSystem.PAPERLESS,
        role=LinkRole.ORIGINAL,
        external_id=external_id,
        state=LinkState.SYNCED,
        synced_fields={"title": "Abschlag", "correspondent": KORRESPONDENT},
    )


def _konfliktfall(objekt, anderes_objekt) -> ReviewCase:
    """Abgleichskonflikt object_changed_remote an einem Dokument des Objekts 623."""
    from django.utils import timezone

    doc = Document.objects.create(
        object=objekt,
        sha256="e" * 64,
        size_bytes=1234,
        mime_type="application/pdf",
        original_name="Konflikt_Test.pdf",
        current_name="Konflikt_Test.pdf",
        source="upload",
        status="filed",
        first_seen_at=timezone.now(),
    )
    ExternalLink.objects.create(
        document=doc,
        system=SyncSystem.PAPERLESS,
        role=LinkRole.ORIGINAL,
        external_id="777",
        state=LinkState.CONFLICT,
        synced_fields={"title": "Konflikt"},
    )
    case = common.conflict(
        doc,
        "object_changed_remote",
        "777",
        {
            "remote_object_id": anderes_objekt.pk,
            "remote_object": anderes_objekt.object_number,
            "local_object": objekt.object_number,
        },
    )
    assert case is not None
    return case


def _sync_action(client, case, **data):
    return client.post(reverse("review_action", args=[case.pk]), {"query": "", **data})


# ---------------------------------------------------------------- Objektzuordnung
def test_eindeutiger_objektbezug_wird_automatisch_uebernommen(
    objekt, anderes_objekt, paperless, eingang, drive, run_all, ops
):
    doc = _hochladen(eingang, LINES_BEZUG_623, "Wartung_Heizung.pdf")
    uuid_vorher = doc.uuid
    run_all(eingang)
    job = _zuordnungsjob(eingang, doc)
    assert job.status == JobStatus.DONE and job.result["decision"] == "auto"
    doc.refresh_from_db()
    assert doc.status == "moved_out" and doc.uuid != uuid_vorher and doc.drive_file_id is None
    neu = Document.objects.get(uuid=uuid_vorher)
    assert neu.pk != doc.pk and neu.object_id == objekt.pk and neu.source == "moved_in"
    assert doc.duplicate_of_id == neu.pk and job.result["document_id"] == neu.pk
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=neu)
    assert fall.case_subtype == "auto" and fall.status == CaseStatus.RESOLVED and fall.object_id == objekt.pk
    assert fall.candidates[0]["object_number"] == "623" and fall.candidates[0]["object_id"] == objekt.pk
    assert fall.context["score"] >= 0.85 and fall.context["from_document_id"] == doc.pk
    assert "automatische Zuordnung" in fall.context["reasons"]
    assert fall.resolution["action"] == "assign_object_auto" and fall.resolution["object_id"] == objekt.pk
    arten = {e["kind"] for e in fall.candidates[0]["evidence"]}
    assert {"object_number", "address"} <= arten
    ereignis = AuditEvent.objects.get(action="inbox.assign_auto", entity_id=neu.pk)
    assert ereignis.after_state["score"] == fall.context["score"] and ereignis.object_id == objekt.pk
    assert ereignis.after_state["from_document_id"] == doc.pk and ereignis.after_state["evidence"]
    # kein offener Fall, kein Lernbeispiel (automatische Zuordnungen zaehlen nicht)
    assert not ReviewCase.objects.filter(
        case_type=CaseType.OBJECT_ASSIGNMENT, status=CaseStatus.OPEN
    ).exists()
    assert not AssignmentExample.objects.exists()
    # gespiegelte Datei wandert mit der Nachfolgezeile (noch im Eingangsordner, Ablage folgt im Zielobjekt)
    assert neu.drive_file_id == job.result["mirrored"]
    assert drive.get(neu.drive_file_id).parent_id == store.get("sync.inbox_folder_id")
    # die wartende Uebertragung nach Paperless folgt der Nachfolgezeile und laeuft fuer das Pilotobjekt
    push = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_PUSH)
    assert push.document_id == neu.pk and push.status == OperationStatus.PENDING
    ops()
    paperless.process_tasks()
    ops()
    push.refresh_from_db()
    assert push.status == OperationStatus.DONE
    link = ExternalLink.objects.get(document=neu, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
    assert link.state == LinkState.SYNCED
    werte = _feldwerte(paperless, int(link.external_id))
    assert werte["MHV Objekt"].startswith("623") and werte["MHV Dokument-UUID"] == str(uuid_vorher)


def test_hohe_schwelle_erzwingt_vorschlag_statt_automatik(
    objekt, anderes_objekt, paperless, eingang, run_all, admin_user
):
    store.set("sync.assignment_auto_min", 0.99, user=admin_user, reason="Test")
    doc = _hochladen(eingang, LINES_BEZUG_623, "Wartung_Heizung.pdf")
    run_all(eingang)
    job = _zuordnungsjob(eingang, doc)
    assert job.status == JobStatus.DONE and job.result["decision"] == "review"
    doc.refresh_from_db()
    assert doc.object_id == eingang.pk and doc.status != "moved_out"
    assert Document.objects.filter(uuid=doc.uuid).count() == 1
    fall = _offener_fall(doc)
    assert (
        fall.case_subtype == "proposal" and fall.object_id == eingang.pk and fall.pk == job.result["case_id"]
    )
    assert fall.candidates[0]["object_number"] == "623" and fall.candidates[0]["object_id"] == objekt.pk
    assert fall.candidates[0]["score"] >= 0.85
    assert fall.proposed_action == {"action": "assign_object", "object_id": objekt.pk}
    assert fall.context["decision"] == "review" and fall.context["mirrored"] is True
    assert any("Score unter auto_min 0.99" in r for r in fall.context["reasons"])
    assert "numbers" in fall.context["features"] and fall.context["features"]["supplier_norm"] is None
    assert not AuditEvent.objects.filter(action="inbox.assign_auto").exists()
    # erneuter Lauf legt keinen zweiten offenen Fall an
    ergebnis = assign.run_for_document(doc)
    assert ergebnis["case_id"] is None and ergebnis["decision"] == "review"
    assert ReviewCase.objects.filter(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc).count() == 1


def test_niedrige_schwelle_erzwingt_automatik_bei_eindeutigem_kandidaten(
    objekt, anderes_objekt, paperless, eingang, run_all, admin_user
):
    # Standardschwellen: einziger Kandidat unter 0,85 ergibt einen Vorschlag
    doc = _hochladen(eingang, LINES_SCHWACH, "Schreiben_A.pdf")
    run_all(eingang)
    fall = _offener_fall(doc)
    assert fall.case_subtype == "proposal" and 0.5 <= fall.candidates[0]["score"] < 0.85
    assert fall.proposed_action["object_id"] == objekt.pk
    # abgesenkte Schwellen (Katalog erlaubt fuer auto_min mindestens 0,5): derselbe Text wird automatisch uebernommen
    store.set("sync.assignment_auto_min", 0.6, user=admin_user, reason="Test")
    store.set("sync.assignment_gap_min", 0.0, user=admin_user, reason="Test")
    doc2 = _hochladen(eingang, [*LINES_SCHWACH, "Zweite Ausfertigung"], "Schreiben_B.pdf")
    uuid2 = doc2.uuid
    run_all(eingang)
    assert _zuordnungsjob(eingang, doc2).result["decision"] == "auto"
    neu = Document.objects.get(uuid=uuid2)
    assert neu.object_id == objekt.pk and neu.source == "moved_in"
    auto = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=neu)
    assert auto.case_subtype == "auto" and auto.status == CaseStatus.RESOLVED and auto.context["score"] < 0.85
    assert AuditEvent.objects.filter(action="inbox.assign_auto", entity_id=neu.pk).exists()
    # der erste Vorschlag bleibt unveraendert offen
    fall.refresh_from_db()
    assert fall.status == CaseStatus.OPEN


def test_text_ohne_objektbezug_bleibt_im_eingang(objekt, anderes_objekt, paperless, eingang, run_all):
    doc = _hochladen(eingang, LINES_OHNE_BEZUG, "Allgemein.pdf")
    run_all(eingang)
    job = _zuordnungsjob(eingang, doc)
    fall = _offener_fall(doc)
    doc.refresh_from_db()
    assert job.status == JobStatus.DONE and job.result == {
        "decision": "none",
        "case_id": fall.pk,
        "candidates": 0,
        "mirrored": doc.drive_file_id,
    }
    assert doc.drive_file_id  # in den Eingangsordner gespiegelt
    assert fall.case_subtype == "no_candidate" and fall.candidates == [] and fall.proposed_action is None
    assert fall.context["decision"] == "none" and fall.object_id == eingang.pk
    assert fall.context["reasons"] == ["kein Kandidat mit positivem Score"]
    assert doc.object_id == eingang.pk and doc.status != "moved_out"
    assert Document.objects.filter(uuid=doc.uuid).count() == 1
    assert not Document.objects.filter(object__in=[objekt, anderes_objekt]).exists()
    assert not AuditEvent.objects.filter(action="inbox.assign_auto").exists()


def test_widerspruechlicher_text_wird_nicht_automatisch_zugeordnet(
    objekt, anderes_objekt, paperless, eingang, run_all
):
    doc = _hochladen(eingang, LINES_WIDERSPRUCH, "Sammelrechnung.pdf")
    run_all(eingang)
    job = _zuordnungsjob(eingang, doc)
    assert job.status == JobStatus.DONE and job.result["decision"] != "auto"
    fall = _offener_fall(doc)
    assert fall.case_subtype in ("proposal", "no_candidate")
    doc.refresh_from_db()
    assert doc.object_id == eingang.pk and doc.status != "moved_out"
    assert Document.objects.filter(uuid=doc.uuid).count() == 1
    assert not Document.objects.filter(object__in=[objekt, anderes_objekt]).exists()
    assert not AuditEvent.objects.filter(action="inbox.assign_auto").exists()
    # beide Objekte sind als Kandidaten belegt, der Widerspruch ist als Grund sichtbar
    nummern = {c["object_number"] for c in fall.candidates}
    assert {"623", "624"} <= nummern
    assert all(c["contradictions"] for c in fall.candidates if c["object_number"] in ("623", "624"))
    assert any("Widerspruch" in r or "Abstand" in r for r in fall.context["reasons"])


def test_spiegelung_in_den_eingangsordner_ist_idempotent(objekt, paperless, eingang, drive, run_all):
    doc = _hochladen(eingang, LINES_OHNE_BEZUG, "Allgemein.pdf")
    assert doc.drive_file_id is None
    run_all(eingang)
    doc.refresh_from_db()
    folder_id = store.get("sync.inbox_folder_id")
    dateien = _dateien(drive, folder_id)
    assert len(dateien) == 1 and dateien[0].id == doc.drive_file_id
    datei = dateien[0]
    assert datei.name == doc.current_name and datei.sha256 == doc.sha256 and doc.drive_md5 == datei.md5
    assert datei.app_properties["sha256"] == doc.sha256
    assert datei.app_properties["mhv_uuid"] == str(doc.uuid)
    assert datei.app_properties["document_id"] == str(doc.pk)
    ereignis = AuditEvent.objects.get(action="drive.upload", entity_id=doc.pk)
    assert ereignis.after_state == {"drive_file_id": datei.id, "folder": "eingang"}
    uploads = len([o for o in drive.ops if o[0] == "upload"])
    assert uploads == 1
    # zweiter Lauf: Drive-ID bekannt, keine zweite Datei, kein zweiter Fall
    ergebnis = assign.run_for_document(doc)
    assert ergebnis["mirrored"] == datei.id and ergebnis["case_id"] is None
    assert len(_dateien(drive, folder_id)) == 1
    # Drive-ID lokal verloren: die Datei gleichen Hashs wird wiederverwendet statt erneut hochgeladen
    Document.objects.filter(pk=doc.pk).update(drive_file_id=None)
    doc.refresh_from_db()
    assert assign.mirror_to_inbox_folder(doc, drive) == datei.id
    doc.refresh_from_db()
    assert doc.drive_file_id == datei.id
    assert len(_dateien(drive, folder_id)) == 1
    assert len([o for o in drive.ops if o[0] == "upload"]) == uploads


# ---------------------------------------------------------------- manuelle Zuordnung und Ablehnung
def test_manuelle_zuordnung_ueber_das_review_center(
    objekt, anderes_objekt, paperless, eingang, drive, run_all, ops, client_as, admin_user
):
    store.set("sync.assignment_auto_min", 0.99, user=admin_user, reason="Test")
    doc = _hochladen(eingang, LINES_BEZUG_623, "Energieausweis_Musterstrasse_49.pdf")
    run_all(eingang)
    # Uebertragung nach Paperless ist abgeschlossen: Verknuepfung vorhanden
    ops()
    paperless.process_tasks()
    ops()
    doc.refresh_from_db()
    link = ExternalLink.objects.get(document=doc, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
    assert link.state == LinkState.SYNCED
    fall = _offener_fall(doc)
    assert fall.proposed_action["object_id"] == objekt.pk
    folder_id = store.get("sync.inbox_folder_id")
    datei_id = doc.drive_file_id
    assert datei_id and drive.get(datei_id).parent_id == folder_id
    uuid_vorher = doc.uuid

    client = client_as(admin_user)
    resp = _sync_action(client, fall, action="assign_object", target_object=str(objekt.pk), reason="Test")
    assert resp.status_code == 302 and resp["Location"].startswith(f"/review/{fall.pk}/")
    neu = Document.objects.get(uuid=uuid_vorher)
    assert neu.pk != doc.pk and neu.object_id == objekt.pk and neu.source == "moved_in"
    assert neu.drive_file_id == datei_id
    doc.refresh_from_db()
    assert doc.status == "moved_out" and doc.duplicate_of_id == neu.pk and doc.drive_file_id is None
    fall.refresh_from_db()
    assert fall.status == CaseStatus.RESOLVED and fall.resolved_by == admin_user
    assert fall.resolution == {"action": "assign_object", "object_id": objekt.pk, "new_document_id": neu.pk}
    beispiel = AssignmentExample.objects.get()
    assert beispiel.kind == "confirm" and beispiel.source == "human"
    assert beispiel.document_id == neu.pk and beispiel.target_object_id == objekt.pk
    assert beispiel.proposed_object_id == objekt.pk and beispiel.previous_object_id is None
    assert beispiel.decided_by == admin_user and beispiel.review_case_id == fall.pk
    assert beispiel.evidence and all(e["object_id"] == objekt.pk for e in beispiel.evidence)
    assert "numbers" in beispiel.features
    ereignis = AuditEvent.objects.get(action="inbox.assign", entity_id=neu.pk)
    assert ereignis.after_state == {
        "from_document_id": doc.pk,
        "kind": "confirm",
        "proposed_object_id": objekt.pk,
    }
    assert ereignis.object_id == objekt.pk and ereignis.user_id == admin_user.pk
    # ein erledigter Fall nimmt weder eine zweite Zuordnung noch eine Ablehnung an (kein Serverfehler, keine
    # zweite Uebernahme, kein Gegenbeispiel, Fall bleibt erledigt)
    for aktion, daten in (
        ("assign_object", {"target_object": str(anderes_objekt.pk)}),
        ("reject_assignment", {"reason": "zu spaet"}),
    ):
        resp = _sync_action(client, fall, action=aktion, **daten)
        assert resp.status_code == 302 and resp["Location"].startswith(f"/review/{fall.pk}/")
        assert "bereits erledigt" in client.get(reverse("review_detail", args=[fall.pk])).content.decode()
    fall.refresh_from_db()
    assert fall.status == CaseStatus.RESOLVED and fall.resolution["object_id"] == objekt.pk
    assert AssignmentExample.objects.count() == 1 and Document.objects.filter(uuid=uuid_vorher).count() == 1
    neu.refresh_from_db()
    assert neu.object_id == objekt.pk and neu.status != "moved_out"
    assert not AuditEvent.objects.filter(action="inbox.reject").exists()
    # Verknuepfung wandert mit; der neue Objektbezug wird nach Paperless vorgemerkt
    link.refresh_from_db()
    assert link.document_id == neu.pk
    meta = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_PUSH_META, document=neu)
    assert meta.status == OperationStatus.PENDING and meta.payload == {"reason": "object"}
    # Ablage im Zielobjekt: die Datei wird aus dem Eingangsordner verschoben, nicht kopiert
    ab = len(drive.ops)
    run_all(objekt)
    neu.refresh_from_db()
    assert neu.drive_file_id == datei_id and neu.drive_node_id is not None and neu.filed_at is not None
    knoten = drive.get(datei_id)
    assert knoten.parent_id == neu.drive_node.drive_file_id and knoten.parent_id != folder_id
    assert _dateien(drive, folder_id) == []
    schreibend = _schreibzugriffe(drive, ab)
    assert [o[0] for o in schreibend] == ["move"] and schreibend[0][1][:2] == (datei_id, folder_id)
    assert AuditEvent.objects.filter(action="drive.move", entity_id=neu.pk).exists()
    # Paperless erhaelt den neuen Objektbezug
    ops()
    meta.refresh_from_db()
    assert meta.status == OperationStatus.DONE
    werte = _feldwerte(paperless, int(link.external_id))
    assert werte["MHV Objekt"].startswith("623") and "Musterstadt" in werte["MHV Objekt"]
    assert werte["MHV Dokument-UUID"] == str(uuid_vorher)


def test_zuordnung_zu_anderem_objekt_ist_korrektur(
    objekt, anderes_objekt, paperless, eingang, run_all, ops, client_as, clerk_user
):
    store.set("sync.assignment_auto_min", 0.99, user=clerk_user, reason="Test")
    doc = _hochladen(eingang, LINES_BEZUG_623, "Wartung_Heizung.pdf")
    run_all(eingang)
    fall = _offener_fall(doc)
    assert fall.proposed_action["object_id"] == objekt.pk
    push = SyncOperation.objects.get(kind=OperationKind.PAPERLESS_PUSH, document=doc)
    assert push.status == OperationStatus.PENDING
    uuid_vorher = doc.uuid
    client = client_as(clerk_user)
    resp = _sync_action(
        client, fall, action="assign_object", target_object=str(anderes_objekt.pk), reason="gehört zu 624"
    )
    assert resp.status_code == 302
    neu = Document.objects.get(uuid=uuid_vorher)
    assert neu.object_id == anderes_objekt.pk and neu.source == "moved_in"
    beispiel = AssignmentExample.objects.get()
    assert beispiel.kind == "correct" and beispiel.source == "human"
    assert beispiel.target_object_id == anderes_objekt.pk and beispiel.proposed_object_id == objekt.pk
    assert beispiel.previous_object_id is None and beispiel.decided_by == clerk_user
    fall.refresh_from_db()
    assert fall.status == CaseStatus.RESOLVED and fall.resolution["object_id"] == anderes_objekt.pk
    ereignis = AuditEvent.objects.get(action="inbox.assign", entity_id=neu.pk)
    assert ereignis.after_state["kind"] == "correct" and ereignis.object_id == anderes_objekt.pk
    audit_transfer = AuditEvent.objects.filter(action="review.transfer_object", entity_id=neu.pk).get()
    assert audit_transfer.reason == "gehört zu 624"
    # die wartende Uebertragung folgt der Nachfolgezeile; 624 liegt ausserhalb des Pilotumfangs: die Uebertragung
    # entfaellt wie bei einem direkten Upload dorthin (verworfen, nicht blockiert), kein Schreiben nach Paperless
    push.refresh_from_db()
    assert push.document_id == neu.pk
    assert push.status == OperationStatus.CANCELLED and "Pilotumfang" in push.blocked_reason
    ops()
    assert "post_document" not in paperless.call_names()
    assert not SyncOperation.objects.filter(
        status__in=[OperationStatus.BLOCKED, OperationStatus.FAILED]
    ).exists()


def test_ablehnung_laesst_dokument_im_eingang(
    objekt, anderes_objekt, paperless, eingang, run_all, client_as, clerk_user
):
    store.set("sync.assignment_auto_min", 0.99, user=clerk_user, reason="Test")
    doc = _hochladen(eingang, LINES_BEZUG_623, "Wartung_Heizung.pdf")
    run_all(eingang)
    fall = _offener_fall(doc)
    client = client_as(clerk_user)
    resp = _sync_action(client, fall, action="reject_assignment", reason="falsches Objekt")
    assert resp.status_code == 302 and resp["Location"].startswith(f"/review/{fall.pk}/")
    fall.refresh_from_db()
    assert fall.status == CaseStatus.DISMISSED and fall.resolved_by == clerk_user
    assert fall.resolution == {"action": "reject", "reason": "falsches Objekt"}
    beispiel = AssignmentExample.objects.get()
    assert beispiel.kind == "reject" and beispiel.source == "human"
    assert beispiel.document_id == doc.pk and beispiel.proposed_object_id == objekt.pk
    assert beispiel.target_object_id is None and beispiel.review_case_id == fall.pk
    assert "numbers" in beispiel.features  # Merkmale aus dem Fall, damit Gegenbeispiele zaehlen
    doc.refresh_from_db()
    assert doc.object_id == eingang.pk and doc.status != "moved_out"
    assert Document.objects.filter(uuid=doc.uuid).count() == 1
    assert not Document.objects.filter(object__in=[objekt, anderes_objekt]).exists()
    ereignis = AuditEvent.objects.get(action="inbox.reject", entity_id=fall.pk)
    assert ereignis.entity_type == "review_case" and ereignis.user_id == clerk_user.pk
    assert ereignis.after_state == {"proposed_object_id": objekt.pk, "reason": "falsches Objekt"}
    assert not ReviewCase.objects.filter(document=doc, status=CaseStatus.OPEN).exists()
    assert derive_rules() == []  # ein Gegenbeispiel ohne Lieferant ergibt keine Regel
    # ein verworfener Fall nimmt keine Zuordnung mehr an (Doppelklick, veraltete Seite): Hinweis statt Uebernahme
    resp = _sync_action(client, fall, action="assign_object", target_object=str(objekt.pk))
    assert resp.status_code == 302 and resp["Location"].startswith(f"/review/{fall.pk}/")
    assert "bereits erledigt" in client.get(reverse("review_detail", args=[fall.pk])).content.decode()
    fall.refresh_from_db()
    assert fall.status == CaseStatus.DISMISSED and fall.resolution == {
        "action": "reject",
        "reason": "falsches Objekt",
    }
    assert AssignmentExample.objects.count() == 1
    assert (
        Document.objects.filter(uuid=doc.uuid).count() == 1
        and not AuditEvent.objects.filter(action="inbox.assign").exists()
    )
    # Wiedereroeffnen ist der Weg: danach ist die Zuordnung moeglich
    resp = _sync_action(client, fall, action="reopen", reason="doch zuordnen")
    assert resp.status_code == 302
    fall.refresh_from_db()
    assert fall.status == CaseStatus.OPEN
    resp = _sync_action(client, fall, action="assign_object", target_object=str(objekt.pk))
    assert resp.status_code == 302
    fall.refresh_from_db()
    neu = Document.objects.get(uuid=doc.uuid)
    assert neu.object_id == objekt.pk and fall.status == CaseStatus.RESOLVED
    assert fall.resolution["new_document_id"] == neu.pk
    assert sorted(AssignmentExample.objects.values_list("kind", flat=True)) == ["confirm", "reject"]


# ---------------------------------------------------------------- Lernen
def test_regel_entsteht_erst_aus_zwei_bestaetigungen(
    objekt, anderes_objekt, paperless, eingang, run_all, client_as, admin_user
):
    store.set("sync.assignment_auto_min", 0.99, user=admin_user, reason="Test")
    client = client_as(admin_user)
    beispiele = []
    for i, nr in enumerate(("2026-0901", "2026-0902"), start=1):
        doc = _hochladen(eingang, lines_stadtwerke(nr), f"Abschlag_{nr}.pdf")
        _korrespondent(doc, f"900{i}")
        run_all(eingang)
        fall = _offener_fall(doc)
        merkmale = fall.context["features"]
        assert merkmale["supplier_norm"] == "stadtwerke beispiel gmbh"
        assert [(n["kind"], n["value"]) for n in merkmale["numbers"]] == [("customer", "998877")]
        assert fall.proposed_action["object_id"] == objekt.pk
        resp = _sync_action(client, fall, action="assign_object", target_object=str(objekt.pk))
        assert resp.status_code == 302
        beispiele.append(AssignmentExample.objects.get(review_case=fall))
        if i == 1:
            # eine einzige Bestaetigung ergibt keine Regel
            assert derive_rules() == [] and AssignmentRule.objects.count() == 0
    assert beispiele[0].document_id != beispiele[1].document_id
    assert all(b.kind == "confirm" and b.source == "human" for b in beispiele)
    regeln = derive_rules(user=admin_user)
    assert len(regeln) == 1
    regel = regeln[0]
    assert regel.kind == "feature_combo" and regel.version == 1 and regel.is_active
    assert regel.object_id == objekt.pk and regel.created_by == admin_user
    assert regel.conditions == {
        "supplier_norm": "stadtwerke beispiel gmbh",
        "number_kind": "customer",
        "number": "998877",
    }
    assert regel.created_from["example_ids"] == sorted(b.pk for b in beispiele)
    assert derive_rules() == [] and AssignmentRule.objects.count() == 1
    assert active_rules() == [regel]
    # die Regel wirkt: Korrespondent und Kundennummer ohne Adresse fuehren zur automatischen Zuordnung
    store.set("sync.assignment_auto_min", 0.85, user=admin_user, reason="Test")
    doc3 = _hochladen(eingang, LINES_NUR_KUNDENNUMMER, "Abschlag_ohne_Adresse.pdf")
    _korrespondent(doc3, "9003")
    uuid3 = doc3.uuid
    run_all(eingang)
    assert _zuordnungsjob(eingang, doc3).result["decision"] == "auto"
    neu3 = Document.objects.get(uuid=uuid3)
    assert neu3.object_id == objekt.pk
    auto = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=neu3)
    assert auto.case_subtype == "auto"
    assert auto.candidates[0]["rule_hits"][0]["code"] == regel.code
    assert any(e["kind"] == "rule" for e in auto.candidates[0]["evidence"])
    assert AssignmentExample.objects.count() == 2  # automatische Zuordnung ist kein Lernbeispiel
    # abgeschaltete Regel wirkt nicht mehr
    deactivate_rule(regel, admin_user, "Testabschaltung")
    assert active_rules() == []
    vorschlag, kandidaten = assign.build_proposal(neu3, text="\n".join(LINES_NUR_KUNDENNUMMER))
    assert kandidaten == [] and vorschlag.decision == "none"
    # ohne Korrespondent trifft die Regel auch aktiv nicht
    regel.refresh_from_db()
    regel.is_active = True
    regel.save(update_fields=["is_active"])
    ohne = _hochladen(eingang, LINES_NUR_KUNDENNUMMER, "Abschlag_ohne_Korrespondent.pdf")
    run_all(eingang)
    assert _offener_fall(ohne).case_subtype == "no_candidate"


# ---------------------------------------------------------------- Oberflaeche Dokumenteneingang
def test_eingangsseite_zeigt_faelle_filter_und_rechte(
    objekt, anderes_objekt, paperless, eingang, run_all, client_as, clerk_user, admin_user
):
    store.set("sync.assignment_auto_min", 0.99, user=admin_user, reason="Test")
    doc = _hochladen(eingang, LINES_BEZUG_623, "Wartung_Heizung.pdf")
    run_all(eingang)
    fall = _offener_fall(doc)
    konflikt = _konfliktfall(objekt, anderes_objekt)
    op, _ = operations.enqueue(
        OperationKind.PAPERLESS_PUSH_META,
        system=SyncSystem.PAPERLESS,
        key="test:fehlgeschlagen",
        document=konflikt.document,
    )
    SyncOperation.objects.filter(pk=op.pk).update(
        status=OperationStatus.FAILED, last_error="Testfehler: Paperless HTTP 503"
    )
    url = reverse("inbox_list")
    client = client_as(clerk_user)
    resp = client.get(url)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Wartung_Heizung.pdf" in html and "Objektzuordnung" in html and "Abgleichskonflikt" in html
    assert reverse("review_detail", args=[fall.pk]) in html
    assert resp.context["counters"] == {"zuordnung": 1, "konflikt": 1, "fehler": 1, "verarbeitung": 1}
    assert {c.pk for c in resp.context["rows"]} == {fall.pk, konflikt.pk}
    verarbeitung = resp.context["processing"]
    assert [d.pk for d, _ in verarbeitung] == [doc.pk]
    stand = verarbeitung[0][1]
    assert stand["zugeordnet"] is False and stand["drive"] is True and stand["text"] is True
    assert stand["paperless"] is False and stand["vollstaendig"] is False
    assert resp.context["can_manage"] is False and 'value="retry"' not in html
    # Filter nach Art
    resp = client.get(url, {"art": "zuordnung"})
    assert resp.status_code == 200 and [c.pk for c in resp.context["rows"]] == [fall.pk]
    assert resp.context["failed_ops"] == []
    resp = client.get(url, {"art": "konflikt"})
    assert resp.status_code == 200 and [c.pk for c in resp.context["rows"]] == [konflikt.pk]
    assert "object_changed_remote" in resp.content.decode()
    resp = client.get(url, {"art": "fehler"})
    assert resp.status_code == 200 and [o.pk for o in resp.context["failed_ops"]] == [op.pk]
    assert "Testfehler: Paperless HTTP 503" in resp.content.decode()
    resp = client.get(url, {"art": "konflikt", "objekt": objekt.object_number})
    assert [c.pk for c in resp.context["rows"]] == [konflikt.pk]
    resp = client.get(url, {"art": "zuordnung", "objekt": objekt.object_number})
    assert resp.context["rows"] == []  # der Zuordnungsfall haengt am Eingangsobjekt
    # Formatproblem im Eingang erscheint unter "format" und "alle Arten", nicht unter Zuordnung oder Dublette
    format_fall = common.open_case(
        eingang, case_type=CaseType.UNCLEAR, subtype="unsupported_format", key="test:format", document=doc
    )
    assert format_fall is not None
    assert [c.pk for c in client.get(url, {"art": "format"}).context["rows"]] == [format_fall.pk]
    assert client.get(url, {"art": "dublette"}).context["rows"] == []
    assert [c.pk for c in client.get(url, {"art": "zuordnung"}).context["rows"]] == [fall.pk]
    resp = client.get(url)
    assert {c.pk for c in resp.context["rows"]} == {fall.pk, konflikt.pk, format_fall.pk}
    assert resp.context["counters"]["zuordnung"] == 1  # Formatfaelle zaehlen nicht als Zuordnung
    # Admin sieht die Wiederholung fuer Synchronisationsfehler
    resp = client_as(admin_user).get(url, {"art": "fehler"})
    assert resp.context["can_manage"] is True and 'value="retry"' in resp.content.decode()
    # Objektsuche liefert das Objekt, nie das Eingangsobjekt
    suche = reverse("inbox_objects_json")
    resp = client.get(suche, {"q": "623"})
    treffer = resp.json()["results"]
    assert [t["id"] for t in treffer] == [objekt.pk] and treffer[0]["label"].startswith("623")
    assert resp.json()["results"][0]["management_type"] == "weg"
    assert client.get(suche, {"q": "Eingang"}).json()["results"] == []
    assert client.get(suche, {"q": "zugeordnet"}).json()["results"] == []
    assert {t["id"] for t in client.get(suche, {"q": "Muster"}).json()["results"]} == {objekt.pk}
    assert client.get(suche, {"q": "6"}).json()["results"] == []
    # ohne Recht inbox.work: 403 mit Audit; ohne Anmeldung: Weiterleitung zur Anmeldung
    from apps.accounts.models import Role, User

    rolle = Role.objects.create(code="gast", name="Gast", permissions=["status.read"], is_system=False)
    gast = User.objects.create_user("gast@example.test", "Startpasswort-12x", role=rolle, display_name="Gast")
    assert client_as(gast).get(url).status_code == 403
    verweigert = AuditEvent.objects.filter(action="auth.denied", user_id=gast.pk).order_by("-id").first()
    assert verweigert is not None and verweigert.after_state["permission"] == "inbox.work"
    anonym = Client().get(url)
    assert anonym.status_code == 302 and anonym["Location"].startswith("/konto/login/")
    assert Client().get(suche, {"q": "623"}).status_code == 302


def test_review_detailseiten_fuer_zuordnung_und_konflikt(
    objekt, anderes_objekt, paperless, eingang, run_all, client_as, admin_user, clerk_user
):
    store.set("sync.assignment_auto_min", 0.99, user=admin_user, reason="Test")
    doc = _hochladen(eingang, LINES_BEZUG_623, "Wartung_Heizung.pdf")
    run_all(eingang)
    fall = _offener_fall(doc)
    client = client_as(admin_user)
    resp = client.get(reverse("review_detail", args=[fall.pk]))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert resp.context["candidates"] == fall.candidates and fall.candidates[0]["object_number"] == "623"
    assert "<td>623</td>" in html and 'value="assign_object"' in html and "Zuordnen" in html
    assert f'name="target_object" value="{objekt.pk}"' in html
    assert 'value="reject_assignment"' in html and "bleibt im Eingang" in html
    assert "Wartung_Heizung.pdf" in html
    assert resp.context["conflict_choices"] == []
    # Konfliktfall: Auswahlmoeglichkeiten aus choices_for
    konflikt = _konfliktfall(objekt, anderes_objekt)
    erwartet = conflicts.choices_for(konflikt)
    assert [k for k, _ in erwartet] == ["apply_remote_object", "push_local_object"]
    resp = client.get(reverse("review_detail", args=[konflikt.pk]))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert resp.context["conflict_choices"] == erwartet
    for code, name in erwartet:
        assert f'value="{code}"' in html and name in html
    assert 'value="resolve_conflict"' in html and "remote_object_id" in html
    assert 'value="assign_object"' not in html
    # Sachbearbeiter (review.decide) sieht dieselben Entscheidungsformulare
    resp = client_as(clerk_user).get(reverse("review_detail", args=[konflikt.pk]))
    assert resp.status_code == 200 and 'value="resolve_conflict"' in resp.content.decode()
    # unzulaessige Entscheidung wird abgewiesen, zulaessige loest den Konflikt
    client = client_as(admin_user)
    resp = _sync_action(client, konflikt, action="resolve_conflict", choice="keep_local", reason="x")
    assert resp.status_code == 302
    konflikt.refresh_from_db()
    assert konflikt.status == CaseStatus.OPEN
    resp = _sync_action(
        client, konflikt, action="resolve_conflict", choice="push_local_object", reason="lokal richtig"
    )
    assert resp.status_code == 302
    konflikt.refresh_from_db()
    assert konflikt.status == CaseStatus.RESOLVED and konflikt.resolved_by == admin_user
    assert (
        konflikt.resolution["choice"] == "push_local_object"
        and konflikt.resolution["reason"] == "lokal richtig"
    )
    assert SyncOperation.objects.filter(
        kind=OperationKind.PAPERLESS_PUSH_META, document=konflikt.document, status=OperationStatus.PENDING
    ).exists()
    assert AuditEvent.objects.filter(action="sync.conflict_resolved", entity_id=konflikt.pk).exists()


def test_verwaltungsseite_mit_pruefung_und_rechten(
    objekt, paperless, eingang, client_as, admin_user, clerk_user
):
    client = client_as(admin_user)
    resp = client.get(reverse("sync_admin"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert resp.context["mode"] == "pilot" and resp.context["pilot"] == ["623"]
    assert resp.context["state"].ok and "in Ordnung" in html and "623" in html and "pilot" in html
    assert resp.context["inbox_obj"].pk == eingang.pk
    assert resp.context["inbox_folder_id"] == store.get("sync.inbox_folder_id")
    assert "MHV-Sync" in html and "MHV Dokument-UUID" in html
    # Verbindung pruefen und Kennzeichnung einrichten: Befund wird aktualisiert
    vorher = services.connection_meta()["checked_at"]
    paperless.reset_calls()
    resp = client.post(reverse("sync_check"), {"einrichten": "1"})
    assert resp.status_code == 302 and resp["Location"] == reverse("sync_admin")
    meta = services.connection_meta()
    assert meta["ok"] is True and meta["checked_at"] != vorher and meta["missing"] == []
    assert {"server_info", "find_tag", "find_custom_field"} <= set(paperless.call_names())
    assert "create_tag" not in paperless.call_names() and "create_custom_field" not in paperless.call_names()
    assert AuditEvent.objects.filter(action="sync.paperless_check", user_id=admin_user.pk).exists()
    # fehlendes Feld: Pruefung meldet es, Einrichten legt es an
    feld_id = next(f["id"] for f in paperless.custom_fields.values() if f["name"] == "MHV Drive-Link")
    del paperless.custom_fields[feld_id]
    resp = client.post(reverse("sync_check"), {})
    assert resp.status_code == 302
    meta = services.connection_meta()
    assert meta["ok"] is False and meta["missing"] == ["MHV Drive-Link"]
    assert client.get(reverse("sync_admin")).context["state"].ok is False
    resp = client.post(reverse("sync_check"), {"einrichten": "1"})
    assert resp.status_code == 302
    meta = services.connection_meta()
    assert meta["ok"] is True and meta["missing"] == []
    assert any(f["name"] == "MHV Drive-Link" for f in paperless.custom_fields.values())
    assert AuditEvent.objects.filter(action="sync.paperless_setup").exists()
    # Operationen einreihen
    resp = client.post(reverse("sync_run_now"), {"was": "operationen"})
    assert resp.status_code == 302 and resp["Location"] == reverse("sync_admin")
    assert client.post(reverse("sync_run_now"), {"was": "unbekannt"}).status_code == 400
    # Sachbearbeiter ohne sync.manage
    clerk = client_as(clerk_user)
    assert clerk.get(reverse("sync_admin")).status_code == 403
    assert clerk.post(reverse("sync_check"), {"einrichten": "1"}).status_code == 403
    assert clerk.post(reverse("sync_run_now"), {"was": "operationen"}).status_code == 403
    verweigert = AuditEvent.objects.filter(action="auth.denied", user_id=clerk_user.pk)
    assert verweigert.count() == 3 and all(e.after_state["permission"] == "sync.manage" for e in verweigert)


def test_eingangsdokument_ohne_eindeutige_kategorie_erreicht_die_zuordnung(
    objekt, anderes_objekt, paperless, eingang, run_all
):
    """Der Standardtext (Wartungsrechnung, Stufe 1 Kategorie 06 ohne sichere Art) darf im Eingang nicht an der
    Ablageregel fuer 06 scheitern: im Eingang gibt es keinen Ablageort, die Zuordnung folgt als eigener Job."""
    doc = _hochladen(eingang, LINES_623, "Wartung_Heizung.pdf")
    uuid_vorher = doc.uuid
    run_all(eingang)
    assert not ProcessingJob.objects.filter(object=eingang, status=JobStatus.FAILED).exists()
    doc.refresh_from_db()
    assert doc.status != "error"
    job = _zuordnungsjob(eingang, doc)
    assert job.status == JobStatus.DONE and job.result["decision"] in ("auto", "review", "none")
    if job.result["decision"] == "auto":
        assert Document.objects.filter(object=objekt, uuid=uuid_vorher).exists()
    else:
        fall = _offener_fall(doc)
        assert fall.candidates[0]["object_id"] == objekt.pk
    # keine Ablage und keine Faelle der Klassifikation im Eingang
    assert (
        not ReviewCase.objects.filter(object=eingang).exclude(case_type=CaseType.OBJECT_ASSIGNMENT).exists()
    )
