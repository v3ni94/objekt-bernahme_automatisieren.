"""verarbeitung_alle_starten: fuer alle aktiven Objekte mit offener Arbeit (Dokumente registered, hashed, ocr_done
oder error) einen Nachlauf einreihen; die Laeufe starten nach processing.max_parallel_objects nacheinander. Ohne
--echt nur die Liste der Objekte. --ohne 133,216 nimmt Objekte aus (Sammelpfade erst nach Sichtung)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.pipeline.runs import start_runs_for_all


class Command(BaseCommand):
    help = "Verarbeitungslaeufe fuer alle Objekte mit offener Arbeit einreihen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Laeufe einreihen")
        parser.add_argument(
            "--ohne",
            default="",
            help="Objektnummern, die ausgenommen bleiben (durch Komma getrennt, zum Beispiel 133,216)",
        )

    def handle(self, *args, **options):
        ohne = [n.strip() for n in str(options["ohne"] or "").split(",") if n.strip()]
        if any(not n.isdigit() for n in ohne):
            raise CommandError("--ohne erwartet Objektnummern aus Ziffern, durch Komma getrennt")
        result = start_runs_for_all(dry_run=not options["echt"], exclude=ohne)
        if result.get("excluded"):
            self.stdout.write("Ausgenommen: " + ", ".join(result["excluded"]))
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
