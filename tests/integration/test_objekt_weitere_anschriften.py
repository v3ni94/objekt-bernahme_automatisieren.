"""Weitere Anschriften (Eckobjekt) ueber die Oberflaeche pflegen und aus den Bezeichnungen nachpflegen
(Kommando objekt_anschriften_ergaenzen, Deploy-Aktion objekt-anschriften)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db


def _data(**over):
    data = {
        "object_number": "623",
        "name": "Musterstadt, Musterweg 1",
        "street": "Musterweg",
        "house_number": "1",
        "postal_code": "12345",
        "city": "Musterstadt",
        "management_type": "weg",
        "status": "new",
        "fiscal_year_start_month": 1,
        "is_test": "on",
    }
    data.update(over)
    return data


def test_weitere_anschriften_im_formular(client_as, clerk_user):
    client = client_as(clerk_user)
    resp = client.post(
        reverse("object_create"),
        _data(additional_addresses_text="Beispielweg 7, 54321 Beispielhausen\nMusterstraße 51"),
    )
    assert resp.status_code == 302
    obj = ManagedObject.objects.get(object_number="623")
    assert obj.additional_addresses == [
        {"street": "Beispielweg", "house_number": "7", "postal_code": "54321", "city": "Beispielhausen"},
        {"street": "Musterstraße", "house_number": "51", "postal_code": "", "city": ""},
    ]
    seite = client.get(reverse("object_edit", args=[obj.pk])).content.decode()
    assert "Beispielweg 7, 54321 Beispielhausen" in seite and "Musterstraße 51" in seite
    detail = client.get(reverse("object_detail", args=[obj.pk])).content.decode()
    assert (
        "Weitere Anschriften" in detail and "Beispielweg 7, 54321 Beispielhausen; Musterstraße 51" in detail
    )
    # Fehleingabe wird abgewiesen, der Bestand bleibt
    resp = client.post(
        reverse("object_edit", args=[obj.pk]), _data(additional_addresses_text="ohne Hausnummer")
    )
    assert resp.status_code == 200 and "Nicht als Anschrift erkannt" in resp.content.decode()
    obj.refresh_from_db()
    assert len(obj.additional_addresses) == 2
    # Leeren entfernt die weiteren Anschriften
    resp = client.post(reverse("object_edit", args=[obj.pk]), _data(additional_addresses_text=""))
    assert resp.status_code == 302
    obj.refresh_from_db()
    assert obj.additional_addresses == []


def test_nachpflege_aus_bezeichnung(seeded):
    eck = ManagedObject.objects.create(
        object_number="529",
        name="Kaiserstraße 77 u. 79, Windmühlenstraße 31",
        street="Kaiserstraße",
        house_number="77",
        postal_code="12345",
        city="Musterstadt",
        management_type="rental",
        is_test=True,
    )
    ohne = ManagedObject.objects.create(
        object_number="513", name="Fahlenberg 23, Linnich", management_type="rental", is_test=True
    )
    ManagedObject.objects.create(
        object_number="303",
        name="Kaiserstraße 34",
        street="Kaiserstraße",
        house_number="34",
        management_type="rental",
        is_test=True,
    )
    out = StringIO()
    call_command("objekt_anschriften_ergaenzen", stdout=out)
    text = out.getvalue()
    assert "Objekt 529" in text and "Kaiserstraße 79, 12345 Musterstadt" in text
    assert "Windmühlenstraße 31, 12345 Musterstadt" in text
    assert "Objekt 513" in text and "Hauptanschrift aus Bezeichnung: Fahlenberg 23, Linnich" in text
    assert "Objekt 303" not in text and "Vorschau, nichts geändert" in text
    eck.refresh_from_db()
    assert eck.additional_addresses == []

    out = StringIO()
    call_command("objekt_anschriften_ergaenzen", "--echt", stdout=out)
    assert "gespeichert 2" in out.getvalue()
    eck.refresh_from_db()
    ohne.refresh_from_db()
    assert [(a["street"], a["house_number"], a["city"]) for a in eck.additional_addresses] == [
        ("Kaiserstraße", "79", "Musterstadt"),
        ("Windmühlenstraße", "31", "Musterstadt"),
    ]
    assert (ohne.street, ohne.house_number, ohne.city) == ("Fahlenberg", "23", "Linnich")
    ereignis = AuditEvent.objects.get(action="object.update", entity_id=eck.pk)
    assert ereignis.after_state["additional_addresses"][1]["street"] == "Windmühlenstraße"

    out = StringIO()
    call_command("objekt_anschriften_ergaenzen", "--echt", stdout=out)
    assert "gespeichert 0" in out.getvalue()


def test_korrigieren_leitet_nur_vom_kommando_gesetzte_hauptanschriften_neu_ab(clerk_user):
    from apps.audit.services import record

    reason = "Weitere Anschriften aus der Bezeichnung (objekt_anschriften_ergaenzen)"

    def alt(nummer, name, street, house_number, city=None, additional=None):
        # Stand nach dem Serverlauf 22.09.2026 mit der alten Erkennung, samt dessen Audit-Eintrag
        obj = ManagedObject.objects.create(
            object_number=nummer,
            name=name,
            street=street,
            house_number=house_number,
            city=city,
            additional_addresses=additional or [],
            management_type="weg",
            is_test=True,
        )
        record(
            "object.update",
            entity_type="object",
            entity_id=obj.pk,
            object_id=obj.pk,
            before={
                "street": None,
                "house_number": None,
                "postal_code": None,
                "city": None,
                "additional_addresses": [],
            },
            after={
                "street": obj.street,
                "house_number": obj.house_number,
                "postal_code": obj.postal_code,
                "city": obj.city,
                "additional_addresses": list(obj.additional_addresses),
            },
            reason=reason,
        )
        return obj

    seesen = alt("424", "Seesen Jacobsonstraße 24", "Seesen Jacobsonstraße", "24")
    juechen = alt(
        "374",
        "Jüchen, Wanloer Str. 28+30 & An der Sandkaule 5, 41363 Jüchen",
        "Wanloer Str. 28+30 & An der Sandkaule",
        "5",
        city="Jüchen",
    )
    # nach dem Lauf von Hand berichtigt: Audit-Nachzustand und Objekt weichen ab, bleibt unangetastet
    hand = alt("427", "Altena Am Stapel 10", "Altena Am Stapel", "10")
    ManagedObject.objects.filter(pk=hand.pk).update(street="Am Stapel", city="Altena")
    # Hauptanschrift stammt aus dem Register, das Kommando hat nur weitere Anschriften ergaenzt
    register = ManagedObject.objects.create(
        object_number="529",
        name="Kaiserstraße 77 u. 79, Windmühlenstraße 31",
        street="Kaiserstraße",
        house_number="77",
        additional_addresses=[
            {"street": "Kaiserstraße", "house_number": "79"},
            {"street": "Windmühlenstraße", "house_number": "31"},
        ],
        management_type="weg",
        is_test=True,
    )
    record(
        "object.update",
        entity_type="object",
        entity_id=register.pk,
        object_id=register.pk,
        before={"street": "Kaiserstraße", "house_number": "77", "additional_addresses": []},
        after={"street": "Kaiserstraße", "house_number": "77", "additional_addresses": []},
        reason=reason,
    )
    # korrekt gesetzt: nichts zu korrigieren
    alt("600", "Fontanestr. 7", "Fontanestr.", "7")

    out = StringIO()
    call_command("objekt_anschriften_ergaenzen", "--korrigieren", stdout=out)
    text = out.getvalue()
    assert (
        "Objekt 424 „Seesen Jacobsonstraße 24“: Korrektur Hauptanschrift: Seesen Jacobsonstraße 24 -> Jacobsonstraße 24, Seesen"
        in text
    )
    assert (
        "Objekt 374 „Jüchen, Wanloer Str. 28+30 & An der Sandkaule 5, 41363 Jüchen“: Korrektur Hauptanschrift: "
        "Wanloer Str. 28+30 & An der Sandkaule 5, Jüchen -> Wanloer Str. 28, 41363 Jüchen | weitere: "
        "Wanloer Str. 30, 41363 Jüchen; An der Sandkaule 5, 41363 Jüchen"
    ) in text
    assert (
        "Objekt 427 „Altena Am Stapel 10“: Hinweis: Anschrift seit dem letzten Lauf von Hand geändert" in text
    )
    assert "Objekt 600" not in text and "Objekt 529" not in text
    assert "korrigiert 2" in text and "Vorschau, nichts geändert" in text
    seesen.refresh_from_db()
    assert (seesen.street, seesen.city) == ("Seesen Jacobsonstraße", None)

    out = StringIO()
    call_command("objekt_anschriften_ergaenzen", "--korrigieren", "--echt", stdout=out)
    assert "korrigiert 2, gespeichert 2" in out.getvalue()
    seesen.refresh_from_db()
    juechen.refresh_from_db()
    hand.refresh_from_db()
    register.refresh_from_db()
    assert (seesen.street, seesen.house_number, seesen.city) == ("Jacobsonstraße", "24", "Seesen")
    assert (juechen.street, juechen.house_number, juechen.postal_code, juechen.city) == (
        "Wanloer Str.",
        "28",
        "41363",
        "Jüchen",
    )
    assert [(a["street"], a["house_number"]) for a in juechen.additional_addresses] == [
        ("Wanloer Str.", "30"),
        ("An der Sandkaule", "5"),
    ]
    assert (hand.street, hand.city) == ("Am Stapel", "Altena")
    assert register.street == "Kaiserstraße" and len(register.additional_addresses) == 2
    korrektur = AuditEvent.objects.get(
        entity_id=seesen.pk, reason__contains="--korrigieren", action="object.update"
    )
    assert korrektur.before_state["street"] == "Seesen Jacobsonstraße"
    assert korrektur.after_state["street"] == "Jacobsonstraße"

    # idempotent: die Korrektur ist der letzte Lauf, ein weiterer Lauf aendert nichts
    out = StringIO()
    call_command("objekt_anschriften_ergaenzen", "--korrigieren", "--echt", stdout=out)
    assert "korrigiert 0, gespeichert 0" in out.getvalue()
