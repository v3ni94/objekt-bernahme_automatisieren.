"""Objektordner festlegen (Befund 11.09.2026, Objekt 82): mehrere Ordner mit derselben Nummer, Fall im Review Center,
Ordnerwahl im Fall oder auf der Objektseite, danach Abgleich und weiterlaufende Ablage; Abgleich endet bei Fehlern
als failed; Temporaerdateien werden bei der Uebernahme uebersprungen."""

from __future__ import annotations

import pytest

from apps.audit.models import AuditEvent
from apps.config import store
from apps.drive import oauth, takeover
from apps.drive.models import DriveNode, DriveSyncRun, NodeKind
from apps.drive.reconcile import DriveConfig, reconcile_object
from apps.objects.models import ManagedObject
from apps.pipeline.models import JobStatus, JobType, ProcessingJob
from apps.review.models import CaseStatus, ReviewCase

pytestmark = pytest.mark.django_db


@pytest.fixture
def doppelt(seeded, drive, admin_user, monkeypatch):
    """Objekt 82 mit zwei Altordnern '82 Alt' und '082 Ratheim' direkt unter dem Wurzelordner."""
    store.set("drive.root_folder_id", drive.root_id, user=admin_user, reason="Test")
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    a = drive.add_folder(drive.root_id, "82 Alt")
    b = drive.add_folder(drive.root_id, "082 Ratheim, Shalomweg 3")
    drive.add_file(b, "Vertrag.pdf", b"v")
    obj = ManagedObject.objects.create(
        object_number="82",
        name="Ratheim, Shalomweg 3",
        street="Shalomweg",
        house_number="3",
        city="Ratheim",
        management_type="rental",
        is_test=True,
    )
    run = reconcile_object(
        obj, drive=drive, dry_run=False, cfg=DriveConfig.from_settings(root_folder_id=drive.root_id)
    )
    case = ReviewCase.objects.get(object=obj, case_type="duplicate_object_number")
    assert (
        run.status == "done"
        and not DriveNode.objects.filter(object=obj, node_kind=NodeKind.OBJECT_ROOT).exists()
    )
    return {"obj": obj, "a": a, "b": b, "case": case}


def test_fall_zeigt_hinweis_und_ordnerwahl_legt_struktur_an(client_as, clerk_user, drive, doppelt):
    obj, case = doppelt["obj"], doppelt["case"]
    client = client_as(clerk_user)
    seite = client.get(f"/objekte/{obj.pk}/dokumente/")
    assert (
        b"Objektordner nicht festgelegt" in seite.content and f"/review/{case.pk}/".encode() in seite.content
    )
    fall = client.get(f"/review/{case.pk}/")
    assert b"Als Objektordner verwenden" in fall.content and b"082 Ratheim" in fall.content
    # Wahl eines Ordners, der nicht Kandidat ist, wird abgelehnt
    resp = client.post(
        f"/review/{case.pk}/aktion/",
        {"action": "choose_folder", "folder_id": "fremd-0001", "query": ""},
        follow=True,
    )
    assert any("gehört nicht zu den Kandidaten" in m.message for m in resp.context["messages"])
    resp = client.post(
        f"/review/{case.pk}/aktion/",
        {"action": "choose_folder", "folder_id": doppelt["b"], "query": ""},
        follow=True,
    )
    assert resp.status_code == 200
    obj.refresh_from_db()
    case.refresh_from_db()
    assert (
        obj.drive_root_folder_id == doppelt["b"] and obj.drive_root_folder_name == "082 Ratheim, Shalomweg 3"
    )
    assert case.status == CaseStatus.RESOLVED and case.resolution["folder_id"] == doppelt["b"]
    # Celery im Test sofort: Abgleich hat den gewaehlten Ordner uebernommen und die Struktur angelegt
    root = DriveNode.objects.get(object=obj, node_kind=NodeKind.OBJECT_ROOT, status="active")
    assert root.drive_file_id == doppelt["b"]
    assert DriveNode.objects.filter(object=obj, node_kind=NodeKind.MAIN_FOLDER, status="active").count() == 6
    assert AuditEvent.objects.filter(action="object.drive_root_set", entity_id=obj.pk).exists()
    assert not ReviewCase.objects.filter(
        object=obj, case_type="duplicate_object_number", status="open"
    ).exists()
    # der andere Ordner bleibt unveraendert
    assert drive.get(doppelt["a"]).name == "82 Alt"


def test_objektordner_auf_der_objektseite_festlegen(client_as, clerk_user, drive, doppelt):
    obj = doppelt["obj"]
    client = client_as(clerk_user)
    seite = client.get(f"/objekte/{obj.pk}/")
    assert b"Objektordner festlegen" in seite.content and b"Objektordner nicht festgelegt" in seite.content
    resp = client.post(f"/objekte/{obj.pk}/ordner/festlegen/", {"ordner": "kein-ordner"}, follow=True)
    assert any(
        "nicht erkannt" in m.message or "nicht gefunden" in m.message for m in resp.context["messages"]
    )
    link = f"https://drive.google.com/drive/folders/{doppelt['b']}?usp=sharing"
    resp = client.post(f"/objekte/{obj.pk}/ordner/festlegen/", {"ordner": link}, follow=True)
    obj.refresh_from_db()
    assert obj.drive_root_folder_id == doppelt["b"]
    assert any("1 Fall (Objektnummer doppelt) erledigt" in m.message for m in resp.context["messages"])
    assert DriveNode.objects.filter(object=obj, node_kind=NodeKind.OBJECT_ROOT, status="active").exists()
    seite = client.get(f"/objekte/{obj.pk}/")
    assert b"Objektordner nicht festgelegt" not in seite.content


def test_wartende_jobs_sichtbar(client_as, clerk_user, drive, doppelt):
    obj = doppelt["obj"]
    ProcessingJob.objects.create(
        object=obj,
        job_type=JobType.FILE_TO_DRIVE,
        status=JobStatus.PENDING,
        idempotency_key="t:1",
        last_error="wartet: Ablageziel noch nicht vorhanden: Objektordner unbekannt",
    )
    client = client_as(clerk_user)
    seite = client.get(f"/objekte/{obj.pk}/dokumente/")
    assert b"Wartende Jobs" in seite.content and b"Ablageziel noch nicht vorhanden" in seite.content
    seite = client.get(f"/objekte/{obj.pk}/")
    assert b"Ablageziel noch nicht vorhanden" in seite.content


def test_abgleich_endet_bei_internem_fehler_als_failed(seeded, drive, monkeypatch):
    from apps.drive import reconcile as reconcile_mod

    obj = ManagedObject.objects.create(
        object_number="777",
        city="Fehlerstadt",
        street="Weg",
        house_number="1",
        management_type="weg",
        is_test=True,
    )

    def kaputt(*a, **kw):
        raise RuntimeError("UPDATE command denied")

    monkeypatch.setattr(reconcile_mod, "_execute", kaputt)
    run = reconcile_object(
        obj, drive=drive, dry_run=False, cfg=DriveConfig.from_settings(root_folder_id=drive.root_id)
    )
    assert run.status == "failed" and "UPDATE command denied" in run.error_message
    assert not DriveSyncRun.objects.filter(object=obj, status="running").exists()


def test_temporaerdateien_werden_uebersprungen(client_as, clerk_user, objekt, drive, admin_user):
    store.set("drive.root_folder_id", drive.root_id, user=admin_user, reason="Test")
    alt = drive.add_folder(drive.root_id, "Alt 623")
    drive.add_file(alt, "Vertrag.pdf", b"v")
    drive.add_file(alt, "E9C799D1.tmp", b"t")
    drive.add_file(alt, "~$Brief.docx", b"w")
    drive.add_file(alt, "Thumbs.db", b"x")
    assert (
        takeover.is_ignored("E9C799D1.tmp")
        and takeover.is_ignored("~$Brief.docx")
        and not takeover.is_ignored("Vertrag.pdf")
    )
    client = client_as(clerk_user)
    seite = client.get(f"/objekte/{objekt.pk}/uebernahme/?ordner={alt}")
    assert seite.context["selectable"] == 1 and b"Tempor" in seite.content
    resp = client.post(f"/objekte/{objekt.pk}/uebernahme/", {"folder": alt, "mode": "folder"}, follow=True)
    from apps.documents.models import Document

    assert list(Document.objects.filter(object=objekt).values_list("current_name", flat=True)) == [
        "Vertrag.pdf"
    ]
    assert any("3 Temporär- oder Systemdatei(en) übersprungen" in m.message for m in resp.context["messages"])


def test_sweeper_schliesst_haengende_abgleiche(seeded):
    from datetime import timedelta

    from django.utils import timezone

    from apps.pipeline.tasks import abort_stale_sync_runs

    obj = ManagedObject.objects.create(
        object_number="778",
        city="Altstadt",
        street="Weg",
        house_number="2",
        management_type="weg",
        is_test=True,
    )
    alt = DriveSyncRun.objects.create(
        object=obj, dry_run=False, status="running", started_at=timezone.now() - timedelta(hours=5)
    )
    frisch = DriveSyncRun.objects.create(
        object=obj, dry_run=False, status="running", started_at=timezone.now()
    )
    assert abort_stale_sync_runs() == 1
    alt.refresh_from_db()
    frisch.refresh_from_db()
    assert alt.status == "failed" and "Sweeper" in alt.error_message and frisch.status == "running"
