"""Feldnormalisierung des Imports (H 6.3, M3 Schritt 4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.imports import normalize as n


@pytest.mark.parametrize(
    ("text", "street", "number"),
    [
        ("Musterweg 12a", "Musterweg", "12a"),
        ("Musterstraße 3-5", "Musterstraße", "3-5"),
        ("Am Muster 1 b", "Am Muster", "1b"),
        ("Musterweg, 7", "Musterweg", "7"),
        ("Postfach", "Postfach", ""),
    ],
)
def test_adresse(text, street, number):
    r = n.split_street(text)
    assert r.value == (street, number)


def test_telefon_mobil():
    assert n.classify_phone("0171 1234567").value[0] == "mobile"
    assert n.classify_phone("+49 171 1234567").value[0] == "mobile"
    assert n.classify_phone("0211 123456").value[0] == "phone"
    assert "unparseable_phone" in n.classify_phone("12").notes


def test_email_datum_betrag():
    assert n.normalize_email(" Erika.Mustermann@Example.test ").value == "erika.mustermann@example.test"
    assert "unparseable_email" in n.normalize_email("keine mail").notes
    assert n.parse_date("01.07.2026").value == date(2026, 7, 1)
    assert n.parse_date("2026-07-01").value == date(2026, 7, 1)
    assert (
        n.parse_date("7/2026").value == date(2026, 7, 1)
        and "day_assumed_first" in n.parse_date("7/2026").notes
    )
    assert "unparseable_date" in n.parse_date("Sommer").notes
    assert n.parse_amount("1.234,56 EUR").value == Decimal("1234.56")
    assert n.parse_amount("1234.56").value == Decimal("1234.56")
    assert n.parse_amount("1.234").value == Decimal("1234.00")
    assert n.parse_amount("345,5").value == Decimal("345.50")
    assert n.parse_amount(345.5).value == Decimal("345.50")
    assert n.parse_amount("-12,00").value == Decimal("-12.00")
    assert "unparseable_amount" in n.parse_amount("k.A.").notes


def test_plz_land_bruch_bool():
    assert n.parse_postal_code("01067").value == "01067"
    assert n.parse_postal_code(1067).value == "01067"
    assert (
        n.country_iso2("Deutschland").value == "DE"
        and n.country_iso2("D").value == "DE"
        and n.country_iso2("AT").value == "AT"
    )
    assert n.country_iso2("Atlantis").value is None
    assert n.parse_fraction("125/10.000").value == (Decimal("125.00"), 10000)
    assert n.parse_fraction("125").value == (Decimal("125.00"), None)
    assert (
        n.parse_bool("ja").value is True
        and n.parse_bool("nein").value is False
        and n.parse_bool("vielleicht").value is None
    )


def test_iban_wird_maskiert():
    key = b"k" * 32
    r = n.parse_iban("DE02 1203 0000 0000 2020 51", key)
    assert (
        r.value["iban_last4"] == "2051"
        and len(r.value["iban_hash"]) == 64
        and "2020" not in r.value["masked"]
    )
    assert "iban_invalid" in n.parse_iban("DE00 1234 5678 9012 3456 78", key).notes
    assert n.mask_raw("Konto DE02 1203 0000 0000 2020 51 bitte") == "Konto [IBAN_****2051] bitte"


def test_kaution_und_staffel():
    syn = {"savings_book": ["Sparbuch"], "bank_guarantee": ["Bürgschaft"]}
    assert n.deposit_type_from("Sparbuch bei der Bank", syn).value == "savings_book"
    assert n.deposit_type_from("Gold", syn).value == "unknown"
    assert n.rent_adjustment_from("Staffelmiete").value == "graduated"
    assert n.rent_adjustment_from("Indexmiete").value == "indexed"
    assert n.rent_adjustment_from("keine").value == "none"
