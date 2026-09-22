"""KI-Schiedsrichter der Objektzuordnung (Zweck assign_object, Entscheidung 22.09.2026): greift immer, wenn die
Anwendung ein Eingangsdokument nicht eindeutig einem Objekt zuordnen kann (Entscheidung review oder none mit
Kandidaten). Die KI erhaelt den maskierten Textauszug, den Dateinamen ohne Personennamen und die Kandidaten mit
allen Anschriften des Gebaeudes (Eckobjekte). Sie beantwortet: Ist es ueberhaupt ein Objektdokument, welcher
Kandidat, welche Rolle haben weitere Anschriften (Rechnungsanschrift, Absender, Nachbar, Fahrtziel, Sammelbeleg)?
Ein sicherer Kandidat wird uebernommen, ein Dokument ohne Objektbezug bleibt im Eingang mit Fall, alles andere
wird Pruefall mit KI-Einschaetzung. Ein manuell verworfenes Objekt (Lernbeispiel reject) waehlt auch die KI nicht
automatisch. Router, Kostenlimit, Circuit Breaker und Protokoll (ai_calls) sind dieselben wie in Stufe 3."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.ai.excerpt import build_excerpt, mask_filename
from apps.ai.router import Router, default_router
from apps.ai.schema import SchemaViolation
from apps.config import store

PROMPT_VERSION = "2026-09-22.1"
MAX_CANDIDATES = 5
OTHER_ADDRESS_ROLES = (
    "none",
    "same_object",
    "billing_address",
    "sender_address",
    "neighbor",
    "travel_destination",
    "multiple_objects",
    "other",
)

SYSTEM_PROMPT = """Du prüfst für eine Hausverwaltung, zu welchem verwalteten Objekt (Liegenschaft) ein Dokument gehört. Du erhältst einen maskierten Textauszug (Bankdaten und Ausweisnummern sind durch Platzhalter ersetzt), den Dateinamen ohne Personennamen und eine Liste von Kandidaten mit Objektnummer, Verwaltungsart und allen Anschriften des jeweiligen Gebäudes.

Regeln in dieser Reihenfolge:
1. Ein Objektdokument betrifft das Gebäude, seine Eigentümergemeinschaft, Eigentümer, Mieter, Verträge, Abrechnungen, Instandhaltung oder Versicherung. Kein Objektdokument sind interne Unterlagen der Verwaltung, in denen eine Objektanschrift nur beiläufig vorkommt: Fahrtkostenabrechnung eines Mitarbeiters mit der Anschrift als Fahrtziel, Kontaktlisten, allgemeine Berechnungen, Übersichten über mehrere Objekte. Dann is_object_document false und object_number null.
2. Mehrere Anschriften desselben Kandidaten (Eckobjekt, mehrere Hausnummern) sind ein Objekt. Nennt das Dokument Anschriften verschiedener Kandidaten, prüfe die Rollen: Rechnungs- oder Absenderanschrift, Nachbargrundstück, Fahrtziel oder bloßer Verweis sind kein Objektbezug. Gibt es keinen klaren Hauptbezug (Sammelbeleg über mehrere Objekte), setze object_number null und multiple_objects true.
3. Wähle object_number ausschließlich aus den Kandidaten, genau wie angegeben. Rate nicht: Bist du unsicher, setze object_number null und eine niedrige Konfidenz.

Nenne in other_addresses_role die Rolle der weiteren Anschriften (none, same_object, billing_address, sender_address, neighbor, travel_destination, multiple_objects, other). Begründe kurz und ohne Personennamen. Antworte nur im vorgegebenen JSON-Schema."""


def _digits(value) -> int | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


class ObjectAssignmentResult(BaseModel):
    """Antwort des Schiedsrichters, strikt (additionalProperties false)."""

    model_config = ConfigDict(extra="forbid")
    is_object_document: bool
    object_number: str | None = None
    multiple_objects: bool = False
    other_addresses_role: str = "none"
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(max_length=400)

    def summary(self) -> dict:
        return {
            "purpose": "assign_object",
            "is_object_document": self.is_object_document,
            "object_number": self.object_number,
            "multiple_objects": self.multiple_objects,
            "other_addresses_role": self.other_addresses_role,
            "confidence": self.confidence,
        }


def response_json_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "is_object_document",
            "object_number",
            "multiple_objects",
            "other_addresses_role",
            "confidence",
            "reasoning",
        ],
        "properties": {
            "is_object_document": {"type": "boolean"},
            "object_number": {"type": ["string", "null"]},
            "multiple_objects": {"type": "boolean"},
            "other_addresses_role": {"type": "string", "enum": list(OTHER_ADDRESS_ROLES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string", "maxLength": 400},
        },
    }


@dataclass
class ObjectAssignmentRequest:
    """Request an den Router: Kandidaten ohne Personendaten (Objektnummer, Verwaltungsart, Anschriften, Bewertung,
    Belegarten), maskierter Auszug, Dateiname ohne Namen."""

    excerpt_masked: str
    filename_masked: str
    candidates: list[dict]
    hints: dict = field(default_factory=dict)
    purpose: ClassVar[str] = "assign_object"
    repair_instruction: ClassVar[str] = (
        "Verwende für object_number ausschließlich eine Objektnummer aus der Kandidatenliste oder null und fülle "
        "alle Pflichtfelder."
    )

    @property
    def mask_check_text(self) -> str:
        return self.filename_masked

    def build_messages(self, *, repair_hint: str | None = None) -> tuple[str, str]:
        payload = {
            "schema": response_json_schema(),
            "kandidaten": self.candidates,
            "hinweise": self.hints,
            "dateiname": self.filename_masked,
            "textauszug": self.excerpt_masked,
        }
        user = json.dumps(payload, ensure_ascii=False, indent=1)
        if repair_hint:
            user += f"\n\nHinweis zur Korrektur der vorherigen Antwort: {repair_hint}"
        return SYSTEM_PROMPT, user

    def parse(self, raw: str | dict) -> ObjectAssignmentResult:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise SchemaViolation(
                f"Antwort ist kein JSON: {exc}", raw if isinstance(raw, str) else None
            ) from exc
        try:
            result = ObjectAssignmentResult.model_validate(data)
        except ValidationError as exc:
            raise SchemaViolation(
                f"Antwort verletzt das Schema: {exc.errors()[:3]}", json.dumps(data)[:2000]
            ) from exc
        if result.object_number is not None:
            number = _digits(result.object_number)
            match = next((c for c in self.candidates if _digits(c.get("object_number")) == number), None)
            if number is None or match is None:
                raise SchemaViolation(
                    f"object_number {result.object_number} ist kein Kandidat", json.dumps(data)[:2000]
                )
            result = result.model_copy(update={"object_number": str(match["object_number"])})
        if result.other_addresses_role not in OTHER_ADDRESS_ROLES:
            result = result.model_copy(update={"other_addresses_role": "other"})
        return result


@dataclass
class ArbiterOutcome:
    status: str  # ok | disabled | skipped | budget_blocked | provider_error | blocked_by_mask_check
    object_id: int | None = None
    object_number: str | None = None
    is_object_document: bool | None = None
    multiple_objects: bool = False
    other_addresses_role: str | None = None
    confidence: float = 0.0
    reasoning: str | None = None
    provider: str | None = None
    call_id: int | None = None
    message: str | None = None

    def summary_text(self) -> str:
        if self.status != "ok":
            return f"KI nicht verfügbar ({self.status}): {self.message or ''}".strip().rstrip(":")
        parts = ["Objektdokument: " + ("ja" if self.is_object_document else "nein")]
        if self.object_number:
            parts.append(f"Vorschlag Objekt {self.object_number} (Konfidenz {self.confidence:.2f})")
        elif self.multiple_objects:
            parts.append("mehrere Objekte ohne Hauptbezug")
        if self.other_addresses_role and self.other_addresses_role != "none":
            parts.append(f"weitere Anschriften: {self.other_addresses_role}")
        if self.reasoning:
            parts.append(self.reasoning)
        return "; ".join(parts)

    def to_context(self) -> dict:
        return {
            "status": self.status,
            "provider": self.provider,
            "call_id": self.call_id,
            "object_number": self.object_number,
            "is_object_document": self.is_object_document,
            "multiple_objects": self.multiple_objects,
            "other_addresses_role": self.other_addresses_role,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "message": self.message,
            "summary": self.summary_text(),
        }


def enabled() -> bool:
    return bool(store.get("sync.assignment_ai_enabled", True))


def min_confidence() -> float:
    return float(store.get("sync.assignment_ai_min", 0.85))


def candidate_payload(objects_by_id: dict, ranked, *, limit: int = MAX_CANDIDATES) -> list[dict]:
    """Kandidaten fuer den Prompt ohne Personendaten: Nummer, Verwaltungsart, alle Anschriften des Gebaeudes,
    Bewertung und Belegarten mit Fundstellen (Adressen, Objektnummern; Dateinamen nur maskiert an anderer Stelle)."""
    from apps.objects.addresses import format_address_lines

    out: list[dict] = []
    for c in ranked[:limit]:
        obj = objects_by_id.get(c.object_id)
        if obj is None:
            continue
        addresses = [obj.address] if obj.address else []
        addresses += format_address_lines(getattr(obj, "additional_addresses", None) or []).splitlines()
        out.append(
            {
                "object_number": obj.object_number,
                "verwaltungsart": obj.management_type,
                "anschriften": addresses,
                "bewertung": round(float(c.score), 2),
                "belege": [
                    f"{e.kind}" + (f" ({e.role})" if e.role else "") + f": {e.text[:80]}"
                    for e in c.evidence
                    if e.kind in ("address", "object_number", "folder", "rule")
                ][:6],
                "widersprueche": list(c.contradictions)[:4],
            }
        )
    return out


def arbitrate(doc, ranked, *, text: str, router: Router | None = None, job=None) -> ArbiterOutcome:
    """Fragt die KI, wenn die lokale Zuordnung nicht eindeutig ist. Liefert nie eine Ausnahme nach aussen, die
    Zuordnung faellt bei jedem Fehler auf den Pruefall zurueck."""
    from apps.ai.services import party_names
    from apps.objects.models import ManagedObject

    if not enabled():
        return ArbiterOutcome(
            "disabled", message="KI-Schiedsrichter abgeschaltet (sync.assignment_ai_enabled)"
        )
    if not ranked:
        return ArbiterOutcome("skipped", message="keine Kandidaten")
    router = router or default_router()
    if not router.enabled_order():
        return ArbiterOutcome("disabled", message="Kein Anbieter freigegeben (AVV, ai.providers.<p>.enabled)")
    ids = [c.object_id for c in ranked[:MAX_CANDIDATES]]
    objects_by_id = {o.pk: o for o in ManagedObject.objects.filter(pk__in=ids)}
    names = [n for o in objects_by_id.values() for n in party_names(o)]
    pages = {i + 1: p for i, p in enumerate((text or "").split("\f"))}
    req = ObjectAssignmentRequest(
        excerpt_masked=build_excerpt(pages),
        filename_masked=mask_filename(doc.current_name or doc.original_name or "", names),
        candidates=candidate_payload(objects_by_id, ranked),
        hints={"kandidaten_gesamt": len(ranked), "seiten": len(pages)},
    )
    result = router.classify(req, obj=doc.object, document=doc, job=job, run=getattr(job, "run", None))
    call_id = result.call.pk if result.call else None
    if result.result is None:
        return ArbiterOutcome(
            result.status, provider=result.provider, message=result.message, call_id=call_id
        )
    r = result.result
    object_id = None
    if r.object_number is not None:
        wanted = _digits(r.object_number)
        object_id = next((pk for pk, o in objects_by_id.items() if _digits(o.object_number) == wanted), None)
    return ArbiterOutcome(
        "ok",
        object_id=object_id,
        object_number=r.object_number,
        is_object_document=r.is_object_document,
        multiple_objects=r.multiple_objects,
        other_addresses_role=r.other_addresses_role,
        confidence=float(r.confidence),
        reasoning=r.reasoning,
        provider=result.provider,
        call_id=call_id,
    )
