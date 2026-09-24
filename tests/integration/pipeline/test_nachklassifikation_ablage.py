"""Erneute Ablage eines bereits in Drive liegenden Dokuments (Nachklassifikation, 23.09.2026): die vorhandene Datei
wird verschoben statt erneut hochgeladen; liegt sie im Papierkorb, wird wie zuvor aus dem Original hochgeladen.
Altkopien aus der Zeit vor der Korrektur legt drive_dubletten_bereinigen in den Papierkorb."""

from __future__ import annotations

import shutil
from io import StringIO

import pytest
from django.core.management import call_command

from apps.audit.models import AuditEvent
from apps.audit.services import record
from apps.documents import ingest
from apps.drive.folders import ensure_category_folder
from apps.pipeline import storage
from apps.pipeline.jobs import enqueue, idempotency_key
from apps.pipeline.models import JobStatus, JobType, ProcessingJob

from .conftest import page_lines

pytestmark = pytest.mark.django_db


def _erste_ablage(objekt, pdf_factory, run_all):
    pdf = pdf_factory("nachklass.pdf", [page_lines("N", 1, 1)])
    doc, _run = ingest.ingest_upload(objekt, filename="nachklass.pdf", data=pdf.read_bytes())
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.drive_file_id, "erste Ablage muss eine Drive-Datei erzeugen"
    return doc, pdf


def _anderes_ziel(objekt, drive, aktueller_elternordner):
    for code in ("02", "01"):  # nicht 03: dort entscheidet seit 24.09.2026 das Jahr ueber den Zielordner
        row = ensure_category_folder(objekt, code, None, drive=drive)
        if row.drive_file_id != aktueller_elternordner:
            return code, row
    raise AssertionError("kein abweichender Zielordner")


def _erneut_ablegen(objekt, doc, code, run_all):
    doc.status = "classified"
    doc.save(update_fields=["status", "updated_at"])
    enqueue(
        JobType.FILE_TO_DRIVE,
        objekt,
        key=idempotency_key(JobType.FILE_TO_DRIVE, objekt.pk, doc.sha256),
        document=doc,
        payload={"category": code, "subfolder": None},
    )
    run_all(objekt)
    doc.refresh_from_db()
    return ProcessingJob.objects.filter(document=doc, job_type=JobType.FILE_TO_DRIVE).order_by("-id").first()


def _uploads(drive):
    return [op for op in drive.ops if op[0] == "upload"]


def test_erneute_ablage_verschiebt_vorhandene_drive_datei_ohne_original(objekt, drive, pdf_factory, run_all):
    doc, _pdf = _erste_ablage(objekt, pdf_factory, run_all)
    alt_id = doc.drive_file_id
    alter_ordner = drive.get(alt_id).parent_id
    code, ziel = _anderes_ziel(objekt, drive, alter_ordner)
    # Arbeitsverzeichnis geraeumt: ohne Korrektur scheiterte die Ablage mit „Quelldatei fuer den Upload fehlt“
    shutil.rmtree(storage.work_dir(doc.sha256), ignore_errors=True)
    assert storage.original_path(doc.sha256) is None
    uploads_vorher = len(_uploads(drive))

    job = _erneut_ablegen(objekt, doc, code, run_all)

    assert job.status == JobStatus.DONE, job.last_error
    assert job.result.get("action") == "moved"
    assert doc.drive_file_id == alt_id and doc.status == "filed"
    assert drive.get(alt_id).parent_id == ziel.drive_file_id
    assert len(_uploads(drive)) == uploads_vorher
    assert AuditEvent.objects.filter(action="drive.move", entity_type="document", entity_id=doc.pk).exists()


def test_erneute_ablage_laedt_hoch_wenn_alte_datei_im_papierkorb_liegt(objekt, drive, pdf_factory, run_all):
    doc, pdf = _erste_ablage(objekt, pdf_factory, run_all)
    alt_id = doc.drive_file_id
    code, ziel = _anderes_ziel(objekt, drive, drive.get(alt_id).parent_id)
    if storage.original_path(doc.sha256) is None:
        wd = storage.work_dir(doc.sha256)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "original.pdf").write_bytes(pdf.read_bytes())
    drive.set_trashed(alt_id, True)
    uploads_vorher = len(_uploads(drive))

    job = _erneut_ablegen(objekt, doc, code, run_all)

    assert job.status == JobStatus.DONE, job.last_error
    assert job.result.get("action") == "uploaded"
    assert doc.drive_file_id != alt_id
    assert drive.get(doc.drive_file_id).parent_id == ziel.drive_file_id
    assert len(_uploads(drive)) == uploads_vorher + 1
    assert drive.get(alt_id).trashed  # der Papierkorb wird nicht angeruehrt


def test_dubletten_bereinigen_legt_bestaetigte_altkopien_in_den_papierkorb(
    objekt, drive, pdf_factory, run_all
):
    doc, _pdf = _erste_ablage(objekt, pdf_factory, run_all)
    alt_id = doc.drive_file_id
    assert AuditEvent.objects.filter(action="drive.upload", entity_id=doc.pk).count() == 1
    _code, ziel = _anderes_ziel(objekt, drive, drive.get(alt_id).parent_id)
    # Verhalten vor der Korrektur nachgebildet: Zweitkopie im Zielordner, Dokument zeigt auf die neue Datei
    neu_id = drive.add_file(
        ziel.drive_file_id,
        doc.current_name,
        b"kopie",
        app_properties={"sha256": doc.sha256, "document_id": str(doc.pk)},
    )
    doc.drive_file_id = neu_id
    doc.save(update_fields=["drive_file_id", "updated_at"])
    record(
        "drive.upload",
        entity_type="document",
        entity_id=doc.pk,
        object_id=objekt.pk,
        after={"drive_file_id": neu_id, "to": ziel.drive_file_id, "name": doc.current_name},
    )
    # fehlerhafter Protokolleintrag auf eine fremde Datei: wird nicht bestaetigt und bleibt liegen
    fremd_id = drive.add_file(drive.root_id, "fremd.pdf", b"f", app_properties={"document_id": "999999"})
    record(
        "drive.upload",
        entity_type="document",
        entity_id=doc.pk,
        object_id=objekt.pk,
        after={"drive_file_id": fremd_id, "to": drive.root_id, "name": "fremd.pdf"},
    )

    out = StringIO()
    call_command("drive_dubletten_bereinigen", stdout=out)
    text = out.getvalue()
    assert "bestaetigte Altkopien: 1" in text and "nicht bestaetigt: 1" in text and "Vorschau" in text
    assert f"Objekt {objekt.object_number}: 1" in text
    assert not [op for op in drive.ops if op[0] == "trash"]
    assert not drive.get(alt_id).trashed

    out = StringIO()
    call_command("drive_dubletten_bereinigen", "--echt", stdout=out)
    assert "1 Altkopien in den Papierkorb gelegt" in out.getvalue()
    assert drive.get(alt_id).trashed
    assert not drive.get(neu_id).trashed and not drive.get(fremd_id).trashed
    assert not [op for op in drive.ops if op[0] == "delete"]
    ereignis = AuditEvent.objects.get(action="drive.trash_duplicate_copy", entity_id=doc.pk)
    assert ereignis.after_state["drive_file_id"] == alt_id and ereignis.after_state["kept"] == neu_id

    out = StringIO()
    call_command("drive_dubletten_bereinigen", "--echt", "--objekt", objekt.object_number, stdout=out)
    text = out.getvalue()
    assert "bereits weg oder im Papierkorb: 1" in text and "0 Altkopien in den Papierkorb gelegt" in text
