from __future__ import annotations

from django import forms

from apps.imports.mapping import TARGET_FIELDS
from apps.imports.models import ImportKind
from apps.imports.profiles import PROFILES


class UploadForm(forms.Form):
    file = forms.FileField(label="Datei (CSV, Excel, Immoware24-Export)")
    import_kind = forms.ChoiceField(
        label="Art der Liste", choices=ImportKind.choices, initial=ImportKind.OWNER_LIST
    )


class ReparseForm(forms.Form):
    profile = forms.ChoiceField(label="Formatprofil", choices=[(c, c) for c in PROFILES])
    sheet = forms.CharField(label="Blatt (Excel)", required=False)


def mapping_form_class(columns: list[dict]):
    """Ein Auswahlfeld je Quellspalte; Zielfeld leer bedeutet nicht übernehmen."""
    choices = [("", "nicht übernehmen"), *[(k, v) for k, v in TARGET_FIELDS.items()]]
    fields = {}
    for col in columns:
        fields[f"col_{col['source_index']}"] = forms.ChoiceField(
            label=col["source_header"] or f"Spalte {col['source_index'] + 1}",
            choices=choices,
            required=False,
            initial=col.get("target") or "",
            help_text=f"Vorschlag {col.get('target') or 'keiner'} ({int(round((col.get('confidence') or 0) * 100))} %); Beispiele: "
            + ", ".join(col.get("samples") or [])[:120],
        )
    return type("MappingForm", (forms.Form,), fields)


class RowDecisionForm(forms.Form):
    action = forms.ChoiceField(label="Entscheidung", choices=[])
    owner_id = forms.IntegerField(label="Bestehender Eigentümer (ID)", required=False)
    first_name = forms.CharField(label="Vorname", required=False, max_length=120)
    last_name = forms.CharField(label="Nachname", required=False, max_length=120)
    company_name = forms.CharField(label="Firma oder Gemeinschaft", required=False, max_length=200)
    unit_label = forms.CharField(label="Einheit", required=False, max_length=80)
    valid_from = forms.DateField(
        label="Eigentumsbeginn", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    reason = forms.CharField(label="Begründung", required=False, max_length=255)

    ACTION_LABELS = {
        "accept": "übernehmen",
        "reject": "nicht übernehmen",
        "use_candidate": "bestehenden Eigentümer verwenden",
        "new_owner": "neuen Eigentümer anlegen",
        "change_owner": "Eigentümerwechsel (alte Zuordnung beenden)",
        "co_owner": "Mehrfacheigentum (zweite Zuordnung)",
        "duplicate": "Dublette (bestehender Eigentümer)",
        "as_community": "als Gemeinschaft mit Gesamttext übernehmen",
    }

    def __init__(self, *args, options: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        opts = options or ["accept", "reject"]
        self.fields["action"].choices = [(o, self.ACTION_LABELS.get(o, o)) for o in opts]
