"""KI-Schiedsrichter der Objektzuordnung (Entscheidung 22.09.2026): greift, sobald die Zuordnung eines
Eingangsdokuments nicht eindeutig ist. Sicherer Kandidat -> Uebernahme (ai_auto), kein Objektdokument
(Fahrtkostenabrechnung mit der Objektanschrift als Fahrtziel) -> Fall ai_not_object im Eingang, niedrige Konfidenz
-> Vorschlag, manuell verworfenes Objekt schlaegt die KI, ohne Anbieter wie bisher, Monatsdeckel blockiert."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from tests.integration.sync.conftest import pdf_bytes

from apps.ai import assignment as arbiter
from apps.ai.fakes import FakeClassificationProvider
from apps.ai.models import AiCall
from apps.ai.provider import PriceList, ProviderConfig
from apps.ai.router import Router
from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document
from apps.pipeline.models import JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync.assignment import learning
from apps.sync.models import ExampleKind

pytestmark = pytest.mark.django_db

ERFUNDEN = "Alle Angaben in diesem Testdokument sind erfunden; keine realen Personen oder Konten."
ENERGIE = "Hinweise zum Energieausweis nach dem Gebäudeenergiegesetz"
ZWEI_OBJEKTE = [
    "Energieausweis für zwei Wohngebäude",
    "Objekt Musterstraße 49, 12345 Musterstadt",
    "Objekt Beispielweg 7, 54321 Beispielhausen",
    ERFUNDEN,
]
FAHRTKOSTEN = [
    ENERGIE,
    "Fahrtkostenabrechnung September 2026 eines Mitarbeiters",
    "Fahrtziel: Musterstraße 49, 12345 Musterstadt",
    "Kilometerpauschale 0,30 EUR je Kilometer",
    ERFUNDEN,
]


def _antwort(**over) -> dict:
    base = {
        "is_object_document": True,
        "object_number": "623",
        "multiple_objects": False,
        "other_addresses_role": "billing_address",
        "confidence": 0.92,
        "reasoning": "Leistungsort ist das erste Objekt, die zweite Anschrift ist die Rechnungsanschrift.",
    }
    base.update(over)
    return base


@pytest.fixture
def ki(monkeypatch):
    """Fake-Anbieter als Router des Schiedsrichters; liefert den Provider zur Pruefung der gesendeten Requests."""

    def setze(answer: dict, script: list[str] | None = None) -> FakeClassificationProvider:
        p = FakeClassificationProvider("openai", answer=answer, script=script)
        router = Router(
            {"openai": p},
            configs={
                "openai": ProviderConfig(
                    name="openai", enabled=True, model="openai-testmodell", timeout_s=5, max_attempts=2
                )
            },
            price_list=PriceList("test-2026-09", {"openai-testmodell": (Decimal("0.001"), Decimal("0.004"))}),
            order=["openai"],
        )
        monkeypatch.setattr(arbiter, "default_router", lambda: router)
        return p

    return setze


def _job(eingang, doc) -> ProcessingJob:
    return ProcessingJob.objects.get(job_type=JobType.ASSIGN_OBJECT, object=eingang, document=doc)


def _eingang(eingang, lines, name="Schreiben.pdf") -> Document:
    doc, _ = ingest.ingest_upload(eingang, filename=name, data=pdf_bytes(lines))
    return doc


def test_ki_entscheidet_bei_zwei_objektbezuegen(
    objekt, anderes_objekt, eingang, paperless, drive, run_all, ki
):
    p = ki(_antwort())
    doc = _eingang(eingang, ZWEI_OBJEKTE, "Rechnung_Dach.pdf")
    uuid = doc.uuid
    run_all(eingang)
    job = _job(eingang, doc)
    assert job.result["decision"] == "ai_auto" and job.result["object_id"] == objekt.pk
    neu = Document.objects.get(uuid=uuid)
    assert neu.object_id == objekt.pk and neu.source == "moved_in"
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=neu)
    assert fall.case_subtype == "ai_auto" and fall.status == CaseStatus.RESOLVED
    assert fall.resolution["action"] == "assign_object_ai" and fall.context["ai"]["object_number"] == "623"
    assert "Rechnungsanschrift" in fall.context["ai"]["summary"]
    call = AiCall.objects.get(purpose="assign_object")
    assert call.status == "ok" and call.document_id == doc.pk and call.object_id == eingang.pk
    assert call.response_summary["object_number"] == "623" and call.cost_eur is not None
    assert AuditEvent.objects.filter(action="inbox.assign_ai", entity_id=neu.pk).exists()
    # Request: Kandidaten mit Nummer und Anschriften, Zweck im Systemprompt, kein Eigentuemername
    gesendet = p.sent[-1]
    assert (
        "kandidaten" in gesendet["user"]
        and "623" in gesendet["user"]
        and "Musterstraße 49" in gesendet["user"]
    )
    assert "verwalteten Objekt" in gesendet["system"]


def test_fahrtkosten_sind_kein_objektdokument(objekt, eingang, paperless, drive, run_all, ki):
    ki(
        _antwort(
            is_object_document=False,
            object_number=None,
            other_addresses_role="travel_destination",
            confidence=0.9,
            reasoning="Fahrtkostenabrechnung eines Mitarbeiters, die Anschrift ist nur das Fahrtziel.",
        )
    )
    doc = _eingang(eingang, FAHRTKOSTEN, "Fahrtkosten.pdf")
    run_all(eingang)
    assert _job(eingang, doc).result["decision"] == "ai_not_object"
    doc.refresh_from_db()
    assert doc.object_id == eingang.pk and doc.status != "moved_out"
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc, status=CaseStatus.OPEN)
    assert fall.case_subtype == "ai_not_object" and fall.proposed_action is None
    assert fall.context["ai"]["is_object_document"] is False
    # Rolle Fahrtziel: die Anschrift zaehlt lokal kaum, ohne KI waere es kein Automatismus
    assert fall.candidates[0]["object_id"] == objekt.pk and fall.candidates[0]["score"] < 0.85
    assert any(e["role"] == "travel" for e in fall.candidates[0]["evidence"] if e["kind"] == "address")


def test_niedrige_konfidenz_bleibt_vorschlag(
    objekt, anderes_objekt, eingang, paperless, drive, run_all, ki, admin_user, client_as
):
    ki(_antwort(confidence=0.6))
    doc = _eingang(eingang, ZWEI_OBJEKTE)
    run_all(eingang)
    job = _job(eingang, doc)
    assert job.result["decision"] == "review" and job.result["ai_status"] == "ok"
    doc.refresh_from_db()
    assert doc.object_id == eingang.pk
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc, status=CaseStatus.OPEN)
    assert fall.case_subtype == "proposal" and fall.proposed_action == {
        "action": "assign_object",
        "object_id": objekt.pk,
    }
    assert fall.context["ai"]["confidence"] == 0.6 and fall.context["ai"]["status"] == "ok"
    client = client_as(admin_user)
    seite = client.get(reverse("review_detail", args=[fall.pk])).content.decode()
    assert "KI-Einschätzung" in seite and "Vorschlag Objekt 623" in seite


def test_manuell_verworfenes_objekt_schlaegt_die_ki(
    objekt, anderes_objekt, eingang, paperless, drive, run_all, ki
):
    ki(_antwort())
    doc = _eingang(eingang, ZWEI_OBJEKTE)
    learning.record_example(doc, kind=ExampleKind.REJECT, proposed_object=objekt)
    run_all(eingang)
    assert _job(eingang, doc).result["decision"] == "review"
    doc.refresh_from_db()
    assert doc.object_id == eingang.pk
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc, status=CaseStatus.OPEN)
    assert fall.proposed_action["object_id"] == objekt.pk
    assert any("bereits manuell verworfen" in r for r in fall.context["reasons"])


def test_ohne_anbieter_wie_bisher(objekt, anderes_objekt, eingang, paperless, drive, run_all):
    doc = _eingang(eingang, ZWEI_OBJEKTE)
    run_all(eingang)
    job = _job(eingang, doc)
    assert job.result["decision"] == "review" and job.result["ai_status"] == "disabled"
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc, status=CaseStatus.OPEN)
    assert (
        fall.context["ai"]["status"] == "disabled" and "KI nicht verfügbar" in fall.context["ai"]["summary"]
    )
    assert not AiCall.objects.exists()


def test_monatsdeckel_blockiert_den_schiedsrichter(
    objekt, anderes_objekt, eingang, paperless, drive, run_all, ki, admin_user
):
    ki(_antwort())
    store.set("ai.monthly_budget_eur", {"openai": 0}, user=admin_user, reason="Test")
    doc = _eingang(eingang, ZWEI_OBJEKTE)
    run_all(eingang)
    assert _job(eingang, doc).result["ai_status"] == "budget_blocked"
    call = AiCall.objects.get(purpose="assign_object")
    assert call.status == "budget_blocked" and "Monatsdeckel" in call.error_message
    fall = ReviewCase.objects.get(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc, status=CaseStatus.OPEN)
    assert fall.context["ai"]["status"] == "budget_blocked"


def test_abgeschaltet_ueber_konfiguration(
    objekt, anderes_objekt, eingang, paperless, drive, run_all, ki, admin_user
):
    ki(_antwort())
    store.set("sync.assignment_ai_enabled", False, user=admin_user, reason="Test")
    doc = _eingang(eingang, ZWEI_OBJEKTE)
    run_all(eingang)
    assert _job(eingang, doc).result["ai_status"] == "disabled"
    assert not AiCall.objects.exists()
