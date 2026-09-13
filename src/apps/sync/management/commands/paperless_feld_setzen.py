"""paperless_feld_setzen: Gesamtlauf ueber alle Paperless-Speicherpfade mit zugeordnetem Objekt. Ohne --echt nur
Vorschau (liest je Pfad die Dokumente und zaehlt ohne Feld, gleicher Wert, abweichender Wert). Mit --echt wird das
Feld MHV Objekt bei Dokumenten ohne Wert gesetzt (nie ueberschrieben) und danach ein echter Bestandslauf fuer alle
betroffenen Objektnummern gestartet; die Uebernahme nach Drive laeuft dann ueber die Operationsliste."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.sync import storage_paths


class Command(BaseCommand):
    help = "Feld MHV Objekt fuer alle zugeordneten Paperless-Speicherpfade setzen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Feld setzen und Bestandslauf starten")

    def handle(self, *args, **options):
        echt: bool = options["echt"]
        try:
            summary = storage_paths.fill_all(dry_run=not echt)
        except storage_paths.StoragePathError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"{'Gesamtlauf' if echt else 'Vorschau'}: {summary['paths']} zugeordnete Speicherpfade, "
            f"{summary['done']} gelesen, {summary['total']} Dokumente"
        )
        for row in summary["per_path"]:
            self.stdout.write(
                f"  {row['path']}: Objekt {row['object_number']}, {row['total']} Dokumente, ohne Feld {row['missing']}, "
                f"bereits gesetzt {row['same']}, abweichend {row['other']}"
                + (f", gesetzt {row['set']}" if echt else "")
            )
        for text in summary["skipped"]:
            self.stdout.write(f"  übersprungen: {text}")
        for text in summary["errors"]:
            self.stdout.write(f"  Fehler: {text}")
        if echt:
            self.stdout.write(
                f"Gesetzt: {summary['set']}; bereits gesetzt: {summary['same']}; abweichend unverändert: {summary['other']}; "
                f"Objektnummern: {', '.join(summary['numbers']) or 'keine'}"
            )
            if summary.get("inventory_run_id"):
                self.stdout.write(
                    f"Bestandslauf {summary['inventory_run_id']} gestartet (Umfang alle Nummern)."
                )
            elif summary.get("inventory_error"):
                self.stdout.write(f"Bestandslauf nicht gestartet: {summary['inventory_error']}")
        else:
            self.stdout.write(
                f"Würde setzen: {summary['missing']}; bereits gesetzt: {summary['same']}; abweichend: {summary['other']}. "
                "Vorschau, nichts geändert. Mit --echt ausführen."
            )
