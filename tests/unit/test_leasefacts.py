"""Vertragsdaten aus Mietvertragstexten (12.09.2026): Mieter, Einheit, Beginn, Miete, Vorauszahlungen, Kaution."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.classification.leasefacts import (
    LeaseFacts,
    extract_lease_facts,
    extract_unit_hint,
    facts_from_stage3,
    parse_party,
    split_parties,
)

VERTRAG = """Mietvertrag für Wohnraum

zwischen
Hausverwaltung Müller GmbH, Rheinpromenade 13, 40789 Monheim am Rhein, vertreten durch Timo Müller
- nachfolgend Vermieter genannt -

und
Herrn Max Mustermann und Frau Erika Mustermann, wohnhaft Alte Straße 1, 41836 Hückelhoven
- nachfolgend Mieter genannt -

§ 1 Mietsache
Vermietet wird die Wohnung Nr. 3 im 2. OG links des Hauses Shalomweg 3, 41836 Hückelhoven.

§ 2 Mietzeit
Das Mietverhältnis beginnt am 01.03.2024 und läuft auf unbestimmte Zeit.

§ 3 Miete
Die Nettokaltmiete beträgt monatlich 650,00 EUR.
Betriebskostenvorauszahlung: 180,00 EUR
Heizkostenvorauszahlung: 90,00 EUR
Gesamtmiete: 920,00 EUR

§ 4 Kaution
Der Mieter leistet eine Kaution in Höhe von 1.950,00 EUR.
"""


def test_mietvertrag_vollstaendig():
    f = extract_lease_facts(VERTRAG, exclude_names=["Hausverwaltung Müller GmbH"])
    assert [(t.first_name, t.last_name, t.salutation) for t in f.tenants] == [
        ("Max", "Mustermann", "Herr"),
        ("Erika", "Mustermann", "Frau"),
    ]
    assert f.landlord_names == ["Hausverwaltung Müller GmbH"]
    assert f.unit_hint == "WE 3"
    assert f.start_date == date(2024, 3, 1) and f.end_date is None
    assert f.base_rent == Decimal("650.00")
    assert f.utilities_prepayment == Decimal("180.00")
    assert f.heating_prepayment == Decimal("90.00")
    assert f.total_rent == Decimal("920.00")
    assert f.deposit_amount == Decimal("1950.00")
    d = f.as_dict()
    assert d["start_date"] == "2024-03-01" and d["base_rent"] == "650.00"
    again = LeaseFacts.from_dict(d)
    assert again.base_rent == f.base_rent and again.tenants[0].last_name == "Mustermann"


def test_mieter_zeile_firma_und_kaution_als_vielfaches():
    text = (
        "Gewerbemietvertrag\nVermieter: Erwin Eigner\nMieter: Beispiel Handels GmbH\n"
        "Mietobjekt: Gewerbeeinheit GE 2, Erdgeschoss rechts\nMietbeginn: 15.07.2023, befristet bis 14.07.2028\n"
        "Kaltmiete 1.200,00 €\nDie Kaution beträgt drei Nettokaltmieten."
    )
    f = extract_lease_facts(text)
    assert len(f.tenants) == 1 and f.tenants[0].kind == "legal_entity"
    assert f.tenants[0].company_name == "Beispiel Handels GmbH"
    assert f.landlord_names == ["Eigner"]
    assert f.unit_hint == "GE 2"
    assert f.start_date == date(2023, 7, 15) and f.end_date == date(2028, 7, 14)
    assert f.deposit_amount == Decimal("3600.00")


def test_lagebezeichnung_ohne_nummer_und_eheleute():
    text = "Mieter: Eheleute Karl und Ute Beispiel\nMietsache: Dachgeschoss links, Hauptstraße 5\nGrundmiete 500 EUR"
    f = extract_lease_facts(text)
    assert [(t.first_name, t.last_name) for t in f.tenants] == [("Karl", "Beispiel"), ("Ute", "Beispiel")]
    assert f.unit_hint == "DG links"
    assert f.base_rent == Decimal("500")


def test_vermieter_wird_nicht_zum_mieter():
    text = "zwischen Frau Vera Vermieterin - nachfolgend Vermieterin genannt - und Herrn Tom Test - nachfolgend Mieter genannt -"
    f = extract_lease_facts(text)
    assert [t.last_name for t in f.tenants] == ["Test"] and f.landlord_names == ["Vermieterin"]


def test_parse_party_und_split():
    assert parse_party("Herrn Dr. Hans Peter Müller, geb. 01.01.1970").last_name == "Müller"
    assert parse_party("Firma Muster Bau AG").kind == "legal_entity"
    assert parse_party("und") is None
    parts = split_parties("Frau Anna Alt und Herr Bernd Neu")
    assert [(p.first_name, p.last_name) for p in parts] == [("Anna", "Alt"), ("Bernd", "Neu")]
    assert extract_unit_hint("Es wird die Wohnung im 1. Obergeschoss rechts vermietet") == "1. OG rechts"
    assert extract_unit_hint("kein Bezug") is None


def test_stufe3_ergaenzt_und_ersetzt_namen():
    regel = extract_lease_facts("Mieter: Max Mustermann\nKaltmiete 400,00 EUR")
    ki = facts_from_stage3(
        {
            "tenant_names": ["Maximilian Mustermann"],
            "unit": "WE 4",
            "start_date": "2024-01-01",
            "end_date": None,
            "base_rent": None,
            "utilities_prepayment": 120.5,
            "heating_prepayment": None,
            "deposit_amount": 1200,
            "total_rent": None,
        }
    )
    m = regel.merge(ki)
    assert m.tenants[0].first_name == "Maximilian" and m.base_rent == Decimal("400.00")
    assert m.unit_hint == "WE 4" and m.start_date == date(2024, 1, 1)
    assert m.utilities_prepayment == Decimal("120.5") and m.deposit_amount == Decimal("1200")
    assert m.source == "merged"
    assert facts_from_stage3({"tenant_names": [], "unit": None}) is None
    assert regel.merge(None) is regel
