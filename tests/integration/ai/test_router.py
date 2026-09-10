"""Stufe 3 (M8, B-28, Ue15): Provider-Wechsel mit FakeClassificationProvider in vier Szenarien (Timeout des Primaeren,
5xx, Schemaverletzung mit Reparatur, Ausfall beider), Kostenlimit, Maskierungssperre, Circuit Breaker, Datenminimierung
(kein Name aus owners im Request), Antwortschema, Kostenrechner mit Preisliste."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.ai import router as router_mod
from apps.ai.fakes import FakeClassificationProvider
from apps.ai.models import AiCall
from apps.ai.provider import PriceList, ProviderConfig
from apps.ai.router import CircuitBreaker, Router
from apps.ai.schema import (
    ClassificationRequest,
    SchemaViolation,
    Taxonomy,
    TaxonomyEntry,
    parse_result,
    response_json_schema,
    taxonomy_from_catalog,
)
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db


def cfg(name, **kw) -> ProviderConfig:
    base = {"enabled": True, "model": f"{name}-testmodell", "timeout_s": 5, "max_attempts": 2}
    base.update(kw)
    return ProviderConfig(name=name, **base)


def price_list() -> PriceList:
    return PriceList(
        "test-2026-09",
        {
            "openai-testmodell": (Decimal("0.0010"), Decimal("0.0040")),
            "anthropic-testmodell": (Decimal("0.0020"), Decimal("0.0080")),
        },
    )


def request(taxonomy=None) -> ClassificationRequest:
    return ClassificationRequest(
        excerpt_masked="Einzelabrechnung 2025 für Einheit WE03. Abrechnungsergebnis: Nachzahlung 312,40 EUR. IBAN [IBAN_****3000]",
        filename_masked="Einzelabrechnung_2025_WE03.pdf",
        management_type="weg",
        taxonomy=taxonomy or taxonomy_from_catalog(),
        unit_label_patterns=["WE"],
        hints={"rule_candidates": ["05"], "has_period_year": True},
    )


def make_router(
    primary_script, fallback_script=None, **cfg_kw
) -> tuple[Router, FakeClassificationProvider, FakeClassificationProvider]:
    p = FakeClassificationProvider("openai", script=primary_script)
    f = FakeClassificationProvider("anthropic", script=fallback_script or [])
    r = Router(
        {"openai": p, "anthropic": f},
        configs={"openai": cfg("openai", **cfg_kw), "anthropic": cfg("anthropic")},
        price_list=price_list(),
        order=["openai", "anthropic"],
    )
    return r, p, f


@pytest.fixture
def objekt(seeded):
    return ManagedObject.objects.create(object_number="623", name="Test", management_type="weg", is_test=True)


def test_timeout_des_primaeren_fallback_antwortet(objekt):
    r, p, f = make_router(["timeout", "timeout"])
    result = r.classify(request(), obj=objekt)
    assert result.status == "ok" and result.provider == "anthropic" and result.fallback_used
    assert result.result.category == "05" and result.result.document_type == "einzelabrechnung"
    calls = list(AiCall.objects.order_by("id"))
    assert [c.status for c in calls] == ["timeout", "timeout", "ok"]
    assert (
        calls[2].fallback_used
        and calls[2].fallback_of_call_id == calls[1].pk
        and calls[2].provider == "anthropic"
    )
    assert calls[2].cost_eur == Decimal("0.0020") * 800 / 1000 + Decimal("0.0080") * 120 / 1000
    assert calls[2].price_list_version == "test-2026-09" and calls[2].prompt_hash and calls[2].prompt_chars
    assert (
        len(p.sent) == 2 and len(f.sent) == 1 and p.sent[0]["user"] == f.sent[0]["user"]
    )  # identischer Request


def test_5xx_dann_fallback(objekt):
    r, p, f = make_router(["5xx", "5xx"])
    result = r.classify(request(), obj=objekt)
    assert result.status == "ok" and result.provider == "anthropic"
    assert list(AiCall.objects.values_list("status", "http_status").order_by("id")) == [
        ("provider_error", 502),
        ("provider_error", 502),
        ("ok", 200),
    ]


def test_schemaverletzung_mit_reparatur(objekt):
    r, p, f = make_router(["schema_error", "ok"])
    result = r.classify(request(), obj=objekt)
    assert result.status == "ok" and result.provider == "openai" and not result.fallback_used
    assert "Hinweis zur Korrektur" in p.sent[1]["user"] and "Hinweis zur Korrektur" not in p.sent[0]["user"]
    assert [c.status for c in AiCall.objects.order_by("id")] == ["schema_error", "ok"]
    assert AiCall.objects.get(status="schema_error").response_summary["error"]


def test_ausfall_beider_anbieter(objekt):
    r, p, f = make_router(["timeout", "5xx"], ["not_json", "429"])
    result = r.classify(request(), obj=objekt)
    assert result.status == "provider_error" and result.result is None and result.fallback_used
    assert AiCall.objects.count() == 4
    assert set(AiCall.objects.values_list("status", flat=True)) == {
        "timeout",
        "provider_error",
        "schema_error",
        "rate_limited",
    }


def test_kostenlimit_stoppt_aufrufe(objekt):
    r, p, f = make_router(["ok"], cost_limit_eur_per_object=Decimal("0.001"))
    first = r.classify(request(), obj=objekt)
    assert first.status == "ok"
    second = r.classify(request(), obj=objekt)
    assert second.status == "budget_blocked" and second.provider == "openai"
    assert AiCall.objects.filter(status="budget_blocked").count() == 1
    assert len(p.sent) == 1  # kein zweiter Aufruf beim Anbieter


def test_maskierungssperre_vor_dem_senden(objekt):
    r, p, f = make_router(["ok"])
    req = ClassificationRequest(
        excerpt_masked="Bitte überweisen Sie auf IBAN DE89 3704 0044 0532 0130 00",
        filename_masked="x.pdf",
        management_type="weg",
        taxonomy=taxonomy_from_catalog(),
    )
    result = r.classify(req, obj=objekt)
    assert result.status == "blocked_by_mask_check" and p.sent == []
    assert AiCall.objects.get().status == "blocked_by_mask_check"
    req2 = ClassificationRequest(
        excerpt_masked="Kontonummer: 1234567890 BLZ 37040044",
        filename_masked="x.pdf",
        management_type="weg",
        taxonomy=taxonomy_from_catalog(),
    )
    assert r.classify(req2, obj=objekt).status == "blocked_by_mask_check"


def test_circuit_breaker(objekt):
    r, p, f = make_router(["5xx"] * 6)
    for _ in range(2):
        r.classify(request(), obj=objekt)
    assert CircuitBreaker("openai").is_open()
    calls_before = len(p.sent)
    result = r.classify(request(), obj=objekt)
    assert (
        result.status == "ok" and result.provider == "anthropic" and len(p.sent) == calls_before
    )  # direkt zum Fallback
    CircuitBreaker("openai").record_success()
    assert not CircuitBreaker("openai").is_open()


def test_datenminimierung_und_schema(welt, fake_oauth, run_all):
    from tests.integration.classification.conftest import make_document

    from apps.ai.services import build_request
    from apps.classification.context import build_context
    from apps.pipeline.runs import start_run

    obj = welt["objects"]["623"]
    doc = make_document(
        obj,
        {
            "filename": "Mahnung_Mustermann_WE03.pdf",
            "pages": [
                "Objekt 623 Düsseldorf, Joachimstraße 49\nZahlungserinnerung Hausgeld 03/2025 für WE03, Eigentümer Max Mustermann, IBAN DE89 3704 0044 0532 0130 00"
            ],
        },
    )
    start_run(obj)
    run_all(obj, job_types=["extract_entities"])
    ctx = build_context(doc)
    req = build_request(doc, ctx, {"hits": [{"category": "05"}]})
    payload = json.dumps(req.prompt_payload(), ensure_ascii=False)
    for name in ("Mustermann", "Altmann", "Neumann", "Beispiel", "Sonder", "Mieterling"):
        assert name not in req.filename_masked and name not in json.dumps(req.hints)
    assert "[NAME]" in req.filename_masked
    assert "0532" not in payload and "DE89" not in payload  # maskiert
    assert req.unit_label_patterns == ["ST", "WE"] and "WE03" not in json.dumps(req.unit_label_patterns)
    assert (
        "Mustermann" in req.excerpt_masked
    )  # Namen im Text bleiben (Abgleich lokal), Stammdatenlisten nicht
    schema = response_json_schema()
    assert schema["additionalProperties"] is False and schema["properties"]["category"]["enum"] == [
        "01",
        "02",
        "03",
        "04",
        "05",
        "06",
    ]
    with pytest.raises(SchemaViolation):
        parse_result('{"category": "05_Eigentümerakte"}')
    with pytest.raises(SchemaViolation):
        parse_result(
            json.dumps(
                {
                    "object_related": True,
                    "category": "05",
                    "subfolder": "99",
                    "document_type": None,
                    "period": {"year": None, "from": None, "to": None, "document_date": None},
                    "mentioned_units": [],
                    "mentioned_parties": [],
                    "confidence": 0.5,
                    "reasoning": "x",
                }
            ),
            req.taxonomy,
        )
    ok = parse_result(
        json.dumps(
            {
                "object_related": True,
                "category": "05",
                "subfolder": "09",
                "document_type": "zahlungserinnerung",
                "period": {"year": 2025, "from": None, "to": None, "document_date": None},
                "mentioned_units": ["WE03"],
                "mentioned_parties": ["Mustermann"],
                "confidence": 0.9,
                "reasoning": "Zahlungserinnerung",
            }
        ),
        req.taxonomy,
    )
    assert ok.document_type == "zahlungserinnerung" and ok.period.year == 2025


def test_taxonomie_und_router_ohne_freigabe(objekt):
    tax = taxonomy_from_catalog()
    assert {c.code for c in tax.categories} == {"01", "02", "03", "04", "05", "06"}
    assert "einzelabrechnung" in tax.document_type_codes("05") and "05" in tax.subfolder_codes("05")
    assert "Einzelabrechnung" in tax.as_prompt_text()
    p = FakeClassificationProvider("openai")
    r = Router(
        {"openai": p},
        configs={"openai": ProviderConfig(name="openai", enabled=False)},
        price_list=price_list(),
        order=["openai"],
    )
    assert r.classify(request(), obj=objekt).status == "disabled" and p.sent == []
    assert isinstance(router_mod.month_costs(), dict)
    small = Taxonomy(
        (TaxonomyEntry("05", "Eigentümerakte"),),
        {"05": (TaxonomyEntry("05", "Abrechnungen"),)},
        {"05/05": (TaxonomyEntry("einzelabrechnung", "Einzelabrechnung"),)},
    )
    assert small.document_type_codes("05") == {"einzelabrechnung"}
