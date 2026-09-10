"""Seeds: sechs Kategorien wortgetreu (05_Eigentümerakte mit Umlaut, F5), elf plus vier Unterordner, Dokumentunterarten
mit Pflichtmetadaten (B-21), sechs Aufbewahrungszeilen ohne Werte; zweiter Lauf ohne Aenderung."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.documents.models import DocumentCategory, DocumentSubfolder, DocumentType, RetentionPolicy

pytestmark = pytest.mark.django_db


def test_katalog_wortgetreu(seeded):
    ordner = list(DocumentCategory.objects.order_by("sort_order").values_list("folder_name", flat=True))
    assert ordner == [
        "01_Legitimationsunterlagen",
        "02_Stammakte",
        "03_Buchhaltung",
        "04_Mieterakte",
        "05_Eigentümerakte",
        "06_Sonstiges",
    ]
    sub05 = list(
        DocumentSubfolder.objects.filter(category_id="05")
        .order_by("sort_order")
        .values_list("folder_name", flat=True)
    )
    assert sub05 == [
        "01_Stammdaten",
        "02_Eigentumsnachweise",
        "03_SEPA",
        "04_Hausgeld",
        "05_Abrechnungen",
        "06_Wirtschaftsplaene",
        "07_Beschluesse",
        "08_Korrespondenz",
        "09_Mahnwesen",
        "10_Vollmachten",
        "11_Sonstiges",
    ]
    sub06 = list(
        DocumentSubfolder.objects.filter(category_id="06")
        .order_by("sort_order")
        .values_list("folder_name", flat=True)
    )
    assert sub06 == ["01_Unklar", "02_Manuelle_Pruefung", "03_Dubletten", "04_Nicht_objektbezogen"]
    assert (
        DocumentSubfolder.objects.filter(category_id__in=["01", "02", "03", "04"]).count() == 0
    )  # F10 offen


def test_dokumentunterarten_pflichtmetadaten(seeded):
    period = set(DocumentType.objects.filter(requires_period=True).values_list("code", flat=True))
    assert {
        "einzelabrechnung",
        "abrechnungsspitze",
        "korrekturabrechnung",
        "einzelwirtschaftsplan",
        "hausgeldvorschuss",
    } <= period
    assert DocumentType.objects.filter(category_id="05", requires_owner=False).count() == 0
    assert DocumentType.objects.get(code="verwaltervollmacht").category_id == "01"
    assert DocumentType.objects.get(code="einzelabrechnung").subfolder.folder_name == "05_Abrechnungen"
    assert DocumentType.objects.filter(category_id="02").count() == 8
    assert DocumentType.objects.count() >= 45


def test_aufbewahrung_ohne_werte(seeded):
    rows = RetentionPolicy.objects.filter(subfolder__isnull=True, document_type__isnull=True)
    assert rows.count() == 6
    assert not rows.exclude(retention_years__isnull=True).exists()
    assert not rows.filter(is_approved=True).exists()


def test_zweiter_lauf_ohne_aenderung(seeded):
    out = StringIO()
    call_command("seed", stdout=out)
    text = out.getvalue()
    assert "0 angelegt, 0 aktualisiert" in text
    for zeile in text.splitlines():
        if zeile.startswith(("document_", "retention_")):
            assert " 0 angelegt, 0 aktualisiert" in zeile, zeile
    assert "Rolle" not in text
