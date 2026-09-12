"""Lernfunktion der Objektzuordnung mit Datenbank: Beispiele, Regelableitung aus mehreren Bestaetigungen,
Regelwirkung in score_candidates, Abschalten, negative und ungepruefte Beispiele, Metriken (Baustein B)."""

from __future__ import annotations

import pytest
from tests.integration.requirements.conftest import make_doc, objekt  # noqa: F401

from apps.sync.assignment.candidates import build_index, extract_features, score_candidates
from apps.sync.assignment.decide import decide
from apps.sync.assignment.evidence import Evidence
from apps.sync.assignment.learning import (
    active_rules,
    deactivate_rule,
    derive_rules,
    evaluate,
    record_example,
)
from apps.sync.models import AssignmentExample, AssignmentRule

pytestmark = pytest.mark.django_db

TEXT = "Stadtwerke Beispiel GmbH\nAbschlagsrechnung Strom\nVertragsnummer: 4711-88\nKundennummer 998877"
KORRESPONDENT = "Stadtwerke Beispiel GmbH"


def features_for(text=TEXT, decision="review"):
    f = extract_features(text, filename="abschlag.pdf", correspondent=KORRESPONDENT)
    f["decision"] = decision
    return f


def test_regel_erst_aus_zwei_bestaetigungen(objekt, admin_user):  # noqa: F811
    doc1 = make_doc(objekt, "einzelabrechnung")
    ex = record_example(
        doc1,
        kind="confirm",
        target_object=objekt,
        proposed_object=objekt,
        user=admin_user,
        features=features_for(),
        evidence=[Evidence(kind="address", text="Musterweg 1", weight=0.8, object_id=objekt.pk)],
    )
    assert ex.pk and ex.evidence[0]["text"] == "Musterweg 1" and ex.decided_by == admin_user
    assert derive_rules() == []
    assert AssignmentRule.objects.count() == 0

    # zweites Beispiel aus demselben Dokument zaehlt nicht als zweiter Beleg
    record_example(doc1, kind="confirm", target_object=objekt, user=admin_user, features=features_for())
    assert derive_rules() == []

    doc2 = make_doc(objekt, "einzelabrechnung")
    record_example(
        doc2,
        kind="correct",
        previous_object=None,
        target_object=objekt,
        user=admin_user,
        features=features_for(),
    )
    rules = derive_rules()
    # Vertragsnummer und Kundennummer ergeben zwei Kombinationen und damit zwei Regeln
    assert len(rules) == 2
    rule = next(r for r in rules if r.conditions["number_kind"] == "contract")
    assert rule.kind == "feature_combo" and rule.version == 1 and rule.object_id == objekt.pk
    assert rule.conditions == {
        "supplier_norm": "stadtwerke beispiel gmbh",
        "number_kind": "contract",
        "number": "471188",
    }
    assert sorted(rule.created_from["example_ids"]) == sorted(
        AssignmentExample.objects.values_list("pk", flat=True)
    )
    codes = set(AssignmentRule.objects.values_list("code", flat=True))
    assert len(codes) == 2 and all(c.startswith("fc-") for c in codes)
    # erneuter Lauf legt nichts Neues an
    assert derive_rules() == []
    assert AssignmentRule.objects.count() == 2


def test_regel_wirkt_in_score_candidates_und_abschalten(objekt, admin_user):  # noqa: F811
    for _ in range(2):
        record_example(
            make_doc(objekt, "einzelabrechnung"),
            kind="confirm",
            target_object=objekt,
            user=admin_user,
            features=features_for(),
        )
    derive_rules(user=admin_user)
    index = build_index([objekt])
    text = "Abschlag 03/2026, Vertragsnummer 4711-88, Betrag 120,00 EUR"
    ohne = decide(score_candidates(text, filename="r.pdf", index=index, own_addresses=()))
    assert ohne.decision == "none"
    mit = score_candidates(
        text,
        filename="r.pdf",
        index=index,
        own_addresses=(),
        extra_hints={"correspondent": KORRESPONDENT},
        rules=active_rules(),
    )
    proposal = decide(mit)
    assert proposal.decision == "auto" and proposal.chosen.object_id == objekt.pk
    assert mit[0].rule_hits[0]["code"] == active_rules()[0].code or mit[0].rule_hits
    assert any(e.kind == "rule" for e in mit[0].evidence)
    # anderer Lieferant mit derselben Nummer trifft nicht
    fremd = score_candidates(
        text,
        index=index,
        own_addresses=(),
        extra_hints={"correspondent": "Andere GmbH"},
        rules=active_rules(),
    )
    assert fremd == []

    rule = active_rules()[0]
    deactivate_rule(rule, admin_user, "Testabschaltung")
    rule.refresh_from_db()
    assert not rule.is_active and rule.deactivated_by == admin_user and rule.notes == "Testabschaltung"
    danach = score_candidates(
        text,
        index=index,
        own_addresses=(),
        extra_hints={"correspondent": KORRESPONDENT},
        rules=AssignmentRule.objects.all(),
    )
    assert all(not c.rule_hits for c in danach)


def test_negative_und_ungepruefte_beispiele_erzeugen_keine_regel(objekt, admin_user):  # noqa: F811
    for _ in range(3):
        record_example(
            make_doc(objekt, "einzelabrechnung"),
            kind="reject",
            proposed_object=objekt,
            user=admin_user,
            features=features_for(),
        )
    assert derive_rules() == []
    for _ in range(2):
        record_example(
            make_doc(objekt, "einzelabrechnung"),
            kind="confirm",
            target_object=objekt,
            user=admin_user,
            features=features_for(),
            source="import_unverified",
        )
    assert derive_rules() == []
    # Pruefmenge zaehlt nicht zur Regelableitung
    holdout = features_for()
    holdout["holdout"] = True
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="confirm",
        target_object=objekt,
        user=admin_user,
        features=holdout,
    )
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="confirm",
        target_object=objekt,
        user=admin_user,
        features=holdout,
    )
    assert derive_rules() == []
    # eine bestaetigte Kombination, fuer die ein reject desselben Ziels vorliegt, ergibt keine Regel
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="confirm",
        target_object=objekt,
        user=admin_user,
        features=features_for(),
    )
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="confirm",
        target_object=objekt,
        user=admin_user,
        features=features_for(),
    )
    assert derive_rules() == []
    with pytest.raises(ValueError):
        derive_rules(min_confirmations=1)
    with pytest.raises(ValueError):
        record_example(make_doc(objekt, "einzelabrechnung"), kind="unbekannt", user=admin_user)


def test_evaluate_liefert_kennzahlen(objekt, admin_user):  # noqa: F811
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="confirm",
        target_object=objekt,
        proposed_object=objekt,
        user=admin_user,
        features=features_for(decision="auto"),
    )
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="correct",
        target_object=objekt,
        user=admin_user,
        features=features_for(decision="auto"),
    )
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="confirm",
        target_object=objekt,
        user=admin_user,
        features=features_for(decision="review"),
    )
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="reject",
        proposed_object=objekt,
        user=admin_user,
        features=features_for(decision="review"),
    )
    holdout = features_for(decision="auto")
    holdout["holdout"] = True
    record_example(
        make_doc(objekt, "einzelabrechnung"),
        kind="confirm",
        target_object=objekt,
        user=admin_user,
        features=holdout,
    )
    result = evaluate()
    assert result["total"] == 5 and result["holdout_count"] == 1
    training = result["sets"]["training"]
    assert training["count"] == 4 and training["auto_count"] == 2
    assert training["auto_hit_rate"] == 0.5
    assert training["correction_rate"] == 0.25
    assert training["review_effort"] == 0.75
    assert result["sets"]["holdout"]["auto_hit_rate"] == 1.0
    assert result["auto_hit_rate"] == 1.0  # Kopfzahlen aus der Pruefmenge
    leer = evaluate([])
    assert leer["total"] == 0 and leer["auto_hit_rate"] is None
