"""Restformate bereinigen: Sammelaktionen fuer die Faelle „nicht unterstuetztes Format", die nach Formatweiche,
Inhaltspruefung und Office-Umwandlung uebrig bleiben (Entscheidungsvorlage E-1 in docs/plan/entscheidungen.md,
26.09.2026). Ohne --echt Vorschau mit Zaehlern je Objekt und Endung, keine Dateinamen.

Gruppen (--gruppe, Pflichtangabe):
- temporaer: Faelle, deren Dokument eine Temporaer-Endung (.tmp, .herunterladen, .indir, .hed, .part, .crdownload)
  oder keine Endung traegt UND deren Fallkontext content_checked=true hat UND deren Inhaltspruefung keine Signatur
  erkannt hat (Kontext content_recognized=false; Altfaelle ohne das Feld nur mit Notiz „(Inhalt unbekannt)" und
  detected_kind unsupported): die Formatweiche hat den Inhalt geprueft (apps.pipeline.sniff) und weder PDF, Bild,
  Office, E-Mail, HTML noch einen Container erkannt. Solche Dateien sind abgebrochene Downloads oder Systemreste der
  Vorverwaltung. Nicht in der Gruppe, obwohl content_checked gesetzt ist: Faelle mit erkanntem Office-Altformat und
  gescheiterter oder nicht verfuegbarer Umwandlung (detected_kind office_legacy, Gruppe office in
  formate_wiederaufnehmen) sowie Faelle mit erkannter, aber nicht verarbeitbarer Signatur (Praesentation .ppt oder
  .pptx, ZIP-Archiv, Makro-Office, Office-Paket ohne bekannten Hauptteil); sie werden getrennt gezaehlt und bleiben
  im Pruefcenter. Die Endung .css (Entscheidungsvorlage E-1, Gruppe „Temporaer und System") gehoert bewusst nicht
  zur Auswahl, weil die Formatweiche dieselbe Menge TEMP_SUFFIXES nutzt; solche Faelle erscheinen als „andere
  Endung" und bleiben Einzelfall im Pruefcenter. Mit --echt: Drive-Datei in den Papierkorb (adapter.trash, nie
  endgueltig, Wiederherstellung in Drive 30 Tage), Dokument als geloescht markiert (Soft-Delete nach D 1:
  deleted_at, delete_reason; keine Zeile wird entfernt), Drive-Verknuepfung auf „im Papierkorb", Fall erledigt
  (resolution action trashed_temp_file), Protokoll pipeline.temp_file_trashed. Faelle ohne content_checked bleiben
  unangetastet: sie gehoeren zuerst in formate_wiederaufnehmen --gruppe temporaer (Inhaltspruefung). Dokumente ohne
  Drive-Datei (Upload oder Paperless-Download, noch nie abgelegt) werden nur gezaehlt: ihre einzige Kopie liegt im
  Transit, ein Papierkorb ist dort nicht vorhanden, deshalb keine Loeschung durch dieses Kommando.
- archive-medien: Faelle mit .zip, .mp4, .mov, .avi, .mkv, .mp3, .wav. Die Kette liest diese Formate nicht
  (Entpacken oder Abspielen ist keine Funktion der Objektakte); die Dateien gehen ungelesen nach
  06_Sonstiges/02_Manuelle_Pruefung, damit sie in der Objektakte liegen und der Sachbearbeiter sie dort sichtet.
  Mit --echt: Dokument auf Kategorie 06, Unterordner 02, ohne Dokumentart, Status klassifiziert; Ablagejob
  FILE_TO_DRIVE wie bei Dubletten (payload category 06, subfolder 02; ein wartender Ablagejob mit alter Zielangabe
  wird ueberschrieben, Muster apply_decision); Fall erledigt (resolution action filed_manual_check), Protokoll
  pipeline.manual_check_filed. Die Drive-Bewegung laeuft nie synchron, sondern im Ablagejob. Dokumente mit offenem
  Fall file_too_large (ueber der Download-Grenze, kein Hash) bleiben im Pruefcenter.

Beide Gruppen: --objekt und --limit wie in den anderen Kommandos; Fehler je Dokument werden gezaehlt und mit
Dokument-ID protokolliert, der Lauf bricht nicht ab; ein zweiter Lauf findet nichts mehr (Fall erledigt).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import Document, DocumentStatus, DocumentSubfolder
from apps.drive import oauth
from apps.pipeline.analysis import GOOGLE_DOC_PREFIX, TEMP_SUFFIXES
from apps.pipeline.jobs import enqueue, idempotency_key
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase

logger = logging.getLogger(__name__)

GRUPPEN = ("temporaer", "archive-medien")
ARCHIV_MEDIEN_EXT = {".zip", ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav"}
RESOLUTION = {"temporaer": "trashed_temp_file", "archive-medien": "filed_manual_check"}
MANUELLE_PRUEFUNG = (
    "06",
    "02",
)  # 06_Sonstiges/02_Manuelle_Pruefung (Katalog db/seeds/document_subfolders.json)
PAPIERKORB_HINWEIS = "Wiederherstellung in Drive innerhalb von 30 Tagen ueber den Papierkorb."


def _endung(doc: Document) -> str:
    return Path(doc.current_name or "").suffix.lower()


def _inhalt_geprueft(case: ReviewCase) -> bool:
    return (case.context or {}).get("content_checked") is True


def _inhalt_erkannt(case: ReviewCase) -> bool:
    """True, wenn die Inhaltspruefung eine Signatur erkannt hat, auch wenn die Art nicht verarbeitbar ist (.ppt,
    .zip, Office-Paket ohne Hauptteil). Kontext content_recognized (seit 26.09.2026); Altfaelle ohne das Feld gelten
    nur dann als unerkannt, wenn die Notiz exakt auf „(Inhalt unbekannt)" endet (sniff ohne Ergebnis); die Notiz
    „(Inhalt: unbekannt)" steht dagegen fuer ein Office-Paket ohne bekannten Hauptteil."""
    context = case.context or {}
    if "content_recognized" in context:
        return context["content_recognized"] is not False
    return not (context.get("note") or "").endswith("(Inhalt unbekannt)")


def _inhalt_bezeichnung(case: ReviewCase) -> str:
    context = case.context or {}
    suffix = context.get("content_suffix")
    if suffix:
        return suffix.lstrip(".")
    note = context.get("note") or ""
    if "(Inhalt: " in note:
        return note.rsplit("(Inhalt: ", 1)[1].rstrip(")")
    return "Container"


def _zu_gross(doc: Document) -> bool:
    return ReviewCase.objects.filter(
        document=doc, case_subtype="file_too_large", status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists()


def _ausschlussgrund(case: ReviewCase, gruppe: str) -> str | None:
    """None, wenn der Fall zur Gruppe gehoert, sonst der Grund fuer die Statistik (kein Dateiname)."""
    doc = case.document
    endung = _endung(doc)
    if gruppe == "temporaer":
        if (doc.mime_type or "").lower().startswith(GOOGLE_DOC_PREFIX):
            return "Google-Dokument"
        if endung not in TEMP_SUFFIXES:
            return "andere Endung"
        if not _inhalt_geprueft(case):
            return "Inhalt noch ungeprueft (formate_wiederaufnehmen --gruppe temporaer)"
        # content_checked steht auch nach einer gescheiterten Office-Umwandlung und bei erkannten, aber nicht
        # verarbeitbaren Signaturen (26.09.2026): solche Dateien sind keine Temporaerdateien
        detected = (case.context or {}).get("detected_kind")
        if detected not in (None, "unsupported"):
            return f"Inhalt erkannt: {detected} (formate_wiederaufnehmen --gruppe office)"
        if _inhalt_erkannt(case):
            return f"Inhalt erkannt: {_inhalt_bezeichnung(case)}, keine Temporaerdatei"
        return None
    if endung not in ARCHIV_MEDIEN_EXT:
        return "andere Endung"
    if _zu_gross(doc):
        return "zu gross (Fall file_too_large)"
    return None


class Command(BaseCommand):
    help = (
        "Restformate bereinigen: Temporaerdateien ohne erkennbaren Inhalt in den Drive-Papierkorb "
        "(gruppe temporaer) oder Archive und Medien nach 06/02 Manuelle Pruefung (gruppe archive-medien)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--gruppe",
            choices=GRUPPEN,
            required=True,
            help="temporaer (Temporaer-Endung oder ohne Endung, Inhalt geprueft und keine Signatur erkannt: "
            "Papierkorb) "
            "oder archive-medien (.zip, .mp4, .mov, .avi, .mkv, .mp3, .wav: Ablage nach 06/02)",
        )
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")
        parser.add_argument("--limit", type=int, default=0, help="hoechstens so viele Faelle (0 = alle)")
        parser.add_argument("--echt", action="store_true", help="ausfuehren statt Vorschau")

    def handle(self, *args, **options):
        nummer = (options["objekt"] or "").strip()
        if nummer and not nummer.isdigit():
            raise CommandError("Objektnummer muss aus Ziffern bestehen")
        gruppe = options["gruppe"]
        echt = bool(options["echt"])
        limit = options["limit"] or 0
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

        ausgewaehlt: list[ReviewCase] = []
        je_endung: Counter = Counter()
        je_objekt: dict[str, Counter] = defaultdict(Counter)
        ausgeschlossen: Counter = Counter()
        begrenzt = 0
        for case in cases.iterator(chunk_size=200):
            grund = _ausschlussgrund(case, gruppe)
            if grund is not None:
                ausgeschlossen[grund] += 1
                continue
            if limit and len(ausgewaehlt) >= limit:
                begrenzt += 1
                continue
            ausgewaehlt.append(case)
            endung = _endung(case.document) or "(ohne)"
            je_endung[endung] += 1
            je_objekt[case.document.object.object_number][endung] += 1

        titel = {
            "temporaer": "Temporaerdateien ohne erkennbaren Inhalt (Papierkorb)",
            "archive-medien": "Archive und Medien (Ablage nach 06/02 Manuelle Pruefung)",
        }[gruppe]
        self.stdout.write(f"{titel}: {len(ausgewaehlt)}")
        if je_endung:
            # Nur Zaehler je Endung und Objekt, keine Dateinamen (Vertraulichkeit)
            self.stdout.write("Nach Endung: " + ", ".join(f"{k} {n}" for k, n in je_endung.most_common()))
            for nr in sorted(je_objekt, key=lambda k: int(k) if k.isdigit() else 0):
                verteilung = ", ".join(f"{k} {n}" for k, n in je_objekt[nr].most_common())
                self.stdout.write(f"Objekt {nr}: {sum(je_objekt[nr].values())} ({verteilung})")
        if ausgeschlossen:
            self.stdout.write(
                f"Nicht in dieser Gruppe: {sum(ausgeschlossen.values())} ("
                + ", ".join(f"{k} {n}" for k, n in ausgeschlossen.most_common())
                + ")"
            )
        if begrenzt:
            self.stdout.write(f"Begrenzung {limit} erreicht, {begrenzt} weitere Faelle nicht ausgewaehlt")
        if not ausgewaehlt:
            self.stdout.write(f"Keine offenen Faelle in der Gruppe {gruppe}.")
            return
        if not echt:
            wirkung = (
                "legt die Drive-Dateien in den Papierkorb und markiert die Dokumente als geloescht; "
                + PAPIERKORB_HINWEIS
                if gruppe == "temporaer"
                else "legt die Dokumente nach 06/02 Manuelle Pruefung ab (Ablagejob)"
            )
            self.stdout.write(f"Vorschau: {len(ausgewaehlt)} Faelle, nichts geaendert (--echt {wirkung})")
            return

        if gruppe == "temporaer":
            drive = oauth.get_adapter()
            if drive is None:
                raise CommandError("Keine Google-Verbindung")
            ergebnis = self._temporaer(ausgewaehlt, drive)
        else:
            unterordner = DocumentSubfolder.objects.filter(
                category_id=MANUELLE_PRUEFUNG[0], code=MANUELLE_PRUEFUNG[1], is_active=True
            ).first()
            if unterordner is None:
                raise CommandError("Unterordner 06/02 Manuelle Pruefung fehlt im Katalog")
            ergebnis = self._archive_medien(ausgewaehlt, unterordner)
        self.stdout.write("Ergebnis: " + ", ".join(f"{k} {n}" for k, n in ergebnis.most_common()))
        if gruppe == "temporaer":
            self.stdout.write(PAPIERKORB_HINWEIS)
        else:
            self.stdout.write(
                "Die Ablage nach 06/02 laeuft als Job (file_to_drive); Stand im Laufmonitor oder mit "
                "verarbeitung_alle_starten --echt nachziehen, falls Jobs warten."
            )

    # ------------------------------------------------------------------ Gruppe temporaer
    def _temporaer(self, faelle: list[ReviewCase], drive) -> Counter:
        ergebnis: Counter = Counter()
        for n, case in enumerate(faelle, start=1):
            doc = case.document
            try:
                stand = self._temporaerdatei_bereinigen(case, doc, drive)
            except Exception as exc:  # je Dokument protokollieren, Lauf nicht abbrechen
                logger.warning(
                    "restformate_bereinigen: Dokument %s nicht bereinigt (%s)", doc.pk, type(exc).__name__
                )
                self.stdout.write(f"Fehler bei Dokument {doc.pk}: {type(exc).__name__}")
                stand = "Fehler"
            ergebnis[stand] += 1
            if n % 200 == 0:
                self.stdout.write(f"... {n} bearbeitet")
                self.stdout.flush()
        return ergebnis

    def _temporaerdatei_bereinigen(self, case: ReviewCase, doc: Document, drive) -> str:
        if not doc.drive_file_id:
            # Upload oder Paperless-Download ohne Ablage: einzige Kopie im Transit, kein Papierkorb vorhanden
            return "ohne Drive-Datei, bleibt in der Pruefung"
        node = drive.get(doc.drive_file_id)
        if node is None:
            stand = "Drive-Datei fehlte bereits"
        elif node.trashed:
            stand = "bereits im Papierkorb"
        else:
            drive.trash(doc.drive_file_id)  # Papierkorb, nie endgueltig
            stand = "in den Papierkorb gelegt"
        now = timezone.now()
        with transaction.atomic():
            Document.objects.filter(pk=doc.pk, deleted_at__isnull=True).update(
                deleted_at=now,
                delete_reason="restformate_bereinigen: Temporaerdatei ohne erkennbaren Inhalt, Drive-Papierkorb",
                updated_at=now,
            )
            self._drive_verknuepfung_papierkorb(doc)
            ReviewCase.objects.filter(pk=case.pk).update(
                status=CaseStatus.RESOLVED,
                resolved_at=now,
                resolution={
                    "action": RESOLUTION["temporaer"],
                    "reason": "restformate_bereinigen",
                    "gruppe": "temporaer",
                    "drive": stand,
                },
            )
            record(
                "pipeline.temp_file_trashed",
                entity_type="document",
                entity_id=doc.pk,
                object_id=doc.object_id,
                after={
                    "case_id": case.pk,
                    "drive_file_id": doc.drive_file_id,
                    "parent": node.parent_id if node is not None else None,
                    "suffix": _endung(doc),
                    "drive": stand,
                },
                reason="Temporaerdatei ohne erkennbaren Inhalt (restformate_bereinigen, Papierkorb 30 Tage)",
            )
        return stand

    @staticmethod
    def _drive_verknuepfung_papierkorb(doc: Document) -> None:
        """Drive-Verknuepfung der Synchronisation (System drive, Rolle original) auf „im Papierkorb" setzen, damit
        der Bestandsabgleich die Datei nicht als verschwunden meldet."""
        from apps.sync.models import ExternalLink, LinkRole, LinkState, SyncSystem

        ExternalLink.objects.filter(document=doc, system=SyncSystem.DRIVE, role=LinkRole.ORIGINAL).exclude(
            state=LinkState.TOMBSTONE
        ).update(
            state=LinkState.TRASHED, state_reason="restformate_bereinigen: in den Drive-Papierkorb gelegt"
        )

    # ------------------------------------------------------------------ Gruppe archive-medien
    def _archive_medien(self, faelle: list[ReviewCase], unterordner: DocumentSubfolder) -> Counter:
        ergebnis: Counter = Counter()
        for n, case in enumerate(faelle, start=1):
            doc = case.document
            try:
                self._manuelle_pruefung_ablegen(case, doc, unterordner)
                stand = "zur Ablage nach 06/02 eingereiht"
            except Exception as exc:  # je Dokument protokollieren, Lauf nicht abbrechen
                logger.warning(
                    "restformate_bereinigen: Dokument %s nicht abgelegt (%s)", doc.pk, type(exc).__name__
                )
                self.stdout.write(f"Fehler bei Dokument {doc.pk}: {type(exc).__name__}")
                stand = "Fehler"
            ergebnis[stand] += 1
            if n % 200 == 0:
                self.stdout.write(f"... {n} bearbeitet")
                self.stdout.flush()
        return ergebnis

    @staticmethod
    def _manuelle_pruefung_ablegen(case: ReviewCase, doc: Document, unterordner: DocumentSubfolder) -> None:
        now = timezone.now()
        payload = {"category": MANUELLE_PRUEFUNG[0], "subfolder": MANUELLE_PRUEFUNG[1]}
        with transaction.atomic():
            doc.category_id = MANUELLE_PRUEFUNG[0]
            doc.subfolder = unterordner
            doc.document_type = None
            doc.status = DocumentStatus.CLASSIFIED
            doc.error_message = None
            doc.save(
                update_fields=[
                    "category",
                    "subfolder",
                    "document_type",
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )
            ReviewCase.objects.filter(pk=case.pk).update(
                status=CaseStatus.RESOLVED,
                resolved_at=now,
                resolution={
                    "action": RESOLUTION["archive-medien"],
                    "reason": "restformate_bereinigen",
                    "gruppe": "archive-medien",
                    "target": payload,
                },
            )
            record(
                "pipeline.manual_check_filed",
                entity_type="document",
                entity_id=doc.pk,
                object_id=doc.object_id,
                after={"case_id": case.pk, "suffix": _endung(doc), "target": payload},
                reason="Archiv oder Mediendatei ungelesen nach 06/02 (restformate_bereinigen)",
            )
            # Ein wartender Ablagejob traegt sonst die alte Zielangabe; enqueue wuerde ihn unveraendert
            # wiederverwenden (Muster apply_decision, 12.09.2026)
            ProcessingJob.objects.filter(
                document=doc, job_type=JobType.FILE_TO_DRIVE, status=JobStatus.PENDING
            ).update(payload=payload, last_error=None, next_attempt_at=None)
            enqueue(
                JobType.FILE_TO_DRIVE,
                doc.object,
                key=idempotency_key(JobType.FILE_TO_DRIVE, doc.object_id, doc.sha256 or f"doc-{doc.pk}"),
                document=doc,
                payload=payload,
            )
