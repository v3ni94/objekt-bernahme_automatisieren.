"""Synthetisches Objekt fuer die Vollstaendigkeitspruefung: drei Einheiten, Eigentuemer mit und ohne bestaetigte Daten,
Dokumente und Verknuepfungen als Nachweise. Namen sind synthetisch (Mustermann, Beispiel, Altmuster, Neumuster)."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest
from django.utils import timezone

from apps.documents.models import Document, DocumentOwnerLink, DocumentType
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import Owner, OwnerUnitAssignment

TODAY = date(2026, 7, 1)
_counter = {"n": 0}


@pytest.fixture
def objekt(seeded):
    return ManagedObject.objects.create(
        object_number="623",
        name="Musterstadt, Musterweg 1",
        street="Musterweg",
        house_number="1",
        postal_code="12345",
        city="Musterstadt",
        management_type="weg",
        status="takeover",
        takeover_to=date(2026, 7, 1),
        expected_unit_count=3,
        previous_manager_name="Altverwaltung Beispiel GmbH",
        previous_manager_street="Beispielstraße",
        previous_manager_house_number="5",
        previous_manager_postal_code="12345",
        previous_manager_city="Musterstadt",
        previous_manager_contact_person="Frau Beispiel",
        previous_manager_reference="VV-623",
        is_test=True,
    )


def make_unit(obj, label: str, **kw) -> Unit:
    return Unit.objects.create(
        object=obj,
        unit_label=label,
        unit_label_normalized=label.upper().replace(" ", ""),
        unit_type=kw.pop("unit_type", "apartment"),
        **kw,
    )


def make_owner(last: str, first: str | None = None, company: str | None = None, **kw) -> Owner:
    if company:
        return Owner.objects.create(
            type="legal_entity", company_name=company, search_name=company.upper(), **kw
        )
    return Owner.objects.create(
        type="natural_person",
        first_name=first,
        last_name=last,
        search_name=f"{last} {first or ''}".strip().upper(),
        **kw,
    )


def assign(owner, unit, valid_from, valid_to=None, *, data_status="confirmed", source_document=None, **kw):
    return OwnerUnitAssignment.objects.create(
        owner=owner,
        unit=unit,
        valid_from=valid_from,
        valid_to=valid_to,
        data_status=data_status,
        source_document=source_document,
        **kw,
    )


def make_doc(obj, type_code: str, status: str = "filed", name: str | None = None) -> Document:
    dt = DocumentType.objects.get(code=type_code)
    _counter["n"] += 1
    sha = hashlib.sha256(f"{obj.pk}-{type_code}-{_counter['n']}".encode()).hexdigest()
    return Document.objects.create(
        object=obj,
        sha256=sha,
        size_bytes=1024,
        mime_type="application/pdf",
        original_name=name or f"{type_code}_{_counter['n']}.pdf",
        current_name=name or f"{type_code}_{_counter['n']}.pdf",
        source="upload",
        status=status,
        first_seen_at=timezone.now(),
        document_type=dt,
        category=dt.category,
        subfolder=dt.subfolder,
    )


def link(
    doc: Document,
    assignment: OwnerUnitAssignment,
    *,
    status: str = "confirmed",
    period_year: int | None = None,
):
    return DocumentOwnerLink.objects.create(
        document=doc,
        link_kind="whole_document",
        owner=assignment.owner,
        unit=assignment.unit,
        assignment=assignment,
        document_type=doc.document_type,
        subfolder=doc.subfolder,
        period_year=period_year,
        confidence=1,
        status=status,
    )


@pytest.fixture
def szenario(objekt):
    """WE01 Mustermann (bestaetigt, seit 2020, Anschrift und E-Mail), WE02 Beispiel (Beginn unbekannt, nicht
    bestaetigt), WE03 ohne Eigentuemer; Altmuster war vor dem Zeitraum Eigentuemer von WE01."""
    we01, we02, we03 = (make_unit(objekt, f"WE0{i}") for i in (1, 2, 3))
    mustermann = make_owner(
        "Mustermann",
        "Max",
        data_status="confirmed",
        correspondence_street="Musterweg",
        correspondence_house_number="1",
        correspondence_postal_code="12345",
        correspondence_city="Musterstadt",
        email="max.mustermann@example.test",
        sepa_mandate_present=True,
    )
    beispiel = make_owner("Beispiel", "Erika", data_status="incomplete", correspondence_city="Musterstadt")
    altmuster = make_owner("Altmuster", "Karl", data_status="confirmed")
    a_alt = assign(altmuster, we01, date(2015, 1, 1), date(2019, 12, 31))
    a1 = assign(mustermann, we01, date(2020, 1, 1), balance_at_takeover=0)
    a2 = assign(beispiel, we02, None, data_status="incomplete")
    liste = make_doc(objekt, "eigentuemerliste", status="review")
    ea24 = make_doc(objekt, "einzelabrechnung")
    link(ea24, a1, period_year=2024)
    ea23 = make_doc(objekt, "einzelabrechnung")
    link(ea23, a1, status="suggested", period_year=2023)
    sepa = make_doc(objekt, "sepa_mandat")
    link(sepa, a1)
    return {
        "units": {"WE01": we01, "WE02": we02, "WE03": we03},
        "owners": {"mustermann": mustermann, "beispiel": beispiel, "altmuster": altmuster},
        "assignments": {"a1": a1, "a2": a2, "a_alt": a_alt},
        "docs": {"liste": liste, "ea24": ea24, "ea23": ea23, "sepa": sepa},
    }
