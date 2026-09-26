"""Office-Altformate in der Kette (26.09.2026, Vorlage E-1): die Umwandlung ueber LibreOffice ist durch einen
Fake ersetzt, der eine synthetische PDF in das Ausgabeverzeichnis schreibt. Geprueft werden die Kette (Textebene
ohne OCR, OCR nur fuer Scan-Seiten, Seitenbilder, Original bleibt .doc), der Fall mit Notiz ohne LibreOffice oder
bei gescheiterter Umwandlung, das Zeitlimit aus der Konfiguration und die Gruppe office von
formate_wiederaufnehmen."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents import ingest
from apps.documents.models import Document, DocumentPage
from apps.pipeline import analysis, office_convert, sniff, storage
from apps.review.models import CaseStatus, ReviewCase

from .conftest import page_lines, write_pdf, write_scan

pytestmark = pytest.mark.django_db

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(512)
PARAMS = {"min_chars": 50, "alnum_ratio": 0.6, "cover_ratio": 0.9, "chunk_pages": 20}


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


@pytest.fixture
def fake_convert(monkeypatch):
    """Ersetzt office_convert.convert_to_pdf: schreibt <stem>.pdf mit dem Text aus texte[name] (scan: Scan-PDF)
    in das Ausgabeverzeichnis und zeichnet die Aufrufe auf."""
    calls: list[dict] = []
    texte: dict[str, tuple[str, bool]] = {}

    def _fake(path: Path, out_dir: Path, timeout_s: int = 120) -> Path:
        calls.append({"path": path, "out_dir": out_dir, "timeout_s": timeout_s})
        marker, scan = texte.get(path.read_bytes(), ("ALTFORMAT", False))
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (path.stem + ".pdf")
        pages = [page_lines(marker, 1, 1)]
        return write_scan(target, pages) if scan else write_pdf(target, pages)

    monkeypatch.setattr(office_convert, "convert_to_pdf", _fake)
    return {"calls": calls, "texte": texte}


@pytest.fixture
def ohne_soffice(monkeypatch, tmp_path):
    monkeypatch.setenv("SOFFICE_BIN", str(tmp_path / "soffice-fehlt"))


def test_analyse_altformat_als_pdf(tmp_path, fake_convert):
    alt = tmp_path / "Schreiben.doc"
    alt.write_bytes(OLE)
    result = analysis.analyze_file(alt, "application/msword", work=tmp_path, office_timeout_s=77, **PARAMS)
    assert result.kind == "office_legacy" and result.page_count == 1 and result.digital_pages == [1]
    assert result.origin_kind == "digital" and result.ocr_pages == [] and result.chunks == []
    assert result.pdf_path == str(tmp_path / "converted.pdf") and Path(result.pdf_path).exists()
    assert "ALTFORMAT" in result.texts[1] and result.content_checked is False and result.suffix == ".doc"
    aufruf = fake_convert["calls"][0]
    assert aufruf["out_dir"] == tmp_path / "convert" and aufruf["timeout_s"] == 77
    assert not (tmp_path / "convert" / "Schreiben.pdf").exists()  # nach work/converted.pdf verschoben
    # Endungslose Datei mit RTF-Inhalt: Inhaltspruefung liefert .rtf, Umwandlung laeuft ebenfalls
    ohne = tmp_path / "original"
    ohne.write_bytes(b"{\\rtf1\\ansi Testtext}")
    result = analysis.analyze_file(ohne, None, work=tmp_path, **PARAMS)
    assert result.kind == "office_legacy" and result.content_checked is True and result.suffix == ".rtf"
    assert result.note == "Format aus Dateiinhalt erkannt: office_legacy (.rtf)"
    assert result.page_count == 1 and len(fake_convert["calls"]) == 2


def test_analyse_ohne_libreoffice_und_bei_fehlschlag(tmp_path, ohne_soffice, monkeypatch):
    alt = tmp_path / "Tabelle.xls"
    alt.write_bytes(OLE)
    result = analysis.analyze_file(alt, "application/vnd.ms-excel", work=tmp_path, **PARAMS)
    assert result.kind == "unsupported" and result.note == "Umwandlung nicht verfügbar"
    assert result.content_checked is True and result.page_count == 0 and result.suffix == ".xls"

    def _scheitert(path, out_dir, timeout_s=120):
        raise office_convert.ConversionFailed("Rückgabecode 1")

    monkeypatch.setattr(office_convert, "convert_to_pdf", _scheitert)
    result = analysis.analyze_file(alt, "application/vnd.ms-excel", work=tmp_path, **PARAMS)
    assert result.kind == "unsupported" and result.note == "Umwandlung fehlgeschlagen (Rückgabecode 1)"
    assert result.content_checked is True


def test_altformat_durchlaeuft_die_kette(objekt, stammdaten, run_all, fake_ocr, fake_convert):
    digital = _dokument_mit_datei(objekt, "Schreiben.doc", OLE + b"digital", mime="application/msword")
    scan = _dokument_mit_datei(objekt, "Altvertrag.doc", OLE + b"scan", mime="application/msword")
    fake_convert["texte"][OLE + b"digital"] = ("DOC-DIGITAL", False)
    fake_convert["texte"][OLE + b"scan"] = ("DOC-SCAN", True)
    ingest.ensure_run(objekt, documents=[digital, scan])
    run_all(objekt)
    digital.refresh_from_db()
    scan.refresh_from_db()

    # Original bleibt die .doc-Datei, die PDF dient nur der Texterkennung
    assert digital.current_name == "Schreiben.doc" and digital.mime_type == "application/msword"
    assert storage.original_path(digital.sha256).name == "original.doc"
    assert (storage.work_dir(digital.sha256) / "converted.pdf").exists()
    assert not AuditEvent.objects.filter(action="document.rename_by_content", entity_id=digital.pk).exists()
    assert digital.page_count == 1 and digital.origin_kind == "digital"
    assert digital.status in ("review", "classified", "filed")
    assert not ReviewCase.objects.filter(document=digital, case_subtype="unsupported_format").exists()
    seite = DocumentPage.objects.get(document=digital, page_no=1)
    assert "DOC-DIGITAL" in seite.text_content and seite.text_source == "text_layer" and not seite.is_scan
    analyse = json.loads(storage.analysis_path(digital.sha256).read_text(encoding="utf-8"))
    assert analyse["kind"] == "office_legacy" and analyse["pdf_path"].endswith("converted.pdf")
    assert "texts" not in analyse
    # Seitenbilder aus der gewandelten PDF
    assert (storage.previews_dir(digital.pk) / "0001.jpg").exists()

    # Scan im Altformat: OCR nur fuer die Seiten ohne Textebene
    assert scan.page_count == 1 and scan.origin_kind == "scan"
    assert [c["pages"] for c in fake_ocr] == [[1]]
    seite = DocumentPage.objects.get(document=scan, page_no=1)
    assert "FAKE-OCR Seite 1" in seite.text_content and seite.is_scan
    assert len(fake_convert["calls"]) == 2
    assert all(c["timeout_s"] == 120 for c in fake_convert["calls"])


def test_zeitlimit_aus_der_konfiguration(objekt, stammdaten, run_all, fake_convert, admin_user):
    store.set("processing.office_convert_timeout_s", 45, user=admin_user)
    doc = _dokument_mit_datei(objekt, "Liste.xls", OLE, mime="application/vnd.ms-excel")
    ingest.ensure_run(objekt, documents=[doc])
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.page_count == 1
    assert fake_convert["calls"][0]["timeout_s"] == 45
    assert fake_convert["calls"][0]["path"] == storage.original_path(doc.sha256)
    assert fake_convert["calls"][0]["out_dir"] == storage.work_dir(doc.sha256) / "convert"


def test_ohne_libreoffice_entsteht_fall_mit_notiz(objekt, stammdaten, run_all, ohne_soffice, fake_ocr):
    doc = _dokument_mit_datei(objekt, "Schreiben.doc", OLE, mime="application/msword")
    ingest.ensure_run(objekt, documents=[doc])
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.status == "review" and doc.page_count is None
    fall = ReviewCase.objects.get(document=doc, case_subtype="unsupported_format")
    assert fall.status == CaseStatus.OPEN and fall.context["note"] == "Umwandlung nicht verfügbar"
    assert fall.context["kind"] == "unsupported" and fall.context["content_checked"] is True
    assert fall.context["detected_kind"] == "office_legacy"
    assert not storage.analysis_path(doc.sha256).exists()
    assert not (storage.work_dir(doc.sha256) / "converted.pdf").exists()


def test_praesentation_bleibt_nicht_unterstuetzt(
    objekt, stammdaten, run_all, fake_convert, fake_ocr, monkeypatch
):
    """Kein libreoffice-impress im Worker-Image: .ppt (Endung, MIME-Typ oder PowerPoint-Strom im OLE-Container)
    gilt weiter als nicht unterstuetzt und startet keine Umwandlung (26.09.2026). Der OLE-Inhalt wird ueber die
    Stromliste nachgestellt, wie in tests/unit/test_sniff.py."""
    monkeypatch.setattr(sniff, "_ole_streams", lambda p: ["PowerPoint Document", "Current User"])
    ppt = _dokument_mit_datei(objekt, "Vortrag.ppt", OLE + b"ppt", mime="application/vnd.ms-powerpoint")
    ingest.ensure_run(objekt, documents=[ppt])
    run_all(objekt)
    ppt.refresh_from_db()
    fall = ReviewCase.objects.get(document=ppt, case_subtype="unsupported_format")
    assert ppt.status == "review" and fall.context["kind"] == "unsupported"
    assert fall.context["note"] == "Format nicht unterstützt, manuelle Prüfung (Inhalt: ppt)"
    assert fall.context["detected_kind"] == "unsupported" and fake_convert["calls"] == []
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "office", stdout=out)
    assert "Keine offenen Faelle in der Gruppe office." in out.getvalue()


def test_gruppe_office_nimmt_nur_am_inhalt_erkanntes_altformat(
    objekt, stammdaten, run_all, ohne_soffice, fake_ocr, monkeypatch
):
    """Rechnung.tmp mit RTF-Inhalt ohne LibreOffice: der Name bleibt (Umbenennung nach Inhalt nur bei gelesener
    Datei), detect_kind nach Name und MIME-Typ liefert unsupported. Die Gruppe office findet den Fall ueber
    detected_kind im Kontext und nimmt ihn wieder auf (26.09.2026)."""
    rtf = _dokument_mit_datei(objekt, "Rechnung.tmp", b"{\\rtf1\\ansi Testtext ohne Personen}")
    zip_doc = _dokument_mit_datei(objekt, "Archiv.tmp", b"PK\x03\x04" + bytes(64))
    ingest.ensure_run(objekt, documents=[rtf, zip_doc])
    run_all(objekt)
    rtf.refresh_from_db()
    assert rtf.current_name == "Rechnung.tmp" and rtf.status == "review"
    fall = ReviewCase.objects.get(document=rtf, case_subtype="unsupported_format")
    assert fall.context["note"] == "Umwandlung nicht verfügbar" and fall.context["content_checked"] is True
    assert fall.context["detected_kind"] == "office_legacy"
    assert analysis.detect_kind(Path(rtf.current_name), rtf.mime_type) == "unsupported"
    assert ReviewCase.objects.get(document=zip_doc).context["detected_kind"] == "unsupported"

    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "office", stdout=out)
    text = out.getvalue()
    assert "Faelle mit Office-Altformat (Umwandlung ueber LibreOffice): 1" in text
    assert "Nach Endung: .tmp 1" in text and "Nach Format: office_legacy 1" in text
    assert "Weiterhin nicht verarbeitbar: 1 (.tmp 1)" in text and "Rechnung" not in text
    # Gruppe bekannt zaehlt den Fall als Altformat, nicht als "weiterhin unbekannt"
    out = StringIO()
    call_command("formate_wiederaufnehmen", stdout=out)
    assert "Office-Altformat (Gruppe office): 1 (.tmp 1)" in out.getvalue()

    call_command("formate_wiederaufnehmen", "--gruppe", "office", "--echt", stdout=StringIO())
    rtf.refresh_from_db()
    assert rtf.status == "registered"
    assert ReviewCase.objects.get(pk=fall.pk).status == CaseStatus.RESOLVED
    assert ReviewCase.objects.get(document=zip_doc).status == CaseStatus.OPEN

    def _fake(path: Path, out_dir: Path, timeout_s: int = 120) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        return write_pdf(out_dir / (path.stem + ".pdf"), [page_lines("RTF-NACH-WIEDERAUFNAHME", 1, 1)])

    monkeypatch.setattr(office_convert, "convert_to_pdf", _fake)
    ingest.ensure_run(objekt, documents=[rtf])
    run_all(objekt)
    rtf.refresh_from_db()
    assert rtf.page_count == 1 and rtf.current_name == "Rechnung.rtf"
    assert "RTF-NACH-WIEDERAUFNAHME" in DocumentPage.objects.get(document=rtf, page_no=1).text_content


def test_formate_wiederaufnehmen_gruppe_office(
    objekt, stammdaten, run_all, ohne_soffice, fake_ocr, monkeypatch
):
    alt = _dokument_mit_datei(objekt, "Schreiben.doc", OLE, mime="application/msword")
    # anderer Inhalt als Schreiben.doc, sonst Dublette statt zweitem Fall
    tabelle = _dokument_mit_datei(objekt, "Tabelle.xls", OLE + b"xls", mime="application/vnd.ms-excel")
    zip_doc = _dokument_mit_datei(objekt, "Archiv.tmp", b"PK\x03\x04" + bytes(64))
    ingest.ensure_run(objekt, documents=[alt, tabelle, zip_doc])
    run_all(objekt)
    assert ReviewCase.objects.filter(case_subtype="unsupported_format", status=CaseStatus.OPEN).count() == 3

    # Vorschau: Gruppe office nimmt beide Altformate trotz content_checked, ohne Dateinamen
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "office", stdout=out)
    text = out.getvalue()
    assert "Faelle mit Office-Altformat (Umwandlung ueber LibreOffice): 2" in text
    assert "Nach Endung: .doc 1, .xls 1" in text or "Nach Endung: .xls 1, .doc 1" in text
    assert "Nach Format: office_legacy 2" in text and f"Objekt {objekt.object_number}: 2" in text
    assert "Weiterhin nicht verarbeitbar: 1 (.tmp 1)" in text and "Vorschau: 2 Faelle" in text
    assert "Schreiben" not in text and "Tabelle" not in text and "Archiv" not in text
    alt.refresh_from_db()
    assert alt.status == "review"

    # Gruppe bekannt laesst Altformate liegen und zaehlt sie gesondert
    out = StringIO()
    call_command("formate_wiederaufnehmen", stdout=out)
    text = out.getvalue()
    assert "Keine offenen Faelle mit inzwischen bekanntem Format." in text
    assert "Office-Altformat (Gruppe office): 2 (" in text
    assert "Inhalt bereits geprueft, weiterhin unbekannt: 1 (.tmp 1)" in text

    out = StringIO()
    call_command(
        "formate_wiederaufnehmen",
        "--gruppe",
        "office",
        "--objekt",
        objekt.object_number,
        "--echt",
        stdout=out,
    )
    assert "Wieder aufgenommen: 2 Dokumente" in out.getvalue()
    for doc in (alt, tabelle):
        doc.refresh_from_db()
        fall = ReviewCase.objects.get(document=doc, case_subtype="unsupported_format")
        assert doc.status == "registered" and doc.page_count is None
        assert fall.status == CaseStatus.RESOLVED and fall.resolution["gruppe"] == "office"
        ereignis = AuditEvent.objects.get(action="pipeline.format_reprocess", entity_id=doc.pk)
        assert ereignis.after_state["kind"] == "office_legacy" and ereignis.after_state["gruppe"] == "office"
    assert ReviewCase.objects.get(document=zip_doc).status == CaseStatus.OPEN

    # Zweiter Lauf mit LibreOffice (Fake): Seiten entstehen, kein neuer Fall
    def _fake(path: Path, out_dir: Path, timeout_s: int = 120) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        return write_pdf(out_dir / (path.stem + ".pdf"), [page_lines("NACH-WIEDERAUFNAHME", 1, 1)])

    monkeypatch.setattr(office_convert, "convert_to_pdf", _fake)
    ingest.ensure_run(objekt, documents=[alt, tabelle])
    run_all(objekt)
    for doc in (alt, tabelle):
        doc.refresh_from_db()
        assert doc.page_count == 1 and doc.status in ("review", "classified", "filed")
        assert "NACH-WIEDERAUFNAHME" in DocumentPage.objects.get(document=doc, page_no=1).text_content
        assert not ReviewCase.objects.filter(
            document=doc, case_subtype="unsupported_format", status=CaseStatus.OPEN
        ).exists()
    out = StringIO()
    call_command("formate_wiederaufnehmen", "--gruppe", "office", stdout=out)
    assert "Keine offenen Faelle in der Gruppe office." in out.getvalue()
