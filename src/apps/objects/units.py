"""Einheitennormalisierung (Fachentwurf H 6.5, D 3.2; Beschluss B-47).

Muster aus dem Befund (Punkt 4), als Codeblock zitiert:

    WEN, WE N, SN, Garage N, N, GE N, MVW N, Stellplatz Nr. N, GAN, TGN, SPN, GA N, GEN, STN,
    Haus N, Container N, Wohnung N, WE N - N.OG rechts/mitte/links, Lagerhalle N, STPN, VEN_SILN_N.L

Ergebnis: Praefix, Ziffernanteil, Buchstabenzusatz, Rest (Lage), Vergleichsschluessel und Einheitentyp
aus dem Praefix-Mapping units.type_prefix_mapping. Keine Nebenwirkungen, Konfiguration wird uebergeben.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_LABEL = re.compile(
    r"^\s*(?P<prefix>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß.\s-]*?)?\s*(?:Nr\.?\s*)?(?P<number>\d+)\s*(?P<suffix>[A-Za-z]?)(?![A-Za-z0-9])"
    r"\s*(?:[-–]\s*(?P<rest>.+?))?\s*$"
)

DEFAULT_PREFIX_MAPPING = {"WE": "apartment"}


@dataclass(frozen=True)
class UnitParse:
    label: str
    prefix: str
    number: str | None
    suffix: str
    rest: str | None
    label_normalized: str
    unit_type: str
    reasons: tuple[str, ...]

    @property
    def parsed(self) -> bool:
        return self.number is not None


def _nfc(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()


def normalize_label(label: str, *, strip_leading_zeros: bool = True) -> str:
    """Vergleichsschluessel nach D 3.2 (Grossschreibung, ohne Leerzeichen), fuehrende Nullen optional entfernt (H 6.5)."""
    parsed = parse_unit_label(label, strip_leading_zeros=strip_leading_zeros)
    return parsed.label_normalized


def prefix_key(prefix: str) -> str:
    return re.sub(r"[\s.\-]", "", prefix or "").upper()


def parse_unit_label(
    label: str,
    mapping: dict[str, str] | None = None,
    *,
    strip_leading_zeros: bool = True,
    default_type_without_prefix: str = "apartment",
) -> UnitParse:
    mapping = {prefix_key(k): v for k, v in (mapping or DEFAULT_PREFIX_MAPPING).items()}
    clean = _nfc(label)
    match = _LABEL.match(clean)
    if not match:
        normalized = re.sub(r"\s+", "", clean).upper()
        return UnitParse(clean, "", None, "", None, normalized, "other", ("unit_label_unparsed",))
    prefix = (match.group("prefix") or "").strip(" .-")
    key = prefix_key(prefix)
    number = match.group("number")
    if strip_leading_zeros:
        number = number.lstrip("0") or "0"
    suffix = (match.group("suffix") or "").lower()
    rest = (match.group("rest") or "").strip() or None
    if rest:
        # Lagezusatz gehoert nach units.location; die Bezeichnung endet vor dem Trennstrich (H 6.5)
        clean = clean[: match.start("rest")].rstrip(" -–")
    reasons: list[str] = []
    if not key:
        unit_type = default_type_without_prefix
        reasons.append("prefix_missing")
    elif key in mapping:
        unit_type = mapping[key]
        if unit_type == "other":
            reasons.append("prefix_other")
    else:
        unit_type = "other"
        reasons.append("prefix_unknown")
    normalized = f"{key}{number}{suffix.upper()}"
    return UnitParse(clean, prefix, number, suffix, rest, normalized, unit_type, tuple(reasons))


def same_unit(label_a: str, label_b: str, *, strip_leading_zeros: bool = True) -> bool:
    return normalize_label(label_a, strip_leading_zeros=strip_leading_zeros) == normalize_label(
        label_b, strip_leading_zeros=strip_leading_zeros
    )
