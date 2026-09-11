"""altbestand_import: Quellordner aus einer Datei (Drive-Links oder Folder-IDs, je Zeile einer) in die
Altbestand-Tabelle aufnehmen; Doppelte werden nicht erneut angelegt. Mit --aufloesen werden Namen und
Objektnummern aus Drive gelesen und vorhandene Objekte zugeordnet. Vorgabe: db/seeds/altbestand_ordner.txt."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.drive import oauth, takeover
from apps.drive.adapter import DriveError
from apps.drive.models import TakeoverSource


class Command(BaseCommand):
    help = "Altbestand-Ordner aus einer Datei in die Tabelle aufnehmen (idempotent)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--datei", default=str(Path(settings.REPO_DIR) / "db" / "seeds" / "altbestand_ordner.txt")
        )
        parser.add_argument(
            "--aufloesen", action="store_true", help="Namen und Objektnummern aus Drive lesen"
        )

    def handle(self, *args, **options):
        path = Path(options["datei"])
        if not path.exists():
            raise CommandError(f"Datei fehlt: {path}")
        refs = takeover.parse_folder_refs(path.read_text(encoding="utf-8"))
        created, existing = takeover.add_sources(refs)
        self.stdout.write(
            f"{len(refs)} IDs in der Datei, {len(created)} neu aufgenommen, {existing} bereits vorhanden"
        )
        if not options["aufloesen"]:
            return
        adapter = oauth.get_adapter()
        if adapter is None:
            self.stdout.write("Keine Google-Verbindung; Namen bleiben offen")
            return
        ok = failed = 0
        for src in TakeoverSource.objects.filter(resolved_at__isnull=True):
            try:
                if takeover.resolve_source(src, adapter):
                    ok += 1
                else:
                    failed += 1
            except DriveError as exc:
                failed += 1
                self.stdout.write(f"{src.drive_folder_id}: Drive-Fehler {exc}")
        linked = TakeoverSource.objects.filter(object__isnull=False).count()
        self.stdout.write(f"aufgelöst: {ok}, nicht gefunden: {failed}, mit Objekt: {linked}")
