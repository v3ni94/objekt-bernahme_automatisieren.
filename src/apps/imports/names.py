"""Namens-Splitter fuer Mehrfacheigentuemer (Fachentwurf H 6.4, Befund 4).

Eingabemuster: "Nachname, Vorname & Vorname", "Nachname, Vorname u. Vorname", "Vorname und Vorname Nachname",
"Vorname u. Vorname Nachname", GbR-Bezeichnungen, "c/o"-Zusaetze, Trennung mehrerer Personen durch Schraegstrich.
Konfidenzbaender aus import.name_split_thresholds (ANNAHME A-20): hoch ab 0,90, mittel ab 0,60, darunter unsicher.
Nebenwirkungsfrei; Konfiguration wird uebergeben.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_LEGAL_FORM_MARKERS = (
    "GmbH",
    "AG",
    "KG",
    "OHG",
    "UG",
    "e.V.",
    "GbR",
    "Stiftung",
    "Genossenschaft",
    "eG",
    "Gemeinschaft",
    "Erbengemeinschaft",
    "WEG",
    "Eheleute",
    "Ehepaar",
    "Familie",
)
COUPLE_MARKERS = ("Eheleute", "Ehepaar", "Familie")
COMMUNITY_MARKERS = ("Erbengemeinschaft", "Gemeinschaft", "WEG")
TITLES = (
    "Dr.",
    "Prof.",
    "Dipl.-Ing.",
    "Dipl.-Kfm.",
    "Dipl.-Kffr.",
    "Mag.",
    "Ing.",
    "Dr. med.",
    "Dr. jur.",
    "Prof. Dr.",
)
SALUTATIONS = {"herr": "Herr", "herrn": "Herr", "frau": "Frau", "hr.": "Herr", "fr.": "Frau"}
CONJUNCTION = re.compile(r"\s*(?:&|\+|\bund\b|\bu\.)\s*", re.IGNORECASE)
PERSON_SEPARATOR = re.compile(r"\s*(?:/|;)\s*")
CO_PATTERN = re.compile(r"\s*,?\s*\bc/o\b\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class Person:
    type: str  # natural_person
    first_name: str | None
    last_name: str | None
    salutation: str | None = None
    title: str | None = None
    split_pattern: str = ""
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SplitResult:
    persons: tuple[Person, ...] = ()
    entity_type: str = "natural_person"  # natural_person | legal_entity | community
    company_name: str | None = None
    correspondence_addition: str | None = None
    split_pattern: str = ""
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()
    raw: str = ""
    thresholds: dict = field(default_factory=lambda: {"high": 0.9, "medium": 0.6})

    @property
    def band(self) -> str:
        if self.confidence >= self.thresholds.get("high", 0.9):
            return "high"
        if self.confidence >= self.thresholds.get("medium", 0.6):
            return "medium"
        return "low"

    @property
    def is_uncertain(self) -> bool:
        return self.band == "low"


def _clean(text: str) -> str:
    text = (text or "").replace(" ", " ").replace('"', "").replace("„", "").replace("“", "").replace("'", "")
    return re.sub(r"\s+", " ", text).strip(" ,;")


def _has_marker(text: str, markers) -> str | None:
    for m in sorted(markers, key=len, reverse=True):
        if re.search(rf"(?<![\wäöüÄÖÜß]){re.escape(m)}(?![\wäöüÄÖÜß])", text, re.IGNORECASE):
            return m
    return None


def _strip_title_salutation(tokens: list[str]) -> tuple[list[str], str | None, str | None]:
    salutation = None
    title_parts: list[str] = []
    rest = list(tokens)
    changed = True
    while rest and changed:
        changed = False
        low = rest[0].lower()
        if low in SALUTATIONS:
            salutation = SALUTATIONS[low]
            rest.pop(0)
            changed = True
            continue
        joined2 = " ".join(rest[:2])
        if joined2 in TITLES:
            title_parts.append(joined2)
            rest = rest[2:]
            changed = True
            continue
        if rest[0] in TITLES or (
            rest[0].endswith(".") and rest[0][:-1].isalpha() and rest[0][0].isupper() and len(rest[0]) <= 6
        ):
            title_parts.append(rest.pop(0))
            changed = True
    return rest, salutation, (" ".join(title_parts) or None)


def _split_first_names(text: str) -> list[str]:
    return [p.strip() for p in CONJUNCTION.split(text) if p and p.strip()]


def _person(first, last, *, pattern, confidence, salutation=None, title=None, reasons=()) -> Person:
    return Person(
        "natural_person", first or None, last or None, salutation, title, pattern, confidence, tuple(reasons)
    )


def _split_part(part: str) -> list[Person]:
    part = _clean(part)
    if not part:
        return []
    if "," in part:
        last, firsts = (p.strip() for p in part.split(",", 1))
        tokens, salutation, title = _strip_title_salutation(firsts.split())
        first_list = _split_first_names(" ".join(tokens)) or [None]
        last_tokens, sal2, _ = _strip_title_salutation(last.split())
        last = " ".join(last_tokens) or last
        return [
            _person(f, last, pattern="comma", confidence=0.95, salutation=salutation or sal2, title=title)
            for f in first_list
        ]
    tokens, salutation, title = _strip_title_salutation(part.split())
    text = " ".join(tokens)
    if CONJUNCTION.search(text):
        pieces = _split_first_names(text)
        # letztes Stueck: "Vorname Nachname"; Nachname ist das letzte Token
        tail = pieces[-1].split()
        if len(tail) >= 2:
            last = tail[-1]
            firsts = pieces[:-1] + [" ".join(tail[:-1])]
            return [
                _person(
                    f, last, pattern="space_conjunction", confidence=0.75, salutation=salutation, title=title
                )
                for f in firsts
            ]
        # "Erika & Max" ohne Nachname
        return [
            _person(
                f,
                None,
                pattern="space_conjunction",
                confidence=0.5,
                salutation=salutation,
                title=title,
                reasons=("last_name_missing",),
            )
            for f in pieces
        ]
    if len(tokens) == 0:
        return []
    if len(tokens) == 1:
        return [
            _person(
                None,
                tokens[0],
                pattern="single_token",
                confidence=0.7,
                salutation=salutation,
                title=title,
                reasons=("first_names_missing",),
            )
        ]
    if len(tokens) == 2:
        return [
            _person(tokens[0], tokens[1], pattern="space", confidence=0.9, salutation=salutation, title=title)
        ]
    if len(tokens) == 3:
        return [
            _person(
                " ".join(tokens[:2]),
                tokens[2],
                pattern="space_multi",
                confidence=0.7,
                salutation=salutation,
                title=title,
                reasons=("name_tokens_ambiguous",),
            )
        ]
    return [
        _person(
            " ".join(tokens[:-1]),
            tokens[-1],
            pattern="space_multi",
            confidence=0.5,
            salutation=salutation,
            title=title,
            reasons=("name_split_ambiguous",),
        )
    ]


def split_names(
    text: str,
    *,
    legal_form_markers=DEFAULT_LEGAL_FORM_MARKERS,
    thresholds: dict | None = None,
) -> SplitResult:
    thresholds = thresholds or {"high": 0.9, "medium": 0.6}
    raw = text or ""
    cleaned = _clean(raw)
    addition = None
    co = CO_PATTERN.search(cleaned)
    if co:
        addition = "c/o " + co.group(1).strip()
        cleaned = _clean(cleaned[: co.start()])
    if not cleaned:
        return SplitResult(
            (), "natural_person", None, addition, "empty", 0.0, ("name_missing",), raw, thresholds
        )

    marker = _has_marker(cleaned, legal_form_markers)
    if marker:
        if marker in COUPLE_MARKERS:
            last = _clean(
                re.sub(
                    rf"(?<![\wäöüÄÖÜß]){re.escape(marker)}(?![\wäöüÄÖÜß])", " ", cleaned, flags=re.IGNORECASE
                )
            )
            persons = tuple(
                _person(None, last, pattern="couple", confidence=0.5, reasons=("first_names_missing",))
                for _ in range(2)
            )
            return SplitResult(
                persons,
                "natural_person",
                None,
                addition,
                "couple",
                0.5,
                ("first_names_missing",),
                raw,
                thresholds,
            )
        if marker in COMMUNITY_MARKERS:
            return SplitResult((), "community", cleaned, addition, "community", 0.95, (), raw, thresholds)
        return SplitResult((), "legal_entity", cleaned, addition, "legal_entity", 0.95, (), raw, thresholds)

    persons: list[Person] = []
    for part in PERSON_SEPARATOR.split(cleaned):
        persons.extend(_split_part(part))
    if not persons:
        return SplitResult(
            (), "natural_person", None, addition, "empty", 0.0, ("name_missing",), raw, thresholds
        )
    confidence = min(p.confidence for p in persons)
    reasons = tuple(sorted({r for p in persons for r in p.reasons}))
    pattern = "+".join(sorted({p.split_pattern for p in persons}))
    return SplitResult(
        tuple(persons), "natural_person", None, addition, pattern, confidence, reasons, raw, thresholds
    )
