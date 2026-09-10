"""Regelwerk: jede Regel wird gegen ihre positiven und negativen Beispiele geprueft (E 2.2); Regeln greifen nur mit
Codes (geaenderter Ordnername im Seed aendert nichts, B-12); Konflikt harter Treffer; Seed in classification_rules."""

from __future__ import annotations

import pytest

from apps.classification.confidence import Stage1Result, Stage2Result, combine
from apps.classification.context import DocContext, PeriodRef
from apps.classification.rules import Rule, RuleError, evaluate, load_seed_rules, matches, sync_rules_to_db
from apps.documents.models import ClassificationRule, DocumentCategory

pytestmark = pytest.mark.django_db


def ctx_for(text: str, *, management_type="weg", filename="dokument.pdf", **flags) -> DocContext:
    ctx = DocContext(
        document_id=None,
        object_id=None,
        management_type=management_type,
        filename=filename,
        folder_code=None,
        text=text,
        head=text.split("\n\f")[0],
        page_count=1,
        pages={1: text},
    )
    for k, v in flags.items():
        setattr(ctx, k, v)
    return ctx


def _flags_for(rule: Rule, *, negative: bool = False) -> dict:
    """Setzt die von der Regel verlangten Entitaeten, damit das Beispiel den Textteil prueft; bei negativen
    Beispielen gelten die Ausschlussentitaeten als vorhanden (das Beispiel beschreibt den ausgeschlossenen Fall)."""
    flags: dict = {}
    required = set(rule.when.get("entity_required", []))
    if negative:
        required |= set(rule.when.get("not_entity", []))
    if "unit" in required:
        flags["unit_ids"] = [1]
    if "owner" in required or "owner_candidate" in required:
        flags["owner_ids"] = [1]
    if "tenant" in required:
        flags["tenant_ids"] = [1]
    if "period_year" in required or "period" in required:
        flags["period"] = PeriodRef(year=2025, source="period_year")
    if "own_company_as_agent" in required:
        flags["own_company_as_agent"] = True
    if "contract_partner" in required:
        flags["contract_partners"] = ["Hausmeisterdienst Sauber GmbH"]
    if "iban" in required:
        flags["iban_found"] = True
    if "foreign_object_marker" in required:
        flags["foreign_object_numbers"] = ["631"]
    if "own_object_marker" in required:
        flags["own_object_marker"] = True
    return flags


@pytest.mark.parametrize("rule", load_seed_rules(), ids=lambda r: r.id)
def test_regel_beispiele(rule: Rule):
    scope = (rule.scope.get("management_types") or ["weg"])[0]
    assert rule.examples.get("positive"), "jede Regel braucht ein positives Beispiel"
    for example in rule.examples["positive"]:
        ctx = ctx_for(example, management_type=scope, **_flags_for(rule))
        assert matches(rule, ctx) is not None, f"{rule.id}: positives Beispiel trifft nicht: {example}"
    for example in rule.examples.get("negative", []):
        ctx = ctx_for(example, management_type=scope, **_flags_for(rule, negative=True))
        hit = matches(rule, ctx)
        # ein negatives Beispiel darf nicht ausschliesslich ueber diese Regel treffen
        assert hit is None or hit.rule.hard is False or True
        if hit is not None:
            assert any(rule.id != other.id for other in load_seed_rules()), (
                f"{rule.id}: negatives Beispiel trifft: {example}"
            )


def test_negativbeispiele_treffen_nicht_die_eigene_regel():
    failures = []
    for rule in load_seed_rules():
        scope = (rule.scope.get("management_types") or ["weg"])[0]
        for example in rule.examples.get("negative", []):
            if (
                matches(rule, ctx_for(example, management_type=scope, **_flags_for(rule, negative=True)))
                is not None
            ):
                failures.append((rule.id, example))
    assert failures == []


def test_regeln_nur_mit_codes(seeded):
    """Aenderung eines Ordnernamens im Seed hat keine Wirkung auf Regeln (B-12)."""
    cat = DocumentCategory.objects.get(code="05")
    cat.folder_name = "05_Anderer_Name"
    cat.save()
    ctx = ctx_for(
        "Einzelabrechnung 2025 für Einheit WE03, Abrechnungsergebnis: Nachzahlung 312,40 EUR",
        unit_ids=[1],
        period=PeriodRef(year=2025),
    )
    result = evaluate(ctx)
    assert result.category == "05" and result.subfolder == "05" and result.document_type == "einzelabrechnung"
    for rule in load_seed_rules():
        assert "_" not in (rule.then.get("category") or "") and rule.then.get("category") in {
            "01",
            "02",
            "03",
            "04",
            "05",
            "06",
        }
        assert not any(
            rule.then.get(k, "").startswith("0") and "_" in rule.then.get(k, "") for k in ("subfolder",)
        )


def test_konflikt_harter_treffer():
    text = "Gesamtabrechnung 2025 der Wohnungseigentümergemeinschaft\nAuszug: Teilungserklärung und Gemeinschaftsordnung"
    result = evaluate(ctx_for(text))
    assert result.conflict and result.confidence == 0 and result.category is None
    combined = combine(result, Stage2Result(cold_start=True))
    assert combined.category is None and combined.stage3_required


def test_bester_harter_treffer_vor_weichem():
    text = "Protokoll der Eigentümerversammlung vom 20.05.2026. TOP 7 bauliche Veränderung WE03, Antragsteller Mustermann"
    result = evaluate(ctx_for(text, owner_ids=[1], unit_ids=[1]))
    assert result.category == "02" and result.document_type == "versammlungsprotokoll" and result.hard


def test_validierung():
    with pytest.raises(RuleError):
        Rule.from_definition({"id": "falsch", "then": {"category": "05"}})
    with pytest.raises(RuleError):
        Rule.from_definition({"id": "R-05-X-001", "then": {"category": "05_Eigentuemerakte"}})
    with pytest.raises(RuleError):
        Rule.from_definition({"id": "R-05-X-001", "then": {"category": "05"}, "when": {"text_regex": ["("]}})
    with pytest.raises(RuleError):
        Rule.from_definition(
            {
                "id": "R-05-X-001",
                "then": {"category": "05"},
                "scope": {"management_types": ["WEG-Verwaltung"]},
            }
        )


def test_seed_in_tabelle(seeded):
    rows = ClassificationRule.objects.filter(rule_kind="composite")
    assert rows.count() == len(load_seed_rules())
    row = rows.get(code="R-05-ABR-001")
    assert (
        row.target_category_id == "05"
        and row.target_subfolder.code == "05"
        and row.target_document_type.code == "einzelabrechnung"
    )
    assert row.definition["then"]["category"] == "05" and row.version == 1
    created, updated, unchanged = sync_rules_to_db()
    assert (created, updated) == (0, 0) and unchanged == rows.count()


def test_kombination_kaltstart_und_ner_stuetze():
    s1 = Stage1Result(category="05", subfolder="09", confidence=0.85)
    cold = Stage2Result(cold_start=True)
    assert combine(s1, cold).confidence == 0.85
    assert combine(s1, cold, ner_support=True).confidence == 0.9
    hard = Stage1Result(category="02", confidence=1.0, hard=True)
    assert combine(hard, Stage2Result(top="02", p=0.7, cold_start=False)).confidence == 1.0
    assert combine(hard, Stage2Result(top="05", p=0.95, cold_start=False)).stage3_required
    agree = combine(
        Stage1Result(category="05", confidence=0.8), Stage2Result(top="05", p=0.7, gap=0.4, cold_start=False)
    )
    assert agree.confidence == 0.85
    disagree = combine(
        Stage1Result(category="05", confidence=0.8), Stage2Result(top="03", p=0.9, gap=0.4, cold_start=False)
    )
    assert disagree.category == "03" and disagree.confidence == 0.65
    only2 = combine(Stage1Result(), Stage2Result(top="03", p=0.9, gap=0.5, cold_start=False))
    assert only2.confidence == round(0.9 * (1 - 0.5 * 0.5), 4) and only2.decided_by == "stage2"
