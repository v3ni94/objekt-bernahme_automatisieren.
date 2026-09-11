"""Bestand aus Drive uebernehmen (11.09.2026): Ordner durchsehen, Dateien registrieren, Lauf starten, nichts loeschen."""

from __future__ import annotations

import pytest

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents.models import Document
from apps.drive import takeover
from apps.pipeline.models import JobType, ProcessingJob, ProcessingRun

pytestmark = pytest.mark.django_db


@pytest.fixture
def altbestand(objekt, drive, admin_user):
    """Alte Struktur neben dem Objektordner: Alt/Objekt 623 alt/Rechnungen mit zwei PDFs, daneben eine Notiz."""
    store.set("drive.root_folder_id", drive.root_id, user=admin_user, reason="Test")
    alt = drive.add_folder(drive.root_id, "Alt")
    obj_alt = drive.add_folder(alt, "Objekt 623 alt")
    rechnungen = drive.add_folder(obj_alt, "Rechnungen")
    a = drive.add_file(rechnungen, "Rechnung Dach 2024.pdf", b"a" * 10)
    b = drive.add_file(rechnungen, "Rechnung Heizung 2023.pdf", b"b" * 10)
    notiz = drive.add_file(alt, "Notiz.txt", b"n", mime_type="text/plain")
    return {"alt": alt, "obj_alt": obj_alt, "rechnungen": rechnungen, "a": a, "b": b, "notiz": notiz}


def test_ordner_durchsehen(client_as, clerk_user, objekt, drive, altbestand):
    client = client_as(clerk_user)
    resp = client.get(f"/objekte/{objekt.pk}/uebernahme/")
    assert resp.status_code == 200
    assert b"Alt" in resp.content and resp.context["is_root"]
    resp = client.get(f"/objekte/{objekt.pk}/uebernahme/?ordner={altbestand['rechnungen']}")
    assert [c.name for c in resp.context["crumbs"]] == ["Meine Ablage", "Alt", "Objekt 623 alt", "Rechnungen"]
    assert [e.node.name for e in resp.context["files"]] == [
        "Rechnung Dach 2024.pdf",
        "Rechnung Heizung 2023.pdf",
    ]
    assert resp.context["selectable"] == 2
    # Drive-Link statt ID
    resp = client.get(
        f"/objekte/{objekt.pk}/uebernahme/?ordner=https://drive.google.com/drive/folders/{altbestand['alt']}?usp=x"
    )
    assert resp.context["folder"].id == altbestand["alt"] and [f.name for f in resp.context["folders"]] == [
        "Objekt 623 alt"
    ]


def test_ordner_uebernehmen_und_erneut(client_as, clerk_user, objekt, drive, altbestand):
    client = client_as(clerk_user)
    url = f"/objekte/{objekt.pk}/uebernahme/"
    ops_before = len(drive.ops)
    resp = client.post(url, {"folder": altbestand["rechnungen"], "mode": "folder"}, follow=True)
    assert resp.status_code == 200
    docs = Document.objects.filter(object=objekt, source="drive_existing").order_by("current_name")
    assert [d.current_name for d in docs] == ["Rechnung Dach 2024.pdf", "Rechnung Heizung 2023.pdf"]
    assert docs[0].source_path == "Rechnungen/Rechnung Dach 2024.pdf" and docs[0].status in (
        "registered",
        "hashed",
        "ocr_done",
        "classified",
        "review",
        "filed",
    )
    assert ProcessingJob.objects.filter(object=objekt, job_type=JobType.DISCOVER).count() == 2
    assert ProcessingRun.objects.filter(object=objekt).exists()
    ev = AuditEvent.objects.get(action="drive.takeover", object_id=objekt.pk)
    assert ev.after_state["registered"] == 2 and ev.after_state["source_folder_name"] == "Rechnungen"
    # Quellordner und Dateien bleiben in Drive; nichts wird geloescht
    assert drive.get(altbestand["a"]) is not None and not drive.get(altbestand["a"]).trashed
    assert not [op for op in drive.ops[ops_before:] if op[0] in ("trash", "delete")]
    texte = [m.message for m in resp.context["messages"]]
    assert any("2 Datei(en)" in t and "nichts gelöscht" in t for t in texte)
    # zweiter Durchgang: bereits registriert, keine Dubletten
    resp = client.post(url, {"folder": altbestand["rechnungen"], "mode": "folder"}, follow=True)
    assert Document.objects.filter(object=objekt, source="drive_existing").count() == 2
    assert any("bereits einem Objekt zugeordnet" in m.message for m in resp.context["messages"])
    resp = client.get(f"{url}?ordner={altbestand['rechnungen']}")
    assert resp.context["selectable"] == 0 and all(e.registered_in == "623" for e in resp.context["files"])


def test_auswahl_und_unterordner(client_as, clerk_user, objekt, drive, altbestand):
    client = client_as(clerk_user)
    url = f"/objekte/{objekt.pk}/uebernahme/"
    client.post(
        url, {"folder": altbestand["rechnungen"], "mode": "selected", "files": [altbestand["b"]]}, follow=True
    )
    assert list(Document.objects.filter(object=objekt).values_list("drive_file_id", flat=True)) == [
        altbestand["b"]
    ]
    resp = client.post(url, {"folder": altbestand["alt"], "mode": "recursive"}, follow=True)
    paths = sorted(Document.objects.filter(object=objekt).values_list("source_path", flat=True))
    assert paths == [
        "Alt/Notiz.txt",
        "Alt/Objekt 623 alt/Rechnungen/Rechnung Dach 2024.pdf",
        "Rechnungen/Rechnung Heizung 2023.pdf",
    ]
    texte = [m.message for m in resp.context["messages"]]
    assert any("2 Datei(en)" in t for t in texte) and any("1 Datei(en) waren bereits" in t for t in texte)


def test_grenze_und_leerer_ordner(client_as, clerk_user, admin_user, objekt, drive, altbestand):
    store.set("drive.takeover_max_files", 10, user=admin_user, reason="Test")
    leer = drive.add_folder(altbestand["alt"], "Leer")
    for i in range(11):
        drive.add_file(altbestand["obj_alt"], f"Datei {i}.pdf", b"x")
    client = client_as(clerk_user)
    url = f"/objekte/{objekt.pk}/uebernahme/"
    resp = client.post(url, {"folder": altbestand["obj_alt"], "mode": "folder"}, follow=True)
    assert not Document.objects.filter(object=objekt).exists()
    assert any("Mehr als 10 Dateien" in m.message for m in resp.context["messages"])
    resp = client.post(url, {"folder": leer, "mode": "folder"}, follow=True)
    assert any("keine Dateien" in m.message for m in resp.context["messages"])
    resp = client.post(url, {"folder": altbestand["rechnungen"], "mode": "selected"}, follow=True)
    assert any("Keine Datei ausgewählt" in m.message for m in resp.context["messages"])


def test_parse_folder_ref():
    fid = "1zsFK7_A3vsw7-TaNKeFzE5P3lzO00S8W"
    assert takeover.parse_folder_ref(fid) == fid
    assert takeover.parse_folder_ref(f"https://drive.google.com/drive/u/0/folders/{fid}") == fid
    assert takeover.parse_folder_ref(f"https://drive.google.com/open?id={fid}") == fid
    assert takeover.parse_folder_ref("") is None and takeover.parse_folder_ref("kurz") is None


def test_einzelauswahl_in_grossem_ordner_und_wartender_lauf(
    client_as, clerk_user, admin_user, objekt, drive, altbestand, monkeypatch
):
    from django.core.cache import cache

    from apps.pipeline.models import ProcessingRun, RunStatus, RunType

    store.set("drive.takeover_max_files", 10, user=admin_user, reason="Test")
    for i in range(12):
        drive.add_file(altbestand["rechnungen"], f"Beleg {i}.pdf", b"x")
    client = client_as(clerk_user)
    url = f"/objekte/{objekt.pk}/uebernahme/"
    # Einzelauswahl bleibt moeglich, obwohl der Ordner mehr als die Grenze enthaelt
    resp = client.post(
        url, {"folder": altbestand["rechnungen"], "mode": "selected", "files": [altbestand["a"]]}, follow=True
    )
    assert Document.objects.filter(object=objekt).count() == 1
    assert not any("Mehr als" in m.message for m in resp.context["messages"])
    # Wartender Lauf: Jobs werden angehaengt, aber nicht sofort versendet (Objektserialitaet); Dry-Run zaehlt nicht
    gesendet = []
    from apps.drive import takeover as takeover_mod

    monkeypatch.setattr(takeover_mod, "send", lambda job: gesendet.append(job.pk))
    ProcessingRun.objects.filter(object=objekt).update(status=RunStatus.DONE)
    dry = ProcessingRun.objects.create(
        object=objekt, run_type=RunType.FULL, status=RunStatus.RUNNING, dry_run=True
    )
    wartend = ProcessingRun.objects.create(
        object=objekt, run_type=RunType.INCREMENTAL, status=RunStatus.PENDING
    )
    client.post(
        url, {"folder": altbestand["rechnungen"], "mode": "selected", "files": [altbestand["b"]]}, follow=True
    )
    job = ProcessingJob.objects.get(document__drive_file_id=altbestand["b"], job_type=JobType.DISCOVER)
    assert job.run_id == wartend.pk and job.run_id != dry.pk and gesendet == []
    # laufender Lauf: Jobs werden nach dem Commit versendet
    wartend.status = RunStatus.RUNNING
    wartend.save(update_fields=["status"])
    client.post(
        url, {"folder": altbestand["alt"], "mode": "selected", "files": [altbestand["notiz"]]}, follow=True
    )
    assert len(gesendet) == 1
    # Sperre je Objekt: waehrend einer laufenden Uebernahme wird die zweite abgewiesen
    cache.add(f"takeover:{objekt.pk}", "1", timeout=60)
    resp = client.post(
        url, {"folder": altbestand["rechnungen"], "mode": "selected", "files": [altbestand["a"]]}, follow=True
    )
    assert any("läuft gerade eine Übernahme" in m.message for m in resp.context["messages"])
    cache.delete(f"takeover:{objekt.pk}")
