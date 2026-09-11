"""Akten-Vorlage (Entscheidung 11.09.2026): Platzhalter-Einheiten aus der Sollzahl, Eigentuemer- und Mieterakte je
Einheit, Ordner in Drive sofort, Umbenennung nach Zuordnung, manuelle Namen bleiben, Mieterakte als Ablageziel."""

from __future__ import annotations

from datetime import date

import pytest

from apps.audit.models import AuditEvent
from apps.config import store
from apps.drive import oauth
from apps.drive.folders import ensure_unit_folders
from apps.drive.models import DriveNode, NodeKind
from apps.drive.reconcile import DriveConfig, reconcile_object
from apps.objects.models import ManagedObject, Unit
from apps.parties import services as party_services
from apps.parties.models import Owner, OwnerFile, Tenant, TenantFile, TenantUnitAssignment
from apps.parties.unit_files import ensure_placeholder_units, ensure_unit_files, tenant_file_for_document

pytestmark = pytest.mark.django_db


def _reconcile(obj, drive):
    reconcile_object(
        obj, drive=drive, dry_run=False, cfg=DriveConfig.from_settings(root_folder_id=drive.root_id)
    )


def _names(drive, obj, category):
    main = DriveNode.objects.get(object=obj, node_kind=NodeKind.MAIN_FOLDER, category_id=category)
    return sorted(n.name for n in drive.list_children(main.drive_file_id, folders_only=True))


@pytest.fixture
def objekt_mit_sollzahl(seeded, drive, admin_user):
    store.set("owner_file.create_folders_eagerly", True, user=admin_user, reason="Test")
    obj = ManagedObject.objects.create(
        object_number="641",
        name="Musterstadt, Vorlagenweg 1",
        street="Vorlagenweg",
        house_number="1",
        city="Musterstadt",
        management_type="weg",
        expected_unit_count=3,
        is_test=True,
    )
    _reconcile(obj, drive)
    return obj


def test_platzhalter_einheiten_und_akten(objekt_mit_sollzahl, drive):
    obj = objekt_mit_sollzahl
    created = ensure_placeholder_units(obj, obj.expected_unit_count)
    assert [u.unit_label for u in created] == ["WE 1", "WE 2", "WE 3"]
    assert {u.unit_label_normalized for u in created} == {"WE1", "WE2", "WE3"}
    assert all(u.unit_type == "apartment" and u.data_status == "incomplete" for u in created)
    assert ensure_placeholder_units(obj, 5) == []  # zweiter Aufruf legt nichts nach, Einheiten vorhanden
    stats = ensure_unit_files(obj)
    assert stats == {"owner_files": 3, "tenant_files": 3}
    assert sorted(OwnerFile.active.filter(object=obj).values_list("folder_name", flat=True)) == [
        "WE01",
        "WE02",
        "WE03",
    ]
    assert sorted(TenantFile.active.filter(object=obj).values_list("folder_name", flat=True)) == [
        "WE01",
        "WE02",
        "WE03",
    ]
    assert ensure_unit_files(obj) == {"owner_files": 0, "tenant_files": 0}  # idempotent
    result = ensure_unit_folders(obj, drive=drive)
    assert result["owner_folders"] == 3 and result["tenant_folders"] == 3 and result["renamed"] == 0
    assert _names(drive, obj, "05") == ["WE01", "WE02", "WE03"]
    assert _names(drive, obj, "04") == ["WE01", "WE02", "WE03"]
    we01 = DriveNode.objects.get(object=obj, node_kind=NodeKind.OWNER_FILE_FOLDER, drive_name="WE01")
    assert len(drive.list_children(we01.drive_file_id, folders_only=True)) == 11
    assert (
        DriveNode.objects.filter(object=obj, node_kind=NodeKind.TENANT_FILE_FOLDER, category_id="04").count()
        == 3
    )
    ops_before = len(drive.ops)
    ensure_unit_folders(obj, drive=drive)
    assert not [op for op in drive.ops[ops_before:] if op[0] in ("create_folder", "rename", "move")]


def test_zuordnung_benennt_akte_und_ordner_um(objekt_mit_sollzahl, drive):
    obj = objekt_mit_sollzahl
    ensure_placeholder_units(obj, 3)
    ensure_unit_files(obj)
    ensure_unit_folders(obj, drive=drive)
    we2 = Unit.active.get(object=obj, unit_label_normalized="WE2")
    owner = Owner.objects.create(
        type="natural_person", first_name="Erik", last_name="Mustermann", search_name="MUSTERMANN ERIK"
    )
    party_services.create_assignment(owner=owner, unit=we2, valid_from=date(2024, 1, 1), valid_to=None)
    akte = OwnerFile.active.get(unit=we2, file_kind="unit_owner")
    assert akte.folder_name == "WE02_Mustermann" and akte.owner_id == owner.pk
    assert akte.name_basis.get("adopted_placeholder") is True
    assert akte.file_assignments.count() == 1
    assert OwnerFile.active.filter(object=obj, file_kind="unit_owner").count() == 3  # keine zweite Akte
    stats = ensure_unit_folders(obj, drive=drive)
    assert stats["renamed"] == 1
    assert _names(drive, obj, "05") == ["WE01", "WE02_Mustermann", "WE03"]
    row = DriveNode.objects.get(owner_file=akte, node_kind=NodeKind.OWNER_FILE_FOLDER)
    assert row.drive_name == "WE02_Mustermann" and row.expected_name == "WE02_Mustermann"
    ev = AuditEvent.objects.filter(action="drive.rename", object_id=obj.pk).latest("id")
    assert ev.before_state == {"name": "WE02"} and ev.after_state["name"] == "WE02_Mustermann"
    # Mehrfacheigentum derselben Gruppe: Name waechst, Ordner folgt
    partner = Owner.objects.create(
        type="natural_person", first_name="Anna", last_name="Beispiel", search_name="BEISPIEL ANNA"
    )
    party_services.create_assignment(owner=partner, unit=we2, valid_from=date(2024, 1, 1), valid_to=None)
    akte.refresh_from_db()
    assert akte.folder_name == "WE02_Beispiel-Mustermann" or akte.folder_name == "WE02_Beispiel-Mustermann"
    ensure_unit_folders(obj, drive=drive)
    assert akte.folder_name in _names(drive, obj, "05")


def test_manuell_umbenannter_ordner_bleibt(objekt_mit_sollzahl, drive):
    obj = objekt_mit_sollzahl
    ensure_placeholder_units(obj, 3)
    ensure_unit_files(obj)
    ensure_unit_folders(obj, drive=drive)
    we3 = Unit.active.get(object=obj, unit_label_normalized="WE3")
    row = DriveNode.objects.get(owner_file__unit=we3, node_kind=NodeKind.OWNER_FILE_FOLDER)
    drive.rename(row.drive_file_id, "WE03 Sonderfall")
    owner = Owner.objects.create(
        type="natural_person", first_name="Karl", last_name="Schmidt", search_name="SCHMIDT KARL"
    )
    party_services.create_assignment(owner=owner, unit=we3, valid_from=date(2023, 5, 1), valid_to=None)
    stats = ensure_unit_folders(obj, drive=drive)
    assert stats["manual"] == 1 and stats["renamed"] == 0
    assert drive.get(row.drive_file_id).name == "WE03 Sonderfall"
    row.refresh_from_db()
    assert row.drive_name == "WE03 Sonderfall" and row.expected_name == "WE03_Schmidt"
    assert not AuditEvent.objects.filter(action="drive.rename", object_id=obj.pk).exists()


def test_mieterakte_wird_ablageziel(objekt_mit_sollzahl, drive):
    obj = objekt_mit_sollzahl
    ensure_placeholder_units(obj, 3)
    ensure_unit_files(obj)
    we1 = Unit.active.get(object=obj, unit_label_normalized="WE1")
    mieter = Tenant.objects.create(
        type="natural_person", first_name="Mia", last_name="Mieterling", search_name="MIETERLING MIA"
    )
    a = TenantUnitAssignment.objects.create(tenant=mieter, unit=we1, valid_from=date(2022, 3, 1))
    placeholder = TenantFile.active.get(unit=we1)
    akte = tenant_file_for_document(obj, [mieter.pk], [])
    assert akte.pk == placeholder.pk and akte.folder_name == "WE01_Mieterling"
    assert akte.file_assignments.filter(assignment=a).exists()
    # nur Einheit erkannt: Platzhalter der Einheit; ohne Einheit und Mieter keine Akte
    we2 = Unit.active.get(object=obj, unit_label_normalized="WE2")
    assert tenant_file_for_document(obj, [], [we2.pk]).folder_name == "WE02"
    assert tenant_file_for_document(obj, [], []) is None
    # ohne create wird keine Akte angelegt, eine vorhandene aber genutzt
    TenantFile.active.filter(unit=we2).delete()
    assert tenant_file_for_document(obj, [], [we2.pk], create=False) is None
    assert tenant_file_for_document(obj, [mieter.pk], [], create=False).pk == akte.pk
    ensure_unit_folders(obj, drive=drive)
    assert "WE01_Mieterling" in _names(drive, obj, "04")


def test_akte_ohne_platzhalter_folgt_der_gruppe(seeded, drive, admin_user):
    """Einheit von Hand angelegt (kein Platzhalter), Schalter an: die erste Zuordnung legt die Akte an, die zweite
    derselben Gruppe erweitert den Namen (Importzeile mit zwei Namen)."""
    store.set("owner_file.create_folders_eagerly", True, user=admin_user, reason="Test")
    obj = ManagedObject.objects.create(
        object_number="644",
        city="Musterstadt",
        street="Hand",
        house_number="4",
        management_type="weg",
        is_test=True,
    )
    unit = Unit.objects.create(
        object=obj, unit_label="WE 7", unit_label_normalized="WE7", unit_number="7", unit_type="apartment"
    )
    a = Owner.objects.create(type="natural_person", first_name="Ute", last_name="Alt", search_name="ALT UTE")
    b = Owner.objects.create(type="natural_person", first_name="Bo", last_name="Neu", search_name="NEU BO")
    party_services.create_assignment(owner=a, unit=unit, valid_from=date(2020, 1, 1), valid_to=None)
    akte = OwnerFile.active.get(unit=unit)
    assert akte.folder_name == "WE07_Alt" and akte.name_basis["managed_name"] is True
    party_services.create_assignment(owner=b, unit=unit, valid_from=date(2020, 1, 1), valid_to=None)
    akte.refresh_from_db()
    assert akte.folder_name == "WE07_Alt-Neu" and akte.owner_id is None and akte.file_assignments.count() == 2
    assert OwnerFile.active.filter(unit=unit).count() == 1


def test_ohne_schalter_keine_vorlage(seeded, drive, admin_user):
    store.set("owner_file.create_folders_eagerly", False, user=admin_user, reason="Test")
    obj = ManagedObject.objects.create(
        object_number="642",
        city="Musterstadt",
        street="Ohne",
        house_number="2",
        management_type="weg",
        is_test=True,
    )
    unit = Unit.objects.create(
        object=obj, unit_label="WE 7", unit_label_normalized="WE7", unit_number="7", unit_type="apartment"
    )
    owner = Owner.objects.create(
        type="natural_person", first_name="Ute", last_name="Alt", search_name="ALT UTE"
    )
    party_services.create_assignment(owner=owner, unit=unit, valid_from=date(2020, 1, 1), valid_to=None)
    assert not OwnerFile.active.filter(object=obj).exists()  # Akte erst mit der ersten Ablage (F 6.3)


def test_objektanlage_mit_sollzahl_ueber_die_oberflaeche(
    client_as, clerk_user, admin_user, drive, monkeypatch
):
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    store.set("drive.root_folder_id", drive.root_id, user=admin_user, reason="Test")
    store.set("owner_file.create_folders_eagerly", True, user=admin_user, reason="Test")
    client = client_as(clerk_user)
    resp = client.post(
        "/objekte/neu/",
        {
            "object_number": "643",
            "name": "Musterstadt, Vorlagenweg 3",
            "street": "Vorlagenweg",
            "house_number": "3",
            "postal_code": "12345",
            "city": "Musterstadt",
            "management_type": "weg",
            "status": "new",
            "fiscal_year_start_month": 1,
            "expected_unit_count": 2,
            "is_test": "on",
        },
        follow=True,
    )
    assert resp.status_code == 200
    obj = ManagedObject.objects.get(object_number="643")
    assert Unit.active.filter(object=obj).count() == 2
    texte = [m.message for m in resp.context["messages"]]
    assert any("2 Einheiten WE 1 bis WE 2" in t for t in texte)
    # Celery laeuft im Test sofort: Objektordner und Aktenordner stehen
    assert _names(drive, obj, "05") == ["WE01", "WE02"] and _names(drive, obj, "04") == ["WE01", "WE02"]
    assert b"Mieterakten (2)" in client.get(f"/objekte/{obj.pk}/").content


def test_knopf_akten_anlegen(client_as, clerk_user, admin_user, objekt_mit_sollzahl, drive, monkeypatch):
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    store.set("drive.root_folder_id", drive.root_id, user=admin_user, reason="Test")
    obj = objekt_mit_sollzahl
    client = client_as(clerk_user)
    resp = client.post(f"/objekte/{obj.pk}/akten/anlegen/", follow=True)
    assert resp.status_code == 200
    assert Unit.active.filter(object=obj).count() == 3
    assert _names(drive, obj, "05") == ["WE01", "WE02", "WE03"]
    ev = AuditEvent.objects.get(action="unit_files.prepare", entity_id=obj.pk)
    assert ev.after_state["units_created"] == 3 and ev.after_state["drive"] == "queued"
    texte = [m.message for m in resp.context["messages"]]
    assert any("3 Eigentümerakten und 3 Mieterakten" in t for t in texte)
