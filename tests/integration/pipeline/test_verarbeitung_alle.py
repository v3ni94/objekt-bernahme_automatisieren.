"""„Verarbeitung für alle Objekte starten“ (13.09.2026): je aktivem Objekt mit offener Arbeit ein Nachlauf,
nacheinander nach processing.max_parallel_objects; Objekte ohne offene Dokumente oder mit wartendem Lauf werden
ausgelassen; Knopf auf der Objektliste, Kommando mit Vorschau."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents.models import Document
from apps.objects.models import ManagedObject
from apps.pipeline import jobs as jobs_mod
from apps.pipeline import runs as runs_mod
from apps.pipeline.jobs import (
    REDISPATCH_HOURS,
    dedupe_repeat_jobs,
    enqueue,
    idempotency_key,
    redispatch_lost_jobs,
)
from apps.pipeline.models import JobStatus, JobType, ProcessingJob, ProcessingRun, RunStatus, RunType
from apps.pipeline.runs import (
    REPAIR_AFTER_MINUTES,
    RESET_LIMIT,
    dispatch_run,
    maybe_finish_run,
    objects_with_open_work,
    start_runs_for_all,
)
from apps.review.models import CaseStatus, CaseType, ReviewCase

pytestmark = pytest.mark.django_db


def test_sweep_versendet_wartende_jobs_beendeter_laeufe(drei_objekte, admin_user, monkeypatch):
    """Seit 14.09.2026 endet ein Lauf bei offener Ablage; deren Jobs gehoeren dann zu einem beendeten Lauf. Geht
    ihre Nachricht bei einem Neustart verloren, sendet der Sweep sie trotzdem nach (Befund 20.09.2026: 124 Ablagen
    standen stundenlang auf „wartet“, der Sweep fand 0). Jobs abgebrochener Laeufe bleiben liegen."""
    a, _, c = drei_objekte
    start_runs_for_all(user=admin_user)
    lauf_a = ProcessingRun.objects.get(object=a)
    lauf_c = ProcessingRun.objects.get(object=c)
    ProcessingJob.objects.filter(status=JobStatus.PENDING).update(dispatched_at=timezone.now())
    gesendet: list[int] = []

    def _send(job, countdown=None):
        gesendet.append(job.pk)
        ProcessingJob.objects.filter(pk=job.pk).update(dispatched_at=timezone.now())

    monkeypatch.setattr(jobs_mod, "send", _send)
    ablage_dok = _dok(a, "ablage.pdf", "classified")
    ablage, _ = enqueue(
        JobType.FILE_TO_DRIVE,
        a,
        key=idempotency_key(JobType.FILE_TO_DRIVE, a.pk, ablage_dok.drive_file_id, ""),
        document=ablage_dok,
        run=lauf_a,
        dispatch=False,
    )
    verwaist, _ = enqueue(
        JobType.HASH, c, key=idempotency_key(JobType.HASH, c.pk, "abgebrochen"), run=lauf_c, dispatch=False
    )
    ProcessingRun.objects.filter(pk=lauf_a.pk).update(status=RunStatus.DONE, finished_at=timezone.now())
    ProcessingRun.objects.filter(pk=lauf_c.pk).update(status=RunStatus.ABORTED, finished_at=timezone.now())
    assert redispatch_lost_jobs() == 1 and gesendet == [ablage.pk]
    assert redispatch_lost_jobs() == 0
    verwaist.refresh_from_db()
    assert verwaist.dispatched_at is None


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


def test_versand_merkt_zeitpunkt_und_sweep_versendet_nur_verlorene_jobs(
    drei_objekte, admin_user, monkeypatch
):
    """14.09.2026: jeder Versand merkt dispatched_at. Der Sweep versendet nur Jobs laufender Laeufe, die nie
    versandt wurden oder deren Versand aelter als REDISPATCH_HOURS ist (Warteschlange geleert), hoechstens limit je
    Aufruf; dispatch_run versendet unterwegs befindliche Jobs nicht erneut. Damit waechst die Warteschlange nicht
    durch Doppelte (Redis war am 13.09.2026 durch Mehrfachversand voll)."""
    a, b, c = drei_objekte
    start_runs_for_all(user=admin_user)
    lauf_a = ProcessingRun.objects.get(object=a)
    lauf_c = ProcessingRun.objects.get(object=c)
    assert lauf_a.status == RunStatus.RUNNING and lauf_c.status == RunStatus.PENDING
    offen_a = ProcessingJob.objects.filter(object=a, status=JobStatus.PENDING)
    assert offen_a.exists() and all(j.dispatched_at is not None for j in offen_a)

    gesendet: list[int] = []

    def _send(job, countdown=None):
        gesendet.append(job.pk)
        ProcessingJob.objects.filter(pk=job.pk).update(dispatched_at=timezone.now())

    monkeypatch.setattr(jobs_mod, "send", _send)
    monkeypatch.setattr(runs_mod, "send", _send)
    # alles unterwegs: nichts nachzusenden
    assert redispatch_lost_jobs() == 0 and gesendet == []
    # nie versandter Job eines laufenden Laufs (z. B. waehrend des Laufs von der Aufarbeitung angelegt) ...
    neu = _dok(a, "a3.pdf", "registered")
    verloren, _ = enqueue(
        JobType.DISCOVER,
        a,
        key=idempotency_key(JobType.DISCOVER, a.pk, neu.drive_file_id, ""),
        document=neu,
        run=lauf_a,
        payload={"drive_file_id": neu.drive_file_id},
        dispatch=False,
    )
    # ... und einer eines wartenden Laufs, der erst mit dessen Start versandt wird
    wartend, _ = enqueue(
        JobType.HASH, c, key=idempotency_key(JobType.HASH, c.pk, "warte"), run=lauf_c, dispatch=False
    )
    assert verloren.dispatched_at is None and wartend.dispatched_at is None
    assert redispatch_lost_jobs() == 1 and gesendet == [verloren.pk]
    assert redispatch_lost_jobs() == 0
    # Versand aelter als das Fenster: erneut, aber hoechstens limit je Aufruf
    alt = timezone.now() - timedelta(hours=REDISPATCH_HOURS + 1)
    ProcessingJob.objects.filter(object=a, status=JobStatus.PENDING).update(dispatched_at=alt)
    n = ProcessingJob.objects.filter(object=a, status=JobStatus.PENDING).count()
    assert n >= 2
    assert redispatch_lost_jobs(limit=1) == 1
    assert redispatch_lost_jobs() == n - 1
    assert redispatch_lost_jobs() == 0
    # der Sweep meldet den Nachversand
    from apps.pipeline.tasks import sweep

    ProcessingJob.objects.filter(pk=verloren.pk).update(dispatched_at=None)
    assert sweep()["redispatched"] == 1
    # dispatch_run: unterwegs befindliche Jobs werden nicht erneut versandt
    lauf_c.status = RunStatus.RUNNING
    lauf_c.save(update_fields=["status"])
    gesendet.clear()
    dispatch_run(lauf_c)
    erster = sorted(gesendet)
    assert (
        wartend.pk in erster
        and len(erster) == ProcessingJob.objects.filter(object=c, status=JobStatus.PENDING).count()
    )
    dispatch_run(lauf_c)
    assert sorted(gesendet) == erster
    ProcessingJob.objects.filter(object=c).update(dispatched_at=alt)
    dispatch_run(lauf_c)
    assert len(gesendet) == 2 * len(erster)


def test_enqueue_legt_keinen_weiteren_wiederholungsjob_an_solange_einer_offen_ist(drei_objekte):
    """14.09.2026: Ist der Job zu einem Schluessel abgeschlossen, entsteht bei repeat ein Wiederholungsjob #n.
    Wartet dieser noch, liefert jeder weitere Aufruf denselben Job statt #n+1 (zuvor wuchs die Jobtabelle und die
    Warteschlange mit jedem Dokumenteneingang um einen Job je betroffenem Dokument)."""
    a, _, _ = drei_objekte
    dok = _dok(a, "wdh.pdf", "hashed")
    key = idempotency_key(JobType.ANALYZE_PAGES, a.pk, "sha-wdh")
    erster, created = enqueue(JobType.ANALYZE_PAGES, a, key=key, document=dok, dispatch=False)
    assert created and erster.idempotency_key == key
    ProcessingJob.objects.filter(pk=erster.pk).update(status=JobStatus.DONE)
    zweiter, created = enqueue(JobType.ANALYZE_PAGES, a, key=key, document=dok, dispatch=False)
    assert created and zweiter.idempotency_key == f"{key}#2" and zweiter.status == JobStatus.PENDING
    # solange #2 offen ist: kein #3, auch nicht bei vielen Aufrufen
    for _ in range(5):
        wieder, created = enqueue(JobType.ANALYZE_PAGES, a, key=key, document=dok, dispatch=False)
        assert not created and wieder.pk == zweiter.pk
    assert ProcessingJob.objects.filter(idempotency_key__startswith=key).count() == 2
    # ein laufender Wiederholungsjob zaehlt ebenso als offen
    ProcessingJob.objects.filter(pk=zweiter.pk).update(status=JobStatus.RUNNING)
    wieder, created = enqueue(JobType.ANALYZE_PAGES, a, key=key, document=dok, dispatch=False)
    assert not created and wieder.pk == zweiter.pk
    # erst nach Abschluss von #2 entsteht #3; ein Lauf wird dem offenen Wiederholungsjob nachgetragen
    ProcessingJob.objects.filter(pk=zweiter.pk).update(status=JobStatus.SKIPPED)
    lauf = ProcessingRun.objects.create(object=a, run_type=RunType.INCREMENTAL, status=RunStatus.RUNNING)
    dritter, created = enqueue(JobType.ANALYZE_PAGES, a, key=key, document=dok, dispatch=False)
    assert created and dritter.idempotency_key == f"{key}#3"
    ProcessingJob.objects.filter(pk=dritter.pk).update(run=None)
    wieder, created = enqueue(JobType.ANALYZE_PAGES, a, key=key, document=dok, run=lauf, dispatch=False)
    wieder.refresh_from_db()
    assert not created and wieder.pk == dritter.pk and wieder.run_id == lauf.pk


def test_jobs_bereinigen_setzt_ueberzaehlige_wiederholungsjobs_auf_skipped(drei_objekte, capsys):
    """Altbestand aus der Stoerung: mehrere wartende #n-Jobs zum selben Schluessel. Der aelteste bleibt, die
    uebrigen werden skipped (Grund „doppelter Wiederholungsjob“), Vorschau aendert nichts."""
    a, _, _ = drei_objekte
    dok = _dok(a, "mehrfach.pdf", "hashed")
    key = idempotency_key(JobType.ANALYZE_PAGES, a.pk, "sha-mehrfach")
    ProcessingJob.objects.create(
        object=a, document=dok, job_type=JobType.ANALYZE_PAGES, idempotency_key=key, status=JobStatus.DONE
    )
    jobs = [
        ProcessingJob.objects.create(
            object=a,
            document=dok,
            job_type=JobType.ANALYZE_PAGES,
            idempotency_key=f"{key}#{n}",
            status=JobStatus.PENDING,
        )
        for n in range(2, 6)
    ]
    anderer = ProcessingJob.objects.create(
        object=a,
        document=dok,
        job_type=JobType.OCR_CHUNK,
        idempotency_key=f"ocr:{a.pk}:sha-mehrfach:1#2",
        status=JobStatus.PENDING,
    )
    call_command("jobs_bereinigen")
    out = capsys.readouterr().out
    assert "Vorschau: 3 überzählige Wiederholungsjobs (analyze_pages=3)" in out and "nichts geändert" in out
    assert ProcessingJob.objects.filter(status=JobStatus.SKIPPED).count() == 0
    result = dedupe_repeat_jobs(dry_run=False)
    assert result["extra"] == 3 and result["updated"] == 3 and result["kept"] == 2
    jobs[0].refresh_from_db()
    assert jobs[0].status == JobStatus.PENDING
    for j in jobs[1:]:
        j.refresh_from_db()
        assert (
            j.status == JobStatus.SKIPPED and j.skip_reason == "doppelter Wiederholungsjob" and j.finished_at
        )
        assert j.events.filter(to_status=JobStatus.SKIPPED).exists()
    anderer.refresh_from_db()
    assert anderer.status == JobStatus.PENDING
    assert AuditEvent.objects.filter(action="processing.jobs_dedupe").count() == 1
    assert dedupe_repeat_jobs(dry_run=False)["extra"] == 0


def test_fehlerdokumente_werden_hoechstens_dreimal_automatisch_wiederaufgenommen(drei_objekte):
    """Ein Dokument im Status error wird beim Start eines Laufs zurueckgesetzt; nach RESET_LIMIT erledigten Faellen
    job_failed mit Wiederaufnahme bleibt es stehen, damit ein dauerhaft scheiterndes Dokument nicht bei jedem Eingang
    neue Jobs erzeugt."""
    a, _, _ = drei_objekte
    dok = _dok(a, "kaputt.pdf", "error")
    lauf = ProcessingRun.objects.create(object=a, run_type=RunType.INCREMENTAL, status=RunStatus.RUNNING)
    for i in range(RESET_LIMIT - 1):
        ReviewCase.objects.create(
            object=a,
            case_type=CaseType.UNCLEAR,
            case_subtype="job_failed",
            document=dok,
            batch_key=f"job_failed:test-{i}",
            status=CaseStatus.RESOLVED,
            resolution={"action": "reprocess", "run_id": 1},
        )
    ReviewCase.objects.create(
        object=a,
        case_type=CaseType.UNCLEAR,
        case_subtype="job_failed",
        document=dok,
        batch_key="job_failed:offen",
    )
    dispatch_run(lauf)
    dok.refresh_from_db()
    assert dok.status == "registered"  # zurueckgesetzt; der offene Fall ist erledigt
    assert ReviewCase.objects.filter(document=dok, status=CaseStatus.RESOLVED).count() == RESET_LIMIT
    # erneuter Fehler: jetzt ist die Grenze erreicht, das Dokument bleibt error
    Document.objects.filter(pk=dok.pk).update(status="error")
    ReviewCase.objects.create(
        object=a,
        case_type=CaseType.UNCLEAR,
        case_subtype="job_failed",
        document=dok,
        batch_key="job_failed:offen2",
    )
    dispatch_run(lauf)
    dok.refresh_from_db()
    assert dok.status == "error"
    assert ReviewCase.objects.filter(document=dok, status=CaseStatus.OPEN).count() == 1


def test_sweep_versorgt_unterbrochene_laeufe_und_verwaiste_jobs_nach(drei_objekte, monkeypatch):
    """14.09.2026: Laeufe wurden als laufend markiert, der Jobversand brach am vollen Redis ab. Ihre Jobs hingen ohne
    Lauf und ohne Nachricht, documents_total blieb leer. Der Sweep ordnet verwaiste Jobs dem laufenden Lauf zu,
    versendet sie und versorgt den Lauf mit dispatch_run nach; ein frisch gestarteter Lauf bleibt unangetastet."""
    a, _, c = drei_objekte
    alt = timezone.now() - timedelta(minutes=REPAIR_AFTER_MINUTES + 1)
    lauf_a = ProcessingRun.objects.create(
        object=a, run_type=RunType.INCREMENTAL, status=RunStatus.RUNNING, started_at=alt
    )
    lauf_c = ProcessingRun.objects.create(
        object=c, run_type=RunType.INCREMENTAL, status=RunStatus.RUNNING, started_at=timezone.now()
    )
    # offener Job, damit lauf_c nicht als fertig abgeschlossen wird
    enqueue(JobType.HASH, c, key=idempotency_key(JobType.HASH, c.pk, "frisch"), run=lauf_c, dispatch=False)
    verwaist, _ = enqueue(
        JobType.DISCOVER, a, key=idempotency_key(JobType.DISCOVER, a.pk, "verwaist", ""), dispatch=False
    )
    assert verwaist.run_id is None and verwaist.dispatched_at is None and lauf_a.documents_total is None
    # kein Job des Objekts in den letzten Minuten angefasst: der Versand gilt als abgebrochen
    ProcessingJob.objects.filter(object=a).update(updated_at=alt)

    gesendet: list[int] = []

    def _send(job, countdown=None):
        gesendet.append(job.pk)
        ProcessingJob.objects.filter(pk=job.pk).update(dispatched_at=timezone.now())

    monkeypatch.setattr(jobs_mod, "send", _send)
    monkeypatch.setattr(runs_mod, "send", _send)
    from apps.pipeline.tasks import sweep

    result = sweep()
    assert result["runs_repaired"] == 1  # nur der alte Lauf; lauf_c ist zu frisch
    lauf_a.refresh_from_db()
    lauf_c.refresh_from_db()
    assert (
        lauf_a.documents_total == Document.objects.filter(object=a).count() and lauf_c.documents_total is None
    )
    verwaist.refresh_from_db()
    assert verwaist.run_id == lauf_a.pk and verwaist.dispatched_at is not None and verwaist.pk in gesendet
    # die Startjobs der Dokumente des Objekts sind angelegt und versandt
    assert (
        ProcessingJob.objects.filter(object=a, status=JobStatus.PENDING, dispatched_at__isnull=True).count()
        == 0
    )
    # zweiter Sweep: nichts mehr zu reparieren, nichts doppelt versandt
    vorher = len(gesendet)
    result = sweep()
    assert result["runs_repaired"] == 0 and result["redispatched"] == 0 and len(gesendet) == vorher
    # ein Lauf, dessen Versand gerade laeuft (Jobs in den letzten Minuten angefasst), wird nicht erneut versorgt
    from apps.pipeline.runs import repair_interrupted_runs

    lauf_d = ProcessingRun.objects.create(
        object=c, run_type=RunType.FULL, status=RunStatus.RUNNING, started_at=alt
    )
    enqueue(JobType.HASH, c, key=idempotency_key(JobType.HASH, c.pk, "laeuft"), run=lauf_d, dispatch=False)
    assert repair_interrupted_runs() == []


def test_lauf_endet_trotz_ablage_die_auf_den_objektordner_wartet(drei_objekte, monkeypatch):
    """14.09.2026 (Freigabe Geschaeftsfuehrung): Ablagejobs halten den Lauf nicht offen, ob sie auf den Objektordner
    warten oder faellig sind; der Lauf gibt seinen Platz frei, die Ablage laeuft weiter. Andere offene Jobs halten
    den Lauf offen. Das Nachraeumen des Objektordners folgt mit der letzten Ablage des abgeschlossenen Laufs."""
    from apps.drive import auto_cleanup as cleanup_mod
    from apps.pipeline.jobs import _after_done

    ausgeloest: list[tuple] = []
    monkeypatch.setattr(
        cleanup_mod,
        "trigger_auto_cleanup",
        lambda object_id, trigger="run": ausgeloest.append((object_id, trigger)),
    )
    a, _, _ = drei_objekte
    lauf = ProcessingRun.objects.create(
        object=a, run_type=RunType.INCREMENTAL, status=RunStatus.RUNNING, started_at=timezone.now()
    )
    dok = _dok(a, "abgelegt.pdf", "classified")
    wartend = ProcessingJob.objects.create(
        object=a,
        document=dok,
        run=lauf,
        job_type=JobType.FILE_TO_DRIVE,
        idempotency_key=f"file:{a.pk}:warte",
        status=JobStatus.PENDING,
        next_attempt_at=timezone.now() + timedelta(minutes=10),
        last_error="wartet: Ablageziel noch nicht vorhanden: Objektordner unbekannt: Ordnerabgleich zuerst ausführen (CR 9)",
    )
    anderer = ProcessingJob.objects.create(
        object=a,
        run=lauf,
        job_type=JobType.HASH,
        idempotency_key=f"hash:{a.pk}:offen",
        status=JobStatus.PENDING,
    )
    assert maybe_finish_run(lauf) is False
    ProcessingJob.objects.filter(pk=anderer.pk).update(status=JobStatus.DONE)
    # auch ein faelliger Ablagejob haelt den Lauf nicht mehr offen
    ProcessingJob.objects.filter(pk=wartend.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    assert maybe_finish_run(lauf) is True
    lauf.refresh_from_db()
    wartend.refresh_from_db()
    assert lauf.status == RunStatus.DONE and wartend.status == JobStatus.PENDING
    assert ausgeloest == [
        (a.pk, "run")
    ]  # Nachraeumen beim Laufabschluss (ueberspringt bei offenen Jobs selbst)
    # zweite Ablage noch offen: kein Nachraeumen nach der ersten erledigten Ablage
    zweite = ProcessingJob.objects.create(
        object=a,
        document=dok,
        run=lauf,
        job_type=JobType.FILE_TO_DRIVE,
        idempotency_key=f"file:{a.pk}:zweite",
        status=JobStatus.PENDING,
    )
    ProcessingJob.objects.filter(pk=wartend.pk).update(status=JobStatus.DONE)
    wartend.refresh_from_db()
    _after_done(wartend)
    assert ausgeloest == [(a.pk, "run")]
    # letzte Ablage des abgeschlossenen Laufs erledigt: Nachraeumen mit Ausloeser filing
    ProcessingJob.objects.filter(pk=zweite.pk).update(status=JobStatus.DONE)
    zweite.refresh_from_db()
    _after_done(zweite)
    assert ausgeloest == [(a.pk, "run"), (a.pk, "filing")]


def test_sammelstart_stoesst_ordneranlage_fuer_objekte_ohne_ordner_an(drei_objekte, admin_user, monkeypatch):
    """Objekte ohne Objektordner werden nicht uebersprungen, sondern die Ordneranlage wird angestossen (bei
    unvollstaendiger Anschrift entsteht ein vorlaeufiger Ordner); die Meldung nennt sie."""
    import apps.drive.tasks as drive_tasks
    from apps.drive.models import DriveNode as DriveNodeRow
    from apps.drive.models import NodeKind

    a, _, c = drei_objekte
    aufrufe: list[tuple] = []
    monkeypatch.setattr(
        drive_tasks,
        "trigger_object_folders",
        lambda object_id, **kw: aufrufe.append((object_id, kw.get("trigger"), kw.get("force"))) or "queued",
    )
    DriveNodeRow.objects.create(
        object=c, node_kind=NodeKind.OBJECT_ROOT, drive_file_id="root-c", drive_name="703 C", is_folder=True
    )
    vorschau = start_runs_for_all(user=admin_user, dry_run=True)
    assert vorschau["without_folder"] == ["701"] and aufrufe == []
    result = start_runs_for_all(user=admin_user)
    assert result["without_folder"] == ["701"] and aufrufe == [(a.pk, "start_all", True)]
    assert result["folders_requested"] == [{"object": "701", "status": "queued"}]
    assert len(result["runs"]) == 2


def test_dokumenteneingang_versorgt_laufenden_lauf_nur_mit_dem_neuen_dokument(
    drei_objekte, admin_user, monkeypatch
):
    """14.09.2026: ensure_run rief fuer einen laufenden Lauf dispatch_run ueber alle Dokumente des Objekts auf, bei
    Objekt 216 (4.733 Dokumente) je Paperless-Uebernahme; fuer Dokumente mit erledigtem Startjob entstanden dabei
    Wiederholungsjobs (#n) und wartende Jobs wurden erneut versandt. Jetzt erhaelt der laufende Lauf nur den
    Startjob des neuen Dokuments; ohne Dokumente bleibt der volle Nachversand."""
    from apps.documents import ingest as ingest_mod

    a, b, c = drei_objekte
    start_runs_for_all(user=admin_user)
    lauf = ProcessingRun.objects.get(object=a, status=RunStatus.RUNNING)
    # Lage waehrend des Laufs: Startjobs erledigt, die Dokumente warten auf den naechsten Schritt
    ProcessingJob.objects.filter(object=a).update(status=JobStatus.DONE)
    vorher = ProcessingJob.objects.filter(object=a).count()
    gesendet: list[int] = []

    def _send(job, countdown=None):
        gesendet.append(job.pk)
        ProcessingJob.objects.filter(pk=job.pk).update(dispatched_at=timezone.now())

    monkeypatch.setattr(jobs_mod, "send", _send)
    monkeypatch.setattr(runs_mod, "send", _send)
    neu = Document.objects.create(
        object=a,
        size_bytes=8,
        mime_type="application/pdf",
        original_name="p1.pdf",
        current_name="p1.pdf",
        source="paperless",
        status="registered",
        first_seen_at=timezone.now(),
    )
    assert ingest_mod.ensure_run(a, documents=[neu]) == lauf
    job = ProcessingJob.objects.get(document=neu)
    assert job.job_type == JobType.HASH and job.run == lauf and gesendet == [job.pk]
    # kein Durchlauf ueber die uebrigen Dokumente: keine Wiederholungsjobs, keine weiteren Nachrichten
    assert ProcessingJob.objects.filter(object=a).count() == vorher + 1
    assert not ProcessingJob.objects.filter(object=a, idempotency_key__contains="#").exists()
    # ohne Dokumente bleibt der volle Nachversand
    aufrufe: list[int] = []
    monkeypatch.setattr(ingest_mod, "dispatch_run", lambda run: aufrufe.append(run.pk))
    assert ingest_mod.ensure_run(a) == lauf and aufrufe == [lauf.pk]


def test_dispatch_run_legt_keinen_startjob_fuer_dokumente_in_der_kette_an(
    drei_objekte, admin_user, monkeypatch
):
    """14.09.2026, Objekt 216: ein Dokument bleibt hashed, waehrend seine OCR-Bloecke warten; der Startjob
    analyze_pages ist erledigt. dispatch_run legte je Aufruf einen Wiederholungsjob analyze_pages #n an (9.512 nie
    versandte Jobs). Dokumente mit offenem Job erhalten keinen Startjob; ein Wiederholungsjob analyze_pages wird
    uebersprungen, solange OCR oder Zusammenfuehrung offen sind."""
    from apps.pipeline import tasks as tasks_mod

    a, b, c = drei_objekte
    monkeypatch.setattr(jobs_mod, "send", lambda job, countdown=None: None)
    monkeypatch.setattr(runs_mod, "send", lambda job, countdown=None: None)
    start_runs_for_all(user=admin_user)
    lauf = ProcessingRun.objects.get(object=a, status=RunStatus.RUNNING)
    doc = Document.objects.get(object=a, original_name="a1.pdf")
    doc.status, doc.sha256 = "hashed", "ab" * 32
    doc.save(update_fields=["status", "sha256"])
    ProcessingJob.objects.filter(document=doc).update(status=JobStatus.DONE)  # discover von a1 erledigt
    analyse, _ = enqueue(
        JobType.ANALYZE_PAGES,
        a,
        key=idempotency_key(JobType.ANALYZE_PAGES, a.pk, doc.sha256),
        document=doc,
        run=lauf,
    )
    ProcessingJob.objects.filter(pk=analyse.pk).update(status=JobStatus.DONE)
    block, _ = enqueue(
        JobType.OCR_CHUNK,
        a,
        key=f"ocr:{a.pk}:{doc.sha256}:1",
        document=doc,
        run=lauf,
        payload={"chunk_no": 1},
    )
    vorher = ProcessingJob.objects.filter(object=a).count()
    dispatch_run(lauf)
    dispatch_run(lauf)
    assert ProcessingJob.objects.filter(object=a).count() == vorher
    assert not ProcessingJob.objects.filter(
        document=doc, job_type=JobType.ANALYZE_PAGES, idempotency_key__contains="#"
    ).exists()
    # ein bereits vorhandener Wiederholungsjob wird uebersprungen, solange der OCR-Block offen ist
    wieder, _ = enqueue(
        JobType.ANALYZE_PAGES,
        a,
        key=idempotency_key(JobType.ANALYZE_PAGES, a.pk, doc.sha256),
        document=doc,
        run=lauf,
    )
    assert "#" in wieder.idempotency_key
    assert tasks_mod.analyze_pages(wieder.pk) == {"job_id": wieder.pk, "skipped": "chain_running"}
    wieder.refresh_from_db()
    assert wieder.status == JobStatus.SKIPPED and block.pk in set(
        ProcessingJob.objects.filter(object=a, status=JobStatus.PENDING).values_list("pk", flat=True)
    )


def test_plattenreserve_pausiert_download_und_transit_kopie_wird_nach_ablage_geloescht(
    drei_objekte, admin_user, monkeypatch, tmp_path
):
    """15.09.2026: Erreicht die Platte die Reserve, wartet der Hash-Job (DeferJob) statt als Fehler zu enden; die
    Transit-Kopie eines Uploads oder einer Paperless-Uebernahme wird nach der Ablage in Drive geloescht,
    Bestandsdateien aus Drive haben keine Kopie."""
    from apps.pipeline import storage as storage_mod
    from apps.pipeline import tasks as tasks_mod

    a, _, _ = drei_objekte
    monkeypatch.setattr(jobs_mod, "send", lambda job, countdown=None: None)
    quelle = tmp_path / "eingang.pdf"
    quelle.write_bytes(b"%PDF-1.4 test")
    doc = Document.objects.create(
        object=a,
        size_bytes=13,
        mime_type="application/pdf",
        original_name="eingang.pdf",
        current_name="eingang.pdf",
        source="paperless",
        source_path=str(quelle),
        status="registered",
        first_seen_at=timezone.now(),
    )
    job, _ = enqueue(
        JobType.HASH, a, key=idempotency_key(JobType.HASH, a.pk, f"upload-{doc.pk}"), document=doc, run=None
    )

    def _voll(path=None):
        raise storage_mod.DiskFull("Nur 17,0 GB frei, Reserve 18 GB")

    monkeypatch.setattr(storage_mod, "ensure_disk_reserve", _voll)
    ergebnis = tasks_mod.hash_document(job.pk)
    job.refresh_from_db()
    doc.refresh_from_db()
    assert ergebnis["job_id"] == job.pk and "Plattenreserve" in ergebnis["deferred"]
    assert job.status == JobStatus.PENDING and job.next_attempt_at is not None and doc.status == "registered"
    assert quelle.exists()
    # Transit-Kopie nach der Ablage
    assert tasks_mod._remove_transit_copy(doc) is True and not quelle.exists()
    bestand = Document.objects.get(object=a, original_name="a1.pdf")
    assert tasks_mod._remove_transit_copy(bestand) is False


def test_transit_bereinigen_loescht_nur_kopien_abgelegter_dokumente(drei_objekte, tmp_path, capsys):
    """15.09.2026: Transit-Kopien (Upload, Paperless) werden geloescht, sobald das Dokument in Drive liegt; Kopien
    von Dokumenten in Pruefung bleiben, Bestandsdateien aus Drive sind nicht betroffen. Vorschau ohne --echt."""
    a, _, _ = drei_objekte

    def _kopie(name, source, status, drive_id):
        ordner = tmp_path / "transit" / str(a.pk) / "incoming" / name
        ordner.mkdir(parents=True)
        datei = ordner / f"{name}.pdf"
        datei.write_bytes(b"%PDF-1.4 " + name.encode())
        Document.objects.create(
            object=a,
            size_bytes=12,
            mime_type="application/pdf",
            original_name=f"{name}.pdf",
            current_name=f"{name}.pdf",
            source=source,
            source_path=str(datei),
            drive_file_id=drive_id,
            status=status,
            first_seen_at=timezone.now(),
        )
        return datei

    abgelegt = _kopie("p1", "paperless", "filed", "d-p1")
    dublette = _kopie("p2", "paperless", "duplicate", "d-p2")
    pruefung = _kopie("p3", "paperless", "review", None)
    upload = _kopie("u1", "upload", "filed", "d-u1")
    call_command("transit_bereinigen")
    out = capsys.readouterr().out
    assert "Vorschau: 3 Transit-Kopien" in out and all(
        p.exists() for p in (abgelegt, dublette, pruefung, upload)
    )
    call_command("transit_bereinigen", echt=True)
    out = capsys.readouterr().out
    assert "Gelöscht: 3 Transit-Kopien" in out and "leere Eingangsordner entfernt: 3" in out
    assert not abgelegt.exists() and not dublette.exists() and not upload.exists() and pruefung.exists()
    assert not abgelegt.parent.exists() and pruefung.parent.exists()
    assert AuditEvent.objects.filter(action="processing.transit_cleanup").count() == 1
