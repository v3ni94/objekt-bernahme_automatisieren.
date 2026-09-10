"""classifier: Status der Kaltstartphase, Training (auch erzwungen), Aktivierung und Rollback (E 3.4, 3.5, B-27)."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.classification import training
from apps.documents.models import ClassifierModel


class Command(BaseCommand):
    help = "Lokaler Klassifikator: status | train [--force] | activate <version> | rollback <version>"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["status", "train", "activate", "rollback", "list"])
        parser.add_argument("version", nargs="?")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Training auch unter der Kaltstartschwelle und während laufender Verarbeitung",
        )

    def handle(self, *args, **options):
        action = options["action"]
        if action == "status":
            self.stdout.write(json.dumps(training.cold_start_status(), ensure_ascii=False, indent=1))
        elif action == "list":
            for row in ClassifierModel.objects.order_by("-trained_at"):
                flag = "aktiv" if row.is_active else ""
                self.stdout.write(
                    f"{row.version} {row.algorithm} {row.samples_count} Beispiele Makro-F1 {(row.metrics or {}).get('macro_f1')} {flag}"
                )
        elif action == "train":
            row = training.retrain_if_allowed(force=options["force"])
            if row is None:
                self.stdout.write(
                    "Kein Training: Kaltstartschwelle nicht erreicht oder Verarbeitung läuft (--force erzwingt)"
                )
            else:
                self.stdout.write(
                    f"Modell {row.version} trainiert, aktiv: {row.is_active}, Metriken: {json.dumps(row.metrics, ensure_ascii=False)}"
                )
        else:
            if not options["version"]:
                raise CommandError("Version angeben")
            row = training.rollback(options["version"])
            self.stdout.write(f"Modell {row.version} aktiv")
