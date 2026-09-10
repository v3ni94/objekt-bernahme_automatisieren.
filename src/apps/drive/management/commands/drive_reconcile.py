"""drive:reconcile: Ordnerabgleich fuer ein Objekt oder alle Objekte, wahlweise als Dry-Run (F 10.3, Definition of Done)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.drive import oauth
from apps.drive.protocol import write_json, write_summary_xlsx, write_xlsx
from apps.drive.reconcile import reconcile_object
from apps.objects.models import ManagedObject


class Command(BaseCommand):
    help = "Ordnerabgleich nach CR 9 (Probelauf mit --dry-run); --all fuer den Sammellauf"

    def add_arguments(self, parser):
        parser.add_argument("--object", help="Objektnummer")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--export", action="store_true", help="Protokoll je Lauf als JSON und Excel exportieren"
        )

    def handle(self, *args, **options):
        adapter = oauth.get_adapter()
        if adapter is None:
            raise CommandError("Keine Google-Verbindung (docs/betrieb.md 7.7)")
        if options["all"]:
            objects = list(
                ManagedObject.active.filter(status__in=["new", "takeover", "active"]).order_by(
                    "object_number_numeric"
                )
            )
        elif options["object"]:
            objects = list(ManagedObject.active.filter(object_number_numeric=int(options["object"])))
            if not objects:
                raise CommandError(f"Objekt {options['object']} nicht gefunden")
        else:
            raise CommandError("--object <Nummer> oder --all angeben")
        for obj in objects:
            run = reconcile_object(obj, drive=adapter, dry_run=options["dry_run"], trigger="command")
            self.stdout.write(
                f"Objekt {obj.object_number}: Lauf {run.pk} {run.status}, geplant {run.actions_planned}, ausgeführt {run.actions_executed}, no_changes {run.no_changes}"
            )
            if options["export"]:
                self.stdout.write(f"  {write_json(run)}\n  {write_xlsx(run)}")
        if options["all"]:
            self.stdout.write(f"Sammelfassung: {write_summary_xlsx()}")
