"""drive:export-protocol --run <id> | --all [--format json|xlsx] (F 10.2)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.drive.models import DriveSyncRun
from apps.drive.protocol import write_json, write_summary_xlsx, write_xlsx


class Command(BaseCommand):
    help = "Abgleichsprotokoll exportieren"

    def add_arguments(self, parser):
        parser.add_argument("--run", type=int)
        parser.add_argument("--all", action="store_true", help="Sammelfassung ueber alle Objekte (Excel)")
        parser.add_argument("--format", choices=["json", "xlsx"], default="xlsx")

    def handle(self, *args, **options):
        if options["all"]:
            self.stdout.write(str(write_summary_xlsx()))
            return
        if not options["run"]:
            raise CommandError("--run <id> oder --all angeben")
        try:
            run = DriveSyncRun.objects.select_related("object").get(pk=options["run"])
        except DriveSyncRun.DoesNotExist as exc:
            raise CommandError("Lauf nicht gefunden") from exc
        self.stdout.write(str(write_json(run) if options["format"] == "json" else write_xlsx(run)))
