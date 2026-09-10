"""Namens-Splitter: alle elf Faelle aus Fachentwurf H 6.4 (synthetische Namen)."""

from __future__ import annotations

import pytest

from apps.imports.names import split_names


def names(result):
    return [(p.last_name, p.first_name) for p in result.persons]


def test_kommaform_und():
    for text in ("Mustermann, Erika & Max", "Mustermann, Erika u. Max"):
        r = split_names(text)
        assert names(r) == [("Mustermann", "Erika"), ("Mustermann", "Max")] and r.band == "high"


def test_leerzeichenform_mittel():
    for text in ("Erika und Max Mustermann", "Erika u. Max Mustermann"):
        r = split_names(text)
        assert names(r) == [("Mustermann", "Erika"), ("Mustermann", "Max")] and r.band == "medium"


def test_schraegstrich_trennt_personen():
    r = split_names("Mustermann, Erika / Beispiel, Hans")
    assert names(r) == [("Mustermann", "Erika"), ("Beispiel", "Hans")] and r.band == "high"


def test_gbr_kein_split():
    r = split_names("Muster GbR")
    assert (
        r.entity_type == "legal_entity"
        and r.company_name == "Muster GbR"
        and r.persons == ()
        and r.band == "high"
    )


def test_erbengemeinschaft():
    r = split_names("Erbengemeinschaft Mustermann")
    assert (
        r.entity_type == "community" and r.company_name == "Erbengemeinschaft Mustermann" and r.band == "high"
    )


def test_co_zusatz():
    r = split_names("Mustermann, Erika c/o Beispiel Hausverwaltung")
    assert (
        names(r) == [("Mustermann", "Erika")]
        and r.correspondence_addition == "c/o Beispiel Hausverwaltung"
        and r.band == "high"
    )


def test_eheleute_zwei_personen_ohne_vornamen():
    r = split_names("Eheleute Mustermann")
    assert names(r) == [("Mustermann", None), ("Mustermann", None)]
    assert r.band == "low" and "first_names_missing" in r.reasons


def test_titel_und_doppelname():
    r = split_names("Dr. Erika Mustermann-Beispiel")
    assert names(r) == [("Mustermann-Beispiel", "Erika")] and r.persons[0].title == "Dr." and r.band == "high"


def test_vierteiliger_name_unsicher():
    r = split_names("Anna Maria Mustermann Beispiel")
    assert r.confidence < 0.6 and r.is_uncertain and "name_split_ambiguous" in r.reasons


@pytest.mark.parametrize("text", ["", "   ", '"'])
def test_leer(text):
    r = split_names(text)
    assert r.persons == () and "name_missing" in r.reasons


def test_anrede_und_einzelperson():
    r = split_names("Frau Erika Mustermann")
    assert names(r) == [("Mustermann", "Erika")] and r.persons[0].salutation == "Frau" and r.band == "high"
    r = split_names("Mustermann")
    assert names(r) == [("Mustermann", None)] and r.band == "medium"


def test_schwellen_konfigurierbar():
    r = split_names("Erika und Max Mustermann", thresholds={"high": 0.7, "medium": 0.5})
    assert r.band == "high"
