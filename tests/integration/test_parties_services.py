"""Historische Zuordnung (CR 14: Dokument 03/2025 bei Wechsel 01.07.2026 gehoert dem Alteigentuemer), Ueberlappung,
Datensatzstatus, IBAN-Felder. Synthetische Namen Altmuster und Neumuster (B-39)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.objects.models import ManagedObject, Unit
from apps.parties import services
from apps.parties.models import Owner, OwnerUnitAssignment
from apps.parties.services import InvalidIban, OverlapError

pytestmark = pytest.mark.django_db


@pytest.fixture
def objekt():
    return ManagedObject.objects.create(
        object_number="623", name="Musterstadt, Musterweg 1", management_type="weg", is_test=True
    )


@pytest.fixture
def we05(objekt):
    return Unit.objects.create(
        object=objekt, unit_label="WE05", unit_label_normalized="WE5", unit_number="5", unit_type="apartment"
    )


def owner(name: str) -> Owner:
    return Owner.objects.create(type="natural_person", last_name=name, search_name=name.upper())


def test_wechsel_zum_01_07_2026_dokument_maerz_2025_gehoert_altmuster(we05):
    alt, neu = owner("Altmuster"), owner("Neumuster")
    services.create_assignment(owner=alt, unit=we05, valid_from=None, valid_to=date(2026, 6, 30))
    services.create_assignment(owner=neu, unit=we05, valid_from=date(2026, 7, 1), valid_to=None)

    stichtag = list(services.owners_at(we05, date(2025, 3, 15)))
    assert [a.owner.last_name for a in stichtag] == ["Altmuster"]
    # Mahnschreiben vom 15.04.2025 ueber Hausgeld 03/2025 (docs/architektur.md 5.4)
    zeitraum = list(services.owners_for_period(we05, date(2025, 3, 1), date(2025, 3, 31)))
    assert [a.owner.last_name for a in zeitraum] == ["Altmuster"]
    # Abrechnungsjahr 2026 beruehrt beide
    jahr = services.owners_for_document(we05, period_year=2026)
    assert sorted(a.owner.last_name for a in jahr) == ["Altmuster", "Neumuster"]
    # Stichtag am Wechseltag: nur Neumuster; Vortag: nur Altmuster (Grenzen einschliesslich)
    assert [a.owner.last_name for a in services.owners_at(we05, date(2026, 7, 1))] == ["Neumuster"]
    assert [a.owner.last_name for a in services.owners_at(we05, date(2026, 6, 30))] == ["Altmuster"]
    assert services.owners_for_document(we05) is None
    assert OwnerUnitAssignment.objects.get(owner=neu).is_current is True
    assert OwnerUnitAssignment.objects.get(owner=alt).is_current is False


def test_ueberlappung_gleicher_eigentuemer_wird_abgewiesen(we05):
    o = owner("Mustermann")
    services.create_assignment(owner=o, unit=we05, valid_from=date(2020, 1, 1), valid_to=None)
    with pytest.raises(OverlapError):
        services.create_assignment(owner=o, unit=we05, valid_from=date(2024, 1, 1), valid_to=None)
    # Lueckenlos anschliessend ist zulaessig
    a = OwnerUnitAssignment.objects.get(owner=o)
    services.end_assignment(a, date(2023, 12, 31))
    services.create_assignment(owner=o, unit=we05, valid_from=date(2024, 1, 1), valid_to=None)
    assert OwnerUnitAssignment.active.filter(owner=o).count() == 2
    assert services.find_overlaps(we05) == []


def test_mehrfacheigentum_verschiedener_eigentuemer_mit_anteilswarnung(we05):
    a, b = owner("Alpha"), owner("Beta")
    r1 = services.create_assignment(
        owner=a, unit=we05, valid_from=date(2020, 1, 1), valid_to=None, share=Decimal("0.5")
    )
    assert r1.share_warning is None
    r2 = services.create_assignment(
        owner=b, unit=we05, valid_from=date(2020, 1, 1), valid_to=None, share=Decimal("0.75")
    )
    assert r2.share_warning == Decimal("1.25")
    assert services.owners_at(we05, date(2021, 1, 1)).count() == 2


def test_geloeschte_zuordnung_zaehlt_nicht(we05):
    o = owner("Mustermann")
    res = services.create_assignment(owner=o, unit=we05, valid_from=date(2020, 1, 1), valid_to=None)
    res.assignment.deleted_at = timezone.now()
    res.assignment.save()
    assert services.owners_at(we05, date(2021, 1, 1)).count() == 0


def test_find_overlaps_meldet_direkt_angelegte_verstoesse(we05):
    o = owner("Mustermann")
    OwnerUnitAssignment.objects.create(owner=o, unit=we05, valid_from=date(2020, 1, 1))
    OwnerUnitAssignment.objects.create(owner=o, unit=we05, valid_from=date(2022, 1, 1))
    paare = services.find_overlaps()
    assert len(paare) == 1


@pytest.mark.parametrize(
    ("felder", "pflicht", "erwartet"),
    [
        ({}, ("a",), "incomplete"),
        ({"a": "confirmed"}, ("a",), "confirmed"),
        ({"a": "confirmed", "b": "ai_suggested"}, ("a",), "ai_suggested"),
        ({"a": "ai_suggested"}, ("a",), "ai_suggested"),
        ({"a": "incomplete", "b": "confirmed"}, ("a",), "incomplete"),
        ({"b": "confirmed"}, (), "confirmed"),
    ],
)
def test_datensatzstatus_vorrang(felder, pflicht, erwartet):
    assert services.derive_data_status(felder, pflicht) == erwartet


def test_provenance_und_recompute(objekt):
    o = owner("Mustermann")
    for f in (
        "last_name_or_company",
        "correspondence_street",
        "correspondence_postal_code",
        "correspondence_city",
    ):
        services.set_provenance("owner", o.pk, f, source_kind="manual", status="confirmed")
    assert services.recompute_data_status("owner", o) == "confirmed"
    services.set_provenance("owner", o.pk, "email", source_kind="ai", status="ai_suggested")
    assert services.recompute_data_status("owner", o) == "ai_suggested"
    o.refresh_from_db()
    assert o.data_status == "ai_suggested"


def test_iban_felder_ohne_klartext():
    felder = services.iban_fields(
        "DE02 1203 0000 0000 2020 51"
    )  # Test-IBAN der Bundesbank-Beispielform, synthetisch
    assert felder["iban_last4"] == "2051"
    assert isinstance(felder["iban_hash"], bytes) and len(felder["iban_hash"]) == 32
    assert services.iban_fields("") == {"iban_last4": None, "iban_hash": None}
    with pytest.raises(InvalidIban):
        services.iban_fields("DE00 1234 5678 9012 3456 78")


def test_search_name():
    assert (
        services.search_name(type="natural_person", first_name="Erika", last_name="Müller", company_name=None)
        == "MUELLER ERIKA"
    )
    assert (
        services.search_name(
            type="legal_entity", first_name=None, last_name=None, company_name="Muster  GmbH"
        )
        == "MUSTER GMBH"
    )
