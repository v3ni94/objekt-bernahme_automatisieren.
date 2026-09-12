"""Vertragsdaten aus Mieterdokumenten (Entscheidung 12.09.2026).

Aus dem maskierten Text eines Mietvertrags (oder eines anderen Mieterdokuments) werden Mieter, Einheit,
Mietbeginn und Mietende, Kaltmiete, Vorauszahlungen und Kaution gelesen. Das Ergebnis ist ein Vorschlag fuer den
Review-Fall „Mieter unbekannt“ (Knopf „Mieter aus Dokument anlegen“); Stammdaten entstehen erst mit der
Bestaetigung durch einen Menschen (CR 15). Stufe 3 (KI) kann die Werte ergaenzen oder ueberschreiben, der
Abgleich bleibt lokal. Keine Nebenwirkungen, keine Datenbank.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

LEGAL_FORM = re.compile(
    r"\b(GmbH|AG|UG|KG|OHG|GbR|e\.\s?V\.|eG|SE|mbH|Co\.|Stiftung|Verein|Gesellschaft)\b", re.IGNORECASE
)
SALUTATION = re.compile(
    r"^(?:Herrn?|Frau|Fr\.|Hr\.|Eheleute|Ehepaar|Familie|Fam\.|Firma|Fa\.)\s+", re.IGNORECASE
)
NAME_RE = r"[A-ZÄÖÜ][\wäöüßéèáàóòúùçñ'’.-]*"
PERSON_WITH_SALUTATION = re.compile(
    rf"(?:Herrn?|Frau|Eheleute|Ehepaar|Familie)\s+((?:{NAME_RE}\s+){{0,3}}{NAME_RE})"
)
# Eheleute Karl und Ute Beispiel, Eheleute Max Mustermann und Erika Mustermann
COUPLE = re.compile(
    rf"(?:Eheleute|Ehepaar|Familie)\s+((?:{NAME_RE}\s+){{0,2}}{NAME_RE})\s+und\s+((?:{NAME_RE}\s+){{0,2}}{NAME_RE})"
)
CONNECTOR = re.compile(r"^[\s,;]*(?:und|sowie|&)?[\s,;]*$")
TITLE = re.compile(r"^(?:Dr|Prof|Dipl|Ing|Mag|Med|Dr\.-Ing|Prof\.\s*Dr)\.?$", re.IGNORECASE)
# Zeile "Mieter: Max Mustermann" oder "Name des Mieters: ..."
TENANT_LABEL = re.compile(
    r"(?im)^[ \t]*(?:Mieter(?:in|/in|/-in|innen|partei)?|Name des Mieters|Name der Mieterin|Mietpartei)"
    r"[ \t]*[:\-–][ \t]*(.{3,140})$"
)
# "... – nachfolgend Mieter genannt –", "(im Folgenden „Mieter“)": Markierung suchen, Abschnitt davor ausschneiden
DESIGNATION = (
    r"(?:nachfolgend|nachstehend|im Folgenden|folgend|künftig|kuenftig|hiernach)"
    r"\s+(?:auch\s+|kurz\s+|gemeinsam\s+)?[„\"“'‚]?\s*"
)
TENANT_MARK = re.compile(DESIGNATION + r"Mieter(?:in|innen|partei|seite)?\b", re.IGNORECASE)
LANDLORD_MARK = re.compile(DESIGNATION + r"Vermieter(?:in|seite)?\b", re.IGNORECASE)
DESIGNATION_WINDOW = 220
LANDLORD_LABEL = re.compile(r"(?im)^[ \t]*Vermieter(?:in)?[ \t]*[:\-–][ \t]*(.{3,140})$")

UNIT_PATTERNS = (
    re.compile(
        r"\b(?P<prefix>WE|Wohnung|Whg\.?|Wohneinheit|Einheit|Gewerbeeinheit|GE|Stellplatz|Garage|TG)"
        r"\s*(?:Nr\.?\s*)?(?P<number>\d{1,4}[a-zA-Z]?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<floor>\d)\.\s*(?P<kind>OG|Obergeschoss|Stock|Etage)\b\s*(?P<side>links|rechts|mitte|Mitte)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<kind>Erdgeschoss|EG|Dachgeschoss|DG|Souterrain|Untergeschoss|UG|Hochparterre)\b"
        r"\s*(?P<side>links|rechts|mitte|Mitte)?",
        re.IGNORECASE,
    ),
)
UNIT_CONTEXT = re.compile(
    r"(?:Mietsache|Mietobjekt|Mietgegenstand|vermietet|Wohnung im|Wohnung in|folgende Räume|Lage der Wohnung)",
    re.IGNORECASE,
)

DATE = r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})"
START_DATE = re.compile(
    r"(?:Mietbeginn|Beginn des Mietverh(?:ä|ae)ltnisses|Mietverh(?:ä|ae)ltnis beginnt am|beginnt am|"
    r"Mietzeit(?:\s+beginnt)?\s+(?:am|ab)|Mietzeit ab|Vertragsbeginn|ab dem|Übergabe am|Einzug am)"
    r"\s*[:]?\s*(?:dem\s+)?" + DATE,
    re.IGNORECASE,
)
END_DATE = re.compile(
    r"(?:endet am|Mietende|befristet bis|läuft bis|laeuft bis|bis zum|Vertragsende|Auszug am|gekündigt zum)"
    r"\s*[:]?\s*(?:dem\s+)?" + DATE,
    re.IGNORECASE,
)
AMOUNT = r"(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d{2,6}(?:,-|,--)?)\s*(?:€|EUR|Euro)"
AMOUNT_LABELS = {
    "deposit_amount": r"(?:Kaution|Mietsicherheit|Mietkaution|Sicherheitsleistung)",
    "base_rent": r"(?:Grundmiete|Kaltmiete|Nettokaltmiete|Netto-?Kaltmiete|Nettomiete|Mietzins|"
    r"monatliche\s+Miete|Miete\s+monatlich|Miete\s+\(kalt\)|Miete\s+kalt)",
    "utilities_prepayment": r"(?:Betriebskosten|Nebenkosten)(?:-?vorauszahlung|-?vorschuss|-?pauschale)?",
    "heating_prepayment": r"(?:Heizkosten|Heiz-?\s*und\s+Warmwasserkosten|Heizung)(?:-?vorauszahlung|-?vorschuss)?",
    "total_rent": r"(?:Gesamtmiete|Bruttomiete|Warmmiete|Gesamtbetrag|Miete\s+gesamt|insgesamt)",
}
# Beschriftung endet an einer Wortgrenze (Kaltmiete, nicht Kaltmieten), Zwischenraum ohne Satzende (Gegenpruefung 12.09.2026)
AMOUNT_RES = {
    key: re.compile(label + r"(?![a-zäöüß])[^\d€\n.;]{0,40}?" + AMOUNT, re.IGNORECASE)
    for key, label in AMOUNT_LABELS.items()
}
DEPOSIT_MULTIPLE = re.compile(
    r"(?:Kaution|Mietsicherheit)[^.\n]{0,80}?(drei|zwei|3|2)\s+(?:Netto-?)?(?:Kalt-?)?(?:Monats-?)?mieten",
    re.IGNORECASE,
)
STOPWORDS = {
    "und",
    "sowie",
    "zwischen",
    "vertreten",
    "durch",
    "wohnhaft",
    "geboren",
    "geb",
    "als",
    "der",
    "die",
    "das",
    "dem",
    "den",
    "mit",
    "für",
    "fuer",
    "von",
    "vom",
    "am",
    "im",
    "in",
    "an",
    "auf",
}


@dataclass
class PartyGuess:
    raw: str
    kind: str = "natural_person"  # natural_person | legal_entity
    salutation: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    confidence: float = 0.5


@dataclass
class LeaseFacts:
    tenants: list[PartyGuess] = field(default_factory=list)
    unit_hint: str | None = None
    unit_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    base_rent: Decimal | None = None
    utilities_prepayment: Decimal | None = None
    heating_prepayment: Decimal | None = None
    deposit_amount: Decimal | None = None
    total_rent: Decimal | None = None
    landlord_names: list[str] = field(default_factory=list)
    source: str = "rules"

    @property
    def empty(self) -> bool:
        return not (
            self.tenants or self.unit_hint or self.start_date or self.base_rent or self.deposit_amount
        )

    def as_dict(self) -> dict:
        d = asdict(self)
        for key in ("start_date", "end_date"):
            d[key] = d[key].isoformat() if d[key] else None
        for key in (
            "base_rent",
            "utilities_prepayment",
            "heating_prepayment",
            "deposit_amount",
            "total_rent",
        ):
            d[key] = f"{d[key]:.2f}" if d[key] is not None else None
        return d

    @classmethod
    def from_dict(cls, data: dict | None) -> LeaseFacts:
        data = dict(data or {})
        facts = cls(
            tenants=[
                PartyGuess(**{k: v for k, v in t.items() if k in PartyGuess.__dataclass_fields__})
                for t in data.get("tenants") or []
            ],
            unit_hint=data.get("unit_hint"),
            unit_id=data.get("unit_id"),
            start_date=_iso(data.get("start_date")),
            end_date=_iso(data.get("end_date")),
            base_rent=_dec(data.get("base_rent")),
            utilities_prepayment=_dec(data.get("utilities_prepayment")),
            heating_prepayment=_dec(data.get("heating_prepayment")),
            deposit_amount=_dec(data.get("deposit_amount")),
            total_rent=_dec(data.get("total_rent")),
            landlord_names=list(data.get("landlord_names") or []),
            source=data.get("source") or "rules",
        )
        return facts

    def merge(self, other: LeaseFacts | None) -> LeaseFacts:
        """Werte einer zweiten Quelle (Stufe 3) uebernehmen: Namen der KI ersetzen die Regeltreffer, Zahlen und Daten
        fuellen Luecken; Einheit aus dem lokalen Abgleich (unit_id) hat Vorrang vor einem Hinweis."""
        if other is None or other.empty:
            return self
        merged = LeaseFacts(**{**asdict_shallow(self)})
        ki_tenants = filter_excluded(other.tenants, self.landlord_names) if other.tenants else []
        if ki_tenants:
            merged.tenants = ki_tenants
        for key in (
            "unit_hint",
            "start_date",
            "end_date",
            "base_rent",
            "utilities_prepayment",
            "heating_prepayment",
            "deposit_amount",
            "total_rent",
        ):
            if getattr(other, key) is not None and getattr(merged, key) is None:
                setattr(merged, key, getattr(other, key))
        merged.unit_id = self.unit_id or other.unit_id
        merged.source = "merged" if not self.empty else other.source
        return merged


def asdict_shallow(facts: LeaseFacts) -> dict:
    return {k: getattr(facts, k) for k in LeaseFacts.__dataclass_fields__}


def _iso(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_amount(raw: str) -> Decimal | None:
    text = raw.strip().replace("€", "").replace("EUR", "").replace("Euro", "").strip()
    text = text.replace(",-", "").replace(",--", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if 0 < value < Decimal("1000000") else None


def parse_date(d: str, m: str, y: str) -> date | None:
    try:
        year = int(y)
        if year < 100:
            year += 2000 if year < 70 else 1900
        value = date(year, int(m), int(d))
    except ValueError:
        return None
    return value if 1950 <= value.year <= 2100 else None


def parse_party(raw: str) -> PartyGuess | None:
    """„Herrn Max Mustermann“ -> natural_person Max / Mustermann; „Firma Beispiel GmbH“ -> legal_entity."""
    text = re.sub(r"\s+", " ", raw).strip(" ,;:()-–„“\"'")
    if len(text) < 3:
        return None
    salutation = None
    m = SALUTATION.match(text)
    if m:
        salutation = m.group(0).strip().rstrip(".")
        text = text[m.end() :].strip()
    text = re.split(r"\s*(?:,|\(|wohnhaft|geb\.|geboren|vertreten|Personalausweis|Ausweis|Tel\b)", text)[
        0
    ].strip()
    if not text:
        return None
    if LEGAL_FORM.search(text):
        return PartyGuess(raw=raw.strip(), kind="legal_entity", company_name=text[:200], confidence=0.6)
    tokens = [t for t in text.split(" ") if t and t.lower().strip(".") not in STOPWORDS]
    tokens = [t for t in tokens if re.match(r"^[A-ZÄÖÜ]", t) and not TITLE.match(t)]
    if not tokens or len(tokens) > 5:
        return None
    if salutation and salutation.lower() in ("eheleute", "ehepaar", "familie", "fam"):
        return PartyGuess(
            raw=raw.strip(),
            salutation=salutation,
            first_name=" ".join(tokens[:-1]) or None,
            last_name=tokens[-1][:120],
            confidence=0.55,
        )
    last = tokens[-1].rstrip(".")
    first = " ".join(tokens[:-1]) or None
    if salutation and salutation.lower() in ("herr", "herrn", "hr"):
        salutation = "Herr"
    elif salutation and salutation.lower() in ("frau", "fr"):
        salutation = "Frau"
    return PartyGuess(
        raw=raw.strip(), salutation=salutation, first_name=first, last_name=last[:120], confidence=0.6
    )


def _couple(m: re.Match) -> list[PartyGuess]:
    """Eheleute Karl und Ute Beispiel -> Karl Beispiel, Ute Beispiel; gemeinsamer Nachname ist der letzte Token."""
    left, right = m.group(1).split(), m.group(2).split()
    last = right[-1]
    first_left = left[:-1] if len(left) > 1 and left[-1] == last else left
    return [
        PartyGuess(
            raw=m.group(0), salutation="Eheleute", first_name=" ".join(first_left) or None, last_name=last
        ),
        PartyGuess(
            raw=m.group(0), salutation="Eheleute", first_name=" ".join(right[:-1]) or None, last_name=last
        ),
    ]


def split_parties(chunk: str) -> list[PartyGuess]:
    """Mehrere Personen in einem Abschnitt: „Herrn Max Mustermann und Frau Erika Mustermann“,
    „Eheleute Max und Erika Mustermann“, „Max Mustermann, Erika Mustermann“. Stehen Personen mit Anrede im Text,
    zaehlt die letzte zusammenhaengende Gruppe (durch „und“, Komma oder Leerzeichen verbunden): die Mieter stehen am
    Ende des Abschnitts, der Vermieter davor."""
    chunk = re.sub(r"\s+", " ", chunk).strip()
    couple = COUPLE.search(chunk)
    if couple:
        return _dedupe(_couple(couple))
    hits = list(PERSON_WITH_SALUTATION.finditer(chunk))
    if hits:
        groups: list[list[re.Match]] = [[hits[0]]]
        for prev, cur in zip(hits, hits[1:], strict=False):
            if CONNECTOR.match(chunk[prev.end() : cur.start()]):
                groups[-1].append(cur)
            else:
                groups.append([cur])
        found: list[PartyGuess] = []
        for h in groups[-1]:
            salut = re.search(r"(Herrn?|Frau|Eheleute|Ehepaar|Familie)\s*$", chunk[: h.start(1)])
            party = parse_party((salut.group(1) + " " if salut else "") + h.group(1))
            if party:
                found.append(party)
        return _dedupe(found)
    found = []
    for part in re.split(r"\s*(?:,|;|\bund\b|\bsowie\b|&)\s*", chunk):
        if re.search(r"\d", part):
            continue  # Adresse, Postleitzahl, Datum: keine Person
        party = parse_party(part)
        if party:
            found.append(party)
            if party.kind == "legal_entity":
                break  # nach der Firma folgen Anschrift und Vertreter, keine weitere Partei
    return _dedupe(found)


def _dedupe(parties: list[PartyGuess]) -> list[PartyGuess]:
    seen: set[str] = set()
    out: list[PartyGuess] = []
    for p in parties:
        key = (p.company_name or f"{p.first_name or ''} {p.last_name or ''}").strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


LANDLORD_TAG = re.compile(
    r"(?:nachfolgend|nachstehend|im Folgenden|folgend|künftig|kuenftig|hiernach)\s+(?:auch\s+|kurz\s+)?"
    r"[„\"“'‚]?\s*Vermieter(?:in|seite)?\s*[“\"”']?\s*(?:genannt|bezeichnet)?",
    re.IGNORECASE,
)


def _designation_chunks(text: str, mark: re.Pattern) -> list[str]:
    """Abschnitte vor einer Bezeichnung („nachfolgend Mieter“): das Fenster davor, beim Mieter hinter der letzten
    Vermieterbezeichnung, sonst hinter der letzten Leerzeile oder dem Wort „zwischen“."""
    chunks: list[str] = []
    for m in mark.finditer(text):
        chunk = text[max(0, m.start() - DESIGNATION_WINDOW) : m.start()]
        if mark is TENANT_MARK:
            tags = list(LANDLORD_MARK.finditer(chunk))
            if tags:
                chunk = chunk[tags[-1].end() :]
                chunk = re.sub(
                    r"^[\s\-–,;:)“\"']*(?:genannt|bezeichnet)?[\s\-–,;:)“\"']*(?:und|sowie)?\s*", "", chunk
                )
                chunks.append(chunk)
                continue
        chunks.append(re.split(r"\n\s*\n|\bzwischen\b", chunk, flags=re.IGNORECASE)[-1])
    return chunks


def extract_tenants(text: str, *, exclude: list[str] | None = None) -> tuple[list[PartyGuess], list[str]]:
    """Mieter aus Beschriftung („Mieter: ...“) oder Bezeichnung („... nachfolgend Mieter genannt“); Vermieter werden
    erkannt und ausgeschlossen. Rueckgabe (Mieter, Vermieternamen)."""
    landlords: list[str] = []
    for m in LANDLORD_LABEL.finditer(text):
        landlords.extend(p.last_name or p.company_name or "" for p in split_parties(m.group(1)))
    for chunk in _designation_chunks(text, LANDLORD_MARK):
        landlords.extend(p.last_name or p.company_name or "" for p in split_parties(chunk))
    landlords = [n for n in landlords if n]
    tenants: list[PartyGuess] = []
    for m in TENANT_LABEL.finditer(text):
        tenants.extend(split_parties(m.group(1)))
    if not tenants:
        for chunk in _designation_chunks(text, TENANT_MARK):
            tenants.extend(split_parties(chunk))
    tenants = filter_excluded(_dedupe(tenants), list(exclude or []) + landlords)
    return tenants[:4], landlords[:4]


def filter_excluded(parties: list[PartyGuess], exclude: list[str]) -> list[PartyGuess]:
    """Eigene Firmen und erkannte Vermieter sind nie Mieter (auch nicht aus der KI-Antwort)."""
    excluded = {n.casefold() for n in exclude if n}
    out: list[PartyGuess] = []
    for t in parties:
        name = (t.last_name or t.company_name or "").casefold()
        company = (t.company_name or "").casefold()
        if name in excluded or any(ex and (ex in company or company and company in ex) for ex in excluded):
            continue
        out.append(t)
    return out


def extract_unit_hint(text: str) -> str | None:
    """Einheit als Hinweis, auch ohne Stammdaten: „WE 3“, „Wohnung Nr. 3“ oder Lage „2. OG links“."""
    best: tuple[int, str] | None = None
    for pattern in UNIT_PATTERNS:
        for m in pattern.finditer(text):
            if "prefix" in m.groupdict() and m.group("prefix"):
                prefix = m.group("prefix").rstrip(".")
                key = prefix.upper()
                token = {
                    "WOHNUNG": "WE",
                    "WHG": "WE",
                    "WOHNEINHEIT": "WE",
                    "EINHEIT": "WE",
                    "GEWERBEEINHEIT": "GE",
                }.get(key, prefix.upper() if len(prefix) <= 3 else prefix.title())
                hint = f"{token} {m.group('number').upper()}"
            elif "floor" in m.groupdict() and m.group("floor"):
                side = (m.group("side") or "").lower()
                hint = f"{m.group('floor')}. OG{(' ' + side) if side else ''}"
            else:
                kind = m.group("kind")
                kind = {
                    "erdgeschoss": "EG",
                    "dachgeschoss": "DG",
                    "untergeschoss": "UG",
                    "souterrain": "Souterrain",
                    "hochparterre": "Hochparterre",
                }.get(kind.lower(), kind.upper())
                side = (m.group("side") or "").lower()
                hint = f"{kind}{(' ' + side) if side else ''}"
            window = text[max(0, m.start() - 80) : m.start()]
            score = 2 if UNIT_CONTEXT.search(window) else 1
            if best is None or score > best[0]:
                best = (score, hint)
    return best[1] if best else None


def extract_amounts(text: str) -> dict[str, Decimal | None]:
    """Betraege je Beschriftung; ein Betrag im Text zaehlt nur einmal (Kaution vor Miete vor Vorauszahlungen), damit
    „Die Betriebskosten trägt der Mieter. Die Kaution beträgt 1.950,00 EUR“ nicht als Betriebskosten gelesen wird."""
    out: dict[str, Decimal | None] = {k: None for k in AMOUNT_LABELS}
    used: set[tuple[int, int]] = set()
    for key, pattern in AMOUNT_RES.items():  # Reihenfolge der Beschriftungen ist die Prioritaet
        for m in pattern.finditer(text):
            span = m.span(1)
            if span in used:
                continue
            value = parse_amount(m.group(1))
            if value is not None:
                out[key] = value
                used.add(span)
                break
    if out["deposit_amount"] is None and out["base_rent"] is not None:
        m = DEPOSIT_MULTIPLE.search(text)
        if m:
            factor = {"drei": 3, "3": 3, "zwei": 2, "2": 2}[m.group(1).lower()]
            out["deposit_amount"] = out["base_rent"] * factor
    if (
        out["base_rent"] is not None
        and out["deposit_amount"] is not None
        and out["deposit_amount"] > out["base_rent"] * 3
    ):
        # mehr als drei Kaltmieten sind gesetzlich nicht zulaessig: der Wert ist wahrscheinlich kein Kautionsbetrag
        out["deposit_amount"] = None
    return out


def extract_lease_facts(
    text: str, *, exclude_names: list[str] | None = None, unit_id: int | None = None
) -> LeaseFacts:
    """Alle Vertragsdaten aus einem Text; unit_id ist der lokale Treffer aus dem Entitaetenabgleich (Vorrang)."""
    text = text or ""
    tenants, landlords = extract_tenants(text, exclude=exclude_names)
    amounts = extract_amounts(text)
    start = END = None
    m = START_DATE.search(text)
    if m:
        start = parse_date(m.group(1), m.group(2), m.group(3))
    m = END_DATE.search(text)
    if m:
        END = parse_date(m.group(1), m.group(2), m.group(3))
    return LeaseFacts(
        tenants=tenants,
        unit_hint=extract_unit_hint(text),
        unit_id=unit_id,
        start_date=start,
        end_date=END,
        base_rent=amounts["base_rent"],
        utilities_prepayment=amounts["utilities_prepayment"],
        heating_prepayment=amounts["heating_prepayment"],
        deposit_amount=amounts["deposit_amount"],
        total_rent=amounts["total_rent"],
        landlord_names=landlords,
        source="rules",
    )


def _bounded(value) -> Decimal | None:
    dec = _dec(value)
    return dec if dec is not None and 0 < dec < Decimal("1000000") else None


def facts_from_stage3(lease: dict | None, *, exclude_names: list[str] | None = None) -> LeaseFacts | None:
    """Antwortblock „lease“ der Stufe 3 in LeaseFacts uebersetzen (Namen wie im Text, Zahlen als Dezimal im
    zulaessigen Bereich); eigene Firmen und erkannte Vermieter werden auch hier ausgeschlossen."""
    if not lease:
        return None
    tenants: list[PartyGuess] = []
    for raw in lease.get("tenant_names") or []:
        party = parse_party(str(raw))
        if party:
            party.confidence = 0.8
            tenants.append(party)
    facts = LeaseFacts(
        tenants=filter_excluded(_dedupe(tenants), list(exclude_names or [])),
        unit_hint=(str(lease.get("unit"))[:80] if lease.get("unit") else None),
        start_date=_iso(lease.get("start_date")),
        end_date=_iso(lease.get("end_date")),
        base_rent=_bounded(lease.get("base_rent")),
        utilities_prepayment=_bounded(lease.get("utilities_prepayment")),
        heating_prepayment=_bounded(lease.get("heating_prepayment")),
        deposit_amount=_bounded(lease.get("deposit_amount")),
        total_rent=_bounded(lease.get("total_rent")),
        source="stage3",
    )
    return None if facts.empty else facts
