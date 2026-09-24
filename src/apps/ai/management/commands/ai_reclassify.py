"""ai_reclassify: Nachklassifikationslauf (E 4.2, F17). Dokumente in 06/01_Unklar mit offenem Fall und Grund KI nicht
verfuegbar, Kostenlimit, Stufe 3 nicht freigegeben oder Stufe 3 nie aufgerufen (kein Anbieter freigegeben, daher
stage3_status leer, 20.09.2026) gehen erneut durch classify (und damit Stufe 3), solange kein Mensch sie bearbeitet hat.

23.09.2026: Auswahl nach Objekt oder mit Ausnahmen (--ohne 133,216 fuer Sammelpfade ohne Objektbezug), Begrenzung
(--limit) fuer Piloten, Zusammenfassung je Objekt statt einer Zeile je Dokument (12.500 offene Faelle); Einzelzeilen
nur mit --details.

24.09.2026: --unterfall manual_check mit --alle: Dokumente, die die KI als „kein Objektbezug“ nach 06 gelegt hat
(4.303 Faelle, entstanden ohne Objektanschrift im Auftrag), gehen nach Aenderung von Auftrag oder Regeln erneut durch
Stufe 3; --alle hebt fuer below_threshold die Beschraenkung auf Faelle ohne Stufe-3-Aufruf auf."""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.config import store
from apps.documents.models import Document
from apps.pipeline.jobs import JobType, enqueue, idempotency_key
from apps.review.models import CaseStatus, ReviewCase


def _nummern(text: str | None) -> list[str]:
    nummern = [t.strip() for t in (text or "").split(",") if t.strip()]
    if any(not n.isdigit() for n in nummern):
        raise CommandError("Objektnummern muessen aus Ziffern bestehen (Komma-Liste)")
    return nummern


class Command(BaseCommand):
    help = "Dokumente in 06/01_Unklar mit Grund KI nicht verfügbar, Kostenlimit oder ohne Stufe-3-Aufruf erneut klassifizieren"

    def add_arguments(self, parser):
        parser.add_argument("--object", help="Objektnummer, sonst alle")
        parser.add_argument("--ohne", help="Objektnummern ausnehmen (Komma-Liste), z. B. 133,216")
        parser.add_argument("--limit", type=int, default=0, help="hoechstens so viele Dokumente (0 = alle)")
        parser.add_argument(
            "--details", action="store_true", help="eine Zeile je Dokument statt nur je Objekt"
        )
        parser.add_argument(
            "--unterfall",
            default="below_threshold",
            help="Unterfaelle (Komma-Liste): below_threshold (Standard), manual_check (KI-Einstufung Sonstiges)",
        )
        parser.add_argument(
            "--alle",
            action="store_true",
            help="auch Faelle mit frueherem Stufe-3-Ergebnis (nach Aenderung von Auftrag oder Regeln); "
            "fuer manual_check immer noetig",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true", help="auch wenn ai.reclassify_enabled falsch ist")

    def handle(self, *args, **options):
        if not store.get("ai.reclassify_enabled", False) and not options["force"]:
            self.stdout.write("ai.reclassify_enabled ist falsch; --force erzwingt den Lauf")
            return
        ohne = _nummern(options["ohne"])
        limit = max(int(options["limit"] or 0), 0)
        # stage3_status leer: classify hat die Stufe 3 gar nicht angestossen, weil kein Anbieter freigegeben war
        # (stage3_enabled false); diese Faelle sind der Bestand vor der Freischaltung des ersten Anbieters
        ohne_stufe3 = (
            Q(context__stage3_status__in=["provider_error", "budget_blocked", "disabled"])
            | Q(context__stage3_status=None)
            | Q(context__stage3_status__isnull=True)
        )
        unterfaelle = [u.strip() for u in str(options["unterfall"] or "").split(",") if u.strip()]
        unbekannt = [u for u in unterfaelle if u not in ("below_threshold", "manual_check")]
        if unbekannt or not unterfaelle:
            raise CommandError("--unterfall erlaubt below_threshold und manual_check")
        if "manual_check" in unterfaelle and not options["alle"]:
            # manual_check-Faelle tragen keinen stage3_status im Kontext; der Filter ohne_stufe3 wuerde sie still
            # einschliessen, obwohl die KI bereits geantwortet hat. Der erneute Aufruf kostet Geld, daher ausdruecklich.
            raise CommandError("--unterfall manual_check erfordert --alle (Faelle mit Stufe-3-Ergebnis)")
        cases = ReviewCase.objects.filter(
            status=CaseStatus.OPEN, case_subtype__in=unterfaelle, document__isnull=False
        )
        if not options["alle"]:
            # manual_check-Faelle haben immer ein Stufe-3-Ergebnis (die KI hat 06 gewaehlt); ohne --alle bleiben
            # nur Faelle unter Schwelle ohne Stufe-3-Aufruf (24.09.2026)
            cases = cases.filter(ohne_stufe3)
        cases = cases.select_related("document", "object").order_by("object__object_number", "id")
        if options["object"]:
            cases = cases.filter(object__object_number=options["object"])
        if ohne:
            cases = cases.exclude(object__object_number__in=ohne)
        count = 0
        je_objekt: Counter = Counter()
        for case in cases.iterator(chunk_size=200):
            doc: Document = case.document
            if (
                doc.status not in ("review",)
                or ReviewCase.objects.filter(document=doc, status=CaseStatus.RESOLVED).exists()
            ):
                continue
            if limit and count >= limit:
                break
            count += 1
            je_objekt[case.object.object_number] += 1
            if options["dry_run"]:
                if options["details"]:
                    self.stdout.write(
                        f"würde neu klassifizieren: Dokument {doc.pk} {doc.current_name} (Fall {case.pk})"
                    )
                continue
            case.status = CaseStatus.DISMISSED
            case.resolution = {
                "decision": "reclassify",
                "reason": "Nachklassifikationslauf",
                "unterfall": case.case_subtype,
            }
            case.save(update_fields=["status", "resolution", "updated_at"])
            doc.status = "ocr_done"
            doc.category = None
            doc.subfolder = None
            doc.save(update_fields=["status", "category", "subfolder", "updated_at"])
            enqueue(
                JobType.CLASSIFY,
                doc.object,
                key=idempotency_key(JobType.CLASSIFY, doc.object_id, doc.sha256),
                document=doc,
            )
        for nr, n in sorted(je_objekt.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            self.stdout.write(f"Objekt {nr}: {n}")
        if ohne:
            self.stdout.write("Ausgenommen: " + ", ".join(ohne))
        if limit and count >= limit:
            self.stdout.write(f"Begrenzung {limit} erreicht")
        self.stdout.write(f"{count} Dokumente {'gefunden' if options['dry_run'] else 'erneut eingereiht'}")
