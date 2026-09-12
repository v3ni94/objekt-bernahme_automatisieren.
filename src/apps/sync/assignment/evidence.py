"""Beleg einer Zuordnungsentscheidung: nachvollziehbar je Signal mit Fundstelle, Gewicht und Rolle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Evidence:
    """Ein Signal fuer oder gegen ein Objekt.

    kind: folder, object_number, filename_number, address, filename_address, rule, management_type,
    party_name, own_address. role: Rolle der Fundstelle (object, neutral, billing, supplier, owner,
    own). weight: Beitrag zum Score (negativ bei Gegenbelegen)."""

    kind: str
    text: str
    page: int | None = None
    position: int | None = None
    weight: float = 0.0
    role: str | None = None
    object_id: int | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["weight"] = round(float(self.weight), 4)
        return data


def evidence_to_dicts(items) -> list[dict]:
    """Serialisiert Belege (Evidence oder bereits dict) fuer JSON-Felder."""
    out: list[dict] = []
    for item in items or ():
        if isinstance(item, Evidence):
            out.append(item.to_dict())
        elif isinstance(item, dict):
            out.append(dict(item))
    return out
