"""Offene Faelle „nicht unterstuetztes Format" erledigen, deren Format die Kette inzwischen kennt (25.09.2026).

Die Seitenanalyse liest seit dem 25.09.2026 E-Mails (.eml, .msg) als Textseite. Im Bestand standen 4.404 E-Mails aus
dem Drive-Altbestand als Fall in der Pruefung. Auswahl: offene Faelle unclear/unsupported_format mit Dokument, dessen
Formatweiche (detect_kind) jetzt etwas anderes als „unsupported" liefert; optional je Objekt und begrenzt. Mit --echt:
Fall erledigt (action reprocess_format), Dokument zurueck auf registriert (Hash, Seiten und Klassifikation werden neu
berechnet; Bestandsdateien aus Drive laedt die Kette erneut aus Drive), Protokoll pipeline.format_reprocess. Ohne
--echt Vorschau mit Verteilung nach Format, Quelle und Objekt. Danach die Verarbeitung starten
(verarbeitung_alle_starten --echt), der Lauf nimmt die Dokumente von vorn auf.
"""

from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import DocumentSource, DocumentStatus
from apps.pipeline.analysis import detect_kind
from apps.review.models import CaseStatus, CaseType, ReviewCase


class Command(BaseCommand):
    help = (
        "Faelle 'nicht unterstuetztes Format' mit inzwischen bekanntem Format erledigen und neu verarbeiten"
    )

    def add_arguments(self, parser):
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")
        parser.add_argument("--limit", type=int, default=0, help="hoechstens so viele Faelle (0 = alle)")
        parser.add_argument("--echt", action="store_true", help="wieder aufnehmen statt Vorschau")

    def handle(self, *args, **options):
        nummer = (options["objekt"] or "").strip()
        if nummer and not nummer.isdigit():
            raise CommandError("Objektnummer muss aus Ziffern bestehen")
        cases = (
            ReviewCase.objects.filter(
                status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
                case_type=CaseType.UNCLEAR,
                case_subtype="unsupported_format",
                document__isnull=False,
                document__deleted_at__isnull=True,
            )
            .select_related("document", "document__object")
            .order_by("id")
        )
        if nummer:
            cases = cases.filter(document__object__object_number_numeric=int(nummer))
        je_format: Counter = Counter()
        je_quelle: Counter = Counter()
        je_objekt: Counter = Counter()
        weiterhin: Counter = Counter()
        ausgewaehlt: list[ReviewCase] = []
        limit = options["limit"] or 0
        for case in cases.iterator(chunk_size=200):
            doc = case.document
            kind = detect_kind(Path(doc.current_name or ""), doc.mime_type)
            if kind == "unsupported":
                weiterhin[Path(doc.current_name or "").suffix.lower() or "(ohne)"] += 1
                continue
            if limit and len(ausgewaehlt) >= limit:
                continue
            ausgewaehlt.append(case)
            je_format[kind] += 1
            je_quelle[
                DocumentSource(doc.source).label if doc.source in DocumentSource.values else doc.source
            ] += 1
            je_objekt[doc.object.object_number] += 1
        self.stdout.write(f"Faelle mit inzwischen bekanntem Format: {len(ausgewaehlt)}")
        if je_format:
            self.stdout.write("Nach Format: " + ", ".join(f"{k} {n}" for k, n in je_format.most_common()))
            self.stdout.write("Nach Quelle: " + ", ".join(f"{k} {n}" for k, n in je_quelle.most_common()))
            for nr, n in sorted(je_objekt.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
                self.stdout.write(f"Objekt {nr}: {n}")
        if weiterhin:
            # Nur Zaehler je Endung, keine Dateinamen (Vertraulichkeit)
            self.stdout.write(
                f"Weiterhin nicht verarbeitbar: {sum(weiterhin.values())} ("
                + ", ".join(f"{k} {n}" for k, n in weiterhin.most_common(12))
                + ")"
            )
        if not ausgewaehlt:
            self.stdout.write("Keine offenen Faelle mit inzwischen bekanntem Format.")
            return
        if not options["echt"]:
            self.stdout.write(
                f"Vorschau: {len(ausgewaehlt)} Faelle, nichts geaendert (--echt nimmt wieder auf)."
            )
            return
        now = timezone.now()
        umgestellt = 0
        for case in ausgewaehlt:
            doc = case.document
            with transaction.atomic():
                doc.status = DocumentStatus.REGISTERED
                doc.page_count = None
                doc.origin_kind = None
                doc.error_message = None
                doc.save(update_fields=["status", "page_count", "origin_kind", "error_message", "updated_at"])
                ReviewCase.objects.filter(pk=case.pk).update(
                    status=CaseStatus.RESOLVED,
                    resolved_at=now,
                    resolution={"action": "reprocess_format", "reason": "formate_wiederaufnehmen"},
                )
                record(
                    "pipeline.format_reprocess",
                    entity_type="document",
                    entity_id=doc.pk,
                    object_id=doc.object_id,
                    after={
                        "case_id": case.pk,
                        "kind": detect_kind(Path(doc.current_name or ""), doc.mime_type),
                    },
                )
            umgestellt += 1
            if umgestellt % 500 == 0:
                self.stdout.write(f"... {umgestellt} wieder aufgenommen")
                self.stdout.flush()
        self.stdout.write(
            f"Wieder aufgenommen: {umgestellt} Dokumente zurueck auf registriert. "
            "Verarbeitung starten: verarbeitung_alle_starten --echt"
        )
