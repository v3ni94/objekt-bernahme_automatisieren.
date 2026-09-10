"""ai_reclassify: Nachklassifikationslauf (E 4.2, F17). Dokumente in 06/01_Unklar mit offenem Fall und Grund KI nicht
verfuegbar oder Kostenlimit gehen erneut durch classify (und damit Stufe 3), solange kein Mensch sie bearbeitet hat."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.config import store
from apps.documents.models import Document
from apps.pipeline.jobs import JobType, enqueue, idempotency_key
from apps.review.models import CaseStatus, ReviewCase


class Command(BaseCommand):
    help = "Dokumente in 06/01_Unklar mit Grund KI nicht verfügbar oder Kostenlimit erneut klassifizieren"

    def add_arguments(self, parser):
        parser.add_argument("--object", help="Objektnummer, sonst alle")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true", help="auch wenn ai.reclassify_enabled falsch ist")

    def handle(self, *args, **options):
        if not store.get("ai.reclassify_enabled", False) and not options["force"]:
            self.stdout.write("ai.reclassify_enabled ist falsch; --force erzwingt den Lauf")
            return
        cases = ReviewCase.objects.filter(
            status=CaseStatus.OPEN,
            case_subtype="below_threshold",
            context__stage3_status__in=["provider_error", "budget_blocked", "disabled"],
            document__isnull=False,
        ).select_related("document", "object")
        if options["object"]:
            cases = cases.filter(object__object_number=options["object"])
        count = 0
        for case in cases:
            doc: Document = case.document
            if (
                doc.status not in ("review",)
                or ReviewCase.objects.filter(document=doc, status=CaseStatus.RESOLVED).exists()
            ):
                continue
            count += 1
            if options["dry_run"]:
                self.stdout.write(
                    f"würde neu klassifizieren: Dokument {doc.pk} {doc.current_name} (Fall {case.pk})"
                )
                continue
            case.status = CaseStatus.DISMISSED
            case.resolution = {"decision": "reclassify", "reason": "Nachklassifikationslauf"}
            case.save(update_fields=["status", "resolution", "updated_at"])
            doc.status = "ocr_done"
            doc.category = None
            doc.subfolder = None
            doc.save(update_fields=["status", "category", "subfolder", "updated_at"])
            enqueue(
                JobType.CLASSIFY,
                doc.object,
                key=idempotency_key(JobType.CLASSIFY, doc.object_id, doc.sha256),
                document=doc,
            )
        self.stdout.write(f"{count} Dokumente {'gefunden' if options['dry_run'] else 'erneut eingereiht'}")
