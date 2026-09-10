"""Formulare aus dem Katalog (C-06): je Typ ein passendes Feld, Listen und Objekte als JSON."""

from __future__ import annotations

import json
from decimal import Decimal

from django import forms

from . import store


class SettingForm(forms.Form):
    reason = forms.CharField(label="Begründung", max_length=500, required=False)

    def __init__(self, key: str, current, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key = key
        self.entry = store.catalog()[key]
        vt = self.entry["value_type"]
        nullable = self.entry.get("nullable", False)
        if vt == "boolean":
            field = forms.BooleanField(required=False, initial=bool(current))
        elif vt == "integer":
            field = forms.IntegerField(required=not nullable, initial=current)
        elif vt == "decimal":
            field = forms.DecimalField(required=not nullable, initial=current, decimal_places=4)
        elif vt == "string":
            field = forms.CharField(required=not nullable, initial=current or "", max_length=2000)
        else:
            field = forms.CharField(
                required=not nullable,
                widget=forms.Textarea(attrs={"rows": 8, "class": "mono"}),
                initial=json.dumps(current, ensure_ascii=False, indent=2) if current is not None else "",
            )
        field.label = key
        field.help_text = self.entry["description"]
        self.fields["value"] = field
        self.order_fields(["value", "reason"])

    def clean_value(self):
        raw = self.cleaned_data["value"]
        vt = self.entry["value_type"]
        nullable = self.entry.get("nullable", False)
        if vt in ("list", "object"):
            if raw in ("", None):
                if nullable:
                    return None
                raise forms.ValidationError("Wert fehlt")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise forms.ValidationError(f"Kein gültiges JSON: {exc.msg}") from exc
        elif vt == "decimal":
            value = float(raw) if isinstance(raw, Decimal) else raw
        elif vt == "string":
            value = raw if raw != "" else (None if nullable else "")
        else:
            value = raw
        try:
            store.validate(self.key, value)
        except store.InvalidSetting as exc:
            raise forms.ValidationError(str(exc)) from exc
        return value
