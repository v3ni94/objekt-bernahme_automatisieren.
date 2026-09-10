"""Stufe 3 in der Kette: classify -> classify_ai -> decide mit Fake-Anbietern; Uebereinstimmung hebt die Konfidenz,
Widerspruch mit hoher Konfidenz uebernimmt die KI-Kategorie (decided_by stage3, Stichprobe), Ausfall beider Anbieter
ergibt 06/01_Unklar mit Fall und Grund KI nicht verfuegbar (B-28); Ausweiskopie und Kategorie 01 gehen nie an Stufe 3;
Nachklassifikationslauf; Statusseite zeigt Kosten."""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.urls import reverse
from tests.integration.classification.conftest import make_document

from apps.ai import services as ai_services
from apps.ai.fakes import FakeClassificationProvider
from apps.ai.models import AiCall
from apps.ai.provider import PriceList, ProviderConfig
from apps.ai.router import Router
from apps.config import store
from apps.documents.models import DocumentClassification
from apps.pipeline.models import JobType, ProcessingJob
from apps.pipeline.runs import start_run
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db
H623 = "Objekt 623 Düsseldorf, Joachimstraße 49\n"
UNKLAR = {
    "filename": "Schreiben_unklar.pdf",
    "pages": [
        H623
        + "Sehr geehrte Damen und Herren, anbei die Unterlagen wie besprochen zur Wohnung. Mit freundlichen Grüßen"
    ],
}


def enable_ai(admin_user):
    providers = store.get("ai.providers")
    for name in providers:
        providers[name]["enabled"] = True
        providers[name]["model"] = f"{name}-testmodell"
    store.set("ai.providers", providers, user=admin_user)


@pytest.fixture
def fake_router(monkeypatch):
    holder = {}

    def _make(primary_script=None, fallback_script=None, answer=None):
        p = FakeClassificationProvider("openai", script=primary_script or [], answer=answer)
        f = FakeClassificationProvider("anthropic", script=fallback_script or [], answer=answer)
        r = Router(
            {"openai": p, "anthropic": f},
            configs={
                "openai": ProviderConfig("openai", True, "openai-testmodell"),
                "anthropic": ProviderConfig("anthropic", True, "anthropic-testmodell"),
            },
            price_list=PriceList("t", {}),
            order=["openai", "anthropic"],
        )
        monkeypatch.setattr(ai_services, "default_router", lambda: r)
        holder["p"], holder["f"] = p, f
        return r

    return _make


def _answer(category, subfolder=None, dtype=None, confidence=0.95, object_related=True, year=None):
    return {
        "object_related": object_related,
        "category": category,
        "subfolder": subfolder,
        "document_type": dtype,
        "period": {"year": year, "from": None, "to": None, "document_date": None},
        "mentioned_units": [],
        "mentioned_parties": [],
        "confidence": confidence,
        "reasoning": "Testantwort",
    }


def test_ki_entscheidet_bei_unklarem_dokument(
    welt, fake_oauth, run_all, admin_user, fake_router, monkeypatch
):
    enable_ai(admin_user)
    fake_router(answer=_answer("02", None, "gebaeudeversicherung", 0.95))
    monkeypatch.setattr(ai_services, "sample_for_review", lambda: True)
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.CLASSIFY_AI, status="done").exists()
    assert doc.final_decided_by == "stage3" and doc.category_id == "02" and doc.status == "filed"
    s3 = DocumentClassification.objects.get(document=doc, stage=3, is_final=False)  # Anbieterzeile
    assert (
        s3.provider == "openai"
        and s3.category_id == "02"
        and s3.ai_call is not None
        and float(s3.confidence) == 0.95
    )
    final = DocumentClassification.objects.get(document=doc, is_final=True)
    assert final.category_id == "02" and final.document_type.code == "gebaeudeversicherung"
    assert final.provider == "openai" and "Stufe 3 entscheidet" in final.reasoning
    # Stichprobe (F17): Ablage erfolgt, der Fall haelt die Ablage nicht auf
    case = ReviewCase.objects.get(document=doc, case_subtype="ai_sample")
    assert (
        case.case_type == "move_proposal" and case.status == "open" and case.context["provider"] == "openai"
    )
    assert AiCall.objects.filter(document=doc, status="ok").count() == 1


def test_ausfall_beider_anbieter_unklar_mit_fall(welt, fake_oauth, run_all, admin_user, fake_router):
    enable_ai(admin_user)
    fake_router(["timeout", "5xx"], ["5xx", "timeout"])
    obj = welt["objects"]["623"]
    doc = make_document(obj, UNKLAR)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "01" and doc.status == "review"
    case = ReviewCase.objects.get(document=doc)
    assert case.case_type == "unclear" and case.context["stage3_status"] == "provider_error"
    assert "KI nicht verfügbar" in case.context["reason"]
    assert (
        AiCall.objects.filter(document=doc).count() == 4
        and AiCall.objects.filter(document=doc, fallback_used=True).count() == 2
    )
    # Nachklassifikationslauf reiht das Dokument erneut ein, solange kein Mensch entschieden hat
    store.set("ai.reclassify_enabled", True, user=admin_user)
    fake_router(answer=_answer("05", "08", "schriftverkehr", 0.95))
    call_command("ai_reclassify", object="623")
    run_all(obj)
    doc.refresh_from_db()
    assert doc.final_decided_by == "stage3" and doc.category_id == "05"


def test_uebereinstimmung_hebt_konfidenz_und_sperren(welt, fake_oauth, run_all, admin_user, fake_router):
    enable_ai(admin_user)
    r = fake_router(answer=_answer("05", "09", "mahnung", 0.8, year=2025))
    obj = welt["objects"]["623"]
    # t_ai_call ueber der Regelkonfidenz 0,90 (E 7.2: t_ai_call ist konfigurierbar): weiche Regel ohne NER-Stuetze
    # geht an Stufe 3, die Uebereinstimmung hebt die Konfidenz ueber t_auto
    store.set("classification.threshold_stage3_call", 0.95, user=admin_user)
    doc = make_document(
        obj,
        {
            "filename": "Mahnung.pdf",
            "pages": [
                H623
                + "Letzte Mahnung: Das Hausgeld ist trotz Zahlungserinnerung nicht eingegangen. Mahngebühr 5,00 EUR."
            ],
        },
    )
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    final = DocumentClassification.objects.get(document=doc, is_final=True)
    assert final.category_id == "05" and "Stufe 3 stimmt zu" in final.reasoning
    assert len(r.providers["openai"].sent) == 1
    # Ausweiskopie: kein Aufruf
    doc2 = make_document(
        obj,
        {
            "filename": "Ausweis.pdf",
            "pages": [H623 + "Kopie Personalausweis Nr. L01X00T47 des Eigentümers, Vorderseite"],
        },
    )
    start_run(obj)
    run_all(obj)
    assert not AiCall.objects.filter(document=doc2).exists()
    case = ReviewCase.objects.filter(document=doc2).first()
    assert case is not None and case.context.get("stage3_status") == "skipped"
    assert len(r.providers["openai"].sent) == 1


def test_statusseite_zeigt_stufe3(welt, fake_oauth, client_as, admin_user):
    client = client_as(admin_user)
    resp = client.get(reverse("status_page"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Stufe 3 (externe KI)" in html and "deaktiviert (F17, AVV)" in html
    assert json.dumps(resp.context["processing"]["ai"]["order"]) == '["openai", "anthropic"]'
