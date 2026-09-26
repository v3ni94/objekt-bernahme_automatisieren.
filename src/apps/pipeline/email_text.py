"""E-Mails als Text lesen (25.09.2026): .eml (RFC 822, Standardbibliothek) und .msg (Outlook, OLE-Container ueber
olefile). Ergebnis ist eine Textseite mit Kopfzeilen, Textkoerper und Anhangsliste, die die Kette wie eine
Office-Datei ohne OCR verarbeitet. Bestand: 4.404 E-Mails im Drive-Altbestand (2.695 .eml, 1.709 .msg) standen als
Fall „nicht unterstuetztes Format" in der Pruefung. Anhaenge werden nur mit Name und Groesse aufgefuehrt, ihr
Inhalt wird nicht gelesen; ein Text nur als RTF (ohne Klartext oder HTML) bleibt ungelesen und wird vermerkt.
Nur Klartext im Speicher, keine Persistierung hier (Maskierung erfolgt in der Kette wie fuer alle Seitentexte)."""

from __future__ import annotations

import email
import email.policy
import logging
import re
import struct
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

MAX_TEXT_CHARS = 100_000  # Schutz vor Newslettern mit sehr grossem HTML (Seitentext in der Datenbank)
EMAIL_EXT = {".eml", ".msg"}
EMAIL_MIMES = {"message/rfc822", "application/vnd.ms-outlook", "application/vnd.ms-outlook-pst"}

logger = logging.getLogger(__name__)
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "pre"}


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "td":
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HtmlText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001, S110 (fehlerhaftes HTML: so viel Text wie moeglich)
        logger.debug("HTML unvollstaendig gelesen")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _size_label(size: int | None) -> str:
    if not size:
        return ""
    if size < 1024:
        return f" ({size} B)"
    if size < 1024 * 1024:
        return f" ({size / 1024:.1f} KB)".replace(".", ",")
    return f" ({size / (1024 * 1024):.1f} MB)".replace(".", ",")


def compose_text(
    *,
    sender: str | None,
    to: str | None,
    cc: str | None,
    date: str | None,
    subject: str | None,
    body: str | None,
    attachments: list[tuple[str, int | None]],
    note: str | None = None,
) -> str:
    lines = []
    for label, value in (("Von", sender), ("An", to), ("Cc", cc), ("Datum", date), ("Betreff", subject)):
        if value and value.strip():
            lines.append(f"{label}: {' '.join(value.split())}")
    text = "\n".join(lines)
    body = (body or "").strip()
    if len(body) > MAX_TEXT_CHARS:
        body = body[:MAX_TEXT_CHARS] + "\n[Text gekürzt]"
    if body:
        text += "\n\n" + body
    if note:
        text += "\n\n" + note
    if attachments:
        text += "\n\nAnhänge: " + ", ".join(f"{name}{_size_label(size)}" for name, size in attachments)
    return text.strip()


# ---------------------------------------------------------------- .eml
def eml_text(data: bytes) -> str:
    msg = email.message_from_bytes(data, policy=email.policy.default)

    def header(name: str) -> str | None:
        try:
            value = msg.get(name)
        except Exception:  # noqa: BLE001 (defekte Kopfzeile)
            return None
        return str(value) if value else None

    body = None
    note = None
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001
        part = None
    if part is not None:
        try:
            content = part.get_content()
        except Exception:  # noqa: BLE001 (unbekannte Kodierung)
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        body = html_to_text(content) if part.get_content_subtype() == "html" else str(content)
    elif not msg.is_multipart():
        payload = msg.get_payload(decode=True) or b""
        body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    attachments: list[tuple[str, int | None]] = []
    try:
        for att in msg.iter_attachments():
            name = att.get_filename() or (
                "(eingebettete Nachricht)"
                if att.get_content_type() == "message/rfc822"
                else att.get_content_type()
            )
            try:
                payload = att.get_payload(decode=True)
                size = len(payload) if isinstance(payload, bytes) else None
            except Exception:  # noqa: BLE001
                size = None
            attachments.append((name, size))
    except Exception:  # noqa: BLE001 (defekte Struktur: Anhaenge unbekannt)
        note = "[Anhänge nicht lesbar]"
    date = header("Date")
    try:
        parsed = email.utils.parsedate_to_datetime(date) if date else None
        if parsed is not None:
            date = parsed.strftime("%d.%m.%Y %H:%M")
    except Exception:  # noqa: BLE001 (unbekanntes Datumsformat: Rohtext behalten)
        logger.debug("Datum der E-Mail nicht lesbar")
    return compose_text(
        sender=header("From"),
        to=header("To"),
        cc=header("Cc"),
        date=date,
        subject=header("Subject"),
        body=body,
        attachments=attachments,
        note=note,
    )


# ---------------------------------------------------------------- .msg
_PT_UNICODE = "001F"
_PT_STRING8 = "001E"
_PT_BINARY = "0102"
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def _filetime(value: int) -> str | None:
    if not value:
        return None
    try:
        return (_FILETIME_EPOCH + timedelta(microseconds=value // 10)).strftime("%d.%m.%Y %H:%M")
    except (OverflowError, ValueError):
        return None


class _MsgReader:
    """Eigenschaften eines Outlook-Containers lesen. `ole` ist ein olefile.OleFileIO oder ein gleichartiges Objekt
    mit listdir(streams=True, storages=False) und openstream(pfad)."""

    def __init__(self, ole) -> None:
        self.ole = ole
        self.streams: list[list[str]] = [list(p) for p in ole.listdir(streams=True, storages=False)]
        self.codepage = "cp1252"

    def _read(self, path: list[str]) -> bytes | None:
        try:
            with self.ole.openstream(path) as fh:
                return fh.read()
        except Exception:  # noqa: BLE001 (Strom fehlt oder defekt)
            return None

    def _find(self, prefix: list[str], prop: str) -> tuple[str, list[str]] | None:
        wanted = f"__substg1.0_{prop}".upper()
        for path in self.streams:
            if path[:-1] == prefix and path[-1].upper().startswith(wanted):
                return path[-1][-4:].upper(), path
        return None

    def string(self, prop: str, prefix: list[str] | None = None) -> str | None:
        found = self._find(prefix or [], prop)
        if found is None:
            return None
        kind, path = found
        raw = self._read(path)
        if raw is None:
            return None
        if kind == _PT_UNICODE:
            return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        if kind == _PT_STRING8:
            return raw.decode(self.codepage, errors="replace").rstrip("\x00")
        if kind == _PT_BINARY:
            for enc in ("utf-8", self.codepage):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode(self.codepage, errors="replace")
        return None

    def binary_size(self, prop: str, prefix: list[str]) -> int | None:
        found = self._find(prefix, prop)
        if found is None:
            return None
        raw = self._read(found[1])
        return len(raw) if raw is not None else None

    def fixed_properties(self, prefix: list[str] | None = None) -> dict[str, int]:
        """Eigenschaften fester Laenge aus __properties_version1.0 (Zeitstempel, Zahlen), Schluessel ist die
        vierstellige Eigenschafts-ID, Wert der rohe 64-Bit-Inhalt."""
        raw = self._read([*(prefix or []), "__properties_version1.0"])
        if not raw:
            return {}
        offset = 32 if not prefix else 8
        result: dict[str, int] = {}
        for pos in range(offset, len(raw) - 15, 16):
            kind, prop_id = struct.unpack_from("<HH", raw, pos)
            value = struct.unpack_from("<Q", raw, pos + 8)[0]
            if kind in (0x0003, 0x000B, 0x0002):  # 32-Bit-Werte: obere Haelfte ist reserviert
                value &= 0xFFFFFFFF
            result[f"{prop_id:04X}"] = value
        return result

    def attachments(self) -> list[tuple[str, int | None]]:
        storages = sorted({p[0] for p in self.streams if p[0].startswith("__attach_version1.0_")})
        result = []
        for name in storages:
            prefix = [name]
            label = (
                self.string("3707", prefix)
                or self.string("3704", prefix)
                or self.string("3001", prefix)
                or "(Anhang ohne Namen)"
            )
            props = self.fixed_properties(prefix)
            size = props.get("0E20")
            if size is None or size > 2**40:
                size = self.binary_size("3701", prefix)
            result.append((label, int(size) if size else None))
        return result


def msg_text_from_reader(reader: _MsgReader) -> str:
    props = reader.fixed_properties()
    cpid = props.get("3FDE") or props.get("3FFD")
    if cpid:
        cpid = int(cpid) & 0xFFFFFFFF
        reader.codepage = {1252: "cp1252", 65001: "utf-8", 28591: "latin-1", 20127: "ascii"}.get(
            cpid, "cp1252"
        )
    body = reader.string("1000")
    note = None
    if not body:
        html = reader.string("1013")
        if html:
            body = html_to_text(html)
        elif reader._find([], "1009") is not None:
            note = "[Text nur als RTF vorhanden, nicht gelesen]"
    sender = reader.string("0C1A")
    address = reader.string("5D01") or reader.string("0C1F")
    if address and "@" in address and (not sender or address.lower() not in sender.lower()):
        sender = f"{sender} <{address}>" if sender else address
    date = _filetime(props.get("0039") or props.get("0E06") or 0)
    return compose_text(
        sender=sender,
        to=reader.string("0E04"),
        cc=reader.string("0E03"),
        date=date,
        subject=reader.string("0037") or reader.string("0E1D"),
        body=body,
        attachments=reader.attachments(),
        note=note,
    )


def msg_text(path: Path) -> str:
    import olefile

    if not olefile.isOleFile(str(path)):
        raise ValueError("keine Outlook-Nachricht (kein OLE-Container)")
    with olefile.OleFileIO(str(path)) as ole:
        return msg_text_from_reader(_MsgReader(ole))


def extract_email_text(path: Path, suffix: str | None = None) -> list[str]:
    """Eine Textseite je Nachricht; .msg ueber den OLE-Container, alles andere als RFC 822. suffix uebersteuert
    die Endung der Datei, wenn die Art aus dem Inhalt erkannt wurde (26.09.2026, apps.pipeline.sniff)."""
    if (suffix or path.suffix).lower() == ".msg":
        return [msg_text(path)]
    return [eml_text(path.read_bytes())]


# ---------------------------------------------------------------- Kopfzeilen (26.09.2026)
# Kopfzeilen als Daten fuer Regeln und Entscheidung (Regel 06 Sonstiges fuer E-Mails, Vorlage E-4): Betreff,
# Absender, Empfaenger, Datum. Die Textseite oben bleibt unveraendert; diese Funktionen lesen dieselben Quellen
# noch einmal und liefern ein dict statt Text.
_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:\[[^\]]{1,40}\]\s*)?(?:(?:aw|re|wg|fw|fwd|antw|antwort|tr|sv|vs)\s*(?:\^?\d+)?\s*:\s*)+",
    re.IGNORECASE,
)
_TEXT_HEADER = re.compile(r"^(Von|An|Datum|Betreff):\s*(.*)$")
EMAIL_TITLE_MAX = 120  # Laenge des Ablage-Titels ohne Endung (Dateiname in Drive)


def clean_subject(subject: str | None) -> str:
    """Betreff ohne Antwort- und Weiterleitungspraefixe (AW:, WG:, Re:, Fwd:, FW:, auch mehrfach, mit Zaehler wie
    AW^2: und mit Kennung in eckigen Klammern davor), Leerraum bereinigt; leer ohne Betreff."""
    text = " ".join((subject or "").split())
    while True:
        stripped = _SUBJECT_PREFIX.sub("", text, count=1).strip()
        if stripped == text:
            break
        text = stripped
    return text


def _address(value: str | None) -> tuple[str | None, str | None]:
    """Anzeigename und Adresse aus 'Name <adresse>'; nur der erste Absender, Adresse in Kleinschreibung."""
    if not value:
        return None, None
    value = " ".join(value.split())
    name, addr = email.utils.parseaddr(value)
    if not addr or "@" not in addr:
        # kein Adressteil (Exchange-Absender in Outlook nur als Name): der ganze Wert ist der Name
        return value.strip('"') or None, None
    return name.strip().strip('"') or None, addr.strip().lower()


def _headers(
    *,
    subject: str | None,
    sender: str | None,
    to: str | None,
    date: datetime | None,
    sender_address: str | None = None,
) -> dict:
    name, addr = _address(sender)
    if sender_address and "@" in sender_address:
        addr = sender_address.strip().lower()
    subject = " ".join((subject or "").split()) or None
    return {
        "subject": subject,
        "subject_clean": clean_subject(subject),
        "from_name": name,
        "from_address": addr,
        "to": " ".join((to or "").split()) or None,
        "date": date.isoformat(timespec="minutes") if date is not None else None,
    }


def eml_headers(data: bytes) -> dict:
    msg = email.message_from_bytes(data, policy=email.policy.default)

    def header(name: str) -> str | None:
        try:
            value = msg.get(name)
        except Exception:  # noqa: BLE001 (defekte Kopfzeile)
            return None
        return str(value) if value else None

    date = None
    try:
        raw = header("Date")
        date = email.utils.parsedate_to_datetime(raw) if raw else None
    except Exception:  # noqa: BLE001 (unbekanntes Datumsformat)
        logger.debug("Datum der E-Mail nicht lesbar")
    return _headers(subject=header("Subject"), sender=header("From"), to=header("To"), date=date)


def _filetime_datetime(value: int) -> datetime | None:
    if not value:
        return None
    try:
        return _FILETIME_EPOCH + timedelta(microseconds=value // 10)
    except (OverflowError, ValueError):
        return None


def msg_headers_from_reader(reader: _MsgReader) -> dict:
    props = reader.fixed_properties()
    cpid = props.get("3FDE") or props.get("3FFD")
    if cpid:
        cpid = int(cpid) & 0xFFFFFFFF
        reader.codepage = {1252: "cp1252", 65001: "utf-8", 28591: "latin-1", 20127: "ascii"}.get(
            cpid, "cp1252"
        )
    return _headers(
        subject=reader.string("0037") or reader.string("0E1D"),
        sender=reader.string("0C1A"),
        to=reader.string("0E04"),
        date=_filetime_datetime(props.get("0039") or props.get("0E06") or 0),
        sender_address=reader.string("5D01") or reader.string("0C1F"),
    )


def msg_headers(path: Path) -> dict:
    import olefile

    if not olefile.isOleFile(str(path)):
        raise ValueError("keine Outlook-Nachricht (kein OLE-Container)")
    with olefile.OleFileIO(str(path)) as ole:
        return msg_headers_from_reader(_MsgReader(ole))


def parse_email_headers(path: Path) -> dict:
    """Kopfzeilen einer E-Mail-Datei: subject, subject_clean, from_name, from_address, to, date (ISO 8601, mit
    Zeitzone, sofern die Nachricht eine traegt). .msg ueber den OLE-Container, alles andere als RFC 822."""
    if path.suffix.lower() == ".msg":
        return msg_headers(path)
    return eml_headers(path.read_bytes())


def headers_from_text(text: str) -> dict | None:
    """Kopfzeilen aus der Textseite (Zeilen Von, An, Datum, Betreff aus compose_text), wenn das Original nicht mehr
    im Arbeitsverzeichnis liegt (Sweeper). None, wenn der Text keine Kopfzeilen traegt."""
    head = (text or "").split("\n\n", 1)[0]
    found: dict[str, str] = {}
    for line in head.splitlines()[:8]:
        m = _TEXT_HEADER.match(line.strip())
        if m:
            found.setdefault(m.group(1), m.group(2).strip())
    if "Betreff" not in found and "Von" not in found:
        return None
    date = None
    raw = found.get("Datum")
    if raw:
        try:
            date = datetime.strptime(raw, "%d.%m.%Y %H:%M")
        except ValueError:
            date = None
    return _headers(subject=found.get("Betreff"), sender=found.get("Von"), to=found.get("An"), date=date)


def email_title(subject_clean: str | None, suffix: str) -> str | None:
    """Ablage-Titel einer E-Mail: bereinigter Betreff ohne unzulaessige Zeichen, gekuerzt, mit der Endung der
    Nachricht (Namensbildung wie bei den Akten: apps.drive.naming.clean_text). None ohne Betreff."""
    from apps.drive.naming import clean_text

    stem = clean_text(subject_clean or "")
    if not stem:
        return None
    if len(stem) > EMAIL_TITLE_MAX:
        stem = stem[:EMAIL_TITLE_MAX].rstrip(" .,;-")
    return f"{stem}{suffix.lower()}"
