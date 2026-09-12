"""Normalisierung der Objektzuordnung: Strasse und Str., ss und ß, Hausnummernzusaetze und Bereiche, PLZ und
Ort, OCR-Verwechslungen nur im Ziffernkontext, Adresssuche mit Rolle und Seite (Baustein B)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from apps.sync.assignment.normalize import (
    address_variants,
    build_entries,
    find_addresses,
    house_numbers_overlap,
    normalize_street,
    normalize_text,
    parse_house_number,
    parse_own_address,
    repair_ocr_digits,
)


@dataclass
class Obj:
    pk: int
    street: str
    house_number: str
    postal_code: str
    city: str
    object_number: str = "1"


def test_normalize_text_kleinschreibung_umlaute_und_ss():
    assert normalize_text("Große  Straße") == "grosse strasse"
    assert normalize_text("Müller – Köln") == "mueller - koeln"
    assert normalize_text(None) == ""


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [
        ("Hauptstraße", "hauptstr"),
        ("Hauptstrasse", "hauptstr"),
        ("Haupt Str.", "hauptstr"),
        ("Hauptstr", "hauptstr"),
        ("Karl-Marx-Straße", "karl marxstr"),
        ("Karl Marx Str.", "karl marxstr"),
        ("An der Alten Mühle", "an der alten muehle"),
        ("Musterweg", "musterweg"),
    ],
)
def test_normalize_street_varianten(eingabe, erwartet):
    assert normalize_street(eingabe) == erwartet


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [
        ("12", (12, "", None)),
        ("12a", (12, "a", None)),
        ("12 a", (12, "a", None)),
        ("12B", (12, "b", None)),
        ("12-14", (12, "", 14)),
        ("12 - 14", (12, "", 14)),
        ("12/14", (12, "", 14)),
        ("1O", (10, "", None)),
        ("l2a", (12, "a", None)),
        ("2S-27", (25, "", 27)),
        ("l", (1, "", None)),
        ("", (None, "", None)),
        ("Nr.", (None, "", None)),
    ],
)
def test_parse_house_number(eingabe, erwartet):
    assert parse_house_number(eingabe) == erwartet


def test_ocr_reparatur_nur_im_ziffernkontext():
    assert repair_ocr_digits("1o") == "10"
    assert repair_ocr_digits("12b") == "12b"  # Zusatz b bleibt, kein 8
    assert repair_ocr_digits("12s") == "12s"
    assert repair_ocr_digits("b12") == "812"
    assert repair_ocr_digits("abc") == "abc"


def test_zusaetze_und_bereiche_trennen_oder_ueberlappen():
    assert house_numbers_overlap(parse_house_number("12a"), parse_house_number("12 a"))
    assert not house_numbers_overlap(parse_house_number("12a"), parse_house_number("12b"))
    assert not house_numbers_overlap(parse_house_number("12"), parse_house_number("12a"))
    assert house_numbers_overlap(parse_house_number("12-14"), parse_house_number("13"))
    assert house_numbers_overlap(parse_house_number("12"), parse_house_number("10-14"))
    assert not house_numbers_overlap(parse_house_number("12-14"), parse_house_number("16"))


def test_address_variants_enthalten_plz_und_ort():
    v = address_variants("Musterstraße", "12a", "12345", "Musterstadt")
    assert "musterstr 12a" in v
    assert "musterstr 12 a" in v
    assert "musterstr 12a 12345 musterstadt" in v
    assert address_variants("", "1") == set()


def test_parse_own_address_zeichenkette_und_tupel():
    e = parse_own_address("Rheinpromenade 13, 40789 Monheim am Rhein")
    assert e is not None
    assert (e.street, e.house, e.postal_code, e.city) == (
        "rheinpromenade",
        (13, "", None),
        "40789",
        "monheim rhein",
    )
    t = parse_own_address(("Rheinpromenade", "13", "40789", "Monheim am Rhein"))
    assert t is not None and t.address_key == e.address_key
    assert parse_own_address("ohne Nummer") is None


def _index():
    return build_entries(
        [
            Obj(1, "Musterweg", "1", "12345", "Musterstadt"),
            Obj(2, "Hauptstraße", "12a", "40789", "Monheim am Rhein"),
            Obj(3, "Hauptstraße", "12b", "40789", "Monheim am Rhein"),
            Obj(4, "Hauptstraße", "12a", "50667", "Köln"),
            Obj(5, "Karl-Marx-Straße", "3-5", "50667", "Köln"),
        ],
        own_addresses=("Rheinpromenade 13, 40789 Monheim am Rhein",),
    )


def test_find_addresses_rolle_seite_und_ortsbestaetigung():
    text = [
        "Hausverwaltung Müller GmbH\nRheinpromenade 13\n40789 Monheim am Rhein\n\nLeistungsort: Haupt Str. 12 a, 40789 Monheim",
        "Seite 2: Rechnungsanschrift Musterweg l, 12345 Musterstadt",
    ]
    hits = find_addresses(text, _index())
    own = [h for h in hits if h.object_id is None]
    assert len(own) == 1 and own[0].role == "own" and own[0].postal_match
    leistung = [h for h in hits if h.object_id == 2]
    assert len(leistung) == 1
    assert leistung[0].role == "object" and leistung[0].role_word == "leistungsort"
    assert leistung[0].postal_match and leistung[0].page == 1
    assert leistung[0].matched_text == "Haupt Str. 12 a"
    assert not [h for h in hits if h.object_id in (3, 4)]  # 12b und Koeln bleiben aussen vor
    rechnung = [h for h in hits if h.object_id == 1]
    assert len(rechnung) == 1 and rechnung[0].role == "billing" and rechnung[0].page == 2
    assert rechnung[0].postal_match and rechnung[0].city_match


def test_find_addresses_gleiche_strasse_in_zwei_orten():
    hits = find_addresses("Objekt Hauptstraße 12a, 50667 Köln", _index())
    assert [h.object_id for h in hits] == [4]
    assert hits[0].role == "object"
    ohne_ort = find_addresses("Hauptstraße 12a", _index())
    assert sorted(h.object_id for h in ohne_ort) == [2, 4]
    assert all(h.ambiguous_location for h in ohne_ort)


def test_find_addresses_bereich_und_bindestrich_strasse():
    hits = find_addresses("Karl Marx Str. 4 in Köln", _index())
    assert [h.object_id for h in hits] == [5] and hits[0].city_match
    hits = find_addresses("Karl-Marx-Straße 3-5, 50667 Köln", _index())
    assert [h.object_id for h in hits] == [5] and hits[0].postal_match


def test_find_addresses_absender_in_kopfzeile():
    hits = find_addresses("Firma Beispiel, Musterweg 1, 12345 Musterstadt, Telefon 0123 4567", _index())
    assert len(hits) == 1 and hits[0].role == "supplier" and hits[0].role_word == "telefon"


def test_find_addresses_ohne_treffer():
    assert find_addresses("Kein Bezug zu einer bekannten Anschrift", _index()) == []
    assert find_addresses("", _index()) == []
