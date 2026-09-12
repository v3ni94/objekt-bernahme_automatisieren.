"""Lernfunktion der Objektzuordnung (Datenbank): Beispiele aufzeichnen, Regeln aus mehreren Bestaetigungen
ableiten, Regeln anwenden und abschalten, Metriken gegen die gespeicherten Beispiele berechnen.

Grundsaetze aus dem Auftrag: eine Regel entsteht nie aus einem einzelnen Beleg, nur aus mindestens
min_confirmations bestaetigten Beispielen (kind confirm oder correct, source human) verschiedener Dokumente mit
identischer Merkmalskombination (Lieferant plus objektspezifische Nummer) fuer dasselbe Zielobjekt. Beispiele
mit source import_unverified zaehlen nicht, Beispiele mit features {"holdout": true} zaehlen nur zur Messung.
Regeln sind versioniert (code, version) und abschaltbar; bestaetigte Altdokumente werden nie umsortiert."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from apps.sync.models import AssignmentExample, AssignmentRule, ExampleKind, ExampleSource

from .candidates import apply_rules, normalize_number
from .evidence import evidence_to_dicts

RULE_EXAMPLE_KINDS = (ExampleKind.CONFIRM, ExampleKind.CORRECT)
RULE_EXAMPLE_SOURCES = (ExampleSource.HUMAN,)
RULE_KIND = "feature_combo"
NUMBER_KINDS_FOR_RULES = ("contract", "customer", "meter", "insurance", "property")

__all__ = [
    "active_rules",
    "apply_rules",
    "combo_keys",
    "deactivate_rule",
    "derive_rules",
    "evaluate",
    "is_holdout",
    "record_example",
    "rule_code",
]


def record_example(
    document,
    *,
    kind: str,
    previous_object=None,
    target_object=None,
    proposed_object=None,
    user=None,
    features: dict | None = None,
    evidence: Iterable | None = None,
    review_case=None,
    source: str = ExampleSource.HUMAN,
    rule_version: str | None = None,
) -> AssignmentExample:
    """Speichert ein Lernbeispiel. kind: confirm, correct oder reject. features: gefundene Merkmale
    (normalisierte Adressen, Lieferant oder Korrespondent, Nummernpaare mit Kontextwort, Drive-Ordner).
    evidence: Belege (Evidence oder dict). Negative Beispiele bleiben erhalten."""
    if kind not in ExampleKind.values:
        raise ValueError(f"Unbekannte Beispielart: {kind}")
    if source not in ExampleSource.values:
        raise ValueError(f"Unbekannte Quelle: {source}")
    return AssignmentExample.objects.create(
        document=document,
        kind=kind,
        source=source,
        previous_object=previous_object,
        target_object=target_object,
        proposed_object=proposed_object,
        decided_by=user,
        decided_at=timezone.now(),
        features=dict(features or {}),
        evidence=evidence_to_dicts(evidence),
        rule_version=rule_version,
        review_case=review_case,
    )


def is_holdout(example: AssignmentExample) -> bool:
    return bool((example.features or {}).get("holdout"))


def combo_keys(features: dict | None) -> list[tuple[str, str, str]]:
    """Merkmalskombinationen eines Beispiels: (Lieferant, Nummernart, Nummer), nur mit Lieferant."""
    features = features or {}
    supplier = features.get("supplier_norm")
    if not supplier:
        return []
    keys: list[tuple[str, str, str]] = []
    for item in features.get("numbers") or ():
        kind = item.get("kind")
        number = normalize_number(item.get("value"))
        if kind in NUMBER_KINDS_FOR_RULES and number and (supplier, kind, number) not in keys:
            keys.append((supplier, kind, number))
    return keys


def rule_code(supplier: str, number_kind: str, number: str) -> str:
    digest = hashlib.sha256(f"{supplier}|{number_kind}|{number}".encode()).hexdigest()[:24]
    return f"fc-{digest}"


def derive_rules(min_confirmations: int = 2, user=None) -> list[AssignmentRule]:
    """Leitet Regeln aus bestaetigten Beispielen ab und legt sie an (neue Version, falls der Code schon
    existiert und sich Ziel oder Bedingungen unterscheiden). Kombinationen mit Gegenbeispielen (reject des
    Ziels, Korrektur weg vom Ziel) oder mit mehreren Zielobjekten ergeben keine Regel. Liefert die neu
    angelegten Regeln."""
    if min_confirmations < 2:
        raise ValueError(
            "Eine Regel entsteht nie aus einem einzelnen Beleg: min_confirmations muss mindestens 2 sein"
        )
    positives: dict[tuple[str, str, str], dict[int, set[int]]] = {}
    negatives: dict[tuple[str, str, str], set[int]] = {}
    examples = AssignmentExample.objects.filter(is_active=True).order_by("pk")
    for ex in examples:
        if is_holdout(ex) or ex.source == ExampleSource.IMPORT_UNVERIFIED:
            continue
        for key in combo_keys(ex.features):
            if ex.kind in RULE_EXAMPLE_KINDS and ex.source in RULE_EXAMPLE_SOURCES and ex.target_object_id:
                positives.setdefault(key, {}).setdefault(ex.target_object_id, set()).add(ex.document_id)
            if ex.kind == ExampleKind.REJECT and ex.proposed_object_id:
                negatives.setdefault(key, set()).add(ex.proposed_object_id)
            if ex.kind == ExampleKind.CORRECT and ex.previous_object_id:
                negatives.setdefault(key, set()).add(ex.previous_object_id)
    created: list[AssignmentRule] = []
    example_ids_by_key = _example_ids_by_key(examples)
    for key, targets in positives.items():
        if len(targets) != 1:
            continue
        target_id, documents = next(iter(targets.items()))
        if len(documents) < min_confirmations or target_id in negatives.get(key, set()):
            continue
        supplier, number_kind, number = key
        conditions = {"supplier_norm": supplier, "number_kind": number_kind, "number": number}
        code = rule_code(supplier, number_kind, number)
        rule = _create_rule_version(
            code, conditions, target_id, example_ids_by_key.get((key, target_id), []), user
        )
        if rule is not None:
            created.append(rule)
    return created


def _example_ids_by_key(examples) -> dict[tuple[tuple[str, str, str], int], list[int]]:
    out: dict[tuple[tuple[str, str, str], int], list[int]] = {}
    for ex in examples:
        if is_holdout(ex) or ex.source not in RULE_EXAMPLE_SOURCES or ex.kind not in RULE_EXAMPLE_KINDS:
            continue
        for key in combo_keys(ex.features):
            out.setdefault((key, ex.target_object_id), []).append(ex.pk)
    return out


@transaction.atomic
def _create_rule_version(code: str, conditions: dict, target_id: int, example_ids: list[int], user):
    latest = AssignmentRule.objects.filter(code=code).order_by("-version").first()
    if (
        latest is not None
        and latest.is_active
        and latest.object_id == target_id
        and latest.conditions == conditions
    ):
        return None
    version = latest.version + 1 if latest is not None else 1
    rule = AssignmentRule.objects.create(
        code=code,
        version=version,
        kind=RULE_KIND,
        conditions=conditions,
        object_id=target_id,
        created_from={"example_ids": sorted(example_ids)},
        created_by=user,
        notes=f"abgeleitet aus {len(example_ids)} bestaetigten Beispielen",
    )
    AssignmentRule.objects.filter(code=code, is_active=True).exclude(pk=rule.pk).update(
        is_active=False,
        deactivated_at=timezone.now(),
        deactivated_by=user,
        notes=f"ersetzt durch Version {version}",
    )
    return rule


def deactivate_rule(rule: AssignmentRule, user=None, reason: str | None = None) -> AssignmentRule:
    """Schaltet eine Regel ab; bestehende Zuordnungen bleiben unveraendert."""
    rule.is_active = False
    rule.deactivated_at = timezone.now()
    rule.deactivated_by = user
    rule.notes = (reason or "abgeschaltet")[:500]
    rule.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "notes", "updated_at"])
    return rule


def active_rules():
    """Aktive Regeln fuer score_candidates(rules=...)."""
    return list(AssignmentRule.objects.filter(is_active=True).order_by("code", "-version"))


def _metrics(examples: list[AssignmentExample]) -> dict:
    total = len(examples)
    auto = [e for e in examples if (e.features or {}).get("decision") == "auto"]
    auto_hits = [e for e in auto if e.kind == ExampleKind.CONFIRM]
    corrections = [e for e in examples if e.kind == ExampleKind.CORRECT]
    rejects = [e for e in examples if e.kind == ExampleKind.REJECT]
    manual = [
        e for e in examples if (e.features or {}).get("decision") != "auto" or e.kind != ExampleKind.CONFIRM
    ]

    def rate(part: int, whole: int) -> float | None:
        return round(part / whole, 4) if whole else None

    return {
        "count": total,
        "auto_count": len(auto),
        "auto_hit_rate": rate(len(auto_hits), len(auto)),
        "correction_rate": rate(len(corrections), total),
        "reject_rate": rate(len(rejects), total),
        "review_effort": rate(len(manual), total),
        "unknown_decision": sum(1 for e in examples if "decision" not in (e.features or {})),
    }


def evaluate(examples: Iterable[AssignmentExample] | None = None) -> dict:
    """Metriken gegen die gespeicherten Beispiele: auto-Trefferquote (Anteil bestaetigter automatischer
    Zuordnungen), Korrekturrate, Pruefaufwand (Anteil der Beispiele, die eine Person bearbeiten musste).
    Getrennt fuer Trainingsmenge und Pruefmenge (features holdout true); die Kennzahlen auf oberster Ebene
    stammen aus der Pruefmenge, falls vorhanden, sonst aus allen Beispielen."""
    items = list(examples) if examples is not None else list(AssignmentExample.objects.filter(is_active=True))
    holdout = [e for e in items if is_holdout(e)]
    training = [e for e in items if not is_holdout(e)]
    sets = {"all": _metrics(items), "training": _metrics(training), "holdout": _metrics(holdout)}
    headline = sets["holdout"] if holdout else sets["all"]
    return {
        "total": len(items),
        "holdout_count": len(holdout),
        "auto_hit_rate": headline["auto_hit_rate"],
        "correction_rate": headline["correction_rate"],
        "review_effort": headline["review_effort"],
        "sets": sets,
    }
