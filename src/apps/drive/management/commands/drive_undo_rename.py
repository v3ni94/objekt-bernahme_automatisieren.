"""drive:undo-rename <action_id>: Umbenennung eines Altordners mit name_before zuruecknehmen (F 4.3 Punkt 6, CR 0 Punkt 5)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.drive import oauth
from apps.drive.models import DriveSyncAction
from apps.drive.reconcile import ReconcileError, undo_rename


class Command(BaseCommand):
    help = "Ausgefuehrte Umbenennung des Ordnerabgleichs rueckgaengig machen"

    def add_arguments(self, parser):
        parser.add_argument("action_id", type=int)

    def handle(self, *args, **options):
        adapter = oauth.get_adapter()
        if adapter is None:
            raise CommandError("Keine Google-Verbindung")
        try:
            action = DriveSyncAction.objects.select_related("sync_run").get(pk=options["action_id"])
            node = undo_rename(action, drive=adapter)
        except DriveSyncAction.DoesNotExist as exc:
            raise CommandError("Aktion nicht gefunden") from exc
        except ReconcileError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Ordner {node.id} heißt wieder {node.name}")
