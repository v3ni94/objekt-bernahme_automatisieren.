"""Dokumente im Status error werden mit dem naechsten Verarbeitungslauf wieder aufgenommen (11.09.2026).

Hintergrund: Auf dem Server blieben nach einem Umgebungsfehler (fehlendes Datenbankrecht) vier Dokumente im
Status error liegen; ein neuer Lauf griff sie nicht mehr auf. Der Wiedereinstieg erfolgt so spaet wie moeglich.
"""

from __future__ import annotations

import pytest

from apps.documents import ingest
from apps.documents.models import Document, DocumentPage
from apps.pipeline.models import JobType, ProcessingJob
from apps.pipeline.runs import dispatch_run, restart_status, start_run
from apps.review.models import CaseStatus, ReviewCase

from .conftest import page_lines

pytestmark = pytest.mark.django_db


def _fehlgeschlagen(doc: Document, job_type: str) -> ReviewCase:
    doc.status = "error"
    doc.error_message = "OperationalError: (1142, 'DELETE command denied')"
    doc.save(update_fields=["status", "error_message", "updated_at"])
    return ReviewCase.objects.create(
        object=doc.object,
        case_type="unclear",
        case_subtype="job_failed",
        document=doc,
        batch_key=f"job_failed:test-{doc.pk}",
        context={"job_type": job_type},
    )


def test_wiedereinstieg_so_spaet_wie_moeglich(objekt, stammdaten, pdf_factory, run_all):
    pdf = pdf_factory("a.pdf", [page_lines("A", 1, 1)])
    doc, run = ingest.ingest_upload(objekt, filename="a.pdf", data=pdf.read_bytes())
    # ohne Hash: von vorn
    assert restart_status(doc) == "registered"
    run_all(objekt, job_types=[JobType.HASH])
    doc.refresh_from_db()
    assert doc.sha256 and restart_status(doc) == "hashed"
    run_all(objekt, job_types=[JobType.ANALYZE_PAGES, JobType.OCR_CHUNK, JobType.MERGE_PAGES])
    doc.refresh_from_db()
    assert DocumentPage.objects.filter(document=doc).exists()
    assert restart_status(doc) == "ocr_done"


def test_neuer_lauf_nimmt_fehlgeschlagene_dokumente_wieder_auf(objekt, stammdaten, pdf_factory, run_all):
    pdf = pdf_factory("b.pdf", [page_lines("B", 1, 1)])
    doc, run = ingest.ingest_upload(objekt, filename="b.pdf", data=pdf.read_bytes())
    run_all(objekt, job_types=[JobType.HASH])
    doc.refresh_from_db()
    fall = _fehlgeschlagen(doc, JobType.MERGE_PAGES)
    ProcessingJob.objects.filter(document=doc).update(status="failed")
    # Ein neuer Lauf greift das Dokument wieder auf und schliesst den Fall
    neuer = start_run(objekt, run_type="incremental")
    dispatch_run(neuer)
    doc.refresh_from_db()
    fall.refresh_from_db()
    assert doc.status == "hashed" and doc.error_message is None
    assert fall.status == CaseStatus.RESOLVED and fall.resolution["action"] == "reprocess"
    assert ProcessingJob.objects.filter(
        document=doc, job_type=JobType.ANALYZE_PAGES, status="pending"
    ).exists()
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status in ("classified", "review", "filed")
