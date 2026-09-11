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
