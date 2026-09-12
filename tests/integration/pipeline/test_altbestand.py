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
    assert gruende == ["im Papierkorb", "in Drive nicht gefunden oder kein Zugriff"]
    # Voruebergehender Fehler: nichts wird entfernt
    drive.inject("get", TransientError("Drive 503"), times=1)
    resp = client.post("/verwaltung/altbestand/aktualisieren/", follow=True)
    assert TakeoverSource.objects.count() == 1
    src = TakeoverSource.objects.get()
    assert src.last_error and src.last_error.startswith("Drive nicht erreichbar")
    assert any("1 nicht lesbar" in m.message for m in resp.context["messages"])


# ---------------------------------------------------------------- Aufraeumen nach der Aufarbeitung (12.09.2026)


def _dok(obj, name, drive_file_id, status, *, sha=None, duplicate_of=None):
    from django.utils import timezone

    return Document.objects.create(
        object=obj,
        sha256=sha,
        size_bytes=8,
        mime_type="application/pdf",
        original_name=name,
        current_name=name,
        source="drive_existing",
        drive_file_id=drive_file_id,
        status=status,
        duplicate_of=duplicate_of,
        first_seen_at=timezone.now(),
    )


@pytest.fixture
def aufgeraeumt(objekt, drive, altordner, admin_user):
    """Zustand nach der Aufarbeitung: Original nach 02 verschoben, im Quellordner bleiben eine Dublette, eine Datei
    in Pruefung, zwei Systemdateien und ein leerer Unterordner."""
    from apps.drive.models import DriveNode, NodeKind
    from apps.review.models import ReviewCase

    quelle = altordner["o623"]
    kinder = {n.name: n for n in drive.list_children(quelle)}
    rechnungen = altordner["rech"]
    original_id = kinder["Protokoll 2023.pdf"].id
    ziel = DriveNode.objects.get(object=objekt, node_kind=NodeKind.MAIN_FOLDER, category_id="02")
    drive.move(original_id, quelle, ziel.drive_file_id)
    original = _dok(objekt, "Protokoll 2023.pdf", original_id, "filed", sha="a" * 64)
    kopie_id = drive.add_file(quelle, "Protokoll 2023 Kopie.pdf", b"b" * 8)
    kopie = _dok(objekt, "Protokoll 2023 Kopie.pdf", kopie_id, "duplicate", duplicate_of=original)
    ReviewCase.objects.create(
        object=objekt,
        document=kopie,
        case_type="unclear",
        case_subtype="duplicate",
        context={"reason": "Test"},
    )
    rechnung = {n.name: n for n in drive.list_children(rechnungen)}["Rechnung Dach.pdf"]
    pruefung = _dok(objekt, "Rechnung Dach.pdf", rechnung.id, "review", sha="c" * 64)
    thumbs = drive.add_file(quelle, "Thumbs.db", b"t" * 8)
    tmp = drive.add_file(rechnungen, "~$Protokoll.docx", b"x" * 8)
    leer = drive.add_folder(quelle, "Leer")
    src = TakeoverSource.objects.create(
        drive_folder_id=quelle,
        name="623 alt",
        object=objekt,
        status="done",
        files_registered=3,
        taken_at=None,
    )
    return {
        "src": src,
        "quelle": quelle,
        "rechnungen": rechnungen,
        "original": original,
        "kopie": kopie,
        "kopie_id": kopie_id,
        "pruefung": pruefung,
        "thumbs": thumbs,
        "tmp": tmp,
        "leer": leer,
    }


def test_aufraeumen_plan_und_ausfuehrung(objekt, drive, aufgeraeumt, admin_user):
    from apps.review.models import ReviewCase

    z = aufgeraeumt
    plan = takeover.plan_cleanup(z["src"], drive)
    assert not plan.blockers and plan.total_files == 4
    assert [d.document_id for d in plan.duplicates] == [z["kopie"].pk]
    assert sorted(j.path.rsplit("/", 1)[-1] for j in plan.junk) == ["Thumbs.db", "~$Protokoll.docx"]
    assert [(r.document_id, r.reason) for r in plan.remaining] == [(z["pruefung"].pk, "Status review")]
    assert [f[0] for f in plan.empty_folders] == [z["leer"]] and plan.root_empty is False
    assert plan.actionable
    result = takeover.run_cleanup(z["src"], drive, plan, user=admin_user, reason="Test")
    assert result == {"files": 3, "folders": 1, "documents": 1, "errors": [], "root_trashed": False}
    assert drive.get(z["kopie_id"]).trashed and drive.get(z["thumbs"]).trashed and drive.get(z["tmp"]).trashed
    assert drive.get(z["leer"]).trashed
    assert not drive.get(z["rechnungen"]).trashed and not drive.get(z["quelle"]).trashed
    assert (
        not drive.get(z["pruefung"].drive_file_id).trashed
        and not drive.get(z["original"].drive_file_id).trashed
    )
    z["kopie"].refresh_from_db()
    assert z["kopie"].deleted_at is not None and "Papierkorb" in z["kopie"].delete_reason
    assert ReviewCase.objects.get(document=z["kopie"]).status == "resolved"
    assert AuditEvent.objects.filter(action="drive.trash", object_id=objekt.pk).count() == 4
    assert TakeoverSource.objects.filter(pk=z["src"].pk).exists()
    # zweiter Durchgang: nichts mehr zu tun, Pruefdatei schuetzt weiterhin ihren Ordner
    plan2 = takeover.plan_cleanup(z["src"], drive)
    assert not plan2.actionable and len(plan2.remaining) == 1


def test_aufraeumen_leerer_quellordner_verschwindet_struktur_bleibt(objekt, drive, aufgeraeumt, admin_user):
    from apps.drive.models import DriveNode, NodeStatus

    z = aufgeraeumt
    Document.objects.filter(pk=z["pruefung"].pk).update(
        status="duplicate", duplicate_of=z["original"], sha256=None
    )
    plan = takeover.plan_cleanup(z["src"], drive)
    assert len(plan.duplicates) == 2 and not plan.remaining and plan.root_empty
    assert [f[0] for f in plan.empty_folders][-1] == z["quelle"]  # Quellordner zuletzt
    result = takeover.run_cleanup(z["src"], drive, plan, user=admin_user)
    assert result["files"] == 4 and result["folders"] == 3 and result["root_trashed"] is True
    assert drive.get(z["quelle"]).trashed and drive.get(z["rechnungen"]).trashed
    assert not TakeoverSource.objects.filter(pk=z["src"].pk).exists()
    assert AuditEvent.objects.filter(action="drive.takeover_source_prune").exists()
    # Struktur des Objekts und das verschobene Original bleiben unangetastet
    for row in DriveNode.objects.filter(object=objekt, status=NodeStatus.ACTIVE):
        assert not drive.get(row.drive_file_id).trashed
    assert not drive.get(z["original"].drive_file_id).trashed
    assert not [op for op in drive.ops if op[0] == "delete"]


def test_aufraeumen_sperren_und_rechte(
    objekt, drive, aufgeraeumt, client_as, clerk_user, admin_user, monkeypatch
):
    from allauth.account.internal.flows import reauthentication

    z = aufgeraeumt
    # Sperre: offener Job des Objekts
    ProcessingJob.objects.create(
        object=objekt, job_type=JobType.FILE_TO_DRIVE, idempotency_key="t-offen", status=JobStatus.PENDING
    )
    plan = takeover.plan_cleanup(z["src"], drive)
    assert plan.blockers and "offen" in plan.blockers[0]
    ProcessingJob.objects.filter(idempotency_key="t-offen").delete()
    # Sperre: noch nicht aufgearbeitet
    TakeoverSource.objects.filter(pk=z["src"].pk).update(status="linked")
    z["src"].refresh_from_db()
    assert takeover.plan_cleanup(z["src"], drive).blockers
    TakeoverSource.objects.filter(pk=z["src"].pk).update(status="done")
    # Rechte: Sachbearbeiter nicht, Admin mit Vorschau und Ausfuehrung ueber die Oberflaeche
    monkeypatch.setattr(reauthentication, "did_recently_authenticate", lambda request: True)
    assert client_as(clerk_user).get(f"/verwaltung/altbestand/{z['src'].pk}/aufraeumen/").status_code == 403
    client = client_as(admin_user)
    seite = client.get(f"/verwaltung/altbestand/{z['src'].pk}/aufraeumen/")
    assert seite.status_code == 200 and b"Vorschau" in seite.content and seite.context["plan"].actionable
    liste = client.get("/verwaltung/altbestand/")
    assert "Aufräumen".encode() in liste.content
    resp = client.post(
        f"/verwaltung/altbestand/{z['src'].pk}/aufraeumen/",
        {"action": "ausfuehren", "junk": "1", "folders": "1", "reason": "Test UI"},
        follow=True,
    )
    assert resp.status_code == 200
    assert any("3 Dateien und 1 Ordner in den Papierkorb" in m.message for m in resp.context["messages"])
    assert drive.get(z["kopie_id"]).trashed
