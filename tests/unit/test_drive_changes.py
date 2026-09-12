"""Aenderungsprotokoll, appProperties, Export und Revisionsangaben des Drive-Adapters (Synchronisation 12.09.2026).

In-Memory-Fake als Referenz fuer das Verhalten von changes.getStartPageToken und changes.list; der echte Client wird
ohne Netz gegen einen aufzeichnenden HTTP-Transport geprueft (Parameter, Felder, Cursorfehler)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from apps.drive.adapter import (
    READ_METHODS,
    WRITE_METHODS,
    ChangePage,
    DriveChange,
    DriveCursorInvalid,
    DriveError,
    InMemoryDriveAdapter,
    NotFound,
    PermanentError,
    RecordingDriveAdapter,
    export_placeholder,
)
from apps.drive.google_adapter import CHANGE_FIELDS, FIELDS, GoogleDriveAdapter, build_service


def _fake_mit_bestand() -> tuple[InMemoryDriveAdapter, dict[str, str]]:
    d = InMemoryDriveAdapter()
    ids = d.load_scenario(d.root_id, {"623 Musterstadt": {"a.pdf": "1", "b.pdf": "2"}})
    return d, ids


def test_cursor_und_drei_aenderungen():
    d, ids = _fake_mit_bestand()
    obj = ids["623 Musterstadt"]
    token = d.start_page_token()
    assert token.isdigit()
    leer = d.list_changes(token)
    assert leer.changes == [] and leer.next_page_token is None and leer.new_start_page_token == token

    neu = d.add_file(obj, "c.pdf", b"3")
    d.rename(ids["623 Musterstadt/a.pdf"], "a_neu.pdf")
    d.trash(ids["623 Musterstadt/b.pdf"])

    page = d.list_changes(token)
    assert isinstance(page, ChangePage) and page.next_page_token is None
    assert [c.file_id for c in page.changes] == [
        neu,
        ids["623 Musterstadt/a.pdf"],
        ids["623 Musterstadt/b.pdf"],
    ]
    c_neu, c_ren, c_trash = page.changes
    assert isinstance(c_neu, DriveChange) and not c_neu.removed and c_neu.node.name == "c.pdf"
    assert not c_ren.removed and c_ren.node.name == "a_neu.pdf" and not c_ren.node.trashed
    assert not c_trash.removed and c_trash.node.trashed is True  # Papierkorb wie die echte API: file.trashed
    assert all(c.time for c in page.changes) and c_neu.time <= c_ren.time <= c_trash.time

    assert page.new_start_page_token and int(page.new_start_page_token) > int(token)
    danach = d.list_changes(page.new_start_page_token)
    assert danach.changes == [] and danach.new_start_page_token == page.new_start_page_token


def test_pagination_und_letzter_stand_je_datei():
    d, ids = _fake_mit_bestand()
    obj = ids["623 Musterstadt"]
    token = d.start_page_token()
    neu = d.add_file(obj, "c.pdf", b"3")
    d.rename(neu, "c_neu.pdf")  # dieselbe Datei: nur der letzte Stand
    d.rename(ids["623 Musterstadt/a.pdf"], "a_neu.pdf")
    d.trash(ids["623 Musterstadt/b.pdf"])

    gesehen: list[DriveChange] = []
    seiten = 0
    cursor = token
    while True:
        page = d.list_changes(cursor, page_size=1)
        seiten += 1
        assert len(page.changes) <= 1
        gesehen.extend(page.changes)
        if page.new_start_page_token:
            assert page.next_page_token is None
            break
        assert page.next_page_token
        cursor = page.next_page_token
    assert seiten == 3 and [c.file_id for c in gesehen] == [
        neu,
        ids["623 Musterstadt/a.pdf"],
        ids["623 Musterstadt/b.pdf"],
    ]
    assert gesehen[0].node.name == "c_neu.pdf"


def test_endgueltige_loeschung_und_ungueltiger_cursor():
    d, ids = _fake_mit_bestand()
    token = d.start_page_token()
    d.delete_permanently(ids["623 Musterstadt/a.pdf"])
    page = d.list_changes(token)
    assert len(page.changes) == 1
    assert page.changes[0].removed is True and page.changes[0].node is None
    assert d.get(ids["623 Musterstadt/a.pdf"]) is None
    with pytest.raises(DriveCursorInvalid):
        d.list_changes("abc")
    with pytest.raises(DriveCursorInvalid):
        d.list_changes(str(int(page.new_start_page_token) + 5))
    assert issubclass(DriveCursorInvalid, DriveError)


def test_set_app_properties_sichtbar_in_get():
    d, ids = _fake_mit_bestand()
    fid = ids["623 Musterstadt/a.pdf"]
    token = d.start_page_token()
    node = d.set_app_properties(fid, {"objektakte_uuid": "u-1", "sha256": "abc"})
    assert node.app_properties == {"objektakte_uuid": "u-1", "sha256": "abc"}
    assert d.get(fid).app_properties == {"objektakte_uuid": "u-1", "sha256": "abc"}
    d.set_app_properties(fid, {"sha256": None})  # None entfernt den Schluessel
    assert d.get(fid).app_properties == {"objektakte_uuid": "u-1"}
    with pytest.raises(PermanentError):
        d.set_app_properties(fid, {"anzahl": 3})
    page = d.list_changes(token)
    assert [c.file_id for c in page.changes] == [fid]
    with pytest.raises(NotFound):
        d.set_app_properties("gibt-es-nicht", {"a": "b"})


def test_export_google_dokument_und_pdf(tmp_path):
    d, ids = _fake_mit_bestand()
    doc = d.add_google_doc(ids["623 Musterstadt"], "Protokoll")
    ziel = tmp_path / "export" / "protokoll.pdf"
    ergebnis = d.export(doc, "application/pdf", ziel)
    assert (
        ergebnis == ziel and ziel.read_bytes().startswith(b"%PDF-1.4") and b"Protokoll" in ziel.read_bytes()
    )
    assert ziel.read_bytes() == export_placeholder("Protokoll", "application/pdf")
    with pytest.raises(DriveError):
        d.export(ids["623 Musterstadt/a.pdf"], "application/pdf", tmp_path / "a.pdf")
    assert not (tmp_path / "a.pdf").exists()


def test_revisionen_und_pruefsummen():
    d, ids = _fake_mit_bestand()
    fid = ids["623 Musterstadt/a.pdf"]
    vorher = d.get(fid)
    assert vorher.version == "1" and vorher.head_revision_id == "rev-1" and len(vorher.sha256) == 64
    info = d.get_revision_info(fid)
    assert info == {
        "head_revision_id": "rev-1",
        "version": "1",
        "modified_time": vorher.modified_time,
        "md5": vorher.md5,
        "sha256": vorher.sha256,
        "size": 1,
    }
    d.set_content(fid, b"neu")
    nachher = d.get(fid)
    assert nachher.version == "2" and nachher.head_revision_id == "rev-2" and nachher.sha256 != vorher.sha256
    d.rename(fid, "umbenannt.pdf")
    assert d.get(fid).version == "2"  # Umbenennung ist keine Inhaltsaenderung
    ordner = d.get(ids["623 Musterstadt"])
    assert ordner.sha256 is None and ordner.md5 is None and ordner.drive_id is None
    with pytest.raises(NotFound):
        d.get_revision_info("gibt-es-nicht")
    geteilt = InMemoryDriveAdapter(drive_id="0AShared")
    assert geteilt.get(geteilt.root_id).drive_id == "0AShared"
    assert geteilt.list_changes(geteilt.start_page_token()).new_start_page_token


def test_recording_adapter_lesemethoden_bleiben_lesend(tmp_path):
    inner, ids = _fake_mit_bestand()
    doc = inner.add_google_doc(ids["623 Musterstadt"], "Doc")
    rec = RecordingDriveAdapter(inner)
    token = rec.start_page_token()
    rec.list_changes(token)
    rec.export(doc, "application/pdf", tmp_path / "doc.pdf")
    rec.get_revision_info(ids["623 Musterstadt/a.pdf"])
    assert rec.read_only and {c[0] for c in rec.calls} <= READ_METHODS
    rec.set_app_properties(ids["623 Musterstadt/a.pdf"], {"k": "v"})
    assert not rec.read_only and rec.write_calls[0][0] == "set_app_properties"
    assert "set_app_properties" in WRITE_METHODS and not (READ_METHODS & WRITE_METHODS)


# ---------------------------------------------------------------- echter Client ohne Netz
class _RecordingHttp:
    """HTTP-Transport fuer den Discovery-Client: zeichnet Anfragen auf und liefert vorbereitete Antworten."""

    def __init__(self, responses: list[tuple[int, dict]]):
        import httplib2

        self._httplib2 = httplib2
        self._responses = list(responses)
        self.requests: list[tuple[str, str, dict | None]] = []

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        self.requests.append((method, uri, json.loads(body) if body else None))
        status, payload = self._responses.pop(0)
        resp = self._httplib2.Response({"status": status, "content-type": "application/json"})
        return resp, json.dumps(payload).encode()


def _query(uri: str) -> dict[str, str]:
    """Abfrageparameter ohne das vom Discovery-Client stets gesetzte alt=json."""
    return {k: v[0] for k, v in parse_qs(urlparse(uri).query).items() if k != "alt"}


def _file_item(file_id: str, **extra) -> dict:
    item = {
        "id": file_id,
        "name": "x.pdf",
        "mimeType": "application/pdf",
        "parents": ["p1"],
        "size": "3",
        "md5Checksum": "m",
        "sha256Checksum": "s" * 64,
        "headRevisionId": "0B-rev",
        "version": "7",
        "driveId": "0AShared",
        "modifiedTime": "2026-09-12T10:00:00.000Z",
    }
    item.update(extra)
    return item


def test_google_adapter_changes():
    http = _RecordingHttp(
        [
            (200, {"startPageToken": "4711"}),
            (
                200,
                {
                    "nextPageToken": "4712",
                    "changes": [
                        {
                            "fileId": "f1",
                            "removed": False,
                            "time": "2026-09-12T10:00:01.000Z",
                            "driveId": "0AShared",
                            "file": _file_item("f1"),
                        },
                        {"fileId": "f2", "removed": True, "time": "2026-09-12T10:00:02.000Z"},
                    ],
                },
            ),
            (200, {"newStartPageToken": "4720", "changes": []}),
            (404, {"error": {"errors": [{"reason": "notFound"}], "code": 404, "message": "Invalid Value"}}),
        ]
    )
    adapter = GoogleDriveAdapter(build_service(None, http=http), drive_id="0AShared", sleep=lambda s: None)

    assert adapter.start_page_token() == "4711"
    method, uri, body = http.requests[0]
    assert method == "GET" and "/changes/startPageToken" in uri
    assert _query(uri) == {"supportsAllDrives": "true", "driveId": "0AShared"}

    page = adapter.list_changes("4711", page_size=500)
    q = _query(http.requests[1][1])
    assert "/changes?" in http.requests[1][1]
    assert q["pageToken"] == "4711" and q["pageSize"] == "500" and q["driveId"] == "0AShared"
    assert (
        q["includeItemsFromAllDrives"] == "true"
        and q["supportsAllDrives"] == "true"
        and q["includeRemoved"] == "true"
        and q["restrictToMyDrive"] == "false"
        and q["spaces"] == "drive"
        and q["fields"] == CHANGE_FIELDS
    )
    assert page.next_page_token == "4712" and page.new_start_page_token is None
    c1, c2 = page.changes
    assert c1.file_id == "f1" and not c1.removed and c1.drive_id == "0AShared"
    assert c1.node.sha256 == "s" * 64 and c1.node.head_revision_id == "0B-rev" and c1.node.version == "7"
    assert c1.node.drive_id == "0AShared" and c1.node.size == 3
    assert c2.file_id == "f2" and c2.removed and c2.node is None

    letzte = adapter.list_changes("4712", drive_id="0AAndere")
    assert _query(http.requests[2][1])["driveId"] == "0AAndere"
    assert letzte.changes == [] and letzte.next_page_token is None and letzte.new_start_page_token == "4720"

    with pytest.raises(DriveCursorInvalid) as exc:
        adapter.list_changes("veraltet")
    assert exc.value.status == 404


def test_google_adapter_start_page_token_ohne_geteilte_ablage():
    http = _RecordingHttp([(200, {"startPageToken": "1"})])
    adapter = GoogleDriveAdapter(build_service(None, http=http), sleep=lambda s: None)
    assert adapter.start_page_token() == "1"
    assert _query(http.requests[0][1]) == {"supportsAllDrives": "true"}


def test_google_adapter_app_properties_und_revisionsinfo():
    item = _file_item("f1", appProperties={"objektakte_uuid": "u-1"})
    http = _RecordingHttp([(200, item), (200, item)])
    adapter = GoogleDriveAdapter(build_service(None, http=http), sleep=lambda s: None)
    node = adapter.set_app_properties("f1", {"objektakte_uuid": "u-1", "alt": None})
    method, uri, body = http.requests[0]
    assert method == "PATCH" and "/files/f1" in uri
    assert body == {"appProperties": {"objektakte_uuid": "u-1", "alt": None}}
    assert _query(uri)["supportsAllDrives"] == "true" and _query(uri)["fields"] == FIELDS
    assert node.app_properties == {"objektakte_uuid": "u-1"} and node.version == "7"
    with pytest.raises(PermanentError):
        adapter.set_app_properties("f1", {"anzahl": 1})
    assert len(http.requests) == 1  # abgewiesen, bevor ein Aufruf erfolgt

    info = adapter.get_revision_info("f1")
    assert info["head_revision_id"] == "0B-rev" and info["version"] == "7" and info["size"] == 3
    assert (
        info["sha256"] == "s" * 64 and info["md5"] == "m" and _query(http.requests[1][1])["fields"] == FIELDS
    )


def test_google_adapter_export_nur_fuer_google_dokumente(tmp_path):
    http = _RecordingHttp([(200, _file_item("f1"))])
    adapter = GoogleDriveAdapter(build_service(None, http=http), sleep=lambda s: None)
    with pytest.raises(PermanentError, match="kein Google-Dokument"):
        adapter.export("f1", "application/pdf", tmp_path / "f1.pdf")
    assert len(http.requests) == 1 and not (tmp_path / "f1.pdf").exists()
