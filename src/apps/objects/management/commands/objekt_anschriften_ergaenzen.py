"""objekt_anschriften_ergaenzen: Weitere Anschriften (Eckobjekte, mehrere Hausnummern) aus den Objektbezeichnungen
ableiten, zum Beispiel „Kaiserstraße 77 u. 79, Windmühlenstraße 31“ -> Hauptanschrift Kaiserstraße 77, weitere
Kaiserstraße 79 und Windmühlenstraße 31. Ohne --echt nur Vorschau je Objekt mit Hinweisen (nicht erkannte
Bestandteile, nicht auswertbare Hausnummer der Hauptanschrift). Mit --echt werden fehlende Hauptanschriften aus der
Bezeichnung gesetzt und neue weitere Anschriften ergaenzt; vorhandene Werte werden nie ueberschrieben. Audit
object.update mit Grund. Idempotent: ein zweiter Lauf ergaenzt nichts mehr.

--korrigieren (22.09.2026): Hauptanschriften, die ein frueherer Lauf dieses Kommandos aus der Bezeichnung gesetzt
hat, werden mit der aktuellen Erkennung neu abgeleitet (Ausgangspunkt ist der im Audit festgehaltene Zustand vor
dem ersten Lauf). Angefasst wird nur ein Objekt, dessen Anschriftsfelder noch genau dem Stand des letzten Laufs
entsprechen; wurde seitdem von Hand geaendert, bleibt es mit Hinweis stehen. Ohne --echt Vorschau der Korrekturen.
Anlass: Serverlauf 22.09.2026 mit 29 gesetzten Hauptanschriften, davon 8 falsch zerlegt („Seesen Jacobsonstraße“
als Strasse, „Wanloer Str. 28+30 & An der Sandkaule“ als eine Strasse)."""

from __future__ import annotations

from types import SimpleNamespace

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record
from apps.objects.addresses import additional_from_name, format_address_lines
from apps.objects.models import ManagedObject

REASON = "Weitere Anschriften aus der Bezeichnung (objekt_anschriften_ergaenzen)"
REASON_KORREKTUR = (
    "Hauptanschrift aus der Bezeichnung neu abgeleitet (objekt_anschriften_ergaenzen --korrigieren)"
)
TEXT_FIELDS = ("street", "house_number", "postal_code", "city")
MAX_LEN = {"street": 120, "house_number": 20, "postal_code": 10, "city": 80}


def _state(source) -> dict:
    """Anschriftsfelder eines Objekts oder eines Audit-Zustands, leere Werte als leere Zeichenkette."""
    get = source.get if isinstance(source, dict) else lambda f: getattr(source, f, None)
    return {
        **{f: (get(f) or "") for f in TEXT_FIELDS},
        "additional_addresses": [a for a in (get("additional_addresses") or []) if isinstance(a, dict)],
    }


def _apply(basis: dict, vorschlag: dict) -> dict:
    """Zustand nach Uebernahme eines Vorschlags auf einen Ausgangszustand (dieselben Regeln wie --echt)."""
    neu = _state(basis)
    primary = vorschlag["primary"]
    if primary:
        neu["street"] = primary["street"][:120]
        neu["house_number"] = primary["house_number"][:20]
        if not neu["postal_code"] and primary.get("postal_code"):
            neu["postal_code"] = primary["postal_code"][:10]
        if not neu["city"] and primary.get("city"):
            neu["city"] = primary["city"][:80]
    neu["additional_addresses"] = [*neu["additional_addresses"], *vorschlag["additional"]]
    return neu


def korrektur_basis(obj) -> tuple[dict | None, str | None]:
    """Ausgangszustand vor dem Lauf, der die Hauptanschrift aus der Bezeichnung setzte, oder (None, None), wenn
    die Hauptanschrift nicht von diesem Kommando stammt; (None, Hinweis), wenn das Objekt seit dem letzten Lauf
    von Hand geaendert wurde."""
    events = list(
        AuditEvent.objects.filter(
            action="object.update",
            entity_type="object",
            entity_id=obj.pk,
            reason__contains="objekt_anschriften_ergaenzen",
        ).order_by("occurred_at", "id")
    )
    erster = next((e for e in events if not (e.before_state or {}).get("street")), None)
    if erster is None:
        return None, None
    if _state(obj) != _state(events[-1].after_state or {}):
        return None, "Anschrift seit dem letzten Lauf von Hand geändert, bleibt unverändert"
    return _state(erster.before_state or {}), None


def _save(obj, neu: dict, before: dict, reason: str) -> None:
    with transaction.atomic():
        for f in TEXT_FIELDS:
            setattr(obj, f, (neu[f] or None) and neu[f][: MAX_LEN[f]])
        obj.additional_addresses = list(neu["additional_addresses"])
        obj.save(update_fields=[*TEXT_FIELDS, "additional_addresses", "updated_at"])
        record(
            "object.update",
            entity_type="object",
            entity_id=obj.pk,
            object_id=obj.pk,
            before=before,
            after=_state(obj),
            reason=reason,
        )


class Command(BaseCommand):
    help = "Weitere Anschriften (Eckobjekte) aus den Objektbezeichnungen ableiten; --echt speichert, sonst Vorschau"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Vorschlaege speichern (sonst Vorschau)")
        parser.add_argument(
            "--korrigieren",
            action="store_true",
            help="von diesem Kommando gesetzte Hauptanschriften mit der aktuellen Erkennung neu ableiten "
            "(nur Objekte, die seitdem nicht von Hand geaendert wurden)",
        )

    def handle(self, *args, **options):
        echt = bool(options["echt"])
        korrigieren = bool(options["korrigieren"])
        counts = {
            "geprüft": 0,
            "ergänzt": 0,
            "hauptanschrift": 0,
            "hinweise": 0,
            "korrigiert": 0,
            "gespeichert": 0,
        }
        for obj in ManagedObject.active.filter(is_system_inbox=False).order_by("object_number_numeric"):
            counts["geprüft"] += 1
            kopf = f"Objekt {obj.object_number} „{obj.name or ''}“: "
            if korrigieren:
                basis, hinweis = korrektur_basis(obj)
                if hinweis:
                    counts["hinweise"] += 1
                    self.stdout.write(kopf + "Hinweis: " + hinweis)
                    continue
                if basis is not None:
                    vorschlag = additional_from_name(SimpleNamespace(name=obj.name, **basis))
                    neu = _apply(basis, vorschlag)
                    aktuell = _state(obj)
                    if neu == aktuell:
                        continue
                    counts["korrigiert"] += 1
                    teile = [
                        "Korrektur Hauptanschrift: "
                        f"{format_address_lines([aktuell]) or '(leer)'} -> {format_address_lines([neu]) or '(leer)'}"
                    ]
                    if neu["additional_addresses"]:
                        teile.append(
                            "weitere: "
                            + "; ".join(format_address_lines([a]) for a in neu["additional_addresses"])
                        )
                    if vorschlag["notes"]:
                        counts["hinweise"] += 1
                        teile.append("Hinweis: " + "; ".join(vorschlag["notes"]))
                    self.stdout.write(kopf + " | ".join(teile))
                    if echt:
                        _save(obj, neu, aktuell, REASON_KORREKTUR)
                        counts["gespeichert"] += 1
                    continue
            vorschlag = additional_from_name(obj)
            primary, additional, notes = vorschlag["primary"], vorschlag["additional"], vorschlag["notes"]
            if not (primary or additional or notes):
                continue
            teile = []
            if primary:
                counts["hauptanschrift"] += 1
                teile.append("Hauptanschrift aus Bezeichnung: " + format_address_lines([primary]))
            if additional:
                counts["ergänzt"] += 1
                teile.append("weitere: " + "; ".join(format_address_lines([a]) for a in additional))
            if notes:
                counts["hinweise"] += 1
                teile.append("Hinweis: " + "; ".join(notes))
            self.stdout.write(kopf + " | ".join(teile))
            if echt and (primary or additional):
                before = _state(obj)
                _save(obj, _apply(before, vorschlag), before, REASON)
                counts["gespeichert"] += 1
        self.stdout.write(
            f"Geprüft {counts['geprüft']}, mit weiteren Anschriften {counts['ergänzt']}, Hauptanschrift aus "
            f"Bezeichnung {counts['hauptanschrift']}, mit Hinweisen {counts['hinweise']}"
            + (f", korrigiert {counts['korrigiert']}" if korrigieren else "")
            + (
                f", gespeichert {counts['gespeichert']}"
                if echt
                else ". Vorschau, nichts geändert (--echt speichert)."
            )
        )
