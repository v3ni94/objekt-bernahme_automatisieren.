"""paperless_feld_abgleich: Vergleicht je Dokument eines Objekts den Wert des Feldes MHV Objekt in Paperless mit der
Zuordnung in der Anwendung. Abweichungen entstehen, wenn das Feld in Paperless nach der Uebernahme geleert oder
geaendert wurde: Die 5-Minuten-Abfrage uebernimmt jedes Dokument mit gesetztem Feld sofort, ein spaeteres Leeren
in Paperless wirkt nicht auf die Anwendung zurueck (Pilot Objekt 82, 22.09.2026: Fahrtkostenabrechnungen mit der
Objektadresse als Fahrtziel). Ohne --echt nur Vorschau. Mit --echt werden Dokumente mit leerem Feld in das
Eingangsobjekt uebernommen (transfer_document, Drive-Datei folgt), Dokumente mit anderer aktiver Objektnummer in
dieses Objekt; unbekannte Nummern und in Paperless nicht mehr vorhandene Dokumente werden nur gemeldet.

Jede Uebernahme wird als Lernbeispiel festgehalten: das geleerte Feld als Ablehnung des alten Objekts (reject),
die andere Nummer als Korrektur (correct). Die Ablehnung verhindert, dass die Inhaltszuordnung im Eingang dasselbe
Objekt wegen desselben Adresstreffers sofort wieder automatisch waehlt; sie bleibt dort Vorschlag."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Document
from apps.documents.transfer import TransferError, transfer_document
from apps.objects.models import ManagedObject
from apps.sync import services
from apps.sync.assignment import learning
from apps.sync.flows.paperless_pull import _object_number_from_field
from apps.sync.inbox import ensure_inbox_object
from apps.sync.models import ExampleKind, ExternalLink, LinkRole, SyncSystem
from apps.sync.paperless.errors import PaperlessError, PaperlessNotFound


def _same_number(a: str, b: str) -> bool:
    return a.isdigit() and b.isdigit() and int(a) == int(b)


def abgleich(obj: ManagedObject) -> list[dict]:
    """Eine Zeile je Dokument mit Paperless-Original: befund ok, leer, anderes_objekt, unbekannt, fehlt, fehler."""
    client = services.get_client()
    if client is None:
        raise CommandError("Paperless nicht konfiguriert")
    meta = services.connection_meta()
    if not (meta.get("field_ids") or {}).get("object"):
        raise CommandError("Feld MHV Objekt in Paperless nicht eingerichtet (Verbindungstest ausführen)")
    rows: list[dict] = []
    links = (
        ExternalLink.objects.filter(
            document__object=obj,
            document__deleted_at__isnull=True,
            system=SyncSystem.PAPERLESS,
            role=LinkRole.ORIGINAL,
        )
        .exclude(document__status__in=["moved_out", "duplicate"])
        .select_related("document")
        .order_by("document_id")
    )
    for link in links:
        doc = link.document
        row = {"doc": doc, "remote_id": link.external_id, "befund": "ok", "ziel": None, "feld": None}
        try:
            remote = client.get_document(int(link.external_id))
        except PaperlessNotFound:
            row["befund"] = "fehlt"
            rows.append(row)
            continue
        except PaperlessError as exc:
            row["befund"] = "fehler"
            row["fehler"] = str(exc)[:160]
            rows.append(row)
            continue
        number = _object_number_from_field(remote, meta)
        row["feld"] = number
        if number is None:
            row["befund"] = "leer"
        elif not _same_number(number, obj.object_number):
            ziel = ManagedObject.active.filter(
                object_number_numeric=int(number), is_system_inbox=False
            ).first()
            if ziel is None:
                row["befund"] = "unbekannt"
            else:
                row["befund"] = "anderes_objekt"
                row["ziel"] = ziel
        rows.append(row)
    return rows


class Command(BaseCommand):
    help = "Feld MHV Objekt in Paperless gegen die Zuordnung eines Objekts pruefen; --echt uebernimmt Abweichungen"

    def add_arguments(self, parser):
        parser.add_argument("objekt", help="Objektnummer")
        parser.add_argument("--echt", action="store_true", help="Abweichungen uebernehmen (sonst Vorschau)")

    def handle(self, *args, **options):
        nummer = str(options["objekt"]).strip()
        if not nummer.isdigit():
            raise CommandError("Objektnummer muss aus Ziffern bestehen")
        obj = ManagedObject.active.filter(object_number_numeric=int(nummer), is_system_inbox=False).first()
        if obj is None:
            raise CommandError(f"Objekt {nummer} nicht gefunden")
        rows = abgleich(obj)
        counts = {}
        for r in rows:
            counts[r["befund"]] = counts.get(r["befund"], 0) + 1
        self.stdout.write(
            f"Objekt {obj.object_number}: {len(rows)} Dokumente mit Paperless-Original, "
            + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        )
        for r in rows:
            if r["befund"] == "ok":
                continue
            doc: Document = r["doc"]
            name = (doc.current_name or doc.original_name or "")[:70]
            ziel = f" -> Objekt {r['ziel'].object_number}" if r["ziel"] else ""
            extra = f" ({r['fehler']})" if r.get("fehler") else ""
            self.stdout.write(
                f"  {r['befund']:14} Dok {doc.pk} Paperless {r['remote_id']} Feld {r['feld'] or '-'}{ziel}: {name}{extra}"
            )
        if not options["echt"]:
            self.stdout.write(
                "Vorschau, nichts geändert. Mit --echt: leer -> Eingangsobjekt, anderes_objekt -> Zielobjekt."
            )
            return
        eingang = ensure_inbox_object()
        moved = 0
        for r in rows:
            if r["befund"] not in ("leer", "anderes_objekt"):
                continue
            ziel = r["ziel"] if r["befund"] == "anderes_objekt" else eingang
            try:
                new = transfer_document(
                    r["doc"], ziel, reason="Feldabgleich Paperless (Feld geleert oder geändert)"
                )
            except TransferError as exc:
                self.stdout.write(f"  nicht übernommen Dok {r['doc'].pk}: {exc}")
                continue
            merkmale = {"reason": "feldabgleich_paperless", "paperless_id": str(r["remote_id"])}
            if r["befund"] == "leer":
                learning.record_example(
                    new, kind=ExampleKind.REJECT, previous_object=obj, proposed_object=obj, features=merkmale
                )
            else:
                learning.record_example(
                    new, kind=ExampleKind.CORRECT, previous_object=obj, target_object=ziel, features=merkmale
                )
            moved += 1
            self.stdout.write(
                f"  übernommen Dok {r['doc'].pk} -> Objekt {ziel.object_number} als Dok {new.pk}"
            )
        self.stdout.write(f"Übernommen: {moved}")
