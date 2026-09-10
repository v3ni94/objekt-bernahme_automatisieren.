"""Unscharfer Abgleich gegen owners (Fachentwurf H 6.6, E 3.2; Schwellen ANNAHME A-19).

Signale: Namensaehnlichkeit (rapidfuzz, token_set_ratio fuer Personen, partial_ratio fuer Firmen) als Basis,
Zuschlaege aus import.owner_match_weights fuer gleiche Einheit im Objekt, gleiche Anschrift, gleiche E-Mail,
gleicher iban_hash, gleicher Vorname. Ein Nachnamentreffer allein reicht nie fuer einen automatischen Treffer;
objektuebergreifende Treffer werden angezeigt, aber nie automatisch uebernommen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from apps.drive.naming import transliterate

DEFAULT_WEIGHTS = {
    "same_unit": 15,
    "same_address": 8,
    "same_email": 15,
    "same_iban_hash": 15,
    "same_first_name": 8,
}
DEFAULT_THRESHOLDS = {"auto": 90, "candidate": 78}
STRONG_SIGNALS = {"same_unit", "same_address", "same_email", "same_iban_hash", "same_first_name"}


def norm_name(text: str | None) -> str:
    text = transliterate(text or "").casefold()
    text = text.replace("-", " ")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


@dataclass(frozen=True)
class OwnerRecord:
    """Minimaler Auszug eines bestehenden Eigentuemers fuer den Abgleich (einmal je Import geladen)."""

    id: int
    type: str
    first_name: str | None
    last_name: str | None
    company_name: str | None
    email: str | None
    postal_code: str | None
    street: str | None
    iban_hash_hex: str | None
    unit_ids_in_object: frozenset[int] = frozenset()
    has_assignment_in_object: bool = False
    display: str = ""


@dataclass(frozen=True)
class PersonQuery:
    type: str
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    email: str | None = None
    postal_code: str | None = None
    street: str | None = None
    iban_hash_hex: str | None = None
    unit_id: int | None = None


@dataclass
class OwnerCandidate:
    owner_id: int
    score: float
    base: float
    signals: list[str] = field(default_factory=list)
    display: str = ""
    in_object: bool = False

    def as_dict(self) -> dict:
        return {
            "owner_id": self.owner_id,
            "score": round(self.score, 1),
            "signals": self.signals,
            "display": self.display,
            "in_object": self.in_object,
        }


@dataclass
class MatchResult:
    decision: str  # auto | candidates | new
    best: OwnerCandidate | None
    candidates: list[OwnerCandidate]

    def as_dict(self) -> dict:
        return {
            "decision": self.decision,
            "best": self.best.as_dict() if self.best else None,
            "candidates": [c.as_dict() for c in self.candidates[:5]],
        }


def _name_similarity(q: PersonQuery, o: OwnerRecord) -> float:
    if q.type == "natural_person" and o.type == "natural_person":
        q_full = norm_name(f"{q.last_name or ''} {q.first_name or ''}")
        o_full = norm_name(f"{o.last_name or ''} {o.first_name or ''}")
        if not q.first_name or not o.first_name:
            # nur Nachnamen vergleichbar
            return fuzz.ratio(norm_name(q.last_name), norm_name(o.last_name))
        return fuzz.token_set_ratio(q_full, o_full)
    q_name = norm_name(q.company_name or q.last_name)
    o_name = norm_name(o.company_name or o.last_name)
    if not q_name or not o_name:
        return 0.0
    return (
        fuzz.partial_ratio(q_name, o_name)
        if q.type != "natural_person" and o.type != "natural_person"
        else fuzz.ratio(q_name, o_name)
    )


def score_owner(q: PersonQuery, o: OwnerRecord, weights: dict | None = None) -> OwnerCandidate:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    base = _name_similarity(q, o)
    signals: list[str] = []
    score = base
    if q.unit_id is not None and q.unit_id in o.unit_ids_in_object:
        signals.append("same_unit")
    if (
        q.postal_code
        and o.postal_code
        and q.postal_code == o.postal_code
        and norm_name(q.street)
        and norm_name(q.street) == norm_name(o.street)
    ):
        signals.append("same_address")
    if q.email and o.email and q.email.strip().lower() == o.email.strip().lower():
        signals.append("same_email")
    if q.iban_hash_hex and o.iban_hash_hex and q.iban_hash_hex == o.iban_hash_hex:
        signals.append("same_iban_hash")
    if (
        q.type == "natural_person"
        and o.type == "natural_person"
        and q.first_name
        and o.first_name
        and norm_name(q.first_name) == norm_name(o.first_name)
        and fuzz.ratio(norm_name(q.last_name), norm_name(o.last_name)) >= 85
    ):
        signals.append("same_first_name")
    for s in signals:
        score += weights.get(s, 0)
    return OwnerCandidate(o.id, min(score, 100.0), base, signals, o.display, o.has_assignment_in_object)


def match_owner(
    q: PersonQuery, owners: list[OwnerRecord], *, weights: dict | None = None, thresholds: dict | None = None
) -> MatchResult:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    scored = [score_owner(q, o, weights) for o in owners]
    scored = [c for c in scored if c.base >= 60 or c.score >= th["candidate"]]
    scored.sort(key=lambda c: -c.score)
    if not scored:
        return MatchResult("new", None, [])
    best = scored[0]
    strong = any(s in STRONG_SIGNALS for s in best.signals)
    if best.score >= th["auto"] and strong and best.in_object:
        second = scored[1] if len(scored) > 1 else None
        if second is None or second.score < th["candidate"]:
            return MatchResult("auto", best, scored)
        return MatchResult("candidates", best, scored)
    if best.score >= th["candidate"]:
        return MatchResult("candidates", best, scored)
    return MatchResult("new", None, scored)
