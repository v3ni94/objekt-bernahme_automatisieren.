"""transit_bereinigen: Transit-Kopien (Uploads und Downloads der Paperless-Uebernahme unter transit/) loeschen, deren
Dokument bereits in Drive liegt (drive_file_id gesetzt, Status filed oder duplicate). Bis 15.09.2026 wurde die Kopie
nur fuer Uploads nach der Ablage geloescht, die Paperless-Downloads blieben liegen und fuellten die Platte. Leere
Eingangsordner (incoming/<uuid>) werden mit entfernt. Ohne --echt nur Vorschau. Bestandsdateien aus Drive haben keine
Kopie und sind nicht betroffen."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from apps.audit.services import record
from apps.documents.models import Document

ABGELEGT = ("filed", "duplicate")


def bereinigen(*, echt: bool) -> dict:
    qs = (
        Document.objects.filter(
            source__in=["upload", "paperless"], drive_file_id__isnull=False, status__in=ABGELEGT
        )
        .exclude(source_path__isnull=True)
        .exclude(source_path="")
        .only("id", "source_path", "object_id")
    )
    dateien = 0
    bytes_ = 0
    ordner = 0
    for doc in qs.iterator(chunk_size=500):
        p = Path(doc.source_path)
        if not p.is_file():
            continue
        dateien += 1
        bytes_ += p.stat().st_size
        if not echt:
            continue
        p.unlink(missing_ok=True)
        # incoming/<uuid> ohne weitere Dateien mit entfernen
        eltern = p.parent
        try:
            if eltern.name and not any(eltern.iterdir()):
                eltern.rmdir()
                ordner += 1
        except OSError:
            pass
    result = {"echt": echt, "dateien": dateien, "mb": round(bytes_ / 1024**2, 1), "ordner": ordner}
    if echt and dateien:
        record("processing.transit_cleanup", entity_type="document", entity_id=None, after=result)
    return result


class Command(BaseCommand):
    help = "Transit-Kopien bereits in Drive abgelegter Dokumente loeschen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Dateien tatsaechlich loeschen")

    def handle(self, *args, **options):
        result = bereinigen(echt=options["echt"])
        self.stdout.write(
            f"{'Gelöscht' if result['echt'] else 'Vorschau'}: {result['dateien']} Transit-Kopien, "
            f"{result['mb']} MB, leere Eingangsordner entfernt: {result['ordner']}"
        )
        if not result["echt"]:
            self.stdout.write("Vorschau, nichts geändert. Mit --echt ausführen.")
