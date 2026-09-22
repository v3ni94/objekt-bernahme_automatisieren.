"""Weitere Anschriften eines Objekts (Eckobjekt, mehrere Hausnummern): Ableitung aus Bezeichnungen, Formularzeilen
und Wirkung im Kandidatenindex (zwei Strassen desselben Objekts sind ein Objektbezug, kein Widerspruch)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from apps.objects.addresses import (
    additional_from_name,
    addresses_from_name,
    format_address_lines,
    parse_address_lines,
)
from apps.sync.assignment.candidates import build_index, score_candidates
from apps.sync.assignment.decide import Thresholds, decide

OWN = ("Rheinpromenade 13, 40789 Monheim am Rhein",)


@dataclass
class Obj:
    pk: int
    object_number: str
    street: str | None
    house_number: str | None
    postal_code: str = ""
    city: str = ""
    name: str | None = None
    management_type: str = "rental"
    drive_root_folder_id: str | None = None
    additional_addresses: list = field(default_factory=list)


@pytest.mark.parametrize(
    "name, erwartet, hinweise",
    [
        (
            "Kaiserstraße 77 u. 79, Windmühlenstraße 31",
            [("Kaiserstraße", "77"), ("Kaiserstraße", "79"), ("Windmühlenstraße", "31")],
            0,
        ),
        ("Weberstraße 2,4+6", [("Weberstraße", "2"), ("Weberstraße", "4"), ("Weberstraße", "6")], 0),
        ("Richard-Wagner-Str. 8/9", [("Richard-Wagner-Str.", "8"), ("Richard-Wagner-Str.", "9")], 0),
        ("Vorstadt 15 / 15a", [("Vorstadt", "15"), ("Vorstadt", "15a")], 0),
        ("WEG Stockackerweg 25/27", [("Stockackerweg", "25"), ("Stockackerweg", "27")], 0),
        (
            "Rheydter Straße 266 und 268, Prof. Otten",
            [("Rheydter Straße", "266"), ("Rheydter Straße", "268")],
            1,
        ),
        ("Massolleweg 4, 4a-e", [("Massolleweg", "4")], 1),
        ("Südwall 111-113, 41179 Mönchengladbach", [("Südwall", "111-113")], 0),
        ("Kaiserstraße 34", [("Kaiserstraße", "34")], 0),
        ("", [], 0),
    ],
)
def test_anschriften_aus_bezeichnung(name, erwartet, hinweise):
    found, notes = addresses_from_name(name)
    assert [(a["street"], a["house_number"]) for a in found] == erwartet
    assert len(notes) == hinweise


def test_ort_und_plz_gelten_fuer_alle_anschriften_der_bezeichnung():
    found, notes = addresses_from_name("Graf-Reinald-Str. 34, 36, 38, 40, 42, 41812 Erkelenz")
    assert [a["house_number"] for a in found] == ["34", "36", "38", "40", "42"] and not notes
    assert {(a["postal_code"], a["city"]) for a in found} == {("41812", "Erkelenz")}
    found, _ = addresses_from_name("Aachener Straße 25, Erkelenz")
    assert found == [
        {"street": "Aachener Straße", "house_number": "25", "postal_code": "", "city": "Erkelenz"}
    ]
    found, _ = addresses_from_name("Leostr. 9, 51145 Köln")
    assert found[0]["postal_code"] == "51145" and found[0]["city"] == "Köln"


def test_formularzeilen_und_rueckformatierung():
    items, bad = parse_address_lines(
        "Beispielweg 7, 54321 Beispielhausen\n\nMusterstraße 51\nohne Hausnummer"
    )
    assert bad == ["ohne Hausnummer"]
    assert items == [
        {"street": "Beispielweg", "house_number": "7", "postal_code": "54321", "city": "Beispielhausen"},
        {"street": "Musterstraße", "house_number": "51", "postal_code": "", "city": ""},
    ]
    assert format_address_lines(items) == "Beispielweg 7, 54321 Beispielhausen\nMusterstraße 51"
    assert format_address_lines([{"street": "", "house_number": "1"}, "x"]) == ""


def test_vorschlag_je_objekt_ohne_hauptanschrift_und_ohne_dubletten():
    eck = Obj(
        1,
        "529",
        "Kaiserstraße",
        "77",
        "12345",
        "Musterstadt",
        name="Kaiserstraße 77 u. 79, Windmühlenstraße 31",
    )
    vorschlag = additional_from_name(eck)
    assert vorschlag["primary"] is None and vorschlag["notes"] == []
    assert vorschlag["additional"] == [
        {"street": "Kaiserstraße", "house_number": "79", "postal_code": "12345", "city": "Musterstadt"},
        {"street": "Windmühlenstraße", "house_number": "31", "postal_code": "12345", "city": "Musterstadt"},
    ]
    eck.additional_addresses = vorschlag["additional"]
    assert additional_from_name(eck)["additional"] == []
    # Hauptanschrift fehlt: erste Anschrift der Bezeichnung wird Hauptanschrift, der Ort gilt fuer alle
    ohne = Obj(2, "513", None, None, name="Fahlenberg 23, Linnich")
    vorschlag = additional_from_name(ohne)
    assert vorschlag["primary"] == {
        "street": "Fahlenberg",
        "house_number": "23",
        "postal_code": "",
        "city": "Linnich",
    }
    assert vorschlag["additional"] == []
    # Hausnummer der Hauptanschrift nicht auswertbar: Hinweis, die Nummern der Bezeichnung werden ergaenzt
    krumm = Obj(3, "600", "Kaiserstraße", "77 u. 79", "12345", "Musterstadt", name="Kaiserstraße 77 u. 79")
    vorschlag = additional_from_name(krumm)
    assert any("nicht auswertbar" in n for n in vorschlag["notes"])
    assert [a["house_number"] for a in vorschlag["additional"]] == ["77", "79"]


def test_zwei_strassen_eines_eckobjekts_sind_ein_objektbezug():
    eck = Obj(
        1,
        "529",
        "Kaiserstraße",
        "77",
        "12345",
        "Musterstadt",
        additional_addresses=[
            {"street": "Kaiserstraße", "house_number": "79"},
            {"street": "Windmühlenstraße", "house_number": "31"},
        ],
    )
    andere = Obj(2, "530", "Bahnhofstraße", "7", "12345", "Musterstadt")
    index = build_index([eck, andere])
    nur_zweite = score_candidates(
        "Rechnung Dachreparatur, Objekt Windmühlenstraße 31, 12345 Musterstadt",
        index=index,
        own_addresses=OWN,
    )
    assert [c.object_id for c in nur_zweite] == [1] and nur_zweite[0].score >= 0.8
    nur_79 = score_candidates(
        "Wartung Heizung Kaiserstraße 79, 12345 Musterstadt", index=index, own_addresses=OWN
    )
    assert [c.object_id for c in nur_79] == [1]
    beide = score_candidates(
        "Rechnung Fassade Kaiserstraße 77 / Windmühlenstraße 31, 12345 Musterstadt",
        index=index,
        own_addresses=OWN,
    )
    assert len(beide) == 1 and beide[0].object_id == 1 and not beide[0].contradictions
    assert {e.text for e in beide[0].evidence if e.kind == "address"} >= {
        "Kaiserstraße 77",
        "Windmühlenstraße 31",
    }
    assert decide(beide, Thresholds()).decision == "auto"
    # zwei Strassen zweier Objekte bleiben ein Pruefall
    fremd = score_candidates(
        "Rechnung Kaiserstraße 77 und Bahnhofstraße 7, 12345 Musterstadt", index=index, own_addresses=OWN
    )
    assert {c.object_id for c in fremd} == {1, 2} and decide(fremd, Thresholds()).decision == "review"
