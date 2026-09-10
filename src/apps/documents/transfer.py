"""Aktion "In anderes Objekt uebernehmen" in Grundform (B-29): neue documents-Zeile im Zielobjekt (source moved_in),
alte Zeile moved_out mit Verweis, Seitentexte aus dem OCR-Cache uebernommen (keine erneute OCR), Neuklassifikation im
Zielobjekt, Drive-Verschiebung ueber file_to_drive, Audit beidseitig."""

from __future__ import annotations

from django.db import transaction

from apps.audit.services import record
from apps.documents.models import Document, DocumentPage
from apps.pipeline.jobs import JobType, enqueue, idempotency_key


class TransferError(Exception):
    pass


def transfer_document(
    doc: Document, target_obj, *, user=None, request=None, reason: str | None = None
) -> Document:
    if target_obj.pk == doc.object_id:
        raise TransferError("Zielobjekt ist das Quellobjekt")
    if doc.status in ("moved_out", "duplicate"):
        raise TransferError(f"Dokument im Status {doc.status} kann nicht übernommen werden")
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=doc.pk)
        new = Document.objects.create(
            object=target_obj,
            sha256=doc.sha256,
            drive_md5=doc.drive_md5,
            size_bytes=doc.size_bytes,
            mime_type=doc.mime_type,
            original_name=doc.original_name,
            current_name=doc.current_name,
            source="moved_in",
            source_path=doc.source_path,
            drive_file_id=doc.drive_file_id,
            drive_node=doc.drive_node,
            page_count=doc.page_count,
            origin_kind=doc.origin_kind,
            ocr_cache_key=doc.ocr_cache_key,
            status="ocr_done"
            if doc.sha256 and DocumentPage.objects.filter(document=doc).exists()
            else "registered",
            first_seen_at=doc.first_seen_at,
        )
        DocumentPage.objects.bulk_create(
            [
                DocumentPage(
                    document=new,
                    page_no=p.page_no,
                    text_source=p.text_source,
                    is_scan=p.is_scan,
                    text_content=p.text_content,
                    text_hash=p.text_hash,
                    char_count=p.char_count,
                    word_count=p.word_count,
                    ocr_engine=p.ocr_engine,
                    ocr_language=p.ocr_language,
                    masked_entities_count=p.masked_entities_count,
                )
                for p in DocumentPage.objects.filter(document=doc).order_by("page_no")
            ],
            batch_size=500,
        )
        doc.status = "moved_out"
        doc.duplicate_of = (
            new  # Verweis auf die Nachfolgezeile (Ergaenzung: Spalte wird fuer beide Verweise genutzt)
        )
        doc.drive_file_id = None
        doc.save(update_fields=["status", "duplicate_of", "drive_file_id", "updated_at"])
        for action, entity, object_id in (
            ("review.transfer_object", doc, doc.object_id),
            ("review.transfer_object", new, target_obj.pk),
        ):
            record(
                action,
                entity_type="document",
                entity_id=entity.pk,
                object_id=object_id,
                request=request,
                actor=user,
                reason=reason,
                after={
                    "from_object": doc.object_id,
                    "to_object": target_obj.pk,
                    "new_document_id": new.pk,
                    "old_document_id": doc.pk,
                },
            )
    if new.status == "ocr_done":
        enqueue(
            JobType.EXTRACT_ENTITIES,
            target_obj,
            key=idempotency_key(JobType.EXTRACT_ENTITIES, target_obj.pk, new.sha256),
            document=new,
        )
    else:
        enqueue(
            JobType.HASH,
            target_obj,
            key=idempotency_key(JobType.HASH, target_obj.pk, new.drive_file_id or f"upload-{new.pk}"),
            document=new,
            payload={"source_path": new.source_path},
        )
    return new
