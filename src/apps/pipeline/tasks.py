"""Pipeline-Tasks (Fachentwurf E 1.2, docs/architektur.md 6.1): discover, hash, analyze_pages, ocr_chunk, merge_pages,
render_previews, extract_entities, sweep. Jeder Task prueft den Zustand in der Datenbank, arbeitet, schreibt den
Folgezustand und reiht den naechsten Job ein. Klassifikation (classify, classify_ai, decide, file_to_drive) folgt in M6."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.config import store
from apps.documents.models import Document, DocumentEntity, DocumentPage
from apps.pipeline import analysis as analysis_mod
from apps.pipeline import ocr as ocr_mod
from apps.pipeline import storage
from apps.pipeline.entities import build_gazetteer, extract_page, match_iban_owners
from apps.pipeline.jobs import (
    DeferJob,
    RetryableError,
    SkipJob,
    enqueue,
    heartbeat,
    idempotency_key,
    job_task,
    sweep_stale_jobs,
)
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.pipeline.previews import render_previews
from apps.review.models import CaseStatus, CaseType, ReviewCase

logger = logging.getLogger(__name__)


def _review_once(
    obj,
    *,
    case_type: str,
    subtype: str,
    document=None,
    key: str,
    misc_code: str | None = None,
    context: dict | None = None,
) -> ReviewCase | None:
    if ReviewCase.objects.filter(
        batch_key=key, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists():
        return None
    misc = None
    if misc_code:
        from apps.documents.models import DocumentSubfolder

        misc = DocumentSubfolder.objects.filter(category_id="06", code=misc_code).first()
    return ReviewCase.objects.create(
        object=obj,
        case_type=case_type,
        case_subtype=subtype[:32],
        document=document,
        misc_subfolder=misc,
        batch_key=key,
        priority=80,
        context=context or {},
    )


def _hmac_key() -> bytes:
    from apps.parties.services import _hmac_key

    return _hmac_key()


# ---------------------------------------------------------------- 1 discover
@job_task(JobType.DISCOVER)
def discover(job: ProcessingJob) -> dict:
    doc = job.document
    if doc is None:
        raise SkipJob("kein Dokument")
    if doc.status not in ("registered",):
        raise SkipJob("already_processed")
    limit = int(store.get("documents.max_download_bytes", 524288000))
    if doc.size_bytes and doc.size_bytes > limit:
        _review_once(
            job.object,
            case_type=CaseType.UNCLEAR,
            subtype="file_too_large",
            document=doc,
            key=f"file_too_large:{doc.pk}",
            misc_code="02",
            context={"size_bytes": doc.size_bytes, "limit": limit, "name": doc.current_name},
        )
        return {"skipped": "file_too_large"}
    key = idempotency_key(JobType.HASH, job.object_id, doc.drive_file_id or f"upload-{doc.pk}")
    enqueue(
        JobType.HASH, job.object, key=key, document=doc, run=job.run, payload={"source_path": doc.source_path}
    )
    return {"next": "hash"}


# ---------------------------------------------------------------- 2 hash
def _download(doc: Document, target_dir: Path) -> Path:
    if doc.source == "drive_existing":
        from apps.drive import oauth

        adapter = oauth.get_adapter()
        if adapter is None:
            raise DeferJob("Keine Google-Verbindung für den Download")
        target = target_dir / ("original" + Path(doc.current_name).suffix.lower())
        adapter.download(doc.drive_file_id, target)
        return target
    src = Path(doc.source_path or "")
    if not src.exists():
        raise FileNotFoundError(f"Quelldatei fehlt: {doc.source_path}")
    target = target_dir / ("original" + src.suffix.lower())
    shutil.copy2(src, target)
    return target


GOOGLE_DOC_PREFIX = "application/vnd.google-apps."


@job_task(JobType.HASH)
def hash_document(job: ProcessingJob) -> dict:
    doc = job.document
    if doc is None or doc.status not in ("registered",):
        raise SkipJob("already_processed")
    if (doc.mime_type or "").startswith(GOOGLE_DOC_PREFIX):
        # Google-Dokumente haben keine herunterladbare Datei (nur Export); sie gehen ohne Download in die Pruefung,
        # wie es die Seitenanalyse fuer nicht unterstuetzte Formate tut (Bestand und Uebernahme gleich behandelt)
        _review_once(
            job.object,
            case_type=CaseType.UNCLEAR,
            subtype="unsupported_format",
            document=doc,
            key=f"unsupported:{doc.pk}",
            misc_code="02",
            context={
                "mime_type": doc.mime_type,
                "name": doc.current_name,
                "note": "Google-Dokument, Export offen",
            },
        )
        doc.status = "review"
        doc.save(update_fields=["status", "updated_at"])
        return {"kind": "google_doc"}
    storage.ensure_disk_reserve()
    tmp = storage.work_tmp_dir()
    try:
        path = _download(doc, tmp)
        sha = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                sha.update(block)
        digest = sha.hexdigest()
        final_dir = storage.work_dir(digest)
        if final_dir.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.rename(final_dir)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    size = storage.original_path(digest).stat().st_size if storage.original_path(digest) else doc.size_bytes
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=doc.pk)
        original = (
            Document.objects.filter(object=doc.object, sha256=digest, deleted_at__isnull=True)
            .exclude(pk=doc.pk)
            .first()
        )
        if original is not None:
            # Dublette im Objekt: keine OCR, Verweis auf das Original, Fall mit Ablagevorschlag 03_Dubletten (E 1.2 Schritt 3)
            doc.status = "duplicate"
            doc.duplicate_of = original
            doc.size_bytes = size
            doc.save(update_fields=["status", "duplicate_of", "size_bytes", "updated_at"])
            _review_once(
                doc.object,
                case_type=CaseType.UNCLEAR,
                subtype="duplicate",
                document=doc,
                key=f"duplicate:{doc.pk}",
                misc_code="03",
                context={"original_document_id": original.pk, "sha256": digest, "name": doc.current_name},
            )
        if original is not None:
            # sichere 06-Entscheidung: physische Ablage in 03_Dubletten auch fuer Bestandsdateien (B-10)
            if not (job.run and job.run.dry_run):
                enqueue(
                    JobType.FILE_TO_DRIVE,
                    job.object,
                    key=idempotency_key(JobType.FILE_TO_DRIVE, job.object_id, f"dup-{doc.pk}"),
                    document=doc,
                    run=job.run,
                    payload={"category": "06", "subfolder": "03"},
                )
            return {"duplicate_of": original.pk}
        doc.sha256 = digest
        doc.size_bytes = size
        doc.status = "hashed"
        doc.save(update_fields=["sha256", "size_bytes", "status", "updated_at"])
    _sync_hook("on_document_hashed", doc)
    elsewhere = (
        Document.objects.filter(sha256=digest, deleted_at__isnull=True)
        .exclude(object=doc.object)
        .values_list("object__object_number", flat=True)
    )
    result = {"sha256": digest, "size": size}
    if elsewhere:
        result["same_hash_in_objects"] = list(elsewhere)[:5]
    key = idempotency_key(JobType.ANALYZE_PAGES, job.object_id, digest)
    enqueue(JobType.ANALYZE_PAGES, job.object, key=key, document=doc, run=job.run)
    return result


def _sync_hook(name: str, doc) -> None:
    """Synchronisation Paperless und Drive (12.09.2026): Einhaengepunkt ohne Rueckwirkung auf die Pipeline."""
    try:
        from apps.sync import hooks

        hooks.safe(name, doc)
    except (
        Exception
    ):  # Import- oder Konfigurationsfehler der Synchronisation duerfen die Pipeline nicht stoppen
        logger.exception("Synchronisations-Hook %s nicht ausfuehrbar", name)


# ---------------------------------------------------------------- 3 analyze_pages
@job_task(JobType.ANALYZE_PAGES)
def analyze_pages(job: ProcessingJob) -> dict:
    doc = job.document
    if doc is None or doc.status not in ("hashed",) or not doc.sha256:
        raise SkipJob("already_processed")
    original = storage.original_path(doc.sha256)
    if original is None:
        # Arbeitsverzeichnis fehlt (geraeumt oder anderer Host): erneut laden
        doc.status = "registered"
        doc.save(update_fields=["status", "updated_at"])
        enqueue(
            JobType.HASH,
            job.object,
            key=idempotency_key(JobType.HASH, job.object_id, doc.drive_file_id or f"upload-{doc.pk}"),
            document=doc,
            run=job.run,
        )
        return {"requeued": "hash"}
    work = storage.work_dir(doc.sha256)
    result = analysis_mod.analyze_file(
        original,
        doc.mime_type,
        min_chars=int(store.get("ocr.digital_min_chars", 50)),
        alnum_ratio=float(store.get("ocr.digital_alnum_ratio", 0.6)),
        cover_ratio=float(store.get("ocr.image_cover_ratio", 0.9)),
        chunk_pages=int(store.get("ocr.chunk_pages", 20)),
        work=work,
    )
    if result.kind in ("unsupported", "google_doc"):
        _review_once(
            job.object,
            case_type=CaseType.UNCLEAR,
            subtype="unsupported_format",
            document=doc,
            key=f"unsupported:{doc.pk}",
            misc_code="02",
            context={"mime_type": doc.mime_type, "name": doc.current_name, "note": result.note},
        )
        doc.status = "review"
        doc.save(update_fields=["status", "updated_at"])
        return {"kind": result.kind}
    storage.cache_dir(doc.sha256).mkdir(parents=True, exist_ok=True)
    storage.analysis_path(doc.sha256).write_text(
        json.dumps(result.to_json(), ensure_ascii=False), encoding="utf-8"
    )
    key = _hmac_key()
    for page_no in result.digital_pages:
        ocr_mod.mask_and_cache(
            doc.sha256,
            page_no,
            result.texts.get(page_no, ""),
            source="text_layer" if result.kind != "office" else "office",
            hmac_key=key,
        )
    doc.page_count = result.page_count
    doc.origin_kind = result.origin_kind
    doc.save(update_fields=["page_count", "origin_kind", "updated_at"])
    if result.kind == "office" or (original.suffix.lower() == ".pdf" and result.digital_pages):
        _maybe_import_candidate(job, doc, original)  # Listenerkennung auch fuer digitale PDF (M9 Schritt 5)
    if result.chunks:
        for i, pages in enumerate(result.chunks, start=1):
            enqueue(
                JobType.OCR_CHUNK,
                job.object,
                key=f"ocr:{job.object_id}:{doc.sha256}:{i}",
                document=doc,
                run=job.run,
                payload={"chunk_no": i, "pages": pages, "pdf": str(result.pdf_path)},
            )
    else:
        enqueue(
            JobType.MERGE_PAGES,
            job.object,
            key=idempotency_key(JobType.MERGE_PAGES, job.object_id, doc.sha256),
            document=doc,
            run=job.run,
        )
    return {
        "kind": result.kind,
        "pages": result.page_count,
        "digital": len(result.digital_pages),
        "ocr": len(result.ocr_pages),
        "chunks": len(result.chunks),
    }


LIST_FIELDS = {
    "last_name",
    "first_name",
    "owner_name_raw",
    "tenant_name_raw",
    "company_name",
    "unit_label",
    "external_ref",
    "co_ownership_share",
    "iban_raw",
    "valid_from",
    "postal_code",
    "street",
    "email",
    "phone",
    "house_fee_monthly",
    "base_rent",
}


TENANT_LIST_FIELDS = {
    "tenant_name_raw",
    "base_rent",
    "utilities_prepayment",
    "heating_prepayment",
    "deposit_amount",
    "deposit_type",
    "start_date",
    "end_date",
    "persons_count",
}
OWNER_LIST_FIELDS = {
    "owner_name_raw",
    "co_ownership_share",
    "house_fee_monthly",
    "valid_from",
    "valid_to",
    "sepa_mandate_present",
    "mandate_reference",
}


def _import_kind(targets: list[str]) -> str:
    tenant = bool(TENANT_LIST_FIELDS & set(targets))
    owner = bool(OWNER_LIST_FIELDS & set(targets))
    if tenant and owner:
        return "mixed"
    return "tenant_list" if tenant else "owner_list"


def _maybe_import_candidate(job: ProcessingJob, doc: Document, path: Path) -> bool:
    """Erkannte Eigentuemer- oder Mieterliste (xlsx, csv, digitales PDF) erzeugt einen Fall import_candidate mit
    vorbelegtem Profil und Art der Liste statt eines automatischen Imports (B-34, Plan M5 Schritt 5, M9 Schritt 5).
    Gescannte Listen werden erst nach der OCR ueber den Import selbst gelesen (Profil pdf_scan_ocr)."""
    suffix = path.suffix.lower()
    if suffix not in (".xlsx", ".xlsm", ".csv", ".pdf"):
        return False
    try:
        from apps.imports.mapping import header_row_index, propose_mapping
        from apps.imports.profiles import choose_profile, read_csv, read_xlsx

        if suffix == ".pdf":
            from apps.imports import pdf_tables

            if not pdf_tables.has_text_layer(path):
                return False
            result = pdf_tables.extract_digital(path, max_pages=3)
            rows = result.rows
        else:
            table = read_csv(path) if suffix == ".csv" else read_xlsx(path)
            rows = table.rows
        synonyms = store.get("import.column_synonyms", {}) or {}
        idx = header_row_index(rows, synonyms)
        header = rows[idx] if idx < len(rows) else []
        proposals = propose_mapping(header, rows[idx + 1 : idx + 21], synonyms)
        fields = LIST_FIELDS | TENANT_LIST_FIELDS | OWNER_LIST_FIELDS
        targets = sorted({p.target for p in proposals if p.target in fields and p.confidence >= 0.8})
        if len(targets) < 3:
            return False
        profile, _scores = choose_profile(path, doc.current_name)
        kind = _import_kind(targets)
        _review_once(
            job.object,
            case_type=CaseType.IMPORT_CANDIDATE,
            subtype=kind,
            document=doc,
            key=f"import_candidate:{doc.pk}",
            context={
                "profile": profile.code,
                "targets": targets,
                "import_kind": kind,
                "name": doc.current_name,
                "rows": len(rows),
            },
        )
        return True
    except Exception:  # Erkennung ist Zusatznutzen, darf die Seitenanalyse nicht scheitern lassen
        logger.exception("Listenerkennung für Dokument %s fehlgeschlagen", doc.pk)
        return False


# ---------------------------------------------------------------- 4 ocr_chunk
@job_task(JobType.OCR_CHUNK)
def ocr_chunk(job: ProcessingJob) -> dict:
    doc = job.document
    payload = job.payload or {}
    if doc is None or doc.status in ("ocr_done", "classified", "filed", "review", "duplicate", "moved_out"):
        raise SkipJob("already_processed")
    pages = payload.get("pages") or []
    pdf = Path(payload.get("pdf") or "")
    if not pdf.exists():
        raise RetryableError(f"Block-PDF fehlt: {pdf}")
    if not ocr_mod.tools_available():
        raise RetryableError("Tesseract oder Ghostscript nicht verfügbar")
    missing = [p for p in pages if storage.read_page(doc.sha256, p) is None]
    if not missing:
        result_pages = len(pages)
    else:
        res = ocr_mod.ocr_chunk(
            pdf,
            doc.sha256,
            missing,
            int(payload.get("chunk_no", 1)),
            storage.work_dir(doc.sha256),
            language=store.get("ocr.language", "deu"),
            hmac_key=_hmac_key(),
            heartbeat=lambda n: heartbeat(job, n),
        )
        result_pages = len(res.pages)
    # Letzter fertiger Block loest merge_pages aus (unter Zeilensperre auf dem Dokument, E 10.2)
    with transaction.atomic():
        Document.objects.select_for_update().get(pk=doc.pk)
        open_chunks = (
            ProcessingJob.objects.filter(document=doc, job_type=JobType.OCR_CHUNK)
            .exclude(pk=job.pk)
            .exclude(status__in=[JobStatus.DONE, JobStatus.SKIPPED])
            .exists()
        )
        if not open_chunks:
            enqueue(
                JobType.MERGE_PAGES,
                job.object,
                key=idempotency_key(JobType.MERGE_PAGES, job.object_id, doc.sha256),
                document=doc,
                run=job.run,
            )
    return {"pages": result_pages, "reused": len(pages) - len(missing)}


# ---------------------------------------------------------------- 5 merge_pages und render_previews
@job_task(JobType.MERGE_PAGES)
def merge_pages(job: ProcessingJob) -> dict:
    doc = job.document
    if doc is None or doc.status not in ("hashed",):
        raise SkipJob("already_processed")
    analysis_file = storage.analysis_path(doc.sha256)
    if not analysis_file.exists():
        raise RetryableError("Seitenanalyse fehlt")
    analysis = json.loads(analysis_file.read_text(encoding="utf-8"))
    page_count = int(analysis.get("page_count") or 0)
    rows: list[DocumentPage] = []
    missing: list[int] = []
    for page_no in range(1, page_count + 1):
        cached = storage.read_page(doc.sha256, page_no)
        if cached is None:
            missing.append(page_no)
            continue
        text, hits, meta = cached
        source = meta.get("source") or "ocr"
        rows.append(
            DocumentPage(
                document=doc,
                page_no=page_no,
                text_source="text_layer" if source in ("text_layer", "office") else "ocr",
                is_scan=source == "ocr",
                text_content=text,
                text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                char_count=len(text),
                word_count=len(text.split()),
                ocr_engine=meta.get("engine"),
                ocr_language=store.get("ocr.language", "deu") if source == "ocr" else None,
                masked_entities_count=len(hits),
            )
        )
    if missing:
        raise RetryableError(f"Seitentexte fehlen: {missing[:10]}")
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=doc.pk)
        if doc.status != "hashed":
            raise SkipJob("already_processed")
        DocumentPage.objects.filter(document=doc).delete()
        DocumentPage.objects.bulk_create(rows, batch_size=500)
        doc.status = "ocr_done"
        doc.ocr_cache_key = doc.sha256
        doc.page_count = page_count
        doc.save(update_fields=["status", "ocr_cache_key", "page_count", "updated_at"])
    enqueue(
        JobType.RENDER_PREVIEWS,
        job.object,
        key=idempotency_key(JobType.RENDER_PREVIEWS, job.object_id, doc.sha256),
        document=doc,
        run=job.run,
        priority=200,
    )
    enqueue(
        JobType.EXTRACT_ENTITIES,
        job.object,
        key=idempotency_key(JobType.EXTRACT_ENTITIES, job.object_id, doc.sha256),
        document=doc,
        run=job.run,
    )
    return {"pages": page_count, "masked": sum(r.masked_entities_count for r in rows)}


@job_task(JobType.RENDER_PREVIEWS)
def render_previews_task(job: ProcessingJob) -> dict:
    doc = job.document
    if doc is None or not doc.sha256:
        raise SkipJob("kein Dokument")
    analysis_file = storage.analysis_path(doc.sha256)
    pdf_path = None
    if analysis_file.exists():
        pdf_path = json.loads(analysis_file.read_text(encoding="utf-8")).get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        raise SkipJob("kein PDF für Seitenbilder")
    count = render_previews(
        Path(pdf_path),
        doc.pk,
        long_edge_px=int(store.get("previews.long_edge_px", 1200)),
        quality=int(store.get("previews.jpeg_quality", 80)),
        heartbeat=lambda n: heartbeat(job, n),
    )
    return {"rendered": count}


# ---------------------------------------------------------------- 6 extract_entities
@job_task(JobType.EXTRACT_ENTITIES)
def extract_entities(job: ProcessingJob) -> dict:
    doc = job.document
    if doc is None or doc.status not in ("ocr_done", "classified", "review", "filed"):
        raise SkipJob("not_ready")
    gaz = build_gazetteer(job.object)
    fuzzy_auto = int(store.get("classification.fuzzy_auto", 90))
    fuzzy_candidate = int(store.get("classification.fuzzy_candidate_min", 78))
    entities = []
    for page in DocumentPage.objects.filter(document=doc).order_by("page_no"):
        cached = storage.read_page(doc.sha256, page.page_no)
        hits = cached[1] if cached else []
        entities.extend(
            extract_page(
                page.text_content or "",
                page.page_no,
                gaz,
                hits,
                fuzzy_auto=fuzzy_auto,
                fuzzy_candidate=fuzzy_candidate,
            )
        )
        heartbeat(job, page.page_no)
    match_iban_owners(entities, job.object)
    with transaction.atomic():
        DocumentEntity.objects.filter(document=doc).delete()
        DocumentEntity.objects.bulk_create(
            [
                DocumentEntity(
                    document=doc,
                    page_no=e.page_no,
                    entity_type=e.entity_type,
                    value_text=(e.value_text or "")[:255],
                    value_normalized=(e.value_normalized or None) and e.value_normalized[:255],
                    iban_last4=e.iban_last4,
                    iban_hash=e.iban_hash,
                    char_from=e.char_from,
                    char_to=e.char_to,
                    confidence=e.confidence,
                    matched_owner_id=e.matched_owner_id,
                    matched_unit_id=e.matched_unit_id,
                    matched_tenant_id=e.matched_tenant_id,
                    match_confidence=e.match_confidence,
                )
                for e in entities
            ],
            batch_size=500,
        )
    if doc.status in ("ocr_done",):
        enqueue(
            JobType.CLASSIFY,
            job.object,
            key=idempotency_key(JobType.CLASSIFY, job.object_id, doc.sha256),
            document=doc,
            run=job.run,
        )
    return {
        "entities": len(entities),
        "units": sum(1 for e in entities if e.entity_type == "unit_label"),
        "iban": sum(1 for e in entities if e.entity_type == "iban"),
    }


# ---------------------------------------------------------------- 7 classify, 9 decide, 10 file_to_drive
@job_task(JobType.CLASSIFY)
def classify(job: ProcessingJob) -> dict:
    """Stufe 1 und 2 mit Konfidenzmodell (docs/architektur.md 6.1 Nr. 7); Stufe 3 folgt in M8, sonst decide."""
    from apps.classification import rules as rules_mod
    from apps.classification import stage2
    from apps.classification.confidence import combine
    from apps.classification.context import build_context

    doc = job.document
    if doc is None or doc.status not in ("ocr_done", "classified", "review") or not doc.sha256:
        raise SkipJob("not_ready")
    ctx = build_context(doc)
    s1 = rules_mod.evaluate(ctx)
    s2 = stage2.predict(ctx)
    combined = combine(s1, s2, ner_support=bool(ctx.owner_ids or ctx.unit_ids))
    # Sperren (Ausweiskopie, Kategorie 01) prueft classify_ai ueber stage3_allowed und protokolliert den Grund im Fall
    stage3 = stage2.stage3_enabled() and combined.stage3_required
    stage3_purpose = None
    if (
        not stage3
        and combined.category == "04"
        and not ctx.tenant_ids
        and job.object.management_type != "weg"  # reine WEG: Mieterdokumente gehen ohnehin in die Pruefung
        and stage2.stage3_enabled()
        and bool(store.get("ai.extract_lease_facts", True))
    ):
        # Mieterdokument ohne bekannten Mieter (12.09.2026): Stufe 3 liest nur die Vertragsdaten (Mieter, Einheit,
        # Beginn, Miete, Kaution) fuer den Vorschlag „Mieter aus Dokument anlegen“; die sichere lokale Kategorie
        # bleibt (Zweck lease_facts, keine Uebersteuerung durch die KI).
        stage3 = True
        stage3_purpose = "lease_facts"
    payload = {
        "stage1": {
            "category": s1.category,
            "subfolder": s1.subfolder,
            "document_type": s1.document_type,
            "scope": s1.scope,
            "confidence": s1.confidence,
            "hard": s1.hard,
            "conflict": s1.conflict,
            "rule_codes": s1.rule_codes,
            "metadata": s1.metadata,
            "proposal": s1.proposal,
            "subtype": s1.subtype,
            "hits": s1.hits,
        },
        "stage2": {
            "top": s2.top,
            "p": s2.p,
            "gap": s2.gap,
            "labels": s2.labels,
            "model_version": s2.model_version,
            "cold_start": s2.cold_start,
            "subfolder": s2.subfolder,
            "document_type": s2.document_type,
            "sub_p": s2.sub_p,
        },
        "combined": {
            "category": combined.category,
            "confidence": combined.confidence,
            "reason": combined.reason,
        },
        "stage3_purpose": stage3_purpose,
    }
    next_type = JobType.CLASSIFY_AI if stage3 else JobType.DECIDE
    enqueue(
        next_type,
        job.object,
        key=idempotency_key(next_type, job.object_id, doc.sha256),
        document=doc,
        run=job.run,
        payload=payload,
    )
    return {"stage1": s1.category, "stage2": s2.top, "confidence": combined.confidence, "next": next_type}


def _stage3_result(payload: dict | None):
    if not payload:
        return None
    from apps.ai.services import Stage3Outcome

    return Stage3Outcome(**{k: v for k, v in payload.items() if k in Stage3Outcome.__dataclass_fields__})


def _stage_results(payload: dict):
    from apps.classification.confidence import Stage1Result, Stage2Result

    p1, p2 = payload.get("stage1") or {}, payload.get("stage2") or {}
    s1 = Stage1Result(**{k: v for k, v in p1.items() if k in Stage1Result.__dataclass_fields__})
    s2 = Stage2Result(
        **{
            k: (tuple(map(tuple, v)) if k == "labels" else v)
            for k, v in p2.items()
            if k in Stage2Result.__dataclass_fields__
        }
    )
    s2.labels = [tuple(x) for x in s2.labels]
    return s1, s2


@job_task(JobType.CLASSIFY_AI)
def classify_ai(job: ProcessingJob) -> dict:
    """Stufe 3 ueber die Provider-Schnittstelle (docs/architektur.md 6.1 Nr. 8, 6.7); Ergebnis in ai_calls und
    document_classifications, danach decide mit dem Stufe-3-Ergebnis im Payload."""
    from apps.ai import services as ai_services
    from apps.classification.context import build_context

    doc = job.document
    if doc is None or doc.status not in ("ocr_done", "classified", "review") or not doc.sha256:
        raise SkipJob("not_ready")
    payload = dict(job.payload or {})
    ctx = build_context(doc)
    outcome = ai_services.run_stage3(doc, ctx, s1_payload=payload.get("stage1"), run=job.run, job=job)
    payload["stage3"] = {
        "status": outcome.status,
        "category": outcome.category,
        "subfolder": outcome.subfolder,
        "document_type": outcome.document_type,
        "confidence": outcome.confidence,
        "object_related": outcome.object_related,
        "period_year": outcome.period_year,
        "provider": outcome.provider,
        "fallback_used": outcome.fallback_used,
        "message": outcome.message,
        "call_id": outcome.call_id,
        "reasoning": outcome.reasoning,
        "lease": outcome.lease,
    }
    enqueue(
        JobType.DECIDE,
        job.object,
        key=idempotency_key(JobType.DECIDE, job.object_id, doc.sha256),
        document=doc,
        run=job.run,
        payload=payload,
    )
    return {
        "stage3": outcome.status,
        "provider": outcome.provider,
        "confidence": outcome.confidence,
        "next": JobType.DECIDE,
    }


@job_task(JobType.DECIDE)
def decide_task(job: ProcessingJob) -> dict:
    """Entscheidungsalgorithmus und Fallbildung (docs/architektur.md 6.4, 6.5, 6.9); Dry-Run schreibt nur den Plan."""
    from apps.classification import decide as decide_mod
    from apps.classification.context import build_context

    doc = job.document
    if doc is None or doc.status not in ("ocr_done", "classified", "review") or not doc.sha256:
        raise SkipJob("not_ready")
    ctx = build_context(doc)
    s1, s2 = _stage_results(job.payload or {})
    s3 = _stage3_result((job.payload or {}).get("stage3"))
    if s3 is not None and s3.status == "ok" and (job.payload or {}).get("stage3_purpose") == "lease_facts":
        # nur Vertragsdaten lesen: die Kategorie der KI wird nicht mit der lokalen Entscheidung verrechnet
        s3.status = "lease_facts"
    dry_run = bool(job.run and job.run.dry_run)
    decision = decide_mod.decide(ctx, s1, s2, doc, s3=s3)
    if getattr(job.object, "is_system_inbox", False):
        # Eingangsobjekt (Synchronisation): nur Kategorie und Art festhalten, keine Faelle, keine Akten, keine
        # Ablage; die Objektzuordnung folgt als eigener Job und uebernimmt das Dokument in das Zielobjekt.
        decision.cases = []
        decision.links = []
        decision.segments = []
        decision.move_allowed = False
        final = decide_mod.persist(doc, ctx, s1, s2, decision, run=job.run, dry_run=dry_run)
        if not dry_run:
            enqueue(
                JobType.ASSIGN_OBJECT,
                job.object,
                key=idempotency_key(JobType.ASSIGN_OBJECT, job.object_id, doc.sha256),
                document=doc,
                run=job.run,
            )
        return {"category": decision.category, "document_type": decision.document_type, "inbox": True}
    final = decide_mod.persist(doc, ctx, s1, s2, decision, run=job.run, dry_run=dry_run)
    result = {
        "category": decision.category,
        "subfolder": decision.subfolder,
        "document_type": decision.document_type,
        "confidence": decision.confidence,
        "review": decision.review_required,
        "plan": decision.plan_text(),
        "dry_run": dry_run,
        "classification_id": final.pk,
    }
    if dry_run or decision.physical_category is None or not decision.move_allowed:
        return result
    tenant_file_id = None
    if decision.physical_category == "04":
        # Akten-Vorlage: Mieterdokumente in die Mieterakte der Einheit; ohne Akte (Schalter aus) flach in 04
        from django.db import IntegrityError

        from apps.parties.unit_files import tenant_file_for_document

        try:
            akte = tenant_file_for_document(
                job.object,
                decision.tenant_ids,
                ctx.unit_ids,
                create=bool(store.get("owner_file.create_folders_eagerly", False)),
            )
        except IntegrityError:  # zweites Dokument derselben Einheit hat die Akte gleichzeitig angelegt
            akte = tenant_file_for_document(job.object, decision.tenant_ids, ctx.unit_ids, create=False)
        tenant_file_id = akte.pk if akte is not None else None
        if tenant_file_id:
            from apps.documents.models import DocumentTenantLink

            DocumentTenantLink.objects.filter(
                document=doc, classification=final, tenant_file__isnull=True
            ).update(tenant_file_id=tenant_file_id)
    enqueue(
        JobType.FILE_TO_DRIVE,
        job.object,
        key=idempotency_key(JobType.FILE_TO_DRIVE, job.object_id, doc.sha256),
        document=doc,
        run=job.run,
        payload={
            "category": decision.physical_category,
            "subfolder": decision.physical_subfolder,
            "owner_file_id": next((link.owner_file_id for link in decision.links if link.primary), None)
            or decision.physical_owner_file_id,
            "link_subfolder": next((link.subfolder for link in decision.links if link.primary), None),
            "tenant_file_id": tenant_file_id,
        },
    )
    return result


@job_task(JobType.FILE_TO_DRIVE)
def file_to_drive(job: ProcessingJob) -> dict:
    """Letzter Schritt (docs/architektur.md 6.1 Nr. 10): Bestandsdatei per Elternwechsel verschieben, Upload in den
    Zielordner mit Idempotenzpruefung, Elternordner zurücklesen, status filed. Sperre je Objekt."""
    from django.core.cache import cache

    from apps.audit.services import record
    from apps.drive import oauth
    from apps.drive.folders import (
        FolderError,
        ensure_category_folder,
        ensure_owner_folder,
        ensure_tenant_folder,
    )
    from apps.drive.models import DriveNode as DriveNodeRow
    from apps.drive.models import NodeStatus
    from apps.parties.models import OwnerFile, TenantFile

    doc = job.document
    payload = job.payload or {}
    if doc is None or doc.status not in ("classified", "review", "duplicate"):
        raise SkipJob("not_ready")
    if doc.drive_file_id is None and doc.source == "drive_existing":
        raise SkipJob("no_drive_file")
    drive = oauth.get_adapter()
    if drive is None:
        # Kein Fehlversuch: das Dokument bleibt klassifiziert und wartet, bis die Verbindung steht (B-10, kein
        # Datenverlust). Mit RetryableError stuende es nach drei Versuchen faelschlich auf error.
        raise DeferJob("Keine Google-Verbindung für die Ablage")
    lock_key = f"drive:write:{job.object_id}"
    if not cache.add(lock_key, job.pk, timeout=600):
        # Ein anderes Dokument desselben Objekts wird gerade abgelegt. Das ist Reihenfolge, kein Fehler: kurz
        # warten, ohne einen Versuch zu verbrauchen. Mit RetryableError standen bei acht gleichzeitigen Uploads
        # fuenf Dokumente nach drei schnellen Fehlversuchen auf error (Befund 11.09.2026).
        raise DeferJob("Drive-Schreibsperre des Objekts belegt", seconds=20)
    try:
        try:
            if payload.get("category") == "05":
                akte = OwnerFile.objects.get(pk=payload["owner_file_id"])
                rows = ensure_owner_folder(akte, drive=drive)
                sub_code = payload.get("subfolder") or payload.get("link_subfolder")
                target = next((r for r in rows if r.subfolder_id and r.subfolder.code == sub_code), rows[0])
            elif payload.get("category") == "04" and payload.get("tenant_file_id"):
                target = ensure_tenant_folder(
                    TenantFile.objects.get(pk=payload["tenant_file_id"]), drive=drive
                )
            else:
                target = ensure_category_folder(
                    job.object, payload["category"], payload.get("subfolder"), drive=drive
                )
        except FolderError as exc:
            # Zielstruktur fehlt noch (Objektordner oder Hauptordner nicht registriert): der Ordnerabgleich laeuft
            # oder ist zu starten. Warten statt Fehlversuch, damit uebernommene Bestandsdateien nicht auf error
            # laufen, bevor die Struktur steht (Altbestand, 11.09.2026).
            raise DeferJob(f"Ablageziel noch nicht vorhanden: {exc}", seconds=120) from exc
        if doc.source == "drive_existing" or (doc.drive_file_id and doc.source == "moved_in"):
            node = drive.get(doc.drive_file_id)
            if node is None:
                raise SkipJob("drive_file_missing")
            current_parent = node.parent_id
            if current_parent != target.drive_file_id:
                drive.move(doc.drive_file_id, current_parent, target.drive_file_id)
                record(
                    "drive.move",
                    entity_type="document",
                    entity_id=doc.pk,
                    object_id=job.object_id,
                    after={"from": current_parent, "to": target.drive_file_id, "name": doc.current_name},
                )
            action = "moved" if current_parent != target.drive_file_id else "already_there"
        else:
            # Dubletten tragen keinen eigenen Hash (Pruefabfrage sha256 eindeutig); Quelle ist die Datei des Originals
            sha_for_file = doc.sha256 or (doc.duplicate_of.sha256 if doc.duplicate_of_id else None)
            existing = next(
                (
                    c
                    for c in drive.list_children(target.drive_file_id)
                    if not c.is_folder
                    and c.name == doc.current_name
                    and (c.app_properties or {}).get("sha256") == sha_for_file
                ),
                None,
            )
            if existing is not None:
                node = existing
                action = "reused"
            else:
                original = (storage.original_path(sha_for_file) if sha_for_file else None) or (
                    Path(doc.source_path) if doc.source_path and Path(doc.source_path).exists() else None
                )
                if original is None:
                    raise RetryableError("Quelldatei für den Upload fehlt")
                node = drive.upload(
                    target.drive_file_id,
                    original,
                    doc.current_name,
                    doc.mime_type,
                    app_properties={
                        "sha256": doc.sha256,
                        "document_id": str(doc.pk),
                        "object_id": str(job.object_id),
                    },
                )
                record(
                    "drive.upload",
                    entity_type="document",
                    entity_id=doc.pk,
                    object_id=job.object_id,
                    after={"drive_file_id": node.id, "to": target.drive_file_id, "name": doc.current_name},
                )
                action = "uploaded"
            doc.drive_file_id = node.id
            if doc.source_path and doc.source == "upload":
                Path(doc.source_path).unlink(missing_ok=True)
        # Elternordner zuruecklesen
        check = drive.get(doc.drive_file_id)
        if check is None or check.parent_id != target.drive_file_id:
            raise RetryableError("Elternordner nach der Ablage stimmt nicht mit dem Ziel überein")
        doc.drive_node = target
        doc.target_drive_node = target
        doc.drive_moved_at = timezone.now()
        doc.filed_at = timezone.now()
        if doc.status == "classified":  # mit offenem Fall bleibt review, Dubletten behalten ihren Status
            doc.status = "filed"
        doc.save(
            update_fields=[
                "drive_file_id",
                "drive_node",
                "target_drive_node",
                "drive_moved_at",
                "filed_at",
                "status",
                "updated_at",
            ]
        )
        DriveNodeRow.objects.filter(pk=target.pk).update(
            last_verified_at=timezone.now(), status=NodeStatus.ACTIVE
        )
    finally:
        cache.delete(lock_key)
    _sync_hook("on_document_filed", doc)
    return {"action": action, "target": target.drive_file_id, "target_name": target.drive_name}


# ---------------------------------------------------------------- Sweeper
@shared_task(name="pipeline.sweep", queue="io")
def sweep() -> dict:
    from apps.pipeline.runs import maybe_finish_run, schedule_runs

    result = sweep_stale_jobs()
    result["orphans_removed"] = remove_orphan_work_dirs()
    result["sync_runs_aborted"] = abort_stale_sync_runs()
    from apps.pipeline.models import ProcessingRun, RunStatus

    for run in ProcessingRun.objects.filter(status=RunStatus.RUNNING):
        maybe_finish_run(run)
    result["runs_started"] = len(schedule_runs())
    return result


STALE_SYNC_RUN_HOURS = 2


def abort_stale_sync_runs() -> int:
    """Ordnerabgleiche, die seit Stunden auf „läuft“ stehen (Worker abgebrochen, fruehere Fassung ohne Abfang
    unerwarteter Fehler), als fehlgeschlagen abschliessen, damit die Liste den wahren Zustand zeigt."""
    from apps.drive.models import DriveSyncRun

    grenze = timezone.now() - timedelta(hours=STALE_SYNC_RUN_HOURS)
    return DriveSyncRun.objects.filter(status="running", started_at__lt=grenze).update(
        status="failed",
        finished_at=timezone.now(),
        error_message=f"Abbruch: Lauf ohne Ende nach {STALE_SYNC_RUN_HOURS} Stunden (Sweeper)",
    )


def remove_orphan_work_dirs() -> int:
    """work/<sha256> ohne offenen Job nach processing.work_orphan_hours loeschen (E 10.4 Punkt 4); ocr-cache bleibt."""
    hours = int(store.get("processing.work_orphan_hours", 48))
    root = storage.data_dir() / "work"
    if not root.exists():
        return 0
    removed = 0
    cutoff = time.time() - hours * 3600
    for path in root.iterdir():
        if not path.is_dir() or path.stat().st_mtime > cutoff:
            continue
        sha = path.name
        if sha.startswith("tmp-"):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
            continue
        active = ProcessingJob.objects.filter(
            document__sha256=sha, status__in=[JobStatus.PENDING, JobStatus.RUNNING]
        ).exists()
        unfinished = Document.objects.filter(sha256=sha, status__in=["hashed"]).exists()
        if not active and not unfinished:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def now_iso() -> str:
    return timezone.now().isoformat()
