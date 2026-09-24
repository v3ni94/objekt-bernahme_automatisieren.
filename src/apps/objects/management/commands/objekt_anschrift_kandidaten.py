"""objekt_anschrift_kandidaten: Anschrift eines Objekts aus den Seitentexten seiner Dokumente vorschlagen und auf
Wunsch setzen (Objekte ohne erfasste Strasse; Befund 24.09.2026: 467, 469, 470, 411 und 523 ohne Strasse, dadurch
konnte die KI den Objektbezug nicht pruefen und die Ablage lief nach 06 Sonstiges).

Quelle sind die maskierten Seitentexte (document_pages.text_content, erste Seiten) der Dokumente des Objekts:
Anschriftenbloecke (Strasse Hausnummer, Zeilenumbruch, PLZ Ort) und Inline-Anschriften (Strasse Hausnummer, PLZ Ort).
Anschriften werden je Strasse und PLZ zu einer Gruppe zusammengefasst (Schreibweisen Str., Strasse, Bindestrich,
Hausnummern eines Gebaeudekomplexes), gezaehlt wird die Zahl der Dokumente. Ein Vorschlag gilt als belegt, wenn
(a) die Strasse in der Objektbezeichnung steht (zwei unabhaengige Quellen), (b) die Anschrift im Text als WEG- oder
Eigentuemergemeinschaftsname vorkommt oder (c) die Gruppe mindestens --min-hits Dokumente hat und dreimal so haeufig
ist wie die zweite. Die Hausnummer kommt aus der Bezeichnung (etwa 67-85), sonst aus der haeufigsten Schreibweise in
den Dokumenten. Fremdanschriften lassen sich mit --ausser ausschliessen. Mit --echt wird die Hauptanschrift nur bei
belegtem Vorschlag und nur bei leerer Strasse gesetzt (nie ueberschreiben), mit Audit object.update und Grund. Ohne
--echt Vorschau. Die Ausgabe nennt keine Dateinamen und keine Personennamen. Serverlauf 24.09.2026 (erste Fassung):
7 Objekte, 0 eindeutig, weil Schreibweisen und Hausnummern getrennt gezaehlt wurden."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

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
    r"(?:stra(?:ß|ss)e|str\.?|weg|allee|platz|gasse|ring|damm|ufer|chaussee|park|promenade|markt|hof|steig|pfad"
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
_WEG_PREFIX = re.compile(
    r"(?i)^(weg|wohnungseigent(?:ue|ü)mergemeinschaft|eigent(?:ue|ü)mergemeinschaft|gemeinschaft der wohnungseigent"
    r"(?:ue|ü)mer)\s+"
)
_ABBREV = re.compile(r"(?i)\bstr\.?$")
_LIST_TOKEN = re.compile(r"^[.,/]\s*\d")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .,;-")


def _norm_street(s: str) -> str:
    """Vergleichsform: Kleinschreibung, ss statt ß, Str. und Strasse gleichgesetzt, ohne Leerzeichen, Bindestriche
    und Punkte (Am Panke-Park und Am Panke Park, Erkelenzer Str und Erkelenzer Straße fallen zusammen)."""
    s = _clean(s).lower().replace("ß", "ss")
    s = re.sub(r"\bstr\.?(?=\s|$)", "strasse", s)
    s = re.sub(r"[\s\-.]+", "", s)
    return re.sub(r"strasse$", "str", s)


def _norm_nr(n: str) -> str:
    return re.sub(r"\s+", "", n).lower().replace("–", "-")


def _norm_name(name: str) -> str:
    return re.sub(r"[\s\-.]+", "", (name or "").lower().replace("ß", "ss"))


def extract_addresses(text: str) -> list[tuple[str, str, str, str, bool]]:
    """Anschriften eines Textes als (Strasse, Hausnummer, PLZ, Ort, WEG-Name), je Text ohne Wiederholung."""
    found: dict[tuple, tuple] = {}
    for rx in (_BLOCK, _INLINE):
        for m in rx.finditer(text or ""):
            street, nr, plz, city = _clean(m["street"]), _norm_nr(m["nr"]), m["plz"], _clean(m["city"])
            weg = bool(_WEG_PREFIX.match(street))
            street = _clean(_WEG_PREFIX.sub("", street, count=1))
            if len(street) < 3 or _BAD_STREET.search(street) or _BAD_CITY.search(city) or plz == "00000":
                continue
            if not re.search(r"[A-Za-zÄÖÜäöüß]{3}", street) or not re.search(r"[A-Za-zÄÖÜäöüß]{3}", city):
                continue
            key = (_norm_street(street), nr, plz)
            alt = found.get(key)
            found[key] = (street, nr, plz, city, weg or bool(alt and alt[4]))
    return list(found.values())


@dataclass
class Gruppe:
    """Eine Strasse mit PLZ: alle Hausnummern und Schreibweisen zusammen."""

    street_norm: str
    postal_code: str
    docs: set = field(default_factory=set)
    streets: Counter = field(default_factory=Counter)  # Schreibweise -> Dokumente
    numbers: Counter = field(default_factory=Counter)  # Hausnummer -> Dokumente
    cities: Counter = field(default_factory=Counter)
    weg: bool = False

    @property
    def count(self) -> int:
        return len(self.docs)

    @property
    def street(self) -> str:
        voll = [(n, s) for s, n in self.streets.items() if not _ABBREV.search(s)]
        if voll:
            return max(voll)[1]
        return self.streets.most_common(1)[0][0]

    @property
    def city(self) -> str:
        return self.cities.most_common(1)[0][0]

    def house_number(self, aus_bezeichnung: str | None = None) -> str:
        if aus_bezeichnung:
            return aus_bezeichnung
        bereiche = [(n, nr) for nr, n in self.numbers.items() if "-" in nr]
        if bereiche and max(bereiche)[0] >= max(self.numbers.values()) * 0.5:
            return max(bereiche)[1]  # ein Bereich wie 67-85 benennt den Gebaeudekomplex
        return self.numbers.most_common(1)[0][0]

    def anzeige(self, aus_bezeichnung: str | None = None) -> str:
        return f"{self.street} {self.house_number(aus_bezeichnung)}, {self.postal_code} {self.city}"


def candidates_for(
    obj, *, max_pages: int = 6000, pages_per_doc: int = 2, ausser=()
) -> tuple[list[Gruppe], int, int]:
    """Gruppen nach Zahl der Dokumente (absteigend), geprueft Dokumente, geprueft Seiten."""
    rows = (
        DocumentPage.objects.filter(
            document__object=obj, document__deleted_at__isnull=True, page_no__lte=pages_per_doc
        )
        .exclude(text_content__isnull=True)
        .exclude(text_content="")
        .order_by("-document_id", "page_no")
        .values_list("document_id", "text_content")[:max_pages]
    )
    gruppen: dict[tuple, Gruppe] = {}
    docs: set[int] = set()
    pages = 0
    ausser_l = [a.lower() for a in ausser if a]
    for doc_id, text in rows:
        pages += 1
        docs.add(doc_id)
        for street, nr, plz, city, weg in extract_addresses(text):
            if any(a in f"{street} {nr} {plz} {city}".lower() for a in ausser_l):
                continue
            key = (_norm_street(street), plz)
            g = gruppen.get(key)
            if g is None:
                g = gruppen[key] = Gruppe(key[0], plz)
            g.docs.add(doc_id)
            g.streets[street] += 1
            g.numbers[nr] += 1
            g.cities[city] += 1
            g.weg = g.weg or weg
    out = sorted(gruppen.values(), key=lambda g: (-g.count, g.street_norm))
    return out, len(docs), pages


def number_from_name(name: str | None, street: str) -> str | None:
    """Hausnummer oder Bereich hinter dem Strassennamen in der Objektbezeichnung (Am Panke Park 67-85 H5 -> 67-85);
    None bei Listen wie 13.15.15a.15b, dann entscheidet die haeufigste Schreibweise in den Dokumenten."""
    if not name or not street:
        return None
    teile = [re.escape(t) for t in re.split(r"[\s\-]+", street.strip()) if t]
    if not teile:
        return None
    muster = r"[\s\-]*".join(teile)
    # in Bezeichnungen folgt der Hausnummer oft ein Gebaeudekuerzel (H5): Buchstabenzusatz nur ohne Leerzeichen
    nr = r"\d{1,4}[a-zA-Z]?(?:\s?[-–]\s?\d{1,4}[a-zA-Z]?)?"
    m = re.search(rf"(?i){muster}\s*(?P<nr>{nr})(?![\w])(?P<rest>[.,/]\s*\d)?", name)
    if not m or m["rest"]:
        return None
    return _norm_nr(m["nr"])


def _begruendung(gruppen: list[Gruppe], obj, min_hits: int) -> tuple[Gruppe | None, str | None]:
    """Belegter Vorschlag und Grund: Bezeichnung, WEG-Name oder Haeufigkeit; sonst (None, None)."""
    if not gruppen:
        return None, None
    name_norm = _norm_name(obj.name)
    passend = next((g for g in gruppen if len(g.street_norm) >= 5 and g.street_norm in name_norm), None)
    if passend is not None and passend.count >= 2:
        return passend, "Bezeichnung"
    top = gruppen[0]
    if top.weg and top.count >= 2:
        return top, "WEG-Name"
    zweite = gruppen[1].count if len(gruppen) > 1 else 0
    if top.count >= min_hits and (zweite == 0 or top.count >= 3 * zweite):
        return top, "Häufigkeit"
    return None, None


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


class Command(BaseCommand):
    help = "Anschrift eines Objekts aus den Seitentexten seiner Dokumente vorschlagen; --echt setzt belegte Vorschlaege"

    def add_arguments(self, parser):
        parser.add_argument(
            "--objekt", default=None, help="Objektnummern (Komma-Liste), sonst alle ohne Strasse"
        )
        parser.add_argument(
            "--ausser", default=None, help="Anschriften mit diesen Textteilen ausschliessen (Komma-Liste)"
        )
        parser.add_argument(
            "--min-hits",
            type=int,
            default=5,
            help="Mindestzahl Dokumente fuer Eindeutigkeit nach Haeufigkeit",
        )
        parser.add_argument("--top", type=int, default=5, help="so viele Strassen je Objekt zeigen")
        parser.add_argument(
            "--seiten", type=int, default=6000, help="hoechstens so viele Seiten je Objekt lesen"
        )
        parser.add_argument(
            "--echt", action="store_true", help="belegte Vorschlaege als Hauptanschrift setzen"
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
        gesetzt = belegt = 0
        for obj in objekte:
            gruppen, n_docs, n_pages = candidates_for(
                obj, max_pages=int(options["seiten"] or 6000), ausser=ausser
            )
            self.stdout.write(
                f"Objekt {obj.object_number} ({obj.management_type}): {_fmt(n_docs)} Dokumente, "
                f"{_fmt(n_pages)} Seiten geprueft"
                + (f", erfasst: {obj.address}" if obj.street else ", keine Strasse erfasst")
            )
            if not gruppen:
                self.stdout.write("  keine Anschrift in den Texten gefunden")
                continue
            wahl, grund = _begruendung(gruppen, obj, min_hits)
            nr_name = number_from_name(obj.name, wahl.street) if wahl is not None else None
            belegt += int(wahl is not None)
            for i, g in enumerate(gruppen[:top], start=1):
                anteil = round(100 * g.count / n_docs) if n_docs else 0
                marke = (
                    f"  <- belegt ({grund})"
                    if g is wahl
                    else ("  <- nicht belegt" if i == 1 and wahl is None else "")
                )
                nummern = ", ".join(f"{nr} ({n})" for nr, n in g.numbers.most_common(6))
                self.stdout.write(
                    f"  {i}. {g.anzeige(nr_name if g is wahl else None)}: {_fmt(g.count)} Dokumente ({anteil} %){marke}"
                )
                if len(g.numbers) > 1 or len(g.streets) > 1:
                    self.stdout.write(
                        f"     Hausnummern: {nummern}"
                        + (f" | Schreibweisen: {', '.join(g.streets)}" if len(g.streets) > 1 else "")
                    )
            if wahl is None:
                continue
            if obj.street:
                self.stdout.write("  Hauptanschrift bereits erfasst, bleibt unveraendert")
                continue
            anzeige = wahl.anzeige(nr_name)
            if not options["echt"]:
                self.stdout.write(f"  Vorschau: wuerde Hauptanschrift setzen: {anzeige} (Grund: {grund})")
                continue
            before = _state(obj)
            neu = dict(before)
            neu["street"], neu["house_number"] = wahl.street, wahl.house_number(nr_name)
            neu["postal_code"] = neu["postal_code"] or wahl.postal_code
            neu["city"] = neu["city"] or wahl.city
            _save(obj, neu, before, f"{REASON}; Grund: {grund}")
            gesetzt += 1
            self.stdout.write(f"  Hauptanschrift gesetzt: {obj.address} (Grund: {grund})")
        self.stdout.write(
            f"{len(objekte)} Objekte geprueft, {belegt} belegt, "
            + (f"{gesetzt} gesetzt" if options["echt"] else "Vorschau, nichts veraendert (mit --echt setzen)")
        )
