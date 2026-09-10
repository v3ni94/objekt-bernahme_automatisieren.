from django import forms


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Mehrere Dateien in einem Feld (Django-Referenz zu allow_multiple_selected)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, list | tuple):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)]


class DocumentUploadForm(forms.Form):
    files = MultipleFileField(label="Dateien (PDF, Bilder, Office)")
