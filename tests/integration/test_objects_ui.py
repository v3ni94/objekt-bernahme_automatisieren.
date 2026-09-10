"""Objektverwaltung ueber die Oberflaeche: Objekt 623 (synthetisch) anlegen, Einheiten und Eigentuemer erfassen,
Zuordnung mit Zeitraum speichern, Stichtagsabfrage, Dublettenschutz (M2 Abnahmekriterium)."""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import Owner, OwnerUnitAssignment

pytestmark = pytest.mark.django_db


def _object_data(**over):
    data = {
        "object_number": "623",
        "name": "Musterstadt, Musterweg 1",
        "street": "Musterweg",
        "house_number": "1",
        "postal_code": "12345",
        "city": "Musterstadt",
        "management_type": "weg",
        "status": "new",
        "takeover_from": "2026-04-01",
        "takeover_to": "2026-07-01",
        "fiscal_year_start_month": 1,
        "is_test": "on",
    }
    data.update(over)
    return data


def test_objekt_anlegen_und_dublette_ueber_zahlenwert(client_as, clerk_user):
    client = client_as(clerk_user)
    resp = client.post(reverse("object_create"), _object_data())
    assert resp.status_code == 302
    obj = ManagedObject.objects.get(object_number="623")
    assert obj.object_number_numeric == 623 and obj.created_by == clerk_user
    assert AuditEvent.objects.filter(action="object.create", entity_id=obj.pk).exists()

    resp = client.post(reverse("object_create"), _object_data(object_number="0623"))
    assert resp.status_code == 200 and b"denselben Zahlenwert" in resp.content
    resp = client.post(reverse("object_create"), _object_data(object_number="6230"))
    assert resp.status_code == 302
    assert ManagedObject.objects.filter(object_number="6230").exists()
    resp = client.post(reverse("object_create"), _object_data(object_number="1"))
    assert resp.status_code == 200 and b"2 bis 6 Stellen" in resp.content


def test_einheiten_eigentuemer_zuordnung_und_stichtag(client_as, clerk_user):
    client = client_as(clerk_user)
    client.post(reverse("object_create"), _object_data())
    obj = ManagedObject.objects.get(object_number="623")

    resp = client.post(reverse("unit_create", args=[obj.pk]), {"unit_label": "WE 3", "status": "active"})
    assert resp.status_code == 302
    unit = Unit.objects.get(object=obj)
    assert (unit.unit_label_normalized, unit.unit_number, unit.unit_type) == ("WE3", "3", "apartment")
    resp = client.post(reverse("unit_create", args=[obj.pk]), {"unit_label": "WE03", "status": "active"})
    assert resp.status_code == 200 and b"dieselbe Einheit" in resp.content
    resp = client.post(reverse("unit_create", args=[obj.pk]), {"unit_label": "Garage 3", "status": "active"})
    assert resp.status_code == 302 and Unit.objects.filter(object=obj).count() == 2

    for name in ("Altmuster", "Neumuster"):
        resp = client.post(
            reverse("owner_create"),
            {
                "type": "natural_person",
                "last_name": name,
                "first_name": "Erika",
                "correspondence_city": "Musterstadt",
                "iban": "DE02 1203 0000 0000 2020 51" if name == "Altmuster" else "",
            },
        )
        assert resp.status_code == 302, resp.content[:500]
    alt = Owner.objects.get(last_name="Altmuster")
    neu = Owner.objects.get(last_name="Neumuster")
    assert alt.iban_last4 == "2051" and alt.iban_hash is not None and alt.search_name == "ALTMUSTER ERIKA"
    audit = AuditEvent.objects.get(action="owner.create", entity_id=alt.pk)
    assert "2051" not in str(audit.after_state.get("iban_hash", "")) and "iban_hash" not in audit.after_state

    resp = client.post(
        reverse("assignment_create", args=[obj.pk]),
        {"owner": alt.pk, "unit": unit.pk, "valid_from": "", "valid_to": "2026-06-30", "confirmed": "on"},
    )
    assert resp.status_code == 302, resp.content[:800]
    resp = client.post(
        reverse("assignment_create", args=[obj.pk]),
        {"owner": neu.pk, "unit": unit.pk, "valid_from": "2026-07-01", "valid_to": "", "confirmed": "on"},
    )
    assert resp.status_code == 302
    assert OwnerUnitAssignment.objects.filter(unit=unit).count() == 2
    assert AuditEvent.objects.filter(action="assignment.create", object_id=obj.pk).count() == 2

    # Ueberschneidung derselben Person wird im Formular abgewiesen
    resp = client.post(
        reverse("assignment_create", args=[obj.pk]),
        {"owner": neu.pk, "unit": unit.pk, "valid_from": "2027-01-01", "valid_to": ""},
    )
    assert resp.status_code == 200 and "überschneidet" in resp.content.decode()

    detail = client.get(reverse("object_detail", args=[obj.pk]), {"stichtag": "2025-03-15"})
    body = detail.content.decode()
    assert detail.status_code == 200 and "Altmuster" in body
    zeile = body.rsplit("Eigentümer am Stichtag", 1)[1].split("</table>")[0]
    assert "Altmuster" in zeile and "Neumuster" not in zeile

    detail = client.get(reverse("object_detail", args=[obj.pk]), {"stichtag": "2026-07-01"})
    zeile = detail.content.decode().rsplit("Eigentümer am Stichtag", 1)[1].split("</table>")[0]
    assert "Neumuster" in zeile and "Altmuster" not in zeile

    # Eigentuemerwechsel ueber Beenden
    a_neu = OwnerUnitAssignment.objects.get(owner=neu)
    resp = client.post(
        reverse("assignment_end", args=[a_neu.pk]), {"valid_to": "2027-12-31", "reason": "Verkauf"}
    )
    assert resp.status_code == 302
    a_neu.refresh_from_db()
    assert a_neu.valid_to == date(2027, 12, 31) and a_neu.is_current is False


def test_rechte(client_as, clerk_user, admin_user, seeded):
    from apps.accounts.models import Role

    client = client_as(clerk_user)
    assert client.get(reverse("object_list")).status_code == 200
    assert client.get(reverse("owner_list")).status_code == 200
    # Sachbearbeiter hat objects.write und masterdata.write (Rechtematrix), also 200 auf Formularen
    assert client.get(reverse("object_create")).status_code == 200
    # Rolle ohne Rechte
    role = Role.objects.create(code="gast", name="Gast", permissions=[], is_system=False)
    clerk_user.role = role
    clerk_user.save()
    assert client.get(reverse("object_create")).status_code == 403
    assert AuditEvent.objects.filter(action="auth.denied", reason="objects.write").exists()


def test_login_erforderlich(client):
    resp = client.get(reverse("object_list"))
    assert resp.status_code == 302 and "/konto/login/" in resp["Location"]
