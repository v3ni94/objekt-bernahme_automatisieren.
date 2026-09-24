"""buchhaltung_jahresordner: bereits in 03_Buchhaltung abgelegte Dokumente in die Jahresordner 03/JJJJ bringen
(Wunsch der Geschaeftsfuehrung 24.09.2026). Der Befehl verschiebt nicht selbst: er setzt die Dokumente auf classified
und reiht den Ablagejob erneut ein; die Ablage verschiebt die vorhandene Drive-Datei per Elternwechsel in den
Jahresordner (legt ihn bei Bedarf an) und setzt den Status wieder auf filed. Jahr: Abrechnungs- oder Wirtschaftsjahr,
sonst Beginn des Zeitraums, sonst Dokumentdatum; ohne Jahr bleibt das Dokument flach in 03. Ohne --echt Vorschau je
Objekt und Jahr. Es wird nie geloescht, nur verschoben; jede Bewegung protokolliert die Ablage (drive.move)."""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Document
from apps.documents.periods import YEAR_FOLDER_CATEGORY, document_filing_year
from apps.drive.models import NodeKind
from apps.pipeline.jobs import JobType, enqueue, idempotency_key
from apps.pipeline.models import JobStatus, ProcessingJob


class Command(BaseCommand):
    help = "Abgelegte Dokumente in 03_Buchhaltung in die Jahresordner 03/JJJJ bringen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Ablagejobs einreihen statt Vorschau")
        parser.add_argument("--objekt", default=None, help="nur dieses Objekt (Objektnummer)")
        parser.add_argument("--limit", type=int, default=0, help="hoechstens so viele Dokumente einreihen")

    def handle(self, *args, **options):
        docs = (
            Document.objects.filter(
                category_id=YEAR_FOLDER_CATEGORY,
                subfolder__isnull=True,
                status__in=["filed", "review"],
                drive_file_id__isnull=False,
                deleted_at__isnull=True,
                object__deleted_at__isnull=True,
            )
            .select_related("object", "drive_node")
            .order_by("object__object_number", "id")
        )
        if options["objekt"]:
            nr = str(options["objekt"]).strip()
            if not nr.isdigit():
                raise CommandError("Objektnummer muss aus Ziffern bestehen")
            docs = docs.filter(object__object_number=nr)
        limit = max(int(options["limit"] or 0), 0)
        zaehler: Counter = Counter()
        je_ziel: Counter = Counter()
        eingereiht = 0
        for doc in docs.iterator(chunk_size=500):
            if doc.status == "review":
                # liegt mit offenem Fall physisch in 03 und zieht mit der Entscheidung im Pruefcenter um
                zaehler["mit_offenem_fall"] += 1
                continue
            year = document_filing_year(doc)
            node = doc.drive_node
            im_jahresordner = node is not None and node.node_kind == NodeKind.YEAR_FOLDER
            if year is None:
                zaehler["ohne_jahr"] += 1
                continue
            if im_jahresordner and node.year == year:
                zaehler["bereits_im_jahresordner"] += 1
                continue
            if not doc.sha256:
                zaehler["ohne_hash"] += 1
                continue
            if limit and eingereiht >= limit:
                zaehler["begrenzung"] += 1
                continue
            if im_jahresordner:
                zaehler["falsches_jahr"] += 1  # Jahr nach der Ablage geaendert (Import, Datenkorrektur)
            je_ziel[(doc.object.object_number, year)] += 1
            eingereiht += 1
            if not options["echt"]:
                continue
            doc.status = "classified"
            doc.save(update_fields=["status", "updated_at"])
            payload = {"category": YEAR_FOLDER_CATEGORY, "subfolder": None}
            # ein noch wartender Ablagejob traegt sonst seine alte Zielangabe (Muster Pruefcenter)
            ProcessingJob.objects.filter(
                document=doc, job_type=JobType.FILE_TO_DRIVE, status=JobStatus.PENDING
            ).update(payload=payload, last_error=None, next_attempt_at=None)
            enqueue(
                JobType.FILE_TO_DRIVE,
                doc.object,
                key=idempotency_key(JobType.FILE_TO_DRIVE, doc.object_id, doc.sha256),
                document=doc,
                payload=payload,
            )
        for (nr, year), n in sorted(je_ziel.items()):
            self.stdout.write(f"Objekt {nr}, Jahr {year}: {n}")
        self.stdout.write(
            f"Umzug in Jahresordner: {eingereiht} | bereits im Jahresordner: {zaehler['bereits_im_jahresordner']} | "
            f"ohne Jahr (bleiben flach in 03): {zaehler['ohne_jahr']} | ohne Hash: {zaehler['ohne_hash']} | "
            f"im falschen Jahr: {zaehler['falsches_jahr']} | "
            f"mit offenem Fall (ziehen mit der Entscheidung um): {zaehler['mit_offenem_fall']}"
        )
        if zaehler["begrenzung"]:
            self.stdout.write(f"Begrenzung {limit} erreicht, {zaehler['begrenzung']} nicht eingereiht")
        if options["echt"]:
            self.stdout.write(
                f"{eingereiht} Ablagejobs eingereiht; die Worker verschieben die Dateien (lauf-monitor zeigt die Jobs)"
            )
        else:
            self.stdout.write("Vorschau, nichts veraendert (mit --echt einreihen)")
