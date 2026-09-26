"""Formatweiche nach Dateiinhalt und HTML als Textseite (26.09.2026): endungslose und temporaer benannte Dateien
werden am Inhalt erkannt und erhalten die massgebliche Endung als Dokumentname (Umbenennung mit Protokoll,
Listenerkennung), HTML liefert digitalen Text ohne OCR, Office-Altformate bleiben ohne LibreOffice mit Vermerk
in der Pruefung (Umwandlung: test_office_altformate.py), formate_wiederaufnehmen kennt die Gruppen temporaer
und html."""

from __future__ import annotations

import json
import shutil
import zipfile
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents import ingest
from apps.documents.models import Document, DocumentPage
from apps.pipeline import analysis, storage
from apps.review.models import CaseStatus, CaseType, ReviewCase

from .conftest import page_lines

pytestmark = pytest.mark.django_db

HTML = (
    '<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>Protokoll</title>'
    "<style>p{color:red}</style></head><body><h1>Beiratssitzung Musterstraße 49</h1>"
    "<p>Tagesordnung: <b>Wirtschaftsplan 2026</b></p><table><tr><td>Summe</td><td>1.234,56 EUR</td></tr></table>"
    "<script>alert(1)</script></body></html>"
)

PARAMS = {"min_chars": 50, "alnum_ratio": 0.6, "cover_ratio": 0.9, "chunk_pages": 20}


def _pdf_bytes(pdf_factory, marker: str) -> bytes:
    return pdf_factory(f"{marker}.pdf", [page_lines(marker, 1, 1)]).read_bytes()


@pytest.fixture
def ohne_soffice(monkeypatch, tmp_path):
    """LibreOffice fehlt (26.09.2026): Altformate bleiben mit Notiz in der Pruefung."""
    monkeypatch.setenv("SOFFICE_BIN", str(tmp_path / "soffice-fehlt"))


def test_formatweiche_inhalt(tmp_path, pdf_factory, ohne_soffice):
    original = tmp_path / "original"  # so heisst die lokale Kopie einer Datei ohne Endung
    original.write_bytes(_pdf_bytes(pdf_factory, "OHNE-ENDUNG"))
    result = analysis.analyze_file(original, None, work=tmp_path, **PARAMS)
    assert result.kind == "pdf" and result.page_count == 1 and result.digital_pages == [1]
    assert result.content_checked is True and result.note == "Format aus Dateiinhalt erkannt: pdf (.pdf)"
    assert "OHNE-ENDUNG" in result.texts[1]

    # Temporaer-Endung mit falschem MIME-Typ: der Inhalt zaehlt, nicht der MIME-Typ
    laden = tmp_path / "Protokoll.crdownload"
    laden.write_text(HTML, encoding="utf-8")
    result = analysis.analyze_file(laden, "application/pdf", work=tmp_path, **PARAMS)
    assert result.kind == "html" and result.content_checked is True
    assert "Beiratssitzung" in result.texts[1] and "alert" not in result.texts[1]

    html = tmp_path / "Protokoll.html"
    html.write_text(HTML, encoding="utf-8")
    result = analysis.analyze_file(html, "text/html", work=tmp_path, **PARAMS)
    assert result.kind == "html" and result.digital_pages == [1] and result.pdf_path is None
    assert result.pages[0].reason == "html_text" and result.origin_kind == "digital"
    assert result.content_checked is False and result.note is None
    assert "Wirtschaftsplan 2026" in result.texts[1] and "Summe 1.234,56 EUR" in result.texts[1]
    assert "color:red" not in result.texts[1]

    alt = tmp_path / "Schreiben.doc"
    alt.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(512))
    result = analysis.analyze_file(alt, "application/msword", work=tmp_path, **PARAMS)
    assert result.kind == "unsupported" and result.note == "Umwandlung nicht verfügbar"
    assert result.content_checked is True and result.page_count == 0 and result.suffix == ".doc"

    archiv = tmp_path / "Archiv.tmp"
    with zipfile.ZipFile(archiv, "w") as zf:
        zf.writestr("liste.txt", "a;b")
    result = analysis.analyze_file(archiv, None, work=tmp_path, **PARAMS)
    assert result.kind == "unsupported" and result.content_checked is True
    assert result.note == "Format nicht unterstützt, manuelle Prüfung (Inhalt: zip)"

    unbekannt = tmp_path / "Rest.indir"
    unbekannt.write_bytes(bytes(range(256)))
    result = analysis.analyze_file(unbekannt, "application/pdf", work=tmp_path, **PARAMS)
    assert result.kind == "unsupported" and result.content_checked is True
    assert result.note == "Format nicht unterstützt, manuelle Prüfung (Inhalt unbekannt)"

    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["Einheit", "Eigentümer"])
    wb.active.append(["WE 1", "Mustermann"])
    tabelle = tmp_path / "tabelle.hed"
    wb.save(str(tabelle))
    result = analysis.analyze_file(tabelle, None, work=tmp_path, **PARAMS)
    assert result.kind == "office" and "WE 1\tMustermann" in result.texts[1]
    assert result.note == "Format aus Dateiinhalt erkannt: office (.xlsx)"


def _dokument_mit_datei(objekt, name: str, data: bytes, mime: str = "application/octet-stream") -> Document:
    """Dokument wie aus dem Drive-Bestand: Datei ohne Uploadpruefung der Endung, Status registriert."""
    target = storage.upload_dir(objekt.pk) / name
    target.write_bytes(data)
    return Document.objects.create(
        object=objekt,
        size_bytes=len(data),
        mime_type=mime,
        original_name=name,
        current_name=name,
        source="upload",
        source_path=str(target),
        status="registered",
        first_seen_at=timezone.now(),
    )


def test_dokument_ohne_endung_durchlaeuft_die_kette(objekt, stammdaten, run_all, pdf_factory):
    doc = _dokument_mit_datei(objekt, "Scan", _pdf_bytes(pdf_factory, "OHNE-ENDUNG"))
    ingest.ensure_run(objekt, documents=[doc])
    run_all(objekt)
    doc.refresh_from_db()
    # Umbenennung nach Inhalt (26.09.2026): Dokumentname, MIME-Typ und lokale Kopie tragen die massgebliche Endung
    assert doc.sha256 and storage.original_path(doc.sha256).name == "original.pdf"
    assert (
        doc.current_name == "Scan.pdf" and doc.original_name == "Scan" and doc.mime_type == "application/pdf"
    )
    assert doc.page_count == 1 and doc.origin_kind == "digital"
    assert doc.status in ("review", "classified", "filed")
    assert not ReviewCase.objects.filter(document=doc, case_subtype="unsupported_format").exists()
    seite = DocumentPage.objects.get(document=doc, page_no=1)
    assert "OHNE-ENDUNG" in seite.text_content and seite.text_source == "text_layer" and not seite.is_scan


def test_html_upload_liefert_text(objekt, stammdaten, run_all):
    doc, _ = ingest.ingest_upload(objekt, filename="Protokoll.html", data=HTML.encode("utf-8"))
    assert doc.mime_type == "text/html"
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.page_count == 1 and doc.origin_kind == "digital"
    assert doc.status in ("review", "classified", "filed")
    assert not ReviewCase.objects.filter(document=doc, case_subtype="unsupported_format").exists()
    seite = DocumentPage.objects.get(document=doc, page_no=1)
    assert "Beiratssitzung" in seite.text_content and "Wirtschaftsplan 2026" in seite.text_content
    assert "alert" not in seite.text_content and seite.text_source == "text_layer"
    assert not storage.previews_dir(doc.pk).exists() or not any(storage.previews_dir(doc.pk).iterdir())


def test_unsupported_fall_traegt_content_checked(objekt, stammdaten, run_all, ohne_soffice):
    zip_bytes = b"PK\x03\x04" + bytes(64)
    archiv = _dokument_mit_datei(objekt, "Archiv.tmp", zip_bytes)
    alt = _dokument_mit_datei(objekt, "Schreiben.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(512))
    ingest.ensure_run(objekt, documents=[archiv, alt])
    run_all(objekt)
    archiv.refresh_from_db()
    alt.refresh_from_db()
    assert archiv.status == "review" and alt.status == "review"
    fall = ReviewCase.objects.get(document=archiv, case_subtype="unsupported_format")
    assert fall.context["content_checked"] is True and fall.context["kind"] == "unsupported"
    assert fall.context["note"] == "Format nicht unterstützt, manuelle Prüfung (Inhalt: zip)"
    # Signatur erkannt (ZIP), Art nicht verarbeitbar: keine Temporaerdatei fuer restformate_bereinigen (26.09.2026)
    assert fall.context["content_recognized"] is True and fall.context["content_suffix"] == ".zip"
    fall_alt = ReviewCase.objects.get(document=alt, case_subtype="unsupported_format")
    assert fall_alt.context["kind"] == "unsupported" and fall_alt.context["content_checked"] is True
    assert fall_alt.context["note"] == "Umwandlung nicht verfügbar"
    # Altformat nach Endung erkannt, Inhalt nicht gesnifft: content_recognized bleibt None, detected_kind zaehlt
    assert (
        fall_alt.context["detected_kind"] == "office_legacy"
        and fall_alt.context["content_recognized"] is None
    )
    # Gruppe temporaer nimmt den bereits geprueften Fall nicht erneut auf, Gruppe bekannt kein Altformat
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "temporaer", stdout=out)
    text = out.getvalue()
    assert "Inhalt bereits geprueft, weiterhin unbekannt: 1 (.tmp 1)" in text
    assert "Keine offenen Faelle in der Gruppe temporaer." in text and "Archiv" not in text
    out = StringIO()
    call_command("formate_wiederaufnehmen", stdout=out)
    text = out.getvalue()
    assert "Office-Altformat (Gruppe office): 1 (.doc 1)" in text
    # Gruppe bekannt fuehrt den geprueften Fall ebenfalls unter "bereits geprueft" (nicht "weiterhin")
    assert "Inhalt bereits geprueft, weiterhin unbekannt: 1 (.tmp 1)" in text and "Schreiben" not in text
    assert "Weiterhin nicht verarbeitbar" not in text


def _altfall(objekt, doc: Document) -> ReviewCase:
    """Zustand vor dem 26.09.2026 nachstellen: Dokument in der Pruefung, Kontext ohne content_checked."""
    doc.status = "review"
    doc.save(update_fields=["status", "updated_at"])
    return ReviewCase.objects.create(
        object=objekt,
        case_type=CaseType.UNCLEAR,
        case_subtype="unsupported_format",
        document=doc,
        batch_key=f"unsupported:{doc.pk}",
        priority=80,
        context={"mime_type": doc.mime_type, "name": doc.current_name},
    )


def test_formate_wiederaufnehmen_gruppen(objekt, stammdaten, run_all, pdf_factory):
    ohne = _dokument_mit_datei(objekt, "Bescheid", _pdf_bytes(pdf_factory, "OHNE-ENDUNG"))
    fall_ohne = _altfall(objekt, ohne)
    tmp = _dokument_mit_datei(objekt, "Rechnung.tmp", _pdf_bytes(pdf_factory, "TEMPORAER"))
    fall_tmp = _altfall(objekt, tmp)
    html = _dokument_mit_datei(objekt, "Protokoll.html", HTML.encode("utf-8"), mime="text/html")
    fall_html = _altfall(objekt, html)
    google = Document.objects.create(
        object=objekt,
        size_bytes=0,
        mime_type="application/vnd.google-apps.document",
        original_name="Notiz",
        current_name="Notiz",
        source="drive_existing",
        drive_file_id="gdoc-1",
        status="review",
        first_seen_at=timezone.now(),
    )
    fall_google = _altfall(objekt, google)

    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "temporaer", stdout=out)
    text = out.getvalue()
    assert (
        "Faelle mit Temporaer-Endung, ohne Endung oder unbekannter Endung (Inhalt noch ungeprueft): 2" in text
    )
    assert "Nach Endung: (ohne) 1, .tmp 1" in text or "Nach Endung: .tmp 1, (ohne) 1" in text
    assert f"Objekt {objekt.object_number}: 2" in text and "Vorschau: 2 Faelle" in text
    assert "Bescheid" not in text and "Rechnung" not in text and "Notiz" not in text
    ohne.refresh_from_db()
    assert ohne.status == "review"

    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "html", stdout=out)
    text = out.getvalue()
    assert "Faelle mit HTML: 1" in text and "Nach Format: html 1" in text and "Nach Endung: .html 1" in text

    # Standardgruppe kennt html als bekanntes Format (Google-Dokument wie bisher), endungslose Dateien nicht
    out = StringIO()
    call_command("formate_wiederaufnehmen", stdout=out)
    text = out.getvalue()
    assert "Faelle mit inzwischen bekanntem Format: 2" in text
    assert "html 1" in text and "google_doc 1" in text and "Nach Endung: .html 1, (ohne) 1" in text
    assert "Weiterhin nicht verarbeitbar: 2 ((ohne) 1, .tmp 1)" in text or "(.tmp 1, (ohne) 1)" in text

    out = StringIO()
    call_command(
        "formate_wiederaufnehmen",
        "--gruppe",
        "temporaer",
        "--objekt",
        objekt.object_number,
        "--echt",
        stdout=out,
    )
    assert "Wieder aufgenommen: 2 Dokumente" in out.getvalue()
    for doc, fall in ((ohne, fall_ohne), (tmp, fall_tmp)):
        doc.refresh_from_db()
        fall.refresh_from_db()
        assert doc.status == "registered" and doc.page_count is None
        assert fall.status == CaseStatus.RESOLVED and fall.resolution["gruppe"] == "temporaer"
        ereignis = AuditEvent.objects.get(action="pipeline.format_reprocess", entity_id=doc.pk)
        assert ereignis.after_state["gruppe"] == "temporaer"
    fall_html.refresh_from_db()
    fall_google.refresh_from_db()
    assert fall_html.status == CaseStatus.OPEN and fall_google.status == CaseStatus.OPEN

    ingest.ensure_run(objekt, documents=[ohne, tmp])
    run_all(objekt)
    for doc, marker in ((ohne, "OHNE-ENDUNG"), (tmp, "TEMPORAER")):
        doc.refresh_from_db()
        assert doc.page_count == 1 and doc.status in ("review", "classified", "filed")
        assert marker in DocumentPage.objects.get(document=doc, page_no=1).text_content
        assert not ReviewCase.objects.filter(
            document=doc, case_subtype="unsupported_format", status=CaseStatus.OPEN
        ).exists()

    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "html", "--echt", stdout=out)
    assert "Wieder aufgenommen: 1 Dokumente" in out.getvalue()
    ingest.ensure_run(objekt, documents=[html])
    run_all(objekt)
    html.refresh_from_db()
    assert html.page_count == 1 and "Beiratssitzung" in DocumentPage.objects.get(document=html).text_content
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "temporaer", stdout=out)
    assert "Keine offenen Faelle in der Gruppe temporaer." in out.getvalue()


def test_upload_nimmt_html_an(objekt, tmp_path):
    with pytest.raises(ingest.IngestError):
        ingest.register_upload(objekt, filename="Datei.crdownload", data=b"%PDF-1.4")
    doc = ingest.register_upload(objekt, filename="Seite.htm", data=b"<html></html>")
    assert doc.mime_type == "text/html" and Path(doc.source_path).exists()
    shutil.rmtree(Path(doc.source_path).parent, ignore_errors=True)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "imports"


def test_umbenennung_nach_inhalt_mit_listenerkennung(objekt, stammdaten, run_all, pdf_factory):
    """Drive-Bestandsdatei Liste.hed mit Excel-Inhalt: das Dokument heisst danach Liste.xlsx mit passendem MIME-Typ,
    die lokale Kopie original.xlsx, die Listenerkennung meldet einen import_candidate, das Protokoll traegt
    document.rename_by_content; Rechnung.pdf.tmp wird Rechnung.pdf ohne doppelte Endung."""
    liste = _dokument_mit_datei(
        objekt, "Liste.hed", (FIXTURES / "eigentuemerliste_generic.xlsx").read_bytes()
    )
    rechnung = _dokument_mit_datei(
        objekt, "Rechnung.pdf.tmp", _pdf_bytes(pdf_factory, "DOPPELT"), mime="application/pdf"
    )
    ingest.ensure_run(objekt, documents=[liste, rechnung])
    run_all(objekt)
    liste.refresh_from_db()
    rechnung.refresh_from_db()
    assert liste.current_name == "Liste.xlsx" and liste.original_name == "Liste.hed"
    assert liste.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert storage.original_path(liste.sha256).name == "original.xlsx"
    assert liste.page_count >= 1 and liste.origin_kind == "digital"  # eine Seite je Blatt
    assert liste.status in ("review", "classified", "filed")
    assert not ReviewCase.objects.filter(document=liste, case_subtype="unsupported_format").exists()
    kandidat = ReviewCase.objects.get(document=liste, case_type=CaseType.IMPORT_CANDIDATE)
    assert (
        kandidat.context["profile"] in ("xlsx_generic", "generic_table")
        and len(kandidat.context["targets"]) >= 3
    )
    ereignis = AuditEvent.objects.get(action="document.rename_by_content", entity_id=liste.pk)
    assert ereignis.before_state["name"] == "Liste.hed" and ereignis.after_state["name"] == "Liste.xlsx"
    assert ereignis.after_state["suffix"] == ".xlsx"
    analyse = json.loads(storage.analysis_path(liste.sha256).read_text(encoding="utf-8"))
    assert analyse["suffix"] == ".xlsx" and analyse["content_checked"] is True
    assert rechnung.current_name == "Rechnung.pdf" and rechnung.mime_type == "application/pdf"
    assert storage.original_path(rechnung.sha256).name == "original.pdf" and rechnung.page_count == 1
    assert "DOPPELT" in DocumentPage.objects.get(document=rechnung, page_no=1).text_content
    # Eine Datei mit korrekter Endung wird nicht umbenannt und nicht protokolliert
    assert AuditEvent.objects.filter(action="document.rename_by_content").count() == 2


def test_wiederaufnahme_unbekannte_endung_und_geprueften_inhalt(objekt, stammdaten, run_all, pdf_factory):
    """Gruppe temporaer nimmt auch sonst unbekannte Endungen auf (.pdf_ mit PDF-Inhalt), die die Formatweiche am
    Inhalt prueft; Gruppe bekannt laesst Faelle mit bereits geprueftem, weiterhin unbekanntem Inhalt liegen, auch
    wenn der MIME-Typ aus Drive ein bekanntes Format nennt (Rechnung.tmp mit application/pdf): sonst liefe die
    Kette im Kreis (Wiederaufnahme, erneute Pruefung, derselbe Fall)."""
    strich = _dokument_mit_datei(objekt, "Abrechnung.pdf_", _pdf_bytes(pdf_factory, "UNTERSTRICH"))
    fall_strich = _altfall(objekt, strich)
    unbekannt = _dokument_mit_datei(objekt, "Rechnung.tmp", bytes(range(256)) * 4, mime="application/pdf")
    ingest.ensure_run(objekt, documents=[unbekannt])
    run_all(objekt)
    unbekannt.refresh_from_db()
    fall_unbekannt = ReviewCase.objects.get(document=unbekannt, case_subtype="unsupported_format")
    assert unbekannt.status == "review" and unbekannt.current_name == "Rechnung.tmp"
    assert (
        fall_unbekannt.context["content_checked"] is True and fall_unbekannt.context["kind"] == "unsupported"
    )
    assert (
        fall_unbekannt.context["content_recognized"] is False
        and fall_unbekannt.context["content_suffix"] is None
    )

    out = StringIO()
    call_command("formate_wiederaufnehmen", stdout=out)
    text = out.getvalue()
    assert "Keine offenen Faelle mit inzwischen bekanntem Format." in text
    assert "Inhalt bereits geprueft, weiterhin unbekannt: 1 (.tmp 1)" in text
    assert "Weiterhin nicht verarbeitbar: 1 (.pdf_ 1)" in text and "Rechnung" not in text

    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "temporaer", stdout=out)
    text = out.getvalue()
    assert (
        "Faelle mit Temporaer-Endung, ohne Endung oder unbekannter Endung (Inhalt noch ungeprueft): 1" in text
    )
    assert (
        "Nach Endung: .pdf_ 1" in text and "Inhalt bereits geprueft, weiterhin unbekannt: 1 (.tmp 1)" in text
    )
    assert "Abrechnung" not in text

    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "temporaer", "--echt", stdout=out)
    assert "Wieder aufgenommen: 1 Dokumente" in out.getvalue()
    fall_strich.refresh_from_db()
    fall_unbekannt.refresh_from_db()
    assert fall_strich.status == CaseStatus.RESOLVED and fall_unbekannt.status == CaseStatus.OPEN
    ingest.ensure_run(objekt, documents=[strich])
    run_all(objekt)
    strich.refresh_from_db()
    assert strich.current_name == "Abrechnung.pdf" and strich.page_count == 1
    assert "UNTERSTRICH" in DocumentPage.objects.get(document=strich, page_no=1).text_content
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "temporaer", stdout=out)
    assert "Keine offenen Faelle in der Gruppe temporaer." in out.getvalue()
