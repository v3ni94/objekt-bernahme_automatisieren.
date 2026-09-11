"""Ohne Google-Verbindung wartet die Ablage, statt das Dokument nach drei Versuchen auf error zu setzen
(11.09.2026). Sobald die Verbindung steht, wird abgelegt."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.documents import ingest
from apps.drive import oauth
from apps.pipeline.models import JobStatus, JobType, ProcessingJob

from .conftest import page_lines

pytestmark = pytest.mark.django_db


def test_ablage_wartet_ohne_verbindung_und_holt_nach(objekt, stammdaten, pdf_factory, run_all, monkeypatch):
    adapter = oauth.get_adapter()  # Fake aus der Fixture, spaeter wieder eingesetzt
    monkeypatch.setattr(oauth, "get_adapter", lambda: None)
    pdf = pdf_factory("w.pdf", [page_lines("W", 1, 1)])
    doc, run = ingest.ingest_upload(objekt, filename="w.pdf", data=pdf.read_bytes())
    run_all(objekt)
    doc.refresh_from_db()
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.FILE_TO_DRIVE)
    assert doc.status in ("classified", "review"), doc.status
    assert job.status == JobStatus.PENDING and job.attempt_count == 0
    assert job.next_attempt_at is not None and job.next_attempt_at > timezone.now()
    assert job.last_error.startswith("wartet:")
    # Verbindung da: faellig stellen und ausfuehren
    monkeypatch.setattr(oauth, "get_adapter", lambda: adapter)
    ProcessingJob.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())
    run_all(objekt)
    doc.refresh_from_db()
    job.refresh_from_db()
    assert job.status == JobStatus.DONE and doc.drive_file_id
    assert doc.status in ("filed", "review")


def test_belegte_schreibsperre_ist_wartegrund_kein_fehlversuch(objekt, stammdaten, pdf_factory, run_all):
    """Zwei Dokumente desselben Objekts legen gleichzeitig ab: das zweite wartet, statt einen Versuch zu verbrauchen."""
    from django.core.cache import cache

    pdf = pdf_factory("s.pdf", [page_lines("S", 1, 1)])
    doc, run = ingest.ingest_upload(objekt, filename="s.pdf", data=pdf.read_bytes())
    run_all(objekt, job_types=[JobType.HASH, JobType.ANALYZE_PAGES, JobType.OCR_CHUNK, JobType.MERGE_PAGES])
    run_all(
        objekt,
        job_types=[JobType.RENDER_PREVIEWS, JobType.EXTRACT_ENTITIES, JobType.CLASSIFY, JobType.DECIDE],
    )
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.FILE_TO_DRIVE)
    cache.add(f"drive:write:{objekt.pk}", 999999, timeout=600)  # ein anderer Job haelt die Sperre
    try:
        run_all(objekt, job_types=[JobType.FILE_TO_DRIVE])
        job.refresh_from_db()
        assert job.status == JobStatus.PENDING and job.attempt_count == 0
        assert "Schreibsperre" in job.last_error and job.next_attempt_at > timezone.now()
    finally:
        cache.delete(f"drive:write:{objekt.pk}")
    ProcessingJob.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())
    run_all(objekt, job_types=[JobType.FILE_TO_DRIVE])
    job.refresh_from_db()
    doc.refresh_from_db()
    assert job.status == JobStatus.DONE and doc.drive_file_id


def test_ablage_wartet_ohne_zielstruktur(seeded, data_dir, drive, pdf_factory, run_all):
    """Objekt ohne Ordnerabgleich (Altbestand vor dem Objektordner): die Ablage wartet, statt nach drei Versuchen
    auf error zu laufen; nach dem Abgleich wird abgelegt."""
    from apps.objects.models import ManagedObject

    from .conftest import reconcile

    obj = ManagedObject.objects.create(
        object_number="777",
        city="Wartestadt",
        street="Weg",
        house_number="1",
        management_type="weg",
        is_test=True,
    )
    pdf = pdf_factory("z.pdf", [page_lines("Z", 1, 1)])
    doc, run = ingest.ingest_upload(obj, filename="z.pdf", data=pdf.read_bytes())
    run_all(obj)
    doc.refresh_from_db()
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.FILE_TO_DRIVE)
    assert doc.status != "error" and job.status == JobStatus.PENDING and job.attempt_count == 0
    assert job.last_error.startswith("wartet: Ablageziel noch nicht vorhanden")
    reconcile(obj, drive)
    ProcessingJob.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())
    run_all(obj)
    job.refresh_from_db()
    doc.refresh_from_db()
    assert job.status == JobStatus.DONE and doc.drive_file_id


def test_google_dokument_geht_ohne_download_in_die_pruefung(objekt, drive, run_all):
    """Google-Dokumente sind nicht herunterladbar; sie duerfen nicht als Fehler enden, sondern gehen in die Pruefung."""
    from apps.documents.models import Document
    from apps.drive import takeover
    from apps.review.models import ReviewCase

    ordner = drive.add_folder(drive.root_id, "Alt Google")
    gdoc = drive.add_file(ordner, "Protokoll", b"", mime_type="application/vnd.google-apps.document")
    folder = drive.get(ordner)
    result = takeover.register_files(
        objekt, takeover.collect_files(drive, folder, recursive=False, limit=50), source_folder=folder
    )
    assert result.registered == 1
    run_all(objekt)
    doc = Document.objects.get(drive_file_id=gdoc)
    assert doc.status == "review" and doc.sha256 is None
    assert ReviewCase.objects.filter(document=doc, case_subtype="unsupported_format").exists()
    assert not [op for op in drive.ops if op[0] == "download" and op[1] == gdoc]
