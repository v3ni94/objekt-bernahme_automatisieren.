"""objekt_anschrift_kandidaten (24.09.2026): Anschrift eines Objekts ohne Strasse aus den Seitentexten seiner
Dokumente vorschlagen; Schreibweisen und Hausnummern einer Strasse bilden eine Gruppe; belegt durch Bezeichnung,
WEG-Namen oder Haeufigkeit; --echt setzt nur belegte Vorschlaege und nie ueber eine vorhandene Strasse."""

from __future__ import annotations

import hashlib
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents.models import Document, DocumentPage
from apps.objects.management.commands.objekt_anschrift_kandidaten import (
    _norm_street,
    extract_addresses,
    number_from_name,
)
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db
_n = {"i": 0}
BLOCK = (
    "Wohnungseigentümergemeinschaft\nAm Beispielpark 5\n12345 Beispielstadt\n\nSehr geehrte Damen und Herren,"
)
INLINE = "Betreff: Objekt Am Beispielpark 5, 12345 Beispielstadt, Hausgeld 2026"
FREMD = "Verwalterweg 1\n99999 Verwaltstadt\nTelefon [NAME]"
ANDERE = "Musterweg 3a\n54321 Musterdorf"


def _obj(nr, **kw):
    kw.setdefault("name", f"Objekt {nr}")
    return ManagedObject.objects.create(
        object_number=nr, management_type="weg", status="takeover", is_test=True, **kw
    )


def _doc(obj, *pages):
    _n["i"] += 1
    doc = Document.objects.create(
        object=obj,
        sha256=hashlib.sha256(f"{obj.pk}-{_n['i']}".encode()).hexdigest(),
        size_bytes=100,
        mime_type="application/pdf",
        original_name=f"d{_n['i']}.pdf",
        current_name=f"d{_n['i']}.pdf",
        source="upload",
        status="review",
        first_seen_at=timezone.now(),
    )
    for i, text in enumerate(pages, start=1):
        DocumentPage.objects.create(
            document=doc,
            page_no=i,
            text_source="text_layer",
            is_scan=False,
            text_content=text,
            char_count=len(text),
        )
    return doc


def test_extraktion_block_inline_weg_name_und_schreibweisen():
    found = extract_addresses(
        "\n".join(
            [
                BLOCK,
                INLINE,
                FREMD,
                "Herr Mustermann",
                ANDERE,
                "Seite 1\n12345 Ort",
                "WEG Erkelenzer Str 127\n50181 Bedburg",
            ]
        )
    )
    keys = {(s.lower(), n, p) for s, n, p, _c, _w in found}
    assert ("am beispielpark", "5", "12345") in keys
    assert ("verwalterweg", "1", "99999") in keys and ("musterweg", "3a", "54321") in keys
    assert not [f for f in found if "Herr" in f[0] or "Seite" in f[0] or f[0].startswith("WEG")]
    assert (
        len([f for f in found if f[0].lower() == "am beispielpark"]) == 1
    )  # Block und Inline zaehlen einmal
    weg = [f for f in found if f[0] == "Erkelenzer Str"]
    assert weg and weg[0][4] is True and weg[0][1] == "127"
    assert (
        _norm_street("Erkelenzer Straße") == _norm_street("Erkelenzer Str") == _norm_street("Erkelenzer Str.")
    )
    assert _norm_street("Am Panke-Park") == _norm_street("Am Panke Park")
    assert number_from_name("Am Panke Park 67-85 H5", "Am Panke Park") == "67-85"
    assert number_from_name("Am Panke Park 1-21 H1 16321 Bernau", "Am Panke-Park") == "1-21"
    assert (
        number_from_name("Katzemer Straße 13.15.15a.15b", "Katzemer Straße") is None
    )  # Liste: Dokumente entscheiden
    assert number_from_name("Bedburg Projekt", "Erkelenzer Straße") is None


def test_vorschau_und_setzen_bei_haeufigkeit():
    obj = _obj("700")
    for _ in range(3):
        _doc(obj, BLOCK, "Seite 2 ohne Anschrift")
    _doc(obj, FREMD)
    _doc(obj, "Kein Text mit Anschrift")
    out = StringIO()
    call_command("objekt_anschrift_kandidaten", objekt="700", ausser="Verwalterweg", min_hits=3, stdout=out)
    text = out.getvalue()
    assert "Objekt 700 (weg): 5 Dokumente" in text
    assert "1. Am Beispielpark 5, 12345 Beispielstadt: 3 Dokumente (60 %)  <- belegt (Häufigkeit)" in text
    assert "Verwalterweg" not in text and "wuerde Hauptanschrift setzen" in text
    obj.refresh_from_db()
    assert not obj.street

    out = StringIO()
    call_command(
        "objekt_anschrift_kandidaten", objekt="700", ausser="Verwalterweg", min_hits=3, echt=True, stdout=out
    )
    obj.refresh_from_db()
    assert (obj.street, obj.house_number, obj.postal_code, obj.city) == (
        "Am Beispielpark",
        "5",
        "12345",
        "Beispielstadt",
    )
    assert "1 gesetzt" in out.getvalue()
    ereignisse = AuditEvent.objects.filter(
        action="object.update", entity_id=obj.pk, reason__contains="objekt_anschrift_kandidaten"
    )
    assert ereignisse.count() == 1 and "Häufigkeit" in ereignisse.first().reason
    out = StringIO()
    call_command(
        "objekt_anschrift_kandidaten", objekt="700", ausser="Verwalterweg", min_hits=3, echt=True, stdout=out
    )
    assert "bereits erfasst" in out.getvalue() and "0 gesetzt" in out.getvalue()
    assert AuditEvent.objects.filter(action="object.update", entity_id=obj.pk).count() == 1


def test_bezeichnung_belegt_strasse_und_liefert_hausnummernbereich():
    """Serverbefund 24.09.2026: Objekt 470 'Am Panke Park 1-21 H1 16321 Bernau' mit vielen Einzelhausnummern in den
    Dokumenten und einer haeufigeren Fremdanschrift; Bezeichnung und Dokumente zusammen belegen die Anschrift."""
    obj = _obj("701", name="Am Beispielpark 1-21 H1 12345 Beispielstadt")
    for _ in range(2):
        _doc(obj, "Am Beispielpark 19\n12345 Beispielstadt")
    _doc(obj, "Am Beispiel-Park 5\n12345 Beispielstadt")
    for _ in range(4):
        _doc(obj, "Fremdweg 2\n99999 Fremdstadt")
    out = StringIO()
    call_command("objekt_anschrift_kandidaten", objekt="701", stdout=out)
    text = out.getvalue()
    assert "Fremdweg 2, 99999 Fremdstadt: 4 Dokumente" in text
    assert "Am Beispielpark 1-21, 12345 Beispielstadt: 3 Dokumente (43 %)  <- belegt (Bezeichnung)" in text
    assert "Hausnummern: 19 (2), 5 (1)" in text and "Schreibweisen:" in text
    assert "Am Beispiel-Park" in text and "Am Beispielpark" in text
    call_command("objekt_anschrift_kandidaten", objekt="701", echt=True, stdout=StringIO())
    obj.refresh_from_db()
    assert (obj.street, obj.house_number, obj.postal_code, obj.city) == (
        "Am Beispielpark",
        "1-21",
        "12345",
        "Beispielstadt",
    )


def test_weg_name_im_text_belegt_und_bereich_aus_dokumenten():
    """Serverbefund Objekt 523: 'WEG Erkelenzer Str 127' im Text, Schreibweisen Str und Straße, eine haeufige
    Fremdanschrift; ohne Strasse in der Bezeichnung entscheidet der WEG-Name."""
    obj = _obj("702", name="Bedburg Projekt Bauernhof")
    for _ in range(3):
        _doc(obj, "WEG Erkelenzer Str 127\n50181 Bedburg")
    for _ in range(2):
        _doc(obj, "Erkelenzer Straße 127\n50181 Bedburg")
    for _ in range(4):
        _doc(obj, "Dülkenstr 9\n51143 Köln")
    out = StringIO()
    call_command("objekt_anschrift_kandidaten", objekt="702", echt=True, stdout=out)
    text = out.getvalue()
    assert "1. Erkelenzer Straße 127, 50181 Bedburg: 5 Dokumente (56 %)  <- belegt (WEG-Name)" in text
    obj.refresh_from_db()
    assert (obj.street, obj.house_number, obj.postal_code, obj.city) == (
        "Erkelenzer Straße",
        "127",
        "50181",
        "Bedburg",
    )
    # Gebaeudekomplex: der Bereich 67-85 benennt das Objekt, Einzelnummern sind Haeuser darin
    obj2 = _obj("703", name="Objekt 703")
    for _ in range(4):
        _doc(obj2, "Am Beispielpark 67-85\n12345 Beispielstadt")
    for nr in ("83", "85", "69"):
        _doc(obj2, f"Am Beispiel-Park {nr}\n12345 Beispielstadt")
    _doc(obj2, "Fremdweg 2\n99999 Fremdstadt")
    out = StringIO()
    call_command("objekt_anschrift_kandidaten", objekt="703", min_hits=5, stdout=out)
    assert (
        "Am Beispielpark 67-85, 12345 Beispielstadt: 7 Dokumente (88 %)  <- belegt (Häufigkeit)"
        in out.getvalue()
    )


def test_nicht_belegt_wird_nicht_gesetzt():
    obj = _obj("704")
    for _ in range(2):
        _doc(obj, BLOCK)
    for _ in range(2):
        _doc(obj, ANDERE)
    out = StringIO()
    call_command("objekt_anschrift_kandidaten", objekt="704", min_hits=2, echt=True, stdout=out)
    assert "<- nicht belegt" in out.getvalue() and "0 gesetzt" in out.getvalue()
    obj.refresh_from_db()
    assert not obj.street


def test_standardauswahl_nur_objekte_ohne_strasse():
    _obj("705", street="Musterweg", house_number="1")
    ohne = _obj("706")
    _doc(ohne, BLOCK)
    out = StringIO()
    call_command("objekt_anschrift_kandidaten", stdout=out)
    text = out.getvalue()
    assert "Objekt 706" in text and "Objekt 705" not in text
    with pytest.raises(Exception, match="nicht gefunden"):
        call_command("objekt_anschrift_kandidaten", objekt="999")
