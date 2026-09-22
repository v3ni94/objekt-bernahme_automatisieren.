"""Unterordner unter 06 nur aus dem Katalog; Wiederaufnahme von Fehlerdokumenten nach Objekt und Fehlerklasse."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.classification.confidence import Stage1Result
from apps.classification.decide import _misc_subfolder
from apps.documents.models import Document
from apps.objects.models import ManagedObject
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.pipeline.tasks import _known_subfolder
from apps.review.models import CaseStatus, CaseType, ReviewCase

pytestmark = pytest.mark.django_db


def test_unterordner_unter_06_nur_aus_dem_katalog(seeded):
    assert _misc_subfolder(Stage1Result(category="05", subfolder="13")) == "01"
    assert _misc_subfolder(Stage1Result(category="06", subfolder="04")) == "04"
    assert _misc_subfolder(Stage1Result(category="06", subfolder="09")) == "01"
    assert _misc_subfolder(Stage1Result()) == "01"
    assert _known_subfolder("06", "13") is None
    assert _known_subfolder("06", "02") == "02"
    assert _known_subfolder("06", None) is None


def _dok(obj, name, **kw):
    return Document.objects.create(
        object=obj,
        size_bytes=8,
        mime_type="application/pdf",
        original_name=name,
        current_name=name,
        source="paperless",
        status="error",
        first_seen_at=timezone.now(),
        **kw,
    )


def _fall(obj, doc, key, **kw):
    return ReviewCase.objects.create(
        object=obj, case_type=CaseType.UNCLEAR, case_subtype="job_failed", document=doc, batch_key=key, **kw
    )


def test_fehler_wiederaufnehmen_nach_objekt_und_klasse(seeded):
    obj = ManagedObject.objects.create(
        object_number="797", name="Wiederaufnahme", management_type="weg", is_test=True
    )
    a = _dok(obj, "a.pdf", sha256="a" * 64)
    b = _dok(obj, "b.pdf", sha256="b" * 64)
    now = timezone.now()
    ProcessingJob.objects.create(
        object=obj,
        document=a,
        job_type=JobType.MERGE_PAGES,
        idempotency_key="wa-a",
        status=JobStatus.FAILED,
        error_class="OperationalError",
        finished_at=now,
    )
    ProcessingJob.objects.create(
        object=obj,
        document=b,
        job_type=JobType.ANALYZE_PAGES,
        idempotency_key="wa-b",
        status=JobStatus.FAILED,
        error_class="PdfiumError",
        finished_at=now,
    )
    for i in range(3):  # RESET_LIMIT erreicht: der automatische Wiederanlauf liesse a stehen
        _fall(
            obj,
            a,
            f"job_failed:wa-{i}",
            status=CaseStatus.RESOLVED,
            resolution={"action": "reprocess", "run_id": 1},
        )
    offen = _fall(obj, a, "job_failed:wa-offen")

    out = StringIO()
    call_command(
        "fehler_wiederaufnehmen", "--objekt", "797", "--fehlerklasse", "OperationalError", stdout=out
    )
    a.refresh_from_db()
    assert a.status == "error" and "Vorschau: 1" in out.getvalue()

    out = StringIO()
    call_command(
        "fehler_wiederaufnehmen",
        "--objekt",
        "797",
        "--fehlerklasse",
        "OperationalError",
        "--echt",
        stdout=out,
    )
    a.refresh_from_db()
    b.refresh_from_db()
    offen.refresh_from_db()
    assert a.status == "hashed"  # Hash vorhanden, keine Seiten: Wiedereinstieg bei merge_pages
    assert b.status == "error"  # andere Fehlerklasse bleibt unberuehrt
    assert offen.status == CaseStatus.RESOLVED and offen.resolution["action"] == "reprocess_manual"
    assert "Wieder aufgenommen: 1" in out.getvalue()


def test_fehler_wiederaufnehmen_braucht_auswahl():
    with pytest.raises(CommandError):
        call_command("fehler_wiederaufnehmen")
