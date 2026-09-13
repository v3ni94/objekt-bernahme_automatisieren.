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


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        (
            "Aachener Straße 25, Erkelenz",
            {"street": "Aachener Straße", "house_number": "25", "postal_code": "", "city": "Erkelenz"},
        ),
        (
            "Ratheim, Shalomweg 3",
            {"street": "Shalomweg", "house_number": "3", "postal_code": "", "city": "Ratheim"},
        ),
        ("Aachener Straße 119 (GmbH)", {}),
        ("Windmühlenstraße 31, Kaiserstraße 77 u. 79", {}),
        ("Seesen Jacobsonstraße 24", {}),
        ("Wandlitz, Richard-Wagner-Weg 8_9", {}),
        (
            "Wacholderstr 28, 45770 Marl",
            {"street": "Wacholderstr", "house_number": "28", "postal_code": "45770", "city": "Marl"},
        ),
        (
            "Am Fließ 6 41812 Erkelenz",
            {"street": "Am Fließ", "house_number": "6", "postal_code": "41812", "city": "Erkelenz"},
        ),
        (
            "Heiligenberger Straße 5 10318 Berlin",
            {
                "street": "Heiligenberger Straße",
                "house_number": "5",
                "postal_code": "10318",
                "city": "Berlin",
            },
        ),
        ("Giesenkirchener Str. 124 und 126", {}),
        ("Aachenerstraße 21, 23, 23a", {}),
        ("Graf-Reinald-Str. 34, 36, 38, 40, 42, 41812 Erkelenz", {}),
        ("Gladbacher Straße 95", {}),
        ("Musterstadt Musterstraße 49 alt", {}),
        ("", {}),
    ],
)
def test_anschrift_aus_bezeichnung(text, erwartet):
    from apps.drive.management.commands.altbestand_objekte_anlegen import parse_address

    assert parse_address(text) == erwartet


@pytest.mark.parametrize(
    ("register_name", "folder_name", "erwartet"),
    [
        ("WEG Kesselstraße 58-60", "462 Essen, Kesselstr. 58-60", True),
        ("In Gerderhahn 105", "394 Erkelenz, In Gerderhahn 106", True),
        ("R31 Rigaer Straße 31", "503 R31 RigaerStr", True),
        ("Schenkendorfstraße 6", "499 Richard-Wagner-Strasse 8_9, 16348 Wandlitz", False),
        ("WEG Erkelenzer Straße 127", "523 Bedburg Dr. Harald Fett Bauernhof Projekt", False),
        ("Am Fließ 6", "602 Am Fließ 6, 50181 Bedburg", True),
        ("", "623 Musterstadt", False),
    ],
)
def test_registerbezeichnung_passt_zum_ordnernamen(register_name, folder_name, erwartet):
    from apps.drive.management.commands.altbestand_objekte_anlegen import names_match

    assert names_match(register_name, folder_name) is erwartet


def test_massenanlage_der_objekte_aus_dem_altbestand(drive, altordner, objekt, tmp_path, capsys, monkeypatch):
    """Einmalige Anlage aller Objekte aus der Altbestand-Tabelle (13.09.2026): Vorschau ohne Wirkung, echte Anlage
    mit Bezeichnung und Verwaltungsart aus dem Objektregister, Bindung der Quellordner, Ordneranlage, idempotent."""
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    refs = takeover.parse_folder_refs("\n".join([altordner["o623"], altordner["o700"], altordner["ohne"]]))
    takeover.add_sources(refs)
    for src in TakeoverSource.objects.all():
        takeover.resolve_source(src, drive)
    register = tmp_path / "objektregister.csv"
    register.write_text(
        "nummer;objekt;verwaltungsart;status\n700;Beispielstadt, Neuweg 1;Mietverwaltung;archiv\n",
        encoding="utf-8",
    )
    # 623 ist beim Aufloesen bereits an das vorhandene Objekt gebunden; offen bleiben 700 und der Ordner ohne Nummer
    assert TakeoverSource.objects.get(drive_folder_id=altordner["o623"]).object_id == objekt.pk
    # Vorschau: nichts angelegt
    call_command("altbestand_objekte_anlegen", register=str(register))
    out = capsys.readouterr().out
    assert (
        "Vorschau" in out
        and "700: neu „Beispielstadt, Neuweg 1“ (Mietverwaltung, Register archiv; Neuweg 1, Beispielstadt)"
        in out
    )
    assert "ohne Nummer, bleibt offen: Sonstige Unterlagen" in out
    assert not ManagedObject.objects.filter(object_number="700").exists()
    assert TakeoverSource.objects.filter(object__isnull=False).count() == 1

    call_command("altbestand_objekte_anlegen", register=str(register), echt=True)
    out = capsys.readouterr().out
    assert "Angelegt: 1 Objekte; Quellordner gebunden: 1" in out
    obj = ManagedObject.objects.get(object_number="700")
    assert obj.name == "Beispielstadt, Neuweg 1" and obj.management_type == "rental" and obj.status == "new"
    assert (obj.city, obj.street, obj.house_number, obj.postal_code) == ("Beispielstadt", "Neuweg", "1", None)
    assert "Stammdaten bitte nachpflegen" in obj.notes and "abgegeben" in obj.notes
    assert "Anschrift aus Bezeichnung abgeleitet" in obj.notes
    src700 = TakeoverSource.objects.get(drive_folder_id=altordner["o700"])
    src623 = TakeoverSource.objects.get(drive_folder_id=altordner["o623"])
    assert src700.object_id == obj.pk and src700.status == "linked"
    assert src623.object_id == objekt.pk and src623.status == "linked"
    ereignis = AuditEvent.objects.get(action="object.create", entity_id=obj.pk)
    assert ereignis.after_state["source"] == "altbestand_bulk" and ereignis.after_state["sources"] == [
        src700.pk
    ]
    # Ordneranlage lief (Celery im Test sofort): Objektordner mit Struktur
    obj.refresh_from_db()
    assert obj.drive_root_folder_id
    assert any(c.name.startswith("02_") for c in drive.list_children(obj.drive_root_folder_id))
    # zweiter Lauf: nichts mehr offen
    call_command("altbestand_objekte_anlegen", register=str(register), echt=True)
    out = capsys.readouterr().out
    assert "Angelegt: 0 Objekte" in out and ManagedObject.objects.filter(object_number="700").count() == 1
    # Registereintrag passt nicht zum Ordnernamen: Bezeichnung aus dem Ordnernamen, Vorgabe WEG, Hinweis mit beidem
    drive_id = drive.add_folder(drive.get(altordner["o700"]).parent_id, "0810 Neustadt, Ringstraße 2")
    takeover.add_sources(takeover.parse_folder_refs(drive_id))
    takeover.resolve_source(TakeoverSource.objects.get(drive_folder_id=drive_id), drive)
    register.write_text(
        "nummer;objekt;verwaltungsart;status\n810;Schenkendorfstraße 6;Mietverwaltung;archiv\n",
        encoding="utf-8",
    )
    call_command("altbestand_objekte_anlegen", register=str(register), echt=True)
    out = capsys.readouterr().out
    assert "810: neu „Neustadt, Ringstraße 2“ (WEG, Register passt nicht; Ringstraße 2, Neustadt)" in out
    neu = ManagedObject.objects.get(object_number="810")
    assert neu.name == "Neustadt, Ringstraße 2" and neu.management_type == "weg"
    assert (neu.city, neu.street, neu.house_number) == ("Neustadt", "Ringstraße", "2")
    assert "passt nicht zum Ordnernamen" in neu.notes and "Schenkendorfstraße 6" in neu.notes
    assert "Quellordner: 0810 Neustadt, Ringstraße 2." in neu.notes
    # ohne Register: Hinweis, Bezeichnung aus dem Ordnernamen
    drive_id2 = drive.add_folder(drive.get(altordner["o700"]).parent_id, "0811 Altstadt Marktplatz 4")
    takeover.add_sources(takeover.parse_folder_refs(drive_id2))
    takeover.resolve_source(TakeoverSource.objects.get(drive_folder_id=drive_id2), drive)
    call_command("altbestand_objekte_anlegen", register=str(tmp_path / "fehlt.csv"), echt=True)
    ohne = ManagedObject.objects.get(object_number="811")
    assert ohne.name == "Altstadt Marktplatz 4" and ohne.city is None and ohne.street is None
    assert "Nicht im Objektregister" in ohne.notes and "Anschrift unvollständig" in ohne.notes
    assert ohne.drive_root_folder_id is None  # Ordner wartet auf die Nachpflege der Anschrift
    neu.refresh_from_db()
    assert (
        neu.drive_root_folder_id and drive.get(neu.drive_root_folder_id).name == "810 Neustadt, Ringstraße 2"
    )


# ---------------------------------------------------------------- Nachraeumen (13.09.2026)
def test_nachraeumen_entfernt_nur_leere_altordner_und_systemdateien(objekt, drive, aufgeraeumt):
    """Nach der Verteilung gehen leere Ordner ausserhalb der Struktur und Systemdateien in den Papierkorb; Dubletten,
    Dateien in Pruefung, ihre Ordner, der Quellordner und die Sollstruktur bleiben. Nichts wird endgueltig geloescht."""
    from apps.drive import auto_cleanup
    from apps.drive.models import DriveNode, NodeStatus

    z = aufgeraeumt
    vorschau = auto_cleanup.sweep(objekt, drive=drive, dry_run=True)
    assert vorschau["status"] == "done" and vorschau["files"] == 0 and vorschau["folders"] == 0
    assert [t["root"] for t in vorschau["trees"]] == [
        drive.get(objekt.drive_root_folder_id).name,
        "623 Musterstadt Musterstraße 49 alt",
    ]
    quelle = vorschau["trees"][1]
    assert (
        quelle["junk"] == 2
        and quelle["empty_folders"] == 1
        and quelle["kept"] == 2
        and quelle["blocker"] is None
    )
    assert (
        not drive.get(z["thumbs"]).trashed
        and not AuditEvent.objects.filter(action="drive.auto_cleanup").exists()
    )

    result = auto_cleanup.sweep(objekt, drive=drive, trigger="test")
    assert (
        result["status"] == "done"
        and result["files"] == 2
        and result["folders"] == 1
        and result["errors"] == []
    )
    assert drive.get(z["thumbs"]).trashed and drive.get(z["tmp"]).trashed and drive.get(z["leer"]).trashed
    assert not drive.get(z["kopie_id"]).trashed and not drive.get(z["pruefung"].drive_file_id).trashed
    assert not drive.get(z["rechnungen"]).trashed and not drive.get(z["quelle"]).trashed
    assert (
        not drive.get(z["original"].drive_file_id).trashed
        and not drive.get(objekt.drive_root_folder_id).trashed
    )
    for row in DriveNode.objects.filter(object=objekt, status=NodeStatus.ACTIVE):
        assert not drive.get(row.drive_file_id).trashed
    assert TakeoverSource.objects.filter(pk=z["src"].pk).exists()
    ev = AuditEvent.objects.get(action="drive.auto_cleanup", object_id=objekt.pk)
    assert (
        ev.after_state["files"] == 2
        and ev.after_state["folders"] == 1
        and ev.after_state["trigger"] == "test"
    )
    bewegungen = [
        e for e in AuditEvent.objects.filter(action="drive.trash") if e.after_state.get("trigger") == "test"
    ]
    assert sorted(e.after_state["kind"] for e in bewegungen) == ["empty_folder", "junk", "junk"]
    assert not [op for op in drive.ops if op[0] == "delete"]
    # zweiter Durchgang: nichts mehr zu tun, kein zweites Protokoll
    wieder = auto_cleanup.sweep(objekt, drive=drive)
    assert (
        wieder["files"] == 0 and wieder["folders"] == 0 and wieder["kept"] == 3
    )  # Dublette, Prüfdatei, Original in 02
    assert AuditEvent.objects.filter(action="drive.auto_cleanup").count() == 1
    # Dublette bleibt dem Aufraeumen von Hand vorbehalten
    plan = takeover.plan_cleanup(z["src"], drive)
    assert [d.document_id for d in plan.duplicates] == [z["kopie"].pk] and not plan.junk


def test_nachraeumen_im_objektordner_mit_sperren_und_schalter(objekt, drive, admin_user):
    """Fremde Ordner im Objektordner: leere und solche mit nur Systemdateien gehen in den Papierkorb, Ordner mit
    unbekannten Dateien bleiben (auch wenn ein leerer Unterordner darin entfernt wird); Strukturordner sind tabu.
    Offene Jobs, der Schalter und die Objektsperre halten das Nachraeumen an."""
    from django.core.cache import cache

    from apps.drive import auto_cleanup
    from apps.drive.models import DriveNode, NodeStatus

    root = objekt.drive_root_folder_id
    alt = drive.add_folder(root, "Alt")
    scans = drive.add_folder(root, "Scans")
    thumbs = drive.add_file(scans, "Thumbs.db", b"t" * 4)
    wichtig = drive.add_folder(root, "Wichtig")
    vertrag = drive.add_file(wichtig, "Vertrag.pdf", b"v" * 8)
    tief = drive.add_folder(wichtig, "Leer tief")
    struktur = DriveNode.objects.filter(object=objekt, status=NodeStatus.ACTIVE).first()

    ProcessingJob.objects.create(
        object=objekt, job_type=JobType.FILE_TO_DRIVE, idempotency_key="t-auto", status=JobStatus.PENDING
    )
    assert auto_cleanup.sweep(objekt, drive=drive)["reason"] == "offene Verarbeitungsjobs"
    ProcessingJob.objects.filter(idempotency_key="t-auto").update(status=JobStatus.DONE)
    store.set("drive.auto_cleanup_enabled", False, user=admin_user, reason="Test")
    assert auto_cleanup.sweep(objekt, drive=drive)["reason"] == "drive.auto_cleanup_enabled aus"
    assert auto_cleanup.trigger_auto_cleanup(objekt.pk) == "disabled"
    store.set("drive.auto_cleanup_enabled", True, user=admin_user, reason="Test")
    assert cache.add(f"takeover:{objekt.pk}", "test", timeout=60)
    assert "gesperrt" in auto_cleanup.sweep(objekt, drive=drive)["reason"]
    cache.delete(f"takeover:{objekt.pk}")
    assert not drive.get(alt).trashed

    # Ausloeser nach einem Verarbeitungslauf laeuft im Test sofort
    assert auto_cleanup.trigger_auto_cleanup(objekt.pk, trigger="run") == "done"
    assert drive.get(alt).trashed and drive.get(scans).trashed and drive.get(thumbs).trashed
    assert drive.get(tief).trashed
    assert not drive.get(wichtig).trashed and not drive.get(vertrag).trashed
    assert not drive.get(root).trashed and not drive.get(struktur.drive_file_id).trashed
    for row in DriveNode.objects.filter(object=objekt, status=NodeStatus.ACTIVE):
        assert not drive.get(row.drive_file_id).trashed
    ev = AuditEvent.objects.get(action="drive.auto_cleanup", object_id=objekt.pk)
    assert (
        ev.after_state["files"] == 1 and ev.after_state["folders"] == 3 and ev.after_state["trigger"] == "run"
    )
    assert ev.after_state["kept"] == 1


def test_nachraeumen_leerer_quellordner_wird_aus_der_tabelle_entfernt(objekt, drive):
    from apps.drive import auto_cleanup

    alt = drive.add_folder(drive.root_id, "Altbestand")
    quelle = drive.add_folder(alt, "623 alt leer")
    unter = drive.add_folder(quelle, "Unter")
    src = TakeoverSource.objects.create(
        drive_folder_id=quelle, name="623 alt leer", object=objekt, status="done"
    )
    offen = TakeoverSource.objects.create(
        drive_folder_id=drive.add_folder(alt, "623 noch nicht"),
        name="623 noch nicht",
        object=objekt,
        status="linked",
    )
    result = auto_cleanup.sweep(objekt, drive=drive)
    assert result["folders"] == 2 and result["sources_pruned"] == 1 and result["errors"] == []
    assert drive.get(quelle).trashed and drive.get(unter).trashed and not drive.get(alt).trashed
    assert not TakeoverSource.objects.filter(pk=src.pk).exists()
    assert (
        TakeoverSource.objects.filter(pk=offen.pk).exists() and not drive.get(offen.drive_folder_id).trashed
    )
    assert AuditEvent.objects.filter(action="drive.takeover_source_prune", entity_id=src.pk).exists()
