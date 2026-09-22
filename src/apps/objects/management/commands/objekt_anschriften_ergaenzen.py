"""objekt_anschriften_ergaenzen: Weitere Anschriften (Eckobjekte, mehrere Hausnummern) aus den Objektbezeichnungen
ableiten, zum Beispiel „Kaiserstraße 77 u. 79, Windmühlenstraße 31“ -> Hauptanschrift Kaiserstraße 77, weitere
Kaiserstraße 79 und Windmühlenstraße 31. Ohne --echt nur Vorschau je Objekt mit Hinweisen (nicht erkannte
Bestandteile, nicht auswertbare Hausnummer der Hauptanschrift). Mit --echt werden fehlende Hauptanschriften aus der
Bezeichnung gesetzt und neue weitere Anschriften ergaenzt; vorhandene Werte werden nie ueberschrieben. Audit
object.update mit Grund. Idempotent: ein zweiter Lauf ergaenzt nichts mehr."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.audit.services import record
from apps.objects.addresses import additional_from_name, format_address_lines
from apps.objects.models import ManagedObject


class Command(BaseCommand):
    help = "Weitere Anschriften (Eckobjekte) aus den Objektbezeichnungen ableiten; --echt speichert, sonst Vorschau"

    def add_arguments(self, parser):
        parser.add_argument("--echt", action="store_true", help="Vorschlaege speichern (sonst Vorschau)")

    def handle(self, *args, **options):
        echt = bool(options["echt"])
        counts = {"geprüft": 0, "ergänzt": 0, "hauptanschrift": 0, "hinweise": 0, "gespeichert": 0}
        for obj in ManagedObject.active.filter(is_system_inbox=False).order_by("object_number_numeric"):
            counts["geprüft"] += 1
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
            self.stdout.write(f"Objekt {obj.object_number} „{obj.name or ''}“: " + " | ".join(teile))
            if echt and (primary or additional):
                before = {
                    "street": obj.street,
                    "house_number": obj.house_number,
                    "postal_code": obj.postal_code,
                    "city": obj.city,
                    "additional_addresses": list(obj.additional_addresses or []),
                }
                with transaction.atomic():
                    fields = ["additional_addresses", "updated_at"]
                    if primary:
                        obj.street = primary["street"][:120]
                        obj.house_number = primary["house_number"][:20]
                        if not obj.postal_code and primary.get("postal_code"):
                            obj.postal_code = primary["postal_code"][:10]
                        if not obj.city and primary.get("city"):
                            obj.city = primary["city"][:80]
                        fields += ["street", "house_number", "postal_code", "city"]
                    obj.additional_addresses = [*(obj.additional_addresses or []), *additional]
                    obj.save(update_fields=fields)
                    record(
                        "object.update",
                        entity_type="object",
                        entity_id=obj.pk,
                        object_id=obj.pk,
                        before=before,
                        after={
                            "street": obj.street,
                            "house_number": obj.house_number,
                            "postal_code": obj.postal_code,
                            "city": obj.city,
                            "additional_addresses": list(obj.additional_addresses),
                        },
                        reason="Weitere Anschriften aus der Bezeichnung (objekt_anschriften_ergaenzen)",
                    )
                counts["gespeichert"] += 1
        self.stdout.write(
            f"Geprüft {counts['geprüft']}, mit weiteren Anschriften {counts['ergänzt']}, Hauptanschrift aus "
            f"Bezeichnung {counts['hauptanschrift']}, mit Hinweisen {counts['hinweise']}"
            + (
                f", gespeichert {counts['gespeichert']}"
                if echt
                else ". Vorschau, nichts geändert (--echt speichert)."
            )
        )
