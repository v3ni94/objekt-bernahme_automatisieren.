"""Entitaetenerkennung (E 3.1): Einheitenvarianten, Betraege mit Kontext, Datum, Zeitraum, Objektmarker eigen und fremd,
Personen ueber Kontext und unscharfen Abgleich, IBAN nur aus Maskierungstreffern."""

from __future__ import annotations

import pytest

from apps.pipeline.entities import build_gazetteer, extract_page, unit_variants

pytestmark = pytest.mark.django_db


def test_unit_variants():
    v = unit_variants("WE 14")
    for expected in ("WE14", "WE 14", "WE-14", "WE_14", "Wohnung 14", "Whg. 14", "Einheit 14", "Wohnung 014"):
        assert expected in v, expected
    assert "Wohnung14" in v
    assert "Tiefgaragenstellplatz 3" in unit_variants("TG 3")


def test_extract_page_alle_typen(objekt, stammdaten):
    gaz = build_gazetteer(objekt)
    text = (
        "Objekt 623 Musterstraße 49, Musterstadt\n"
        "Einzelabrechnung Wohnung 14 für das Abrechnungsjahr 2024\n"
        "Eigentümerin: Frau Erika Mustermann, Zeitraum 01.01.2024 bis 31.12.2024\n"
        "Nachzahlung 1.234,56 EUR fällig am 15.03.2025, Hausgeld 320,00 € ab 04/2025\n"
        "Nicht dieses Objekt: Beispielweg 7\n"
        "Stellplatz WE-1 gehört nicht dazu, aber WE01 schon.\n"
    )
    hits = [
        {
            "kind": "iban",
            "start": 10,
            "end": 20,
            "last4": "3000",
            "hmac": stammdaten["owner"].iban_hash.hex(),
            "mod97_valid": True,
        }
    ]
    ents = extract_page(text, 1, gaz, hits)
    by_type: dict[str, list] = {}
    for e in ents:
        by_type.setdefault(e.entity_type, []).append(e)
    units = {(e.value_text, e.matched_unit_id) for e in by_type["unit_label"]}
    assert ("Wohnung 14", stammdaten["we14"].pk) in units
    assert ("WE-1", stammdaten["we1"].pk) in units and ("WE01", stammdaten["we1"].pk) in units
    amounts = {(e.value_normalized, e.extra.get("context")) for e in by_type["amount"]}
    assert ("1234.56", "Nachzahlung") in amounts and ("320.00", "Hausgeld") in amounts
    assert {e.value_normalized for e in by_type["date"]} >= {"2024-01-01", "2024-12-31", "2025-03-15"}
    periods = {(e.extra.get("kind"), e.value_normalized) for e in by_type["period"]}
    assert ("year", "2024") in periods and ("range", "2024") in periods and ("month", "2025-04") in periods
    markers = {(e.extra.get("marker"), e.value_normalized) for e in by_type["object_number"]}
    assert ("own", "own") in markers and ("foreign", "624") in markers
    persons = [e for e in by_type["person_name"] if e.matched_owner_id == stammdaten["owner"].pk]
    assert persons and persons[0].match_confidence >= 0.78
    iban = by_type["iban"][0]
    assert (
        iban.iban_last4 == "3000" and iban.value_text == "[IBAN_****3000]" and iban.value_normalized is None
    )
