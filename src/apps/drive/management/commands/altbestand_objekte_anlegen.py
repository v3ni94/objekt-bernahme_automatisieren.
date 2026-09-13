"""altbestand_objekte_anlegen: Fuer jede Altbestand-Quelle (takeover_sources) mit erkannter Objektnummer und ohne
Objekt einmalig ein Objekt anlegen (Auftrag 13.09.2026: „lege einmalig alle Objekte aus dem Altbestand an, ich pflege
diese nach“). Bezeichnung und Verwaltungsart kommen, wenn vorhanden, aus dem Objektregister
db/seeds/objektregister.csv (Immoware24-Export, Stand 01.07.2026); sonst Bezeichnung aus dem Ordnernamen und
Verwaltungsart nach --verwaltungsart mit Hinweis in den Notizen. Quellordner mit derselben Nummer werden gebunden,
die Ordneranlage in Drive wird wie bei der Anlage von Hand angestossen. Ohne --echt nur Vorschau. Idempotent:
Nummern mit vorhandenem Objekt werden nur gebunden, nie doppelt angelegt."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.forms.models import model_to_dict

from apps.audit.services import record
from apps.drive.models import TakeoverSource
from apps.drive.object_numbers import parse_object_number
from apps.drive.takeover import _number_config, link_source, link_sources_for_object
from apps.drive.tasks import trigger_object_folders
from apps.objects.models import ManagedObject, ManagementType, ObjectStatus

REGISTER_TYPES = {
    "weg-verwaltung": ManagementType.WEG,
    "mietverwaltung": ManagementType.RENTAL,
    "weg mit se-verwaltung": ManagementType.WEG_WITH_SE,
}
REGISTER_STATUS = {
    "aktiv": "aktive Verwaltung",
    "abrechnung": "Verwaltung beendet, nur noch Abrechnung",
    "archiv": "Verwaltung abgegeben (Archiv)",
}


def read_register(path: Path) -> dict[int, dict]:
    """Objektregister als Abbildung Nummer -> {name, management_type, status}; fehlende Datei ergibt leer."""
    if not path.exists():
        return {}
    out: dict[int, dict] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            nummer = (row.get("nummer") or "").strip()
            if not nummer.isdigit():
                continue
            art = REGISTER_TYPES.get((row.get("verwaltungsart") or "").strip().casefold())
            out[int(nummer)] = {
                "name": (row.get("objekt") or "").strip(),
                "management_type": art,
                "status": (row.get("status") or "").strip().casefold(),
            }
    return out


_HOUSE = r"\d+\s*[a-zA-Z]?(?:\s*[-–/]\s*\d+\s*[a-zA-Z]?)*"
_STREET_NR = re.compile(rf"^(?P<street>[^\d]*?[^\d\s])\s+(?P<nr>{_HOUSE})$")
_PLZ_CITY = re.compile(r"^(?:(?P<plz>\d{5})\s+)?(?P<city>[^\d].*)$")
_STREET_NR_PLZ_CITY = re.compile(
    rf"^(?P<street>.*?[^\d\s])\s+(?P<nr>{_HOUSE})\s+(?P<plz>\d{{5}})\s+(?P<city>.+)$"
)


def parse_address(text: str | None) -> dict:
    """Konservative Anschrift aus einer Bezeichnung: „Aachener Straße 25, Erkelenz“ und „Ratheim, Shalomweg 3“
    (Strasse mit Hausnummer und Ort in beliebiger Reihenfolge, durch Komma getrennt), „Am Fließ 6 41812 Erkelenz“
    (mit Postleitzahl) oder nur „Gladbacher Straße 95“ (ohne Ort). Klammerzusaetze werden entfernt. Was nicht
    eindeutig ist, bleibt leer; die Ordneranlage wartet dann auf die Nachpflege."""
    clean = re.sub(r"\s*\([^)]*\)\s*", " ", text or "").strip(" ,;")
    clean = " ".join(clean.split())
    if not clean:
        return {}
    parts = [p.strip() for p in clean.split(",") if p.strip()]
    if len(parts) == 2:
        matches = [(_STREET_NR.match(p), p) for p in parts]
        streets = [(m, p) for m, p in matches if m]
        if len(streets) == 1:
            m, street_part = streets[0]
            other = parts[0] if street_part == parts[1] else parts[1]
            city = _PLZ_CITY.match(other)
            if city:
                return {
                    "street": m.group("street").strip(),
                    "house_number": " ".join(m.group("nr").split()),
                    "postal_code": city.group("plz") or "",
                    "city": city.group("city").strip(),
                }
        return {}
    if len(parts) == 1:
        m = _STREET_NR_PLZ_CITY.match(clean)
        if m:
            return {
                "street": m.group("street").strip(),
                "house_number": " ".join(m.group("nr").split()),
                "postal_code": m.group("plz"),
                "city": m.group("city").strip(),
            }
        m = _STREET_NR.match(clean)
        if m:
            return {"street": m.group("street").strip(), "house_number": " ".join(m.group("nr").split())}
    return {}


def folder_label(name: str | None) -> str:
    """Bezeichnung aus dem Ordnernamen ohne die fuehrende Nummer, etwa „082 Ratheim, Shalomweg 3“ -> „Ratheim, Shalomweg 3“."""
    match = parse_object_number(name or "", **_number_config())
    rest = match.rest if match else (name or "")
    return rest.strip(" _-,.") or (name or "").strip()


class Command(BaseCommand):
    help = "Objekte fuer alle Altbestand-Quellen ohne Objekt anlegen (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Objekte tatsaechlich anlegen")
        parser.add_argument(
            "--register", default=str(Path(settings.REPO_DIR) / "db" / "seeds" / "objektregister.csv")
        )
        parser.add_argument(
            "--verwaltungsart",
            default=ManagementType.WEG,
            choices=[c for c in ManagementType.values],
            help="Verwaltungsart fuer Nummern ohne Eintrag im Objektregister",
        )
        parser.add_argument(
            "--ohne-ordner", action="store_true", help="Ordneranlage in Drive nicht anstossen"
        )

    def handle(self, *args, **options):
        echt: bool = options["echt"]
        register = read_register(Path(options["register"]))
        if not Path(options["register"]).exists():
            self.stdout.write(
                f"Objektregister {options['register']} nicht gefunden; Bezeichnungen aus Ordnernamen"
            )
        default_type = options["verwaltungsart"]
        offen = list(
            TakeoverSource.objects.filter(object__isnull=True).order_by("detected_object_number", "id")
        )
        ohne_nummer = [s for s in offen if not s.detected_object_number]
        gruppen: dict[int, list[TakeoverSource]] = defaultdict(list)
        for src in offen:
            if src.detected_object_number:
                gruppen[int(src.detected_object_number)].append(src)
        if not gruppen and not ohne_nummer:
            self.stdout.write("Keine offenen Altbestand-Quellen; nichts zu tun.")
            return
        angelegt = gebunden = 0
        ordner: dict[str, int] = defaultdict(int)
        self.stdout.write(
            f"{'Anlage' if echt else 'Vorschau'}: {len(gruppen)} Nummern aus {len(offen) - len(ohne_nummer)} Quellordnern, "
            f"Objektregister mit {len(register)} Einträgen"
        )
        for numeric in sorted(gruppen):
            srcs = gruppen[numeric]
            vorhanden = ManagedObject.active.filter(
                object_number_numeric=numeric, is_system_inbox=False
            ).first()
            if vorhanden is not None:
                n = 0
                for src in srcs:
                    if link_source(src):
                        if echt:
                            src.save(update_fields=["object", "status", "updated_at"])
                        n += 1
                gebunden += n
                self.stdout.write(
                    f"  {numeric}: Objekt vorhanden ({vorhanden.name or ''}), {n} Quellordner gebunden"
                )
                continue
            eintrag = register.get(numeric)
            name = (eintrag or {}).get("name") or folder_label(srcs[0].name)
            art = (eintrag or {}).get("management_type") or default_type
            hinweise = [
                "Angelegt aus dem Altbestand (Massenanlage 13.09.2026), Stammdaten bitte nachpflegen."
            ]
            adresse: dict = {}
            for kandidat in [(eintrag or {}).get("name")] + [folder_label(s.name) for s in srcs]:
                geparst = parse_address(kandidat)
                if geparst.get("city") and geparst.get("street"):
                    adresse = geparst
                    break
                if geparst and not adresse:
                    adresse = geparst
            if adresse:
                hinweise.append("Anschrift aus Bezeichnung abgeleitet, bitte prüfen.")
            if not (adresse.get("city") and adresse.get("street") and adresse.get("house_number")):
                hinweise.append(
                    "Anschrift unvollständig; der Objektordner in Drive entsteht nach der Nachpflege."
                )
            if eintrag:
                hinweise.append(
                    f"Objektregister 01.07.2026: {REGISTER_STATUS.get(eintrag['status'], eintrag['status'])}."
                )
                if not eintrag.get("management_type"):
                    hinweise.append("Verwaltungsart nicht aus dem Register bestimmbar, bitte prüfen.")
            else:
                hinweise.append(
                    f"Nicht im Objektregister; Verwaltungsart {ManagementType(art).label} als Vorgabe, bitte prüfen."
                )
            quelle = ", ".join((s.name or s.drive_folder_id) for s in srcs)
            strasse = " ".join(x for x in (adresse.get("street"), adresse.get("house_number")) if x)
            ort = " ".join(x for x in (adresse.get("postal_code"), adresse.get("city")) if x)
            anschrift = ", ".join(x for x in (strasse, ort) if x) or "Anschrift offen"
            self.stdout.write(
                f"  {numeric}: neu „{name}“ ({ManagementType(art).label}"
                f"{', Register ' + eintrag['status'] if eintrag else ', ohne Registereintrag'}; {anschrift}) aus {quelle}"
            )
            if not echt:
                angelegt += 1
                continue
            with transaction.atomic():
                obj = ManagedObject.objects.create(
                    object_number=str(numeric),
                    name=name[:160],
                    street=(adresse.get("street") or "")[:120] or None,
                    house_number=(adresse.get("house_number") or "")[:20] or None,
                    postal_code=(adresse.get("postal_code") or "")[:10] or None,
                    city=(adresse.get("city") or "")[:80] or None,
                    management_type=art,
                    status=ObjectStatus.NEW,
                    notes=" ".join(hinweise),
                )
                record(
                    "object.create",
                    entity_type="object",
                    entity_id=obj.pk,
                    object_id=obj.pk,
                    after={
                        **model_to_dict(obj),
                        "source": "altbestand_bulk",
                        "sources": [s.pk for s in srcs],
                    },
                    actor_type="system",
                )
                gebunden += link_sources_for_object(obj)
            angelegt += 1
            if not options["ohne_ordner"]:
                ordner[trigger_object_folders(obj.pk, trigger="altbestand_bulk")] += 1
        for src in ohne_nummer:
            self.stdout.write(f"  ohne Nummer, bleibt offen: {src.name or src.drive_folder_id}")
        self.stdout.write(
            f"{'Angelegt' if echt else 'Würde anlegen'}: {angelegt} Objekte; Quellordner gebunden: {gebunden}; "
            f"ohne Nummer: {len(ohne_nummer)}" + (f"; Ordneranlage: {dict(ordner)}" if ordner else "")
        )
        if not echt:
            self.stdout.write("Vorschau, nichts geändert. Mit --echt ausführen.")
