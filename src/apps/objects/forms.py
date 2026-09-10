"""Formulare der Objekt- und Einheitenverwaltung (Umsetzungsplan M2 Schritte 5 und 6)."""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from apps.config import store
from apps.drive.object_numbers import normalize_object_number
from apps.objects.models import ManagedObject, Unit
from apps.objects.units import parse_unit_label


class ObjectForm(forms.ModelForm):
    class Meta:
        model = ManagedObject
        fields = [
            "object_number",
            "name",
            "street",
            "house_number",
            "postal_code",
            "city",
            "management_type",
            "status",
            "takeover_from",
            "takeover_to",
            "fiscal_year_start_month",
            "expected_unit_count",
            "sepa_used",
            "special_levies_in_period",
            "previous_manager_name",
            "previous_manager_street",
            "previous_manager_house_number",
            "previous_manager_postal_code",
            "previous_manager_city",
            "previous_manager_contact_person",
            "previous_manager_reference",
            "is_test",
            "notes",
        ]
        labels = {
            "object_number": "Objektnummer",
            "name": "Bezeichnung",
            "street": "Straße",
            "house_number": "Hausnummer",
            "postal_code": "PLZ",
            "city": "Ort",
            "management_type": "Verwaltungsart",
            "status": "Status",
            "takeover_from": "Übernahmezeitraum von",
            "takeover_to": "Übernahmezeitraum bis (Stichtag)",
            "fiscal_year_start_month": "Wirtschaftsjahr beginnt im Monat (1 = Kalenderjahr)",
            "expected_unit_count": "Sollzahl Einheiten",
            "sepa_used": "SEPA-Lastschrift genutzt",
            "special_levies_in_period": "Sonderumlagen im Übernahmezeitraum",
            "previous_manager_name": "Vorverwaltung",
            "previous_manager_street": "Vorverwaltung Straße",
            "previous_manager_house_number": "Vorverwaltung Hausnummer",
            "previous_manager_postal_code": "Vorverwaltung PLZ",
            "previous_manager_city": "Vorverwaltung Ort",
            "previous_manager_contact_person": "Ansprechpartner Vorverwaltung",
            "previous_manager_reference": "Aktenzeichen Vorverwaltung",
            "is_test": "Testobjekt",
            "notes": "Bemerkungen",
        }
        widgets = {
            "takeover_from": forms.DateInput(attrs={"type": "date"}),
            "takeover_to": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_object_number(self) -> str:
        raw = (self.cleaned_data.get("object_number") or "").strip()
        if not raw.isdigit():
            raise ValidationError("Die Objektnummer besteht nur aus Ziffern.")
        lo = int(store.get("drive.object_number_digits_min", 2))
        hi = int(store.get("drive.object_number_digits_max", 6))
        if not lo <= len(raw) <= hi:
            raise ValidationError(f"Die Objektnummer hat {lo} bis {hi} Stellen.")
        pad = store.get("drive.object_number_zero_pad_to")
        number = normalize_object_number(raw, zero_pad_to=int(pad) if pad else None)
        clash = ManagedObject.active.filter(object_number_numeric=int(number))
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError(
                f"Objekt {clash.first().object_number} hat denselben Zahlenwert (Eindeutigkeit über den Zahlenwert)."
            )
        return number

    def clean(self):
        data = super().clean()
        if (
            data.get("takeover_from")
            and data.get("takeover_to")
            and data["takeover_from"] > data["takeover_to"]
        ):
            self.add_error("takeover_to", "Ende liegt vor dem Beginn.")
        month = data.get("fiscal_year_start_month")
        if month is not None and not 1 <= month <= 12:
            self.add_error("fiscal_year_start_month", "Monat 1 bis 12.")
        return data


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = [
            "unit_label",
            "unit_type",
            "co_ownership_share",
            "co_ownership_share_base",
            "building",
            "location",
            "external_ref",
            "house_fee_monthly",
            "status",
            "se_managed",
            "vacancy_confirmed",
            "notes",
        ]
        labels = {
            "unit_label": "Bezeichnung (z. B. WE03, Garage 4)",
            "unit_type": "Einheitentyp (leer = aus Bezeichnung)",
            "co_ownership_share": "Miteigentumsanteil (Zähler)",
            "co_ownership_share_base": "Miteigentumsanteil (Nenner)",
            "building": "Gebäude",
            "location": "Lage",
            "external_ref": "Fremdkennung (z. B. VE-Nr.)",
            "house_fee_monthly": "Hausgeld monatlich",
            "status": "Status",
            "se_managed": "Sondereigentumsverwaltung",
            "vacancy_confirmed": "Leerstand bestätigt",
            "notes": "Bemerkung",
        }

    def __init__(self, *args, object: ManagedObject, **kwargs):
        super().__init__(*args, **kwargs)
        self.object = object
        self.fields["unit_type"].required = False
        self.fields["status"].widget = forms.Select(choices=[("active", "aktiv"), ("inactive", "inaktiv")])

    def clean(self):
        data = super().clean()
        label = (data.get("unit_label") or "").strip()
        if not label:
            return data
        mapping = store.get("units.type_prefix_mapping", {}) or {}
        strip = bool(store.get("units.normalize_strip_leading_zeros", True))
        parsed = parse_unit_label(label, mapping, strip_leading_zeros=strip)
        data["unit_label"] = parsed.label
        self.parsed = parsed
        if not data.get("unit_type"):
            data["unit_type"] = parsed.unit_type
        clash = Unit.active.filter(object=self.object, unit_label_normalized=parsed.label_normalized)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            self.add_error(
                "unit_label",
                f"Einheit {clash.first().unit_label} ist dieselbe Einheit ({parsed.label_normalized}).",
            )
        return data

    def save(self, commit=True):
        unit = super().save(commit=False)
        unit.object = self.object
        unit.unit_number = self.parsed.number
        unit.unit_label_normalized = self.parsed.label_normalized
        if self.parsed.rest and not unit.location:
            unit.location = self.parsed.rest
        if commit:
            unit.save()
        return unit
