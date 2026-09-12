"""Sonderakten (Unzugeordnet, Unbekannte_WE) im Wettlauf zweier Entscheidungsjobs (Praxisfall Objekt 82, 12.09.2026):
hat ein paralleler Job die Akte nach der ersten Suche angelegt, laeuft der zweite in den eindeutigen Schluessel
uq_owner_files_name_active und verwendet dann die vorhandene Akte, statt mit IntegrityError zu scheitern."""

from __future__ import annotations

import pytest
from django.db import transaction

from apps.classification import ownerfiles
from apps.objects.models import ManagedObject
from apps.parties.models import Owner, OwnerFile

pytestmark = pytest.mark.django_db


def _objekt() -> ManagedObject:
    return ManagedObject.objects.create(
        object_number="623",
        name="Musterstadt, Musterstraße 1",
        city="Musterstadt",
        street="Musterstraße",
        house_number="1",
        management_type="weg",
        is_test=True,
    )


def test_unzugeordnet_im_wettlauf(seeded):
    obj = _objekt()
    # der andere Job war schneller: die Akte existiert, als der zweite Job anlegen will
    vorhanden = OwnerFile.objects.create(
        object=obj, file_kind="unassigned", folder_name="Unzugeordnet", name_basis={}
    )
    with (
        transaction.atomic()
    ):  # Entscheidungsjobs laufen in einer Transaktion; der Sicherungspunkt haelt sie nutzbar
        akte = ownerfiles._create_or_refetch(
            lambda: OwnerFile.active.filter(object=obj, file_kind="unassigned").first(),
            object=obj,
            file_kind="unassigned",
            folder_name="Unzugeordnet",
            name_basis={},
        )
        assert akte.pk == vorhanden.pk
        assert OwnerFile.active.filter(object=obj, file_kind="unassigned").count() == 1
    # ohne Wettlauf liefert die Funktion dieselbe Akte
    assert ownerfiles.owner_file_unassigned(obj).pk == vorhanden.pk


def test_unbekannte_we_im_wettlauf(seeded):
    obj = _objekt()
    owner = Owner.objects.create(type="natural_person", last_name="Beispiel", first_name="Karl")
    name = ownerfiles.owner_file_unknown_unit(obj, owner).folder_name
    OwnerFile.active.filter(object=obj, owner=owner).delete()  # Ausgangslage wie vor der ersten Anlage
    vorhanden = OwnerFile.objects.create(
        object=obj, owner=owner, file_kind="unknown_unit", folder_name=name, name_basis={"owner": owner.pk}
    )
    akte = ownerfiles._create_or_refetch(
        lambda: OwnerFile.active.filter(object=obj, owner=owner, file_kind="unknown_unit").first(),
        object=obj,
        owner=owner,
        file_kind="unknown_unit",
        folder_name=name,
        name_basis={"owner": owner.pk},
    )
    assert akte.pk == vorhanden.pk and akte.folder_name.startswith("Unbekannte_WE")


def test_ohne_vorhandene_akte_bleibt_der_fehler(seeded, monkeypatch):
    """Ein anderer Integritaetsfehler (keine vorhandene Akte) wird nicht verschluckt."""
    from django.db import IntegrityError

    obj = _objekt()

    def kaputt(**kw):
        raise IntegrityError("anderer Fehler")

    monkeypatch.setattr(OwnerFile.objects, "create", kaputt)
    with pytest.raises(IntegrityError):
        ownerfiles.owner_file_unassigned(obj)
