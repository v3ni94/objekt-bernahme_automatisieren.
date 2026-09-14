"""verarbeitung_alle_starten: fuer alle aktiven Objekte mit offener Arbeit (Dokumente registered, hashed, ocr_done
oder error) einen Nachlauf einreihen; die Laeufe starten nach processing.max_parallel_objects nacheinander. Ohne
--echt nur die Liste der Objekte."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.pipeline.runs import start_runs_for_all


class Command(BaseCommand):
    help = "Verarbeitungslaeufe fuer alle Objekte mit offener Arbeit einreihen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Laeufe einreihen")

    def handle(self, *args, **options):
        result = start_runs_for_all(dry_run=not options["echt"])
        if not options["echt"]:
            self.stdout.write(
                f"Vorschau: {len(result['objects'])} Objekte mit offener Arbeit: {', '.join(result['objects']) or 'keine'}"
            )
            self.stdout.write("Vorschau, nichts geändert. Mit --echt ausführen.")
            return
        if result.get("background"):
            start = "Start im Hintergrund (pipeline.schedule_runs, spätestens mit dem nächsten Sweep)"
        else:
            start = f"sofort gestartet: {len(result.get('running_now') or [])}"
        self.stdout.write(
            f"Eingereiht: {len(result['runs'])} Läufe für {', '.join(result['started']) or 'kein Objekt'}; {start}"
        )
