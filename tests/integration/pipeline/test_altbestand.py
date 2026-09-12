"""Altbestand-Tabelle (11.09.2026): Quellordner aufnehmen ohne Doppelte, Namen und Objektnummern aus Drive lesen,
Objekt zuordnen oder vorbelegt anlegen, „Aufarbeiten“ uebernimmt rekursiv und verschiebt, nichts wird geloescht."""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.audit.models import AuditEvent
from apps.config import store
from apps.documents.models import Document
from apps.drive import oauth, takeover
from apps.drive.models import TakeoverSource
from apps.objects.models import ManagedObject
from apps.pipeline.models import JobStatus, JobType, ProcessingJob

pytestmark = pytest.mark.django_db

LINK = "https://drive.google.com/open?id={}&usp=drive_copy"


@pytest.fixture
def altordner(objekt, drive, admin_user):
    store.set("drive.root_folder_id", drive.root_id, user=admin_user, reason="Test")
    alt = drive.add_folder(drive.root_id, "Altbestand")
    o623 = drive.add_folder(alt, "623 Musterstadt Musterstraße 49 alt")
    rech = drive.add_folder(o623, "Rechnungen")
    drive.add_file(rech, "Rechnung Dach.pdf", b"a" * 8)
    drive.add_file(o623, "Protokoll 2023.pdf", b"b" * 8)
    o700 = drive.add_folder(alt, "0700 Beispielstadt Neuweg 1")
    drive.add_file(o700, "Teilungserklaerung.pdf", b"c" * 8)
    ohne = drive.add_folder(alt, "Sonstige Unterlagen")
    return {"o623": o623, "o700": o700, "ohne": ohne, "rech": rech}


def test_links_erkennen_und_ohne_doppelte_aufnehmen(client_as, clerk_user, altordner):
    client = client_as(clerk_user)
    text = "\n".join(
        [
            LINK.format(altordner["o623"]),
            LINK.format(altordner["o700"]),
            LINK.format(altordner["o623"]),
            "kein link",
            altordner["ohne"],
        ]
    )
    assert takeover.parse_folder_refs(text) == [altordner["o623"], altordner["o700"], altordner["ohne"]]
    resp = client.post("/verwaltung/altbestand/aufnehmen/", {"links": text}, follow=True)
    assert resp.status_code == 200
    rows = {r.drive_folder_id: r for r in TakeoverSource.objects.all()}
    assert set(rows) == {altordner["o623"], altordner["o700"], altordner["ohne"]}
    # Namen und Nummern wurden ueber die Fake-Verbindung gleich gelesen
    assert rows[altordner["o623"]].detected_object_number == "623" and rows[altordner["o623"]].object_id
    assert rows[altordner["o623"]].status == "linked"
    assert (
        rows[altordner["o700"]].detected_object_number == "700" and rows[altordner["o700"]].object_id is None
    )
    assert (
        rows[altordner["ohne"]].detected_object_number is None
        and rows[altordner["ohne"]].name == "Sonstige Unterlagen"
    )
    texte = [m.message for m in resp.context["messages"]]
    assert any("3 Ordner aufgenommen, 0 waren bereits" in t for t in texte)
    resp = client.post("/verwaltung/altbestand/aufnehmen/", {"links": text}, follow=True)
    assert TakeoverSource.objects.count() == 3
    assert any("0 Ordner aufgenommen, 3 waren bereits" in m.message for m in resp.context["messages"])
    assert AuditEvent.objects.filter(action="drive.takeover_source_add").count() == 2
    seite = client.get("/verwaltung/altbestand/")
    assert seite.status_code == 200 and b"Objekt anlegen" in seite.content
    assert [r.detected_object_number for r in seite.context["rows"]] == ["623", "700", None]


def test_aufarbeiten_uebernimmt_rekursiv_und_loescht_nichts(client_as, clerk_user, objekt, drive, altordner):
    client = client_as(clerk_user)
    client.post("/verwaltung/altbestand/aufnehmen/", {"links": altordner["o623"]}, follow=True)
    src = TakeoverSource.objects.get(drive_folder_id=altordner["o623"])
    ops_before = len(drive.ops)
    resp = client.post(f"/verwaltung/altbestand/{src.pk}/aufarbeiten/", follow=True)
    assert resp.status_code == 200
    src.refresh_from_db()
    assert src.status == "done" and src.files_registered == 2 and src.files_skipped == 0 and src.last_run_id
    paths = sorted(
        Document.objects.filter(object=objekt, source="drive_existing").values_list("source_path", flat=True)
    )
    assert paths == [
        "623 Musterstadt Musterstraße 49 alt/Protokoll 2023.pdf",
        "623 Musterstadt Musterstraße 49 alt/Rechnungen/Rechnung Dach.pdf",
    ]
    assert not [op for op in drive.ops[ops_before:] if op[0] in ("trash", "delete")]
    ev = AuditEvent.objects.get(action="drive.takeover", object_id=objekt.pk)
    assert ev.after_state["source_id"] == src.pk and ev.after_state["registered"] == 2
    texte = [m.message for m in resp.context["messages"]]
    assert any("2 Datei(en)" in t and "Quellordner bleibt" in t for t in texte)
    # zweiter Durchgang: nichts Neues, Zaehler fuer Uebersprungene
    resp = client.post(f"/verwaltung/altbestand/{src.pk}/aufarbeiten/", follow=True)
    src.refresh_from_db()
    assert src.files_registered == 2 and src.files_skipped == 2 and src.status == "done"
    assert any("keine neue Datei" in m.message for m in resp.context["messages"])


def test_ohne_objekt_kein_aufarbeiten_und_objektanlage_verknuepft(
    client_as, clerk_user, drive, altordner, monkeypatch
):
    client = client_as(clerk_user)
    client.post("/verwaltung/altbestand/aufnehmen/", {"links": altordner["o700"]}, follow=True)
    src = TakeoverSource.objects.get(drive_folder_id=altordner["o700"])
    resp = client.post(f"/verwaltung/altbestand/{src.pk}/aufarbeiten/", follow=True)
    assert any("Kein Zielobjekt" in m.message for m in resp.context["messages"])
    src.refresh_from_db()
    assert src.status == "new" and not Document.objects.filter(source_path__startswith="0700").exists()
    # Formular vorbelegt, Objekt anlegen bindet den Ordner
    form = client.get("/objekte/neu/?nummer=700&name=0700%20Beispielstadt%20Neuweg%201")
    assert form.context["form"].initial["object_number"] == "700"
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    resp = client.post(
        "/objekte/neu/",
        {
            "object_number": "700",
            "name": "Beispielstadt, Neuweg 1",
            "street": "Neuweg",
            "house_number": "1",
            "city": "Beispielstadt",
            "management_type": "weg",
            "status": "new",
            "fiscal_year_start_month": 1,
            "is_test": "on",
        },
        follow=True,
    )
    assert resp.status_code == 200
    obj = ManagedObject.objects.get(object_number="700")
    src.refresh_from_db()
    assert src.object_id == obj.pk and src.status == "linked"
    assert any("1 Altbestand-Ordner" in m.message for m in resp.context["messages"])
    # jetzt aufarbeiten: Objektordner steht (Celery im Test sofort), Datei wird registriert
    resp = client.post(f"/verwaltung/altbestand/{src.pk}/aufarbeiten/", follow=True)
    src.refresh_from_db()
    assert src.status == "done" and src.files_registered == 1
    assert Document.objects.filter(object=obj, current_name="Teilungserklaerung.pdf").exists()


def test_aufarbeiten_ohne_objektordner_stoesst_abgleich_an(
    client_as, clerk_user, drive, altordner, seeded, monkeypatch
):
    """Objekt ohne Ordnerabgleich: Uebernahme registriert die Dateien, der Abgleich wird angestossen, die Ablage
    wartet bis die Struktur steht (kein Fehlversuch)."""
    obj = ManagedObject.objects.create(
        object_number="700",
        city="Beispielstadt",
        street="Neuweg",
        house_number="1",
        management_type="weg",
        is_test=True,
    )
    client = client_as(clerk_user)
    client.post("/verwaltung/altbestand/aufnehmen/", {"links": altordner["o700"]}, follow=True)
    src = TakeoverSource.objects.get(drive_folder_id=altordner["o700"])
    assert src.object_id == obj.pk
    aufrufe = []
    from apps.drive import tasks

    monkeypatch.setattr(tasks.reconcile_object_task, "delay", lambda *a, **kw: aufrufe.append(a))
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    resp = client.post(f"/verwaltung/altbestand/{src.pk}/aufarbeiten/", follow=True)
    assert aufrufe == [(obj.pk, False, clerk_user.pk, "takeover")]
    assert any("wird jetzt angelegt" in m.message for m in resp.context["messages"])
    # die Ablage kommt erst nach dem Abgleich dran: Discover-Job wurde eingereiht
    assert ProcessingJob.objects.filter(
        object=obj, job_type=JobType.DISCOVER, status__in=[JobStatus.PENDING, JobStatus.DONE]
    ).exists()


def test_import_kommando_und_entfernen(client_as, clerk_user, drive, altordner, tmp_path, capsys):
    datei = tmp_path / "ordner.txt"
    datei.write_text(
        "\n".join([LINK.format(altordner["o623"]), altordner["o623"], altordner["ohne"]]), encoding="utf-8"
    )
    call_command("altbestand_import", datei=str(datei), aufloesen=True)
    out = capsys.readouterr().out
    assert "2 neu aufgenommen" in out and "aufgelöst: 2" in out and "mit Objekt: 1" in out
    call_command("altbestand_import", datei=str(datei))
    assert TakeoverSource.objects.count() == 2
    src = TakeoverSource.objects.get(drive_folder_id=altordner["ohne"])
    client = client_as(clerk_user)
    resp = client.post(f"/verwaltung/altbestand/{src.pk}/entfernen/", follow=True)
    assert resp.status_code == 200 and TakeoverSource.objects.count() == 1
    assert drive.get(altordner["ohne"]) is not None  # in Drive unveraendert
    assert AuditEvent.objects.filter(action="drive.takeover_source_remove", entity_id=src.pk).exists()


def test_seed_datei_ohne_doppelte():
    from pathlib import Path

    from django.conf import settings

    text = (Path(settings.REPO_DIR) / "db" / "seeds" / "altbestand_ordner.txt").read_text(encoding="utf-8")
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    ids = takeover.parse_folder_refs(text)
    assert len(zeilen) == 113 and len(ids) == 113 and len(set(ids)) == 113


def test_aktualisieren_entfernt_geloeschte_und_papierkorb_ordner(client_as, clerk_user, altordner, drive):
    """„Aktualisieren“ (12.09.2026): Ordner, die es in Drive nicht mehr gibt, verschwinden aus der Liste; ein Ordner,
    den Drive gerade nicht liefert, bleibt mit Hinweis stehen. In Drive wird nichts veraendert."""
    from apps.drive.adapter import TransientError

    client = client_as(clerk_user)
    client.post(
        "/verwaltung/altbestand/aufnehmen/",
        {"links": "\n".join([altordner["o623"], altordner["o700"], altordner["ohne"]])},
    )
    assert TakeoverSource.objects.count() == 3
    del drive._entries[altordner["o700"]]  # in Drive endgueltig geloescht
    drive.trash(altordner["ohne"])  # im Papierkorb
    resp = client.post("/verwaltung/altbestand/aktualisieren/", follow=True)
    assert resp.status_code == 200
    assert set(TakeoverSource.objects.values_list("drive_folder_id", flat=True)) == {altordner["o623"]}
    texte = [m.message for m in resp.context["messages"]]
    assert any("3 Ordner geprüft, 1 aktualisiert, 2 nicht mehr in Drive vorhanden" in t for t in texte), texte
    gruende = sorted(
        e.after_state["reason"] for e in AuditEvent.objects.filter(action="drive.takeover_source_prune")
    )
    assert gruende == ["im Papierkorb", "in Drive gelöscht"]
    # Voruebergehender Fehler: nichts wird entfernt
    drive.inject("get", TransientError("Drive 503"), times=1)
    resp = client.post("/verwaltung/altbestand/aktualisieren/", follow=True)
    assert TakeoverSource.objects.count() == 1
    src = TakeoverSource.objects.get()
    assert src.last_error and src.last_error.startswith("Drive nicht erreichbar")
    assert any("1 nicht lesbar" in m.message for m in resp.context["messages"])
