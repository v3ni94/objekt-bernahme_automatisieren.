"""Request- und Antwortschema der Stufe 3 (E 4.1, docs/architektur.md 6.7, B-12): Codes statt Ordnernamen,
Datenminimierung nach Auflage Ue15 (keine Personennamen, keine konkreten Einheiten, keine Stammdatenlisten im Request).
Validierung mit pydantic; das JSON-Schema wird beim Anbieter fuer strukturierte Ausgaben erzwungen."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CATEGORY_CODES = ("01", "02", "03", "04", "05", "06")


@dataclass(frozen=True)
class TaxonomyEntry:
    code: str
    name: str
    definition: str = ""


@dataclass(frozen=True)
class Taxonomy:
    categories: tuple[TaxonomyEntry, ...]
    subfolders: dict[str, tuple[TaxonomyEntry, ...]]  # Kategoriecode -> Unterordner
    document_types: dict[
        str, tuple[TaxonomyEntry, ...]
    ]  # "Kategorie/Unterordner" oder Kategorie -> Unterarten

    def subfolder_codes(self, category: str) -> set[str]:
        return {e.code for e in self.subfolders.get(category, ())}

    def document_type_codes(self, category: str) -> set[str]:
        codes: set[str] = set()
        for key, entries in self.document_types.items():
            if key == category or key.startswith(category + "/"):
                codes |= {e.code for e in entries}
        return codes

    def as_prompt_text(self) -> str:
        lines = []
        for cat in self.categories:
            lines.append(f"{cat.code} {cat.name}: {cat.definition}".rstrip(": "))
            for sub in self.subfolders.get(cat.code, ()):
                lines.append(f"  Unterordner {sub.code} {sub.name}")
                types = self.document_types.get(f"{cat.code}/{sub.code}", ())
                if types:
                    lines.append("    Unterarten: " + ", ".join(f"{t.code} ({t.name})" for t in types))
            types = self.document_types.get(cat.code, ())
            if types:
                lines.append("  Unterarten: " + ", ".join(f"{t.code} ({t.name})" for t in types))
        return "\n".join(lines)


@dataclass(frozen=True)
class ClassificationRequest:
    excerpt_masked: str
    filename_masked: str
    management_type: str  # weg | rental | weg_with_se
    taxonomy: Taxonomy
    unit_label_patterns: list[str] = field(default_factory=list)  # nur Praefixmuster (WE, GE, ST)
    hints: dict = field(default_factory=dict)  # ohne Personenbezug

    def prompt_payload(self) -> dict:
        return {
            "dateiname": self.filename_masked,
            "verwaltungsart": self.management_type,
            "einheitenmuster": self.unit_label_patterns,
            "hinweise": self.hints,
            "textauszug": self.excerpt_masked,
        }

    # Gemeinsame Schnittstelle aller Requests an den Router (Zweck, Prompt, Antwortpruefung, Maskierungspruefung)
    purpose: ClassVar[str] = "classify"
    schema_name: ClassVar[str] = "klassifikation"
    repair_instruction: ClassVar[str] = (
        "Verwende ausschließlich Codes aus der Taxonomie und alle Pflichtfelder."
    )

    @property
    def mask_check_text(self) -> str:
        return self.filename_masked

    def response_schema(self) -> dict:
        """JSON-Schema der Antwort fuer die strukturierte Ausgabe des Anbieters."""
        return response_json_schema()

    def build_messages(self, *, repair_hint: str | None = None) -> tuple[str, str]:
        from apps.ai.prompt import build_messages

        return build_messages(self, repair_hint=repair_hint)

    def parse(self, raw: str | dict) -> ClassificationResult:
        return parse_result(raw, self.taxonomy)


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    document_date: str | None = None


class LeaseBlock(BaseModel):
    """Vertragsdaten eines Mieterdokuments (12.09.2026): Grundlage fuer den Vorschlag „Mieter aus Dokument anlegen“.
    Namen wie im Text; der Abgleich und die Anlage erfolgen lokal nach Bestaetigung."""

    model_config = ConfigDict(extra="forbid")
    tenant_names: list[str] = Field(default_factory=list)
    unit: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    base_rent: float | None = None
    utilities_prepayment: float | None = None
    heating_prepayment: float | None = None
    deposit_amount: float | None = None
    total_rent: float | None = None

    @property
    def empty(self) -> bool:
        return not (
            self.tenant_names or self.unit or self.start_date or self.base_rent or self.deposit_amount
        )


_CODE_PREFIX = re.compile(r"^\s*([0-9A-Za-z_.-]+)")


def coerce_code(value, allowed) -> str | None:
    """Liefert den Code, wenn die Antwort den Code allein oder mit Name traegt ("04", "04 Hausgeld", "04 (Hausgeld)").

    Modelle kopieren gelegentlich die Zeile aus der Taxonomie statt nur des Codes. Der fuehrende Token wird gegen die
    erlaubten Codes geprueft; alles andere bleibt eine Schemaverletzung.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in allowed:
        return text
    m = _CODE_PREFIX.match(text)
    if m and m.group(1) in allowed:
        return m.group(1)
    return None


class ClassificationResult(BaseModel):
    """Antwort der Stufe 3, strikt (additionalProperties false), Codes nach B-12."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    object_related: bool
    category: str
    subfolder: str | None = None
    document_type: str | None = None
    period: Period
    mentioned_units: list[str] = Field(default_factory=list)
    mentioned_parties: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(max_length=400)
    lease: LeaseBlock | None = None

    def summary(self) -> dict:
        """Kurzfassung fuer das Protokoll ai_calls (keine Personendaten)."""
        return {
            "category": self.category,
            "subfolder": self.subfolder,
            "document_type": self.document_type,
            "confidence": self.confidence,
            "object_related": self.object_related,
            "period_year": self.period.year,
            "units": len(self.mentioned_units),
            "parties": len(self.mentioned_parties),
        }

    @field_validator("category", mode="before")
    @classmethod
    def _category_code(cls, v):
        code = coerce_code(v, CATEGORY_CODES)
        if code is None:
            raise ValueError("category muss ein Code 01 bis 06 sein")
        return code


def response_json_schema() -> dict:
    """JSON-Schema fuer strukturierte Ausgaben beim Anbieter; identisch fuer beide Anbieter."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "object_related",
            "category",
            "subfolder",
            "document_type",
            "period",
            "mentioned_units",
            "mentioned_parties",
            "confidence",
            "reasoning",
            "lease",
        ],
        "properties": {
            "object_related": {"type": "boolean"},
            "category": {"type": "string", "enum": list(CATEGORY_CODES)},
            "subfolder": {"type": ["string", "null"]},
            "document_type": {"type": ["string", "null"]},
            "period": {
                "type": "object",
                "additionalProperties": False,
                "required": ["year", "from", "to", "document_date"],
                "properties": {
                    "year": {"type": ["integer", "null"]},
                    "from": {"type": ["string", "null"]},
                    "to": {"type": ["string", "null"]},
                    "document_date": {"type": ["string", "null"]},
                },
            },
            "mentioned_units": {"type": "array", "items": {"type": "string"}},
            "mentioned_parties": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string", "maxLength": 400},
            "lease": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "tenant_names",
                    "unit",
                    "start_date",
                    "end_date",
                    "base_rent",
                    "utilities_prepayment",
                    "heating_prepayment",
                    "deposit_amount",
                    "total_rent",
                ],
                "properties": {
                    "tenant_names": {"type": "array", "items": {"type": "string"}},
                    "unit": {"type": ["string", "null"]},
                    "start_date": {"type": ["string", "null"]},
                    "end_date": {"type": ["string", "null"]},
                    "base_rent": {"type": ["number", "null"]},
                    "utilities_prepayment": {"type": ["number", "null"]},
                    "heating_prepayment": {"type": ["number", "null"]},
                    "deposit_amount": {"type": ["number", "null"]},
                    "total_rent": {"type": ["number", "null"]},
                },
            },
        },
    }


class SchemaViolation(ValueError):
    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


def parse_result(raw: str | dict, taxonomy: Taxonomy | None = None) -> ClassificationResult:
    """Validiert die Anbieterantwort gegen das Schema und die Taxonomie (Codes muessen existieren)."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise SchemaViolation(f"Antwort ist kein JSON: {exc}", raw if isinstance(raw, str) else None) from exc
    try:
        result = ClassificationResult.model_validate(data)
    except ValidationError as exc:
        raise SchemaViolation(
            f"Antwort verletzt das Schema: {exc.errors()[:3]}", json.dumps(data)[:2000]
        ) from exc
    if taxonomy is not None:
        if result.subfolder:
            code = coerce_code(result.subfolder, taxonomy.subfolder_codes(result.category))
            if code is None:
                raise SchemaViolation(
                    f"Unterordner {result.subfolder} nicht in der Taxonomie für {result.category}",
                    json.dumps(data),
                )
            result = result.model_copy(update={"subfolder": code})
        if result.document_type:
            code = coerce_code(result.document_type, taxonomy.document_type_codes(result.category))
            if code is None:
                raise SchemaViolation(
                    f"Unterart {result.document_type} nicht in der Taxonomie für {result.category}",
                    json.dumps(data),
                )
            result = result.model_copy(update={"document_type": code})
    return result


def taxonomy_from_catalog() -> Taxonomy:
    """Taxonomie aus den Seeds (Kategorien, Unterordner, Unterarten) mit Codes und Definitionen (CR 5 und 6)."""
    from apps.documents.models import DocumentCategory, DocumentSubfolder, DocumentType

    categories = tuple(
        TaxonomyEntry(c.code, c.display_name or c.folder_name, getattr(c, "description", "") or "")
        for c in DocumentCategory.objects.filter(is_active=True).order_by("code")
    )
    subfolders: dict[str, list[TaxonomyEntry]] = {}
    for s in DocumentSubfolder.objects.select_related("category").order_by("category_id", "code"):
        subfolders.setdefault(s.category_id, []).append(
            TaxonomyEntry(s.code, s.display_name or s.folder_name)
        )
    types: dict[str, list[TaxonomyEntry]] = {}
    for t in (
        DocumentType.objects.filter(is_active=True)
        .select_related("subfolder")
        .order_by("category_id", "name")
    ):
        key = f"{t.category_id}/{t.subfolder.code}" if t.subfolder_id else t.category_id
        types.setdefault(key, []).append(TaxonomyEntry(t.code, t.name))
    return Taxonomy(
        categories, {k: tuple(v) for k, v in subfolders.items()}, {k: tuple(v) for k, v in types.items()}
    )
