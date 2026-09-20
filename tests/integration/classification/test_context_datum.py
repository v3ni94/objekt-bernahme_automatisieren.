"""Dokumentdatum aus Entitaeten: unlesbare Normalwerte und fehlende Seitenzahlen duerfen den Klassifizierungsjob
nicht abbrechen (Produktionsfehler 15.09.2026: TypeError beim Sortieren von None neben date)."""

from datetime import date

from apps.classification.context import period_from_entities
from apps.documents.models import DocumentEntity


def _datum(page_no, value_normalized):
    return DocumentEntity(
        page_no=page_no,
        entity_type="date",
        value_text=value_normalized or "",
        value_normalized=value_normalized,
    )


def test_unlesbarer_normalwert_neben_datum_bricht_nicht_ab():
    ref = period_from_entities([_datum(1, "2024-13-45"), _datum(1, "2024-03-05"), _datum(2, "2024-01-01")])
    assert ref.document_date == date(2024, 3, 5)
    assert ref.source == "document_date"


def test_erste_seite_ohne_lesbares_datum_nimmt_naechste_seite():
    ref = period_from_entities([_datum(1, "kein datum"), _datum(2, "2024-01-01")])
    assert ref.document_date == date(2024, 1, 1)


def test_fehlende_seitenzahl_wird_toleriert():
    assert period_from_entities([_datum(None, "2024-02-02")]).document_date == date(2024, 2, 2)
    # Mit Seitenzahl versehene Daten haben Vorrang vor Daten ohne Seitenzahl
    ref = period_from_entities([_datum(None, "2024-02-02"), _datum(3, "2024-05-05")])
    assert ref.document_date == date(2024, 5, 5)


def test_nur_unlesbare_werte_ergeben_kein_datum():
    ref = period_from_entities([_datum(1, "x"), _datum(1, None)])
    assert ref.document_date is None
