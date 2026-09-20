"""Antworten der Stufe 3 mit Code plus Name ("04 Hausgeld") werden auf den Code zurueckgefuehrt (Probe 20.09.2026)."""

from __future__ import annotations

import pytest

from apps.ai.schema import SchemaViolation, Taxonomy, TaxonomyEntry, coerce_code, parse_result


def _taxonomy() -> Taxonomy:
    cats = tuple(TaxonomyEntry(c, n) for c, n in (("05", "Eigentümer"), ("06", "Unklar")))
    subs = {"05": (TaxonomyEntry("04", "Hausgeld"),), "06": (TaxonomyEntry("01", "unklar"),)}
    types = {"05/04": (TaxonomyEntry("hausgeldabrechnung", "Hausgeldabrechnung"),)}
    return Taxonomy(cats, subs, types)


def _antwort(**over) -> dict:
    data = {
        "object_related": True,
        "category": "05",
        "subfolder": "04",
        "document_type": "hausgeldabrechnung",
        "period": {"year": 2025, "from": None, "to": None, "document_date": None},
        "mentioned_units": ["WE01"],
        "mentioned_parties": [],
        "confidence": 0.93,
        "reasoning": "Hausgeldabrechnung einer Einheit",
        "lease": None,
    }
    data.update(over)
    return data


@pytest.mark.parametrize(
    "wert,erwartet",
    [
        ("04", "04"),
        ("04 Hausgeld", "04"),
        ("04 (Hausgeld)", "04"),
        ("  04  ", "04"),
        ("Hausgeld", None),
        ("99", None),
    ],
)
def test_coerce_code(wert, erwartet):
    assert coerce_code(wert, {"04", "01"}) == erwartet


def test_code_mit_name_wird_auf_code_zurueckgefuehrt():
    res = parse_result(_antwort(subfolder="04 Hausgeld", category="05 Eigentümer"), _taxonomy())
    assert res.category == "05" and res.subfolder == "04" and res.document_type == "hausgeldabrechnung"


def test_unterart_mit_name_wird_zurueckgefuehrt():
    res = parse_result(_antwort(document_type="hausgeldabrechnung (Hausgeldabrechnung)"), _taxonomy())
    assert res.document_type == "hausgeldabrechnung"


def test_fremder_unterordner_bleibt_schemaverletzung():
    with pytest.raises(SchemaViolation):
        parse_result(_antwort(subfolder="07 Sonstiges"), _taxonomy())


def test_fremde_kategorie_bleibt_schemaverletzung():
    with pytest.raises(SchemaViolation):
        parse_result(_antwort(category="Eigentümer"), _taxonomy())
