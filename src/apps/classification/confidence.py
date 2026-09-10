"""Konfidenzmodell (E 7.2, docs/architektur.md 6.6) mit dem Schluesselsatz classification.* aus app_settings (B-08)."""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.config import store


@dataclass
class Stage1Result:
    category: str | None = None  # Code 01 bis 06 oder None
    subfolder: str | None = None
    document_type: str | None = None
    scope: str | None = None
    confidence: float = 0.0
    hard: bool = False
    conflict: bool = False
    rule_codes: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    proposal: str | None = None
    subtype: str | None = None  # z. B. tenant_document_in_weg, misplaced_weg_document
    hits: list[dict] = field(default_factory=list)


@dataclass
class Stage2Result:
    top: str | None = None  # Kategorie-Code oder nicht_objektbezogen
    p: float = 0.0
    gap: float = 0.0  # Abstand zur zweiten Klasse
    labels: list[tuple[str, float]] = field(default_factory=list)
    model_version: str | None = None
    cold_start: bool = True
    subfolder: str | None = None
    document_type: str | None = None
    sub_p: float = 0.0


@dataclass
class Combined:
    category: str | None
    confidence: float
    reason: str
    stage3_required: bool = False
    decided_by: str = "stage1"


def thresholds() -> dict[str, float]:
    keys = (
        "threshold_auto_file",
        "threshold_stage3_call",
        "threshold_stage3_override",
        "stage2_conflict_p",
        "bonus_agree",
        "malus_disagree",
        "gap_factor",
        "ner_support_bonus",
    )
    return {k: float(store.get(f"classification.{k}", 0)) for k in keys}


def combine(s1: Stage1Result, s2: Stage2Result | None, *, ner_support: bool = False) -> Combined:
    """KOMBINIERE(s1, s2) nach E 7.2; ohne scharfes Modell traegt Stufe 2 nur die NER-Stuetze (Kaltstart)."""
    t = thresholds()
    cold = s2 is None or s2.cold_start or s2.top is None
    if s1.conflict:
        return Combined(None, 0.0, "Konflikt harter Regeln", stage3_required=True)
    if s1.category is None:
        if cold:
            return Combined(
                None, 0.0, "kein Regeltreffer, Klassifikator in der Kaltstartphase", stage3_required=True
            )
        c = s2.p * (1 - t["gap_factor"] * (1 - s2.gap))
        return Combined(
            s2.top,
            round(c, 4),
            "nur Stufe 2",
            stage3_required=c < t["threshold_stage3_call"],
            decided_by="stage2",
        )
    if cold:
        c = s1.confidence
        reason = "Regeltreffer, Klassifikator in der Kaltstartphase"
        if not s1.hard and ner_support and s1.category == "05":
            c = min(1.0, c + t["ner_support_bonus"])
            reason += ", NER-Stütze (Eigentümer oder Einheit erkannt)"
        return Combined(s1.category, round(c, 4), reason, stage3_required=c < t["threshold_stage3_call"])
    if s1.hard:
        if s2.top == s1.category:
            return Combined(s1.category, 1.0, "harte Regel, Stufe 2 stimmt zu")
        if s2.p > t["stage2_conflict_p"]:
            c = t["threshold_stage3_call"] - 0.01
            return Combined(
                s1.category, round(c, 4), "harte Regel, Stufe 2 widerspricht deutlich", stage3_required=True
            )
        return Combined(s1.category, s1.confidence, "harte Regel")
    if s1.category == s2.top:
        c = min(1.0, max(s1.confidence, s2.p) + t["bonus_agree"])
        return Combined(
            s1.category,
            round(c, 4),
            "Stufe 1 und 2 stimmen überein",
            stage3_required=c < t["threshold_stage3_call"],
        )
    c = max(s1.confidence, s2.p) - t["malus_disagree"]
    category = s1.category if s1.confidence >= s2.p else s2.top
    return Combined(
        category,
        round(max(c, 0.0), 4),
        "Stufe 1 und 2 widersprechen sich",
        stage3_required=c < t["threshold_stage3_call"],
        decided_by="stage1" if s1.confidence >= s2.p else "stage2",
    )
