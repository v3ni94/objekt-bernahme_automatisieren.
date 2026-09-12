"""Normalisierung von Text, Strassen und Hausnummern sowie Adresssuche im Volltext (Baustein B).

Regeln: Kleinschreibung, Umlaute und ss/ß, Strasse/Str./Straße auf "str", Hausnummern mit Zusatz (12a, 12 a),
Bereiche (12-14) und mehreren Eingaengen (12/14), uebliche OCR-Verwechslungen (0/O, 1/l/I, 5/S, 8/B) nur im
Ziffernkontext. Unterschiede wie 12, 12a, 12b bleiben erhalten. Die erste gefundene Adresse ist nicht
automatisch das Objekt: jede Fundstelle traegt eine Rolle aus dem Kontextfenster (Leistungsort, Rechnung,
Absender, Kopfzeile, eigene Anschrift)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a"})
DASHES = str.maketrans({"–": "-", "—": "-", "‐": "-", "‑": "-", " ": " ", " ": " "})
STREET_SUFFIX = re.compile(r"(?<=[a-z])[\s\-]*(?:strasse|str\.?|straße)(?![a-z])")
STREET_WORD = re.compile(r"\b(?:strasse|str\.?)(?![a-z])")
PUNCT = re.compile(r"[^a-z0-9\-/ ]+")
SPACES = re.compile(r"\s+")
# Hausnummer: Nummer, optionaler Zusatz, optionaler Bereich oder zweiter Eingang
HOUSE_NUMBER = re.compile(
    r"^(?P<num>\d{1,4})\s*(?P<suffix>[a-z]?)(?:\s*[-/]\s*(?P<to>\d{1,4})\s*(?P<to_suffix>[a-z]?))?$"
)
# Zeichen, die OCR im Ziffernkontext verwechselt (nach Kleinschreibung)
OCR_DIGITS = {"o": "0", "l": "1", "i": "1", "s": "5", "b": "8"}
OCR_TRAILING = {"o": "0"}  # nur Zeichen, die als Hausnummernzusatz nicht vorkommen
HOUSE_NUMBER_PART = (
    r"(?:(?:\d|[loi](?=\d))[0-9a-z]{0,4}|[lo](?![a-z0-9]))(?:\s?[a-z](?![a-z0-9]))?"
    r"(?:\s*[-/]\s*\d[0-9a-z]{0,4}(?:\s?[a-z](?![a-z0-9]))?)?"
)

# Rollenwoerter im Kontextfenster vor der Adresse (normalisierte Schreibweise)
ROLE_WORDS: dict[str, tuple[str, ...]] = {
    "object": (
        "leistungsort",
        "leistungsadresse",
        "objektadresse",
        "objektanschrift",
        "verwaltungsobjekt",
        "liegenschaft",
        "objekt",
        "obj.",
        "lieferanschrift",
        "lieferadresse",
        "lieferstelle",
        "verbrauchsstelle",
        "abnahmestelle",
        "entnahmestelle",
        "ausfuehrungsort",
        "einsatzort",
        "baustelle",
        "bauvorhaben",
        "gebaeude",
        "wohnanlage",
        "mietobjekt",
        "anwesen",
        "grundstueck",
        "versicherungsort",
        "versichertes objekt",
        "risikoanschrift",
    ),
    "billing": (
        "rechnungsanschrift",
        "rechnungsadresse",
        "rechnungsempfaenger",
        "rechnung an",
        "rechnungsempfanger",
        "kundenanschrift",
        "auftraggeber",
        "vertragspartner",
        "zahlungspflichtiger",
    ),
    "supplier": (
        "absender",
        "lieferant",
        "auftragnehmer",
        "ust-idnr",
        "ust-id",
        "steuernummer",
        "steuer-nr",
        "handelsregister",
        "amtsgericht",
        "geschaeftsfuehrer",
        "bankverbindung",
        "iban",
        "telefon",
        "telefax",
        "fax",
        "e-mail",
        "www.",
        "impressum",
    ),
    "owner": ("eigentuemer", "eigentuemerin", "privatanschrift", "wohnhaft", "mieter", "mieterin"),
}
ROLE_WINDOW_BEFORE = 90
ROLE_WINDOW_AFTER = 70
LOCATION_WINDOW = 90


def normalize_text(s: str | None) -> str:
    """Kleinschreibung, Umlaute und ß, Bindestrichvarianten, Leerraum. Erhaelt Satzzeichen."""
    if not s:
        return ""
    return SPACES.sub(" ", str(s).lower().translate(DASHES).translate(TRANSLIT)).strip()


def normalize_street(s: str | None) -> str:
    """Strassenname als Suchform: normalisiert, Strasse/Str./Straße wird zu "str" ohne Leerzeichen davor,
    Bindestriche werden Leerzeichen (Karl-Marx-Straße wird "karl marx str")."""
    text = normalize_text(s)
    if not text:
        return ""
    text = STREET_SUFFIX.sub("str", text)
    text = STREET_WORD.sub("str", text)
    text = PUNCT.sub(" ", text.replace("-", " ").replace("/", " "))
    return SPACES.sub(" ", text).strip()


def street_pattern(street_key: str) -> str:
    """Suchmuster einer Strassen-Suchform: Worttrennung im Text durch Leerraum oder Bindestrich."""
    return r"[\s\-]+".join(re.escape(part) for part in street_key.split(" "))


def repair_ocr_digits(token: str) -> str:
    """Ersetzt verwechselte Buchstaben nur zwischen Ziffern, am Anfang vor einer Ziffer oder (nur o) am Ende
    nach einer Ziffer. Zusaetze wie a, b, s bleiben erhalten."""
    chars = list(token.lower())
    n = len(chars)
    for i, ch in enumerate(chars):
        if ch not in OCR_DIGITS:
            continue
        prev_digit = i > 0 and chars[i - 1].isdigit()
        next_digit = i + 1 < n and (
            chars[i + 1].isdigit() or (chars[i + 1] in "-/" and i + 2 < n and chars[i + 2].isdigit())
        )
        at_start = i == 0
        at_end = i == n - 1
        if next_digit and (prev_digit or at_start):
            chars[i] = OCR_DIGITS[ch]
        elif prev_digit and at_end and ch in OCR_TRAILING:
            chars[i] = OCR_TRAILING[ch]
    return "".join(chars)


def parse_house_number(s: str | None) -> tuple[int | None, str, int | None]:
    """Zerlegt eine Hausnummer in (nummer, zusatz, bis). "12a" ergibt (12, "a", None), "12-14" ergibt
    (12, "", 14), "12 a" ergibt (12, "a", None). Nicht lesbare Angaben ergeben (None, "", None)."""
    text = normalize_text(s)
    if not text:
        return None, "", None
    text = re.sub(r"\s*([-/])\s*", r"\1", text)
    if text in OCR_DIGITS:
        text = OCR_DIGITS[text]
    text = repair_ocr_digits(text)
    m = HOUSE_NUMBER.match(text)
    if not m:
        return None, "", None
    num = int(m.group("num"))
    to = int(m.group("to")) if m.group("to") else None
    if to is not None and to < num:
        to = None
    return num, m.group("suffix") or "", to


def house_numbers_overlap(
    a: tuple[int | None, str, int | None], b: tuple[int | None, str, int | None]
) -> bool:
    """Zwei Hausnummernangaben passen, wenn sich ihre Bereiche schneiden und der Zusatz gleich ist.
    12a und 12b passen nicht, 12 und 12a passen nicht, 12-14 passt zu 13."""
    if a[0] is None or b[0] is None:
        return False
    if a[1] != b[1]:
        return False
    a_from, a_to = a[0], a[2] if a[2] is not None else a[0]
    b_from, b_to = b[0], b[2] if b[2] is not None else b[0]
    return a_from <= b_to and b_from <= a_to


def normalize_city(s: str | None) -> str:
    text = normalize_text(s)
    text = re.sub(r"\b(am|an der|a\.|a\. d\.|i\.|bei)\b", " ", text)
    text = PUNCT.sub(" ", text.replace("-", " "))
    return SPACES.sub(" ", text).strip()


def address_variants(
    street: str | None, house_number: str | None, postal_code: str | None = None, city: str | None = None
) -> set[str]:
    """Normalisierte Suchformen einer Anschrift: "musterstr 12", "musterstr 12a", mit PLZ und Ort."""
    street_n = normalize_street(street)
    if not street_n:
        return set()
    num, suffix, to = parse_house_number(house_number)
    numbers: list[str] = []
    if num is not None:
        numbers.append(f"{num}{suffix}")
        if suffix:
            numbers.append(f"{num} {suffix}")
        if to is not None:
            numbers.extend([f"{num}-{to}", f"{num} - {to}", f"{num}/{to}"])
    variants = {f"{street_n} {n}" for n in numbers} or {street_n}
    plz = (postal_code or "").strip()
    city_n = normalize_city(city)
    if plz:
        variants |= {f"{v} {plz}" for v in list(variants)}
    if city_n:
        variants |= {f"{v} {city_n}" for v in list(variants)}
    return variants


@dataclass(frozen=True)
class AddressEntry:
    """Eine bekannte Anschrift im Index (Objekt oder eigene Anschrift der Hausverwaltung)."""

    key: str
    street: str
    house: tuple[int | None, str, int | None]
    postal_code: str
    city: str
    object_id: int | None
    label: str = ""

    @property
    def address_key(self) -> str:
        num, suffix, to = self.house
        span = f"{num}{suffix}" + (f"-{to}" if to is not None else "") if num is not None else ""
        return f"{self.street} {span} {self.postal_code} {self.city}".strip()


@dataclass
class AddressHit:
    """Fundstelle einer bekannten Anschrift im Text."""

    entry: AddressEntry
    start: int
    end: int
    page: int
    matched_text: str
    role: str
    role_word: str | None
    postal_match: bool
    city_match: bool
    ambiguous_location: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def object_id(self) -> int | None:
        return self.entry.object_id

    @property
    def location_confirmed(self) -> bool:
        return self.postal_match or self.city_match


class NormalizedText:
    """Normalisierter Text mit Rueckabbildung der Positionen auf den Ursprungstext und Seitengrenzen."""

    def __init__(self, pages: Sequence[str]):
        raw_parts: list[str] = []
        norm_parts: list[str] = []
        offsets: list[int] = []
        page_starts: list[int] = []
        raw_pos = 0
        norm_pos = 0
        for page_text in pages:
            page_starts.append(norm_pos)
            page_text = page_text or ""
            raw_parts.append(page_text)
            for ch in page_text.lower().translate(DASHES):
                out = ch.translate(TRANSLIT)
                if out.isspace():
                    out = " "
                norm_parts.append(out)
                offsets.extend([raw_pos] * len(out))
                raw_pos += 1
                norm_pos += len(out)
            raw_parts.append("\n")
            norm_parts.append(" ")
            offsets.append(raw_pos)
            raw_pos += 1
            norm_pos += 1
        self.raw = "".join(raw_parts)
        self.text = "".join(norm_parts)
        self._offsets = offsets
        self._page_starts = page_starts
        self.street_text, self._street_map = self._street_form(self.text)

    @staticmethod
    def _street_form(text: str) -> tuple[str, list[int]]:
        """Strassenform des Textes ("hauptstrasse" wird "hauptstr") mit Positionsabbildung auf self.text."""
        out: list[str] = []
        mapping: list[int] = []
        pos = 0
        for m in STREET_SUFFIX.finditer(text):
            for i in range(pos, m.start()):
                out.append(text[i])
                mapping.append(i)
            out.extend("str")
            mapping.extend([m.start()] * 3)
            pos = m.end()
        for i in range(pos, len(text)):
            out.append(text[i])
            mapping.append(i)
        mapping.append(len(text))
        return "".join(out), mapping

    def raw_span(self, start: int, end: int) -> tuple[int, int]:
        """Positionen der Strassenform auf den Ursprungstext."""
        s = self._street_map[start]
        e = self._street_map[min(end, len(self._street_map) - 1)]
        raw_s = self._offsets[s] if s < len(self._offsets) else len(self.raw)
        raw_e = self._offsets[e - 1] + 1 if 0 < e <= len(self._offsets) else raw_s
        return raw_s, max(raw_e, raw_s)

    def page_of(self, street_pos: int) -> int:
        norm_pos = self._street_map[min(street_pos, len(self._street_map) - 1)]
        page = 1
        for i, start in enumerate(self._page_starts, start=1):
            if norm_pos >= start:
                page = i
        return page


def split_pages(text: str | Sequence[str] | None) -> list[str]:
    """Seiten aus einer Liste oder einem mit Seitenvorschub getrennten Text."""
    if text is None:
        return [""]
    if isinstance(text, str):
        return text.split("\f") if text else [""]
    return [t or "" for t in text] or [""]


def build_entries(objects: Iterable, *, own_addresses: Iterable = ()) -> list[AddressEntry]:
    """Adresseintraege aus Objekten (Attribute street, house_number, postal_code, city, pk) und eigenen
    Anschriften (Zeichenkette "Strasse 1, 12345 Ort" oder Tupel (strasse, nummer, plz, ort))."""
    entries: list[AddressEntry] = []
    for obj in objects:
        street = normalize_street(getattr(obj, "street", None))
        if not street:
            continue
        entries.append(
            AddressEntry(
                key=street,
                street=street,
                house=parse_house_number(getattr(obj, "house_number", None)),
                postal_code=(getattr(obj, "postal_code", None) or "").strip(),
                city=normalize_city(getattr(obj, "city", None)),
                object_id=getattr(obj, "pk", None) or getattr(obj, "id", None),
                label=str(getattr(obj, "object_number", "") or ""),
            )
        )
    for own in own_addresses:
        entry = parse_own_address(own)
        if entry is not None:
            entries.append(entry)
    return entries


OWN_ADDRESS = re.compile(
    r"^(?P<street>.+?)\s+(?P<number>\d[\w\-/ ]{0,6}?)\s*,\s*(?P<plz>\d{5})?\s*(?P<city>[^,]*)$"
)


def parse_own_address(own) -> AddressEntry | None:
    """Eigene Anschrift aus Zeichenkette oder Tupel; object_id None kennzeichnet die eigene Anschrift."""
    if isinstance(own, AddressEntry):
        return own
    if isinstance(own, str):
        m = OWN_ADDRESS.match(own.strip())
        if not m:
            return None
        street, number, plz, city = (
            m.group("street"),
            m.group("number"),
            m.group("plz") or "",
            m.group("city"),
        )
    else:
        try:
            street, number, plz, city = (list(own) + ["", "", "", ""])[:4]
        except TypeError:
            return None
    street_n = normalize_street(street)
    if not street_n:
        return None
    return AddressEntry(
        key=street_n,
        street=street_n,
        house=parse_house_number(number),
        postal_code=(plz or "").strip(),
        city=normalize_city(city),
        object_id=None,
        label="eigene Anschrift",
    )


def role_hint(window_before: str, window_after: str) -> tuple[str, str | None]:
    """Rolle einer Fundstelle aus dem Kontextfenster. Das naechste Rollenwort vor der Adresse gewinnt; sonst
    entscheiden Rollenwoerter direkt nach der Adresse (etwa Telefon oder USt-IdNr. der Absenderzeile); ohne
    Rollenwort ist die Fundstelle neutral."""
    best: tuple[int, str, str] | None = None
    for role, words in ROLE_WORDS.items():
        for word in words:
            pos = window_before.rfind(word)
            if pos >= 0 and (best is None or pos > best[0]):
                best = (pos, role, word)
    if best is not None:
        return best[1], best[2]
    first: tuple[int, str, str] | None = None
    for role in ("object", "billing", "supplier"):
        for word in ROLE_WORDS[role]:
            pos = window_after.find(word)
            if pos >= 0 and (first is None or pos < first[0]):
                first = (pos, role, word)
    if first is not None:
        return first[1], first[2]
    return "neutral", None


def find_addresses(
    text: str | Sequence[str] | NormalizedText, objects_index: Iterable[AddressEntry]
) -> list[AddressHit]:
    """Sucht alle Index-Anschriften im Text. Liefert je Fundstelle und passendem Eintrag einen Treffer mit
    Position, Seite, Rolle und der Angabe, ob PLZ oder Ort im Umfeld bestaetigt wurden. Gleiche Strasse und
    Nummer in verschiedenen Orten: nur die durch PLZ oder Ort bestaetigten Eintraege bleiben, ohne jede
    Bestaetigung bleiben alle als mehrdeutig markiert."""
    nt = text if isinstance(text, NormalizedText) else NormalizedText(split_pages(text))
    by_street: dict[str, list[AddressEntry]] = {}
    for entry in objects_index:
        by_street.setdefault(entry.street, []).append(entry)
    hits: list[AddressHit] = []
    street_text = nt.street_text
    for street, entries in by_street.items():
        pattern = re.compile(
            rf"(?<![a-z0-9]){street_pattern(street)}\.?\s*[,:]?\s*(?P<num>{HOUSE_NUMBER_PART})(?![0-9a-z])"
        )
        for m in pattern.finditer(street_text):
            house = parse_house_number(m.group("num"))
            matching = [e for e in entries if house_numbers_overlap(e.house, house)]
            if not matching:
                continue
            after = street_text[m.end() : m.end() + LOCATION_WINDOW]
            before = street_text[max(0, m.start() - LOCATION_WINDOW) : m.start()]
            located: list[tuple[AddressEntry, bool, bool]] = []
            city_window = normalize_city(f"{before} | {after}")
            for e in matching:
                plz_ok = bool(e.postal_code) and (e.postal_code in after or e.postal_code in before)
                city_ok = bool(e.city) and f" {e.city} " in f" {city_window} "
                located.append((e, plz_ok, city_ok))
            distinct_places = {(e.postal_code, e.city) for e in matching}
            confirmed = [t for t in located if t[1] or t[2]]
            ambiguous = False
            if len(distinct_places) > 1:
                if confirmed:
                    located = confirmed
                else:
                    ambiguous = True
            role_before = street_text[max(0, m.start() - ROLE_WINDOW_BEFORE) : m.start()]
            role_after = after[:ROLE_WINDOW_AFTER]
            role, word = role_hint(role_before, role_after)
            raw_s, raw_e = nt.raw_span(m.start(), m.end())
            for e, plz_ok, city_ok in located:
                hits.append(
                    AddressHit(
                        entry=e,
                        start=raw_s,
                        end=raw_e,
                        page=nt.page_of(m.start()),
                        matched_text=nt.raw[raw_s:raw_e],
                        role="own" if e.object_id is None else role,
                        role_word=word,
                        postal_match=plz_ok,
                        city_match=city_ok,
                        ambiguous_location=ambiguous,
                        notes=["Ort nicht bestaetigt, gleiche Anschrift in mehreren Orten"]
                        if ambiguous
                        else [],
                    )
                )
    hits.sort(key=lambda h: (h.start, h.entry.object_id or 0))
    return hits
