"""Maskierung von Bank- und Ausweisdaten (docs/architektur.md 6.8, Beschluss B-11).

Grundsatz: IBAN, Kontonummern, BLZ, BIC und Ausweisnummern erscheinen nirgends im Klartext: nicht im
Seitentext, nicht im Volltextindex, nicht in Logs, nicht in KI-Prompts, nicht in Listen. Die Muster sind
bewusst weit gefasst; ein falsch positiver Treffer kostet ein maskiertes Zahlwort, ein falsch negativer
waere ein Datenschutzverstoss.

Drei Ausgabeformen:
- fulltext: [IBAN_****1234] mit den letzten vier Stellen (Zuordnungssignal bleibt lesbar)
- prompt:   [IBAN] ohne Reststellen (externe KI)
- log:      [IBAN] ohne Reststellen (Protokolle)

Jeder Treffer wird strukturiert zurueckgegeben (Typ, Position, letzte vier Stellen, optional HMAC), damit
die Pipeline ihn in document_entities ablegen kann, bevor der Klartext verworfen wird.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field

Mode = str  # "fulltext" | "prompt" | "log"

# DE-IBAN: DE + 2 Pruefziffern + 18 Ziffern, in Vierergruppen mit optionalen Leerzeichen. OCR-tolerant
# fuer den Buchstaben O statt der Ziffer 0.
_IBAN_DE = re.compile(r"\bDE\s?[0-9O]{2}(?:\s?[0-9O]{4}){4}\s?[0-9O]{2}\b")
# Generische IBAN: Laendercode, zwei Pruefziffern, 11 bis 30 alphanumerische Zeichen in Gruppen.
_IBAN_ANY = re.compile(r"\b(?!DE)[A-Z]{2}[0-9]{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")
# BIC nur mit Kontextwort, weil acht Grossbuchstaben sonst deutsche Woerter treffen (HAUSGELD).
_BIC = re.compile(r"\b(BIC|SWIFT(?:-Code)?)\s*[:.]?\s*([A-Z]{6}[A-Z2-9][A-NP-Z0-9](?:[A-Z0-9]{3})?)\b")
_BLZ = re.compile(r"\b(BLZ|Bankleitzahl)\s*[:.]?\s*(\d{3}\s?\d{3}\s?\d{2})\b")
_KONTO = re.compile(
    r"\b(Konto(?:-?\s?Nr\.?|nummer)|Kto\.?\s?-?\s?Nr\.?|Kontonr\.?)\s*[:.]?\s*(\d[\d\s]{4,14}\d)\b",
    re.IGNORECASE,
)
_AUSWEIS = re.compile(
    r"\b(Personalausweis(?:nummer|-?Nr\.?)?|Reisepass(?:nummer|-?Nr\.?)?|Ausweis(?:nummer|-?Nr\.?|-?ID)?|"
    r"Pass-?Nr\.?|Ausweiskopie)\s*[:.]?\s*([A-Z0-9]{9,10})\b"
)

PLACEHOLDER = {
    "iban": "[IBAN]",
    "bic": "[BIC]",
    "blz": "[BLZ]",
    "kontonummer": "[KONTONR]",
    "ausweisnummer": "[AUSWEISNR]",
}


@dataclass(frozen=True)
class MaskHit:
    kind: str
    start: int
    end: int
    last4: str | None = None
    hmac_hex: str | None = None
    mod97_valid: bool | None = None
    context: str | None = None


@dataclass
class MaskResult:
    text: str
    hits: list[MaskHit] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.hits)


def normalize_iban(raw: str) -> str:
    """Leerzeichen entfernen, OCR-Fehler O nach 0 im Ziffernteil korrigieren, Grossschreibung."""
    compact = re.sub(r"\s+", "", raw).upper()
    return compact[:2] + compact[2:].replace("O", "0")


def iban_mod97(iban: str) -> bool:
    """Pruefziffernkontrolle nach ISO 7064 MOD 97-10."""
    compact = normalize_iban(iban)
    if len(compact) < 15 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    if not digits.isdigit():
        return False
    return int(digits) % 97 == 1


def iban_hmac(iban: str, key: bytes) -> str:
    return hmac.new(key, normalize_iban(iban).encode("ascii"), hashlib.sha256).hexdigest()


def _iban_replacement(mode: Mode, last4: str) -> str:
    if mode == "fulltext":
        return f"[IBAN_****{last4}]"
    return PLACEHOLDER["iban"]


def mask_text(text: str, mode: Mode = "log", hmac_key: bytes | None = None) -> MaskResult:
    """Maskiert alle Muster. Reihenfolge: IBAN vor BIC, BLZ, Kontonummer, Ausweisnummer.

    Positionen in den Treffern beziehen sich auf den Originaltext.
    """
    if not text:
        return MaskResult(text="")
    hits: list[MaskHit] = []
    spans: list[tuple[int, int, str]] = []  # (start, end, replacement) im Originaltext

    for pattern in (_IBAN_DE, _IBAN_ANY):
        for m in pattern.finditer(text):
            compact = normalize_iban(m.group(0))
            if len(compact) < 15 or len(compact) > 34:
                continue
            if _overlaps(spans, m.start(), m.end()):
                continue
            last4 = compact[-4:]
            hits.append(
                MaskHit(
                    "iban",
                    m.start(),
                    m.end(),
                    last4=last4,
                    hmac_hex=iban_hmac(compact, hmac_key) if hmac_key else None,
                    mod97_valid=iban_mod97(compact),
                )
            )
            spans.append((m.start(), m.end(), _iban_replacement(mode, last4)))

    for kind, pattern in (("bic", _BIC), ("blz", _BLZ), ("kontonummer", _KONTO), ("ausweisnummer", _AUSWEIS)):
        for m in pattern.finditer(text):
            start, end = m.start(2), m.end(2)
            if _overlaps(spans, start, end):
                continue
            hits.append(MaskHit(kind, start, end, context=m.group(1)))
            spans.append((start, end, PLACEHOLDER[kind]))

    if not spans:
        return MaskResult(text=text)
    spans.sort()
    out: list[str] = []
    cursor = 0
    for start, end, repl in spans:
        out.append(text[cursor:start])
        out.append(repl)
        cursor = end
    out.append(text[cursor:])
    hits.sort(key=lambda h: h.start)
    return MaskResult(text="".join(out), hits=hits)


def _overlaps(spans: list[tuple[int, int, str]], start: int, end: int) -> bool:
    return any(s < end and start < e for s, e, _ in spans)


def contains_sensitive(text: str) -> bool:
    """Schnellpruefung fuer Listen, Exporte und Prompts vor der Veroeffentlichung."""
    return mask_text(text, mode="log").count > 0
