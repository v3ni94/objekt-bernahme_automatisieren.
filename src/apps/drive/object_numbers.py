"""Objektnummer-Erkennung aus Ordnernamen (CR 2, Befund Punkt 4, Frage F4; Konfiguration drive.object_number_*).

Die Objektnummer ist die fuehrende Ziffernfolge mit 2 bis 6 Stellen (konfigurierbar), gefolgt von einem
Trennzeichen oder dem Ende. Identitaet ueber den Zahlenwert: 0631 und 631 sind dasselbe Objekt, 6230 ist
nicht Objekt 623 (Ziffernfolge wird nie gekuerzt).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_SEPARATORS = (" ", "_", ",", ".", "-")


@dataclass(frozen=True)
class ObjectNumberMatch:
    digits: str
    numeric: int
    rest: str

    @property
    def normalized(self) -> str:
        return str(self.numeric)


def parse_object_number(
    folder_name: str,
    *,
    digits_min: int = 2,
    digits_max: int = 6,
    separators: tuple[str, ...] | list[str] = DEFAULT_SEPARATORS,
) -> ObjectNumberMatch | None:
    if digits_min < 1 or digits_max < digits_min:
        raise ValueError("ungültige Stellenzahl")
    seps = "".join(re.escape(s) for s in separators)
    pattern = re.compile(rf"^\s*(?P<digits>\d{{{digits_min},{digits_max}}})(?=$|[{seps}])(?P<rest>.*)$")
    match = pattern.match(folder_name or "")
    if not match:
        return None
    digits = match.group("digits")
    return ObjectNumberMatch(
        digits=digits, numeric=int(digits), rest=match.group("rest").lstrip("".join(separators)).strip()
    )


def same_object(number_a: str, number_b: str) -> bool:
    """Vergleich ueber den Zahlenwert (D 3.1: 082 und 82 sind dasselbe Objekt)."""
    return int(number_a) == int(number_b)


def normalize_object_number(raw: str, *, zero_pad_to: int | None = None) -> str:
    """Erfassungsform: nur Ziffern; ohne Nullauffuellung die Ist-Nummer ohne fuehrende Nullen (F4 Vorschlagswert)."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        raise ValueError("Objektnummer enthält keine Ziffern")
    value = str(int(digits))
    return value.zfill(zero_pad_to) if zero_pad_to else value


def object_folder_name(
    pattern: str, *, number: str, city: str = "", street: str = "", house_number: str = ""
) -> str:
    """Name eines neuen Objektordners nach drive.object_folder_name_pattern; leere Teile werden bereinigt."""
    name = pattern.format(
        number=number, city=city or "", street=street or "", house_number=house_number or ""
    )
    name = re.sub(r"\s+,", ",", name)
    name = re.sub(r",\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" ,")
