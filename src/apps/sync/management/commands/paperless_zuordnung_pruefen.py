"""paperless_zuordnung_pruefen: Gegenprobe der Objektzuordnung fuer alle aus Paperless ueber das Feld MHV Objekt
uebernommenen Dokumente (apps.sync.flows.crosscheck). Ohne --echt Vorschau je Objekt: eindeutig, zweiter Bezug
(anderes Objekt im Text), schwacher Bezug (Feldobjekt nur beilaeufig, etwa Fahrtziel), kein Bezug, ohne Text;
dazu, wie viele nicht eindeutige Faelle die lokale Regel trotzdem eindeutig entscheiden wuerde. Kein KI-Aufruf.
Mit --echt: eindeutige und bezugslose Dokumente werden als geprueft markiert, nicht eindeutige mit der KI
aufgeloest (bestaetigt, umgehaengt, duplikat, Eingang, belassen); --ohne-ki loest ohne KI auf (lokal eindeutig -> belassen,
sonst Eingang), --limit begrenzt die Aufloesungen je Lauf (Kosten, Laufzeit), --objekt beschraenkt auf ein Objekt,
--details listet die nicht eindeutigen Dokumente. Bereits geprüfte Dokumente (assignment_checked_at) werden
uebersprungen; der Lauf ist wiederholbar.

KI-Ausfall (22.09.2026, Serverlauf brach mit fehlender Anbieterbibliothek im Web-Container ab): Ist die KI technisch
nicht verfuegbar (Anbieterfehler, Kostenlimit, abgeschaltet), wird das Dokument uebersprungen und bleibt ungeprueft,
statt ohne Urteil in den Eingang zu wandern; nach MAX_KI_FEHLER_FOLGE Fehlversuchen in Folge haelt der Lauf mit
Fehlercode an. Eine unerwartete Ausnahme je Dokument zaehlt als Fehler, der Lauf geht weiter."""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Document, DocumentPage, DocumentSource
from apps.sync.flows import crosscheck

MAX_KI_FEHLER_FOLGE = 3


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
        use_ai = not options["ohne_ki"]
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
        ki_fehler_folge = 0
        abbruch = None
        fehler_arten: Counter = Counter()
        fehler_beispiele: list[str] = []
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
                    try:
                        ergebnis = crosscheck.resolve(doc, check, use_ai=use_ai, skip_on_ai_error=True)
                    except Exception as exc:
                        # Fehler eines Dokuments beendet den Lauf nicht; das Dokument bleibt ungeprueft. Art und
                        # Beispiele stehen in der Zusammenfassung, weil Einzelzeilen im Sammellauf wegscrollen
                        # (Serverlauf 22.09.2026: 385 Fehler ohne auswertbare Meldung).
                        zaehler["fehler"] += 1
                        art = exc.__class__.__name__
                        fehler_arten[art] += 1
                        if len(fehler_beispiele) < 5:
                            fehler_beispiele.append(
                                f"Dok {doc.pk} ({doc.object.object_number}): {art}: {str(exc)[:200]}"
                            )
                        self.stderr.write(f"  Fehler Dok {doc.pk}: {art}: {str(exc)[:300]}")
                        continue
                    aufgeloest += 1
                    zaehler["aktion_" + ergebnis["action"]] += 1
                    zaehler["ki_" + ergebnis["ai_status"]] += 1
                    if options["details"]:
                        ziel = f" -> Objekt {ergebnis['target']}" if ergebnis.get("target") else ""
                        self.stdout.write(f"    {ergebnis['action']}{ziel} (KI {ergebnis['ai_status']})")
                    if ergebnis["action"] == "uebersprungen":
                        ki_fehler_folge += 1
                        if ki_fehler_folge >= MAX_KI_FEHLER_FOLGE:
                            abbruch = (
                                f"KI nicht verfügbar ({ergebnis['ai_status']}: {ergebnis.get('message') or ''}); "
                                f"Lauf nach {ki_fehler_folge} Fehlversuchen in Folge angehalten. Übersprungene "
                                "Dokumente bleiben ungeprüft und laufen beim nächsten Start erneut. Ohne KI: "
                                "--ohne-ki (lokal eindeutig -> belassen, sonst Eingang)."
                            )
                            break
                    else:
                        ki_fehler_folge = 0
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
        if fehler_arten:
            self.stdout.write(
                "Fehler nach Art: " + ", ".join(f"{k} {v}" for k, v in fehler_arten.most_common())
            )
            for zeile in fehler_beispiele:
                self.stdout.write("  " + zeile)
        if not echt:
            self.stdout.write(
                "Vorschau, nichts geändert, kein KI-Aufruf. Mit --echt: eindeutig und kein_bezug markieren, "
                "zweiter_bezug und schwacher_bezug mit der KI auflösen (bestaetigt, umgehaengt, eingang, belassen)."
            )
        elif abbruch:
            raise CommandError(abbruch)
        elif limit and aufgeloest >= limit:
            self.stdout.write(
                f"Grenze erreicht: {aufgeloest} Auflösungen in diesem Lauf; erneut starten für die übrigen."
            )
