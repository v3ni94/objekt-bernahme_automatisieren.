"""Kopfzeilen einer E-Mail als Daten (26.09.2026, Vorlage E-4): parse_email_headers fuer .eml (im Test erzeugt) und
.msg (nachgebildeter OLE-Container), bereinigter Betreff, Kopfzeilen aus der Textseite, Ablage-Titel; E-Mail-Regeln
der Stufe 1 (Betreff nur mit Textmerkmal stark, Absendermuster, rule_kind)."""

from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from io import BytesIO

import pytest

from apps.classification.context import DocContext
from apps.classification.rules import (
    EMAIL_SUBJECT_ONLY_CONFIDENCE,
    Rule,
    RuleError,
    has_text_feature,
    matches,
    rule_kind_for,
)
from apps.pipeline import email_text

# Testinhalte ohne echte Personen oder Adressen


def _eml(subject: str = "AW: WG: Hausgeldabrechnung 2025 Musterstraße 49") -> bytes:
    msg = EmailMessage()
    msg["From"] = '"Absender, Beispiel" <Absender@Example.test>'
    msg["To"] = "verwaltung@example.test"
    msg["Subject"] = subject
    msg["Date"] = "Tue, 14 Jan 2025 09:30:00 +0100"
    msg.set_content("Sehr geehrte Damen und Herren,\n\nanbei die Abrechnung.")
    return bytes(msg)


def test_parse_email_headers_eml(tmp_path):
    path = tmp_path / "Nachricht.eml"
    path.write_bytes(_eml())
    h = email_text.parse_email_headers(path)
    assert h["subject"] == "AW: WG: Hausgeldabrechnung 2025 Musterstraße 49"
    assert h["subject_clean"] == "Hausgeldabrechnung 2025 Musterstraße 49"
    assert h["from_name"] == "Absender, Beispiel" and h["from_address"] == "absender@example.test"
    assert h["to"] == "verwaltung@example.test" and h["date"] == "2025-01-14T09:30+01:00"
    # die Textseite bleibt unveraendert (Kopfzeilen als Zeilen, Betreff mit Praefix)
    (text,) = email_text.extract_email_text(path)
    assert text.startswith('Von: "Absender, Beispiel" <Absender@Example.test>')
    assert "Betreff: AW: WG: Hausgeldabrechnung 2025 Musterstraße 49" in text


def test_parse_email_headers_eml_defekt(tmp_path):
    path = tmp_path / "kaputt.eml"
    path.write_bytes(b"Subject: Nur Betreff\r\nFrom: x@example.test\r\nDate: irgendwann\r\n\r\n\xff\xfe")
    h = email_text.parse_email_headers(path)
    assert h["subject_clean"] == "Nur Betreff" and h["from_address"] == "x@example.test"
    assert h["from_name"] is None and h["date"] is None and h["to"] is None


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("AW: WG: Rechnung 4711", "Rechnung 4711"),
        ("Re: Re: Fwd: Angebot", "Angebot"),
        ("FW: RE: Mahnung", "Mahnung"),
        ("[EXT] AW^2: Mahnung", "Mahnung"),
        ("  WG:   Zählerstand   2025 ", "Zählerstand 2025"),
        ("RE:AW: x", "x"),
        ("Rechnung: Nr 1", "Rechnung: Nr 1"),
        ("Regelung: Treppenhaus", "Regelung: Treppenhaus"),
        ("Betreff ohne Praefix", "Betreff ohne Praefix"),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_subject(raw, clean):
    assert email_text.clean_subject(raw) == clean


class _FakeOle:
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


def test_msg_headers_aus_container():
    gesendet = datetime(2025, 3, 3, 8, 15, tzinfo=UTC)
    streams = {
        ("__substg1.0_0037001F",): _uni("WG: Wartungsvertrag Aufzug"),
        ("__substg1.0_0C1A001F",): _uni("Firma Beispiel GmbH"),
        ("__substg1.0_5D01001F",): _uni("Service@Example.test"),
        ("__substg1.0_0E04001F",): _uni("Hausverwaltung"),
        ("__properties_version1.0",): _props(
            32, [(0x0040, 0x0039, _filetime(gesendet)), (0x0003, 0x3FDE, 1252)]
        ),
    }
    h = email_text.msg_headers_from_reader(email_text._MsgReader(_FakeOle(streams)))
    assert h["subject"] == "WG: Wartungsvertrag Aufzug" and h["subject_clean"] == "Wartungsvertrag Aufzug"
    assert h["from_name"] == "Firma Beispiel GmbH" and h["from_address"] == "service@example.test"
    assert h["to"] == "Hausverwaltung" and h["date"] == "2025-03-03T08:15+00:00"
    # ohne Adresse (Exchange-Absender nur als Name) und ohne Datum
    nur_name = {
        ("__substg1.0_0037001E",): b"Betreff 8-Bit \xe4\x00",
        ("__substg1.0_0C1A001F",): _uni("Nur Name"),
    }
    h = email_text.msg_headers_from_reader(email_text._MsgReader(_FakeOle(nur_name)))
    assert h["subject_clean"] == "Betreff 8-Bit ä" and h["from_name"] == "Nur Name"
    assert h["from_address"] is None and h["date"] is None


def test_parse_email_headers_msg_ohne_ole(tmp_path):
    path = tmp_path / "falsch.msg"
    path.write_bytes(b"kein OLE")
    with pytest.raises(ValueError, match="OLE"):
        email_text.parse_email_headers(path)


def test_headers_from_text_und_titel():
    text = (
        "Von: Firma Beispiel <a@b.test>\nAn: x@y.test\nDatum: 03.03.2025 09:15\nBetreff: AW: Test / Frage?\n\n"
        "Guten Tag, Betreff: das ist Text."
    )
    h = email_text.headers_from_text(text)
    assert h == {
        "subject": "AW: Test / Frage?",
        "subject_clean": "Test / Frage?",
        "from_name": "Firma Beispiel",
        "from_address": "a@b.test",
        "to": "x@y.test",
        "date": "2025-03-03T09:15",
    }
    assert email_text.headers_from_text("Sehr geehrte Damen und Herren,\n\nText ohne Kopfzeilen") is None
    assert email_text.email_title(h["subject_clean"], ".EML") == "Test Frage.eml"
    assert email_text.email_title("", ".msg") is None and email_text.email_title(None, ".eml") is None
    lang = email_text.email_title("x" * 300, ".msg")
    assert lang.endswith(".msg") and len(lang) == email_text.EMAIL_TITLE_MAX + 4


# ---------------------------------------------------------------- Regeln
def _ctx(text: str, email: dict | None) -> DocContext:
    return DocContext(
        document_id=None,
        object_id=None,
        management_type="weg",
        filename="mail.eml",
        folder_code=None,
        text=text,
        head=text,
        page_count=1,
        pages={1: text},
        email=email,
    )


def _rule(when: dict, *, hard: bool = False, confidence: float = 0.9) -> Rule:
    return Rule.from_definition(
        {
            "id": "R-03-EMAIL-TEST-001",
            "name": "Test",
            "when": when,
            "then": {"category": "03", "confidence": confidence, "hard": hard, "document_type": "beleg"},
        }
    )


def test_betreffregel_nur_mit_textmerkmal_stark():
    mail = {"subject_clean": "Rechnung 4711", "from_address": "a@b.test", "from_name": "Firma"}
    nur_betreff = _rule({"email_subject_regex": [r"(?i)\brechnung\b"]})
    hit = matches(nur_betreff, _ctx("anbei die Unterlagen", mail))
    assert hit is not None and hit.hard is False and hit.confidence == EMAIL_SUBJECT_ONLY_CONFIDENCE
    assert hit.matched == ["email_subject~(?i)\\brechnung\\b"]
    mit_text = _rule({"email_subject_regex": [r"(?i)\brechnung\b"], "text_regex": [r"(?i)zahlbar"]})
    assert matches(mit_text, _ctx("anbei die Unterlagen", mail)) is None
    hit = matches(mit_text, _ctx("Rechnungsbetrag zahlbar bis 10.10.2026", mail))
    assert hit is not None and hit.confidence == 0.9
    # keine E-Mail: Betreffregel greift nicht, auch wenn der Text das Wort traegt
    assert matches(nur_betreff, _ctx("Betreff: Rechnung 4711", None)) is None
    with pytest.raises(RuleError, match="hart"):
        _rule({"email_subject_regex": [r"(?i)rechnung"]}, hard=True)
    # hart erlaubt, wenn ein Textmerkmal dabei ist
    _rule({"email_subject_regex": [r"(?i)rechnung"], "text_regex": [r"zahlbar"]}, hard=True)


def test_betreff_in_any_of_zaehlt_textmuster_nicht_als_textmerkmal():
    """Gegenpruefung 26.09.2026: any_of mit Betreff- und Textmuster ist schon ueber den Betreff erfuellt, die
    Textmuster darin sind kein Textmerkmal; der Treffer bleibt weich und gedeckelt, hart ist unzulaessig."""
    mail = {"subject_clean": "Rechnung 4711", "from_address": "a@b.test", "from_name": "Firma"}
    when = {"any_of": {"email_subject_regex": [r"(?i)rechnung"], "text_regex": [r"(?i)zahlbar"]}}
    assert has_text_feature(_rule(when)) is False
    hit = matches(_rule(when), _ctx("Danke für das Gespräch", mail))
    assert hit is not None and hit.hard is False and hit.confidence == EMAIL_SUBJECT_ONLY_CONFIDENCE
    with pytest.raises(RuleError, match="hart"):
        _rule(when, hard=True)
    # Textmuster auf oberster Ebene oder any_of ohne Betreffmuster bleiben Textmerkmal
    assert has_text_feature(_rule({"email_subject_regex": ["x"], "any_of": {"text_regex": ["y"]}})) is True
    assert has_text_feature(_rule({"email_subject_regex": ["x"], "head_regex": ["y"]})) is True


def test_absenderregel_und_rule_kind():
    mail = {
        "subject_clean": "Info",
        "from_address": "schaden@versicherung.example",
        "from_name": "Musterversicherung AG",
    }
    adresse = _rule({"email_sender_regex": [r"@versicherung\.example$"]})
    hit = matches(adresse, _ctx("Guten Tag", mail))
    assert (
        hit is not None and hit.confidence == 0.9 and hit.matched == ["email_sender~@versicherung\\.example$"]
    )
    name = _rule({"email_sender_regex": [r"(?i)musterversicherung"]})
    assert matches(name, _ctx("Guten Tag", mail)) is not None
    assert matches(name, _ctx("Guten Tag", None)) is None
    assert rule_kind_for(adresse) == "email_sender"
    assert (
        rule_kind_for(_rule({"email_subject_regex": ["x"], "email_sender_regex": ["y"]})) == "email_subject"
    )
    assert rule_kind_for(_rule({"text_regex": ["x"]})) == "composite"
    with pytest.raises(RuleError, match="ungültiger Ausdruck"):
        _rule({"email_sender_regex": ["("]})
