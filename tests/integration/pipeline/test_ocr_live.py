"""Echte OCR mit Tesseract und ocrmypdf (uebersprungen, wenn die Werkzeuge fehlen): Scan-PDF mit drei Seiten in zwei
Bloecken, Seitentexte in Originalreihenfolge, Maskierung der IBAN im Sidecar-Text."""

from __future__ import annotations

import pytest

from apps.config import store
from apps.documents import ingest
from apps.documents.models import DocumentEntity, DocumentPage
from apps.pipeline import ocr as ocr_mod
from apps.pipeline.models import JobType, ProcessingJob

from .conftest import TEST_IBAN, page_lines

pytestmark = [pytest.mark.django_db, pytest.mark.slow]


@pytest.mark.skipif(not ocr_mod.tools_available(), reason="Tesseract oder Ghostscript fehlt")
def test_echte_ocr_scan_pdf(objekt, stammdaten, pdf_factory, run_all, admin_user):
    store.set("ocr.chunk_pages", 2, user=admin_user)
    markers = ["ALPHA", "BRAVO", "CHARLIE"]
    pdf = pdf_factory(
        "scan.pdf",
        [
            page_lines(m, i + 1, 3, extra=[f"IBAN {TEST_IBAN}"] if i == 1 else None)
            for i, m in enumerate(markers)
        ],
        scan=True,
    )
    doc, _run = ingest.ingest_upload(objekt, filename="scan.pdf", data=pdf.read_bytes())
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status in ("review", "classified", "filed"), ProcessingJob.objects.filter(
        document=doc
    ).values_list("job_type", "status", "last_error")
    pages = list(DocumentPage.objects.filter(document=doc).order_by("page_no"))
    assert len(pages) == 3
    for p, marker in zip(pages, markers, strict=True):
        assert marker in p.text_content.upper(), p.text_content
        assert p.text_source == "ocr" and p.ocr_engine == "tesseract"
        assert "0532" not in p.text_content
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.OCR_CHUNK).count() == 2
    iban = DocumentEntity.objects.filter(document=doc, entity_type="iban")
    if iban.exists():  # OCR kann Ziffern verfehlen; wenn erkannt, dann nur als Hash
        assert iban.first().iban_hash == stammdaten["owner"].iban_hash.hex()
