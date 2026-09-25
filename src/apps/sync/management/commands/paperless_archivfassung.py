"""Dokumente aus Paperless mit offenem Fall "nicht unterstuetztes Format" auf die PDF-Archivfassung umstellen
(25.09.2026). Die Uebernahme lud bis dahin immer das Original; E-Mails, Office-Altformate und HTML kennt die
Verarbeitung nicht, Paperless hat sie beim Eingang aber mit Tika und Gotenberg in ein PDF gewandelt (6.527 Faelle im
Bestand). Je Fall: Archivfassung bei Paperless abfragen, laden, Datei und Metadaten des Dokuments ersetzen, Dokument
zurueck auf registriert (Hash, Seiten, Klassifikation werden neu berechnet), Fall erledigen. Ohne --echt Vorschau.
Danach die Verarbeitung starten (verarbeitung_alle_starten --echt), der Lauf nimmt die Dokumente von vorn auf.
"""

from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.ingest import safe_filename
from apps.documents.models import DocumentSource, DocumentStatus
from apps.pipeline import storage
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import services
from apps.sync.flows.common import link_for, upsert_link
from apps.sync.models import SyncSystem
from apps.sync.paperless.errors import PaperlessError


class Command(BaseCommand):
    help = "Paperless-Dokumente mit Fall 'nicht unterstuetztes Format' auf die PDF-Archivfassung umstellen"

    def add_arguments(self, parser):
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")
        parser.add_argument("--limit", type=int, default=0, help="hoechstens so viele Faelle (0 = alle)")
        parser.add_argument("--echt", action="store_true", help="umstellen statt Vorschau")

    def handle(self, *args, **options):
        client = services.get_client()
        if client is None:
            raise CommandError("Paperless ist nicht konfiguriert oder abgeschaltet")
        nummer = (options["objekt"] or "").strip()
        if nummer and not nummer.isdigit():
            raise CommandError("Objektnummer muss aus Ziffern bestehen")
        cases = (
            ReviewCase.objects.filter(
                status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
                case_type=CaseType.UNCLEAR,
                case_subtype="unsupported_format",
                document__isnull=False,
                document__source=DocumentSource.PAPERLESS,
                document__deleted_at__isnull=True,
            )
            .select_related("document", "document__object")
            .order_by("id")
        )
        if nummer:
            cases = cases.filter(document__object__object_number_numeric=int(nummer))
        if options["limit"]:
            cases = cases[: options["limit"]]
        ergebnis: Counter = Counter()
        je_objekt: Counter = Counter()
        for case in cases.iterator(chunk_size=100):
            doc = case.document
            link = link_for(doc, SyncSystem.PAPERLESS)
            if link is None or not link.external_id:
                ergebnis["ohne Paperless-Verknuepfung"] += 1
                continue
            try:
                meta = client.get_metadata(int(link.external_id))
            except PaperlessError as exc:
                ergebnis[f"Paperless-Fehler ({type(exc).__name__})"] += 1
                continue
            if not meta.get("has_archive_version"):
                ergebnis["keine Archivfassung in Paperless"] += 1
                continue
            je_objekt[doc.object.object_number] += 1
            if not options["echt"]:
                ergebnis["Archivfassung vorhanden"] += 1
                continue
            ergebnis["umgestellt" if self._umstellen(client, case, doc, link) else "zu gross"] += 1
        for nr, n in sorted(je_objekt.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            self.stdout.write(f"Objekt {nr}: {n}")
        self.stdout.write(
            "Ergebnis: "
            + (", ".join(f"{k} {n}" for k, n in ergebnis.most_common()) or "keine offenen Faelle")
        )
        if not options["echt"]:
            self.stdout.write("Vorschau, nichts geaendert (--echt stellt um).")
        elif ergebnis["umgestellt"]:
            self.stdout.write(
                f"Umgestellt: {ergebnis['umgestellt']} Dokumente zurueck auf registriert. "
                "Verarbeitung starten: verarbeitung_alle_starten --echt"
            )

    def _umstellen(self, client, case: ReviewCase, doc, link) -> bool:
        remote_id = int(link.external_id)
        stem = Path(doc.current_name or doc.original_name or "").stem or f"paperless-{remote_id}"
        name = safe_filename(f"{stem}.pdf")
        storage.ensure_disk_reserve()
        path = storage.upload_dir(doc.object_id) / name
        client.download(remote_id, path, original=False)
        size = path.stat().st_size
        if size > int(store.get("documents.max_download_bytes", 524288000)):
            path.unlink(missing_ok=True)
            return False
        now = timezone.now()
        with transaction.atomic():
            doc.source_path = str(path)
            doc.current_name = name
            doc.mime_type = "application/pdf"
            doc.size_bytes = size
            doc.sha256 = None
            doc.page_count = None
            doc.origin_kind = None
            doc.status = DocumentStatus.REGISTERED
            doc.error_message = None
            doc.save(
                update_fields=[
                    "source_path",
                    "current_name",
                    "mime_type",
                    "size_bytes",
                    "sha256",
                    "page_count",
                    "origin_kind",
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )
            upsert_link(
                doc,
                system=SyncSystem.PAPERLESS,
                external_id=link.external_id,
                mime_type="application/pdf",
                size_bytes=size,
                synced_fields={**(link.synced_fields or {}), "variant": "archive"},
            )
            ReviewCase.objects.filter(pk=case.pk).update(
                status=CaseStatus.RESOLVED,
                resolved_at=now,
                resolution={"action": "reprocess_archive", "reason": "paperless_archivfassung"},
            )
            record(
                "sync.paperless_archive",
                entity_type="document",
                entity_id=doc.pk,
                object_id=doc.object_id,
                after={"paperless_id": remote_id, "size_bytes": size, "case_id": case.pk},
            )
        return True
