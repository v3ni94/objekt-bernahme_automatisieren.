"""Jahresordner unter 03_Buchhaltung (Wunsch der Geschaeftsfuehrung 24.09.2026): Ablage nach Abrechnungs- oder
Wirtschaftsjahr, sonst Zeitraum, sonst Dokumentdatum, sonst flach in 03; Jahresordner entstehen lazy bei der Ablage,
vorhandene werden per Namen uebernommen und vom Abgleich registriert; Umzug des Bestands per Befehl."""

from __future__ import annotations

from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command

from apps.documents import ingest
from apps.documents.models import Document
from apps.documents.periods import filing_year
from apps.drive.folders import ensure_category_folder
from apps.drive.models import DriveNode, NodeKind, NodeStatus
from apps.pipeline.jobs import enqueue, idempotency_key
from apps.pipeline.models import JobStatus, JobType, ProcessingJob

from .conftest import page_lines, reconcile

pytestmark = pytest.mark.django_db


def test_ablagejahr_reihenfolge():
    assert filing_year(2025, date(2026, 3, 1), date(2024, 1, 1)) == 2025
    assert filing_year(None, date(2026, 3, 1), date(2024, 1, 1)) == 2026
    assert filing_year(None, None, date(2024, 12, 31)) == 2024
    assert filing_year(None, None, None) is None
    assert filing_year(1800, None, None) is None  # unplausibles Jahr zaehlt nicht


def test_jahresordner_werden_angelegt_wiederverwendet_und_von_hand_angelegte_uebernommen(objekt, drive):
    main = ensure_category_folder(objekt, "03", None, drive=drive)
    a = ensure_category_folder(objekt, "03", None, drive=drive, year=2025)
    assert a.node_kind == NodeKind.YEAR_FOLDER and a.year == 2025 and a.category_id == "03"
    assert (
        drive.get(a.drive_file_id).name == "2025"
        and drive.get(a.drive_file_id).parent_id == main.drive_file_id
    )
    b = ensure_category_folder(
        objekt, "03", None, drive=drive, year=2024
    )  # zwei aktive Jahresordner nebeneinander
    assert b.pk != a.pk
    assert (
        DriveNode.objects.filter(
            object=objekt, node_kind=NodeKind.YEAR_FOLDER, status=NodeStatus.ACTIVE
        ).count()
        == 2
    )
    assert ensure_category_folder(objekt, "03", None, drive=drive, year=2025).pk == a.pk
    ordner_vorher = len([op for op in drive.ops if op[0] == "create_folder"])
    manuell = drive.add_folder(main.drive_file_id, "2023")
    c = ensure_category_folder(objekt, "03", None, drive=drive, year=2023)
    assert c.drive_file_id == manuell and c.year == 2023
    assert len([op for op in drive.ops if op[0] == "create_folder"]) == ordner_vorher
    assert ensure_category_folder(objekt, "03", None, drive=drive).pk == main.pk  # ohne Jahr der Hauptordner


def test_ablage_nach_abrechnungsjahr_in_den_jahresordner(objekt, stammdaten, pdf_factory, run_all, drive):
    pdf = pdf_factory(
        "Gesamtabrechnung_2025.pdf",
        [["Gesamtabrechnung 2025 der WEG Musterstadt, Abrechnungsjahr 2025", "Gesamtkosten 45.000,00 EUR"]],
    )
    doc, _run = ingest.ingest_upload(objekt, filename="Gesamtabrechnung_2025.pdf", data=pdf.read_bytes())
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.category_id == "03" and doc.status == "filed" and doc.period_year == 2025
    assert doc.drive_node.node_kind == NodeKind.YEAR_FOLDER and doc.drive_node.year == 2025
    knoten = drive.get(doc.drive_file_id)
    assert drive.get(knoten.parent_id).name == "2025"
    main = ensure_category_folder(objekt, "03", None, drive=drive)
    assert drive.get(knoten.parent_id).parent_id == main.drive_file_id


def _ablegen(objekt, doc, run_all):
    doc.status = "classified"
    doc.save(update_fields=["status", "updated_at"])
    enqueue(
        JobType.FILE_TO_DRIVE,
        objekt,
        key=idempotency_key(JobType.FILE_TO_DRIVE, objekt.pk, doc.sha256),
        document=doc,
        payload={"category": "03", "subfolder": None},
    )
    run_all(objekt)
    doc.refresh_from_db()
    return ProcessingJob.objects.filter(document=doc, job_type=JobType.FILE_TO_DRIVE).order_by("-id").first()


def test_ohne_jahr_flach_und_umzug_per_befehl(objekt, stammdaten, pdf_factory, run_all, drive):
    pdf = pdf_factory("beleg.pdf", [page_lines("B", 1, 1)])
    doc, _run = ingest.ingest_upload(objekt, filename="beleg.pdf", data=pdf.read_bytes())
    run_all(objekt)
    doc.refresh_from_db()
    Document.objects.filter(pk=doc.pk).update(
        category_id="03",
        subfolder=None,
        period_year=None,
        period_from=None,
        period_to=None,
        document_date=None,
    )
    doc.refresh_from_db()
    job = _ablegen(objekt, doc, run_all)
    assert job.status == JobStatus.DONE, job.last_error
    main = ensure_category_folder(objekt, "03", None, drive=drive)
    assert drive.get(doc.drive_file_id).parent_id == main.drive_file_id and doc.status == "filed"
    assert doc.drive_node.node_kind == NodeKind.MAIN_FOLDER

    # Jahr nachgetragen (etwa im Pruefcenter): der Umzugsbefehl reiht die Ablage erneut ein
    Document.objects.filter(pk=doc.pk).update(period_year=2026)
    out = StringIO()
    call_command("buchhaltung_jahresordner", stdout=out)
    text = out.getvalue()
    assert f"Objekt {objekt.object_number}, Jahr 2026: 1" in text and "Vorschau" in text
    doc.refresh_from_db()
    assert doc.status == "filed"
    out = StringIO()
    call_command("buchhaltung_jahresordner", "--echt", "--objekt", objekt.object_number, stdout=out)
    assert "1 Ablagejobs eingereiht" in out.getvalue()
    run_all(objekt)
    doc.refresh_from_db()
    assert (
        doc.status == "filed"
        and doc.drive_node.node_kind == NodeKind.YEAR_FOLDER
        and doc.drive_node.year == 2026
    )
    assert drive.get(drive.get(doc.drive_file_id).parent_id).name == "2026"
    assert not [op for op in drive.ops if op[0] in ("trash", "delete")]
    out = StringIO()
    call_command("buchhaltung_jahresordner", "--echt", stdout=out)
    assert "Umzug in Jahresordner: 0 | bereits im Jahresordner: 1" in out.getvalue()

    # ohne jedes Jahr bleibt ein Dokument flach und wird gezaehlt
    pdf2 = pdf_factory("beleg2.pdf", [page_lines("C", 1, 1)])
    doc2, _run = ingest.ingest_upload(objekt, filename="beleg2.pdf", data=pdf2.read_bytes())
    run_all(objekt)
    Document.objects.filter(pk=doc2.pk).update(
        category_id="03",
        subfolder=None,
        period_year=None,
        period_from=None,
        period_to=None,
        document_date=None,
    )
    doc2.refresh_from_db()
    _ablegen(objekt, doc2, run_all)
    out = StringIO()
    call_command("buchhaltung_jahresordner", stdout=out)
    assert "ohne Jahr (bleiben flach in 03): 1" in out.getvalue()


def test_pruefcenter_zielpfad_mit_jahr(objekt, drive):
    from apps.review.services import Target, _folder_for_target

    assert _folder_for_target(objekt, Target(category="03")) == ("03_Buchhaltung", True)
    assert _folder_for_target(objekt, Target(category="03", period_year=2025)) == (
        "03_Buchhaltung/2025",
        False,
    )
    ensure_category_folder(objekt, "03", None, drive=drive, year=2025)
    assert _folder_for_target(objekt, Target(category="03", period_year=2025)) == (
        "03_Buchhaltung/2025",
        True,
    )
    assert (
        _folder_for_target(objekt, Target(category="03", period_from=date(2026, 3, 1)))[0]
        == "03_Buchhaltung/2026"
    )
    assert _folder_for_target(objekt, Target(category="02", subfolder="08", period_year=2025))[0] == (
        "02_Stammakte/08_Versicherungen"
    )


def test_abgleich_registriert_vorhandene_jahresordner(objekt, drive):
    main = ensure_category_folder(objekt, "03", None, drive=drive)
    jahr = drive.add_folder(main.drive_file_id, "2024")
    anderer = drive.add_folder(main.drive_file_id, "Altes")
    reconcile(objekt, drive)
    row = DriveNode.objects.get(drive_file_id=jahr)
    assert row.node_kind == NodeKind.YEAR_FOLDER and row.year == 2024 and row.status == NodeStatus.ACTIVE
    assert row.parent_node_id == main.pk and row.category_id == "03"
    assert not DriveNode.objects.filter(drive_file_id=anderer).exists()
    reconcile(objekt, drive)  # idempotent
    assert (
        DriveNode.objects.filter(
            object=objekt, node_kind=NodeKind.YEAR_FOLDER, status=NodeStatus.ACTIVE
        ).count()
        == 1
    )
    assert ensure_category_folder(objekt, "03", None, drive=drive, year=2024).pk == row.pk


def test_bestandsdateien_in_und_unter_03(objekt, drive, run_all):
    """Abgleich: Bestandsdateien im Jahresordner bekommen den Jahresknoten (Kategorie 03); erneute Ablage laesst sie
    stehen (already_there); eine flach liegende Bestandsdatei mit Jahr wird per Elternwechsel verschoben, nie hochgeladen."""
    main = ensure_category_folder(objekt, "03", None, drive=drive)
    jahr = drive.add_folder(main.drive_file_id, "2024")
    im_jahr = drive.add_file(jahr, "Beleg_im_Jahresordner.pdf", b"a", mime_type="application/pdf")
    flach = drive.add_file(main.drive_file_id, "Beleg_flach.pdf", b"b", mime_type="application/pdf")
    reconcile(objekt, drive)
    d1 = Document.objects.get(drive_file_id=im_jahr)
    d2 = Document.objects.get(drive_file_id=flach)
    assert d1.source == "drive_existing"
    assert d1.drive_node.node_kind == NodeKind.YEAR_FOLDER and d1.drive_node.year == 2024
    assert d2.drive_node.node_kind == NodeKind.MAIN_FOLDER and d2.drive_node.category_id == "03"
    for d in (d1, d2):
        Document.objects.filter(pk=d.pk).update(
            category_id="03", subfolder=None, period_year=2024, status="classified"
        )
        d.refresh_from_db()
        enqueue(
            JobType.FILE_TO_DRIVE,
            objekt,
            key=idempotency_key(JobType.FILE_TO_DRIVE, objekt.pk, d.sha256 or f"doc-{d.pk}"),
            document=d,
            payload={"category": "03", "subfolder": None},
        )
    run_all(objekt)
    d1.refresh_from_db()
    d2.refresh_from_db()
    j1 = ProcessingJob.objects.filter(document=d1, job_type=JobType.FILE_TO_DRIVE).order_by("-id").first()
    j2 = ProcessingJob.objects.filter(document=d2, job_type=JobType.FILE_TO_DRIVE).order_by("-id").first()
    assert j1.status == JobStatus.DONE and j1.result.get("action") == "already_there", j1.last_error
    assert j2.status == JobStatus.DONE and j2.result.get("action") == "moved", j2.last_error
    assert drive.get(d2.drive_file_id).parent_id == jahr and d2.drive_node.year == 2024
    assert not [op for op in drive.ops if op[0] == "upload"]


def test_zeitraum_ueber_jahreswechsel_und_stammakte_ohne_jahresordner(
    objekt, stammdaten, pdf_factory, run_all, drive
):
    pdf = pdf_factory("konto.pdf", [page_lines("K", 1, 1)])
    doc, _run = ingest.ingest_upload(objekt, filename="konto.pdf", data=pdf.read_bytes())
    run_all(objekt)
    Document.objects.filter(pk=doc.pk).update(
        category_id="03",
        subfolder=None,
        period_year=None,
        period_from=date(2025, 10, 1),
        period_to=date(2026, 9, 30),
        document_date=date(2026, 10, 5),
    )
    doc.refresh_from_db()
    _ablegen(objekt, doc, run_all)
    assert (
        doc.drive_node.node_kind == NodeKind.YEAR_FOLDER and doc.drive_node.year == 2025
    )  # Beginn des Zeitraums

    pdf2 = pdf_factory("police.pdf", [page_lines("P", 1, 1)])
    doc2, _run = ingest.ingest_upload(objekt, filename="police.pdf", data=pdf2.read_bytes())
    run_all(objekt)
    Document.objects.filter(pk=doc2.pk).update(category_id="02", period_year=2025, status="classified")
    doc2.refresh_from_db()
    enqueue(
        JobType.FILE_TO_DRIVE,
        objekt,
        key=idempotency_key(JobType.FILE_TO_DRIVE, objekt.pk, doc2.sha256),
        document=doc2,
        payload={"category": "02", "subfolder": "08"},
    )
    run_all(objekt)
    doc2.refresh_from_db()
    assert doc2.drive_node.node_kind == NodeKind.SUBFOLDER and doc2.drive_node.subfolder.code == "08"
    assert not DriveNode.objects.filter(
        object=objekt, node_kind=NodeKind.YEAR_FOLDER, category_id="02"
    ).exists()


def test_umzugsbefehl_zieht_falsches_jahr_und_zaehlt_offene_faelle(
    objekt, stammdaten, pdf_factory, run_all, drive
):
    pdf = pdf_factory("b.pdf", [page_lines("B", 1, 1)])
    doc, _run = ingest.ingest_upload(objekt, filename="b.pdf", data=pdf.read_bytes())
    run_all(objekt)
    Document.objects.filter(pk=doc.pk).update(
        category_id="03",
        subfolder=None,
        period_year=2025,
        period_from=None,
        period_to=None,
        document_date=None,
    )
    doc.refresh_from_db()
    _ablegen(objekt, doc, run_all)
    assert doc.drive_node.year == 2025
    Document.objects.filter(pk=doc.pk).update(period_year=2026)  # Jahr nach der Ablage korrigiert
    out = StringIO()
    call_command("buchhaltung_jahresordner", stdout=out)
    text = out.getvalue()
    assert f"Objekt {objekt.object_number}, Jahr 2026: 1" in text and "im falschen Jahr: 1" in text
    call_command("buchhaltung_jahresordner", "--echt", stdout=StringIO())
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "filed" and doc.drive_node.year == 2026
    assert drive.get(drive.get(doc.drive_file_id).parent_id).name == "2026"
    Document.objects.filter(pk=doc.pk).update(status="review", period_year=2024)
    out = StringIO()
    call_command("buchhaltung_jahresordner", stdout=out)
    assert "mit offenem Fall (ziehen mit der Entscheidung um): 1" in out.getvalue()
    assert "Umzug in Jahresordner: 0" in out.getvalue()


def test_doppelte_jahresordner_ergeben_prueffall(objekt, drive):
    from apps.review.models import ReviewCase

    main = ensure_category_folder(objekt, "03", None, drive=drive)
    drive.add_folder(main.drive_file_id, "2024")
    drive.add_folder(main.drive_file_id, " 2024 ")
    reconcile(objekt, drive)
    assert not DriveNode.objects.filter(
        object=objekt, node_kind=NodeKind.YEAR_FOLDER, status=NodeStatus.ACTIVE
    ).exists()
    fall = ReviewCase.objects.filter(
        object=objekt, case_type="drive_structure", case_subtype="multiple_folders_same_name", status="open"
    )
    assert fall.exists()


def test_fehlender_jahresordner_wird_erkannt(objekt, drive):
    """Ordner 2025 von Hand in den Papierkorb gelegt: der Abgleich setzt die Zeile auf fehlend, die naechste Ablage
    legt neu an; ohne Abgleich erkennt die Ordneranlage den fehlenden Ordner selbst (Live-Pruefung)."""
    alt = ensure_category_folder(objekt, "03", None, drive=drive, year=2025)
    drive.set_trashed(alt.drive_file_id, True)
    reconcile(objekt, drive)
    alt.refresh_from_db()
    assert alt.status == NodeStatus.MISSING
    neu = ensure_category_folder(objekt, "03", None, drive=drive, year=2025)
    assert neu.pk != alt.pk and neu.status == NodeStatus.ACTIVE and not drive.get(neu.drive_file_id).trashed
    drive.set_trashed(neu.drive_file_id, True)
    neu2 = ensure_category_folder(objekt, "03", None, drive=drive, year=2025)
    neu.refresh_from_db()
    assert neu2.pk != neu.pk and neu.status == NodeStatus.MISSING and neu2.status == NodeStatus.ACTIVE
    assert (
        DriveNode.objects.filter(
            object=objekt, node_kind=NodeKind.YEAR_FOLDER, year=2025, status=NodeStatus.ACTIVE
        ).count()
        == 1
    )


def test_sammelaktion_zeigt_jahr_aus_dokumentdatum(objekt, stammdaten, pdf_factory, run_all, drive):
    from apps.review.models import ReviewCase
    from apps.review.services import bulk_rows

    pdf = pdf_factory("r.pdf", [page_lines("R", 1, 1)])
    doc, _run = ingest.ingest_upload(objekt, filename="r.pdf", data=pdf.read_bytes())
    run_all(objekt)
    Document.objects.filter(pk=doc.pk).update(
        period_year=None, period_from=None, period_to=None, document_date=date(2026, 2, 15), status="review"
    )
    ReviewCase.objects.filter(document=doc).update(status="dismissed")
    case = ReviewCase.objects.create(
        object=objekt,
        document=doc,
        case_type="unclear",
        case_subtype="below_threshold",
        status="open",
        context={"intended": {"category": "03", "subfolder": None, "document_type": "beleg"}},
    )
    row = bulk_rows([case.pk])[0]
    assert (row.folder_name, row.folder_exists) == ("03_Buchhaltung/2026", False), row.errors
    ensure_category_folder(objekt, "03", None, drive=drive, year=2026)
    assert bulk_rows([case.pk])[0].folder_exists is True
