"""Gegenprobe der Objektzuordnung fuer Feldimporte aus Paperless (apps.sync.flows.crosscheck): eindeutige Dokumente
bleiben und werden markiert; ein zweites Objekt im Text geht an die KI (anderes Objekt -> Uebernahme dorthin);
nur beilaeufiger Bezug (Fahrtziel) mit KI-Urteil „kein Objektdokument“ -> Eingang ohne zweiten KI-Aufruf; ohne KI
entscheidet die lokale Regel (belassen) oder der Eingang; Kommando paperless_zuordnung_pruefen (Vorschau, echt)."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest
from django.core.management import call_command
from tests.integration.sync.conftest import pdf_bytes

from apps.ai.models import AiCall
from apps.audit.models import AuditEvent
from apps.documents.models import Document
from apps.pipeline.models import JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import services
from apps.sync.flows import crosscheck, paperless_pull
from apps.sync.models import ExternalLink, LinkRole, SyncSystem

pytestmark = pytest.mark.django_db

ERFUNDEN = "Alle Angaben in diesem Testdokument sind erfunden; keine realen Personen oder Konten."
ENERGIE = "Hinweise zum Energieausweis nach dem Gebäudeenergiegesetz"
NUR_623 = [
    "Energieausweis für das Wohngebäude Musterstraße 49, 12345 Musterstadt",
    "Ausstellungsdatum 03.09.2026, gültig bis 02.09.2036",
    ERFUNDEN,
]
ZWEI = [
    "Energieausweis für zwei Wohngebäude",
    "Objekt Musterstraße 49, 12345 Musterstadt",
    "Objekt Beispielweg 7, 54321 Beispielhausen",
    ERFUNDEN,
]
FAHRT = [
    ENERGIE,
    "Fahrtkostenabrechnung September 2026 eines Mitarbeiters",
    "Fahrtziel: Musterstraße 49, 12345 Musterstadt",
    "Kilometerpauschale 0,30 EUR je Kilometer",
    ERFUNDEN,
]
MIT_RECHNUNGSANSCHRIFT = [
    "Energieausweis für das Wohngebäude Musterstraße 49, 12345 Musterstadt",
    "Rechnungsanschrift: Beispielweg 7, 54321 Beispielhausen",
    ERFUNDEN,
]


def _import(paperless, ops, objekt, lines, title) -> Document:
    fid = int(services.connection_meta()["field_ids"]["object"])
    pid = paperless.add_document(
        title, pdf_bytes(lines), custom_fields={fid: objekt.object_number}, original_file_name=f"{title}.pdf"
    )
    paperless_pull.poll(force=True)
    ops()
    link = ExternalLink.objects.get(system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL, external_id=str(pid))
    doc = link.document
    assert doc.object_id == objekt.pk and doc.source == "paperless"
    return doc


def _gegenprobe(doc) -> AuditEvent:
    return AuditEvent.objects.get(action="sync.field_crosscheck", entity_id=doc.pk)


def test_eindeutiges_dokument_bleibt_und_wird_markiert(
    objekt, anderes_objekt, eingang, paperless, run_all, ops, ki
):
    ki({"is_object_document": True, "object_number": "623", "confidence": 0.9, "reasoning": "x"})
    doc = _import(paperless, ops, objekt, NUR_623, "Energieausweis 623")
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.object_id == objekt.pk and doc.status != "moved_out" and doc.assignment_checked_at is not None
    ereignis = _gegenprobe(doc)
    assert ereignis.after_state["kind"] == "eindeutig" and ereignis.after_state["action"] == "ok"
    assert not AiCall.objects.exists()


def test_zweiter_objektbezug_ki_haengt_in_das_andere_objekt_um(
    objekt, anderes_objekt, eingang, paperless, run_all, ops, ki
):
    p = ki(
        {
            "is_object_document": True,
            "object_number": "624",
            "multiple_objects": False,
            "other_addresses_role": "neighbor",
            "confidence": 0.93,
            "reasoning": "Der Energieausweis betrifft das zweite Gebäude, das erste ist nur Verweis.",
        }
    )
    doc = _import(paperless, ops, objekt, ZWEI, "Energieausweis zwei Gebaeude")
    uuid = doc.uuid
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "moved_out" and doc.assignment_checked_at is not None
    neu = Document.objects.get(uuid=uuid)
    assert (
        neu.object_id == anderes_objekt.pk
        and neu.source == "moved_in"
        and neu.assignment_checked_at is not None
    )
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=neu)
    assert fall.case_subtype == "ai_auto" and fall.status == CaseStatus.RESOLVED
    assert fall.context["crosscheck"] == {"kind": "zweiter_bezug", "from_object": "623"}
    ereignis = _gegenprobe(doc)
    assert ereignis.after_state["action"] == "umgehaengt" and ereignis.after_state["target"] == "624"
    assert ereignis.after_state["others"] == ["624"] and ereignis.after_state["local_unique"] is False
    assert AiCall.objects.filter(purpose="assign_object").count() == 1
    # das Feldobjekt steht als erster Kandidat mit dem Beleg des Paperless-Feldes im Request
    gesendet = p.sent[-1]["user"]
    assert gesendet.index('"object_number": "623"') < gesendet.index('"object_number": "624"')
    assert "paperless_field" in gesendet
    # die Ablage im alten Objekt entfaellt
    assert not ProcessingJob.objects.filter(job_type=JobType.FILE_TO_DRIVE, document=doc).exists()


def test_fahrtziel_ist_kein_objektdokument_und_wird_im_eingang_nicht_erneut_angefragt(
    objekt, eingang, paperless, run_all, ops, ki
):
    ki(
        {
            "is_object_document": False,
            "object_number": None,
            "multiple_objects": False,
            "other_addresses_role": "travel_destination",
            "confidence": 0.9,
            "reasoning": "Fahrtkostenabrechnung eines Mitarbeiters, die Anschrift ist Fahrtziel.",
        }
    )
    doc = _import(paperless, ops, objekt, FAHRT, "Fahrtkosten September")
    uuid = doc.uuid
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "moved_out"
    neu = Document.objects.get(uuid=uuid)
    assert neu.object_id == eingang.pk
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=neu, status=CaseStatus.OPEN)
    assert fall.case_subtype == "ai_not_object" and fall.context["ai"]["is_object_document"] is False
    assert (
        fall.context["crosscheck"]["kind"] == "schwacher_bezug"
        and fall.context["crosscheck"]["from_object"] == "623"
    )
    assert _gegenprobe(doc).after_state["action"] == "eingang"
    assert AiCall.objects.filter(purpose="assign_object").count() == 1
    # Eingang: die gespeicherte Einschaetzung wird wiederverwendet, kein zweiter Aufruf, der Fall bleibt
    run_all(eingang)
    job = ProcessingJob.objects.get(job_type=JobType.ASSIGN_OBJECT, object=eingang, document=neu)
    assert job.result["decision"] == "ai_not_object" and job.result["case_id"] == fall.pk
    assert AiCall.objects.filter(purpose="assign_object").count() == 1
    neu.refresh_from_db()
    assert neu.object_id == eingang.pk


def test_ohne_ki_entscheidet_die_lokale_regel(objekt, anderes_objekt, eingang, paperless, run_all, ops):
    bleibt = _import(paperless, ops, objekt, MIT_RECHNUNGSANSCHRIFT, "Energieausweis mit Rechnungsanschrift")
    offen = _import(paperless, ops, objekt, ZWEI, "Energieausweis zwei Gebaeude")
    uuid = offen.uuid
    run_all(objekt)
    bleibt.refresh_from_db()
    assert bleibt.object_id == objekt.pk and bleibt.status != "moved_out"
    ereignis = _gegenprobe(bleibt)
    assert ereignis.after_state["kind"] == "zweiter_bezug" and ereignis.after_state["action"] == "belassen"
    assert ereignis.after_state["local_unique"] is True and ereignis.after_state["ai"]["status"] == "disabled"
    offen.refresh_from_db()
    assert offen.status == "moved_out"
    neu = Document.objects.get(uuid=uuid)
    assert neu.object_id == eingang.pk
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=neu, status=CaseStatus.OPEN)
    assert fall.case_subtype == "proposal" and fall.context["ai"]["status"] == "disabled"
    assert fall.context["crosscheck"]["others"] == ["624"] and fall.proposed_action["object_id"] == objekt.pk
    assert not AiCall.objects.exists()


def test_bestandslauf_vorschau_und_echt(
    objekt, anderes_objekt, eingang, paperless, run_all, ops, ki, monkeypatch
):
    # Bestand: live greift die Gegenprobe erst fuer Dokumente ab LIVE_SINCE; aeltere prueft das Kommando
    monkeypatch.setattr(crosscheck, "LIVE_SINCE", datetime(2099, 1, 1, tzinfo=UTC))
    klar = _import(paperless, ops, objekt, NUR_623, "Energieausweis 623")
    zwei = _import(paperless, ops, objekt, ZWEI, "Energieausweis zwei Gebaeude")
    run_all(objekt)
    for d in (klar, zwei):
        d.refresh_from_db()
        assert d.assignment_checked_at is None and d.status != "moved_out"
    out = StringIO()
    call_command("paperless_zuordnung_pruefen", "--details", stdout=out)
    text = out.getvalue()
    assert "Objekt 623: eindeutig 1, geprüft 2, lokal_offen 1, zweiter_bezug 1" in text
    assert (
        f"zweiter_bezug   Dok {zwei.pk}: Energieausweis zwei Gebaeude.pdf | andere: 624 | lokal offen" in text
    )
    assert "Vorschau" in text
    assert not AiCall.objects.exists()
    ki(
        {
            "is_object_document": True,
            "object_number": "623",
            "multiple_objects": False,
            "other_addresses_role": "neighbor",
            "confidence": 0.95,
            "reasoning": "Das Dokument betrifft das Feldobjekt.",
        }
    )
    out = StringIO()
    call_command("paperless_zuordnung_pruefen", "--echt", "--objekt", "623", stdout=out)
    text = out.getvalue()
    assert "aktion_bestaetigt 1" in text and "ki_ok 1" in text and "Vorschau" not in text
    for d in (klar, zwei):
        d.refresh_from_db()
        assert d.assignment_checked_at is not None and d.object_id == objekt.pk
    assert AiCall.objects.filter(purpose="assign_object").count() == 1
    assert _gegenprobe(zwei).after_state["action"] == "bestaetigt"
    # zweiter Lauf: nichts mehr offen
    out = StringIO()
    call_command("paperless_zuordnung_pruefen", "--echt", stdout=out)
    assert "Gesamt: keine Dokumente" in out.getvalue()
