"""drive_dubletten_bereinigen: Altkopien in Drive in den Papierkorb legen, die bei einer erneuten Ablage desselben
Dokuments entstanden sind (Nachklassifikation bis 23.09.2026: Zweitkopie hochgeladen, Altkopie blieb im alten Ordner).

Quelle sind die Protokolleintraege drive.upload je Dokument: jede dort genannte Datei, die nicht die aktuelle Datei des
Dokuments ist, ist eine Altkopie. Vor dem Papierkorb wird die Datei in Drive nachgelesen und ueber ihre Kennzeichen
(document_id oder sha256) dem Dokument zugeordnet; ohne Bestaetigung bleibt sie liegen. Es wird nie endgueltig
geloescht, nur der Papierkorb. Ohne --echt Vorschau."""

from __future__ import annotations

from collections import Counter, defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.audit.models import AuditEvent
from apps.audit.services import record
from apps.documents.models import Document
from apps.drive import oauth


class Command(BaseCommand):
    help = "Altkopien erneut abgelegter Dokumente in Drive in den Papierkorb legen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="in den Papierkorb legen statt Vorschau")
        parser.add_argument("--objekt", default=None, help="nur dieses Objekt (Objektnummer)")
        parser.add_argument("--limit", type=int, default=0, help="hoechstens so viele Altkopien bearbeiten")

    def handle(self, *args, **options):
        drive = oauth.get_adapter()
        if drive is None:
            raise CommandError("Keine Google-Verbindung")
        mehrfach = (
            AuditEvent.objects.filter(action="drive.upload", entity_type="document")
            .values("entity_id")
            .annotate(c=Count("id"))
            .filter(c__gte=2)
        )
        doc_ids = [row["entity_id"] for row in mehrfach if row["entity_id"] is not None]
        docs = Document.objects.filter(pk__in=doc_ids).select_related("object")
        if options["objekt"]:
            docs = docs.filter(object__object_number=str(options["objekt"]).strip())
        docs = {d.pk: d for d in docs}
        uploads = defaultdict(list)
        for ev in (
            AuditEvent.objects.filter(action="drive.upload", entity_type="document", entity_id__in=list(docs))
            .order_by("occurred_at", "id")
            .only("entity_id", "after_state")
        ):
            fid = (ev.after_state or {}).get("drive_file_id")
            if fid:
                uploads[ev.entity_id].append(fid)

        zaehler = Counter()
        je_objekt = Counter()
        limit = options["limit"] or 0
        bearbeitet = 0
        for pk, doc in sorted(docs.items()):
            alt = [fid for fid in dict.fromkeys(uploads.get(pk, [])) if fid != doc.drive_file_id]
            if not alt or not doc.drive_file_id:
                continue
            zaehler["dokumente"] += 1
            for fid in alt:
                if limit and bearbeitet >= limit:
                    zaehler["begrenzung"] += 1
                    continue
                bearbeitet += 1
                node = drive.get(fid)
                if node is None or node.trashed:
                    zaehler["weg_oder_papierkorb"] += 1
                    continue
                props = node.app_properties or {}
                bestaetigt = props.get("document_id") == str(doc.pk) or (
                    doc.sha256 is not None and (props.get("sha256") or node.sha256) == doc.sha256
                )
                if not bestaetigt:
                    zaehler["nicht_bestaetigt"] += 1
                    continue
                zaehler["altkopien"] += 1
                je_objekt[doc.object.object_number] += 1
                if not options["echt"]:
                    continue
                drive.trash(fid)
                record(
                    "drive.trash_duplicate_copy",
                    entity_type="document",
                    entity_id=doc.pk,
                    object_id=doc.object_id,
                    after={"drive_file_id": fid, "kept": doc.drive_file_id, "parent": node.parent_id},
                    reason="Altkopie nach erneuter Ablage (drive_dubletten_bereinigen)",
                )
                zaehler["papierkorb"] += 1

        for nr, n in sorted(je_objekt.items(), key=lambda kv: (-kv[1], kv[0])):
            self.stdout.write(f"Objekt {nr}: {n}")
        self.stdout.write(
            f"Dokumente mit Altkopien: {zaehler['dokumente']} | bestaetigte Altkopien: {zaehler['altkopien']} | "
            f"bereits weg oder im Papierkorb: {zaehler['weg_oder_papierkorb']} | "
            f"nicht bestaetigt: {zaehler['nicht_bestaetigt']}"
        )
        if zaehler["begrenzung"]:
            self.stdout.write(f"Begrenzung {limit} erreicht, {zaehler['begrenzung']} nicht geprueft")
        if options["echt"]:
            self.stdout.write(f"{zaehler['papierkorb']} Altkopien in den Papierkorb gelegt")
        else:
            self.stdout.write("Vorschau, nichts veraendert (mit --echt in den Papierkorb legen)")
