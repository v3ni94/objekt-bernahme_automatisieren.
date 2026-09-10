"""Feldnormalisierung fuer den Import (Fachentwurf H 6.2.1, 6.3; Umsetzungsplan M3 Schritt 4).

Adresse mit Hausnummer, Telefon oder Mobil, E-Mail, Datum, Betrag, PLZ als Text, Land als ISO-2, Bruch fuer
Miteigentumsanteile, Wahrheitswerte. IBAN wird sofort maskiert: nur iban_last4 und HMAC bleiben (B-18).
Jede Funktion liefert einen Wert plus Hinweise; nichts wird stillschweigend verworfen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from objektakte.masking import iban_hmac, iban_mod97, mask_text, normalize_iban

HOUSE_NUMBER = re.compile(
    r"^(?P<street>.*?)[\s,]+(?P<number>\d+\s*[a-zA-Z]?(?:\s*[-/]\s*\d+\s*[a-zA-Z]?)?)\s*$"
)
MOBILE_PREFIX = re.compile(r"^(?:\+49|0049|0)\s*1[5-7]\d")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S")
COUNTRIES = {
    "D": "DE",
    "DE": "DE",
    "DEU": "DE",
    "DEUTSCHLAND": "DE",
    "GERMANY": "DE",
    "BRD": "DE",
    "A": "AT",
    "AT": "AT",
    "AUT": "AT",
    "ÖSTERREICH": "AT",
    "OESTERREICH": "AT",
    "AUSTRIA": "AT",
    "CH": "CH",
    "CHE": "CH",
    "SCHWEIZ": "CH",
    "SWITZERLAND": "CH",
    "NL": "NL",
    "NIEDERLANDE": "NL",
    "F": "FR",
    "FR": "FR",
    "FRANKREICH": "FR",
    "B": "BE",
    "BE": "BE",
    "BELGIEN": "BE",
    "L": "LU",
    "LU": "LU",
    "LUXEMBURG": "LU",
    "PL": "PL",
    "POLEN": "PL",
    "IT": "IT",
    "ITALIEN": "IT",
    "ES": "ES",
    "SPANIEN": "ES",
    "GB": "GB",
    "UK": "GB",
    "GROSSBRITANNIEN": "GB",
    "DK": "DK",
    "DÄNEMARK": "DK",
    "CZ": "CZ",
    "TSCHECHIEN": "CZ",
    "TR": "TR",
    "TÜRKEI": "TR",
    "US": "US",
    "USA": "US",
}
TRUE_WORDS = {"ja", "j", "x", "1", "true", "wahr", "y", "yes", "vorhanden", "erteilt"}
FALSE_WORDS = {"nein", "n", "0", "false", "falsch", "no", "nicht vorhanden", "kein", "keine"}


@dataclass
class Normalized:
    value: object
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value is not None and not any(n.startswith("unparseable") for n in self.notes)


def clean_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value).replace(" ", " ")).strip()


def split_street(text: str) -> Normalized:
    text = clean_cell(text)
    if not text:
        return Normalized(("", ""), 0.0, ["empty"])
    m = HOUSE_NUMBER.match(text)
    if not m:
        return Normalized((text, ""), 0.6, ["house_number_missing"])
    return Normalized((m.group("street").strip(" ,"), re.sub(r"\s+", "", m.group("number"))), 0.95)


def classify_phone(text: str) -> Normalized:
    text = clean_cell(text)
    if not text:
        return Normalized((None, None), 0.0, ["empty"])
    compact = re.sub(r"[^\d+]", "", text)
    if len(re.sub(r"\D", "", compact)) < 6:
        return Normalized((None, text), 0.4, ["unparseable_phone"])
    kind = "mobile" if MOBILE_PREFIX.match(compact) else "phone"
    return Normalized((kind, text), 0.9)


def normalize_email(text: str) -> Normalized:
    text = clean_cell(text).lower()
    if not text:
        return Normalized(None, 0.0, ["empty"])
    if not EMAIL.match(text):
        return Normalized(text, 0.4, ["unparseable_email"])
    return Normalized(text, 0.95)


def parse_date(text) -> Normalized:
    if isinstance(text, datetime):
        return Normalized(text.date(), 1.0)
    if isinstance(text, date):
        return Normalized(text, 1.0)
    raw = clean_cell(text)
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    for fmt in DATE_FORMATS:
        try:
            return Normalized(datetime.strptime(raw, fmt).date(), 0.95)
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})/(\d{4})$", raw)  # Monat/Jahr
    if m:
        return Normalized(date(int(m.group(2)), int(m.group(1)), 1), 0.6, ["day_assumed_first"])
    if re.match(r"^\d{4}$", raw):
        return Normalized(date(int(raw), 1, 1), 0.5, ["year_only"])
    return Normalized(None, 0.0, ["unparseable_date"])


def parse_amount(text) -> Normalized:
    if isinstance(text, int | float | Decimal) and not isinstance(text, bool):
        return Normalized(Decimal(str(text)).quantize(Decimal("0.01")), 1.0)
    raw = clean_cell(text)
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    s = re.sub(r"(?i)(eur|€|euro)", "", raw).strip().replace(" ", "")
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()-+")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[1]) == 3):
        s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return Normalized(None, 0.0, ["unparseable_amount"])
    value = value.quantize(Decimal("0.01"))
    return Normalized(-value if negative else value, 0.9)


def parse_postal_code(text) -> Normalized:
    raw = clean_cell(text)
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 4 and len(raw) == 4:
        return Normalized(digits.zfill(5), 0.7, ["leading_zero_added"])
    if len(digits) == 5:
        return Normalized(digits, 0.95)
    return Normalized(raw[:10], 0.5, ["postal_code_unusual"])


def country_iso2(text) -> Normalized:
    raw = clean_cell(text)
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    code = COUNTRIES.get(raw.upper().replace(".", ""))
    if code:
        return Normalized(code, 0.95)
    return Normalized(None, 0.3, ["country_unknown"])


def parse_fraction(text) -> Normalized:
    raw = clean_cell(text)
    if not raw:
        return Normalized((None, None), 0.0, ["empty"])
    m = re.match(r"^\s*([\d.,]+)\s*/\s*([\d.,]+)\s*$", raw)
    if m:
        num = parse_amount(m.group(1)).value
        den = parse_amount(m.group(2)).value
        if num is not None and den:
            return Normalized((Decimal(num), int(den)), 0.95)
    single = parse_amount(raw)
    if single.value is not None:
        return Normalized((Decimal(single.value), None), 0.7, ["share_base_missing"])
    return Normalized((None, None), 0.0, ["unparseable_fraction"])


def parse_bool(text) -> Normalized:
    raw = clean_cell(text).lower()
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    if raw in TRUE_WORDS:
        return Normalized(True, 0.9)
    if raw in FALSE_WORDS:
        return Normalized(False, 0.9)
    return Normalized(None, 0.3, ["unparseable_bool"])


def parse_iban(text, hmac_key: bytes) -> Normalized:
    """Liefert (iban_last4, iban_hash_hex, masked) und nie den Klartext."""
    raw = clean_cell(text)
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    compact = normalize_iban(raw)
    if not iban_mod97(compact):
        return Normalized(None, 0.3, ["iban_invalid"])
    return Normalized(
        {
            "iban_last4": compact[-4:],
            "iban_hash": iban_hmac(compact, hmac_key),
            "masked": f"[IBAN_****{compact[-4:]}]",
        },
        0.95,
    )


def mask_raw(value) -> str:
    """Rohzelle fuer import_rows.raw_data: Bankdaten maskiert (D 9.2)."""
    return mask_text(clean_cell(value), mode="fulltext").text


def deposit_type_from(text, synonyms: dict[str, list[str]]) -> Normalized:
    raw = clean_cell(text).lower()
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    for code, words in synonyms.items():
        for w in words:
            if w.lower() in raw:
                return Normalized(code, 0.85)
    return Normalized("unknown", 0.4, ["deposit_type_unknown"])


def rent_adjustment_from(text) -> Normalized:
    raw = clean_cell(text).lower()
    if not raw:
        return Normalized(None, 0.0, ["empty"])
    if "staffel" in raw:
        return Normalized("graduated", 0.9)
    if "index" in raw:
        return Normalized("indexed", 0.9)
    if raw in FALSE_WORDS or "keine" in raw:
        return Normalized("none", 0.8)
    return Normalized("unknown", 0.4, ["rent_adjustment_unknown"])
