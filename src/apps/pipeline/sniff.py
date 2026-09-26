"""Formatweiche nach Dateiinhalt (26.09.2026; Fachentwurf E 1.2 Schritt 4, docs/architektur.md 6.1 Nr. 3).

Befund nach dem Bestandslauf E-Mails vom 26.09.2026: von 2.007 offenen Faellen "nicht unterstuetztes Format" trugen
rund 900 eine Temporaer-Endung des Browsers oder Betriebssystems (.tmp 330, .indir 302, .herunterladen 130, .hed 72)
oder gar keine Endung (58). Die Endung sagt dort nichts ueber den Inhalt; die Kette prueft deshalb die ersten Bytes
(Magic Bytes) und erkennt PDF, Bilder, Office-Container (ZIP mit [Content_Types].xml, OLE), Outlook-Nachrichten
(OLE mit Eigenschaftsstroemen __substg1.0_), RFC-822-E-Mails an den Kopfzeilen und HTML. Rueckgabe ist die Art wie
in analysis.Analysis.kind plus eine vorgeschlagene Endung; None, wenn der Inhalt unbekannt bleibt. Es wird nur
gelesen, die Datei bleibt unveraendert; Inhalte werden nicht protokolliert.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Endungen, die Browser und Betriebssystem waehrend des Ladens vergeben (Chrome .crdownload, Firefox .part, Windows
# .tmp, Safari/Edge lokalisiert .herunterladen und .indir, Altbestand .hed); dazu Dateien ohne Endung (26.09.2026)
TEMP_SUFFIXES = {"", ".tmp", ".herunterladen", ".indir", ".hed", ".part", ".crdownload"}

HEAD_BYTES = 8192
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PDF_WINDOW = 1024  # die PDF-Spezifikation erlaubt Vorspann vor %PDF innerhalb der ersten 1024 Byte
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)
_ODF_MIMES = {
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
}
# Kopfzeilen, mit denen gespeicherte E-Mails typischerweise beginnen (RFC 5322 und gaengige Erweiterungen)
_MAIL_FIRST_HEADERS = {
    "received",
    "from",
    "return-path",
    "mime-version",
    "subject",
    "delivered-to",
    "date",
    "to",
    "message-id",
    "x-mozilla-status",
    "x-originating-ip",
}
_HEADER_LINE = re.compile(r"^([!-9;-~]+):")
# Hauptteil-Inhaltstypen der OOXML-Container ([Content_Types].xml, Override PartName /word/document.xml bzw.
# /xl/workbook.xml): nur die Varianten, die python-docx und openpyxl lesen, gelten als office (26.09.2026)
_OOXML_MAIN_TYPES: dict[str, tuple[str, str]] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": ("office", ".docx"),
    "application/vnd.ms-word.document.macroEnabled.main+xml": ("unsupported", ".docm"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml": (
        "unsupported",
        ".dotx",
    ),
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml": ("unsupported", ".dotm"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": ("office", ".xlsx"),
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml": ("office", ".xlsm"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml": ("unsupported", ".xltx"),
    "application/vnd.ms-excel.template.macroEnabled.main+xml": ("unsupported", ".xltm"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": (
        "unsupported",
        ".pptx",
    ),
}
_CONTENT_TYPE_OVERRIDE = re.compile(rb'ContentType="([^"]+)"')
# MIME-Typ je massgeblicher Endung fuer die Umbenennung nach Inhaltspruefung (26.09.2026); Python-mimetypes kennt
# die Office-Typen nicht auf jedem System, deshalb die Tabelle vor mimetypes.guess_type
MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".msg": "application/vnd.ms-outlook",
    ".eml": "message/rfc822",
    ".html": "text/html",
}


def mime_for_suffix(suffix: str) -> str:
    """MIME-Typ zur Endung (Tabelle, sonst mimetypes, sonst application/octet-stream)."""
    ext = (suffix or "").lower()
    return MIME_BY_SUFFIX.get(ext) or mimetypes.guess_type(f"x{ext}")[0] or "application/octet-stream"


def name_with_suffix(name: str, suffix: str) -> str:
    """Dateiname mit der massgeblichen Endung (26.09.2026): eine Temporaer-Endung wird abgeschnitten, ebenso eine
    Endung, die nur um Sonderzeichen von der massgeblichen abweicht (Rechnung.pdf_ -> Rechnung.pdf); traegt der
    Stamm die Endung bereits (Rechnung.pdf.tmp), wird sie nicht verdoppelt. Ohne Endungsvorschlag unveraendert."""
    suffix = (suffix or "").lower()
    if not suffix:
        return name
    path = Path(name)
    current = path.suffix.lower()
    bereinigt = "." + re.sub(r"[^a-z0-9]", "", current)
    base = path.stem if current in TEMP_SUFFIXES or bereinigt == suffix else name
    if base.lower().endswith(suffix):
        return base
    return base + suffix


@dataclass(frozen=True)
class Sniffed:
    kind: str  # pdf | image | office | office_legacy | email | html | unsupported
    suffix: str  # vorgeschlagene Endung mit Punkt, leer wenn keine sinnvoll ist


def _ole_streams(path: Path) -> list[str] | None:
    """Namen der Stroeme im OLE-Container (erste Ebene reicht fuer die Erkennung); None, wenn nicht lesbar."""
    try:
        import olefile
    except ImportError:  # olefile ist eine Abhaengigkeit des Workers, im Web-Container optional
        logger.debug("olefile nicht installiert, OLE-Container ohne Strompruefung")
        return None
    try:
        with olefile.OleFileIO(str(path)) as ole:
            return ["/".join(entry) for entry in ole.listdir(streams=True, storages=True)]
    except Exception:  # noqa: BLE001 (beschaedigter oder abgeschnittener Container)
        logger.debug("OLE-Container nicht lesbar", exc_info=True)
        return None


def _sniff_ole(path: Path) -> Sniffed:
    streams = _ole_streams(path) or []
    names = {s.split("/")[0] for s in streams}
    if any(n.startswith("__substg1.0_") or n.startswith("__properties_version") for n in names):
        return Sniffed("email", ".msg")
    if "WordDocument" in names:
        return Sniffed("office_legacy", ".doc")
    if "Workbook" in names or "Book" in names:
        return Sniffed("office_legacy", ".xls")
    if "PowerPoint Document" in names:
        # Praesentation: kein libreoffice-impress im Worker-Image, deshalb unsupported (26.09.2026)
        return Sniffed("unsupported", ".ppt")
    # OLE ohne bekannten Hauptstrom (z. B. abgeschnitten): Altformat ohne Endungsvorschlag, LibreOffice versucht
    # die Umwandlung (26.09.2026)
    return Sniffed("office_legacy", "")


def _sniff_zip(path: Path) -> Sniffed:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            mimetype = zf.read("mimetype").decode("ascii", "replace").strip() if "mimetype" in names else ""
            content_types = zf.read("[Content_Types].xml") if "[Content_Types].xml" in names else b""
    except Exception:  # noqa: BLE001 (beschaedigtes Archiv)
        logger.debug("ZIP-Container nicht lesbar", exc_info=True)
        return Sniffed("unsupported", ".zip")
    if content_types:
        # Hauptteil-Typ entscheidet (26.09.2026): .docm, .dotx, .dotm und Vorlagen haben denselben Aufbau wie
        # .docx bzw. .xlsx, python-docx und openpyxl lesen sie aber nicht alle
        for ctype in _CONTENT_TYPE_OVERRIDE.findall(content_types):
            hit = _OOXML_MAIN_TYPES.get(ctype.decode("ascii", "replace"))
            if hit is not None:
                return Sniffed(*hit)
        if any(n.startswith(("word/", "xl/", "ppt/")) for n in names):
            # Container ohne bekannten Hauptteil-Typ: Office-Paket, aber nicht lesbar
            return Sniffed("unsupported", "")
        return Sniffed("unsupported", ".zip")
    if mimetype in _ODF_MIMES:
        return Sniffed("office_legacy", _ODF_MIMES[mimetype])
    return Sniffed("unsupported", ".zip")


def _looks_like_html(text: str) -> bool:
    lowered = text.lstrip("﻿ \t\r\n").lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        return True
    # XML-Prolog oder Kommentar vor dem Wurzelelement (XHTML-Exporte)
    return lowered.startswith(("<?xml", "<!--")) and "<html" in lowered[:2048]


def _looks_like_email(text: str) -> bool:
    lines = text.lstrip("﻿").splitlines()[:60]
    if not lines:
        return False
    if lines[0].startswith("From ") and len(lines) > 1:  # mbox-Trennzeile
        lines = lines[1:]
    match = _HEADER_LINE.match(lines[0])
    if match is None or match.group(1).lower() not in _MAIL_FIRST_HEADERS:
        return False
    fields = 0
    for line in lines:
        if not line.strip():
            break
        if line[:1] in (" ", "\t"):  # Fortsetzungszeile
            continue
        if _HEADER_LINE.match(line) is None:
            return False
        fields += 1
    return fields >= 2


def detect_kind_by_content(path: Path) -> Sniffed | None:
    """Art und vorgeschlagene Endung aus den ersten Bytes; None bei leerer oder unbekannter Datei."""
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(HEAD_BYTES)
    except OSError:
        logger.debug("Datei fuer die Inhaltspruefung nicht lesbar", exc_info=True)
        return None
    if not head:
        return None
    if b"%PDF" in head[:_PDF_WINDOW]:
        return Sniffed("pdf", ".pdf")
    for magic, suffix in _IMAGE_MAGIC:
        if head.startswith(magic):
            return Sniffed("image", suffix)
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return Sniffed("image", ".webp")
    if head.startswith(b"BM") and head[6:10] == b"\x00\x00\x00\x00" and len(head) > 26:
        return Sniffed("image", ".bmp")
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(path)
    if head.startswith(_OLE_MAGIC):
        return _sniff_ole(path)
    if head.startswith(b"{\\rtf"):
        return Sniffed("office_legacy", ".rtf")
    text = head.decode("utf-8", errors="replace")
    if _looks_like_html(text):
        return Sniffed("html", ".html")
    if len(head) == HEAD_BYTES and not head.endswith((b"\n", b"\r")):
        # Datei laenger als der Vorspann: die letzte Zeile ist in der Regel abgeschnitten und wuerde als
        # Nicht-Kopfzeile gelten (Exchange-typische ungefaltete Kopfzeilen ueber 8 KB, 26.09.2026)
        text = text.rpartition("\n")[0]
    if _looks_like_email(text):
        return Sniffed("email", ".eml")
    return None
