"""Offene Faelle „nicht unterstuetztes Format" erledigen, deren Format die Kette inzwischen kennt (25.09.2026).

Die Seitenanalyse liest seit dem 25.09.2026 E-Mails (.eml, .msg) als Textseite, seit dem 26.09.2026 auch HTML
(.html, .htm) und prueft endungslose Dateien, Temporaer-Endungen (.tmp, .herunterladen, .indir, .hed, .part,
.crdownload) und sonst unbekannte Endungen am Inhalt. Im Bestand standen 4.404 E-Mails aus dem Drive-Altbestand als Fall in der Pruefung, nach
dem Bestandslauf blieben 2.007 Faelle, davon rund 900 mit Temporaer-Endung oder ohne Endung.

Gruppen (--gruppe):
- bekannt (Standard): Faelle, deren Formatweiche (detect_kind, Endung und MIME-Typ) jetzt etwas anderes als
  „unsupported" liefert; office_legacy (.doc, .xls, .rtf, .odt, .ods) hat die eigene Gruppe office, weil
  die Umwandlung LibreOffice im Worker-Image voraussetzt und die 853 Altformate als eigener Lauf starten sollen.
  Faelle, deren Inhalt die Kette schon geprueft hat (Kontext content_checked=true), bleiben ausgenommen:
  dort hat der Inhalt entschieden, ein MIME-Typ aus Drive (etwa application/pdf an Rechnung.tmp) zaehlt nicht mehr.
- temporaer: Faelle mit Temporaer-Endung, ohne Endung oder mit sonst unbekannter Endung (.pdf_, .bak, .01),
  deren Inhalt die Kette noch nicht geprueft hat (Kontext ohne content_checked=true); die Formatweiche prueft seit
  dem 26.09.2026 jede unbekannte Endung am Inhalt. Google-Dokumente bleiben ausgenommen.
- html: nur Faelle mit Art html.
- office (26.09.2026): Faelle mit Office-Altformat (.doc, .xls, .rtf, .odt, .ods nach Endung oder MIME-Typ,
  oder Kontext detected_kind=office_legacy, wenn nur die Inhaltspruefung das Altformat erkannt hat, etwa eine
  Datei .tmp mit RTF-Inhalt), auch wenn die Kette den Fall schon mit Notiz „Umwandlung nicht verfuegbar" oder
  „Umwandlung fehlgeschlagen" angelegt hat (content_checked=true): die Gruppe dient gerade dem erneuten Lauf,
  sobald LibreOffice im Worker-Image liegt. Ein dauerhaft nicht wandelbares Dokument kommt bei jedem Lauf dieser
  Gruppe erneut in die Kette; die Notiz im neuen Fall nennt den Grund. Praesentationen (.ppt) gehoeren nicht
  dazu (kein libreoffice-impress im Worker-Image, Paperless-Archivfassung als Ersatz).

Optional je Objekt und begrenzt. Mit --echt: Fall erledigt (action reprocess_format), Dokument zurueck auf
registriert (Hash, Seiten und Klassifikation werden neu berechnet; Bestandsdateien aus Drive laedt die Kette erneut
aus Drive), Protokoll pipeline.format_reprocess. Ohne --echt Vorschau mit Verteilung nach Endung, Format, Quelle und
Objekt (keine Dateinamen). Danach die Verarbeitung starten (verarbeitung_alle_starten --echt).
"""

from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import DocumentSource, DocumentStatus
from apps.pipeline.analysis import UNREADABLE_KINDS, detect_kind
from apps.review.models import CaseStatus, CaseType, ReviewCase

GRUPPEN = ("bekannt", "temporaer", "html", "office")


def _endung(doc) -> str:
    return Path(doc.current_name or "").suffix.lower()


def _inhalt_geprueft(case: ReviewCase) -> bool:
    return (case.context or {}).get("content_checked") is True


def _altformat(case: ReviewCase) -> bool:
    """Office-Altformat nach Endung oder MIME-Typ, oder nur aus dem Inhalt erkannt (Kontext detected_kind aus der
    Formatweiche vor der gescheiterten Umwandlung; der Name blieb, weil nur erfolgreich gelesene Dateien nach dem
    Inhalt umbenannt werden), 26.09.2026."""
    doc = case.document
    if detect_kind(Path(doc.current_name or ""), doc.mime_type) == "office_legacy":
        return True
    return (case.context or {}).get("detected_kind") == "office_legacy"


def _auswahlgrund(case: ReviewCase, gruppe: str) -> str | None:
    """Liefert die Art fuer die Statistik, wenn der Fall zur Gruppe gehoert, sonst None."""
    doc = case.document
    kind = detect_kind(Path(doc.current_name or ""), doc.mime_type)
    if gruppe == "html":
        return kind if kind == "html" else None
    if gruppe == "office":
        # unabhaengig von content_checked: gescheiterte Umwandlungen sollen nach Bereitstellung von LibreOffice
        # erneut laufen (26.09.2026)
        return "office_legacy" if _altformat(case) else None
    if _inhalt_geprueft(case):
        # Inhalt bereits geprueft und weiterhin unbekannt: keine Gruppe nimmt den Fall erneut auf (26.09.2026)
        return None
    if gruppe == "temporaer":
        if kind != "unsupported":
            return None
        return kind
    # bekannt: office_legacy nur ueber die Gruppe office (eigener Lauf, braucht LibreOffice im Worker-Image)
    return kind if kind not in UNREADABLE_KINDS and kind != "office_legacy" else None


class Command(BaseCommand):
    help = (
        "Faelle 'nicht unterstuetztes Format' mit inzwischen bekanntem Format erledigen und neu verarbeiten"
    )

    def add_arguments(self, parser):
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")
        parser.add_argument("--limit", type=int, default=0, help="hoechstens so viele Faelle (0 = alle)")
        parser.add_argument(
            "--gruppe",
            choices=GRUPPEN,
            default="bekannt",
            help="bekannt (Format jetzt bekannt), temporaer (Temporaer-Endung, ohne Endung oder unbekannte "
            "Endung, Inhalt noch ungeprueft), html oder office (Altformate .doc, .xls, .rtf, .odt, .ods, auch "
            "nur am Inhalt erkannt)",
        )
        parser.add_argument("--echt", action="store_true", help="wieder aufnehmen statt Vorschau")

    def handle(self, *args, **options):
        nummer = (options["objekt"] or "").strip()
        if nummer and not nummer.isdigit():
            raise CommandError("Objektnummer muss aus Ziffern bestehen")
        gruppe = options["gruppe"]
        cases = (
            ReviewCase.objects.filter(
                status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
                case_type=CaseType.UNCLEAR,
                case_subtype="unsupported_format",
                document__isnull=False,
                document__deleted_at__isnull=True,
            )
            .select_related("document", "document__object")
            .order_by("id")
        )
        if nummer:
            cases = cases.filter(document__object__object_number_numeric=int(nummer))
        je_format: Counter = Counter()
        je_endung: Counter = Counter()
        je_quelle: Counter = Counter()
        je_objekt: Counter = Counter()
        weiterhin: Counter = Counter()
        altformat: Counter = Counter()
        geprueft: Counter = Counter()
        ausgewaehlt: list[ReviewCase] = []
        limit = options["limit"] or 0
        for case in cases.iterator(chunk_size=200):
            doc = case.document
            endung = _endung(doc) or "(ohne)"
            kind = _auswahlgrund(case, gruppe)
            if kind is None:
                # Altformate zuerst, auch mit content_checked (gescheiterte Umwandlung): sie gehoeren zur
                # Gruppe office und nicht zu "weiterhin unbekannt"
                if gruppe != "office" and _altformat(case):
                    altformat[endung] += 1
                elif gruppe not in ("html", "office") and _inhalt_geprueft(case):
                    geprueft[endung] += 1
                else:
                    weiterhin[endung] += 1
                continue
            if limit and len(ausgewaehlt) >= limit:
                continue
            ausgewaehlt.append(case)
            je_format[kind] += 1
            je_endung[endung] += 1
            je_quelle[
                DocumentSource(doc.source).label if doc.source in DocumentSource.values else doc.source
            ] += 1
            je_objekt[doc.object.object_number] += 1
        titel = {
            "bekannt": "Faelle mit inzwischen bekanntem Format",
            "temporaer": "Faelle mit Temporaer-Endung, ohne Endung oder unbekannter Endung (Inhalt noch ungeprueft)",
            "html": "Faelle mit HTML",
            "office": "Faelle mit Office-Altformat (Umwandlung ueber LibreOffice)",
        }[gruppe]
        self.stdout.write(f"{titel}: {len(ausgewaehlt)}")
        if je_format:
            # Nur Zaehler je Endung, Format, Quelle und Objekt, keine Dateinamen (Vertraulichkeit)
            self.stdout.write("Nach Endung: " + ", ".join(f"{k} {n}" for k, n in je_endung.most_common()))
            self.stdout.write("Nach Format: " + ", ".join(f"{k} {n}" for k, n in je_format.most_common()))
            self.stdout.write("Nach Quelle: " + ", ".join(f"{k} {n}" for k, n in je_quelle.most_common()))
            for nr, n in sorted(je_objekt.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
                self.stdout.write(f"Objekt {nr}: {n}")
        if altformat:
            self.stdout.write(
                f"Office-Altformat (Gruppe office): {sum(altformat.values())} ("
                + ", ".join(f"{k} {n}" for k, n in altformat.most_common(12))
                + ")"
            )
        if geprueft:
            self.stdout.write(
                f"Inhalt bereits geprueft, weiterhin unbekannt: {sum(geprueft.values())} ("
                + ", ".join(f"{k} {n}" for k, n in geprueft.most_common(12))
                + ")"
            )
        if weiterhin:
            self.stdout.write(
                f"Weiterhin nicht verarbeitbar: {sum(weiterhin.values())} ("
                + ", ".join(f"{k} {n}" for k, n in weiterhin.most_common(12))
                + ")"
            )
        if not ausgewaehlt:
            self.stdout.write(
                "Keine offenen Faelle mit inzwischen bekanntem Format."
                if gruppe == "bekannt"
                else f"Keine offenen Faelle in der Gruppe {gruppe}."
            )
            return
        if not options["echt"]:
            self.stdout.write(
                f"Vorschau: {len(ausgewaehlt)} Faelle, nichts geaendert (--echt nimmt wieder auf)."
            )
            return
        now = timezone.now()
        umgestellt = 0
        for case in ausgewaehlt:
            doc = case.document
            with transaction.atomic():
                doc.status = DocumentStatus.REGISTERED
                doc.page_count = None
                doc.origin_kind = None
                doc.error_message = None
                doc.save(update_fields=["status", "page_count", "origin_kind", "error_message", "updated_at"])
                ReviewCase.objects.filter(pk=case.pk).update(
                    status=CaseStatus.RESOLVED,
                    resolved_at=now,
                    resolution={
                        "action": "reprocess_format",
                        "reason": "formate_wiederaufnehmen",
                        "gruppe": gruppe,
                    },
                )
                record(
                    "pipeline.format_reprocess",
                    entity_type="document",
                    entity_id=doc.pk,
                    object_id=doc.object_id,
                    after={
                        "case_id": case.pk,
                        "kind": detect_kind(Path(doc.current_name or ""), doc.mime_type),
                        "gruppe": gruppe,
                        "suffix": _endung(doc),
                    },
                )
            umgestellt += 1
            if umgestellt % 500 == 0:
                self.stdout.write(f"... {umgestellt} wieder aufgenommen")
                self.stdout.flush()
        self.stdout.write(
            f"Wieder aufgenommen: {umgestellt} Dokumente zurueck auf registriert. "
            "Verarbeitung starten: verarbeitung_alle_starten --echt"
        )
