"""Stufe 3 liest Vertragsdaten (12.09.2026): Mieterdokumente ohne bekannten Mieter gehen bei aktivem Provider auch
dann an Stufe 3, wenn die Kategorie sicher ist (ai.extract_lease_facts); der Block lease ergaenzt die Textregeln."""

from __future__ import annotations

import pytest
from tests.integration.ai.test_pipeline_stage3 import _answer, enable_ai
from tests.integration.classification.conftest import make_document

from apps.ai import services as ai_services
from apps.ai.fakes import FakeClassificationProvider
from apps.ai.provider import PriceList, ProviderConfig
from apps.ai.router import Router
from apps.config import store
from apps.pipeline.models import JobType, ProcessingJob
from apps.pipeline.runs import start_run
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_router(monkeypatch):
    def _make(answer=None):
        p = FakeClassificationProvider("openai", answer=answer)
        f = FakeClassificationProvider("anthropic", answer=answer)
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
        return r

    return _make


def _vertrag(obj, name: str) -> dict:
    return {
        "filename": name,
        "pages": [
            f"Objekt {obj.object_number} {obj.name}\nMietvertrag\nVermietet wird eine Wohnung im Haus. "
            "Die Miete ist monatlich im Voraus zu zahlen. Unterschriften der Parteien."
        ],
    }


def test_stufe3_liefert_vertragsdaten_fuer_den_vorschlag(welt, fake_oauth, run_all, admin_user, fake_router):
    enable_ai(admin_user)
    answer = _answer("04", None, "mietvertrag", 0.9)
    answer["lease"] = {
        "tenant_names": ["Herrn Jonas Beispielmieter", "Frau Lea Beispielmieter"],
        "unit": "WE 2",
        "start_date": "2024-03-01",
        "end_date": None,
        "base_rent": 650,
        "utilities_prepayment": 180,
        "heating_prepayment": None,
        "deposit_amount": 1950,
        "total_rent": None,
    }
    router = fake_router(answer=answer)
    obj = welt["objects"]["625"]
    doc = make_document(obj, _vertrag(obj, "Mietvertrag_ki.pdf"))
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.CLASSIFY_AI, status="done").exists()
    assert doc.category_id == "04"
    case = ReviewCase.objects.get(document=doc, case_subtype="tenant_unknown")
    facts = case.context["lease_facts"]
    assert facts["source"] == "stage3"
    assert [(t["first_name"], t["last_name"]) for t in facts["tenants"]] == [
        ("Jonas", "Beispielmieter"),
        ("Lea", "Beispielmieter"),
    ]
    assert (
        facts["unit_hint"] == "WE 2"
        and facts["base_rent"] == "650.00"
        and facts["deposit_amount"] == "1950.00"
    )
    assert case.proposed_action == {"action": "create_tenant"}
    # Datenminimierung bleibt: der Request traegt keine Stammdatennamen, die Antwort wird lokal verarbeitet
    sent = router.providers["openai"].sent[-1]["user"]
    assert "Beispielmieter" not in sent and '"lease"' in sent


def test_schalter_aus_kein_zusaetzlicher_aufruf(welt, fake_oauth, run_all, admin_user, fake_router):
    enable_ai(admin_user)
    store.set("ai.extract_lease_facts", False, user=admin_user, reason="Test")
    fake_router(answer=_answer("04", None, "mietvertrag", 0.9))
    obj = welt["objects"]["625"]
    doc = make_document(obj, _vertrag(obj, "Mietvertrag_ohne_ki.pdf"))
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    assert doc.category_id == "04"
    assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.CLASSIFY_AI).exists()
    case = ReviewCase.objects.get(document=doc, case_subtype="tenant_unknown")
    assert case.context["lease_facts"]["source"] == "rules" and case.context["lease_facts"]["tenants"] == []
