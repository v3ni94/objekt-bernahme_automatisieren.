"""Kopfzeilenerkennung und Spaltenzuordnung mit Vorschlag (Fachentwurf H 6.3, Umsetzungsplan M3 Schritt 3).

Score je Quellspalte aus Ueberschrift (exakter, normalisierter oder unscharfer Treffer im Synonymwoerterbuch
import.column_synonyms) und Inhalt (Musterpruefung der ersten Zeilen). Ein Zielfeld wird nur einmal vergeben,
Ausnahme notes. Nicht zugeordnete Spalten bleiben in raw_data erhalten.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from apps.imports import normalize as n
from apps.imports.profiles import UNIT_LIKE

# Zielschema (H 6.3): Code, Bezeichnung, Inhaltsmuster
TARGET_FIELDS: dict[str, str] = {
    "unit_label": "Einheit",
    "external_ref": "Fremdkennung (VE-Nr)",
    "unit_status": "Status der Einheit (Quelle)",
    "building": "Gebäude",
    "location": "Lage",
    "object_number": "Objektnummer (Filter)",
    "object_name": "Objektbezeichnung (Abgleich)",
    "management_type": "Verwaltungsart (Abgleich)",
    "owner_name_raw": "Eigentümer (Freitext)",
    "tenant_name_raw": "Mieter (Freitext)",
    "salutation": "Anrede",
    "first_name": "Vorname",
    "last_name": "Nachname",
    "company_name": "Firma",
    "street": "Straße (mit oder ohne Hausnummer)",
    "house_number": "Hausnummer",
    "postal_code": "PLZ",
    "city": "Ort",
    "country": "Land",
    "delivery_address_raw": "Abweichende Zustelladresse",
    "phone": "Telefon",
    "mobile": "Mobil",
    "email": "E-Mail",
    "co_ownership_share": "Miteigentumsanteil",
    "co_ownership_share_base": "Miteigentumsanteil Nenner",
    "valid_from": "Eigentumsbeginn",
    "valid_to": "Eigentumsende",
    "house_fee_monthly": "Hausgeld monatlich",
    "sepa_mandate_present": "SEPA-Mandat vorhanden",
    "iban_raw": "IBAN (wird maskiert)",
    "mandate_reference": "Mandatsreferenz",
    "notes": "Bemerkung",
    "base_rent": "Kaltmiete",
    "utilities_prepayment": "Nebenkostenvorauszahlung",
    "heating_prepayment": "Heizkostenvorauszahlung",
    "deposit_amount": "Kaution Betrag",
    "deposit_type": "Kaution Anlageform",
    "rent_adjustment_type": "Staffel oder Index",
    "persons_count": "Anzahl Personen",
    "start_date": "Mietbeginn",
    "end_date": "Mietende",
}
MULTI_ASSIGNABLE = {"notes"}
IBAN_PATTERN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9 ]{11,34}$")
FRACTION = re.compile(r"^\s*[\d.,]+\s*/\s*[\d.,]+\s*$")


def norm_header(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("ß", "ss"))


@dataclass
class ColumnProposal:
    source_index: int
    source_header: str
    target: str | None
    confidence: float
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source_index": self.source_index,
            "source_header": self.source_header,
            "target": self.target,
            "confidence": round(self.confidence, 2),
            "alternatives": [[t, round(s, 2)] for t, s in self.alternatives[:3]],
            "samples": self.samples[:3],
        }


def _synonym_index(synonyms: dict[str, list[str]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for target, words in synonyms.items():
        for w in [target, *words]:
            index.setdefault(norm_header(w), target)
    return index


def header_row_index(rows: list[list[str]], synonyms: dict[str, list[str]], min_hits: int = 3) -> int:
    """Erste Zeile mit mindestens min_hits Treffern im Synonymwoerterbuch (H 6.2), sonst erste nicht leere Zeile."""
    index = _synonym_index(synonyms)
    best, best_hits = None, 0
    for i, row in enumerate(rows[:30]):
        hits = sum(1 for c in row if norm_header(c) in index)
        if hits >= min_hits and hits > best_hits:
            best, best_hits = i, hits
    if best is not None:
        return best
    for i, row in enumerate(rows):
        if any((c or "").strip() for c in row):
            return i
    return 0


def content_scores(samples: list[str]) -> dict[str, float]:
    values = [s for s in samples if s and s.strip()]
    if not values:
        return {}
    total = len(values)

    def share(pred) -> float:
        return sum(1 for v in values if pred(v)) / total

    scores = {
        "unit_label": share(lambda v: bool(UNIT_LIKE.match(v))),
        "postal_code": share(lambda v: bool(re.fullmatch(r"\d{5}", v.strip()))),
        "email": share(lambda v: bool(n.EMAIL.match(v.strip()))),
        "iban_raw": share(lambda v: bool(IBAN_PATTERN.match(v.replace(" ", "").upper())) and n.iban_mod97(v)),
        "co_ownership_share": share(lambda v: bool(FRACTION.match(v))),
        "valid_from": share(lambda v: n.parse_date(v).ok and n.parse_date(v).confidence >= 0.9),
        "phone": share(lambda v: bool(re.fullmatch(r"[+\d][\d\s/()-]{6,}", v.strip()))),
        "house_fee_monthly": share(lambda v: bool(re.fullmatch(r"-?[\d.]+,\d{2}", v.strip()))),
        "street": share(lambda v: bool(n.HOUSE_NUMBER.match(v)) and not v.strip().isdigit()),
    }
    return {k: v * 0.7 for k, v in scores.items() if v >= 0.6}


def propose_mapping(
    header: list[str],
    sample_rows: list[list[str]],
    synonyms: dict[str, list[str]],
    *,
    confidence_min: float = 0.8,
) -> list[ColumnProposal]:
    index = _synonym_index(synonyms)
    proposals: list[ColumnProposal] = []
    for i, raw_head in enumerate(header):
        head = norm_header(raw_head)
        samples = [row[i] for row in sample_rows if i < len(row)]
        candidates: dict[str, float] = {}
        if head and head in index:
            candidates[index[head]] = 1.0
        elif head:
            for word, target in index.items():
                if not word:
                    continue
                ratio = fuzz.ratio(head, word) / 100
                if ratio >= 0.8:
                    candidates[target] = max(candidates.get(target, 0), ratio * 0.9)
        for target, score in content_scores(samples).items():
            candidates[target] = max(candidates.get(target, 0), score)
        ranked = sorted(candidates.items(), key=lambda kv: -kv[1])
        target, conf = ranked[0] if ranked else (None, 0.0)
        proposals.append(ColumnProposal(i, raw_head, target, conf, ranked[1:], samples[:3]))
    # Zielfeld nur einmal vergeben (Ausnahme notes): hoechste Konfidenz gewinnt, Verlierer bekommen die naechste Alternative
    taken: dict[str, ColumnProposal] = {}
    for p in sorted(proposals, key=lambda p: -p.confidence):
        while p.target and p.target not in MULTI_ASSIGNABLE and p.target in taken:
            p.target, p.confidence = p.alternatives.pop(0) if p.alternatives else (None, 0.0)
        if p.target:
            taken.setdefault(p.target, p)
    for p in proposals:
        if p.target and p.confidence < confidence_min:
            p.alternatives = p.alternatives  # bleibt Vorschlag, Oberflaeche markiert gelb
    return proposals


def mapping_from_proposals(proposals: list[ColumnProposal]) -> dict:
    """column_mapping fuer import_batches: Quellindex zu Zielfeld mit Konfidenz und Ueberschrift."""
    return {
        "columns": [p.as_dict() for p in proposals],
        "targets": {p.target: p.source_index for p in proposals if p.target},
    }


def apply_mapping_overrides(mapping: dict, overrides: dict[int, str | None]) -> dict:
    """Manuelle Korrektur aus der Oberflaeche: Quellindex zu Zielfeld (None hebt die Zuordnung auf)."""
    cols = mapping.get("columns", [])
    for col in cols:
        idx = col["source_index"]
        if idx in overrides:
            col["target"] = overrides[idx] or None
            col["confidence"] = 1.0 if overrides[idx] else 0.0
            col["manual"] = True
    seen: set[str] = set()
    for col in cols:
        t = col.get("target")
        if t and t not in MULTI_ASSIGNABLE:
            if t in seen:
                raise ValueError(f"Zielfeld {TARGET_FIELDS.get(t, t)} ist mehrfach zugeordnet")
            seen.add(t)
    mapping["targets"] = {c["target"]: c["source_index"] for c in cols if c.get("target")}
    return mapping
