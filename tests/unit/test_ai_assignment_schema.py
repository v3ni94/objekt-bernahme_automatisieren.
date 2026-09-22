"""Request und Antwortschema des KI-Schiedsrichters (Zweck assign_object): Prompt ohne Personendaten, Antwort nur
mit Kandidaten, Normalisierung der Objektnummer, Zusammenfassung fuer Pruefcenter und Protokoll."""

from __future__ import annotations

import json

import pytest

from apps.ai.assignment import (
    OTHER_ADDRESS_ROLES,
    ArbiterOutcome,
    ObjectAssignmentRequest,
    response_json_schema,
)
from apps.ai.schema import SchemaViolation

KANDIDATEN = [
    {"object_number": "623", "verwaltungsart": "weg", "anschriften": ["Musterstraße 49, 12345 Musterstadt"]},
    {"object_number": "0624", "verwaltungsart": "rental", "anschriften": ["Beispielweg 7"]},
]


def _req() -> ObjectAssignmentRequest:
    return ObjectAssignmentRequest(
        excerpt_masked="Rechnung Dach, Objekt Musterstraße 49; Rechnungsanschrift Beispielweg 7",
        filename_masked="Rechnung_[NAME].pdf",
        candidates=KANDIDATEN,
        hints={"kandidaten_gesamt": 2},
    )


def test_prompt_traegt_schema_kandidaten_und_auszug():
    system, user = _req().build_messages()
    assert "verwalteten Objekt" in system and "JSON-Schema" in system
    payload = json.loads(user)
    assert payload["schema"] == response_json_schema() and payload["kandidaten"] == KANDIDATEN
    assert payload["dateiname"] == "Rechnung_[NAME].pdf" and "Musterstraße 49" in payload["textauszug"]
    _, repariert = _req().build_messages(repair_hint="object_number fehlt")
    assert repariert.endswith("Hinweis zur Korrektur der vorherigen Antwort: object_number fehlt")
    assert ObjectAssignmentRequest.purpose == "assign_object"
    assert set(response_json_schema()["properties"]["other_addresses_role"]["enum"]) == set(
        OTHER_ADDRESS_ROLES
    )


def test_antwort_nur_mit_kandidaten_und_normalisierter_nummer():
    r = _req().parse(
        json.dumps(
            {
                "is_object_document": True,
                "object_number": "624",
                "multiple_objects": False,
                "other_addresses_role": "billing_address",
                "confidence": 0.9,
                "reasoning": "Leistungsort.",
            }
        )
    )
    assert r.object_number == "0624" and r.summary()["object_number"] == "0624"
    with pytest.raises(SchemaViolation, match="kein Kandidat"):
        _req().parse(
            {"is_object_document": True, "object_number": "999", "confidence": 0.9, "reasoning": "x"}
        )
    with pytest.raises(SchemaViolation, match="verletzt das Schema"):
        _req().parse(
            {"is_object_document": True, "object_number": "623", "confidence": 1.5, "reasoning": "x"}
        )
    with pytest.raises(SchemaViolation, match="kein JSON"):
        _req().parse("Das Dokument gehört zu Objekt 623.")
    frei = _req().parse(
        {
            "is_object_document": False,
            "object_number": None,
            "other_addresses_role": "irgendwas",
            "confidence": 0.7,
            "reasoning": "Fahrtkosten.",
        }
    )
    assert frei.other_addresses_role == "other" and frei.object_number is None


def test_zusammenfassung_fuer_pruefcenter():
    ok = ArbiterOutcome(
        "ok",
        object_id=1,
        object_number="623",
        is_object_document=True,
        other_addresses_role="billing_address",
        confidence=0.92,
        reasoning="Leistungsort.",
    )
    assert ok.summary_text() == (
        "Objektdokument: ja; Vorschlag Objekt 623 (Konfidenz 0.92); weitere Anschriften: billing_address; "
        "Leistungsort."
    )
    assert ok.to_context()["summary"] == ok.summary_text() and ok.to_context()["status"] == "ok"
    nein = ArbiterOutcome("ok", is_object_document=False, multiple_objects=True, confidence=0.8)
    assert nein.summary_text() == "Objektdokument: nein; mehrere Objekte ohne Hauptbezug"
    gesperrt = ArbiterOutcome("budget_blocked", message="Monatsdeckel 5 EUR erreicht")
    assert gesperrt.summary_text() == "KI nicht verfügbar (budget_blocked): Monatsdeckel 5 EUR erreicht"
