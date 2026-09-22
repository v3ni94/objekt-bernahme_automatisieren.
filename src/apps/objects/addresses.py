"""Anschriften eines Objekts: Hauptanschrift (street, house_number, postal_code, city) und weitere Anschriften
desselben Gebaeudes (Eckobjekt mit zwei Strassen, mehrere Hausnummern, Feld additional_addresses).

Ein Dokument, das eine dieser Anschriften nennt, gehoert zu diesem Objekt; nennt es zwei Anschriften desselben
Objekts (Rechnung „Kaiserstraße 77 / Windmühlenstraße 31“), ist das kein zweiter Objektbezug. Die Ableitung aus
Bezeichnungen wie „Kaiserstraße 77 u. 79, Windmühlenstraße 31“ ist eine Heuristik fuer die Nachpflege: Vorschau
zuerst, was nicht erkannt wird, bleibt als Hinweis stehen und wird nicht geraten."""

from __future__ import annotations

import re

HOUSE_ITEM = r"\d{1,4}\s*[a-zA-Z]?(?:\s*[-–]\s*\d{1,4}\s*[a-zA-Z]?)?"
HOUSE_LIST = rf"{HOUSE_ITEM}(?:\s*(?:/|\+|,|u\.|und)\s*{HOUSE_ITEM})*"
_NUMBERS_ONLY = re.compile(rf"^{HOUSE_LIST}$")
_STREET_WITH_NUMBERS = re.compile(rf"^(?P<street>.*?[^\d\s,])\s+(?P<nrs>{HOUSE_LIST})$")
_PLZ_CITY = re.compile(r"^(?:(?P<plz>\d{5})\s+)?(?P<city>[^\d,]+)$")
_LIST_SPLIT = re.compile(r"\s*(?:/|\+|,|\bu\.|\bund\b)\s*")
_STREET_PREFIXES = ("WEG ", "Weg ", "Objekt ")


def _clean_number(token: str) -> str:
    return re.sub(r"\s+", "", token.replace("–", "-")).lower()


def _clean_street(street: str) -> str:
    street = " ".join(street.split()).strip(" ,;")
    for prefix in _STREET_PREFIXES:
        if street.startswith(prefix) and len(street) > len(prefix):
            street = street[len(prefix) :]
    return street


def addresses_from_name(name: str | None) -> tuple[list[dict], list[str]]:
    """Alle Anschriften einer Bezeichnung in Reihenfolge, je (street, house_number, postal_code, city), plus
    Hinweise fuer nicht erkannte Bestandteile. Ein Bestandteil aus Hausnummern allein gehoert zur vorigen Strasse,
    ein Bestandteil „41812 Erkelenz“ oder „Erkelenz“ gilt fuer alle Anschriften der Bezeichnung."""
    clean = re.sub(r"\s*\([^)]*\)\s*", " ", name or "").strip(" ,;")
    clean = " ".join(clean.split())
    if not clean:
        return [], []
    found: list[dict] = []
    notes: list[str] = []
    plz, city = "", ""
    for part in (p.strip() for p in clean.split(",")):
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
            street = _clean_street(m.group("street"))
            found.extend(
                {"street": street, "house_number": _clean_number(t)}
                for t in _LIST_SPLIT.split(m.group("nrs"))
                if t
            )
            continue
        m = _PLZ_CITY.match(part)
        if m and "." not in part and (m.group("plz") or not city):
            plz = m.group("plz") or plz
            city = m.group("city").strip()
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
