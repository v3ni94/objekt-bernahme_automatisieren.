"""PaperlessClient gegen eine Sitzungsattrappe ohne Netz: Header, Paginierung, Backoff, Fehlerabbildung, Downloads."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from apps.drive.backoff import BackoffConfig
from apps.sync.paperless.client import (
    PaperlessClient,
    mask_headers,
    normalize_task,
    parse_retry_after,
)
from apps.sync.paperless.errors import (
    PaperlessAuthError,
    PaperlessError,
    PaperlessNotFound,
    PaperlessRateLimited,
    PaperlessSchemaError,
    PaperlessUnavailable,
    PaperlessUnsupported,
)

BASE = "https://paperless.example.test"


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, *, headers=None, content: bytes = b""):
        self.status_code = status_code
        self._json = json_data
        self.headers = CaseInsensitiveDict(headers or {})
        self.content = content if json_data is None else json.dumps(json_data).encode()
        self.closed = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self):
        if self._json is None:
            return json.loads(self.content)
        return self._json

    def iter_content(self, chunk_size: int = 1):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Routen: (METHODE, Pfad) -> Liste von Antworten oder Ausnahmen (der Reihe nach), letzte bleibt bestehen."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[dict] = []

    def add(self, method: str, path: str, *responses) -> None:
        self.routes.setdefault((method.upper(), path), []).extend(responses)

    def request(self, method: str, url: str, **kw):
        parsed = urlparse(url)
        query = {k: v if len(v) > 1 else v[0] for k, v in parse_qs(parsed.query).items()}
        params = kw.get("params")
        if isinstance(params, dict):
            query.update(params)
        self.calls.append({"method": method.upper(), "path": parsed.path, "query": query, **kw})
        queue = self.routes.get((method.upper(), parsed.path))
        if not queue:
            raise AssertionError(f"keine Route fuer {method} {parsed.path}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item) and not isinstance(item, FakeResponse):
            item = item(self.calls[-1])
        if isinstance(item, BaseException):
            raise item
        return item


def make_client(session: FakeSession, **kw) -> PaperlessClient:
    sleeps: list[float] = []
    client = PaperlessClient(
        BASE + "/",
        "geheim-token",
        session=session,
        backoff=kw.pop("backoff", BackoffConfig(base_s=0.5, factor=2.0, max_delay_s=4.0, max_attempts=3)),
        sleep=sleeps.append,
        **kw,
    )
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client


def page(results: list, *, count: int | None = None, next_url: str | None = None) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "count": count if count is not None else len(results),
            "next": next_url,
            "previous": None,
            "results": results,
        },
        headers={"X-Version": "2.15.3", "X-Api-Version": "10"},
    )


# --- Header und Konfiguration ----------------------------------------------------------------------
def test_token_header_und_accept_version():
    s = FakeSession()
    s.add("GET", "/api/documents/7/", FakeResponse(200, {"id": 7, "title": "Teilungserklaerung"}))
    c = make_client(s, api_version=9, timeout_s=12, verify_tls=False)
    assert c.get_document(7)["title"] == "Teilungserklaerung"
    call = s.calls[0]
    assert call["headers"]["Authorization"] == "Token geheim-token"
    assert call["headers"]["Accept"] == "application/json; version=9"
    assert call["timeout"] == 12 and call["verify"] is False and call["allow_redirects"] is False
    assert mask_headers(call["headers"])["Authorization"] == "Token ***"
    assert "geheim-token" not in repr(mask_headers(call["headers"]))


def test_ungueltige_konfiguration():
    with pytest.raises(ValueError):
        PaperlessClient(BASE, "", session=FakeSession())
    with pytest.raises(ValueError):
        PaperlessClient("", "t", session=FakeSession())
    with pytest.raises(ValueError):
        PaperlessClient(BASE, "t", api_version=7, session=FakeSession())


# --- Paginierung -----------------------------------------------------------------------------------
def test_pagination_ueber_zwei_seiten_und_filterparameter():
    s = FakeSession()
    s.add(
        "GET",
        "/api/documents/",
        page([{"id": 1}, {"id": 2}], count=3, next_url=f"{BASE}/api/documents/?page=2"),
        page([{"id": 3}], count=3),
    )
    c = make_client(s, page_size=2)
    docs = list(c.list_documents(modified_after="2026-09-01T00:00:00+00:00", fields=["id", "modified"]))
    assert [d["id"] for d in docs] == [1, 2, 3]
    assert len(s.calls) == 2
    q1, q2 = s.calls[0]["query"], s.calls[1]["query"]
    assert q1["page"] == 1 and q2["page"] == 2
    assert q1["page_size"] == 2 and q1["ordering"] == "id"
    assert q1["modified__gt"] == "2026-09-01T00:00:00+00:00" and q1["fields"] == "id,modified"
    assert "added__gt" not in q1


def test_iter_pages_liefert_seitenstand_und_wiederaufnahme():
    s = FakeSession()
    s.add(
        "GET",
        "/api/documents/",
        page([{"id": 3}], count=4, next_url="x"),
        page([{"id": 4}], count=4),
    )
    c = make_client(s, page_size=1)
    pages = list(c.iter_pages(start_page=3))
    assert [p.number for p in pages] == [3, 4]
    assert pages[0].has_next and pages[0].next_number == 4
    assert not pages[1].has_next and pages[1].next_number is None
    assert s.calls[0]["query"]["page"] == 3


def test_ids_filter_wird_in_bloecke_zerlegt():
    s = FakeSession()
    s.add(
        "GET",
        "/api/documents/",
        lambda call: page([{"id": int(i)} for i in call["query"]["id__in"].split(",")]),
    )
    c = make_client(s)
    docs = list(c.list_documents(ids=range(1, 151)))
    assert len(docs) == 150 and len(s.calls) == 2
    assert s.calls[0]["query"]["id__in"].startswith("1,2,3")
    assert list(c.list_documents(ids=[])) == [] and len(s.calls) == 2


# --- Backoff und Fehlerabbildung ---------------------------------------------------------------------
def test_429_mit_retry_after_wird_wiederholt():
    s = FakeSession()
    s.add(
        "GET",
        "/api/tags/",
        FakeResponse(429, {"detail": "zu viele Anfragen"}, headers={"Retry-After": "3"}),
        page([{"id": 1, "name": "Objektakte"}]),
    )
    c = make_client(s)
    assert c.list_tags() == [{"id": 1, "name": "Objektakte"}]
    assert c.sleeps == [3.0] and c.stats["rate_limited"] == 1 and c.stats["retries"] == 1


def test_5xx_wird_bis_zur_grenze_wiederholt():
    s = FakeSession()
    s.add("GET", "/api/documents/9/", FakeResponse(503, content=b"wartung"))
    c = make_client(s, backoff=BackoffConfig(base_s=0.1, factor=2.0, max_delay_s=1.0, max_attempts=3))
    with pytest.raises(PaperlessUnavailable) as exc:
        c.get_document(9)
    assert exc.value.status_code == 503 and len(s.calls) == 3 and len(c.sleeps) == 2
    assert all(0 <= d <= 1.0 for d in c.sleeps)


def test_verbindungsfehler_bei_get_wird_wiederholt():
    s = FakeSession()
    s.add(
        "GET", "/api/documents/1/", requests.exceptions.ConnectionError("reset"), FakeResponse(200, {"id": 1})
    )
    c = make_client(s)
    assert c.get_document(1) == {"id": 1} and len(s.calls) == 2


def test_401_und_403_werden_zu_autorisierungsfehlern_ohne_wiederholung():
    s = FakeSession()
    s.add("GET", "/api/documents/1/", FakeResponse(401, {"detail": "Invalid token."}))
    s.add("GET", "/api/documents/2/", FakeResponse(403, {"detail": "verboten"}))
    c = make_client(s)
    with pytest.raises(PaperlessAuthError) as exc:
        c.get_document(1)
    assert exc.value.status_code == 401 and "geheim-token" not in str(exc.value)
    with pytest.raises(PaperlessAuthError):
        c.get_document(2)
    assert len(s.calls) == 2 and c.sleeps == []


def test_404_wird_zu_not_found_und_400_zu_basisfehler():
    s = FakeSession()
    s.add("GET", "/api/documents/404/", FakeResponse(404, {"detail": "Not found."}))
    s.add("PATCH", "/api/documents/5/", FakeResponse(400, {"title": ["darf nicht leer sein"]}))
    c = make_client(s)
    with pytest.raises(PaperlessNotFound):
        c.get_document(404)
    with pytest.raises(PaperlessError) as exc:
        c.patch_document(5, title="")
    assert exc.value.status_code == 400 and exc.value.payload == {"title": ["darf nicht leer sein"]}
    assert not isinstance(exc.value, PaperlessNotFound | PaperlessAuthError)


def test_retry_after_als_zahl_und_datum():
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after(None) is None and parse_retry_after("bald") is None
    from datetime import UTC, datetime

    now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    assert parse_retry_after("Sat, 12 Sep 2026 10:00:30 GMT", now=lambda: now) == 30.0
    assert parse_retry_after("Sat, 12 Sep 2026 09:00:00 GMT", now=lambda: now) == 0.0


# --- Upload und Aufgaben -------------------------------------------------------------------------------
def test_post_document_liefert_task_uuid_und_sendet_multipart(tmp_path):
    f = tmp_path / "rechnung.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    s = FakeSession()
    s.add("POST", "/api/documents/post_document/", FakeResponse(200, "0c8b1c9e-1111-2222-3333-444455556666"))
    c = make_client(s)
    task = c.post_document(
        f,
        title="Rechnung Sept",
        created="2026-09-01",
        tags=[3, 5],
        custom_fields={12: "623"},
        document_type=2,
        correspondent=9,
    )
    assert task == "0c8b1c9e-1111-2222-3333-444455556666"
    call = s.calls[0]
    data = call["data"]
    assert ("title", "Rechnung Sept") in data and ("created", "2026-09-01") in data
    assert [v for k, v in data if k == "tags"] == ["3", "5"]
    assert ("document_type", "2") in data and ("correspondent", "9") in data
    assert json.loads(dict(data)["custom_fields"]) == {"12": "623"}
    assert call["files"]["document"][0] == "rechnung.pdf"


def test_post_document_wird_bei_verbindungsfehler_und_5xx_nicht_wiederholt(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    s = FakeSession()
    s.add("POST", "/api/documents/post_document/", requests.exceptions.ConnectionError("abbruch"))
    c = make_client(s)
    with pytest.raises(PaperlessUnavailable):
        c.post_document(f, title="A")
    assert len(s.calls) == 1 and c.sleeps == []

    s2 = FakeSession()
    s2.add(
        "POST",
        "/api/documents/post_document/",
        FakeResponse(502, content=b"bad gateway"),
        FakeResponse(200, "uuid"),
    )
    c2 = make_client(s2)
    with pytest.raises(PaperlessUnavailable):
        c2.post_document(f, title="A")
    assert len(s2.calls) == 1


def test_post_document_wird_bei_429_wiederholt(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    s = FakeSession()
    s.add(
        "POST",
        "/api/documents/post_document/",
        FakeResponse(429, headers={"Retry-After": "1"}),
        FakeResponse(200, "uuid-nach-wartezeit"),
    )
    c = make_client(s)
    assert c.post_document(f, title="A") == "uuid-nach-wartezeit" and c.sleeps == [1.0]


def test_get_task_normalisiert_version_9_und_10():
    s = FakeSession()
    s.add(
        "GET",
        "/api/tasks/",
        FakeResponse(200, [{"task_id": "t1", "status": "SUCCESS", "result": "ok", "related_document": "42"}]),
        FakeResponse(
            200,
            [{"task_id": "t2", "status": "SUCCESS", "result": "ok", "related_document_ids": [43, 44]}],
        ),
        FakeResponse(200, {"count": 0, "next": None, "results": []}),
    )
    c = make_client(s)
    t1 = c.get_task("t1")
    assert t1["related_document_ids"] == [42] and t1["status"] == "SUCCESS" and t1["raw"]["task_id"] == "t1"
    assert s.calls[0]["query"]["task_id"] == "t1"
    assert c.get_task("t2")["related_document_ids"] == [43, 44]
    assert c.get_task("t3") is None
    assert normalize_task(
        {"task_id": "x", "status": "FAILURE", "result": "dup", "related_document": None}
    ) == {
        "task_id": "x",
        "status": "FAILURE",
        "result": "dup",
        "related_document_ids": [],
        "task_type": None,
        "date_done": None,
        "raw": {"task_id": "x", "status": "FAILURE", "result": "dup", "related_document": None},
    }


# --- Download --------------------------------------------------------------------------------------
def test_download_streamt_in_datei_mit_temporaerem_namen(tmp_path):
    payload = bytes(range(256)) * 20
    s = FakeSession()
    s.add("GET", "/api/documents/5/download/", FakeResponse(200, content=payload))
    s.add("GET", "/api/documents/5/thumb/", FakeResponse(200, content=b"PNG"))
    c = make_client(s)
    target = tmp_path / "ablage" / "tief" / "5.pdf"
    assert c.download(5, target) == target
    assert target.read_bytes() == payload and not (tmp_path / "ablage" / "tief" / "5.pdf.part").exists()
    assert s.calls[0]["stream"] is True and s.calls[0]["params"] == {"original": "true"}
    assert s.calls[0]["headers"]["Accept"] == "*/*"
    c.download(5, target, original=False)
    assert s.calls[1]["params"] is None
    assert c.download_thumbnail(5, tmp_path / "t.png").read_bytes() == b"PNG"


def test_download_abbruch_laesst_keine_teildatei_zurueck(tmp_path):
    class Abbruch(FakeResponse):
        def iter_content(self, chunk_size: int = 1):
            yield b"anfang"
            raise requests.exceptions.ChunkedEncodingError("abgebrochen")

    s = FakeSession()
    s.add("GET", "/api/documents/6/download/", Abbruch(200, content=b"egal"))
    c = make_client(s)
    target = tmp_path / "6.pdf"
    with pytest.raises(PaperlessUnavailable):
        c.download(6, target)
    assert not target.exists() and not (tmp_path / "6.pdf.part").exists()


# --- Serverinformationen -----------------------------------------------------------------------------
def _server_routes(
    s: FakeSession, *, schema: bool, versions_in_schema: bool = False, sample: dict | None = None
):
    s.add("GET", "/api/documents/", page([sample] if sample else [], count=1 if sample else 0))
    s.add("GET", "/api/custom_fields/", page([]))
    if schema:
        paths = {"/api/documents/bulk_edit/": {}, "/api/documents/{id}/": {}}
        if versions_in_schema:
            paths["/api/documents/{id}/update_version/"] = {}
        s.add("GET", "/api/schema/", FakeResponse(200, {"openapi": "3.0.3", "paths": paths}))
    else:
        s.add("GET", "/api/schema/", FakeResponse(404, {"detail": "Not found."}))
    s.add("GET", "/api/ui_settings/", FakeResponse(200, {"version": "2.15.3"}))
    s.add("GET", "/api/status/", FakeResponse(403, {"detail": "nur Administratoren"}))


def test_server_info_liest_x_version_und_funktionen():
    s = FakeSession()
    _server_routes(s, schema=True, versions_in_schema=True)
    c = make_client(s)
    info = c.server_info()
    assert info.server_version == "2.15.3" and info.api_version == 10
    assert info.features == {
        "document_versions": True,
        "custom_fields": True,
        "bulk_edit": True,
        "tasks_v10": True,
        "schema_available": True,
    }
    assert info.endpoints_seen["/api/schema/"] == 200 and info.endpoints_seen["/api/status/"] == 403
    assert "/api/ui_settings/" not in info.endpoints_seen  # X-Version vorhanden, kein Rueckgriff
    assert c.server_info() is info  # zwischengespeichert
    assert c.sleeps == []


def test_server_info_ohne_schema_und_ohne_versionen():
    s = FakeSession()
    _server_routes(s, schema=False, sample={"id": 1, "title": "x"})
    c = make_client(s)
    info = c.server_info()
    assert not info.supports("document_versions") and not info.supports("schema_available")
    assert info.supports("bulk_edit") and info.supports("custom_fields")


def test_server_info_erkennt_versionen_am_dokument_und_ui_settings_als_rueckfall():
    s = FakeSession()
    s.add(
        "GET",
        "/api/documents/",
        FakeResponse(
            200, {"count": 1, "next": None, "results": [{"id": 1, "versions": [], "root_document": None}]}
        ),
    )
    s.add("GET", "/api/custom_fields/", FakeResponse(404, {"detail": "Not found."}))
    s.add("GET", "/api/schema/", FakeResponse(500, content=b"kaputt"))
    s.add("GET", "/api/ui_settings/", FakeResponse(200, {"version": "2.9.0"}))
    s.add("GET", "/api/status/", FakeResponse(404))
    c = make_client(s, api_version=9)
    info = c.server_info()
    assert info.server_version == "2.9.0" and info.api_version == 9
    assert (
        info.supports("document_versions")
        and not info.supports("custom_fields")
        and not info.supports("tasks_v10")
    )
    assert c.sleeps == []  # Sondierung wiederholt 5xx nicht


def test_update_version_prueft_funktion(tmp_path):
    f = tmp_path / "v2.pdf"
    f.write_bytes(b"v2")
    s = FakeSession()
    _server_routes(s, schema=False)
    c = make_client(s)
    with pytest.raises(PaperlessUnsupported):
        c.update_version(1, f)
    s2 = FakeSession()
    _server_routes(s2, schema=True, versions_in_schema=True)
    s2.add("POST", "/api/documents/1/update_version/", FakeResponse(200, "task-v2"))
    c2 = make_client(s2)
    assert c2.update_version(1, f, label="Nachtrag") == "task-v2"
    call = s2.calls[-1]
    assert call["data"] == [("version_label", "Nachtrag")] and call["files"]["document"][0] == "v2.pdf"


# --- Bearbeitung und Stammdaten ----------------------------------------------------------------------
def test_bulk_edit_bequemlichkeiten_und_patch():
    s = FakeSession()
    s.add("POST", "/api/documents/bulk_edit/", FakeResponse(200, {"result": "OK"}))
    s.add("PATCH", "/api/documents/3/", FakeResponse(200, {"id": 3, "title": "Neu"}))
    c = make_client(s)
    assert c.add_tags(3, [4, 5]) == {"result": "OK"}
    assert s.calls[-1]["json"] == {
        "documents": [3],
        "method": "modify_tags",
        "parameters": {"add_tags": [4, 5], "remove_tags": []},
    }
    c.set_custom_field_values(3, {7: "623", 8: True})
    assert s.calls[-1]["json"]["parameters"] == {
        "add_custom_fields": {"7": "623", "8": True},
        "remove_custom_fields": [],
    }
    assert c.add_tags(3, []) == {"result": "OK"} and len(s.calls) == 2
    assert c.patch_document(3, title="Neu")["title"] == "Neu"
    assert s.calls[-1]["json"] == {"title": "Neu"}
    with pytest.raises(ValueError):
        c.patch_document(3)


def test_stammdaten_und_notizen():
    s = FakeSession()
    s.add("GET", "/api/tags/", page([{"id": 1, "name": "A"}], next_url="x"), page([{"id": 2, "name": "B"}]))
    s.add("POST", "/api/tags/", FakeResponse(201, {"id": 3, "name": "Neu"}))
    s.add("GET", "/api/custom_fields/", page([{"id": 1, "name": "Objekt", "data_type": "string"}]))
    s.add("POST", "/api/custom_fields/", FakeResponse(201, {"id": 2, "name": "Nr", "data_type": "integer"}))
    s.add("GET", "/api/document_types/", page([{"id": 1, "name": "Rechnung"}]))
    s.add("GET", "/api/correspondents/", page([{"id": 1, "name": "Stadtwerke"}]))
    s.add("GET", "/api/documents/1/notes/", FakeResponse(200, [{"id": 1, "note": "Hinweis"}]))
    s.add("GET", "/api/documents/1/metadata/", FakeResponse(200, {"original_checksum": "abc"}))
    c = make_client(s)
    assert [t["name"] for t in c.list_tags()] == ["A", "B"]
    assert c.create_tag("Neu")["id"] == 3 and s.calls[-1]["json"] == {"name": "Neu"}
    assert c.list_custom_fields()[0]["data_type"] == "string"
    assert c.create_custom_field("Nr", "integer", extra_data={"x": 1})["id"] == 2
    assert s.calls[-1]["json"] == {"name": "Nr", "data_type": "integer", "extra_data": {"x": 1}}
    assert c.list_document_types()[0]["name"] == "Rechnung"
    assert c.list_correspondents()[0]["name"] == "Stadtwerke"
    assert c.get_notes(1) == [{"id": 1, "note": "Hinweis"}]
    assert c.get_metadata(1)["original_checksum"] == "abc"


def test_schemafehler_bei_unlesbarer_antwort():
    s = FakeSession()
    s.add("GET", "/api/documents/1/", FakeResponse(200, content=b"<html>Anmeldung</html>"))
    s.add("GET", "/api/documents/", FakeResponse(200, {"irgendwas": 1}))
    c = make_client(s)
    with pytest.raises(PaperlessSchemaError):
        c.get_document(1)
    with pytest.raises(PaperlessSchemaError):
        list(c.list_documents())


# --- from_settings -----------------------------------------------------------------------------------
def test_from_settings_ohne_konfiguration_liefert_none(monkeypatch, settings):
    from apps.config import store
    from apps.sync.paperless import client as mod

    values: dict[str, object] = {}

    def fake_get(key, default=None):
        if key not in values:
            raise store.UnknownSetting(key)
        return values[key]

    monkeypatch.setattr(store, "get", fake_get)
    settings.OBJEKTAKTE = {**settings.OBJEKTAKTE, "PAPERLESS_TOKEN": ""}
    assert mod.from_settings() is None
    values["paperless.base_url"] = "https://paperless.example.test/"
    assert mod.from_settings() is None  # Token fehlt
    settings.OBJEKTAKTE = {**settings.OBJEKTAKTE, "PAPERLESS_TOKEN": "tok"}
    values.update({"paperless.api_version": 9, "paperless.timeout_seconds": 15, "paperless.page_size": 25})
    c = mod.from_settings(session=FakeSession())
    assert c is not None and c.base_url == "https://paperless.example.test"
    assert c.api_version == 9 and c.timeout_s == 15 and c.page_size == 25 and c.verify_tls is True


def test_sleep_ist_injizierbar_und_kein_echtes_warten():
    s = FakeSession()
    s.add("GET", "/api/documents/1/", FakeResponse(500), FakeResponse(200, {"id": 1}))
    waits: list[float] = []
    sleep: Callable[[float], None] = waits.append
    c = PaperlessClient(BASE, "t", session=s, sleep=sleep, backoff=BackoffConfig(base_s=0.2, max_attempts=2))
    assert c.get_document(1) == {"id": 1} and len(waits) == 1 and 0 <= waits[0] <= 0.2


def test_rate_limited_fehler_traegt_retry_after():
    s = FakeSession()
    s.add("GET", "/api/documents/1/", FakeResponse(429, headers={"Retry-After": "7"}))
    c = make_client(s, backoff=BackoffConfig(max_attempts=1))
    with pytest.raises(PaperlessRateLimited) as exc:
        c.get_document(1)
    assert exc.value.retry_after == 7.0 and exc.value.status_code == 429 and len(s.calls) == 1
