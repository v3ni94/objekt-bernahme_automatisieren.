"""altbestand_aufarbeiten: alle Altbestand-Quellordner mit zugeordnetem Objekt nacheinander aufarbeiten (wie
„Aufarbeiten“ je Zeile). Ohne --echt nur die Liste der Ordner und der Stand eines laufenden oder letzten
Sammellaufs. Mit --echt sofort im Vordergrund, mit --echt --hintergrund als Celery-Aufgabe (Stand auf der
Altbestand-Seite)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.drive import oauth, takeover


class Command(BaseCommand):
    help = "Alle Altbestand-Ordner mit Objekt aufarbeiten (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Aufarbeitung ausfuehren")
        parser.add_argument("--hintergrund", action="store_true", help="als Celery-Aufgabe einreihen")

    def handle(self, *args, **options):
        stand = takeover.all_state()
        if stand:
            self.stdout.write(
                f"Letzter Sammellauf: {stand.get('status')}, {stand.get('done', 0)} von {stand.get('total', 0)} Ordnern, "
                f"{stand.get('registered', 0)} übernommen, {stand.get('skipped', 0)} übersprungen"
                + (f", gerade: {stand['current']}" if stand.get("current") else "")
                + (f", Fehler: {len(stand['errors'])}" if stand.get("errors") else "")
            )
            for text in stand.get("errors") or []:
                self.stdout.write(f"  Fehler: {text}")
        if not options["echt"]:
            plan = takeover.run_all(dry_run=True)
            self.stdout.write(f"Vorschau: {plan['total']} Quellordner mit Objekt")
            for row in plan["per_source"]:
                self.stdout.write(
                    f"  {row['name']}: Objekt {row['object_number']}, Status {row['status']}, "
                    f"bisher {row['files_registered']} übernommen"
                )
            self.stdout.write("Vorschau, nichts geändert. Mit --echt ausführen (--hintergrund für Celery).")
            return
        if oauth.get_adapter() is None:
            raise CommandError("Keine Google-Verbindung")
        if options["hintergrund"]:
            from apps.drive.tasks import takeover_all_task

            if stand.get("status") in ("queued", "running"):
                raise CommandError("Sammellauf läuft bereits")
            takeover._set_all_state({"status": "queued", "total": len(takeover.runnable_sources())})
            takeover_all_task.apply_async(args=[None], queue="io")
            self.stdout.write(
                "Sammelaufarbeitung eingereiht; Stand auf der Altbestand-Seite oder mit diesem Kommando."
            )
            return
        try:
            result = takeover.run_all()
        except takeover.TakeoverError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Aufgearbeitet: {result['done']} von {result['total']} Ordnern, {result['registered']} Datei(en) übernommen, "
            f"{result['skipped']} übersprungen, Läufe {result['runs']}"
        )
        for text in result["errors"]:
            self.stdout.write(f"  Fehler: {text}")
