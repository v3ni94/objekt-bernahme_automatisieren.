"""paperless_zuordnung_pruefen: Gegenprobe der Objektzuordnung fuer alle aus Paperless ueber das Feld MHV Objekt
uebernommenen Dokumente (apps.sync.flows.crosscheck). Ohne --echt Vorschau je Objekt: eindeutig, zweiter Bezug
(anderes Objekt im Text), schwacher Bezug (Feldobjekt nur beilaeufig, etwa Fahrtziel), kein Bezug, ohne Text;
dazu, wie viele nicht eindeutige Faelle die lokale Regel trotzdem eindeutig entscheiden wuerde. Kein KI-Aufruf.
Mit --echt: eindeutige und bezugslose Dokumente werden als geprueft markiert, nicht eindeutige mit der KI
aufgeloest (bestaetigt, umgehaengt, Eingang, belassen); --ohne-ki loest ohne KI auf (lokal eindeutig -> belassen,
sonst Eingang), --limit begrenzt die Aufloesungen je Lauf (Kosten, Laufzeit), --objekt beschraenkt auf ein Objekt,
--details listet die nicht eindeutigen Dokumente. Bereits geprüfte Dokumente (assignment_checked_at) werden
uebersprungen; der Lauf ist wiederholbar."""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Document, DocumentPage, DocumentSource
from apps.sync.flows import crosscheck


class Command(BaseCommand):
    help = "Gegenprobe der Objektzuordnung fuer aus Paperless uebernommene Dokumente; --echt loest mit der KI auf"

    def add_arguments(self, parser):
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")
        parser.add_argument(
            "--echt", action="store_true", help="markieren und nicht eindeutige Faelle aufloesen"
        )
        parser.add_argument("--ohne-ki", action="store_true", dest="ohne_ki", help="ohne KI aufloesen")
        parser.add_argument(
            "--limit", type=int, default=0, help="hoechstens so viele Aufloesungen (0 = alle)"
        )
        parser.add_argument(
            "--details", action="store_true", help="nicht eindeutige Dokumente einzeln listen"
        )

    def handle(self, *args, **options):
        echt = bool(options["echt"])
        qs = (
            Document.objects.filter(
                source=DocumentSource.PAPERLESS,
                object__is_system_inbox=False,
                deleted_at__isnull=True,
                assignment_checked_at__isnull=True,
            )
            .exclude(status__in=["moved_out", "duplicate", "registered", "hashed", "error"])
            .select_related("object")
            .order_by("object__object_number_numeric", "id")
        )
        if options["objekt"]:
            nummer = str(options["objekt"]).strip()
            if not nummer.isdigit():
                raise CommandError("Objektnummer muss aus Ziffern bestehen")
            qs = qs.filter(object__object_number_numeric=int(nummer))
        gesamt: Counter = Counter()
        je_objekt: dict[str, Counter] = {}
        aufgeloest = 0
        limit = int(options["limit"] or 0)
        for doc in qs.iterator(chunk_size=200):
            zaehler = je_objekt.setdefault(doc.object.object_number, Counter())
            zaehler["geprüft"] += 1
            if not DocumentPage.objects.filter(document=doc).exists():
                zaehler["ohne_text"] += 1
                continue
            check = crosscheck.analyse(doc)
            zaehler[check.kind] += 1
            if check.kind in crosscheck.AMBIGUOUS:
                zaehler["lokal_eindeutig" if check.local_unique else "lokal_offen"] += 1
                if options["details"]:
                    name = (doc.current_name or doc.original_name or "")[:60]
                    andere = ", ".join(check.other_numbers) or "-"
                    lokal = "lokal eindeutig" if check.local_unique else "lokal offen"
                    self.stdout.write(f"  {check.kind:15} Dok {doc.pk}: {name} | andere: {andere} | {lokal}")
                if echt and (limit == 0 or aufgeloest < limit):
                    ergebnis = crosscheck.resolve(doc, check, use_ai=not options["ohne_ki"])
                    aufgeloest += 1
                    zaehler["aktion_" + ergebnis["action"]] += 1
                    zaehler["ki_" + ergebnis["ai_status"]] += 1
                    if options["details"]:
                        ziel = f" -> Objekt {ergebnis['target']}" if ergebnis.get("target") else ""
                        self.stdout.write(f"    {ergebnis['action']}{ziel} (KI {ergebnis['ai_status']})")
            elif echt:
                aktion = "ok" if check.kind == "eindeutig" else "kein_bezug"
                crosscheck.mark(doc, check, aktion)
        for nummer, zaehler in je_objekt.items():
            gesamt.update(zaehler)
            self.stdout.write(
                f"Objekt {nummer}: " + ", ".join(f"{k} {v}" for k, v in sorted(zaehler.items()))
            )
        self.stdout.write(
            "Gesamt: " + (", ".join(f"{k} {v}" for k, v in sorted(gesamt.items())) or "keine Dokumente")
        )
        if not echt:
            self.stdout.write(
                "Vorschau, nichts geändert, kein KI-Aufruf. Mit --echt: eindeutig und kein_bezug markieren, "
                "zweiter_bezug und schwacher_bezug mit der KI auflösen (bestaetigt, umgehaengt, eingang, belassen)."
            )
        elif limit and aufgeloest >= limit:
            self.stdout.write(
                f"Grenze erreicht: {aufgeloest} Auflösungen in diesem Lauf; erneut starten für die übrigen."
            )
