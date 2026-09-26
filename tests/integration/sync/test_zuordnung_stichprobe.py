"""zuordnung_stichprobe (Vorlage E-3, 26.09.2026): Ziehung je Hauptkategorie aus abgelegten Dokumenten mit
Paperless-Verknuepfung als CSV, deterministischer Seed, Herkunft des Objektbezugs (feldimport, pfad, ki, manuell,
drive_bestand), Drive-Ordnerkette aus drive_nodes, Auswertung einer ausgefuellten Liste als Markdown.
Ergaenzt 26.09.2026 (Gegenpruefung): Herkunft regel, ki und manuell ueber den Audit-Grund, Tiefenbegrenzung der
Ordnerkette, Aktenordner nur als Kennung, Auswertung einer in cp1252 gespeicherten Liste, Schutz der bereits
ausgefuellten Tages-CSV."""

from __future__ import annotations

import csv
import random
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import Document, DocumentCategory, DocumentSubfolder, DocumentType
from apps.drive.folders import ensure_category_folder, ensure_owner_folder, ensure_tenant_folder
from apps.drive.models import DriveNode, NodeKind
from apps.objects.models import Unit
from apps.parties.models import OwnerFile, TenantFile
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync.management.commands import zuordnung_stichprobe as cmd
from apps.sync.models import ExternalLink, LinkRole, LinkState, SyncSystem

pytestmark = pytest.mark.django_db

_zaehler = {"n": 0}


def _dok(
    obj,
    kategorie: str,
    *,
    source: str = "paperless",
    status: str = "filed",
    link: bool = True,
    paperless_id: int | None = None,
    node: DriveNode | None = None,
    subfolder: DocumentSubfolder | None = None,
    document_type: DocumentType | None = None,
) -> Document:
    _zaehler["n"] += 1
    n = _zaehler["n"]
    cat = DocumentCategory.objects.get(code=kategorie)
    if node is None:
        node = DriveNode.objects.get(
            object=obj, node_kind=NodeKind.MAIN_FOLDER, category=cat, status="active"
        )
    doc = Document.objects.create(
        object=obj,
        sha256=f"{n:064x}",
        size_bytes=10,
        mime_type="application/pdf",
        original_name=f"dok-{n}.pdf",
        current_name=f"dok-{n}.pdf",
        source=source,
        status=status,
        category=cat,
        subfolder=subfolder,
        document_type=document_type,
        drive_file_id=f"drive-{n}",
        drive_node=node,
        target_drive_node=node,
        first_seen_at=timezone.now(),
        filed_at=timezone.now(),
    )
    if link:
        ExternalLink.objects.create(
            document=doc,
            system=SyncSystem.PAPERLESS,
            role=LinkRole.ORIGINAL,
            external_id=str(paperless_id or 1000 + n),
            state=LinkState.LINKED,
            synced_fields={"object_field": obj.object_number},
        )
    return doc


def _lauf(*args) -> str:
    out = StringIO()
    call_command("zuordnung_stichprobe", *args, stdout=out)
    return out.getvalue()


def _lesen(pfad, encoding: str = "utf-8-sig") -> list[dict]:
    with open(pfad, encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def test_ziehung_schreibt_csv_je_kategorie(objekt, anderes_objekt, eingang, data_dir, tmp_path):
    for code in ("01", "02", "03", "04", "05"):
        for _ in range(3):
            _dok(objekt, code)
    _dok(objekt, "06")
    # nicht in der Grundgesamtheit: nicht abgelegt, ohne Verknuepfung, geloescht, Eingangsobjekt
    _dok(objekt, "01", status="classified")
    _dok(objekt, "02", link=False)
    geloescht = _dok(objekt, "03")
    Document.objects.filter(pk=geloescht.pk).update(deleted_at=timezone.now())
    irgendein_ordner = DriveNode.objects.filter(object=objekt, node_kind=NodeKind.MAIN_FOLDER).first()
    _dok(eingang, "06", node=irgendein_ordner)
    # anderes Objekt zaehlt ohne --objekt mit
    fremd = _dok(anderes_objekt, "06")
    csv_pfad = tmp_path / "probe.csv"

    out = _lauf("--je-kategorie", "2", "--seed", "7", "--csv", str(csv_pfad), "--ohne-paperless")
    assert "01 2 von 3" in out and "05 2 von 3" in out and "06 2 von 2" in out
    assert "Gezogen: 12 von 17" in out
    assert str(csv_pfad) in out
    assert "dok-" not in out and "Musterstraße" not in out
    rows = _lesen(csv_pfad)
    assert list(rows[0].keys()) == cmd.SPALTEN
    assert len(rows) == 12
    assert [r["kategorie"][:2] for r in rows] == [
        "01",
        "01",
        "02",
        "02",
        "03",
        "03",
        "04",
        "04",
        "05",
        "05",
        "06",
        "06",
    ]
    assert all(r["pruefung"] == "" and r["bemerkung"] == "" for r in rows)
    assert all(r["herkunft_objektbezug"] == "feldimport" for r in rows)
    assert {r["objekt"] for r in rows} == {"623", "624"}
    zeile_06 = next(r for r in rows if r["dokument_id"] == str(fremd.pk))
    root = DriveNode.objects.get(object=anderes_objekt, node_kind=NodeKind.OBJECT_ROOT, status="active")
    assert zeile_06["drive_pfad"] == f"{root.drive_name}/06_Sonstiges"
    assert zeile_06["paperless_id"] == ExternalLink.objects.get(document=fremd).external_id

    # Objektfilter und Standardpfad unter DATA_DIR/exports/stichproben
    out = _lauf("--je-kategorie", "2", "--seed", "7", "--objekt", "624", "--ohne-paperless")
    assert "Objekt 624" in out and "06 1 von 1" in out and "01 0 von 0" in out
    standard = data_dir / "exports" / "stichproben" / f"{timezone.localdate():%Y-%m-%d}_stichprobe.csv"
    assert standard.is_file() and f"CSV: {standard}" in out
    assert [r["dokument_id"] for r in _lesen(standard)] == [str(fremd.pk)]

    with pytest.raises(CommandError):
        _lauf("--objekt", "abc")
    with pytest.raises(CommandError):
        _lauf("--objekt", "999")


def test_seed_ist_deterministisch(objekt, tmp_path):
    docs = [_dok(objekt, "03") for _ in range(10)]
    ids = sorted(d.pk for d in docs)

    def ziehung(seed: int) -> list[str]:
        pfad = tmp_path / f"s{seed}.csv"
        _lauf("--je-kategorie", "4", "--seed", str(seed), "--csv", str(pfad), "--ohne-paperless")
        return [r["dokument_id"] for r in _lesen(pfad)]

    erste = ziehung(42)
    assert ziehung(42) == erste and len(erste) == 4
    erwartet = sorted(random.Random(42).sample(ids, 4))
    assert erste == [str(i) for i in erwartet]
    # ohne --seed gilt das Tagesdatum
    heute = int(timezone.localdate().strftime("%Y%m%d"))
    pfad = tmp_path / "heute.csv"
    out = _lauf("--je-kategorie", "4", "--csv", str(pfad), "--ohne-paperless")
    assert f"Seed {heute}" in out
    assert [r["dokument_id"] for r in _lesen(pfad)] == [
        str(i) for i in sorted(random.Random(heute).sample(ids, 4))
    ]


def test_herkunft_und_ordnerkette(objekt, anderes_objekt, eingang, drive, admin_user, tmp_path):
    unter = DocumentSubfolder.objects.get(category_id="02", code="01")
    art = DocumentType.objects.filter(category_id="02").first()
    node = ensure_category_folder(objekt, "02", "01", drive=drive)
    feld = _dok(objekt, "02", node=node, subfolder=unter, document_type=art)
    bestand = _dok(objekt, "03", source="drive_existing")
    hochgeladen = _dok(objekt, "03", source="upload")
    # manuell: Zuordnung im Dokumenteneingang (Fall mit resolution assign_object)
    manuell = _dok(anderes_objekt, "06", source="moved_in")
    ReviewCase.objects.create(
        object=anderes_objekt,
        case_type=CaseType.OBJECT_ASSIGNMENT,
        case_subtype="proposal",
        document=manuell,
        status=CaseStatus.RESOLVED,
        resolution={"action": "assign_object", "object_id": anderes_objekt.pk},
    )
    # ki: Gegenprobe oder Schiedsrichter (Fall ai_auto)
    ki = _dok(anderes_objekt, "06", source="moved_in")
    ReviewCase.objects.create(
        object=anderes_objekt,
        case_type=CaseType.OBJECT_ASSIGNMENT,
        case_subtype="ai_auto",
        document=ki,
        status=CaseStatus.RESOLVED,
        resolution={"action": "assign_object_ai", "object_id": anderes_objekt.pk},
    )
    # manuell: Uebernahme durch einen Nutzer ohne Fall (review.transfer_object mit Nutzer)
    uebernommen = _dok(anderes_objekt, "01", source="moved_in")
    record(
        "review.transfer_object",
        entity_type="document",
        entity_id=uebernommen.pk,
        object_id=anderes_objekt.pk,
        actor=admin_user,
        after={"to_object": anderes_objekt.pk},
    )
    # unbekannt: moved_in ohne Fall und ohne Audit
    offen = _dok(anderes_objekt, "01", source="moved_in")
    # regel: automatische Zuordnung aus dem Eingang (resolution assign_object_auto)
    regel = _dok(anderes_objekt, "01", source="moved_in")
    ReviewCase.objects.create(
        object=anderes_objekt,
        case_type=CaseType.OBJECT_ASSIGNMENT,
        case_subtype="proposal",
        document=regel,
        status=CaseStatus.RESOLVED,
        resolution={"action": "assign_object_auto", "object_id": anderes_objekt.pk},
    )
    # ki: Gegenprobe ohne Fall am neuen Dokument, erkennbar am Audit-Grund (crosscheck.py)
    gegenprobe = _dok(anderes_objekt, "01", source="moved_in")
    record(
        "review.transfer_object",
        entity_type="document",
        entity_id=gegenprobe.pk,
        object_id=anderes_objekt.pk,
        reason="KI-Gegenprobe Feldimport (0.90): Inhalt bereits im Objekt 624",
        after={"to_object": anderes_objekt.pk},
    )
    # manuell: Feldabgleich nach Handaenderung in Paperless (paperless_feld_abgleich.py), ohne Nutzer
    abgleich = _dok(anderes_objekt, "01", source="moved_in")
    record(
        "review.transfer_object",
        entity_type="document",
        entity_id=abgleich.pk,
        object_id=anderes_objekt.pk,
        reason="Feldabgleich Paperless (Feld geleert oder geändert)",
        after={"to_object": anderes_objekt.pk},
    )
    pfad = tmp_path / "herkunft.csv"
    _lauf("--je-kategorie", "10", "--seed", "1", "--csv", str(pfad), "--ohne-paperless")
    rows = {r["dokument_id"]: r for r in _lesen(pfad)}
    assert rows[str(regel.pk)]["herkunft_objektbezug"] == "regel"
    assert rows[str(gegenprobe.pk)]["herkunft_objektbezug"] == "ki"
    assert rows[str(abgleich.pk)]["herkunft_objektbezug"] == "manuell"
    assert rows[str(feld.pk)]["herkunft_objektbezug"] == "feldimport"
    assert rows[str(bestand.pk)]["herkunft_objektbezug"] == "drive_bestand"
    assert rows[str(hochgeladen.pk)]["herkunft_objektbezug"] == "upload"
    assert rows[str(manuell.pk)]["herkunft_objektbezug"] == "manuell"
    assert rows[str(ki.pk)]["herkunft_objektbezug"] == "ki"
    assert rows[str(uebernommen.pk)]["herkunft_objektbezug"] == "manuell"
    assert rows[str(offen.pk)]["herkunft_objektbezug"] == "unbekannt"
    root = DriveNode.objects.get(object=objekt, node_kind=NodeKind.OBJECT_ROOT, status="active")
    z = rows[str(feld.pk)]
    assert z["drive_pfad"] == f"{root.drive_name}/02_Stammakte/{unter.folder_name}"
    assert z["unterordner"] == unter.folder_name and z["dokumentart"] == art.name
    # abgebrochene Elternkette (Altbestand) und Ordnerstatus werden gekennzeichnet
    DriveNode.objects.filter(pk=node.pk).update(parent_node=None, status="missing")
    feld.drive_node.refresh_from_db()
    assert cmd.drive_pfad(Document.objects.get(pk=feld.pk)) == f".../{unter.folder_name} [missing]"
    # Tiefenbegrenzung: eine Kette mit mehr als MAX_TIEFE Ebenen unterhalb des Objektordners beginnt mit '...'
    main03 = DriveNode.objects.get(
        object=objekt, node_kind=NodeKind.MAIN_FOLDER, category_id="03", status="active"
    )
    eltern = main03
    for jahr in range(2000, 2000 + cmd.MAX_TIEFE + 1):
        eltern = DriveNode.objects.create(
            object=objekt,
            node_kind=NodeKind.YEAR_FOLDER,
            category_id="03",
            year=jahr,
            parent_node=eltern,
            drive_file_id=f"jahr-{jahr}",
            drive_parent_id=eltern.drive_file_id,
            drive_name=str(jahr),
            expected_name=str(jahr),
        )
    tief = _dok(objekt, "03", node=eltern)
    erwartet = "/".join(str(j) for j in range(2001, 2000 + cmd.MAX_TIEFE + 1))
    assert cmd.drive_pfad(tief) == f".../{erwartet}"
    assert cmd.drive_pfad(tief).count("/") == cmd.MAX_TIEFE


def test_aktenordner_nur_als_kennung(objekt, drive, tmp_path):
    """Aktenordner tragen in Drive den Namen des Mieters oder Eigentuemers; die Pruefliste zeigt nur die
    Kennung der Akte, Unterordner der Akte mit dem Katalognamen (26.09.2026)."""
    unit = Unit.objects.create(
        object=objekt, unit_label="WE03", unit_label_normalized="WE3", unit_number="3", unit_type="apartment"
    )
    mieter = TenantFile.objects.create(object=objekt, unit=unit, folder_name="Mustermann, Erika")
    mieter_node = ensure_tenant_folder(mieter, drive=drive)
    assert mieter_node.drive_name == "Mustermann, Erika"
    eigentuemer = OwnerFile.objects.create(object=objekt, unit=unit, folder_name="WE03_Mustermann")
    rows = ensure_owner_folder(eigentuemer, drive=drive)
    akte_node, unter_node = rows[0], rows[1]
    assert akte_node.drive_name == "WE03_Mustermann" and unter_node.node_kind == NodeKind.OWNER_FILE_SUBFOLDER
    a = _dok(objekt, "04", node=mieter_node)
    b = _dok(objekt, "05", node=unter_node)
    c = _dok(objekt, "05", node=akte_node)
    pfad = tmp_path / "akten.csv"
    _lauf("--je-kategorie", "10", "--seed", "1", "--csv", str(pfad), "--ohne-paperless")
    rows = {r["dokument_id"]: r["drive_pfad"] for r in _lesen(pfad)}
    root = DriveNode.objects.get(object=objekt, node_kind=NodeKind.OBJECT_ROOT, status="active")
    assert rows[str(a.pk)] == f"{root.drive_name}/04_Mieterakte/Mieterakte #{mieter.pk}"
    main05 = DriveNode.objects.get(
        object=objekt, node_kind=NodeKind.MAIN_FOLDER, category_id="05", status="active"
    )
    assert rows[str(c.pk)] == f"{root.drive_name}/{main05.drive_name}/Eigentuemerakte #{eigentuemer.pk}"
    assert rows[str(b.pk)] == (
        f"{root.drive_name}/{main05.drive_name}/Eigentuemerakte #{eigentuemer.pk}/{unter_node.expected_name}"
    )
    inhalt = pfad.read_text(encoding="utf-8-sig")
    assert "Mustermann" not in inhalt and "Erika" not in inhalt


def test_pfad_herkunft_aus_paperless_speicherpfad(objekt, paperless, tmp_path):
    sp = paperless.create_storage_path("623 – Musterstadt, Musterstraße 49")["id"]
    fremd = paperless.create_storage_path("0999 Anderes")["id"]
    p_pfad = paperless.add_document("Aus Pfad", b"%PDF-1.4 a", storage_path=sp)
    p_fremd = paperless.add_document("Fremder Pfad", b"%PDF-1.4 b", storage_path=fremd)
    p_ohne = paperless.add_document("Ohne Pfad", b"%PDF-1.4 c")
    a = _dok(objekt, "03", paperless_id=p_pfad)
    b = _dok(objekt, "03", paperless_id=p_fremd)
    c = _dok(objekt, "03", paperless_id=p_ohne)
    d = _dok(objekt, "03", source="drive_existing", paperless_id=p_pfad + 500)
    pfad = tmp_path / "pfad.csv"
    out = _lauf("--je-kategorie", "10", "--seed", "1", "--csv", str(pfad))
    rows = {r["dokument_id"]: r["herkunft_objektbezug"] for r in _lesen(pfad)}
    assert rows == {
        str(a.pk): "pfad",
        str(b.pk): "feldimport",
        str(c.pk): "feldimport",
        str(d.pk): "drive_bestand",
    }
    assert "Herkunft: drive_bestand 1, feldimport 2, pfad 1" in out
    # gebuendelte Abfrage (id__in), ein Speicherpfad wird je Kennung nur einmal gelesen
    assert sum(1 for op in paperless.call_names() if op == "iter_pages") == 1
    assert sum(1 for op in paperless.call_names() if op == "get_storage_path") == 2
    # Paperless nicht erreichbar: Hinweis, Herkunft bleibt feldimport, Lauf geht durch
    from apps.sync.paperless.errors import PaperlessUnavailable

    paperless.inject("iter_pages", PaperlessUnavailable("Wartung"))
    out = _lauf("--je-kategorie", "10", "--seed", "1", "--csv", str(pfad))
    assert "Hinweis: Paperless nicht erreichbar" in out
    assert {r["herkunft_objektbezug"] for r in _lesen(pfad)} == {"feldimport", "drive_bestand"}


def test_auswertung_der_ausgefuellten_liste(data_dir, tmp_path):
    quelle = tmp_path / "2026-09-26_stichprobe.csv"
    with quelle.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        w.writerow(cmd.SPALTEN)
        w.writerow(
            ["11", "501", "623", "01_Legitimationsunterlagen", "", "", "x/01", "feldimport", "richtig", ""]
        )
        w.writerow(
            ["12", "502", "623", "01_Legitimationsunterlagen", "", "", "x/01", "pfad", "Falsch", "Objekt 624"]
        )
        w.writerow(["13", "503", "623", "03_Buchhaltung", "", "", "x/03", "pfad", "richtig", ""])
        w.writerow(["14", "504", "623", "03_Buchhaltung", "", "", "x/03", "pfad", "unklar", ""])
        w.writerow(["15", "505", "623", "03_Buchhaltung", "", "", "x/03", "ki", "", ""])
        w.writerow(["16", "506", "624", "06_Sonstiges", "", "", "x/06", "manuell", " falsch ", ""])
    out = _lauf("--ergebnis", str(quelle))
    assert "Kategorie 01: gezogen 2, richtig 1, falsch 1, unklar 0, offen 0, Quote richtig 50,0 %" in out
    assert "Kategorie 03: gezogen 3, richtig 1, falsch 0, unklar 1, offen 1, Quote richtig 50,0 %" in out
    assert "Kategorie 06: gezogen 1, richtig 0, falsch 1, unklar 0, offen 0, Quote richtig 0,0 %" in out
    assert "Gesamt: gezogen 6, richtig 2, falsch 2, unklar 1, offen 1, Quote richtig 40,0 %" in out
    assert "Dokument-IDs mit Befund falsch: 12, 16" in out
    ziel = data_dir / "exports" / "stichproben" / f"{timezone.localdate():%Y-%m-%d}_ergebnis.md"
    assert ziel.is_file() and f"Auswertung: {ziel}" in out
    text = ziel.read_text(encoding="utf-8")
    assert "| 01 | 2 | 2 | 1 | 1 | 0 | 0 | 50,0 % |" in text
    assert "| gesamt | 6 | 5 | 2 | 2 | 1 | 1 | 40,0 % |" in text
    assert "Dokument-IDs mit Befund falsch: 12, 16" in text
    assert "2026-09-26_stichprobe.csv" in text

    # eigener Zielpfad und fehlende Spalten
    eigener = tmp_path / "auswertung.md"
    _lauf("--ergebnis", str(quelle), "--markdown", str(eigener))
    assert eigener.is_file()
    kaputt = tmp_path / "kaputt.csv"
    kaputt.write_text("a;b\r\n1;2\r\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Spalten fehlen"):
        _lauf("--ergebnis", str(kaputt))
    with pytest.raises(CommandError, match="nicht gefunden"):
        _lauf("--ergebnis", str(tmp_path / "fehlt.csv"))


def test_auswertung_liest_cp1252_aus_excel(data_dir, tmp_path):
    """Excel unter Windows speichert 'CSV (Trennzeichen-getrennt)' als ANSI (cp1252); die Auswertung liest die
    Datei trotz Umlauten und ss in drive_pfad und bemerkung (26.09.2026)."""
    quelle = tmp_path / "ansi.csv"
    zeilen = [
        cmd.SPALTEN,
        [
            "21",
            "601",
            "623",
            "01_Legitimationsunterlagen",
            "",
            "",
            "Musterstraße 49/01",
            "pfad",
            "richtig",
            "",
        ],
        [
            "22",
            "602",
            "623",
            "01_Legitimationsunterlagen",
            "",
            "",
            "Musterstraße 49/01",
            "pfad",
            "falsch",
            "gehört zu 624",
        ],
    ]
    quelle.write_bytes("\r\n".join(";".join(z) for z in zeilen).encode("cp1252") + b"\r\n")
    with pytest.raises(UnicodeDecodeError):
        quelle.read_text(encoding="utf-8")
    out = _lauf("--ergebnis", str(quelle), "--markdown", str(tmp_path / "ansi.md"))
    assert "Kategorie 01: gezogen 2, richtig 1, falsch 1, unklar 0, offen 0, Quote richtig 50,0 %" in out
    assert "Dokument-IDs mit Befund falsch: 22" in out
    # weder UTF-8 noch cp1252 (Byte 0x81 ist in cp1252 nicht belegt): CommandError statt Traceback
    kaputt = tmp_path / "kaputt.csv"
    kaputt.write_bytes(b"dokument_id;kategorie;pruefung\r\n1;01;\x81\xff\r\n")
    with pytest.raises(CommandError, match="weder UTF-8 noch cp1252"):
        _lauf("--ergebnis", str(kaputt))


def test_ausgefuellte_tagesliste_wird_nicht_ueberschrieben(objekt, data_dir, tmp_path):
    """Eine zurueckgespielte CSV mit Eintraegen in pruefung bleibt bei erneuter Ziehung erhalten; nur
    --ueberschreiben ersetzt sie (26.09.2026)."""
    docs = [_dok(objekt, "03") for _ in range(3)]
    standard = data_dir / "exports" / "stichproben" / f"{timezone.localdate():%Y-%m-%d}_stichprobe.csv"
    _lauf("--je-kategorie", "2", "--seed", "1", "--ohne-paperless")
    assert len(_lesen(standard)) == 2
    # leere Liste (keine Pruefergebnisse) darf ersetzt werden, auch mit anderer Auswahl
    _lauf("--je-kategorie", "3", "--seed", "1", "--ohne-paperless")
    rows = _lesen(standard)
    assert len(rows) == 3
    # Sachbearbeiter traegt einen Befund ein und spielt die Datei zurueck (hier: in Excel als ANSI gespeichert)
    rows[0]["pruefung"] = "richtig"
    rows[1]["bemerkung"] = "Ordner prüfen"
    with standard.open("w", encoding="cp1252", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cmd.SPALTEN, delimiter=";", lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(CommandError, match="enthaelt bereits Pruefergebnisse"):
        _lauf("--je-kategorie", "2", "--seed", "1", "--ohne-paperless")
    with pytest.raises(CommandError, match="enthaelt bereits Pruefergebnisse"):
        _lauf("--je-kategorie", "2", "--seed", "1", "--objekt", "623", "--ohne-paperless")
    erhalten = _lesen(standard, "cp1252")
    assert [r["dokument_id"] for r in erhalten] == [str(d.pk) for d in docs]
    assert erhalten[0]["pruefung"] == "richtig" and erhalten[1]["bemerkung"] == "Ordner prüfen"
    # anderer Pfad mit --csv geht, --ueberschreiben ersetzt die Tagesliste
    _lauf("--je-kategorie", "2", "--seed", "1", "--csv", str(tmp_path / "zusatz.csv"), "--ohne-paperless")
    assert _lesen(standard, "cp1252")[0]["pruefung"] == "richtig"
    _lauf("--je-kategorie", "2", "--seed", "1", "--ohne-paperless", "--ueberschreiben")
    neu = _lesen(standard)
    assert len(neu) == 2 and all(r["pruefung"] == "" for r in neu)
