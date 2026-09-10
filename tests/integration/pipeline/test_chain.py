"""Pipeline-Kette (Umsetzungsplan 2.7, Tests CR 14): digitale PDF ohne OCR-Job, Chunking und Merge in Originalreihenfolge,
Dublette vor der OCR (T29), Abbruch und Wiederaufnahme ohne doppelte Seiten, Objektsperre, Statistikzeile."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document, DocumentEntity, DocumentPage
from apps.pipeline import storage
from apps.pipeline.jobs import sweep_stale_jobs
from apps.pipeline.models import JobStatus, JobType, ObjectProgress, ProcessingJob, ProcessingRun, RunStatus
from apps.pipeline.progress import refresh_progress
from apps.pipeline.runs import maybe_finish_run, queue_position, start_run
from apps.review.models import ReviewCase

from .conftest import page_lines

pytestmark = pytest.mark.django_db


def upload(objekt, path, name=None):
    doc, run = ingest.ingest_upload(objekt, filename=name or path.name, data=path.read_bytes())
    return doc, run


def test_digitale_pdf_ohne_ocr_job_bis_ocr_done(objekt, stammdaten, pdf_factory, run_all):
    pdf = pdf_factory(
        "digital.pdf", [page_lines(m, i + 1, 3) for i, m in enumerate(["ALPHA", "BRAVO", "CHARLIE"])]
    )
    doc, run = upload(objekt, pdf)
    assert run.status == RunStatus.RUNNING
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "ocr_done"
    assert doc.sha256 and doc.page_count == 3 and doc.origin_kind == "digital"
    pages = list(DocumentPage.objects.filter(document=doc).order_by("page_no"))
    assert [p.page_no for p in pages] == [1, 2, 3]
    assert all(p.text_source == "text_layer" and not p.is_scan for p in pages)
    assert "ALPHA" in pages[0].text_content and "CHARLIE" in pages[2].text_content
    types = set(ProcessingJob.objects.filter(document=doc).values_list("job_type", flat=True))
    assert JobType.OCR_CHUNK not in types
    assert {
        JobType.HASH,
        JobType.ANALYZE_PAGES,
        JobType.MERGE_PAGES,
        JobType.RENDER_PREVIEWS,
        JobType.EXTRACT_ENTITIES,
    } <= types
    assert not ProcessingJob.objects.filter(object=objekt).exclude(status=JobStatus.DONE).exists()
    assert all(storage.previews_dir(doc.pk).joinpath(f"{n:04d}.jpg").exists() for n in (1, 2, 3))
    assert storage.analysis_path(doc.sha256).exists()
    run.refresh_from_db()
    assert run.status == RunStatus.DONE and run.documents_total == 1 and run.documents_done == 1
    assert run.pages_done == 3
    # Entitaeten: eigene Adresse als Objektmarker
    assert DocumentEntity.objects.filter(
        document=doc, entity_type="object_number", value_normalized="own"
    ).exists()


def test_chunking_und_merge_in_originalreihenfolge(
    objekt, stammdaten, pdf_factory, run_all, fake_ocr, admin_user
):
    store.set("ocr.chunk_pages", 2, user=admin_user)
    markers = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO"]
    pdf = pdf_factory("scan.pdf", [page_lines(m, i + 1, 5) for i, m in enumerate(markers)], scan=True)
    doc, _run = upload(objekt, pdf)
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "ocr_done" and doc.origin_kind == "scan" and doc.page_count == 5
    chunks = ProcessingJob.objects.filter(document=doc, job_type=JobType.OCR_CHUNK).order_by("id")
    assert [c.idempotency_key for c in chunks] == [f"ocr:{objekt.pk}:{doc.sha256}:{n}" for n in (1, 2, 3)]
    assert [c.payload["pages"] for c in chunks] == [[1, 2], [3, 4], [5]]
    assert sorted(c["chunk_no"] for c in fake_ocr) == [1, 2, 3]
    pages = list(DocumentPage.objects.filter(document=doc).order_by("page_no"))
    assert [p.page_no for p in pages] == [1, 2, 3, 4, 5]
    for p in pages:
        assert f"FAKE-OCR Seite {p.page_no} " in p.text_content
        assert p.text_source == "ocr" and p.is_scan and p.ocr_language == "deu"
        assert "0532" not in p.text_content and "[IBAN" in p.text_content  # maskiert
    assert ProcessingJob.objects.filter(document=doc, job_type=JobType.MERGE_PAGES).count() == 1
    # IBAN aus der Maskierung gleich Stammdaten-IBAN, Zuordnung ueber den HMAC
    iban = DocumentEntity.objects.filter(document=doc, entity_type="iban").first()
    assert iban is not None and iban.iban_hash == stammdaten["owner"].iban_hash.hex()
    assert iban.matched_owner_id == stammdaten["owner"].pk and iban.iban_last4 == "3000"


def test_dublette_wird_vor_der_ocr_erkannt_t29(objekt, stammdaten, pdf_factory, run_all, fake_ocr):
    pdf = pdf_factory("scan.pdf", [page_lines("ALPHA", 1, 1)], scan=True)
    first, _ = upload(objekt, pdf, name="erste.pdf")
    second, _ = upload(objekt, pdf, name="zweite.pdf")
    run_all(objekt)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == "ocr_done"
    assert second.status == "duplicate" and second.duplicate_of_id == first.pk
    assert not ProcessingJob.objects.filter(
        document=second, job_type__in=[JobType.ANALYZE_PAGES, JobType.OCR_CHUNK]
    ).exists()
    case = ReviewCase.objects.get(document=second)
    assert case.case_type == "unclear" and case.case_subtype == "duplicate"
    assert case.misc_subfolder.code == "03" and case.misc_subfolder.category_id == "06"
    assert case.context["original_document_id"] == first.pk
    with connection.cursor() as cur:
        cur.execute(
            "SELECT sha256, COUNT(*) FROM documents WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING COUNT(*) > 1"
        )
        assert not cur.fetchall()
    assert len(fake_ocr) == 1


def test_abbruch_und_wiederaufnahme_ohne_doppelte_seiten(
    objekt, stammdaten, pdf_factory, run_all, fake_ocr, admin_user
):
    store.set("ocr.chunk_pages", 2, user=admin_user)
    pdf = pdf_factory("scan.pdf", [page_lines(m, i + 1, 4) for i, m in enumerate("ABCD")], scan=True)
    doc, _ = upload(objekt, pdf)
    run_all(objekt, job_types=[JobType.HASH, JobType.ANALYZE_PAGES])
    doc.refresh_from_db()
    chunks = list(ProcessingJob.objects.filter(document=doc, job_type=JobType.OCR_CHUNK).order_by("id"))
    assert len(chunks) == 2
    # Worker wird waehrend Block 1 hart beendet: Job bleibt running, erste Seite liegt schon im Cache, Heartbeat veraltet
    from apps.parties.services import _hmac_key
    from apps.pipeline import ocr as ocr_mod

    ocr_mod.mask_and_cache(
        doc.sha256, 1, "FAKE-OCR Seite 1 Block 1 (vor Abbruch)", source="ocr", hmac_key=_hmac_key()
    )
    stale = timezone.now() - timedelta(minutes=120)
    ProcessingJob.objects.filter(pk=chunks[0].pk).update(
        status=JobStatus.RUNNING,
        locked_by="worker@tot:1",
        locked_at=stale,
        heartbeat_at=stale,
        attempt_count=1,
    )
    # Ohne Sweeper bleibt der Job liegen (nicht reservierbar)
    assert run_all(objekt, job_types=[JobType.OCR_CHUNK]) == 1  # nur Block 2 laeuft
    assert ProcessingJob.objects.get(pk=chunks[0].pk).status == JobStatus.RUNNING
    doc.refresh_from_db()
    assert doc.status == "hashed"  # Merge noch nicht ausgeloest, weil Block 1 offen ist
    result = sweep_stale_jobs()
    assert result == {"reset": 1, "failed": 0}
    j = ProcessingJob.objects.get(pk=chunks[0].pk)
    assert j.status == JobStatus.PENDING and j.locked_by is None
    run_all(objekt)
    j.refresh_from_db()
    assert j.status == JobStatus.DONE and j.attempt_count == 2
    doc.refresh_from_db()
    assert doc.status == "ocr_done"
    assert DocumentPage.objects.filter(document=doc).count() == 4
    with connection.cursor() as cur:
        cur.execute(
            "SELECT document_id, page_no, COUNT(*) FROM document_pages GROUP BY document_id, page_no HAVING COUNT(*) > 1"
        )
        assert not cur.fetchall()
    # Die vor dem Abbruch gesicherte Seite 1 wurde wiederverwendet, nur Seite 2 erneut erkannt
    assert [c["pages"] for c in fake_ocr] == [[3, 4], [2]]
    assert "vor Abbruch" in DocumentPage.objects.get(document=doc, page_no=1).text_content


def test_sweeper_setzt_nach_max_attempts_auf_failed_mit_review_fall(objekt, stammdaten, pdf_factory, run_all):
    pdf = pdf_factory("d.pdf", [page_lines("A", 1, 1)])
    doc, _ = upload(objekt, pdf)
    job = ProcessingJob.objects.get(document=doc, job_type=JobType.HASH)
    stale = timezone.now() - timedelta(hours=3)
    ProcessingJob.objects.filter(pk=job.pk).update(
        status=JobStatus.RUNNING, heartbeat_at=stale, locked_at=stale, attempt_count=job.max_attempts
    )
    assert sweep_stale_jobs() == {"reset": 0, "failed": 1}
    job.refresh_from_db()
    doc.refresh_from_db()
    assert job.status == JobStatus.FAILED and doc.status == "error"
    case = ReviewCase.objects.get(batch_key=f"job_failed:{job.pk}")
    assert case.case_type == "unclear" and case.case_subtype == "job_failed"
    assert sweep_stale_jobs() == {"reset": 0, "failed": 0}
    assert ReviewCase.objects.filter(batch_key=f"job_failed:{job.pk}").count() == 1


def test_objektsperre_zweites_objekt_wartet_pending(objekt, stammdaten, pdf_factory, run_all):
    other = stammdaten["other"]
    pdf1 = pdf_factory("a.pdf", [page_lines("A", 1, 1)])
    pdf2 = pdf_factory("b.pdf", [page_lines("B", 1, 1)])
    ingest.register_upload(objekt, filename="a.pdf", data=pdf1.read_bytes())
    ingest.register_upload(other, filename="b.pdf", data=pdf2.read_bytes())
    run1 = start_run(objekt)
    run2 = start_run(other)
    assert run1.status == RunStatus.RUNNING
    assert run2.status == RunStatus.PENDING and queue_position(run2) == 1
    assert queue_position(run1) is None
    # Jobs des zweiten Objekts sind noch nicht eingereiht
    assert not ProcessingJob.objects.filter(object=other).exists()
    run_all(objekt)
    run1.refresh_from_db()
    run2.refresh_from_db()
    assert run1.status == RunStatus.DONE
    assert run2.status == RunStatus.RUNNING  # Laufabschluss startet den Wartenden
    run_all(other)
    run2.refresh_from_db()
    assert run2.status == RunStatus.DONE
    assert Document.objects.get(object=other).status == "ocr_done"


def test_statistikzeile_stimmt_mit_zaehlabfrage_ueberein(objekt, stammdaten, pdf_factory, run_all):
    pdf_a = pdf_factory("a.pdf", [page_lines("A", i + 1, 2) for i in range(2)])
    pdf_b = pdf_factory("b.pdf", [page_lines("B", 1, 1)])
    upload(objekt, pdf_a, "a.pdf")
    upload(objekt, pdf_a, "a-kopie.pdf")
    upload(objekt, pdf_b, "b.pdf")
    run_all(objekt)
    row = refresh_progress(objekt)
    docs = Document.objects.filter(object=objekt, deleted_at__isnull=True)
    assert row.documents_total == docs.count() == 3
    assert row.documents_duplicate == docs.filter(status="duplicate").count() == 1
    assert row.documents_ocr_done == docs.filter(status="ocr_done").count() == 2
    assert row.documents_error == 0
    assert row.pages_done == DocumentPage.objects.filter(document__object=objekt).count() == 3
    assert row.pages_total == 3
    assert row.review_open == ReviewCase.objects.filter(object=objekt, status="open").count() == 1
    assert ObjectProgress.objects.filter(object=objekt).count() == 1
    run = ProcessingRun.objects.get(object=objekt)
    assert run.status == RunStatus.DONE
    assert run.documents_total == 3 and run.documents_skipped == 1 and run.documents_done == 2
    assert run.review_cases_created == 1


def test_analyse_json_ohne_klartext_und_cache_maskiert(objekt, stammdaten, pdf_factory, run_all):
    pdf = pdf_factory(
        "iban.pdf",
        [
            page_lines(
                "IBAN",
                1,
                1,
                extra=["Kontoinhaber Erika Mustermann", "IBAN: DE89 3704 0044 0532 0130 00 BIC: COBADEFFXXX"],
            )
        ],
    )
    doc, _ = upload(objekt, pdf)
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "ocr_done"
    page = DocumentPage.objects.get(document=doc)
    assert "0532" not in page.text_content and "DE89" not in page.text_content
    assert page.masked_entities_count >= 1
    cache_text = storage.page_text_path(doc.sha256, 1).read_text(encoding="utf-8")
    assert "0532" not in cache_text
    hits = json.loads(storage.page_hits_path(doc.sha256, 1).read_text(encoding="utf-8"))
    assert all("0532013000" not in json.dumps(h) for h in hits)
    assert not DocumentEntity.objects.filter(document=doc, value_text__contains="0532").exists()
    iban = DocumentEntity.objects.get(document=doc, entity_type="iban")
    assert (
        iban.iban_hash == stammdaten["owner"].iban_hash.hex()
        and iban.matched_owner_id == stammdaten["owner"].pk
    )
    analysis = json.loads(storage.analysis_path(doc.sha256).read_text(encoding="utf-8"))
    assert "texts" not in analysis and analysis["page_count"] == 1
    # Person ueber den Kontext "Kontoinhaber" erkannt und der Eigentuemerin zugeordnet
    person = DocumentEntity.objects.filter(
        document=doc, entity_type="person_name", matched_owner=stammdaten["owner"]
    )
    assert person.exists()


def test_maybe_finish_run_wartet_auf_offene_jobs(objekt, stammdaten, pdf_factory):
    pdf = pdf_factory("a.pdf", [page_lines("A", 1, 1)])
    _doc, run = upload(objekt, pdf)
    assert maybe_finish_run(run) is False
    run.refresh_from_db()
    assert run.status == RunStatus.RUNNING
