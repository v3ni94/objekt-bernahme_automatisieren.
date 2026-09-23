"""Dokumente, die ohne offenen Job in den Fruehstadien haengen blieben (Befund 23.09.2026, 71 Dokumente):
ein frueher fehlgeschlagener OCR-Block darf das Zusammenfuehren nicht blockieren, und eine zu grosse Bestandsdatei geht
in die Pruefung statt bei jedem Lauf erneut einen discover-Job zu bekommen."""

from __future__ import annotations

import pytest

from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document, DocumentPage
from apps.parties.services import _hmac_key
from apps.pipeline import ocr as ocr_mod
from apps.pipeline.jobs import enqueue
from apps.pipeline.models import JobStatus, JobType, ProcessingJob, ProcessingRun, RunStatus, RunType
from apps.pipeline.runs import dispatch_run
from apps.review.models import ReviewCase

from .conftest import page_lines

pytestmark = pytest.mark.django_db


def test_alter_fehlgeschlagener_block_blockiert_das_zusammenfuehren_nicht(
    objekt, stammdaten, pdf_factory, run_all, fake_ocr, admin_user
):
    store.set("ocr.chunk_pages", 2, user=admin_user)
    pdf = pdf_factory("scan.pdf", [page_lines(m, i + 1, 4) for i, m in enumerate("ABCD")], scan=True)
    doc, _run = ingest.ingest_upload(objekt, filename="scan.pdf", data=pdf.read_bytes())
    run_all(objekt, job_types=[JobType.HASH, JobType.ANALYZE_PAGES])
    doc.refresh_from_db()
    chunks = list(ProcessingJob.objects.filter(document=doc, job_type=JobType.OCR_CHUNK).order_by("id"))
    assert len(chunks) == 2
    # Block 1 scheitert endgueltig (Zeitueberschreitung), Block 2 laeuft durch: ohne die Seiten 1 und 2 darf kein
    # Zusammenfuehren eingereiht werden
    ProcessingJob.objects.filter(pk=chunks[0].pk).update(
        status=JobStatus.FAILED, error_class="TimeoutExpired"
    )
    assert run_all(objekt, job_types=[JobType.OCR_CHUNK]) == 1
    assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.MERGE_PAGES).exists()
    doc.refresh_from_db()
    assert doc.status == "hashed"

    # Wiederaufnahme: ein neuer Block 1 (Wiederholungsjob) erkennt die fehlenden Seiten; der alte fehlgeschlagene
    # Block gilt nicht mehr als offen, alle Seiten liegen vor, das Zusammenfuehren folgt
    enqueue(
        JobType.OCR_CHUNK,
        objekt,
        key=f"ocr:{objekt.pk}:{doc.sha256}:1",
        document=doc,
        payload=chunks[0].payload,
    )
    assert run_all(objekt, job_types=[JobType.OCR_CHUNK]) == 1
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.MERGE_PAGES).exists()
    assert ProcessingJob.objects.get(pk=chunks[0].pk).status == JobStatus.FAILED  # Historie bleibt
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status in ("ocr_done", "review", "classified", "filed")
    assert DocumentPage.objects.filter(document=doc).count() == 4
    assert sorted(c["pages"] for c in fake_ocr) == [[1, 2], [3, 4]]


def test_letzter_block_ohne_alle_seiten_reiht_kein_zusammenfuehren_ein(
    objekt, stammdaten, pdf_factory, run_all, fake_ocr, admin_user, monkeypatch
):
    """Ist ein Seitentext trotz erledigter Bloecke nicht im Cache (etwa geraeumt), wird nicht zusammengefuehrt;
    das Zusammenfuehren wuerde sonst mit „Seitentexte fehlen“ scheitern."""
    store.set("ocr.chunk_pages", 2, user=admin_user)
    pdf = pdf_factory("scan.pdf", [page_lines(m, i + 1, 4) for i, m in enumerate("WXYZ")], scan=True)
    doc, _run = ingest.ingest_upload(objekt, filename="scan.pdf", data=pdf.read_bytes())
    run_all(objekt, job_types=[JobType.HASH, JobType.ANALYZE_PAGES])
    doc.refresh_from_db()
    # Block 1 wird als erledigt markiert, ohne dass seine Seiten im Cache liegen
    erster = ProcessingJob.objects.filter(document=doc, job_type=JobType.OCR_CHUNK).order_by("id").first()
    ProcessingJob.objects.filter(pk=erster.pk).update(status=JobStatus.DONE)
    assert run_all(objekt, job_types=[JobType.OCR_CHUNK]) == 1
    assert not ProcessingJob.objects.filter(document=doc, job_type=JobType.MERGE_PAGES).exists()
    # Seiten 1 und 2 nachgeliefert (etwa durch einen Wiederholungsblock), dann folgt das Zusammenfuehren
    for p in (1, 2):
        ocr_mod.mask_and_cache(doc.sha256, p, f"Seite {p} nachgeliefert", source="ocr", hmac_key=_hmac_key())
    enqueue(
        JobType.OCR_CHUNK, objekt, key=f"ocr:{objekt.pk}:{doc.sha256}:1", document=doc, payload=erster.payload
    )
    assert run_all(objekt, job_types=[JobType.OCR_CHUNK]) == 1
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.MERGE_PAGES).exists()


def test_zu_grosse_bestandsdatei_geht_in_die_pruefung_ohne_wiederholung(objekt, drive, run_all):
    from apps.drive import takeover

    ordner = drive.add_folder(drive.root_id, "Alt Archiv")
    fid = drive.add_file(ordner, "archiv.zip", b"x", mime_type="application/zip")
    folder = drive.get(ordner)
    result = takeover.register_files(
        objekt, takeover.collect_files(drive, folder, recursive=False, limit=50), source_folder=folder
    )
    assert result.registered == 1
    doc = Document.objects.get(drive_file_id=fid)
    Document.objects.filter(pk=doc.pk).update(size_bytes=600 * 1024 * 1024)
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "review"
    assert ReviewCase.objects.filter(document=doc, case_subtype="file_too_large", status="open").count() == 1
    discover_vorher = ProcessingJob.objects.filter(document=doc, job_type=JobType.DISCOVER).count()
    assert not [op for op in drive.ops if op[0] == "download"]

    # Ein weiterer Lauf legt fuer das Dokument keinen Startjob mehr an
    lauf = ProcessingRun.objects.create(object=objekt, run_type=RunType.INCREMENTAL, status=RunStatus.RUNNING)
    assert dispatch_run(lauf) == 0
    run_all(objekt)
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.DISCOVER).count() == discover_vorher
    assert ReviewCase.objects.filter(document=doc, case_subtype="file_too_large").count() == 1
