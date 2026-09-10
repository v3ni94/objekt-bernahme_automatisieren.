"""Formulare fuer Eigentuemer und Zuordnungen (M2 Schritt 6). IBAN wird nie gespeichert, nur last4 und Hash (B-18)."""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from apps.objects.models import ManagedObject, Unit
from apps.parties.models import Owner, OwnerUnitAssignment, PartyType
from apps.parties.services import InvalidIban, iban_fields, search_name


class OwnerForm(forms.ModelForm):
    iban = forms.CharField(
        label="IBAN (wird nicht gespeichert, nur letzte vier Stellen und Prüfwert)",
        required=False,
        max_length=42,
    )

    class Meta:
        model = Owner
        fields = [
            "type",
            "salutation",
            "first_name",
            "last_name",
            "company_name",
            "short_name",
            "correspondence_street",
            "correspondence_house_number",
            "correspondence_postal_code",
            "correspondence_city",
            "correspondence_country",
            "correspondence_addition",
            "delivery_street",
            "delivery_house_number",
            "delivery_postal_code",
            "delivery_city",
            "delivery_country",
            "delivery_addition",
            "email",
            "phone",
            "mobile",
            "sepa_mandate_present",
            "sepa_mandate_reference",
            "notes",
        ]
        labels = {
            "type": "Art",
            "salutation": "Anrede",
            "first_name": "Vorname",
            "last_name": "Nachname",
            "company_name": "Firma oder Bezeichnung der Gemeinschaft",
            "short_name": "Firmenkurzname (Ordnerbenennung)",
            "correspondence_street": "Straße",
            "correspondence_house_number": "Hausnummer",
            "correspondence_postal_code": "PLZ",
            "correspondence_city": "Ort",
            "correspondence_country": "Land (ISO-2)",
            "correspondence_addition": "Zusatz (c/o)",
            "delivery_street": "Zustelladresse Straße",
            "delivery_house_number": "Zustelladresse Hausnummer",
            "delivery_postal_code": "Zustelladresse PLZ",
            "delivery_city": "Zustelladresse Ort",
            "delivery_country": "Zustelladresse Land",
            "delivery_addition": "Zustelladresse Zusatz",
            "email": "E-Mail",
            "phone": "Telefon",
            "mobile": "Mobil",
            "sepa_mandate_present": "SEPA-Mandat vorhanden",
            "sepa_mandate_reference": "Mandatsreferenz",
            "notes": "Bemerkung",
        }

    def clean(self):
        data = super().clean()
        kind = data.get("type")
        if kind == PartyType.NATURAL_PERSON and not data.get("last_name"):
            self.add_error("last_name", "Nachname ist bei natürlichen Personen Pflicht.")
        if kind in (PartyType.LEGAL_ENTITY, PartyType.COMMUNITY) and not data.get("company_name"):
            self.add_error("company_name", "Bezeichnung ist bei Firmen und Gemeinschaften Pflicht.")
        try:
            self.iban_data = iban_fields(data.get("iban"))
        except InvalidIban as exc:
            self.add_error("iban", str(exc))
        return data

    def save(self, commit=True):
        owner = super().save(commit=False)
        owner.search_name = search_name(
            type=owner.type,
            first_name=owner.first_name,
            last_name=owner.last_name,
            company_name=owner.company_name,
        )
        if self.cleaned_data.get("iban"):
            owner.iban_last4 = self.iban_data["iban_last4"]
            owner.iban_hash = self.iban_data["iban_hash"]
        if commit:
            owner.save()
        return owner


class AssignmentForm(forms.Form):
    owner = forms.ModelChoiceField(label="Eigentümer", queryset=Owner.active.all())
    unit = forms.ModelChoiceField(label="Einheit", queryset=Unit.objects.none())
    valid_from = forms.DateField(
        label="Eigentumsbeginn (leer = unbekannt)",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    valid_to = forms.DateField(
        label="Eigentumsende (leer = aktuell)", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    share = forms.DecimalField(
        label="Anteil bei Mehrfacheigentum (0,5 = hälftig)",
        required=False,
        max_digits=9,
        decimal_places=6,
        min_value=0,
        max_value=1,
    )
    confirmed = forms.BooleanField(label="Als bestätigt kennzeichnen", required=False, initial=True)
    notes = forms.CharField(label="Bemerkung", required=False, max_length=500)

    def __init__(self, *args, object: ManagedObject, **kwargs):
        super().__init__(*args, **kwargs)
        self.object = object
        self.fields["unit"].queryset = Unit.active.filter(object=object).order_by("unit_label_normalized")

    def clean(self):
        data = super().clean()
        if data.get("valid_from") and data.get("valid_to") and data["valid_from"] > data["valid_to"]:
            raise ValidationError("Eigentumsbeginn liegt nach dem Eigentumsende.")
        if data.get("share") is not None and data["share"] <= 0:
            self.add_error("share", "Anteil größer 0.")
        return data


class EndAssignmentForm(forms.Form):
    valid_to = forms.DateField(
        label="Eigentumsende (letzter Tag)", widget=forms.DateInput(attrs={"type": "date"})
    )
    reason = forms.CharField(label="Grund", required=False, max_length=255)

    def __init__(self, *args, assignment: OwnerUnitAssignment, **kwargs):
        super().__init__(*args, **kwargs)
        self.assignment = assignment

    def clean_valid_to(self):
        value = self.cleaned_data["valid_to"]
        if self.assignment.valid_from and value < self.assignment.valid_from:
            raise ValidationError("Ende liegt vor dem Beginn.")
        return value
