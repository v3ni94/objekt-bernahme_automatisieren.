"""Zentrale Benennungsfunktion der Eigentuemerakten (CR 4, Fachentwurf F 6.1 und 6.2, docs/architektur.md 7.6).

build_owner_folder_name ist nebenwirkungsfrei und liest die Konfiguration aus einem uebergebenen
OwnerFileNamingConfig (aus app_settings, Kategorie owner_file). Aufrufer: Aktenanlage, Review-Vorschau, Tests.
Firmenkurzname ist owners.short_name (Beschluss B-20). Seed unit_prefix_mode = always_we (B-23, Frage F6).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from apps.objects.units import parse_unit_label

FORBIDDEN = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})

FileKind = Literal["unit_owner", "unknown_unit", "unassigned"]


@dataclass(frozen=True)
class OwnerNamePart:
    kind: str = "natural_person"  # natural_person | legal_entity | community
    last_name: str | None = None
    company_name: str | None = None
    short_name: str | None = None


@dataclass(frozen=True)
class OwnerNameInput:
    file_kind: FileKind
    unit_label: str | None = None
    unit_type: str | None = None
    owner_names: tuple[OwnerNamePart, ...] = ()
    valid_from: date | None = None
    existing_names_in_object: frozenset[str] = frozenset()
    current_name: str | None = None  # bereits vergebener Name derselben Akte (Idempotenz, F Testfall 32)


@dataclass(frozen=True)
class OwnerFileNamingConfig:
    name_separator: str = "-"
    name_max_names: int = 3
    name_overflow_suffix: str = "ua"
    name_unknown_unit_prefix: str = "Unbekannte_WE"
    name_unassigned: str = "Unzugeordnet"
    name_unknown_owner: str = "Unbekannt"
    unit_number_pad: int = 2
    unit_prefix_mode: str = "always_we"  # always_we | by_type
    unit_prefix_map: dict[str, str] = field(
        default_factory=lambda: {
            "apartment": "WE",
            "commercial": "GE",
            "parking": "ST",
            "garage": "GA",
            "underground_parking": "TG",
            "cellar": "KE",
            "other": "VE",
        }
    )
    transliterate_umlauts: bool = False
    name_max_length: int = 100
    collision_suffix_mode: str = "year_then_counter"
    legal_form_tokens: tuple[str, ...] = (
        "GmbH & Co. KG",
        "GmbH",
        "AG",
        "KG",
        "UG",
        "e.V.",
        "GbR",
        "OHG",
        "eG",
        "SE",
        "mbH",
        "haftungsbeschränkt",
        "& Co.",
    )
    type_prefix_mapping: dict[str, str] = field(default_factory=lambda: {"WE": "apartment"})

    @classmethod
    def from_settings(cls) -> OwnerFileNamingConfig:
        from apps.config import store

        return cls(
            name_separator=store.get("owner_file.name_separator", "-"),
            name_max_names=int(store.get("owner_file.name_max_names", 3)),
            name_overflow_suffix=store.get("owner_file.name_overflow_suffix", "ua"),
            name_unknown_unit_prefix=store.get("owner_file.name_unknown_unit_prefix", "Unbekannte_WE"),
            name_unassigned=store.get("owner_file.name_unassigned", "Unzugeordnet"),
            name_unknown_owner=store.get("owner_file.name_unknown_owner", "Unbekannt"),
            unit_number_pad=int(store.get("owner_file.unit_number_pad", 2)),
            unit_prefix_mode=store.get("owner_file.unit_prefix_mode", "always_we"),
            unit_prefix_map=dict(store.get("owner_file.unit_prefix_map", {}) or cls().unit_prefix_map),
            transliterate_umlauts=bool(store.get("owner_file.transliterate_umlauts", False)),
            name_max_length=int(store.get("owner_file.name_max_length", 100)),
            collision_suffix_mode=store.get("owner_file.collision_suffix_mode", "year_then_counter"),
            legal_form_tokens=tuple(store.get("owner_file.legal_form_tokens", []) or cls().legal_form_tokens),
            type_prefix_mapping=dict(store.get("units.type_prefix_mapping", {}) or {"WE": "apartment"}),
        )


# ---------------------------------------------------------------- Normalisierung (F 6.2)
def clean_text(value: str) -> str:
    """NFC, verbotene Zeichen entfernt, Leerraum bereinigt, kein Punkt am Rand (F 6.2 Punkte 1 und 2)."""
    text = unicodedata.normalize("NFC", value or "")
    text = FORBIDDEN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".").strip()
    return text


def transliterate(value: str) -> str:
    text = value.translate(_UMLAUTS)
    decomposed = unicodedata.normalize("NFKD", text)
    # Kombinierende Zeichen (Diakritika) entfernen, danach wieder NFC, damit zerlegte Silben (z. B. Hangul) rekomponiert werden
    return unicodedata.normalize("NFC", "".join(ch for ch in decomposed if not unicodedata.combining(ch)))


def sort_key(name: str) -> str:
    return transliterate(name).casefold()


def strip_legal_form(company: str, tokens: tuple[str, ...] | list[str]) -> str:
    text = company
    for token in sorted(tokens, key=len, reverse=True):
        text = re.sub(rf"(?<![\wäöüÄÖÜß]){re.escape(token)}(?![\wäöüÄÖÜß])", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(^|\s)[&,;/+]+(\s|$)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;&+-")
    return text or company.strip()


def display_name(part: OwnerNamePart, cfg: OwnerFileNamingConfig) -> str:
    if part.kind == "legal_entity":
        raw = part.short_name or strip_legal_form(part.company_name or "", cfg.legal_form_tokens)
    elif part.kind == "community":
        raw = part.company_name or part.last_name or ""
    else:
        raw = part.last_name or ""
    text = clean_text(raw)
    return transliterate(text) if cfg.transliterate_umlauts else text


def unit_token(unit_label: str | None, unit_type: str | None, cfg: OwnerFileNamingConfig) -> str:
    parsed = parse_unit_label(unit_label or "", cfg.type_prefix_mapping)
    if cfg.unit_prefix_mode == "by_type":
        prefix = cfg.unit_prefix_map.get(unit_type or parsed.unit_type or "other") or cfg.unit_prefix_map.get(
            "other", "VE"
        )
    else:
        prefix = "WE"
    if parsed.number is None:
        fallback = clean_text(unit_label or "").replace(" ", "")
        return fallback or prefix
    number = parsed.number.zfill(cfg.unit_number_pad)
    return f"{prefix}{number}{parsed.suffix}"


def join_names(names: list[str], cfg: OwnerFileNamingConfig) -> str:
    if len(names) <= cfg.name_max_names:
        return cfg.name_separator.join(names)
    return f"{names[0]}{cfg.name_separator}{cfg.name_overflow_suffix}"


def _fit_length(prefix: str, names: list[str], cfg: OwnerFileNamingConfig) -> str:
    name_part = join_names(names, cfg)
    base = f"{prefix}_{name_part}" if prefix else name_part
    if len(base) <= cfg.name_max_length:
        return base
    if len(names) > 1:
        name_part = f"{names[0]}{cfg.name_separator}{cfg.name_overflow_suffix}"
        base = f"{prefix}_{name_part}" if prefix else name_part
        if len(base) <= cfg.name_max_length:
            return base
    room = cfg.name_max_length - (len(prefix) + 1 if prefix else 0)
    if room <= 0:
        return base[: cfg.name_max_length]
    return (f"{prefix}_" if prefix else "") + name_part[:room].rstrip(" .-")


def _with_suffix(base: str, suffix: str, cfg: OwnerFileNamingConfig) -> str:
    if len(base) + len(suffix) <= cfg.name_max_length:
        return base + suffix
    return base[: cfg.name_max_length - len(suffix)].rstrip(" .-_") + suffix


def resolve_collision(base: str, inp: OwnerNameInput, cfg: OwnerFileNamingConfig) -> str:
    if inp.current_name == base or base not in inp.existing_names_in_object:
        return base
    if cfg.collision_suffix_mode == "year_then_counter" and inp.valid_from is not None:
        candidate = _with_suffix(base, f"_{inp.valid_from.year}", cfg)
        if candidate == inp.current_name or candidate not in inp.existing_names_in_object:
            return candidate
    counter = 2
    while True:
        candidate = _with_suffix(base, f"_{counter}", cfg)
        if candidate == inp.current_name or candidate not in inp.existing_names_in_object:
            return candidate
        counter += 1


def build_owner_folder_name(inp: OwnerNameInput, cfg: OwnerFileNamingConfig | None = None) -> str:
    cfg = cfg or OwnerFileNamingConfig()
    if inp.file_kind == "unassigned":
        return clean_text(cfg.name_unassigned)
    names = sorted({n for n in (display_name(p, cfg) for p in inp.owner_names) if n}, key=sort_key)
    if not names:
        names = [clean_text(cfg.name_unknown_owner)]
    prefix = (
        cfg.name_unknown_unit_prefix
        if inp.file_kind == "unknown_unit"
        else unit_token(inp.unit_label, inp.unit_type, cfg)
    )
    base = _fit_length(prefix, names, cfg)
    return resolve_collision(base, inp, cfg)
