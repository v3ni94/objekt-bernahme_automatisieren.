"""E-Mails als Textseite (25.09.2026): .eml ueber die Standardbibliothek, .msg ueber die OLE-Eigenschaftsstroeme.
Fuer .msg wird der Leser mit einem nachgebildeten Container geprueft (olefile liest, die Auswertung ist unsere)."""

from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

from apps.pipeline import analysis, email_text

# Testinhalte ohne echte Personen oder Adressen


def _eml(*, html: bool = False, attachment: bool = True) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Absender Beispiel <absender@example.test>"
    msg["To"] = "verwaltung@example.test"
    msg["Cc"] = "kopie@example.test"
    msg["Subject"] = "Hausgeldabrechnung 2025 Musterstraße 49"
    msg["Date"] = "Tue, 14 Jan 2025 09:30:00 +0100"
    if html:
        msg.set_content("Nur Text fuer alte Programme")
        msg.add_alternative(
            "<html><head><style>p{}</style></head><body><p>Sehr geehrte Damen und Herren,</p>"
            "<p>anbei die <b>Abrechnung</b>.</p><table><tr><td>Summe</td><td>1.234,56 EUR</td></tr></table>"
            "<script>alert(1)</script></body></html>",
            subtype="html",
        )
    else:
        msg.set_content("Sehr geehrte Damen und Herren,\n\nanbei die Abrechnung.\n\nMit freundlichen Grüßen")
    if attachment:
        msg.add_attachment(
            b"%PDF-1.4 test", maintype="application", subtype="pdf", filename="Abrechnung_2025.pdf"
        )
    return bytes(msg)


def test_eml_klartext_mit_anhang(tmp_path):
    path = tmp_path / "Nachricht.eml"
    path.write_bytes(_eml())
    (text,) = email_text.extract_email_text(path)
    assert text.startswith("Von: Absender Beispiel <absender@example.test>")
    assert "An: verwaltung@example.test" in text and "Cc: kopie@example.test" in text
    assert "Datum: 14.01.2025 09:30" in text
    assert "Betreff: Hausgeldabrechnung 2025 Musterstraße 49" in text
    assert "anbei die Abrechnung." in text and "Mit freundlichen Grüßen" in text
    assert text.endswith("Anhänge: Abrechnung_2025.pdf (13 B)")


def test_eml_nur_html_wird_zu_text(tmp_path):
    path = tmp_path / "Nachricht.eml"
    path.write_bytes(_eml(html=True, attachment=False))
    (text,) = email_text.extract_email_text(path)
    # Klartextteil wird bevorzugt
    assert "Nur Text fuer alte Programme" in text
    html = email_text.html_to_text(
        "<html><head><style>p{}</style></head><body><p>Sehr geehrte Damen und Herren,</p>"
        "<p>anbei die <b>Abrechnung</b>.</p><table><tr><td>Summe</td><td>1.234,56 EUR</td></tr></table>"
        "<script>alert(1)</script></body></html>"
    )
    assert "anbei die Abrechnung." in html and "Summe 1.234,56 EUR" in html
    assert "alert" not in html and "p{}" not in html


def test_eml_defekt_liefert_wenigstens_kopfzeilen(tmp_path):
    path = tmp_path / "kaputt.eml"
    path.write_bytes(b"Subject: Nur Betreff\r\nFrom: x@example.test\r\n\r\n\xff\xfe nicht dekodierbar")
    (text,) = email_text.extract_email_text(path)
    assert "Betreff: Nur Betreff" in text and "Von: x@example.test" in text


class _FakeOle:
    """Nachgebildeter Outlook-Container: Stroeme als Pfadlisten wie bei olefile.listdir."""

    def __init__(self, streams: dict[tuple[str, ...], bytes]):
        self._streams = streams

    def listdir(self, streams=True, storages=False):
        return [list(k) for k in self._streams]

    def openstream(self, path):
        key = tuple(path) if isinstance(path, list) else (path,)
        if key not in self._streams:
            raise OSError("Strom fehlt")
        return BytesIO(self._streams[key])


def _uni(text: str) -> bytes:
    return text.encode("utf-16-le")


def _filetime(dt: datetime) -> int:
    return int((dt - datetime(1601, 1, 1, tzinfo=UTC)) / timedelta(microseconds=1)) * 10


def _props(header: int, entries: list[tuple[int, int, int]]) -> bytes:
    raw = b"\x00" * header
    for kind, prop_id, value in entries:
        raw += struct.pack("<HHIQ", kind, prop_id, 0, value)
    return raw


def test_msg_eigenschaften_und_anhaenge():
    gesendet = datetime(2025, 3, 3, 8, 15, tzinfo=UTC)
    streams = {
        ("__substg1.0_0037001F",): _uni("Wartungsvertrag Aufzug"),
        ("__substg1.0_0C1A001F",): _uni("Firma Beispiel GmbH"),
        ("__substg1.0_5D01001F",): _uni("service@example.test"),
        ("__substg1.0_0E04001F",): _uni("Hausverwaltung"),
        ("__substg1.0_1000001F",): _uni("Guten Tag,\r\n\r\nanbei der Vertrag.\r\n"),
        ("__properties_version1.0",): _props(
            32, [(0x0040, 0x0039, _filetime(gesendet)), (0x0003, 0x3FDE, 1252)]
        ),
        ("__attach_version1.0_#00000000", "__substg1.0_3707001F"): _uni("Vertrag.pdf"),
        ("__attach_version1.0_#00000000", "__substg1.0_37010102"): b"%PDF" + b"x" * 2044,
        ("__attach_version1.0_#00000000", "__properties_version1.0"): _props(8, [(0x0003, 0x0E20, 2048)]),
        ("__attach_version1.0_#00000001", "__substg1.0_3704001E"): b"kurz.txt\x00",
        ("__attach_version1.0_#00000001", "__substg1.0_37010102"): b"abc",
    }
    text = email_text.msg_text_from_reader(email_text._MsgReader(_FakeOle(streams)))
    assert text.startswith("Von: Firma Beispiel GmbH <service@example.test>")
    assert "An: Hausverwaltung" in text and "Datum: 03.03.2025 08:15" in text
    assert "Betreff: Wartungsvertrag Aufzug" in text and "anbei der Vertrag." in text
    assert text.endswith("Anhänge: Vertrag.pdf (2,0 KB), kurz.txt (3 B)")


def test_msg_html_und_rtf_faelle():
    streams = {
        ("__substg1.0_0037001E",): b"Betreff 8-Bit \xe4\x00",
        ("__substg1.0_10130102",): b"<html><body><p>Nur <i>HTML</i></p></body></html>",
    }
    text = email_text.msg_text_from_reader(email_text._MsgReader(_FakeOle(streams)))
    assert "Betreff: Betreff 8-Bit ä" in text and "Nur HTML" in text
    rtf_only = {
        ("__substg1.0_0037001F",): _uni("Nur RTF"),
        ("__substg1.0_10090102",): b"komprimiert",
    }
    text = email_text.msg_text_from_reader(email_text._MsgReader(_FakeOle(rtf_only)))
    assert "Betreff: Nur RTF" in text and "[Text nur als RTF vorhanden, nicht gelesen]" in text


def test_msg_ohne_ole_container(tmp_path):
    path = tmp_path / "falsch.msg"
    path.write_bytes(b"kein OLE")
    try:
        email_text.extract_email_text(path)
    except ValueError as exc:
        assert "OLE" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("ValueError erwartet")


def test_formatweiche_und_analyse_email(tmp_path):
    assert analysis.detect_kind(Path("a.eml"), None) == "email"
    assert analysis.detect_kind(Path("a.MSG"), "application/octet-stream") == "email"
    assert analysis.detect_kind(Path("a"), "message/rfc822") == "email"
    assert analysis.detect_kind(Path("a.zip"), None) == "unsupported"
    path = tmp_path / "Nachricht.eml"
    path.write_bytes(_eml())
    result = analysis.analyze_file(
        path, "message/rfc822", min_chars=50, alnum_ratio=0.6, cover_ratio=0.9, chunk_pages=20, work=tmp_path
    )
    assert result.kind == "email" and result.page_count == 1 and result.digital_pages == [1]
    assert result.origin_kind == "digital" and result.pages[0].reason == "email_text"
    assert "Betreff: Hausgeldabrechnung 2025" in result.texts[1]
    assert "texts" not in result.to_json()


def test_langer_text_wird_gekuerzt():
    text = email_text.compose_text(
        sender="a@example.test", to=None, cc=None, date=None, subject="x", body="y" * 200_000, attachments=[]
    )
    assert len(text) < 100_200 and text.endswith("[Text gekürzt]")
