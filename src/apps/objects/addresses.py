"""Anschriften eines Objekts: Hauptanschrift (street, house_number, postal_code, city) und weitere Anschriften
desselben Gebaeudes (Eckobjekt mit zwei Strassen, mehrere Hausnummern, Feld additional_addresses).

Ein Dokument, das eine dieser Anschriften nennt, gehoert zu diesem Objekt; nennt es zwei Anschriften desselben
Objekts (Rechnung „Kaiserstraße 77 / Windmühlenstraße 31“), ist das kein zweiter Objektbezug. Die Ableitung aus
Bezeichnungen wie „Kaiserstraße 77 u. 79, Windmühlenstraße 31“ ist eine Heuristik fuer die Nachpflege: Vorschau
zuerst, was nicht erkannt wird, bleibt als Hinweis stehen und wird nicht geraten.

Erkannte Schreibweisen der Registerbezeichnungen (Befund Serverlauf 22.09.2026, 104 Objekte): Hausnummernlisten
mit / + , _ u. u und; ganze Anschriften getrennt durch Komma, „&“ oder „u.“/„und“ vor einer weiteren Strasse
(„Bungstr. 5 u. Kreuzbuschstr. 5“); Ort vor der Strasse („Seesen Jacobsonstraße 24“, „Altena Am Stapel 10“) oder
dahinter, mit oder ohne PLZ („Fahlenberg 23a Linnich“, „Notweg 1-7 44229 Dortmund“); Objektnummer vor dem Ort
(„082 Ratheim, Shalomweg 3“); Vorsaetze WEG, SEV, MEG, Objekt und interne Kuerzel wie „R31“. Eine Strasse mit
Ziffern im Namen gilt als nicht erkannt, statt eine falsche Hauptanschrift zu erzeugen."""

from __future__ import annotations

import re

HOUSE_ITEM = r"\d{1,4}\s*[a-zA-Z]?(?:\s*[-–]\s*\d{1,4}\s*[a-zA-Z]?)?"
HOUSE_SEP = r"(?:/|\+|_|,|u\.|u\b|und\b)"
HOUSE_LIST = rf"{HOUSE_ITEM}(?:\s*{HOUSE_SEP}\s*{HOUSE_ITEM})*"
_NUMBERS_ONLY = re.compile(rf"^{HOUSE_LIST}$")
# Strasse, Hausnummern und optional dahinter „in“, PLZ und Ort ohne Komma; der Ort enthaelt keine Ziffer, sonst
# wuerde er Hausnummernlisten zerreissen
_STREET_WITH_NUMBERS = re.compile(
    rf"^(?P<street>.*?[^\d\s,])\s+(?P<nrs>{HOUSE_LIST})"
    rf"(?:\s+(?:in\s+)?(?:(?P<plz>\d{{5}})\s+)?(?P<city>[^\d,]+))?$"
)
# „41812 Erkelenz“, „Erkelenz“ oder mit vorangestellter Objektnummer „082 Ratheim“
_PLZ_CITY = re.compile(r"^(?:(?P<plz>\d{5})\s+|(?P<nr>\d{1,4})\s+)?(?P<city>[^\d,]+)$")
_LIST_SPLIT = re.compile(r"\s*(?:/|\+|_|,|\bu\.|\bu\b|\bund\b)\s*")
# Bestandteile einer Bezeichnung: Komma, „&“ oder „u.“/„und“/„u“ vor einem Wort (nicht vor einer Hausnummer)
_PART_SPLIT = re.compile(r"\s*(?:,|&)\s*|\s+(?:u\.|und|u)\s+(?=[^\d\s])")
_STREET_PREFIXES = ("WEG ", "Weg ", "SEV ", "MEG ", "Objekt ")
_CODE_PREFIX = re.compile(r"^[A-Z]{1,3}\d{1,4}\s+")
_CITY_STOP = re.compile(r"\b(gmbh|ag|kg|ug|ohg|e\.\s?v\.|grundbesitz|projekt|verwaltung|immobilien)\b", re.I)
_PLACE_WORD = re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*$")
_ADJECTIVE_END = re.compile(r"(er|sche|schen|scher)$")
_STREET_SUFFIXES = (
    "straße", "strasse", "str.", "str", "weg", "gasse", "platz", "allee", "ring", "damm", "ufer", "markt",
    "chaussee", "promenade", "steig", "pfad", "wall", "graben", "hof", "feld", "berg", "tal", "winkel", "kamp",
    "busch", "acker", "wiese", "garten", "siedlung", "zeile", "brücke", "stieg", "deich",
)  # fmt: skip
# Woerter, mit denen Strassennamen beginnen; ein solches erstes Wort ist nie ein Ort
_STREET_HEADS = {
    "am", "an", "auf", "im", "in", "zur", "zum", "zu", "bei", "beim", "unter", "hinter", "vor", "vorm", "über",
    "hinterm", "alte", "alter", "altes", "alt", "neue", "neuer", "neues", "neu", "kleine", "kleiner", "kleines",
    "große", "großer", "großes", "grosse", "grosser", "lange", "langer", "obere", "oberer", "untere", "unterer",
    "hohe", "hoher", "sankt", "st.", "von", "vom", "de", "der", "den", "dem", "des", "die", "das",
    "dr.", "prof.", "graf", "gräfin", "prinz", "prinzessin", "könig", "königin", "kaiser", "kaiserin", "herzog",
    "freiherr", "bischof", "pastor", "pfarrer", "general", "kardinal", "ritter", "bürgermeister", "landrat",
}  # fmt: skip
# Gattungsnamen, vor denen ein Adjektiv steht („Düsseldorfer Landstraße“): kein Ortspraefix abtrennen
_GENERIC_STREETS = {"landstraße", "landstrasse", "hauptstraße", "hauptstrasse", "bahnhofstraße", "bahnhofstrasse",
                    "dorfstraße", "dorfstrasse", "kirchstraße", "kirchstrasse", "chaussee", "allee", "ring",
                    "damm", "platz", "tor", "weg", "wall", "straße", "strasse", "str.", "str"}  # fmt: skip


def _clean_number(token: str) -> str:
    return re.sub(r"\s+", "", token.replace("–", "-")).lower()


def _clean_street(street: str) -> str:
    street = " ".join(street.split()).strip(" ,;")
    for prefix in _STREET_PREFIXES:
        if street.startswith(prefix) and len(street) > len(prefix):
            street = street[len(prefix) :]
    rest = _CODE_PREFIX.sub("", street, count=1)
    return rest if rest else street


def _split_city_prefix(street: str) -> tuple[str, str]:
    """„Seesen Jacobsonstraße“ -> (Seesen, Jacobsonstraße). Nur wenn das erste Wort wie ein Ortsname aussieht und
    der Rest fuer sich eine Strasse ist: Wortanfang wie „Am“, zusammengesetzter Name auf -straße/-weg oder Adjektiv
    plus Gattungswort („Teninger Str.“). „Aachener Straße“, „Kleiner Seeweg“ und „Düsseldorfer Landstraße“ bleiben
    ganz."""
    words = street.split(" ")
    if len(words) < 2:
        return "", street
    first, rest = words[0], words[1:]
    if not _PLACE_WORD.match(first) or first.lower() in _STREET_HEADS:
        return "", street
    if rest[0].lower() in _STREET_HEADS and len(rest) >= 2:
        return first, " ".join(rest)
    last = rest[-1].lower()
    suffix = next((s for s in _STREET_SUFFIXES if last.endswith(s)), None)
    if suffix is None:
        return "", street
    if len(rest) == 1 and len(last) > len(suffix) and last not in _GENERIC_STREETS:
        return first, rest[0]
    if len(rest) == 2 and last == suffix and _ADJECTIVE_END.search(rest[0].lower()):
        return first, " ".join(rest)
    return "", street


def _valid_city(city: str, plz: str | None, *, strict: bool = False) -> bool:
    """Hoechstens drei Woerter, kein Punkt, kein Firmenzusatz; strict (Ort hinter der Hausnummer) verlangt ein
    Wort mit Grossbuchstaben und Kleinbuchstaben, damit „9 A-C“ keinen Ort „A-C“ ergibt."""
    words = city.split()
    if not words or len(words) > 3 or "." in city or _CITY_STOP.search(city):
        return False
    if plz:
        return True
    return bool(_PLACE_WORD.match(words[0])) if strict else words[0][:1].isupper()


def addresses_from_name(name: str | None) -> tuple[list[dict], list[str]]:
    """Alle Anschriften einer Bezeichnung in Reihenfolge, je (street, house_number, postal_code, city), plus
    Hinweise fuer nicht erkannte Bestandteile. Ein Bestandteil aus Hausnummern allein gehoert zur vorigen Strasse,
    ein Ort („41812 Erkelenz“, „Erkelenz“, vor oder hinter der Strasse) gilt fuer alle Anschriften der
    Bezeichnung; eine PLZ hat Vorrang vor einem Ort ohne PLZ."""
    clean = re.sub(r"\s*\([^)]*\)\s*", " ", name or "").strip(" ,;")
    clean = " ".join(clean.split())
    if not clean:
        return [], []
    found: list[dict] = []
    notes: list[str] = []
    plz, city = "", ""

    def take_city(new_plz: str | None, new_city: str | None) -> None:
        nonlocal plz, city
        new_city = " ".join((new_city or "").split())
        if new_city and (new_plz or not city):
            plz = new_plz or plz
            city = new_city

    for part in (p.strip() for p in _PART_SPLIT.split(clean)):
        if not part:
            continue
        if _NUMBERS_ONLY.match(part):
            if not found:
                notes.append(f"Hausnummer ohne Straße: {part}")
                continue
            street = found[-1]["street"]
            found.extend(
                {"street": street, "house_number": _clean_number(t)} for t in _LIST_SPLIT.split(part) if t
            )
            continue
        m = _STREET_WITH_NUMBERS.match(part)
        if m:
            tail_city = m.group("city")
            if tail_city and not _valid_city(tail_city, m.group("plz"), strict=True):
                notes.append(f"nicht erkannt: {part}")
                continue
            street = _clean_street(m.group("street"))
            prefix_city, street = _split_city_prefix(street)
            if not street or re.search(r"\d", street):
                notes.append(f"nicht erkannt: {part}")
                continue
            found.extend(
                {"street": street, "house_number": _clean_number(t)}
                for t in _LIST_SPLIT.split(m.group("nrs"))
                if t
            )
            take_city(m.group("plz"), tail_city)
            take_city(None, prefix_city)
            continue
        m = _PLZ_CITY.match(part)
        if m and "." not in part and (not m.group("nr") or _valid_city(m.group("city"), None)):
            take_city(m.group("plz"), m.group("city"))
            continue
        notes.append(f"nicht erkannt: {part}")
    for item in found:
        item["postal_code"] = plz
        item["city"] = city
    return found, notes


def parse_address_lines(text: str | None) -> tuple[list[dict], list[str]]:
    """Formulareingabe, eine Anschrift je Zeile („Windmühlenstraße 31, 47051 Duisburg“ oder „Kaiserstraße 79“);
    Hausnummernlisten in einer Zeile ergeben mehrere Anschriften. Liefert Anschriften und fehlerhafte Zeilen."""
    items: list[dict] = []
    bad: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        found, notes = addresses_from_name(line)
        if not found or notes:
            bad.append(line)
            continue
        items.extend(found)
    return items, bad


def format_address_lines(items) -> str:
    lines = []
    for a in items or ():
        if not isinstance(a, dict) or not a.get("street"):
            continue
        head = " ".join(p for p in (a.get("street"), a.get("house_number")) if p)
        tail = " ".join(p for p in (a.get("postal_code"), a.get("city")) if p)
        lines.append(f"{head}, {tail}" if tail else head)
    return "\n".join(lines)


def address_key(street: str | None, house_number: str | None) -> tuple[str, tuple] | None:
    from apps.sync.assignment.normalize import normalize_street, parse_house_number

    s = normalize_street(street)
    if not s:
        return None
    return s, parse_house_number(house_number)


def additional_from_name(obj) -> dict:
    """Vorschlag fuer ein Objekt aus seiner Bezeichnung: primary (nur wenn die Hauptanschrift fehlt), additional
    (neue weitere Anschriften ohne die Hauptanschrift und ohne bereits erfasste), notes (nicht erkannt, Hausnummer
    der Hauptanschrift nicht auswertbar)."""
    found, notes = addresses_from_name(getattr(obj, "name", None))
    primary = None
    street = getattr(obj, "street", None)
    house_number = getattr(obj, "house_number", None)
    if not street and found:
        primary = dict(found[0])
        found = found[1:]
        street, house_number = primary["street"], primary["house_number"]
    primary_key = address_key(street, house_number)
    if street and (primary_key is None or primary_key[1][0] is None):
        notes.append(f"Hausnummer der Hauptanschrift nicht auswertbar: {house_number or '(leer)'}")
    existing = {
        address_key(a.get("street"), a.get("house_number"))
        for a in (getattr(obj, "additional_addresses", None) or [])
        if isinstance(a, dict)
    }
    postal_code = (primary or {}).get("postal_code") or getattr(obj, "postal_code", None) or ""
    city = (primary or {}).get("city") or getattr(obj, "city", None) or ""
    additional: list[dict] = []
    for a in found:
        key = address_key(a["street"], a["house_number"])
        if key is None or key[1][0] is None or key == primary_key or key in existing:
            continue
        existing.add(key)
        additional.append(
            {
                "street": a["street"],
                "house_number": a["house_number"],
                "postal_code": a.get("postal_code") or postal_code,
                "city": a.get("city") or city,
            }
        )
    return {"primary": primary, "additional": additional, "notes": notes}
