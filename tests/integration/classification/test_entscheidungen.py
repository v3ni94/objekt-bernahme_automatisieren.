"""Entscheidungstabelle B-09 und Quellenweiche B-10: Bestandsdatei unter Schwelle bleibt liegen (move_proposal),
Bestandsdatei als sichere Dublette wird nach 03_Dubletten verschoben, Pflichtmetadatum fehlt, Eigentuemer ohne
Zuordnung (Unbekannte_WE), weder Eigentuemer noch Einheit (Unzugeordnet), Pipeline-Dry-Run ohne Drive-Schreibzugriff,
Uebernahme in anderes Objekt (B-29)."""

from __future__ import annotations

import pytest

from apps.documents.models import Document, DocumentClassification, DocumentOwnerLink
from apps.documents.transfer import TransferError, transfer_document
from apps.drive.adapter import RecordingDriveAdapter
from apps.drive.models import DriveNode as DriveNodeRow
from apps.parties.models import OwnerFile
from apps.pipeline.models import JobType, ProcessingJob, ProcessingRun, RunStatus
from apps.pipeline.runs import start_run
from apps.review.models import ReviewCase

from .conftest import make_document

pytestmark = pytest.mark.django_db
H623 = "Objekt 623 Düsseldorf, Joachimstraße 49\n"


def _root(welt, number: str) -> str:
    return DriveNodeRow.objects.get(object=welt["objects"][number], node_kind="object_root").drive_file_id


def _main(welt, number: str, category: str) -> DriveNodeRow:
    return DriveNodeRow.objects.get(
        object=welt["objects"][number], node_kind="main_folder", category_id=category
    )


def _run(welt, run_all, number="623", **kw):
    run = start_run(welt["objects"][number], **kw)
    run_all(welt["objects"][number])
    run.refresh_from_db()
    return run


def test_bestandsdatei_unter_schwelle_bleibt_liegen(welt, fake_oauth, run_all):
    obj = welt["objects"]["623"]
    drive = welt["drive"]
    parent = _root(welt, "623")
    entry = {
        "filename": "Schreiben_unklar.pdf",
        "pages": [
            H623
            + "Sehr geehrte Damen und Herren, anbei die Unterlagen wie besprochen. Mit freundlichen Grüßen"
        ],
    }
    doc = make_document(obj, entry, source="drive_existing", drive=drive, parent_id=parent)
    ops_before = len(drive.ops)
    _run(welt, run_all)
    doc.refresh_from_db()
    assert doc.status == "review" and doc.category_id == "06" and doc.subfolder.code == "01"
    case = ReviewCase.objects.get(document=doc)
    assert case.case_type == "move_proposal" and case.case_subtype == "below_threshold"
    assert not [op for op in drive.ops[ops_before:] if op[0] in ("move", "upload", "copy", "create_folder")]
    assert drive.get(doc.drive_file_id).parent_id == parent  # liegt am Fundort
    assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.FILE_TO_DRIVE).exists()


def test_bestandsdatei_sichere_dublette_wird_verschoben(welt, fake_oauth, run_all, tmp_path):
    from corpus_generator import write_digital_pdf

    obj = welt["objects"]["623"]
    drive = welt["drive"]
    parent = _root(welt, "623")
    pdf = tmp_path / "ea.pdf"
    write_digital_pdf(
        pdf,
        [
            [
                H623.strip(),
                "Einzelabrechnung 2025 für Einheit WE03",
                "",
                "Eigentümer: Max Mustermann. Abrechnungsjahr 2025.",
                "Abrechnungsergebnis: Nachzahlung 312,40 EUR.",
            ]
        ],
    )
    content = pdf.read_bytes()
    a = drive.add_file(parent, "Einzelabrechnung_2025_WE03.pdf", content, "application/pdf")
    b = drive.add_file(parent, "Einzelabrechnung_2025_WE03 (Kopie).pdf", content, "application/pdf")
    from apps.drive.reconcile import reconcile_object

    reconcile_object(obj, drive=drive, dry_run=False, cfg=welt["cfg"])  # Inventur registriert beide Dateien
    docs = {d.drive_file_id: d for d in Document.objects.filter(object=obj, drive_file_id__in=[a, b])}
    assert len(docs) == 2
    _run(welt, run_all)
    first, second = Document.objects.get(drive_file_id=a), Document.objects.get(drive_file_id=b)
    assert {first.status, second.status} == {"filed", "duplicate"}
    dup = first if first.status == "duplicate" else second
    orig = second if dup is first else first
    assert dup.duplicate_of_id == orig.pk
    dup_node = drive.get(dup.drive_file_id)
    misc03 = DriveNodeRow.objects.get(
        object=obj, node_kind="subfolder", subfolder__category_id="06", subfolder__code="03"
    )
    assert dup_node.parent_id == misc03.drive_file_id
    assert (
        drive.get(orig.drive_file_id).parent_id
        == DriveNodeRow.objects.get(pk=orig.drive_node_id).drive_file_id
    )
    assert orig.drive_node.owner_file.folder_name == "WE03_Mustermann"


def test_pflichtmetadatum_fehlt(welt, fake_oauth, run_all):
    obj = welt["objects"]["623"]
    entry = {
        "filename": "Einzelabrechnung_WE03.pdf",
        "pages": [
            H623
            + "Einzelabrechnung für Einheit WE03\nEigentümer: Max Mustermann. Abrechnungsergebnis: Nachzahlung 312,40 EUR."
        ],
    }
    doc = make_document(obj, entry)
    _run(welt, run_all)
    doc.refresh_from_db()
    # Regel verlangt period_year; ohne Jahr greift die Einzelabrechnungsregel nicht, das Dokument geht ueber die
    # Kaltstart-Schwelle nicht hinaus und landet in 01_Unklar mit Fall
    assert doc.category_id == "06" and ReviewCase.objects.filter(document=doc, status="open").exists()
    entry = {
        "filename": "Gesamtwirtschaftsplan.pdf",
        "pages": [
            H623
            + "Gesamtwirtschaftsplan der Wohnungseigentümergemeinschaft mit Verteilerschlüssel für alle Einheiten, ohne Jahresangabe."
        ],
    }
    doc2 = make_document(obj, entry)
    _run(welt, run_all)
    doc2.refresh_from_db()
    case = ReviewCase.objects.get(document=doc2)
    assert case.case_type == "missing_metadata" and case.case_subtype == "period_year"
    assert (
        doc2.category_id == "06"
        and doc2.document_type.code == "gesamtwirtschaftsplan"
        and doc2.status == "review"
    )
    assert case.misc_subfolder.code == "02" and case.context["intended"]["category"] == "03"
    # Upload wird physisch in 06/02 abgelegt
    assert doc2.drive_node.subfolder.code == "02" and doc2.drive_node.category_id == "06"


def test_eigentuemer_ohne_zuordnung_und_unzugeordnet(welt, fake_oauth, run_all):
    obj = welt["objects"]["623"]
    from apps.parties.models import Owner

    Owner.objects.create(
        type="natural_person", first_name="Otto", last_name="Ohnezuordnung", search_name="OHNEZUORDNUNG OTTO"
    )
    # Eigentuemer ohne Zuordnung im Objekt kommt nicht in den Gazetteer des Objekts; also nur ueber Kandidatenname
    entry = {
        "filename": "Vollmacht_Mustermann.pdf",
        "pages": [
            H623
            + "Vollmacht\nHiermit bevollmächtige ich, Max Mustermann, Herrn Peter Altmann zur Vertretung gegenüber der Verwaltung in allen Angelegenheiten."
        ],
    }
    doc = make_document(obj, entry)
    _run(welt, run_all)
    doc.refresh_from_db()
    # zwei Eigentuemer, keine Einheit: Mustermann hat genau eine Zuordnung, Altmann eine (WE05, alt): mehrere Einheiten
    case = ReviewCase.objects.filter(document=doc).first()
    assert doc.category_id in ("05", "06")
    assert case is not None and case.case_type in ("owner_candidates",)
    entry = {
        "filename": "Sepa_ohne_Namen.pdf",
        "pages": [
            H623
            + "SEPA-Lastschriftmandat\nZahlungsempfänger: WEG Joachimstraße 49. Ich ermächtige den Zahlungsempfänger, Zahlungen von meinem Konto mittels Lastschrift einzuziehen. Mandatsreferenz WEG623-999."
        ],
    }
    doc2 = make_document(obj, entry)
    _run(welt, run_all)
    doc2.refresh_from_db()
    case2 = ReviewCase.objects.get(document=doc2)
    assert case2.case_type == "unclear" and case2.case_subtype == "no_owner_no_unit"
    assert not DocumentOwnerLink.objects.filter(document=doc2).exists()
    akte = OwnerFile.objects.get(pk=case2.context["owner_file_id"])
    assert akte.file_kind == "unassigned" and akte.folder_name == "Unzugeordnet"
    assert doc2.category_id == "05" and doc2.status == "review"
    assert doc2.drive_node.owner_file_id == akte.pk and doc2.drive_node.subfolder.code == "03"


def test_pipeline_dry_run_schreibt_nichts(welt, fake_oauth, run_all, monkeypatch):
    obj = welt["objects"]["623"]
    drive = welt["drive"]
    recording = RecordingDriveAdapter(drive)
    from apps.drive import oauth

    monkeypatch.setattr(oauth, "get_adapter", lambda: recording)
    parent = _root(welt, "623")
    entry = {
        "filename": "Einzelabrechnung_2025_WE03.pdf",
        "pages": [
            H623
            + "Einzelabrechnung 2025 für Einheit WE03\nEigentümer: Max Mustermann. Abrechnungsjahr 2025. Abrechnungsergebnis: Nachzahlung 312,40 EUR."
        ],
    }
    doc = make_document(obj, entry, source="drive_existing", drive=drive, parent_id=parent)
    run = _run(welt, run_all, dry_run=True)
    assert run.status == RunStatus.DONE and run.dry_run
    doc.refresh_from_db()
    assert doc.status == "ocr_done" and doc.category_id is None
    assert not ReviewCase.objects.filter(document=doc).exists()
    plan = DocumentClassification.objects.filter(document=doc).order_by("-id").first()
    assert plan.features["dry_run"] and plan.features["plan"].startswith("05/05") and not plan.is_final
    assert plan.category_id == "05" and plan.document_type.code == "einzelabrechnung"
    assert recording.write_calls == []
    # Ausfuehrung uebernimmt die Entscheidung ohne erneute OCR
    run2 = _run(welt, run_all)
    doc.refresh_from_db()
    assert run2.status == RunStatus.DONE and doc.status == "filed" and doc.category_id == "05"
    assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.OCR_CHUNK).exists()


def test_uebernahme_in_anderes_objekt(welt, fake_oauth, run_all, admin_user):
    obj, target = welt["objects"]["623"], welt["objects"]["631"]
    entry = {
        "filename": "Rechnung_Dachdecker.pdf",
        "pages": [
            "Dachdeckerei Muster GmbH\nRechnung Nr. 2026-0815\nObjekt: Beispielweg 2, Musterstadt. Reparatur Dachrinne. Betrag 1.845,20 EUR."
        ],
    }
    doc = make_document(obj, entry)
    _run(welt, run_all)
    doc.refresh_from_db()
    assert doc.category_id == "06" and doc.subfolder.code == "04"
    with pytest.raises(TransferError):
        transfer_document(doc, obj, user=admin_user)
    new = transfer_document(doc, target, user=admin_user, reason="Fremdobjekt laut Rechnung")
    doc.refresh_from_db()
    assert doc.status == "moved_out" and doc.duplicate_of_id == new.pk and doc.drive_file_id is None
    assert new.object_id == target.pk and new.source == "moved_in" and new.status == "ocr_done"
    assert new.pages.count() == 1 and new.drive_file_id is not None
    assert ProcessingJob.objects.filter(
        document=new, job_type=JobType.EXTRACT_ENTITIES, status="pending"
    ).exists()
    from apps.audit.models import AuditEvent

    assert AuditEvent.objects.filter(action="review.transfer_object", entity_id=new.pk).exists()
    assert AuditEvent.objects.filter(action="review.transfer_object", entity_id=doc.pk).exists()
    assert ProcessingRun.objects.filter(object=obj).count() == 1
