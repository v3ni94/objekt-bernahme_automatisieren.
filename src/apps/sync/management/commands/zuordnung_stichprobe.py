"""zuordnung_stichprobe: Stichprobe der Paperless-Zuordnung als Pruefliste fuer den Sachbearbeiter (Vorlage E-3 in
docs/plan/entscheidungen.md, Nachweis fuer Abnahmepunkt 3; Freigabe 26.09.2026). Anlass: 7.675 Dokumente tragen das
Objekt allein aus dem Paperless-Speicherpfad, eine dokumentierte Kontrolle gegen Objekt, Ordner und Dokumentart in
Drive lag nicht vor. Das Kommando liest nur.

Ziehen: Grundgesamtheit sind abgelegte Dokumente (Status filed, nicht geloescht, kein Eingangsobjekt) mit
Paperless-Verknuepfung (ExternalLink, System paperless, Rolle original; nicht ueber Document.source, weil eine per
Pruefsumme verknuepfte Drive-Bestandsdatei die Quelle drive_existing behaelt). Je Hauptkategorie 01 bis 06 werden
N Dokumente zufaellig gezogen (random.Random(seed).sample ueber die nach ID sortierten Kennungen, damit derselbe
Seed auf demselben Bestand dieselbe Liste ergibt). Ausgabe als CSV unter exports/stichproben/, Spalte pruefung
bleibt leer und wird vom Sachbearbeiter mit richtig, falsch oder unklar gefuellt.

Herkunft des Objektbezugs, soweit aus der Datenbank ermittelbar: feldimport (Feld MHV Objekt beim Pull), pfad
(Feld stammt aus dem Paperless-Speicherpfad, nur mit Paperless-Abfrage erkennbar: Pfadname beginnt mit der
Objektnummer), ki (KI-Schiedsrichter oder KI-Gegenprobe), regel (automatische Zuordnung aus dem Eingang), manuell
(Zuordnung im Dokumenteneingang, Uebernahme durch einen Nutzer oder Feldabgleich nach Handaenderung in Paperless),
drive_bestand (Datei lag schon im Objektordner), upload, sonst unbekannt.

Auswerten: --ergebnis <csv> liest die ausgefuellte Liste, gibt Quote je Kategorie und gesamt sowie die IDs mit
Befund falsch aus und schreibt eine Markdown-Tabelle nach exports/stichproben/ fuer docs/betrieb/abgleich-protokoll.md.

Konsole ohne Titel und Dateinamen (nur Zaehler, IDs, Objektnummern, Pfade); die CSV enthaelt Ordnernamen,
Eigentuemer- und Mieterakten aber nur als Kennung (Aktenordner tragen Personennamen, die Liste bleibt als Beleg;
Gegenpruefung 26.09.2026). Eine Tages-CSV mit Eintraegen in der Spalte pruefung wird nur mit --ueberschreiben
ersetzt; die Auswertung liest UTF-8 und ersatzweise cp1252 (Excel unter Windows speichert ANSI).
"""

from __future__ import annotations

import csv
import io
import random
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents.models import Document
from apps.drive.models import NodeKind, NodeStatus
from apps.objects.models import ManagedObject
from apps.review.models import CaseType, ReviewCase
from apps.sync import services
from apps.sync.models import ExternalLink, LinkRole, SyncSystem
from apps.sync.paperless.errors import PaperlessError
from apps.sync.storage_paths import derive_object_number

KATEGORIEN = ("01", "02", "03", "04", "05", "06")
SPALTEN = [
    "dokument_id",
    "paperless_id",
    "objekt",
    "kategorie",
    "unterordner",
    "dokumentart",
    "drive_pfad",
    "herkunft_objektbezug",
    "pruefung",
    "bemerkung",
]
BEFUNDE = ("richtig", "falsch", "unklar")
MAX_TIEFE = 8  # Elternkette der Drive-Ordner: Objektordner, Hauptordner, Unterordner, Jahresordner, Akten
PAKET = 100  # Paperless-Abfrage id__in je 100 Kennungen (wie paperless_archivfassung)


def export_dir() -> Path:
    path = Path(settings.OBJEKTAKTE["DATA_DIR"]) / "exports" / "stichproben"
    path.mkdir(parents=True, exist_ok=True)
    return path


def standard_seed() -> int:
    """Seed aus dem Tagesdatum (JJJJMMTT), damit ein Lauf am selben Tag wiederholbar ist."""
    return int(timezone.localdate().strftime("%Y%m%d"))


def grundgesamtheit(objekt: ManagedObject | None = None):
    qs = (
        ExternalLink.objects.filter(
            system=SyncSystem.PAPERLESS,
            role=LinkRole.ORIGINAL,
            document__deleted_at__isnull=True,
            document__status="filed",
            document__object__is_system_inbox=False,
            document__object__deleted_at__isnull=True,
            document__category__isnull=False,
        )
        .select_related(
            "document",
            "document__object",
            "document__category",
            "document__subfolder",
            "document__document_type",
            "document__drive_node",
        )
        .order_by("document_id")
    )
    if objekt is not None:
        qs = qs.filter(document__object=objekt)
    return qs


def ziehen(links, je_kategorie: int, seed: int) -> tuple[list[ExternalLink], Counter]:
    """Je Kategorie hoechstens je_kategorie Verknuepfungen; Auswahl deterministisch aus den nach Dokument-ID
    sortierten Kennungen. Liefert die Auswahl (nach Kategorie und ID sortiert) und die Grundgesamtheit je Kategorie."""
    je_kat: dict[str, list[ExternalLink]] = {k: [] for k in KATEGORIEN}
    for link in links:
        code = link.document.category_id
        if code in je_kat:
            je_kat[code].append(link)
    gesamt = Counter({k: len(v) for k, v in je_kat.items()})
    rng = random.Random(seed)
    auswahl: list[ExternalLink] = []
    for code in KATEGORIEN:
        kandidaten = sorted(je_kat[code], key=lambda x: x.document_id)
        n = min(je_kategorie, len(kandidaten))
        gezogen = rng.sample(kandidaten, n) if n else []
        auswahl.extend(sorted(gezogen, key=lambda x: x.document_id))
    return auswahl, gesamt


AKTEN_KENNUNG = {
    NodeKind.OWNER_FILE_FOLDER: "Eigentuemerakte",
    NodeKind.OWNER_FILE_SUBFOLDER: "Eigentuemerakte",
    NodeKind.TENANT_FILE_FOLDER: "Mieterakte",
    NodeKind.TENANT_FILE_SUBFOLDER: "Mieterakte",
}


def ordnername(node) -> str:
    """Name eines Ordners fuer die Pruefliste. Aktenordner tragen in Drive den Namen des Eigentuemers oder
    Mieters (parties.unit_files bildet folder_name aus den Personennamen); sie erscheinen nur als Kennung
    'Eigentuemerakte #<id>' beziehungsweise 'Mieterakte #<id>', Unterordner der Akte mit ihrem Katalognamen
    (expected_name), damit keine Personendaten in die CSV gelangen (26.09.2026)."""
    art = AKTEN_KENNUNG.get(node.node_kind)
    if art is None:
        return node.drive_name or node.expected_name or "?"
    akte_id = node.owner_file_id if art == "Eigentuemerakte" else node.tenant_file_id
    if node.node_kind in (NodeKind.OWNER_FILE_SUBFOLDER, NodeKind.TENANT_FILE_SUBFOLDER):
        return node.expected_name or "?"
    return f"{art} #{akte_id if akte_id is not None else '?'}"


def drive_pfad(doc: Document) -> str:
    """Ordnerkette vom Objektordner bis zum Ablageordner aus drive_nodes; Ordner mit Status missing oder trashed
    werden gekennzeichnet, eine abgebrochene Kette (parent_node leer, Altbestand) beginnt mit '...'."""
    node = doc.drive_node
    if node is None:
        return ""
    teile: list[str] = []
    tiefe = 0
    while node is not None and tiefe < MAX_TIEFE:
        name = ordnername(node)
        if node.status != NodeStatus.ACTIVE:
            name += f" [{node.status}]"
        teile.append(name)
        if node.node_kind in (NodeKind.OBJECT_ROOT, NodeKind.DATA_ROOT):
            break
        node = node.parent_node
        tiefe += 1
    else:
        teile.append("...")
    return "/".join(reversed(teile))


def herkunft_aus_db(doc: Document, link: ExternalLink) -> str:
    """Herkunft des Objektbezugs ohne Paperless-Abfrage; 'feldimport' kann sich mit Paperless zu 'pfad' verfeinern."""
    if doc.source == "moved_in":
        fall = (
            ReviewCase.objects.filter(case_type=CaseType.OBJECT_ASSIGNMENT, document=doc)
            .order_by("-id")
            .first()
        )
        if fall is not None:
            aktion = (fall.resolution or {}).get("action") or ""
            if aktion == "assign_object":
                return "manuell"
            if aktion == "assign_object_ai" or fall.case_subtype in ("ai_auto", "ai_not_object"):
                return "ki"
            if aktion == "assign_object_auto":
                return "regel"
        ereignis = (
            AuditEvent.objects.filter(
                action="review.transfer_object", entity_type="document", entity_id=doc.pk
            )
            .order_by("-id")
            .first()
        )
        if ereignis is not None:
            if ereignis.actor_type == AuditEvent.ActorType.USER:
                return "manuell"
            grund = ereignis.reason or ""
            if grund.startswith("KI-"):
                return "ki"
            if grund.startswith("Feldabgleich"):
                return "manuell"
        return "unbekannt"
    if doc.source == "paperless":
        return "feldimport"
    if doc.source == "drive_existing":
        return "drive_bestand"
    return doc.source or "unbekannt"


def pfad_herkunft(auswahl: list[ExternalLink], herkunft: dict[int, str]) -> tuple[int, str | None]:
    """Verfeinert 'feldimport' zu 'pfad', wenn der Speicherpfad des Paperless-Dokuments mit der Objektnummer
    beginnt (Feld wurde ueber paperless_feld_setzen aus dem Pfad gesetzt). Nur lesende Abfragen, gebuendelt.
    Liefert die Zahl der verfeinerten Zeilen und eine Fehlermeldung, wenn Paperless nicht antwortet."""
    client = services.get_client()
    if client is None:
        return 0, "Paperless nicht konfiguriert, Herkunft pfad nicht ermittelbar"
    kandidaten = {int(x.external_id): x for x in auswahl if herkunft.get(x.document_id) == "feldimport"}
    if not kandidaten:
        return 0, None
    pfade: dict[int, int | None] = {}
    ids = sorted(kandidaten)
    try:
        for start in range(0, len(ids), PAKET):
            for remote in client.list_documents(
                ids=ids[start : start + PAKET], fields=["id", "storage_path"]
            ):
                pfade[int(remote["id"])] = remote.get("storage_path")
    except PaperlessError as exc:
        return 0, f"Paperless nicht erreichbar ({type(exc).__name__}), Herkunft pfad nicht ermittelbar"
    namen: dict[int, str | None] = {}
    verfeinert = 0
    for remote_id, link in kandidaten.items():
        pfad_id = pfade.get(remote_id)
        if pfad_id is None:
            continue
        if pfad_id not in namen:
            try:
                namen[pfad_id] = client.get_storage_path(int(pfad_id)).get("name")
            except PaperlessError:
                namen[pfad_id] = None
        nummer = derive_object_number(namen[pfad_id])
        if nummer is not None and int(nummer) == link.document.object.object_number_numeric:
            herkunft[link.document_id] = "pfad"
            verfeinert += 1
    return verfeinert, None


def zeile(link: ExternalLink, herkunft: str) -> list[str]:
    doc = link.document
    return [
        str(doc.pk),
        str(link.external_id),
        doc.object.object_number,
        doc.category.folder_name if doc.category else "",
        doc.subfolder.folder_name if doc.subfolder else "",
        doc.document_type.name if doc.document_type else "",
        drive_pfad(doc),
        herkunft,
        "",
        "",
    ]


def csv_lesen(pfad: Path) -> str:
    """Inhalt der CSV als Text: UTF-8 (mit oder ohne BOM, so schreibt die Ziehung), ersatzweise cp1252, weil
    Excel unter Windows beim Speichern als CSV (Trennzeichen-getrennt) ANSI verwendet (26.09.2026)."""
    daten = pfad.read_bytes()
    try:
        return daten.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return daten.decode("cp1252")
    except UnicodeDecodeError as exc:
        raise CommandError(
            f"Datei {pfad.name} ist weder UTF-8 noch cp1252 (Position {exc.start}); "
            "in Excel als CSV UTF-8 speichern"
        ) from exc


def pruefergebnisse_vorhanden(pfad: Path) -> bool:
    """True, wenn die CSV mindestens eine Zeile mit Eintrag in der Spalte pruefung enthaelt (Schutz vor dem
    Ueberschreiben einer bereits bearbeiteten Tagesliste, 26.09.2026). Unlesbare Dateien gelten als bearbeitet."""
    if not pfad.is_file():
        return False
    try:
        reader = csv.DictReader(io.StringIO(csv_lesen(pfad), newline=""), delimiter=";")
        if "pruefung" not in (reader.fieldnames or []):
            return False
        return any((row.get("pruefung") or "").strip() for row in reader)
    except (CommandError, csv.Error, OSError):
        return True


def auswerten(pfad: Path) -> dict:
    """Liest die ausgefuellte Liste; Befund aus der Spalte pruefung (richtig, falsch, unklar, sonst offen)."""
    with io.StringIO(csv_lesen(pfad), newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fehlend = [s for s in ("dokument_id", "kategorie", "pruefung") if s not in (reader.fieldnames or [])]
        if fehlend:
            raise CommandError(f"Spalten fehlen in {pfad.name}: {', '.join(fehlend)}")
        je_kat: dict[str, Counter] = {}
        falsch: list[str] = []
        for row in reader:
            code = (row.get("kategorie") or "")[:2] or "??"
            befund = (row.get("pruefung") or "").strip().lower()
            if befund not in BEFUNDE:
                befund = "offen"
            z = je_kat.setdefault(code, Counter())
            z["gezogen"] += 1
            z[befund] += 1
            if befund == "falsch":
                falsch.append((row.get("dokument_id") or "").strip())
    gesamt: Counter = Counter()
    for z in je_kat.values():
        gesamt.update(z)
    return {"je_kategorie": dict(sorted(je_kat.items())), "gesamt": gesamt, "falsch": falsch}


def quote(z: Counter) -> str:
    geprueft = z["richtig"] + z["falsch"] + z["unklar"]
    if not geprueft:
        return "-"
    return f"{100 * z['richtig'] / geprueft:.1f}".replace(".", ",") + " %"


def markdown(ergebnis: dict, quelle: Path, datum) -> str:
    zeilen = [
        f"## Stichprobe der Paperless-Zuordnung, Auswertung vom {datum:%d.%m.%Y}",
        "",
        f"Quelle: `{quelle.name}`. Quote richtig bezogen auf die geprüften Zeilen (richtig, falsch, unklar); "
        "offen sind Zeilen ohne Eintrag in der Spalte pruefung.",
        "",
        "| Kategorie | gezogen | geprüft | richtig | falsch | unklar | offen | Quote richtig |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for code, z in ergebnis["je_kategorie"].items():
        geprueft = z["richtig"] + z["falsch"] + z["unklar"]
        zeilen.append(
            f"| {code} | {z['gezogen']} | {geprueft} | {z['richtig']} | {z['falsch']} | {z['unklar']} | "
            f"{z['offen']} | {quote(z)} |"
        )
    g = ergebnis["gesamt"]
    geprueft = g["richtig"] + g["falsch"] + g["unklar"]
    zeilen.append(
        f"| gesamt | {g['gezogen']} | {geprueft} | {g['richtig']} | {g['falsch']} | {g['unklar']} | "
        f"{g['offen']} | {quote(g)} |"
    )
    zeilen.append("")
    ids = ", ".join(ergebnis["falsch"]) or "keine"
    zeilen.append(f"Dokument-IDs mit Befund falsch: {ids}")
    zeilen.append("")
    return "\n".join(zeilen)


class Command(BaseCommand):
    help = (
        "Stichprobe der Paperless-Zuordnung: je Hauptkategorie N abgelegte Dokumente als CSV-Pruefliste ziehen "
        "(nur lesend); --ergebnis <csv> wertet die ausgefuellte Liste aus"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--je-kategorie", type=int, default=50, help="Dokumente je Kategorie (Standard 50)"
        )
        parser.add_argument(
            "--seed", type=int, help="Seed der Zufallsauswahl (Standard: Tagesdatum JJJJMMTT)"
        )
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")
        parser.add_argument(
            "--csv", help="Pfad der CSV (Standard exports/stichproben/<JJJJ-MM-TT>_stichprobe.csv)"
        )
        parser.add_argument(
            "--ohne-paperless", action="store_true", help="keine Paperless-Abfrage (Herkunft pfad entfaellt)"
        )
        parser.add_argument(
            "--ueberschreiben",
            action="store_true",
            help="CSV auch dann ersetzen, wenn sie schon Eintraege in der Spalte pruefung enthaelt",
        )
        parser.add_argument("--ergebnis", help="ausgefuellte CSV auswerten statt ziehen")
        parser.add_argument(
            "--markdown", help="Pfad der Auswertung (Standard exports/stichproben/<JJJJ-MM-TT>_ergebnis.md)"
        )

    def handle(self, *args, **options):
        if options["ergebnis"]:
            self._auswerten(options)
            return
        self._ziehen(options)

    def _ziehen(self, options) -> None:
        je = int(options["je_kategorie"] or 0)
        if je <= 0:
            raise CommandError("--je-kategorie muss groesser als 0 sein")
        objekt = None
        nummer = (options["objekt"] or "").strip()
        if nummer:
            if not nummer.isdigit():
                raise CommandError("Objektnummer muss aus Ziffern bestehen")
            objekt = ManagedObject.active.filter(
                object_number_numeric=int(nummer), is_system_inbox=False
            ).first()
            if objekt is None:
                raise CommandError(f"Objekt {nummer} nicht gefunden")
        seed = options["seed"] if options["seed"] is not None else standard_seed()
        datum = timezone.localdate()
        pfad = Path(options["csv"]) if options["csv"] else export_dir() / f"{datum:%Y-%m-%d}_stichprobe.csv"
        if not options["ueberschreiben"] and pruefergebnisse_vorhanden(pfad):
            raise CommandError(
                f"{pfad} enthaelt bereits Pruefergebnisse; anderen Pfad mit --csv waehlen oder --ueberschreiben"
            )
        links = list(grundgesamtheit(objekt).iterator(chunk_size=200))
        auswahl, gesamt = ziehen(links, je, seed)
        herkunft = {x.document_id: herkunft_aus_db(x.document, x) for x in auswahl}
        hinweis = None
        if not options["ohne_paperless"]:
            _, hinweis = pfad_herkunft(auswahl, herkunft)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with pfad.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";", lineterminator="\r\n")
            writer.writerow(SPALTEN)
            for link in auswahl:
                writer.writerow(zeile(link, herkunft[link.document_id]))
        gezogen = Counter(x.document.category_id for x in auswahl)
        self.stdout.write(
            f"Stichprobe (Seed {seed}, je Kategorie {je}"
            + (f", Objekt {objekt.object_number}" if objekt else "")
            + "): "
            + ", ".join(f"{k} {gezogen[k]} von {gesamt[k]}" for k in KATEGORIEN)
        )
        herkunft_zaehler = Counter(herkunft.values())
        self.stdout.write(
            f"Gezogen: {len(auswahl)} von {sum(gesamt.values())}; Herkunft: "
            + (", ".join(f"{k} {v}" for k, v in sorted(herkunft_zaehler.items())) or "keine")
        )
        if hinweis:
            self.stdout.write(f"Hinweis: {hinweis}")
        self.stdout.write(f"CSV: {pfad}")

    def _auswerten(self, options) -> None:
        quelle = Path(options["ergebnis"])
        if not quelle.is_file():
            raise CommandError(f"Ergebnisdatei nicht gefunden: {quelle}")
        ergebnis = auswerten(quelle)
        datum = timezone.localdate()
        for code, z in ergebnis["je_kategorie"].items():
            self.stdout.write(
                f"Kategorie {code}: gezogen {z['gezogen']}, richtig {z['richtig']}, falsch {z['falsch']}, "
                f"unklar {z['unklar']}, offen {z['offen']}, Quote richtig {quote(z)}"
            )
        g = ergebnis["gesamt"]
        self.stdout.write(
            f"Gesamt: gezogen {g['gezogen']}, richtig {g['richtig']}, falsch {g['falsch']}, "
            f"unklar {g['unklar']}, offen {g['offen']}, Quote richtig {quote(g)}"
        )
        self.stdout.write("Dokument-IDs mit Befund falsch: " + (", ".join(ergebnis["falsch"]) or "keine"))
        ziel = (
            Path(options["markdown"])
            if options["markdown"]
            else export_dir() / f"{datum:%Y-%m-%d}_ergebnis.md"
        )
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(markdown(ergebnis, quelle, datum), encoding="utf-8")
        self.stdout.write(f"Auswertung: {ziel}")
