"""Entitaetenerkennung Stufe 2 (Fachentwurf E 3.1, 3.2; docs/architektur.md 6.3): regel- und woerterbuchbasiert.

Einheiten ueber einen Variantengenerator aus units.unit_label des Objekts, Personen und Firmen ueber unscharfen
Abgleich gegen owners und tenants des Objekts (rapidfuzz), Betraege, Datum, Zeitraum (Abrechnungs- oder
Wirtschaftsjahr), Objektmarker (eigene Nummer und Adresse, Adressen anderer Objekte als Negativliste).
IBAN erscheinen nur als Maskierungstreffer (letzte vier Stellen, HMAC), nie im Klartext.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz

from apps.drive.naming import transliterate
from apps.objects.units import parse_unit_label

AMOUNT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{3})*,\d{2})\s?(?:EUR|€|Euro)", re.IGNORECASE)
DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
MONTH_YEAR = re.compile(r"\b(0?[1-9]|1[0-2])/(20\d{2})\b")
PERIOD_YEAR = re.compile(
    r"(?i)\b(?P<ctx>Abrechnungsjahr|Wirtschaftsjahr|Abrechnungszeitraum|Wirtschaftsperiode|Jahresabrechnung|"
    r"Wirtschaftsplan|Hausgeld\w*|Einzelabrechnung|Gesamtabrechnung|Betriebskostenabrechnung|Nebenkostenabrechnung|"
    r"R(?:ü|ue)ckst(?:ä|ae)nde?|Forderung(?:en)?|Sonderumlage|Sollstellung)\w*[^\n\d]{0,40}?(?P<year>20\d{2})\b"
)
PERIOD_RANGE = re.compile(r"(\d{1,2}\.\d{1,2}\.(20\d{2}))\s*(?:bis|-|–)\s*(\d{1,2}\.\d{1,2}\.(20\d{2}))")
AMOUNT_CONTEXT = (
    "Nachzahlung",
    "Guthaben",
    "Rückstand",
    "Rueckstand",
    "Hausgeld",
    "Miete",
    "Kaution",
    "Sonderumlage",
    "Vorschuss",
)
PERSON_CONTEXT = re.compile(
    r"(?i)\b(?:Herr|Frau|Herrn|Eheleute|Familie|Eigentümer(?:in)?|Eigentuemer|Mieter(?:in)?|Zahlungspflichtiger|Kontoinhaber)\s*[:]?\s+((?:[A-ZÄÖÜ][\wäöüß-]+\s?){1,3})"
)


@dataclass
class Entity:
    entity_type: str
    page_no: int
    value_text: str
    value_normalized: str | None = None
    char_from: int | None = None
    char_to: int | None = None
    confidence: float = 0.8
    matched_owner_id: int | None = None
    matched_unit_id: int | None = None
    matched_tenant_id: int | None = None
    match_confidence: float | None = None
    iban_last4: str | None = None
    iban_hash: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Gazetteer:
    units: list[tuple[int, str, list[str]]]  # (unit_id, label, Varianten)
    owners: list[tuple[int, str, str]]  # (owner_id, Anzeigename, Typ)
    tenants: list[tuple[int, str, str]]
    own_markers: list[str]
    foreign_markers: dict[str, str]  # Marker -> Objektnummer


def _default_mapping() -> dict[str, str] | None:
    """Praefix-Mapping aus den Seeds (units.type_prefix_mapping), wenn kein Wert aus app_settings uebergeben wird."""
    try:
        from apps.config.store import seeds

        return seeds().get("units.type_prefix_mapping") or None
    except Exception:
        return None


def unit_variants(label: str, mapping: dict[str, str] | None = None) -> list[str]:
    """Varianten wie in E 3.1: WE14, WE 14, WE-14, WE_14, Wohnung 14, Whg. 14, Einheit 14, Nr. mit fuehrender Null."""
    parsed = parse_unit_label(label, mapping or _default_mapping())
    variants = {label.strip()}
    if parsed.number is None:
        return sorted(variants)
    n = parsed.number
    prefixes = {parsed.prefix} if parsed.prefix else set()
    prefix_key = parsed.prefix.replace(" ", "").upper() if parsed.prefix else ""
    if parsed.unit_type == "apartment":
        prefixes |= {"WE", "Wohnung", "Whg.", "Whg", "Einheit", "Sondereigentum Nr.", "Sondereigentum"}
    elif parsed.unit_type == "garage":
        prefixes |= {"Garage", "GA"}
    elif parsed.unit_type == "parking":
        prefixes |= {"Stellplatz", "Stellplatz Nr.", "ST", "STP", "SP"}
    elif parsed.unit_type == "underground_parking":
        prefixes |= {"TG", "Tiefgarage", "Tiefgaragenstellplatz"}
    elif parsed.unit_type == "commercial":
        prefixes |= {"GE", "Gewerbe", "Gewerbeeinheit", "Laden"}
    numbers = {n, n.zfill(2), n.zfill(3)} if len(n) < 3 else {n}
    for p in prefixes:
        for num in numbers:
            for sep in ("", " ", "-", "_"):
                if sep == "" and p.endswith("."):
                    continue
                variants.add(f"{p}{sep}{num}{parsed.suffix}")
    if prefix_key:
        variants.add(f"{prefix_key}{n}{parsed.suffix}")
    return sorted(variants, key=lambda v: (-len(v), v))


def build_gazetteer(obj) -> Gazetteer:
    from apps.config import store
    from apps.objects.models import ManagedObject, Unit
    from apps.parties.models import Owner, OwnerUnitAssignment, Tenant, TenantUnitAssignment

    mapping = store.get("units.type_prefix_mapping", {}) or None
    units = [
        (u.pk, u.unit_label, unit_variants(u.unit_label, mapping)) for u in Unit.active.filter(object=obj)
    ]
    owner_ids = set(OwnerUnitAssignment.active.filter(unit__object=obj).values_list("owner_id", flat=True))
    owners = [(o.pk, str(o), o.type) for o in Owner.active.filter(pk__in=owner_ids)]
    tenant_ids = set(TenantUnitAssignment.active.filter(unit__object=obj).values_list("tenant_id", flat=True))
    tenants = [(t.pk, str(t), t.type) for t in Tenant.active.filter(pk__in=tenant_ids)]
    own = [m for m in [f"{obj.street} {obj.house_number}".strip() if obj.street else "", obj.name or ""] if m]
    foreign: dict[str, str] = {}
    for other in ManagedObject.active.exclude(pk=obj.pk):
        if other.street and other.house_number and other.city:
            foreign[f"{other.street} {other.house_number}".strip()] = other.object_number
    return Gazetteer(units, owners, tenants, own, foreign)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", transliterate(text).casefold().replace("-", " ")).split())


def _find_all(text: str, needle: str, *, word: bool = True):
    pattern = re.compile(
        (r"(?<![\w])" if word else "") + re.escape(needle) + (r"(?![\w])" if word else ""), re.IGNORECASE
    )
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def extract_page(
    text: str,
    page_no: int,
    gaz: Gazetteer,
    hits: list[dict] | None = None,
    *,
    fuzzy_auto: int = 90,
    fuzzy_candidate: int = 78,
) -> list[Entity]:
    out: list[Entity] = []
    if not text:
        text = ""
    # Einheiten (Varianten, exakt)
    for unit_id, label, variants in gaz.units:
        seen_spans: set[tuple[int, int]] = set()
        for v in variants:
            for s, e in _find_all(text, v):
                if any(s >= a and e <= b for a, b in seen_spans):
                    continue
                seen_spans.add((s, e))
                out.append(
                    Entity(
                        "unit_label",
                        page_no,
                        text[s:e],
                        parse_unit_label(label).label_normalized,
                        s,
                        e,
                        0.95,
                        matched_unit_id=unit_id,
                        match_confidence=1.0,
                    )
                )
    # Betraege
    for m in AMOUNT.finditer(text):
        raw = m.group(1)
        try:
            value = Decimal(raw.replace(".", "").replace(",", "."))
        except InvalidOperation:
            continue
        window = text[max(0, m.start() - 60) : m.start()].lower()
        positions = {c: window.rfind(c.lower()) for c in AMOUNT_CONTEXT}
        ctx = max(positions, key=positions.get) if any(v >= 0 for v in positions.values()) else None
        out.append(
            Entity(
                "amount", page_no, m.group(0), f"{value:.2f}", m.start(), m.end(), 0.9, extra={"context": ctx}
            )
        )
    # Datum
    for m in DATE.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12 and 1990 <= y <= 2100:
            out.append(
                Entity("date", page_no, m.group(0), f"{y:04d}-{mo:02d}-{d:02d}", m.start(), m.end(), 0.9)
            )
    # Zeitraum
    for m in PERIOD_RANGE.finditer(text):
        out.append(
            Entity(
                "period",
                page_no,
                m.group(0),
                f"{m.group(2)}-{m.group(4)}" if m.group(2) != m.group(4) else m.group(2),
                m.start(),
                m.end(),
                0.85,
                extra={"kind": "range"},
            )
        )
    for m in MONTH_YEAR.finditer(text):
        out.append(
            Entity(
                "period",
                page_no,
                m.group(0),
                f"{m.group(2)}-{int(m.group(1)):02d}",
                m.start(),
                m.end(),
                0.7,
                extra={"kind": "month"},
            )
        )
    for m in PERIOD_YEAR.finditer(text):
        out.append(
            Entity(
                "period",
                page_no,
                m.group(0),
                m.group("year"),
                m.start(),
                m.end(),
                0.9,
                extra={"kind": "year", "context": m.group("ctx")},
            )
        )
    # Objektmarker
    for marker in gaz.own_markers:
        for s, e in _find_all(text, marker, word=False):
            out.append(Entity("object_number", page_no, text[s:e], "own", s, e, 0.9, extra={"marker": "own"}))
    for marker, number in gaz.foreign_markers.items():
        for s, e in _find_all(text, marker, word=False):
            out.append(
                Entity(
                    "object_number",
                    page_no,
                    text[s:e],
                    number,
                    s,
                    e,
                    0.9,
                    extra={"marker": "foreign", "object_number": number},
                )
            )
    # Personen und Firmen (Kontextmuster plus unscharfer Abgleich gegen Stammdaten des Objekts)
    candidates: list[tuple[str, int, int]] = [
        (m.group(1).strip(), m.start(1), m.end(1)) for m in PERSON_CONTEXT.finditer(text)
    ]
    for _pid, name, ptype in gaz.owners:
        for s, e in _find_all(text, name.split()[-1] if ptype == "natural_person" else name):
            candidates.append((text[s:e], s, e))
    seen: set[tuple[int, int]] = set()
    for cand, s, e in candidates:
        if (s, e) in seen or len(cand) < 3:
            continue
        seen.add((s, e))
        best = None
        for pid, name, ptype in gaz.owners:
            score = (
                fuzz.token_set_ratio(_norm(cand), _norm(name))
                if ptype == "natural_person"
                else fuzz.partial_ratio(_norm(cand), _norm(name))
            )
            if best is None or score > best[1]:
                best = (pid, score, "owner", ptype)
        for pid, name, ptype in gaz.tenants:
            score = (
                fuzz.token_set_ratio(_norm(cand), _norm(name))
                if ptype == "natural_person"
                else fuzz.partial_ratio(_norm(cand), _norm(name))
            )
            if best is None or score > best[1]:
                best = (pid, score, "tenant", ptype)
        etype = "company_name" if best and best[3] != "natural_person" else "person_name"
        ent = Entity(etype, page_no, cand, _norm(cand), s, e, 0.6)
        if best and best[1] >= fuzzy_candidate:
            ent.match_confidence = round(best[1] / 100, 4)
            if best[2] == "owner":
                ent.matched_owner_id = best[0]
            else:
                ent.matched_tenant_id = best[0]
            ent.confidence = 0.9 if best[1] >= fuzzy_auto else 0.7
            ent.extra["match"] = "auto" if best[1] >= fuzzy_auto else "candidate"
        out.append(ent)
    # IBAN aus der Maskierung (nur letzte vier Stellen und HMAC)
    for h in hits or []:
        if h.get("kind") == "iban":
            out.append(
                Entity(
                    "iban",
                    page_no,
                    f"[IBAN_****{h.get('last4') or ''}]",
                    None,
                    h.get("start"),
                    h.get("end"),
                    0.95 if h.get("mod97_valid") else 0.6,
                    iban_last4=h.get("last4"),
                    iban_hash=h.get("hmac"),
                )
            )
        elif h.get("kind") == "ausweisnummer":
            out.append(
                Entity("id_document_number", page_no, "[AUSWEISNR]", None, h.get("start"), h.get("end"), 0.8)
            )
    return out


def match_iban_owners(entities: list[Entity], obj) -> None:
    """IBAN-Treffer ueber den HMAC gegen owners und tenants abgleichen (E 3.1: Klartext nie)."""
    from apps.parties.models import Owner, Tenant

    hashes = {e.iban_hash for e in entities if e.entity_type == "iban" and e.iban_hash}
    if not hashes:
        return
    owners = {o.iban_hash.hex(): o.pk for o in Owner.active.filter(iban_hash__isnull=False) if o.iban_hash}
    tenants = {t.iban_hash.hex(): t.pk for t in Tenant.active.filter(iban_hash__isnull=False) if t.iban_hash}
    for e in entities:
        if e.entity_type == "iban" and e.iban_hash:
            if e.iban_hash in owners:
                e.matched_owner_id = owners[e.iban_hash]
                e.match_confidence = 1.0
            elif e.iban_hash in tenants:
                e.matched_tenant_id = tenants[e.iban_hash]
                e.match_confidence = 1.0
