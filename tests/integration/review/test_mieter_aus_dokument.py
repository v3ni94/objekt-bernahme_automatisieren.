"""„Mieter aus Dokument anlegen“ (12.09.2026): Ein Mietvertrag ohne bekannten Mieter erzeugt den Fall „Mieter unbekannt“
mit den gelesenen Vertragsdaten; die Bestaetigung legt Mieter, Mitmieter, Einheit (unabhaengig von der Sollzahl),
Mietverhaeltnis, Zuordnung und Mieterakte an, verschiebt das Dokument dorthin und fuellt die Mieterliste."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from tests.integration.review.helpers import make_case_document

from apps.audit.models import AuditEvent
from apps.documents.models import DocumentTenantLink
from apps.drive.models import DriveNode, NodeKind
from apps.lists.data import build_tenant_list
from apps.objects.models import Unit
from apps.parties.models import Lease, Tenant, TenantFile, TenantUnitAssignment
from apps.pipeline.models import JobType, ProcessingJob
from apps.review.models import ReviewDecision

pytestmark = pytest.mark.django_db


def vertrag(obj) -> dict:
    kopf = f"Objekt {obj.object_number} {obj.name}\n"
    return {
        "filename": "Mietvertrag_WE2.pdf",
        "pages": [
            kopf
            + "Mietvertrag für Wohnraum\n\nzwischen\nHausverwaltung Müller GmbH, Rheinpromenade 13, 40789 Monheim am Rhein\n"
            "- nachfolgend Vermieter genannt -\n\nund\nHerrn Jonas Beispielmieter und Frau Lea Beispielmieter, "
            "wohnhaft Alte Straße 1, 41836 Hückelhoven\n- nachfolgend Mieter genannt -\n\n"
            "§ 1 Mietsache\nVermietet wird die Wohnung Nr. 2 im 1. OG links.\n",
            kopf + "§ 2 Mietzeit\nDas Mietverhältnis beginnt am 01.03.2024 und läuft auf unbestimmte Zeit.\n"
            "§ 3 Miete\nDie Nettokaltmiete beträgt monatlich 650,00 EUR.\nBetriebskostenvorauszahlung: 180,00 EUR\n"
            "Heizkostenvorauszahlung: 90,00 EUR\nGesamtmiete: 920,00 EUR\n§ 4 Kaution\n"
            "Der Mieter leistet eine Kaution in Höhe von 1.950,00 EUR.\n",
        ],
    }


def test_mietvertrag_erzeugt_fall_mit_vertragsdaten(welt, fake_oauth, run_all):
    obj = welt["objects"]["625"]
    doc, case = make_case_document(welt, run_all, vertrag(obj), number="625")
    assert doc.category_id == "04" and doc.document_type.code == "mietvertrag"
    assert case is not None and case.case_type == "unclear" and case.case_subtype == "tenant_unknown"
    assert case.proposed_action == {"action": "create_tenant"}
    facts = case.context["lease_facts"]
    assert [(t["first_name"], t["last_name"]) for t in facts["tenants"]] == [
        ("Jonas", "Beispielmieter"),
        ("Lea", "Beispielmieter"),
    ]
    assert facts["unit_hint"] == "WE 2" and facts["unit_id"] is None
    assert facts["start_date"] == "2024-03-01" and facts["base_rent"] == "650.00"
    assert facts["deposit_amount"] == "1950.00" and facts["landlord_names"] == ["Hausverwaltung Müller GmbH"]
    assert facts["source"] == "rules"


def test_bestaetigung_legt_mieter_einheit_akte_an_und_verschiebt(
    welt, fake_oauth, run_all, client_as, clerk_user
):
    obj = welt["objects"]["625"]
    doc, case = make_case_document(welt, run_all, vertrag(obj), number="625")
    client = client_as(clerk_user)
    seite = client.get(reverse("review_detail", args=[case.pk]))
    assert seite.status_code == 200
    inhalt = seite.content.decode()
    assert "Mieter aus dem Dokument anlegen" in inhalt and 'value="Beispielmieter"' in inhalt
    assert 'name="nt_unit_label" value="WE 2"' in inhalt and 'name="nt_base_rent" value="650.00"' in inhalt
    assert not Unit.active.filter(object=obj, unit_label_normalized="WE2").exists()
    resp = client.post(
        reverse("review_action", args=[case.pk]),
        {
            "action": "confirm",
            "category": "04",
            "document_type": "mietvertrag",
            "nt_create": "1",
            "nt_salutation": "Herr",
            "nt_first_name": "Jonas",
            "nt_last_name": "Beispielmieter",
            "nt2_salutation": "Frau",
            "nt2_first_name": "Lea",
            "nt2_last_name": "Beispielmieter",
            "nt_unit_id": "",
            "nt_unit_label": "WE 2",
            "nt_start_date": "2024-03-01",
            "nt_base_rent": "650.00",
            "nt_utilities_prepayment": "180.00",
            "nt_heating_prepayment": "90.00",
            "nt_deposit_amount": "1950.00",
        },
    )
    assert resp.status_code == 302, resp.content[:500]
    # Stammdaten: zwei Mieter, neue Einheit unabhaengig von der Sollzahl, ein Mietverhaeltnis, zwei Zuordnungen
    mieter = list(Tenant.objects.filter(last_name="Beispielmieter").order_by("id"))
    assert [(t.first_name, t.salutation, t.data_status) for t in mieter] == [
        ("Jonas", "Herr", "confirmed"),
        ("Lea", "Frau", "confirmed"),
    ]
    unit = Unit.active.get(object=obj, unit_label_normalized="WE2")
    assert unit.unit_type == "apartment" and unit.data_status == "incomplete"
    lease = Lease.objects.get(source_document=doc)
    assert lease.base_rent == Decimal("650.00") and lease.deposit_amount == Decimal("1950.00")
    assert lease.start_date.isoformat() == "2024-03-01" and lease.data_status == "confirmed"
    zuordnungen = list(TenantUnitAssignment.active.filter(tenant__in=mieter))
    assert len(zuordnungen) == 2 and {a.lease_id for a in zuordnungen} == {lease.pk}
    assert all(a.unit_id == unit.pk and a.source_document_id == doc.pk for a in zuordnungen)
    # Akte, Verknuepfung, Fall
    akte = TenantFile.active.get(unit=unit)
    assert akte.folder_name == "WE02_Beispielmieter" and akte.file_assignments.count() == 2
    links = list(DocumentTenantLink.objects.filter(document=doc, status="confirmed"))
    assert len(links) == 2 and all(
        link.tenant_file_id == akte.pk and link.lease_id == lease.pk for link in links
    )
    case.refresh_from_db()
    assert case.status == "resolved"
    assert ReviewDecision.objects.get(review_case=case).decision_type == "assign_tenant"
    ev = AuditEvent.objects.get(action="review.create_tenant", object_id=obj.pk)
    assert ev.after_state["unit_label"] == "WE 2" and ev.after_state["lease_id"] == lease.pk
    # Verschiebung in die Mieterakte
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.FILE_TO_DRIVE, status="pending")
    assert job.payload["tenant_file_id"] == akte.pk
    run_all(obj)
    doc.refresh_from_db()
    assert doc.status == "filed"
    ordner = DriveNode.objects.get(tenant_file=akte, node_kind=NodeKind.TENANT_FILE_FOLDER)
    drive = welt["drive"]
    assert ordner.drive_name == "WE02_Beispielmieter"
    assert drive.get(doc.drive_file_id).parent_id == ordner.drive_file_id
    # Mieterliste kennt die neuen Mieter
    daten = build_tenant_list(obj)
    text = repr(daten)
    assert "Beispielmieter" in text and "Jonas" in text and "Lea" in text


def test_pruefung_verlangt_name_und_einheit(welt, fake_oauth, run_all, client_as, clerk_user):
    obj = welt["objects"]["625"]
    _doc, case = make_case_document(welt, run_all, vertrag(obj), number="625")
    client = client_as(clerk_user)
    resp = client.post(
        reverse("review_action", args=[case.pk]),
        {"action": "confirm", "category": "04", "document_type": "mietvertrag", "nt_create": "1"},
        follow=True,
    )
    texte = " ".join(m.message for m in resp.context["messages"])
    assert "Nachname oder Firma" in texte and "Einheit wählen" in texte
    case.refresh_from_db()
    assert case.status == "open" and not Tenant.objects.filter(last_name="Beispielmieter").exists()
    # ohne Haken und ohne vorhandenen Mieter bleibt die bisherige Pruefung
    resp = client.post(
        reverse("review_action", args=[case.pk]),
        {"action": "confirm", "category": "04", "document_type": "mietvertrag"},
        follow=True,
    )
    assert any("Mieter wählen oder aus dem Dokument anlegen" in m.message for m in resp.context["messages"])
