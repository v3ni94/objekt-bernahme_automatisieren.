"""Kandidaten der Objektzuordnung: Index ueber die Objekte, Signale mit Gewichten, Score und Widersprueche.

Signale (Gewichte in WEIGHTS, nachvollziehbar je Beleg): bekannter Drive-Objektordner (stark), Objektnummer
mit Kontextwort im Text (stark), Objektadresse im Text mit Rolle (Leistungsort oder Objektadresse stark,
neutral mittel, Rechnungsempfaenger schwach, Lieferant negativ, eigene Anschrift kein Kandidat), Adresse
oder Objektnummer im Dateinamen (mittel), bestaetigte Regeln aus der Lernfunktion (stark), Verwaltungsart
und Namen (schwach). Der Score ist die Noisy-Or-Verknuepfung der positiven Gewichte abzueglich der
Gegenbelege. Kein Modul dieses Pakets ausser learning greift auf die Datenbank zu."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .evidence import Evidence
from .normalize import (
    AddressEntry,
    AddressHit,
    NormalizedText,
    build_entries,
    find_addresses,
    normalize_text,
    parse_own_address,
    split_pages,
)

WEIGHTS: dict[str, float] = {
    "folder": 0.88,
    "object_number": 0.85,
    "filename_number": 0.50,
    "address_object": 0.88,
    "address_neutral": 0.80,
    "address_billing": 0.30,
    "address_owner": 0.15,
    "address_supplier": -0.20,
    "filename_address": 0.60,
    "location_postal": 0.15,
    "location_city": 0.15,
    "repeat_hit": 0.05,
    "rule": 0.88,
    "management_type": 0.05,
    "party_name": 0.10,
}
AMBIGUOUS_LOCATION_FACTOR = 0.5
SAME_ADDRESS_FOLDER_FACTOR = 0.5
MAX_REPEAT_BONUS = 2
# Standardanschrift der Hausverwaltung, falls weder Konfiguration noch Parameter eine liefern
DEFAULT_OWN_ADDRESSES: tuple[str, ...] = ("Rheinpromenade 13, 40789 Monheim am Rhein",)
OWN_ADDRESS_KEYS = ("company.street", "company.house_number", "company.postal_code", "company.city")

OBJECT_NUMBER_WORDS = (
    r"objektnummer|objekt-?nr\.?|objekt nr\.?|verwaltungsobjekt|verw\.-?objekt|liegenschafts-?nr\.?|"
    r"liegenschaftsnummer|liegenschaft|objekt|obj\."
)
OBJECT_NUMBER = re.compile(
    rf"(?<![a-z])(?:{OBJECT_NUMBER_WORDS})\s*(?:nr\.?|nummer|no\.?|#)?\s*[:.]?\s*(?P<num>\d{{1,8}})"
    r"(?!\d)(?!\s*(?:wohnungen|einheiten|whg|we\b|%|eur|€|,\d))"
)
FILENAME_NUMBER = re.compile(r"^\s*(?P<num>\d{1,8})(?=[\s_\-.])")
MANAGEMENT_WORDS: dict[str, tuple[str, ...]] = {
    "weg": (
        "wohnungseigentuemergemeinschaft",
        "wohnungseigentuemer",
        "eigentuemergemeinschaft",
        "hausgeld",
        "eigentuemerversammlung",
        "teilungserklaerung",
        "gemeinschaftseigentum",
        "verwalterbestellung",
    ),
    "rental": (
        "mietvertrag",
        "kaltmiete",
        "nettokaltmiete",
        "mietzahlung",
        "mieterhoehung",
        "mietverhaeltnis",
    ),
}
MANAGEMENT_SHORT = {
    "weg": re.compile(r"(?<![a-z])weg(?![a-z])"),
    "rental": re.compile(r"(?<![a-z])mieter(?:in)?(?![a-z])"),
}
MANAGEMENT_GROUP = {"weg": "weg", "weg_with_se": "weg", "rental": "rental"}
NUMBER_KINDS: dict[str, tuple[str, ...]] = {
    "contract": (
        "vertragsnummer",
        "vertrags-nr",
        "vertragsnr",
        "vertrag nr",
        "vertragskontonummer",
        "vertragskonto",
    ),
    "customer": ("kundennummer", "kunden-nr", "kundennr", "kunde nr", "kd-nr", "kd.-nr", "kundenkonto"),
    "meter": ("zaehlernummer", "zaehler-nr", "zaehlernr", "zaehler nr", "zaehlernummern"),
    "insurance": (
        "versicherungsscheinnummer",
        "versicherungsschein-nr",
        "versicherungsnummer",
        "versicherungs-nr",
        "policennummer",
        "police nr",
        "vs-nr",
    ),
    "property": ("liegenschaftsnummer", "liegenschafts-nr", "liegenschaftsnr"),
}
NUMBER_VALUE = r"(?P<val>[a-z]{0,4}[\-/]?\d(?:[a-z0-9\-/.]|\s(?=\d)){2,30})"
NUMBER_PATTERNS = {
    kind: re.compile(
        rf"(?<![a-z])(?:{'|'.join(re.escape(w) for w in words)})\.?\s*[:.]?\s*{NUMBER_VALUE}(?![a-z0-9])"
    )
    for kind, words in NUMBER_KINDS.items()
}
HINT_SAME_ADDRESS = "gleiche Anschrift, Verwaltungsart aus dem Text nicht ableitbar"


@dataclass(frozen=True)
class IndexedObject:
    object_id: int
    object_number: str
    object_number_numeric: int | None
    street: str | None
    house_number: str | None
    postal_code: str | None
    city: str | None
    name: str | None
    management_type: str | None
    drive_root_folder_id: str | None
    address_key: str | None


@dataclass
class ObjectIndex:
    objects: dict[int, IndexedObject] = field(default_factory=dict)
    entries: list[AddressEntry] = field(default_factory=list)
    by_number: dict[int, list[int]] = field(default_factory=dict)
    by_folder: dict[str, int] = field(default_factory=dict)
    by_address_key: dict[str, list[int]] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.objects)


def _numeric(obj) -> int | None:
    value = getattr(obj, "object_number_numeric", None)
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    digits = re.sub(r"\D", "", str(getattr(obj, "object_number", "") or ""))
    return int(digits) if digits else None


def build_index(objects: Iterable) -> ObjectIndex:
    """Index ueber ManagedObject-artige Objekte (object_number, object_number_numeric, street, house_number,
    postal_code, city, name, management_type, drive_root_folder_id). Historische Anschriften gibt es im
    Datenmodell nicht; sie sind nicht enthalten."""
    index = ObjectIndex()
    objs = list(objects)
    index.entries = build_entries(objs)
    by_id = {e.object_id: e for e in index.entries}
    for obj in objs:
        pk = getattr(obj, "pk", None) or getattr(obj, "id", None)
        if pk is None:
            continue
        entry = by_id.get(pk)
        item = IndexedObject(
            object_id=pk,
            object_number=str(getattr(obj, "object_number", "") or ""),
            object_number_numeric=_numeric(obj),
            street=getattr(obj, "street", None),
            house_number=getattr(obj, "house_number", None),
            postal_code=getattr(obj, "postal_code", None),
            city=getattr(obj, "city", None),
            name=getattr(obj, "name", None),
            management_type=getattr(obj, "management_type", None),
            drive_root_folder_id=getattr(obj, "drive_root_folder_id", None),
            address_key=entry.address_key if entry else None,
        )
        index.objects[pk] = item
        if item.object_number_numeric is not None:
            index.by_number.setdefault(item.object_number_numeric, []).append(pk)
        if item.drive_root_folder_id:
            index.by_folder[item.drive_root_folder_id] = pk
        if item.address_key:
            index.by_address_key.setdefault(item.address_key, []).append(pk)
    return index


def resolve_own_addresses(own_addresses: Iterable | None = None) -> list[AddressEntry]:
    """Eigene Anschriften: aus der Konfiguration (company.*-Schluessel, falls im Katalog vorhanden), sonst aus
    dem Parameter, sonst die Standardanschrift der Hausverwaltung."""
    if own_addresses is not None:
        entries = [e for e in (parse_own_address(a) for a in own_addresses) if e is not None]
        return entries
    from_store = _own_address_from_store()
    if from_store is not None:
        return [from_store]
    return [e for e in (parse_own_address(a) for a in DEFAULT_OWN_ADDRESSES) if e is not None]


def _own_address_from_store() -> AddressEntry | None:
    try:
        from apps.config import store
    except Exception:  # noqa: BLE001 - ohne Django-Umgebung keine Konfiguration
        return None
    values = []
    for key in OWN_ADDRESS_KEYS:
        try:
            values.append(store.get(key))
        except Exception:  # noqa: BLE001 - Schluessel nicht im Katalog oder keine Datenbank
            return None
    if not values[0]:
        return None
    return parse_own_address(tuple(values))


@dataclass
class Candidate:
    object_id: int
    object_number: str
    score: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    rule_hits: list[dict] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    address_key: str | None = None
    management_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "object_number": self.object_number,
            "score": round(self.score, 4),
            "evidence": [e.to_dict() for e in self.evidence],
            "contradictions": list(self.contradictions),
            "rule_hits": list(self.rule_hits),
            "hints": list(self.hints),
        }

    @property
    def has_strong_address(self) -> bool:
        return any(e.kind == "address" and e.details.get("strong") for e in self.evidence)


def combine(weights: Iterable[float]) -> float:
    """Noisy-Or der positiven Gewichte abzueglich der Summe der negativen, begrenzt auf 0 bis 1."""
    miss = 1.0
    penalty = 0.0
    for w in weights:
        if w > 0:
            miss *= 1.0 - min(w, 1.0)
        elif w < 0:
            penalty += -w
    return max(0.0, min(1.0, (1.0 - miss) - penalty))


def address_weight(hit: AddressHit) -> tuple[float, bool]:
    """Gewicht einer Adressfundstelle nach Rolle und Ortsbestaetigung sowie ob sie als klarer Objektbezug gilt."""
    role = hit.role
    if role == "object":
        base = WEIGHTS["address_object"]
    elif role == "billing":
        base = WEIGHTS["address_billing"]
    elif role == "supplier":
        return WEIGHTS["address_supplier"], False
    elif role == "owner":
        base = WEIGHTS["address_owner"]
    else:
        base = WEIGHTS["address_neutral"]
    if hit.ambiguous_location:
        base *= AMBIGUOUS_LOCATION_FACTOR
    extra = []
    if hit.postal_match:
        extra.append(WEIGHTS["location_postal"])
    if hit.city_match:
        extra.append(WEIGHTS["location_city"])
    weight = combine([base, *extra])
    strong = role == "object" or (role == "neutral" and hit.postal_match and hit.city_match)
    return weight, strong and not hit.ambiguous_location


def extract_numbers(text: str) -> list[dict]:
    """Nummernpaare mit Kontextwort (Vertrags-, Kunden-, Zaehler-, Versicherungs-, Liegenschaftsnummer)."""
    norm = normalize_text(text)
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in NUMBER_PATTERNS.items():
        for m in pattern.finditer(norm):
            value = normalize_number(m.group("val"))
            if len(value) < 3 or (kind, value) in seen:
                continue
            seen.add((kind, value))
            found.append({"kind": kind, "value": value, "context": m.group(0)[:60], "position": m.start()})
    return found


def normalize_number(value: str | None) -> str:
    """Vergleichsform einer Nummer: nur Kleinbuchstaben und Ziffern."""
    return re.sub(r"[^a-z0-9]", "", normalize_text(value))


def management_words(norm_text: str) -> dict[str, list[str]]:
    """Klare Woerter je Verwaltungsart im normalisierten Text (schwaches Signal)."""
    found: dict[str, list[str]] = {}
    for group, words in MANAGEMENT_WORDS.items():
        hits = [w for w in words if w in norm_text]
        if MANAGEMENT_SHORT[group].search(norm_text):
            hits.append("weg" if group == "weg" else "mieter")
        if hits:
            found[group] = hits
    return found


def extract_features(
    text: str | Sequence[str] | None,
    *,
    filename: str | None = None,
    correspondent: str | None = None,
    folder_object_id: int | None = None,
    drive_folder_id: str | None = None,
    index: ObjectIndex | None = None,
    hits: Sequence[AddressHit] | None = None,
) -> dict:
    """Merkmale eines Dokuments fuer Lernbeispiele und Regeln: normalisierte Adressen mit Rolle, Lieferant
    oder Korrespondent, Nummernpaare mit Kontextwort, Drive-Ordner und Dateiname."""
    pages = split_pages(text)
    if hits is None:
        hits = find_addresses(pages, index.entries) if index is not None else []
    joined = "\f".join(pages)
    return {
        "addresses": [
            {"key": h.entry.address_key, "role": h.role, "object_id": h.object_id, "page": h.page}
            for h in hits
        ],
        "supplier_norm": normalize_text(correspondent) or None,
        "numbers": extract_numbers(joined),
        "drive_folder_id": drive_folder_id,
        "folder_object_id": folder_object_id,
        "filename": filename,
        "management_words": management_words(normalize_text(joined)),
    }


def apply_rules(features: dict, rules: Iterable) -> list[dict]:
    """Prueft bestaetigte Regeln (kind feature_combo: supplier_norm plus Nummernart und Nummer) gegen die
    Merkmale. Liefert Treffer mit Regel-ID, Code, Version und Zielobjekt."""
    supplier = features.get("supplier_norm")
    numbers = {(n.get("kind"), n.get("value")) for n in features.get("numbers") or ()}
    hits: list[dict] = []
    for rule in rules or ():
        if not getattr(rule, "is_active", True):
            continue
        conditions = getattr(rule, "conditions", None) or {}
        if getattr(rule, "kind", "feature_combo") != "feature_combo":
            continue
        want_supplier = conditions.get("supplier_norm")
        pair = (conditions.get("number_kind"), normalize_number(conditions.get("number")))
        if not want_supplier or not pair[1]:
            continue
        if supplier != want_supplier or pair not in numbers:
            continue
        hits.append(
            {
                "rule_id": getattr(rule, "pk", None) or getattr(rule, "id", None),
                "code": getattr(rule, "code", None),
                "version": getattr(rule, "version", None),
                "object_id": getattr(rule, "object_id", None),
                "matched": {"supplier_norm": supplier, "number_kind": pair[0], "number": pair[1]},
            }
        )
    return hits


def score_candidates(
    text: str | Sequence[str] | None,
    *,
    filename: str | None = None,
    index: ObjectIndex,
    folder_object_id: int | None = None,
    own_addresses: Iterable | None = None,
    extra_hints: dict | None = None,
    rules: Iterable = (),
) -> list[Candidate]:
    """Bewertet alle Objekte des Index fuer ein Dokument.

    text: maskierter Volltext (Zeichenkette mit Seitenvorschub oder Liste der Seiten). filename: Dateiname.
    folder_object_id: Objekt des Drive-Ordners, in dem die Datei liegt. own_addresses: eigene Anschriften
    (None: Konfiguration oder Standard). extra_hints: title, correspondent (Paperless), drive_folder_id,
    party_names {object_id: [Namen]}. rules: aktive AssignmentRule-Objekte. Liefert Kandidaten mit Score,
    Belegen, Widerspruechen und Regeltreffern, absteigend sortiert."""
    hints = dict(extra_hints or {})
    pages = split_pages(text)
    nt = NormalizedText(pages)
    own_entries = resolve_own_addresses(own_addresses)
    hits = find_addresses(nt, [*index.entries, *own_entries])
    candidates: dict[int, Candidate] = {}

    def cand(object_id: int) -> Candidate:
        if object_id not in candidates:
            item = index.objects.get(object_id)
            candidates[object_id] = Candidate(
                object_id=object_id,
                object_number=item.object_number if item else "",
                address_key=item.address_key if item else None,
                management_type=item.management_type if item else None,
            )
        return candidates[object_id]

    # Drive-Ordner
    if folder_object_id is None and hints.get("drive_folder_id"):
        folder_object_id = index.by_folder.get(hints["drive_folder_id"])
    if folder_object_id is not None and folder_object_id in index.objects:
        cand(folder_object_id).evidence.append(
            Evidence(
                kind="folder",
                text=hints.get("drive_folder_id") or "Drive-Objektordner",
                weight=WEIGHTS["folder"],
                role="folder",
                object_id=folder_object_id,
            )
        )

    # Adressen im Text
    per_object_hits: dict[int, list[AddressHit]] = {}
    for hit in hits:
        if hit.object_id is None:
            continue
        per_object_hits.setdefault(hit.object_id, []).append(hit)
    for object_id, obj_hits in per_object_hits.items():
        c = cand(object_id)
        weighted = sorted(((address_weight(h), h) for h in obj_hits), key=lambda t: -t[0][0])
        for i, ((weight, strong), h) in enumerate(weighted):
            if i == 0:
                w = weight
            elif weight > 0 and i <= MAX_REPEAT_BONUS:
                w = WEIGHTS["repeat_hit"]
            elif weight < 0:
                w = 0.0
            else:
                w = 0.0
            c.evidence.append(
                Evidence(
                    kind="address",
                    text=h.matched_text,
                    page=h.page,
                    position=h.start,
                    weight=w,
                    role=h.role,
                    object_id=object_id,
                    details={
                        "role_word": h.role_word,
                        "postal_match": h.postal_match,
                        "city_match": h.city_match,
                        "ambiguous_location": h.ambiguous_location,
                        "strong": strong,
                        "notes": list(h.notes),
                    },
                )
            )

    # Objektnummer mit Kontextwort im Text
    for m in OBJECT_NUMBER.finditer(nt.text):
        number = int(m.group("num"))
        for object_id in index.by_number.get(number, ()):
            c = cand(object_id)
            if any(e.kind == "object_number" for e in c.evidence):
                continue
            c.evidence.append(
                Evidence(
                    kind="object_number",
                    text=m.group(0),
                    page=nt.page_of(m.start()),
                    position=m.start(),
                    weight=WEIGHTS["object_number"],
                    role="object",
                    object_id=object_id,
                )
            )

    # Dateiname und Paperless-Titel
    for source, value in (("filename", filename), ("title", hints.get("title"))):
        if not value:
            continue
        fm = FILENAME_NUMBER.match(value)
        if fm:
            for object_id in index.by_number.get(int(fm.group("num")), ()):
                cand(object_id).evidence.append(
                    Evidence(
                        kind="filename_number",
                        text=value,
                        weight=WEIGHTS["filename_number"],
                        role=source,
                        object_id=object_id,
                    )
                )
        for h in find_addresses([value], index.entries):
            if h.object_id is None:
                continue
            factor = AMBIGUOUS_LOCATION_FACTOR if h.ambiguous_location else 1.0
            cand(h.object_id).evidence.append(
                Evidence(
                    kind="filename_address",
                    text=h.matched_text,
                    weight=WEIGHTS["filename_address"] * factor,
                    role=source,
                    object_id=h.object_id,
                    details={"ambiguous_location": h.ambiguous_location},
                )
            )

    # Regeln aus der Lernfunktion
    features = extract_features(
        pages,
        filename=filename,
        correspondent=hints.get("correspondent"),
        folder_object_id=folder_object_id,
        drive_folder_id=hints.get("drive_folder_id"),
        index=index,
        hits=[h for h in hits if h.object_id is not None],
    )
    for rule_hit in apply_rules(features, rules):
        object_id = rule_hit.get("object_id")
        if object_id not in index.objects:
            continue
        c = cand(object_id)
        c.rule_hits.append(rule_hit)
        c.evidence.append(
            Evidence(
                kind="rule",
                text=f"Regel {rule_hit.get('code')} v{rule_hit.get('version')}",
                weight=WEIGHTS["rule"],
                role="rule",
                object_id=object_id,
                details=rule_hit.get("matched", {}),
            )
        )

    # Schwache Signale: Verwaltungsart, Namen
    mgmt = features.get("management_words") or {}
    for c in candidates.values():
        group = MANAGEMENT_GROUP.get(c.management_type or "")
        if group and group in mgmt:
            c.evidence.append(
                Evidence(
                    kind="management_type",
                    text=", ".join(mgmt[group][:3]),
                    weight=WEIGHTS["management_type"],
                    role="text",
                    object_id=c.object_id,
                )
            )
    for object_id, names in (hints.get("party_names") or {}).items():
        try:
            object_id = int(object_id)
        except (TypeError, ValueError):
            continue
        if object_id not in index.objects:
            continue
        for name in names or ():
            name_n = normalize_text(name)
            if len(name_n) >= 4 and name_n in nt.text:
                cand(object_id).evidence.append(
                    Evidence(
                        kind="party_name",
                        text=name,
                        weight=WEIGHTS["party_name"],
                        role="text",
                        object_id=object_id,
                    )
                )
                break

    _mark_contradictions(candidates, index, folder_object_id, mgmt)
    for c in candidates.values():
        c.score = combine(e.weight for e in c.evidence)
    return sorted(candidates.values(), key=lambda c: (-c.score, c.object_number))


def _mark_contradictions(
    candidates: dict[int, Candidate], index: ObjectIndex, folder_object_id: int | None, mgmt: dict
) -> None:
    """Widersprueche und Hinweise: mehrere klare Leistungsadressen verschiedener Anschrift, Ordner- oder
    Objektnummernbezug gegen eine klare Leistungsadresse eines anderen Objekts, gleiche Anschrift (WEG
    und SEV)."""
    strong = {oid: c for oid, c in candidates.items() if c.has_strong_address}
    anchored = {
        oid: kind
        for oid, c in candidates.items()
        for kind in ("folder", "object_number")
        if any(e.kind == kind for e in c.evidence)
    }
    labels = {"folder": "Ordnerobjekt", "object_number": "Objektnummer"}
    for oid, c in strong.items():
        for other_id, other in strong.items():
            if other_id != oid and other.address_key != c.address_key:
                c.contradictions.append(
                    f"weitere Leistungsadresse fuer Objekt {other.object_number} gefunden"
                )
    for oid, kind in anchored.items():
        c = candidates[oid]
        for other_id, other in strong.items():
            if other_id == oid or other.address_key == c.address_key:
                continue
            c.contradictions.append(
                f"{labels[kind]} widerspricht Leistungsadresse Objekt {other.object_number}"
            )
            other.contradictions.append(
                f"Leistungsadresse widerspricht {labels[kind]} Objekt {c.object_number}"
            )
    # gleiche Anschrift: nur die Adresse unterscheidet die Objekte nicht
    groups: dict[str, list[Candidate]] = {}
    for c in candidates.values():
        if c.address_key and any(e.kind in ("address", "filename_address") for e in c.evidence):
            groups.setdefault(c.address_key, []).append(c)
    for group in groups.values():
        if len(group) < 2:
            continue
        anchored_in_group = [c for c in group if c.object_id in anchored]
        if anchored_in_group:
            for c in group:
                if c.object_id in anchored:
                    continue
                for e in c.evidence:
                    if e.kind in ("address", "filename_address"):
                        e.weight *= SAME_ADDRESS_FOLDER_FACTOR
                c.hints.append(
                    f"gleiche Anschrift, {labels[anchored[anchored_in_group[0].object_id]]} spricht fuer Objekt "
                    f"{anchored_in_group[0].object_number}"
                )
            continue
        for c in group:
            if mgmt:
                words = ", ".join(sorted(mgmt))
                c.hints.append(f"gleiche Anschrift, Verwaltungsart aus dem Text nur schwach ({words})")
            else:
                c.hints.append(HINT_SAME_ADDRESS)
