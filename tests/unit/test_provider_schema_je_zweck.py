"""BaseProvider.classify reicht das Antwortschema des jeweiligen Requests an send durch: Klassifikation und
Objektzuordnung haben verschiedene Schemata (Befund 22.09.2026: Zuordnungsantworten scheiterten, weil der Anbieter
fuer jede Anfrage das Klassifikationsschema erzwang)."""

from __future__ import annotations

import json

from apps.ai import assignment
from apps.ai.provider import BaseProvider, ProviderConfig, RawResponse
from apps.ai.schema import ClassificationRequest, Taxonomy, TaxonomyEntry, response_json_schema


class CaptureProvider(BaseProvider):
    name = "capture"

    def __init__(self, answer: dict):
        self.answer = answer
        self.seen: list[tuple[dict | None, str]] = []

    def send(self, system, user, cfg, *, schema=None, schema_name="klassifikation"):
        self.seen.append((schema, schema_name))
        return RawResponse(json.dumps(self.answer), 10, 5, 200, cfg.model)


CFG = ProviderConfig(name="capture", enabled=True, model="testmodell")


def test_objektzuordnung_sendet_ihr_eigenes_schema():
    provider = CaptureProvider(
        {
            "is_object_document": True,
            "object_number": "623",
            "multiple_objects": False,
            "other_addresses_role": "none",
            "confidence": 0.9,
            "reasoning": "Anschrift des Objekts im Text.",
        }
    )
    req = assignment.ObjectAssignmentRequest(
        excerpt_masked="Energieausweis Musterstraße 49, 12345 Musterstadt",
        filename_masked="Energieausweis.pdf",
        candidates=[{"object_number": "623", "anschriften": ["Musterstraße 49, 12345 Musterstadt"]}],
    )
    outcome = provider.classify(req, CFG)
    assert provider.seen == [(assignment.response_json_schema(), "objektzuordnung")]
    assert outcome.error is None and outcome.result.object_number == "623"
    assert provider.seen[0][0] != response_json_schema()


def test_klassifikation_sendet_das_klassifikationsschema():
    provider = CaptureProvider({})
    taxonomy = Taxonomy(categories=(TaxonomyEntry("05", "Eigentümerakte"),), subfolders={}, document_types={})
    req = ClassificationRequest(
        excerpt_masked="Einzelabrechnung 2025",
        filename_masked="Einzelabrechnung.pdf",
        management_type="weg",
        taxonomy=taxonomy,
    )
    outcome = provider.classify(req, CFG)
    assert provider.seen == [(response_json_schema(), "klassifikation")]
    assert outcome.error is not None  # leere Antwort verletzt das Schema, keine Ausnahme nach aussen
