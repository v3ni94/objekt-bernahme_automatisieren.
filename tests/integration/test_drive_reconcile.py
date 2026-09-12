"""Ordnerabgleich gegen den Fake (CR 14 Integrationstests, F 9.2): drei Testobjekte mit Dry-Run, Ausfuehrung und zweitem
Lauf; Objektordner-Erkennung; Sonderfaelle A bis H; Zaehlabweichung nach Umbenennung. Der Altname des Auffangbereichs
wird ausschliesslich aus der Konfiguration gelesen."""

from __future__ import annotations

import pytest

from apps.config import store
from apps.documents.models import Document, DocumentCategory, DocumentSubfolder
from apps.drive.adapter import InMemoryDriveAdapter, RecordingDriveAdapter, count_tree
from apps.drive.models import DriveNode, DriveSyncAction, DriveSyncRun
from apps.drive.reconcile import DriveConfig, reconcile_object, undo_rename
from apps.objects.models import ManagedObject
from apps.pipeline.models import ProcessingJob
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db


@pytest.fixture
def drive(seeded):
    return InMemoryDriveAdapter()


@pytest.fixture
def cfg(drive):
    return DriveConfig.from_settings(root_folder_id=drive.root_id)


def legacy_name() -> str:
    aliases = store.get("drive.legacy_folder_aliases")
    target = DocumentCategory.objects.get(code="06").folder_name
    return aliases[target][0]


def cat_name(code: str) -> str:
    return DocumentCategory.objects.get(code=code).folder_name


def make_object(number="623", **kw) -> ManagedObject:
    data = {
        "name": "Musterstadt, Musterstraße 1",
        "city": "Musterstadt",
        "street": "Musterstraße",
        "house_number": "1",
        "management_type": "weg",
        "is_test": True,
    }
    data.update(kw)
    return ManagedObject.objects.create(object_number=number, **data)


def actions(run: DriveSyncRun) -> list[DriveSyncAction]:
    return list(DriveSyncAction.objects.filter(sync_run=run).order_by("seq_no"))


def names(drive, folder_id) -> list[str]:
    return [n.name for n in drive.list_children(folder_id, folders_only=True)]


def test_objekt_ohne_unterordner(drive, cfg):
    obj = make_object()
    obj_id = drive.add_folder(drive.root_id, "623 Musterstadt, Musterstraße 1")
    rec = RecordingDriveAdapter(drive)
    dry = reconcile_object(obj, drive=rec, dry_run=True, cfg=cfg)
    assert dry.status == "done" and dry.dry_run and rec.read_only
    creates = [a for a in actions(dry) if a.action_type == "create_folder"]
    # sechs Hauptordner, vier Unterordner in 06, fuenfzehn Unterordner der Stammakte (12.09.2026)
    assert len(creates) == 6 + 4 + 15 and not [a for a in actions(dry) if a.action_type == "rename_folder"]
    assert dry.file_count_before == 0 and Document.objects.count() == 0 and ReviewCase.objects.count() == 0
    assert names(drive, obj_id) == []  # Dry-Run schreibt nichts

    run = reconcile_object(obj, drive=drive, dry_run=False, cfg=cfg)
    assert run.status == "done" and run.actions_executed == 25 and run.no_changes is False
    assert names(drive, obj_id) == [cat_name(c) for c in ("01", "02", "03", "04", "05", "06")]
    six = DriveNode.objects.get(object=obj, node_kind="main_folder", category_id="06")
    assert names(drive, six.drive_file_id) == [
        "01_Unklar",
        "02_Manuelle_Pruefung",
        "03_Dubletten",
        "04_Nicht_objektbezogen",
    ]
    two = DriveNode.objects.get(object=obj, node_kind="main_folder", category_id="02")
    assert names(drive, two.drive_file_id)[:2] == [
        "01_Objektstammdaten_und_Einheiten",
        "02_Grundstück_Rechte_und_Baulasten",
    ]
    assert (
        DriveNode.objects.filter(object=obj, node_kind="main_folder").count() == 6
        and DriveNode.objects.filter(object=obj, node_kind="subfolder").count() == 4 + 15
    )
    obj.refresh_from_db()
    assert obj.drive_root_folder_id == obj_id and run.file_count_before == run.file_count_after == 0

    rec2 = RecordingDriveAdapter(drive)
    second = reconcile_object(obj, drive=rec2, dry_run=False, cfg=cfg)
    assert second.no_changes is True and second.actions_executed == 0 and rec2.read_only
    assert {c[0] for c in rec2.calls} <= {"get", "list_children", "walk"}
    assert DriveNode.objects.filter(object=obj, status="active", is_folder=True).count() == 1 + 6 + 4 + 15


def test_objekt_mit_altordner_und_dateien(drive, cfg):
    obj = make_object(
        "624", name="Beispielstadt", city="Beispielstadt", street="Beispielweg", house_number="2"
    )
    alt = legacy_name()
    ids = drive.load_scenario(
        drive.root_id,
        {
            "624 Beispielstadt": {
                cat_name("01"): {},
                cat_name("02"): {},
                cat_name("03"): {},
                cat_name("04"): {},
                alt: {
                    "Alt1": {"a.pdf": "1", "b.pdf": "2", "c.pdf": "3"},
                    "Alt2": {"d.pdf": "4", "e.pdf": "5", "f.pdf": "6", "g.pdf": "7"},
                },
            }
        },
    )
    obj_id = ids["624 Beispielstadt"]
    legacy_id = ids[f"624 Beispielstadt/{alt}"]
    before = count_tree(drive, obj_id)
    assert before[0] == 7

    dry = reconcile_object(obj, drive=drive, dry_run=True, cfg=cfg)
    acts = actions(dry)
    rename = [a for a in acts if a.action_type == "rename_folder"]
    assert (
        len(rename) == 1
        and rename[0].file_count_before == 7
        and rename[0].name_after == cat_name("06")
        and rename[0].name_before == alt
    )
    create05 = [a for a in acts if a.action_type == "create_folder" and a.name_after == cat_name("05")]
    assert (
        create05 and create05[0].seq_no > rename[0].seq_no
    )  # erst umbenennen, dann Eigentuemerakte anlegen (CR 9.3)
    assert Document.objects.count() == 0
    assert drive.get(legacy_id).name == alt

    run = reconcile_object(obj, drive=drive, dry_run=False, cfg=cfg)
    assert run.status == "done"
    node = drive.get(legacy_id)
    assert node.name == cat_name("06") and node.id == legacy_id
    ren = [a for a in actions(run) if a.action_type == "rename_folder"][0]
    assert ren.result == "ok" and ren.file_count_after == 7 and ren.id_hash_after == ren.id_hash_before
    assert names(drive, obj_id) == [cat_name(c) for c in ("01", "02", "03", "04", "05", "06")]
    assert count_tree(drive, obj_id) == before
    assert Document.objects.filter(object=obj, source="drive_existing", status="registered").count() == 7
    assert ProcessingJob.objects.filter(object=obj, job_type="discover").count() == 7
    assert run.file_count_before == run.file_count_after == 7
    # zusaetzliche Unterordner (Alt1, Alt2) bleiben unveraendert, Hinweis im Protokoll
    assert sorted(names(drive, legacy_id)) == [
        "01_Unklar",
        "02_Manuelle_Pruefung",
        "03_Dubletten",
        "04_Nicht_objektbezogen",
        "Alt1",
        "Alt2",
    ]

    rec = RecordingDriveAdapter(drive)
    second = reconcile_object(obj, drive=rec, dry_run=False, cfg=cfg)
    assert (
        second.no_changes is True
        and rec.read_only
        and Document.objects.count() == 7
        and ProcessingJob.objects.count() == 7
    )

    # Rueckgaengig ueber Admin-Befehl, dann stellt der naechste Lauf den Zielnamen wieder her
    undo_rename(ren, drive=drive)
    assert drive.get(legacy_id).name == alt
    third = reconcile_object(obj, drive=drive, dry_run=False, cfg=cfg)
    assert drive.get(legacy_id).name == cat_name("06") and third.actions_executed == 1


def test_objekt_mit_vollstaendiger_struktur(drive, cfg):
    obj = make_object(
        "625", name="Beispielstadt", city="Beispielstadt", street="Beispielweg", house_number="3"
    )
    tree = {cat_name(c): {} for c in ("01", "02", "03", "04", "05")}
    tree[cat_name("02")] = {s.folder_name: {} for s in DocumentSubfolder.objects.filter(category_id="02")}
    tree[cat_name("06")] = {
        "01_Unklar": {},
        "02_Manuelle_Pruefung": {},
        "03_Dubletten": {},
        "04_Nicht_objektbezogen": {},
    }
    tree.update({"lose1.pdf": "1", "lose2.pdf": "2", "lose3.pdf": "3"})
    ids = drive.load_scenario(drive.root_id, {"625 Beispielstadt": tree})
    for label in ("dry", "run", "second"):
        rec = RecordingDriveAdapter(drive)
        run = reconcile_object(obj, drive=rec, dry_run=(label == "dry"), cfg=cfg)
        assert run.actions_executed == 0 and rec.read_only, label
        assert not [a for a in actions(run) if a.action_type in ("create_folder", "rename_folder")]
        assert run.file_count_before == 3
    assert Document.objects.filter(object=obj).count() == 3 and run.no_changes is True
    obj.refresh_from_db()
    assert obj.drive_root_folder_id == ids["625 Beispielstadt"]


def test_objektordner_erkennung(drive, cfg):
    drive.load_scenario(
        drive.root_id,
        {
            "0631 Ort A": {},
            "631 Ort A": {},
            "10014 Ort B": {},
            "6230 Ort C": {},
            "Archiv": {},
            "~623 Ort D": {},
        },
    )
    o631 = make_object("631", city="Ort A", street="Weg", house_number="1")
    run = reconcile_object(o631, drive=drive, dry_run=False, cfg=cfg)
    case = ReviewCase.objects.get(object=o631)
    assert (
        case.case_type == "duplicate_object_number"
        and case.case_subtype == "multiple_active"
        and len(case.candidates) == 2
    )
    assert run.root_matches == 2 and run.actions_executed == 0
    o631.refresh_from_db()
    assert o631.drive_root_folder_id is None

    o10014 = make_object("10014", city="Ort B", street="Weg", house_number="1")
    run = reconcile_object(o10014, drive=drive, dry_run=False, cfg=cfg)
    o10014.refresh_from_db()
    assert o10014.drive_root_folder_name == "10014 Ort B" and run.root_matches == 1

    o623 = make_object("623", city="Ort D", street="Weg", house_number="1")
    run = reconcile_object(o623, drive=drive, dry_run=False, cfg=cfg)
    case = ReviewCase.objects.get(object=o623)
    assert case.case_subtype == "candidate_in_trash" and run.actions_executed == 0
    assert "623 Ort D" not in [n.name for n in drive.list_children(drive.root_id)]  # nichts angelegt

    o700 = make_object("700", city="Ort E", street="Musterweg", house_number="5")
    run = reconcile_object(o700, drive=drive, dry_run=False, cfg=cfg)
    o700.refresh_from_db()
    assert o700.drive_root_folder_name == "700 Ort E, Musterweg 5" and run.status == "done"
    assert names(drive, o700.drive_root_folder_id) == [
        cat_name(c) for c in ("01", "02", "03", "04", "05", "06")
    ]
    assert "Archiv" in " ".join(run.summary["ignored_root_folders"])
    assert ReviewCase.objects.filter(object=o700).count() == 0

    # 6230 ist nicht 623: kein weiterer Kandidat fuer 623
    assert ReviewCase.objects.filter(object=o623).count() == 1

    unvollstaendig = make_object("701", city=None, street=None, house_number=None)
    run = reconcile_object(unvollstaendig, drive=drive, dry_run=False, cfg=cfg)
    assert run.status == "failed" and "Stammdaten fehlen" in run.error_message


def test_sonderfaelle(drive, cfg):
    alt = legacy_name()
    # A: Alt- und Zielordner nebeneinander
    obj = make_object("640", city="A", street="B", house_number="1")
    ids = drive.load_scenario(drive.root_id, {"640 A": {alt: {"x.pdf": "1"}, cat_name("06"): {}}})
    run = reconcile_object(obj, drive=drive, dry_run=False, cfg=cfg)
    case = ReviewCase.objects.get(object=obj, case_type="drive_structure")
    assert case.case_subtype == "legacy_and_target_both_exist"
    assert not [
        a for a in actions(run) if a.action_type == "create_folder" and a.name_after == cat_name("05")
    ]
    assert (
        DriveNode.objects.filter(
            object=obj, category_id="06", node_kind="main_folder", status="active"
        ).count()
        == 1
    )
    assert cat_name("01") in names(drive, ids["640 A"])  # uebrige Kategorien abgeglichen
    # B: mehrere Altordner
    obj_b = make_object("641", city="A", street="B", house_number="1")
    drive.load_scenario(drive.root_id, {"641 A": {alt: {}, alt.lower(): {}}})
    reconcile_object(obj_b, drive=drive, dry_run=False, cfg=cfg)
    assert ReviewCase.objects.get(object=obj_b).case_subtype == "multiple_legacy_folders"
    # C: zwei Ordner mit exakt gleichem Namen
    obj_c = make_object("642", city="A", street="B", house_number="1")
    ids_c = drive.load_scenario(drive.root_id, {"642 A": {}})
    drive.add_folder(ids_c["642 A"], cat_name("02"))
    drive.add_folder(ids_c["642 A"], cat_name("02"))
    run = reconcile_object(obj_c, drive=drive, dry_run=False, cfg=cfg)
    assert ReviewCase.objects.get(object=obj_c).case_subtype == "multiple_folders_same_name"
    assert names(drive, ids_c["642 A"]).count(cat_name("02")) == 2 and cat_name("01") in names(
        drive, ids_c["642 A"]
    )
    # D: abweichende Schreibweise, registriert ohne Umbenennung; lose Aehnlichkeit ergibt Fall
    obj_d = make_object("643", city="A", street="B", house_number="1")
    ids_d = drive.load_scenario(drive.root_id, {"643 A": {"05 Eigentuemerakte": {}, "Buchhaltung": {}}})
    run = reconcile_object(obj_d, drive=drive, dry_run=False, cfg=cfg)
    node05 = DriveNode.objects.get(object=obj_d, category_id="05", node_kind="main_folder")
    assert node05.drive_name == "05 Eigentuemerakte" and node05.expected_name == cat_name("05")
    assert "05 Eigentuemerakte" in names(drive, ids_d["643 A"]) and cat_name("05") not in names(
        drive, ids_d["643 A"]
    )
    case_d = ReviewCase.objects.get(object=obj_d, case_type="drive_structure")
    assert case_d.case_subtype == "similar_folder_name" and case_d.proposed_action["category_code"] == "03"
    assert cat_name("03") not in names(drive, ids_d["643 A"])
    # E: Hauptordner als Verknuepfung
    obj_e = make_object("644", city="A", street="B", house_number="1")
    ids_e = drive.load_scenario(drive.root_id, {"644 A": {}, "Extern": {"Ziel": {}}})
    target = ids_e["Extern/Ziel"]
    drive.rename(target, cat_name("01"))
    drive.add_shortcut(ids_e["644 A"], cat_name("01"), target)
    run = reconcile_object(obj_e, drive=drive, dry_run=False, cfg=cfg)
    assert (
        DriveNode.objects.get(object=obj_e, category_id="01", node_kind="main_folder").drive_file_id == target
    )
    assert any("Verknüpfung" in h for h in run.summary["hints"])
    # F: gespeicherte ID im Papierkorb
    obj_f = make_object("645", city="A", street="B", house_number="1")
    ids_f = drive.load_scenario(drive.root_id, {"645 A": {}})
    reconcile_object(obj_f, drive=drive, dry_run=False, cfg=cfg)
    drive._entry(ids_f["645 A"]).trashed = True
    run = reconcile_object(obj_f, drive=drive, dry_run=False, cfg=cfg)
    assert (
        ReviewCase.objects.get(object=obj_f).case_subtype == "drive_folder_missing"
        and run.actions_executed == 0
    )
    # G und H: lose Dateien und zusaetzliche Ordner
    obj_g = make_object("646", city="A", street="B", house_number="1")
    drive.load_scenario(drive.root_id, {"646 A": {"Fotos": {"f.jpg": "1"}, "lose.pdf": "2"}})
    run = reconcile_object(obj_g, drive=drive, dry_run=False, cfg=cfg)
    assert Document.objects.filter(object=obj_g).count() == 2 and any(
        "Zusätzlicher Ordner" in h for h in run.summary["hints"]
    )
    assert ReviewCase.objects.filter(object=obj_g).count() == 0


def test_zaehlabweichung_stoppt_schreibaktionen(drive, cfg, monkeypatch):
    alt = legacy_name()
    obj = make_object("650", city="A", street="B", house_number="1")
    ids = drive.load_scenario(drive.root_id, {"650 A": {alt: {"a.pdf": "1"}}})
    legacy_id = ids[f"650 A/{alt}"]
    original_rename = drive.rename

    def rename_and_add(file_id, new_name):
        node = original_rename(file_id, new_name)
        drive.add_file(file_id, "nachtraeglich.pdf", b"neu")  # gleichzeitige manuelle Aenderung
        return node

    monkeypatch.setattr(drive, "rename", rename_and_add)
    run = reconcile_object(obj, drive=drive, dry_run=False, cfg=cfg)
    acts = actions(run)
    ren = [a for a in acts if a.action_type == "rename_folder"][0]
    assert ren.result == "failed" and ren.file_count_after == 2 and run.status == "failed"
    assert ReviewCase.objects.filter(object=obj, case_subtype="rename_count_mismatch").exists()
    creates = [a for a in acts if a.action_type == "create_folder"]
    assert creates and all(a.result == "skipped" for a in creates)
    assert names(drive, ids["650 A"]) == [cat_name("06")]  # nichts weiter angelegt
    assert drive.get(legacy_id).name == cat_name("06")  # nichts zurueckgenommen


def test_wurzel_fehlt(drive, cfg):
    obj = make_object("660", city="A", street="B", house_number="1")
    run = reconcile_object(
        obj, drive=drive, dry_run=False, cfg=DriveConfig.from_settings(root_folder_id="nicht-da")
    )
    assert run.status == "failed" and "Wurzelordner" in run.error_message
    run = reconcile_object(
        obj, drive=drive, dry_run=False, cfg=DriveConfig.from_settings(root_folder_id=None)
    )
    assert run.status == "failed" and "root_folder_id" in run.error_message


def test_sammellauf_ohne_schreibbares_exportverzeichnis(monkeypatch):
    """Die Sammelfassung ist Beiwerk: schlaegt das Schreiben fehl (schreibgeschuetztes /data), meldet der Sammellauf
    den Fehler im Ergebnis, statt nach dem Abgleich aller Objekte abzubrechen (Deploy-Lauf 90 vom 12.09.2026)."""
    from apps.drive import protocol, tasks

    make_object("623")
    make_object("624", name="Beispielstadt", city="Beispielstadt", street="Beispielweg", house_number="3")
    gesehen: list[int] = []

    def fake_reconcile(object_id, dry_run, user_id=None, trigger="manual"):
        gesehen.append(object_id)
        return {"status": "done", "no_changes": False}

    def fake_summary():
        raise OSError(30, "Read-only file system", "/data/exports/drive-sync")

    monkeypatch.setattr(tasks, "reconcile_object_task", fake_reconcile)
    monkeypatch.setattr(protocol, "write_summary_xlsx", fake_summary)
    result = tasks.reconcile_all_task(dry_run=False)
    assert len(gesehen) == 2 and result["runs"] == 2 and result["with_changes"] == 2 and result["failed"] == 0
    assert "summary_file" not in result and "Read-only file system" in result["summary_error"]
