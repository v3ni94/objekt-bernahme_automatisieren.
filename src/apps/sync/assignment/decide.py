"""Entscheidung aus den Kandidaten: auto, review oder none nach konfigurierbaren Schwellen."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .candidates import Candidate


@dataclass(frozen=True)
class Thresholds:
    """auto_min: Mindestscore fuer die automatische Zuordnung. gap_min: Mindestabstand zum zweiten
    Kandidaten. contradiction_blocks: Widersprueche am besten Kandidaten verhindern auto. review_min:
    unterhalb dieses Scores wird kein Vorschlag gemacht (none)."""

    auto_min: float = 0.85
    gap_min: float = 0.25
    contradiction_blocks: bool = True
    review_min: float = 0.30


@dataclass
class Proposal:
    decision: str  # auto | review | none
    chosen: Candidate | None
    ranked: list[Candidate]
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "chosen_object_id": self.chosen.object_id if self.chosen else None,
            "ranked": [c.to_dict() for c in self.ranked],
            "reasons": list(self.reasons),
        }


def decide(candidates: Sequence[Candidate], thresholds: Thresholds | None = None) -> Proposal:
    """Sortiert die Kandidaten nach Score und entscheidet. Gleichstand oder zu kleiner Abstand ergibt review,
    ein Widerspruch am besten Kandidaten ebenfalls (sofern contradiction_blocks), kein Kandidat ueber
    review_min ergibt none."""
    t = thresholds or Thresholds()
    ranked = sorted((c for c in candidates if c.score > 0), key=lambda c: (-c.score, c.object_number))
    reasons: list[str] = []
    if not ranked:
        return Proposal("none", None, [], ["kein Kandidat mit positivem Score"])
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    gap = top.score - second.score if second else None
    reasons.append(f"bester Kandidat {top.object_number} mit Score {top.score:.2f}")
    if second is not None:
        reasons.append(
            f"zweiter Kandidat {second.object_number} mit Score {second.score:.2f}, Abstand {gap:.2f}"
        )
    reasons.extend(top.hints)
    for c in top.contradictions:
        reasons.append(f"Widerspruch: {c}")
    if top.score < t.review_min:
        reasons.append(f"Score unter review_min {t.review_min:.2f}")
        return Proposal("none", None, ranked, reasons)
    if top.score < t.auto_min:
        reasons.append(f"Score unter auto_min {t.auto_min:.2f}")
        return Proposal("review", top, ranked, reasons)
    if gap is not None and gap < t.gap_min:
        reasons.append(f"Abstand unter gap_min {t.gap_min:.2f}")
        return Proposal("review", top, ranked, reasons)
    if t.contradiction_blocks and top.contradictions:
        reasons.append("Widerspruch verhindert automatische Zuordnung")
        return Proposal("review", top, ranked, reasons)
    reasons.append("automatische Zuordnung")
    return Proposal("auto", top, ranked, reasons)
