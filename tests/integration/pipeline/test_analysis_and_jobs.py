"""Seitenanalyse (Kriterium E 1.3, A-11), Formatweiche, Jobreservierung, Wiederholung, verwaiste Arbeitsverzeichnisse,
Listenerkennung B-34 und Oberflaeche (Upload, Liste, Dokument, Seitenbild, Sweeper)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from apps.documents import ingest
from apps.documents.models import Document
from apps.pipeline import analysis, storage
from apps.pipeline.jobs import RetryableError, enqueue, fail, idempotency_key, reserve
from apps.pipeline.models import JobStatus, JobType, ProcessingJob, RunStatus
from apps.pipeline.tasks import remove_orphan_work_dirs
from apps.review.models import ReviewCase

from .conftest import page_lines

pytestmark = pytest.mark.django_db
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "imports"


def _mixed_pdf(path: Path) -> Path:
    """Seite 1 Deckblatt nur Bild, Seite 2 Alt-OCR-Zeichensalat, Seite 3 digital."""
    img = path.with_suffix(".png")
    Image.new("L", (1240, 1754), 255).save(img)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.drawImage(str(img), 0, 0, width=w, height=h)
    c.showPage()
    c.setFont("Helvetica", 10)
    y = h - 50
    for _ in range(30):
        c.drawString(40, y, "§$%&/()=?`´^°!\"'#+*~<>|,;.:-_ §$%&/()=?`´^°!\"'#+*~<>|,;.:-_ §$%&/()=?")
        y -= 14
    c.showPage()
    c.setFont("Helvetica", 10)
    y = h - 50
    for line in page_lines(
        "DIGITAL", 3, 3, extra=["Hausgeld Abrechnungsjahr 2024 Nachzahlung 1.234,56 EUR am 12.03.2025"]
    ):
        c.drawString(40, y, line)
        y -= 14
    c.showPage()
    c.save()
    img.unlink()
    return path


def test_textebenen_vorpruefung_deckblatt_zeichensalat_digital(tmp_path):
    pdf = _mixed_pdf(tmp_path / "mixed.pdf")
    result = analysis.analyze_pdf(pdf, min_chars=50, alnum_ratio=0.6, cover_ratio=0.9, chunk_pages=20)
    assert result.page_count == 3
    reasons = {p.page_no: p.reason for p in result.pages}
    assert reasons[1] in ("too_few_chars", "full_page_image")
    assert reasons[2] == "garbage_text_layer"
    assert reasons[3] == "text_layer"
    assert result.digital_pages == [3] and result.ocr_pages == [1, 2]
    assert result.chunks == [[1, 2]] and result.origin_kind == "mixed"
    assert result.pages[0].image_cover >= 0.9
    assert 3 in result.texts and "DIGITAL" in result.texts[3]
    assert "texts" not in result.to_json()


def test_formatweiche(tmp_path):
    assert analysis.detect_kind(Path("a.pdf"), None) == "pdf"
    assert analysis.detect_kind(Path("a.PDF"), "application/octet-stream") == "pdf"
    assert analysis.detect_kind(Path("a.jpg"), None) == "image"
    assert analysis.detect_kind(Path("a.docx"), None) == "office"
    assert analysis.detect_kind(Path("a"), "application/vnd.google-apps.document") == "google_doc"
    assert analysis.detect_kind(Path("a.zip"), None) == "unsupported"
    img = tmp_path / "scan.png"
    Image.new("RGB", (600, 800), (255, 255, 255)).save(img)
    result = analysis.analyze_file(
        img, "image/png", min_chars=50, alnum_ratio=0.6, cover_ratio=0.9, chunk_pages=20, work=tmp_path
    )
    assert result.kind == "image" and result.page_count == 1 and result.ocr_pages == [1]
    assert Path(result.pdf_path).exists()
    csv = tmp_path / "t.csv"
    csv.write_text("a;b\n1;2\n", encoding="utf-8")
    result = analysis.analyze_file(
        csv, "text/csv", min_chars=50, alnum_ratio=0.6, cover_ratio=0.9, chunk_pages=20, work=tmp_path
    )
    assert result.kind == "office" and result.digital_pages == [1] and "1\t2" in result.texts[1]


def test_reservierung_idempotenz_und_wiederholung(objekt, stammdaten):
    key = idempotency_key(JobType.HASH, objekt.pk, "datei-1")
    assert key == f"hash:{objekt.pk}:datei-1"
    job, created = enqueue(JobType.HASH, objekt, key=key)
    job2, created2 = enqueue(JobType.HASH, objekt, key=key)
    assert created and not created2 and job.pk == job2.pk
    assert ProcessingJob.objects.filter(idempotency_key=key).count() == 1
    reserved = reserve(job.pk)
    assert reserved is not None and reserved.status == JobStatus.RUNNING and reserved.attempt_count == 1
    assert reserve(job.pk) is None  # doppelt zugestellte Nachricht
    fail(reserved, RetryableError("Netz"), retryable=True)
    reserved.refresh_from_db()
    assert reserved.status == JobStatus.PENDING and reserved.next_attempt_at is not None
    assert reserve(job.pk) is None  # noch nicht faellig
    ProcessingJob.objects.filter(pk=job.pk).update(next_attempt_at=None)
    again = reserve(job.pk)
    assert again is not None and again.attempt_count == 2
    fail(again, ValueError("kaputt"), retryable=False)
    again.refresh_from_db()
    assert again.status == JobStatus.FAILED and again.error_class == "ValueError"
    assert ReviewCase.objects.filter(batch_key=f"job_failed:{job.pk}", case_subtype="job_failed").count() == 1
    events = list(job.events.order_by("id").values_list("to_status", flat=True))
    assert events == ["pending", "running", "pending", "running", "failed"]


def test_verwaiste_arbeitsverzeichnisse(objekt, stammdaten, data_dir):
    old = time.time() - 72 * 3600
    tmp = data_dir / "work" / "tmp-alt"
    tmp.mkdir(parents=True)
    os.utime(tmp, (old, old))
    orphan = data_dir / "work" / ("a" * 64)
    orphan.mkdir()
    os.utime(orphan, (old, old))
    fresh = data_dir / "work" / "tmp-neu"
    fresh.mkdir()
    kept = data_dir / "work" / ("b" * 64)
    kept.mkdir()
    os.utime(kept, (old, old))
    Document.objects.create(
        object=objekt,
        sha256="b" * 64,
        size_bytes=1,
        mime_type="application/pdf",
        original_name="x.pdf",
        current_name="x.pdf",
        source="upload",
        status="hashed",
        first_seen_at="2026-01-01T00:00:00Z",
    )
    assert remove_orphan_work_dirs() == 2
    assert not tmp.exists() and not orphan.exists() and fresh.exists() and kept.exists()


def test_listenerkennung_erzeugt_import_candidate(objekt, stammdaten, run_all):
    data = (FIXTURES / "eigentuemerliste_generic.xlsx").read_bytes()
    doc, _run = ingest.ingest_upload(objekt, filename="Eigentuemer.xlsx", data=data)
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "ocr_done" and doc.origin_kind == "digital"
    case = ReviewCase.objects.get(document=doc, case_type="import_candidate")
    assert case.context["profile"] in ("xlsx_generic", "generic_table")
    assert len(case.context["targets"]) >= 3


def test_nicht_unterstuetztes_format_geht_in_pruefung(objekt, stammdaten, run_all, tmp_path):
    (tmp_path / "x.txt").write_text("nur Text", encoding="utf-8")
    with pytest.raises(ingest.IngestError):
        ingest.register_upload(objekt, filename="archiv.zip", data=b"PK\x03\x04")
    with pytest.raises(ingest.IngestError):
        ingest.register_upload(objekt, filename="leer.pdf", data=b"")
    doc, _ = ingest.ingest_upload(objekt, filename="notiz.txt", data=b"Kurze Notiz ohne Bankverbindung")
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "ocr_done" and doc.page_count == 1


def test_oberflaeche_upload_liste_dokument_seitenbild_sweeper(
    objekt, stammdaten, pdf_factory, run_all, client_as, admin_user, clerk_user
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client = client_as(admin_user)
    pdf = pdf_factory("upload.pdf", [page_lines("UPLOAD", 1, 1)])
    resp = client.get(f"/objekte/{objekt.pk}/dokumente/hochladen/")
    assert resp.status_code == 200
    resp = client.post(
        f"/objekte/{objekt.pk}/dokumente/hochladen/",
        {
            "files": [
                SimpleUploadedFile("upload.pdf", pdf.read_bytes(), "application/pdf"),
                SimpleUploadedFile("b.zip", b"PK"),
            ]
        },
    )
    assert resp.status_code == 302
    doc = Document.objects.get(object=objekt)
    assert doc.status == "registered" and doc.source == "upload"
    run = doc.object.processing_runs.get()
    assert run.status == RunStatus.RUNNING and run.run_type == "incremental"
    resp = client.get(f"/objekte/{objekt.pk}/dokumente/")
    assert resp.status_code == 200 and "upload.pdf" in resp.content.decode()
    resp = client.get(f"/dokumente/{doc.pk}/seite/1.jpg")
    assert resp.status_code == 404
    run_all(objekt)
    resp = client.get(f"/dokumente/{doc.pk}/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "UPLOAD" in html and "Seitentexte (maskiert)" in html
    resp = client.get(f"/dokumente/{doc.pk}/seite/1.jpg")
    assert resp.status_code == 200 and resp["Content-Type"] == "image/jpeg"
    # Zweiter Lauf: bereits laufender oder wartender Lauf wird gemeldet, kein zweiter angelegt
    resp = client.post(f"/objekte/{objekt.pk}/verarbeitung/starten/", {"run_type": "full"})
    assert resp.status_code == 302
    assert objekt.processing_runs.count() == 2  # erster Lauf ist fertig, zweiter wurde angelegt
    resp = client.post(f"/objekte/{objekt.pk}/verarbeitung/starten/", {"run_type": "full"})
    assert objekt.processing_runs.count() == 2
    # Statusseite mit Verarbeitung und Sweeper
    resp = client.get("/status/")
    assert resp.status_code == 200 and "Verarbeitung je Objekt" in resp.content.decode()
    resp = client.post("/status/sweeper/")
    assert resp.status_code == 302
    # Sachbearbeiter ohne owner_files.read darf Dokumente der Eigentuemerakte nicht sehen
    Document.objects.filter(pk=doc.pk).update(category_id="05")
    client = client_as(clerk_user)
    from apps.accounts.permissions import user_has_permission

    resp = client.get(f"/dokumente/{doc.pk}/")
    expected = 200 if user_has_permission(clerk_user, "owner_files.read") else 403
    assert resp.status_code == expected


def test_arbeitsverzeichnis_fehlt_fuehrt_zu_erneutem_download(objekt, stammdaten, pdf_factory, run_all):
    pdf = pdf_factory("a.pdf", [page_lines("A", 1, 1)])
    doc, _ = ingest.ingest_upload(objekt, filename="a.pdf", data=pdf.read_bytes())
    run_all(objekt, job_types=[JobType.HASH])
    doc.refresh_from_db()
    assert doc.status == "hashed"
    shutil.rmtree(storage.work_dir(doc.sha256))
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "ocr_done"
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.HASH).count() == 2
