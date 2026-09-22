"""Dokumente im Status Fehler gezielt wieder in die Verarbeitungskette nehmen (23.09.2026).

Der automatische Wiederanlauf im Objektlauf (runs.reset_failed_documents) endet nach RESET_LIMIT Wiederaufnahmen.
Nach einer Fehlerkorrektur im Code muessen die betroffenen Dokumente trotzdem erneut laufen. Auswahl nach Objekt
und/oder Fehlerklasse des letzten fehlgeschlagenen Jobs; Wiedereinstieg so spaet wie moeglich (restart_status:
Seiten vorhanden -> ocr_done, Hash vorhanden -> hashed). Offene Faelle job_failed werden mit action reprocess_manual
geschlossen, das zaehlt nicht gegen RESET_LIMIT. Ohne --echt nur Vorschau. Danach die Verarbeitung starten
(verarbeitung_alle_starten --echt), der Lauf nimmt die Dokumente an ihrem Wiedereinstieg auf.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.documents.models import Document
from apps.pipeline.models import JobStatus, ProcessingJob
from apps.pipeline.runs import restart_status
from apps.review.models import CaseStatus, ReviewCase


class Command(BaseCommand):
    help = "Dokumente im Status Fehler nach Objekt oder Fehlerklasse wieder in die Kette nehmen"

    def add_arguments(self, parser):
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")
        parser.add_argument(
            "--fehlerklasse",
            help="nur Dokumente, deren letzter fehlgeschlagener Job diese Fehlerklasse traegt (Komma-Liste)",
        )
        parser.add_argument("--echt", action="store_true", help="wieder aufnehmen statt Vorschau")

    def handle(self, *args, **options):
        klassen = [k.strip() for k in (options["fehlerklasse"] or "").split(",") if k.strip()]
        nummer = (options["objekt"] or "").strip()
        if not nummer and not klassen:
            raise CommandError("Bitte --objekt und/oder --fehlerklasse angeben")
        qs = (
            Document.objects.filter(status="error", deleted_at__isnull=True)
            .select_related("object")
            .order_by("object__object_number_numeric", "id")
        )
        if nummer:
            if not nummer.isdigit():
                raise CommandError("Objektnummer muss aus Ziffern bestehen")
            qs = qs.filter(object__object_number_numeric=int(nummer))
        ausgewaehlt: list[Document] = []
        je_objekt: Counter = Counter()
        je_klasse: Counter = Counter()
        for doc in qs.iterator(chunk_size=200):
            job = (
                ProcessingJob.objects.filter(document=doc, status=JobStatus.FAILED)
                .order_by("-finished_at", "-id")
                .only("error_class", "job_type")
                .first()
            )
            klasse = (job.error_class if job else None) or "-"
            if klassen and klasse not in klassen:
                continue
            ausgewaehlt.append(doc)
            je_objekt[doc.object.object_number] += 1
            je_klasse[f"{job.job_type}/{klasse}" if job else klasse] += 1
        for nr, n in sorted(je_objekt.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            self.stdout.write(f"Objekt {nr}: {n}")
        self.stdout.write(
            "Fehlerklassen: " + (", ".join(f"{k} {n}" for k, n in je_klasse.most_common()) or "keine")
        )
        if not ausgewaehlt:
            self.stdout.write("Keine Dokumente im Status Fehler fuer diese Auswahl.")
            return
        if not options["echt"]:
            self.stdout.write(
                f"Vorschau: {len(ausgewaehlt)} Dokumente, nichts geaendert (--echt nimmt wieder auf)."
            )
            return
        now = timezone.now()
        einstieg: Counter = Counter()
        with transaction.atomic():
            for doc in ausgewaehlt:
                doc.status = restart_status(doc)
                doc.error_message = None
                doc.save(update_fields=["status", "error_message", "updated_at"])
                einstieg[doc.status] += 1
                ReviewCase.objects.filter(
                    document=doc,
                    case_subtype="job_failed",
                    status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
                ).update(
                    status=CaseStatus.RESOLVED,
                    resolved_at=now,
                    resolution={
                        "action": "reprocess_manual",
                        "reason": "fehler_wiederaufnehmen",
                        "restart_status": doc.status,
                    },
                )
        self.stdout.write(
            f"Wieder aufgenommen: {len(ausgewaehlt)} ("
            + ", ".join(f"{k} {n}" for k, n in einstieg.most_common())
            + "). Verarbeitung starten: verarbeitung_alle_starten --echt"
        )
