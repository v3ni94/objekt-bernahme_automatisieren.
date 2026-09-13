"""„Verarbeitung für alle Objekte starten“ (13.09.2026): je aktivem Objekt mit offener Arbeit ein Nachlauf,
nacheinander nach processing.max_parallel_objects; Objekte ohne offene Dokumente oder mit wartendem Lauf werden
ausgelassen; Knopf auf der Objektliste, Kommando mit Vorschau."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents.models import Document
from apps.objects.models import ManagedObject
from apps.pipeline.models import ProcessingRun, RunStatus, RunType
from apps.pipeline.runs import objects_with_open_work, start_runs_for_all

pytestmark = pytest.mark.django_db


def _dok(obj, name, status):
    return Document.objects.create(
        object=obj,
        size_bytes=8,
        mime_type="application/pdf",
        original_name=name,
        current_name=name,
        source="drive_existing",
        drive_file_id=f"f-{obj.pk}-{name}",
        status=status,
        first_seen_at=timezone.now(),
    )


@pytest.fixture
def drei_objekte(seeded, drive):
    a = ManagedObject.objects.create(object_number="701", name="A", management_type="weg", is_test=True)
    b = ManagedObject.objects.create(object_number="702", name="B", management_type="weg", is_test=True)
    c = ManagedObject.objects.create(object_number="703", name="C", management_type="weg", is_test=True)
    _dok(a, "a1.pdf", "registered")
    _dok(a, "a2.pdf", "error")
    _dok(b, "b1.pdf", "filed")  # nichts offen
    _dok(c, "c1.pdf", "hashed")
    return a, b, c


def test_sammelstart_reiht_nur_objekte_mit_offener_arbeit_ein(drei_objekte, admin_user, capsys):
    a, b, c = drei_objekte
    assert [o.pk for o in objects_with_open_work()] == [a.pk, c.pk]
    call_command("verarbeitung_alle_starten")
    out = capsys.readouterr().out
    assert "Vorschau: 2 Objekte mit offener Arbeit: 701, 703" in out
    assert not ProcessingRun.objects.exists()

    result = start_runs_for_all(user=admin_user)
    assert result["started"] == ["701", "703"] and len(result["runs"]) == 2
    runs = ProcessingRun.objects.filter(run_type=RunType.INCREMENTAL).order_by("id")
    assert [r.object_id for r in runs] == [a.pk, c.pk]
    # Objektserialitaet: ein Lauf startet sofort, der zweite wartet
    assert sorted(r.status for r in runs) == sorted([RunStatus.RUNNING, RunStatus.PENDING])
    assert AuditEvent.objects.filter(action="processing.start_all").count() == 1
    # zweiter Sammelstart: beide Objekte haben wartende oder laufende Laeufe, nichts Neues
    wieder = start_runs_for_all(user=admin_user)
    assert wieder["runs"] == [] and ProcessingRun.objects.count() == 2


def test_sammelstart_ueber_die_objektliste(drei_objekte, client_as, admin_user, clerk_user):
    c = client_as(admin_user)
    resp = c.get(reverse("object_list"))
    assert "Verarbeitung für alle Objekte starten" in resp.content.decode()
    resp = c.post(reverse("processing_start_all"), follow=True)
    assert resp.status_code == 200 and ProcessingRun.objects.count() == 2
    assert any("2 Objekt(e) eingereiht" in m.message for m in resp.context["messages"])
    resp = c.post(reverse("processing_start_all"), follow=True)
    assert any("nichts gestartet" in m.message for m in resp.context["messages"])
