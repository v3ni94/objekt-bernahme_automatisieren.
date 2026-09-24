"""objekt_anschrift_kandidaten: Anschrift eines Objekts aus den Seitentexten seiner Dokumente vorschlagen und auf
Wunsch setzen (Objekte ohne erfasste Strasse; Befund 24.09.2026: 467, 469, 470, 411 und 523 ohne Strasse, dadurch
konnte die KI den Objektbezug nicht pruefen und die Ablage lief nach 06 Sonstiges).

Quelle sind die maskierten Seitentexte (document_pages.text_content, erste Seiten) der Dokumente des Objekts:
Anschriftenbloecke (Strasse Hausnummer, Zeilenumbruch, PLZ Ort) und Inline-Anschriften (Strasse Hausnummer, PLZ Ort).
Gezaehlt wird je Anschrift die Zahl der Dokumente, in denen sie vorkommt. Die eigene Firmenanschrift und andere
Fremdanschriften lassen sich mit --ausser ausschliessen. Eindeutig ist ein Vorschlag, wenn er mindestens --min-hits
Dokumente hat und mindestens dreimal so oft vorkommt wie der zweite. Mit --echt wird die Hauptanschrift nur bei
eindeutigem Vorschlag und nur bei leerer Strasse gesetzt (nie ueberschreiben), mit Audit object.update und Grund.
Ohne --echt Vorschau. Die Ausgabe nennt keine Dateinamen und keine Personennamen."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.documents.models import DocumentPage
from apps.objects.management.commands.objekt_anschriften_ergaenzen import _save, _state
from apps.objects.models import ManagedObject

REASON = "Hauptanschrift aus den Anschriften in den Dokumenten (objekt_anschrift_kandidaten)"
_NR = r"\d{1,4} ?[a-zA-Z]?(?: ?[-–/] ?\d{1,4} ?[a-zA-Z]?)?"
_BLOCK = re.compile(
    rf"(?m)^[ \t]*(?P<street>[A-ZÄÖÜ][^\n\d,;:()]{{2,40}}?)[ \t]+(?P<nr>{_NR})[ \t]*,?[ \t]*\n"
    rf"[ \t]*(?P<plz>\d{{5}})[ \t]+(?P<city>[A-ZÄÖÜ][^\n\d,;:()]{{2,40}}?)[ \t]*$"
)
_SUFFIX = (
    r"(?:stra(?:ß|ss)e|str\.|weg|allee|platz|gasse|ring|damm|ufer|chaussee|park|promenade|markt|hof|steig|pfad"
    r"|zeile|berg|tal|garten|h(?:ö|oe)he|kamp|wall|graben|feld|winkel|busch|siedlung|strand|deich|anger)"
)
_INLINE = re.compile(
    rf"(?i)(?P<street>[A-ZÄÖÜa-zäöü][^\n\d,;:()]{{1,40}}?{_SUFFIX})[ \t]+(?P<nr>{_NR})[ \t]*,[ \t]*"
    rf"(?P<plz>\d{{5}})[ \t]+(?P<city>[A-ZÄÖÜ][^\n\d,;:()]{{2,40}}?)(?=[\s,;.]|$)"
)
_BAD_STREET = re.compile(
    r"(?i)\b(herr|frau|firma|familie|eheleute|z\.?\s?hd|c/o|postfach|gmbh|ag|kg|e\.?\s?v\.?|bank|iban|telefon"
    r"|tel\.|fax|seite|blatt|datum|betreff|rechnung|konto|kunden|nr\.|nummer|vom|bis|ab)\b|\[NAME\]|\[|\]"
)
_BAD_CITY = re.compile(r"(?i)\b(gmbh|ag|kg|e\.?\s?v\.?|telefon|tel\.|fax|iban|konto)\b|\[")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .,;-")


def _norm_street(s: str) -> str:
    s = _clean(s).lower().replace("ß", "ss")
    s = re.sub(r"str\.(\s|$)", r"strasse\1", s)
    return re.sub(r"[\s\-]+", "", s)


def _norm_nr(n: str) -> str:
    return re.sub(r"\s+", "", n).lower().replace("–", "-")


def extract_addresses(text: str) -> list[tuple[str, str, str, str]]:
    """Anschriften eines Textes als (Strasse, Hausnummer, PLZ, Ort), je Text ohne Wiederholung."""
    found: dict[tuple, tuple] = {}
    for rx in (_BLOCK, _INLINE):
        for m in rx.finditer(text or ""):
            street, nr, plz, city = _clean(m["street"]), _norm_nr(m["nr"]), m["plz"], _clean(m["city"])
            if len(street) < 3 or _BAD_STREET.search(street) or _BAD_CITY.search(city) or plz == "00000":
                continue
            if not re.search(r"[A-Za-zÄÖÜäöüß]{3}", street) or not re.search(r"[A-Za-zÄÖÜäöüß]{3}", city):
                continue
            key = (_norm_street(street), nr, plz)
            found.setdefault(key, (street, nr, plz, city))
    return list(found.values())


@dataclass
class Kandidat:
    street: str
    house_number: str
    postal_code: str
    city: str
    docs: int

    @property
    def anzeige(self) -> str:
        return f"{self.street} {self.house_number}, {self.postal_code} {self.city}"


def candidates_for(
    obj, *, max_pages: int = 6000, pages_per_doc: int = 2, ausser=()
) -> tuple[list[Kandidat], int, int]:
    """Kandidaten nach Zahl der Dokumente (absteigend), geprueft Dokumente, geprueft Seiten."""
    rows = (
        DocumentPage.objects.filter(
            document__object=obj, document__deleted_at__isnull=True, page_no__lte=pages_per_doc
        )
        .exclude(text_content__isnull=True)
        .exclude(text_content="")
        .order_by("-document_id", "page_no")
        .values_list("document_id", "text_content")[:max_pages]
    )
    docs_by_key: dict[tuple, set] = defaultdict(set)
    anzeige: dict[tuple, tuple] = {}
    docs: set[int] = set()
    pages = 0
    ausser_l = [a.lower() for a in ausser if a]
    for doc_id, text in rows:
        pages += 1
        docs.add(doc_id)
        for street, nr, plz, city in extract_addresses(text):
            if any(a in f"{street} {nr} {plz} {city}".lower() for a in ausser_l):
                continue
            key = (_norm_street(street), nr, plz)
            docs_by_key[key].add(doc_id)
            anzeige.setdefault(key, (street, nr, plz, city))
    out = [Kandidat(*anzeige[k], docs=len(ids)) for k, ids in docs_by_key.items()]
    out.sort(key=lambda k: (-k.docs, k.street, k.house_number))
    return out, len(docs), pages


def is_unambiguous(cands: list[Kandidat], min_hits: int) -> bool:
    if not cands or cands[0].docs < min_hits:
        return False
    return len(cands) == 1 or cands[0].docs >= 3 * cands[1].docs


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


class Command(BaseCommand):
    help = "Anschrift eines Objekts aus den Seitentexten seiner Dokumente vorschlagen; --echt setzt eindeutige Vorschlaege"

    def add_arguments(self, parser):
        parser.add_argument(
            "--objekt", default=None, help="Objektnummern (Komma-Liste), sonst alle ohne Strasse"
        )
        parser.add_argument(
            "--ausser", default=None, help="Anschriften mit diesen Textteilen ausschliessen (Komma-Liste)"
        )
        parser.add_argument(
            "--min-hits", type=int, default=5, help="Mindestzahl Dokumente fuer einen eindeutigen Vorschlag"
        )
        parser.add_argument("--top", type=int, default=5, help="so viele Kandidaten je Objekt zeigen")
        parser.add_argument(
            "--seiten", type=int, default=6000, help="hoechstens so viele Seiten je Objekt lesen"
        )
        parser.add_argument(
            "--echt", action="store_true", help="eindeutige Vorschlaege als Hauptanschrift setzen"
        )

    def handle(self, *args, **options):
        if options["objekt"]:
            nummern = [n.strip() for n in str(options["objekt"]).split(",") if n.strip()]
            if any(not n.isdigit() for n in nummern):
                raise CommandError("Objektnummern muessen aus Ziffern bestehen (Komma-Liste)")
            objekte = list(ManagedObject.objects.filter(deleted_at__isnull=True, object_number__in=nummern))
            fehlend = sorted(set(nummern) - {o.object_number for o in objekte})
            if fehlend:
                raise CommandError("Objekt nicht gefunden: " + ", ".join(fehlend))
        else:
            objekte = list(
                ManagedObject.objects.filter(deleted_at__isnull=True)
                .filter(Q(street__isnull=True) | Q(street=""))
                .order_by("object_number_numeric")
            )
        ausser = [a.strip() for a in str(options["ausser"] or "").split(",") if a.strip()]
        min_hits = max(int(options["min_hits"] or 1), 1)
        top = max(int(options["top"] or 1), 1)
        gesetzt = eindeutig = 0
        for obj in objekte:
            cands, n_docs, n_pages = candidates_for(
                obj, max_pages=int(options["seiten"] or 6000), ausser=ausser
            )
            self.stdout.write(
                f"Objekt {obj.object_number} ({obj.management_type}): {_fmt(n_docs)} Dokumente, "
                f"{_fmt(n_pages)} Seiten geprueft"
                + (f", erfasst: {obj.address}" if obj.street else ", keine Strasse erfasst")
            )
            if not cands:
                self.stdout.write("  keine Anschrift in den Texten gefunden")
                continue
            klar = is_unambiguous(cands, min_hits)
            eindeutig += int(klar)
            for i, k in enumerate(cands[:top], start=1):
                anteil = round(100 * k.docs / n_docs) if n_docs else 0
                marke = "  <- eindeutig" if (i == 1 and klar) else ("  <- nicht eindeutig" if i == 1 else "")
                self.stdout.write(f"  {i}. {k.anzeige}: {_fmt(k.docs)} Dokumente ({anteil} %){marke}")
            weitere = sorted(
                {
                    k.house_number
                    for k in cands[1:]
                    if _norm_street(k.street) == _norm_street(cands[0].street)
                    and k.postal_code == cands[0].postal_code
                }
            )
            if weitere:
                self.stdout.write("  Hinweis: weitere Hausnummern derselben Strasse: " + ", ".join(weitere))
            if not klar:
                continue
            if obj.street:
                self.stdout.write("  Hauptanschrift bereits erfasst, bleibt unveraendert")
                continue
            best = cands[0]
            if not options["echt"]:
                self.stdout.write(f"  Vorschau: wuerde Hauptanschrift setzen: {best.anzeige}")
                continue
            before = _state(obj)
            neu = dict(before)
            neu["street"], neu["house_number"] = best.street, best.house_number
            neu["postal_code"] = neu["postal_code"] or best.postal_code
            neu["city"] = neu["city"] or best.city
            _save(obj, neu, before, REASON)
            gesetzt += 1
            self.stdout.write(f"  Hauptanschrift gesetzt: {obj.address}")
        self.stdout.write(
            f"{len(objekte)} Objekte geprueft, {eindeutig} eindeutig, "
            + (f"{gesetzt} gesetzt" if options["echt"] else "Vorschau, nichts veraendert (mit --echt setzen)")
        )
