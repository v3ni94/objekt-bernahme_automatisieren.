"""Objektnummer-Erkennung (Befund 4, F4) und Einheitennormalisierung (H 6.5, Befund 4). Synthetische Nummern (B-39)."""

from __future__ import annotations

import pytest

from apps.drive.object_numbers import (
    normalize_object_number,
    object_folder_name,
    parse_object_number,
    same_object,
)
from apps.objects.units import normalize_label, parse_unit_label, same_unit

MAPPING = {
    "WE": "apartment",
    "WOHNUNG": "apartment",
    "GE": "commercial",
    "S": "parking",
    "ST": "parking",
    "STP": "parking",
    "SP": "parking",
    "STELLPLATZ": "parking",
    "GA": "garage",
    "GARAGE": "garage",
    "TG": "underground_parking",
    "KE": "cellar",
    "KELLER": "cellar",
    "HAUS": "other",
    "CONTAINER": "other",
    "LAGERHALLE": "other",
    "MVW": "other",
}


@pytest.mark.parametrize(
    ("ordner", "erwartet"),
    [
        ("623 Musterstadt, Musterweg 1", 623),
        ("0631 Musterstadt", 631),
        ("631 Musterstadt", 631),
        ("6230 Musterstadt", 6230),
        ("82_Musterdorf", 82),
        ("10014 Musterstadt", 10014),
        ("700-Musterstadt", 700),
        ("625", 625),
        ("624.Beispielstadt", 624),
    ],
)
def test_objektnummer_erkannt(ordner, erwartet):
    match = parse_object_number(ordner)
    assert match is not None and match.numeric == erwartet


@pytest.mark.parametrize(
    "ordner", ["Musterstadt 623", "1 Musterstadt", "1234567 Musterstadt", "623a Musterstadt", "", "  "]
)
def test_objektnummer_nicht_erkannt(ordner):
    assert parse_object_number(ordner) is None


def test_6230_ist_nicht_623_aber_0631_ist_631():
    assert not same_object(parse_object_number("6230 Musterstadt").digits, "623")
    assert same_object(
        parse_object_number("0631 Musterstadt").digits, parse_object_number("631 Musterstadt").digits
    )


def test_stellenzahl_konfigurierbar():
    assert parse_object_number("1 Musterstadt", digits_min=1) is not None
    assert parse_object_number("6230 Musterstadt", digits_max=3) is None
    with pytest.raises(ValueError):
        parse_object_number("1", digits_min=3, digits_max=2)


def test_rest_und_normalisierung():
    match = parse_object_number("0631 Musterstadt, Musterweg 1")
    assert match.rest == "Musterstadt, Musterweg 1" and match.normalized == "631"
    assert normalize_object_number("0631") == "631"
    assert normalize_object_number("82", zero_pad_to=3) == "082"
    with pytest.raises(ValueError):
        normalize_object_number("abc")


def test_objektordnername_aus_muster():
    pattern = "{number} {city}, {street} {house_number}"
    assert (
        object_folder_name(pattern, number="623", city="Musterstadt", street="Musterweg", house_number="1")
        == "623 Musterstadt, Musterweg 1"
    )
    assert object_folder_name(pattern, number="623", city="Musterstadt") == "623 Musterstadt"


# ---------------------------------------------------------------- Einheiten
@pytest.mark.parametrize(
    ("label", "prefix", "number", "unit_type", "normalized"),
    [
        ("WE3", "WE", "3", "apartment", "WE3"),
        ("WE 3", "WE", "3", "apartment", "WE3"),
        ("WE 14", "WE", "14", "apartment", "WE14"),
        ("WE14", "WE", "14", "apartment", "WE14"),
        ("WE01", "WE", "1", "apartment", "WE1"),
        ("WE 14a", "WE", "14", "apartment", "WE14A"),
        ("S4", "S", "4", "parking", "S4"),
        ("Garage 4", "Garage", "4", "garage", "GARAGE4"),
        ("GE 1", "GE", "1", "commercial", "GE1"),
        ("MVW 2", "MVW", "2", "other", "MVW2"),
        ("Stellplatz Nr. 12", "Stellplatz", "12", "parking", "STELLPLATZ12"),
        ("GA7", "GA", "7", "garage", "GA7"),
        ("TG7", "TG", "7", "underground_parking", "TG7"),
        ("SP2", "SP", "2", "parking", "SP2"),
        ("GA 3", "GA", "3", "garage", "GA3"),
        ("GEN 2", "GEN", "2", "other", "GEN2"),
        ("ST12", "ST", "12", "parking", "ST12"),
        ("Haus 3", "Haus", "3", "other", "HAUS3"),
        ("Container 1", "Container", "1", "other", "CONTAINER1"),
        ("Wohnung 5", "Wohnung", "5", "apartment", "WOHNUNG5"),
        ("Lagerhalle 2", "Lagerhalle", "2", "other", "LAGERHALLE2"),
        ("STP3", "STP", "3", "parking", "STP3"),
        ("Keller 2", "Keller", "2", "cellar", "KELLER2"),
    ],
)
def test_einheitenmuster(label, prefix, number, unit_type, normalized):
    parsed = parse_unit_label(label, MAPPING)
    assert (parsed.prefix, parsed.number, parsed.unit_type, parsed.label_normalized) == (
        prefix,
        number,
        unit_type,
        normalized,
    )


def test_praefixlose_nummer():
    parsed = parse_unit_label("7", MAPPING)
    assert parsed.number == "7" and parsed.unit_type == "apartment" and "prefix_missing" in parsed.reasons


def test_lagezusatz_nach_bindestrich():
    parsed = parse_unit_label("WE 14 - 2.OG rechts", MAPPING)
    assert parsed.number == "14" and parsed.rest == "2.OG rechts" and parsed.label_normalized == "WE14"
    assert parsed.label == "WE 14"


def test_sonderform_nicht_zerlegt():
    parsed = parse_unit_label("VE1_SIL2_3.L", MAPPING)
    assert (
        parsed.number is None and parsed.unit_type == "other" and parsed.reasons == ("unit_label_unparsed",)
    )
    assert parsed.label_normalized == "VE1_SIL2_3.L"


def test_we14_und_we_14_gleich_garage_3_verschieden():
    assert same_unit("WE 14", "WE14")
    assert same_unit("WE01", "WE 1")
    assert not same_unit("WE 3", "Garage 3")
    assert normalize_label("WE01", strip_leading_zeros=False) == "WE01"
    assert normalize_label("we 01", strip_leading_zeros=False) == "WE01"
