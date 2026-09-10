"""Fortschrittszaehler je Objekt in object_progress (Ergaenzung Nr. 4, Auflage Ue18): eine Zeile je Objekt, aus
Zaehlabfragen aktualisiert; die Statusseite liest nur diese Zeile."""

from __future__ import annotations

from django.db.models import Count, Sum

from apps.documents.models import Document, DocumentPage
from apps.pipeline.models import ObjectProgress, ProcessingRun
from apps.review.models import ReviewCase


def refresh_progress(obj) -> ObjectProgress:
    counts = {
        r["status"]: r["c"]
        for r in Document.objects.filter(object=obj, deleted_at__isnull=True)
        .values("status")
        .annotate(c=Count("id"))
    }
    total = sum(counts.values())
    pages_done = DocumentPage.objects.filter(document__object=obj).count()
    pages_total = (
        Document.objects.filter(object=obj, deleted_at__isnull=True).aggregate(s=Sum("page_count"))["s"] or 0
    )
    misc = Document.objects.filter(object=obj, deleted_at__isnull=True, category_id="06").count()
    review_open = ReviewCase.objects.filter(object=obj, status__in=["open", "in_progress"]).count()
    last_run = ProcessingRun.objects.filter(object=obj).order_by("-created_at").first()
    row, _ = ObjectProgress.objects.update_or_create(
        object=obj,
        defaults={
            "documents_total": total,
            "documents_hashed": sum(
                v for k, v in counts.items() if k not in ("registered", "duplicate", "error")
            ),
            "documents_ocr_done": sum(
                v for k, v in counts.items() if k in ("ocr_done", "classified", "filed", "review")
            ),
            "documents_classified": sum(
                v for k, v in counts.items() if k in ("classified", "filed", "review")
            ),
            "documents_filed": counts.get("filed", 0),
            "documents_review": counts.get("review", 0),
            "documents_duplicate": counts.get("duplicate", 0),
            "documents_error": counts.get("error", 0),
            "documents_misc": misc,
            "pages_total": max(pages_total, pages_done),
            "pages_done": pages_done,
            "review_open": review_open,
            "last_run": last_run,
        },
    )
    return row
