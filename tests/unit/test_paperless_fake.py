"""FakePaperless: Anlage, Aufgaben, Dubletten, Filter, Versionen, Fehlerinjektion und Aufzeichnung."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from apps.sync.paperless.client import Page
from apps.sync.paperless.errors import (
    PaperlessError,
    PaperlessNotFound,
    PaperlessRateLimited,
    PaperlessUnavailable,
    PaperlessUnsupported,
)
from apps.sync.paperless.fake import FakePaperless


def test_anlage_stammdaten_und_dokument():
    p = FakePaperless()
    tag = p.create_tag("Objekt 623")
    feld = p.create_custom_field("Objektnummer", "string")
    typ = p.create_document_type("Rechnung")
    assert tag["id"] == 1 and feld["id"] == 1 and typ["id"] == 1  # je Art fortlaufend
    assert p.create_tag("Eingang")["id"] == 2
    with pytest.raises(PaperlessError):
        p.create_tag("objekt 623")  # Namen sind eindeutig
    doc_id = p.add_document(
        "Rechnung Stadtwerke",
        b"%PDF-1.4 inhalt",
        tags=[tag["id"]],
        custom_fields={feld["id"]: "623"},
        document_type=typ["id"],
        notes=["Hinweis"],
    )
    doc = p.get_document(doc_id)
    assert doc["id"] == 1 and doc["tags"] == [1] and doc["custom_fields"] == [{"field": 1, "value": "623"}]
    assert doc["checksum"] == hashlib.sha256(b"%PDF-1.4 inhalt").hexdigest()
    assert doc["added"] == doc["modified"] and doc["original_file_name"] == "Rechnung Stadtwerke.pdf"
    meta = p.get_metadata(doc_id)
    assert meta["original_checksum"] == doc["checksum"] and meta["original_size"] == 15
    assert meta["has_archive_version"] is False
    assert p.get_notes(doc_id)[0]["note"] == "Hinweis"
    assert [t["name"] for t in p.list_tags()] == ["Objekt 623", "Eingang"]
    assert (
        p.list_custom_fields()[0]["data_type"] == "string"
        and p.list_document_types()[0]["name"] == "Rechnung"
    )
    with pytest.raises(PaperlessNotFound):
        p.get_document(99)


def test_post_document_aufgabe_und_verarbeitung(tmp_path):
    p = FakePaperless()
    f = tmp_path / "neu.pdf"
    f.write_bytes(b"neu")
    task_id = p.post_document(f, title="Neu", tags=[1], custom_fields={2: "x"}, correspondent=4)
    task = p.get_task(task_id)
    assert task["status"] == "PENDING" and task["related_document_ids"] == [] and p.documents == {}
    assert p.get_task("unbekannt") is None
    ergebnisse = p.process_tasks()
    assert len(ergebnisse) == 1 and ergebnisse[0]["status"] == "SUCCESS"
    task = p.get_task(task_id)
    assert task["status"] == "SUCCESS" and task["related_document_ids"] == [1]
    assert "New document id 1" in task["result"] and task["raw"]["related_document_ids"] == [1]
    doc = p.get_document(1)
    assert doc["title"] == "Neu" and doc["tags"] == [1] and doc["correspondent"] == 4
    assert doc["custom_fields"] == [{"field": 2, "value": "x"}] and doc["original_file_name"] == "neu.pdf"
    assert p.process_tasks() == []  # nichts mehr offen


def test_get_task_version_9_liefert_related_document_normalisiert(tmp_path):
    p = FakePaperless(api_version=9)
    f = tmp_path / "a.pdf"
    f.write_bytes(b"a")
    task_id = p.post_document(f, title="A")
    p.process_tasks()
    task = p.get_task(task_id)
    assert task["related_document_ids"] == [1] and task["raw"]["related_document"] == "1"
    assert "related_document_ids" not in task["raw"]
    assert p.server_info().features["tasks_v10"] is False


def test_dublette_wird_abgewiesen_oder_angelegt(tmp_path):
    f = tmp_path / "gleich.pdf"
    f.write_bytes(b"identisch")
    p = FakePaperless()
    p.add_document("Original", b"identisch")
    task_id = p.post_document(f, title="Kopie")
    p.process_tasks()
    task = p.get_task(task_id)
    assert task["status"] == "FAILURE" and "is a duplicate of Original (#1)" in task["result"]
    assert task["related_document_ids"] == [] and len(p.documents) == 1

    p2 = FakePaperless(reject_duplicates=False)
    p2.add_document("Original", b"identisch")
    t2 = p2.post_document(f, title="Kopie")
    p2.process_tasks()
    assert p2.get_task(t2)["status"] == "SUCCESS" and len(p2.documents) == 2


def test_filter_pagination_und_modified_fortschreibung():
    p = FakePaperless(page_size=2)
    ids = [p.add_document(f"Dok {i}", str(i).encode()) for i in range(1, 6)]
    stand = p.get_document(ids[2])["modified"]
    p.add_tags(ids[0], [9])  # aeltestes Dokument wird geaendert
    assert p.get_document(ids[0])["modified"] > stand
    geaendert = [d["id"] for d in p.list_documents(modified_after=stand)]
    assert geaendert == [ids[0], ids[3], ids[4]]
    hinzugefuegt = [d["id"] for d in p.list_documents(added_after=stand)]
    assert hinzugefuegt == [ids[3], ids[4]]
    assert [d["id"] for d in p.list_documents(ids=[ids[4], ids[1]])] == [ids[1], ids[4]]
    assert [d["id"] for d in p.list_documents(ordering="-id")][:2] == [ids[4], ids[3]]
    pages = list(p.iter_pages())
    assert [pg.number for pg in pages] == [1, 2, 3] and all(isinstance(pg, Page) for pg in pages)
    assert pages[0].count == 5 and pages[0].has_next and not pages[2].has_next and len(pages[2].results) == 1
    assert [pg.number for pg in p.iter_pages(start_page=3)] == [3]
    assert list(p.iter_pages(start_page=9)) == []
    nur_felder = next(iter(p.list_documents(fields=["id", "title"])))
    assert set(nur_felder) == {"id", "title"}
    zeit = datetime(2030, 1, 1, tzinfo=UTC)
    assert list(p.list_documents(modified_after=zeit)) == []


def test_bulk_edit_patch_und_custom_fields_bleiben_erhalten():
    p = FakePaperless()
    a = p.add_document("A", b"a", tags=[1], custom_fields={1: "alt", 2: "bleibt"})
    b = p.add_document("B", b"b")
    p.set_custom_field_values(a, {1: "neu", 3: 5})
    assert p.get_document(a)["custom_fields"] == [
        {"field": 1, "value": "neu"},
        {"field": 2, "value": "bleibt"},
        {"field": 3, "value": 5},
    ]
    p.bulk_edit([a, b], "add_tag", {"tag": 7})
    assert p.get_document(a)["tags"] == [1, 7] and p.get_document(b)["tags"] == [7]
    p.bulk_edit([a], "modify_tags", {"add_tags": [2], "remove_tags": [1]})
    assert p.get_document(a)["tags"] == [2, 7]
    p.bulk_edit([a], "modify_custom_fields", {"add_custom_fields": [4], "remove_custom_fields": [2]})
    assert {f["field"] for f in p.get_document(a)["custom_fields"]} == {1, 3, 4}
    p.bulk_edit([a, b], "set_document_type", {"document_type": 3})
    assert p.get_document(b)["document_type"] == 3
    with pytest.raises(PaperlessNotFound):
        p.bulk_edit([a, 99], "add_tag", {"tag": 1})
    assert p.get_document(a)["tags"] == [2, 7]  # alles oder nichts
    with pytest.raises(PaperlessError):
        p.bulk_edit([a], "unbekannt", {})
    p.patch_document(a, title="A neu", tags=[5])
    assert p.get_document(a)["title"] == "A neu" and p.get_document(a)["tags"] == [5]
    with pytest.raises(PaperlessError):
        p.patch_document(a, checksum="x")
    p.bulk_edit([b], "delete", {})
    with pytest.raises(PaperlessNotFound):
        p.get_document(b)


def test_versionen(tmp_path):
    p = FakePaperless()
    root = p.add_document("Vertrag", b"v1", tags=[1])
    f = tmp_path / "v2.pdf"
    f.write_bytes(b"v2")
    task_id = p.update_version(root, f, label="Nachtrag 1")
    assert p.get_document(root)["versions"] == []
    p.process_tasks()
    task = p.get_task(task_id)
    assert task["status"] == "SUCCESS" and task["related_document_ids"] == [2]
    assert p.get_document(root)["versions"] == [2]
    neu = p.get_document(2)
    assert neu["root_document"] == root and neu["tags"] == [1] and neu["title"] == "Vertrag"
    assert p.download(2, tmp_path / "out" / "v2.pdf").read_bytes() == b"v2"
    # Version auf eine Version bezieht sich auf die Wurzel
    f.write_bytes(b"v3")
    p.update_version(2, f)
    p.process_tasks()
    assert p.get_document(root)["versions"] == [2, 3] and p.get_document(3)["root_document"] == root

    ohne = FakePaperless(features={"document_versions": False})
    d = ohne.add_document("X", b"x")
    assert "versions" not in ohne.get_document(d)
    with pytest.raises(PaperlessUnsupported):
        ohne.update_version(d, f)


def test_downloads_und_server_info(tmp_path):
    p = FakePaperless(server_version="2.11.0", api_version=9, features={"custom_fields": False})
    d = p.add_document("A", b"inhalt")
    ziel = p.download(d, tmp_path / "a" / "b" / "a.pdf")
    assert ziel.read_bytes() == b"inhalt"
    assert p.download_thumbnail(d, tmp_path / "t.png").read_bytes().startswith(b"\x89PNG")
    info = p.server_info()
    assert info.server_version == "2.11.0" and info.api_version == 9
    assert (
        not info.supports("custom_fields") and not info.supports("tasks_v10") and info.supports("bulk_edit")
    )
    with pytest.raises(PaperlessNotFound):
        p.create_custom_field("X", "string")


def test_injektion_und_aufzeichnung(tmp_path):
    p = FakePaperless()
    d = p.add_document("A", b"a")
    p.inject("get_document", PaperlessRateLimited("zu viel", status_code=429, retry_after=2), times=2)
    with pytest.raises(PaperlessRateLimited):
        p.get_document(d)
    with pytest.raises(PaperlessRateLimited):
        p.get_document(d)
    assert p.get_document(d)["id"] == d
    p.inject("post_document", PaperlessUnavailable("Verbindung"))
    f = tmp_path / "x.pdf"
    f.write_bytes(b"x")
    with pytest.raises(PaperlessUnavailable):
        p.post_document(f, title="X")
    assert p.tasks == {}
    assert p.call_names() == ["get_document", "get_document", "get_document", "post_document"]
    assert p.calls[0][1] == (d,)
    p.reset_calls()
    list(p.list_documents())
    assert p.call_names() == ["list_documents", "iter_pages"]
