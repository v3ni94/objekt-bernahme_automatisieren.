"""jobs_bereinigen: ueberzaehlige wartende Wiederholungsjobs (#n) zum selben Schluessel auf skipped setzen; je
Schluessel bleibt der aelteste offene Job. Entstanden am 13./14.09.2026, als jeder Dokumenteneingang fuer dieselben
Dokumente weitere Wiederholungsjobs anlegte (11.036 wartende analyze_pages fuer 137 Dokumente). Ohne --echt nur
Vorschau."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.pipeline.jobs import dedupe_repeat_jobs


class Command(BaseCommand):
    help = "Ueberzaehlige wartende Wiederholungsjobs (#n) auf skipped setzen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Jobs tatsaechlich auf skipped setzen")

    def handle(self, *args, **options):
        result = dedupe_repeat_jobs(dry_run=not options["echt"])
        je_art = ", ".join(f"{k}={v}" for k, v in sorted(result["by_type"].items())) or "keine"
        self.stdout.write(
            f"{'Vorschau' if result['dry_run'] else 'Bereinigt'}: {result['extra']} überzählige Wiederholungsjobs "
            f"({je_art}); behalten je Schlüssel: {result['kept']}"
        )
        if result["dry_run"]:
            self.stdout.write("Vorschau, nichts geändert. Mit --echt ausführen.")
