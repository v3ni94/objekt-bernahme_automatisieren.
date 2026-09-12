"""Operationsliste der Synchronisation (Fallgruppe D): idempotentes Einreihen ueber den Schluessel, Ausgaenge der
Handler (Ergebnis, Skip, Defer, Retry mit Backoff, Block, unerwartete Ausnahme, fehlender Handler), Reservierung
und Freigabe haengender Operationen, Faelligkeit im Dispatcher, Verwerfen und erneutes Anstossen mit Audit,
Zusammenfassung, Prioritaet und der Beat-Task."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.documents.models import Document
from apps.sync import config, operations
from apps.sync.models import OperationKind, OperationStatus, SyncOperation, SyncSystem
from apps.sync.operations import Block, Defer, Retry, Skip

pytestmark = pytest.mark.django_db

# Operationsarten ohne Anwendungs-Handler: der CHECK-Constraint der Tabelle verlangt echte Werte aus OperationKind,
# daher registrieren die Tests ihre Handler unter diesen Arten und raeumen sie am Testende wieder aus.
KIND_A = OperationKind.DRIVE_REGISTER
KIND_B = OperationKind.PAPERLESS_PULL_META
KIND_OHNE_HANDLER = OperationKind.ASSIGN_OBJECT


@pytest.fixture
def testhandler():
    """Registriert Handler fuer Testarten und stellt am Testende nur die betroffenen Schluessel in HANDLERS wieder
    her (die beim Laden der Ablaeufe registrierten Anwendungs-Handler bleiben erhalten)."""
    vorher: dict[str, object] = {}

    def _register(kind: str, fn=None):
        def _decorator(func):
            if kind not in vorher:
                vorher[kind] = operations.HANDLERS.get(kind)
            operations.HANDLERS[kind] = func
            return func

        return _decorator(fn) if fn is not None else _decorator

    yield _register
    for kind, alt in vorher.items():
        if alt is None:
            operations.HANDLERS.pop(kind, None)
        else:
            operations.HANDLERS[kind] = alt


def _enqueue(kind: str = KIND_A, key: str = "test:a", **kwargs) -> tuple[SyncOperation, bool]:
    kwargs.setdefault("system", SyncSystem.APP)
    return operations.enqueue(kind, key=key, **kwargs)


def _faellig(op: SyncOperation) -> None:
    SyncOperation.objects.filter(pk=op.pk).update(next_attempt_at=None)


def _run(op: SyncOperation) -> dict:
    ergebnis = operations.run(op.pk, worker="test")
    op.refresh_from_db()
    return ergebnis


def _zwischen(zeitpunkt, vorher, nachher, sekunden: int, toleranz: int = 2) -> bool:
    """Liegt zeitpunkt etwa sekunden nach dem Zeitfenster [vorher, nachher]?"""
    return (
        vorher + timedelta(seconds=sekunden - toleranz)
        <= zeitpunkt
        <= nachher + timedelta(seconds=sekunden + toleranz)
    )


def _wirft(exc: Exception):
    """Handler, der die uebergebene Ausnahme wirft."""

    def _handler(op):
        raise exc

    return _handler


def _dokument(obj) -> Document:
    return Document.objects.create(
        object=obj,
        sha256="e" * 64,
        size_bytes=1234,
        mime_type="application/pdf",
        original_name="Test.pdf",
        current_name="Test.pdf",
        source="upload",
        status="hashed",
        first_seen_at=timezone.now(),
    )


# --- 1. Einreihen ------------------------------------------------------------------------------------------


def test_enqueue_ist_idempotent_und_reiht_fehlgeschlagene_neu_ein(seeded):
    assert not SyncOperation.objects.exists()
    vorher = timezone.now()
    op, created = _enqueue(payload={"n": 1}, priority=80)
    assert created and op.status == OperationStatus.PENDING and op.attempt_count == 0
    assert op.kind == KIND_A and op.system == SyncSystem.APP and op.priority == 80
    assert op.max_attempts == config.operation_max_attempts()
    assert op.next_attempt_at is not None and vorher <= op.next_attempt_at <= timezone.now()

    # gleicher Schluessel: dieselbe Operation, kein zweiter Datensatz, Nutzdaten bleiben unveraendert
    op2, created2 = _enqueue(payload={"n": 2}, priority=10)
    assert not created2 and op2.pk == op.pk
    op.refresh_from_db()
    assert op.payload == {"n": 1} and op.priority == 80
    assert SyncOperation.objects.filter(op_key="test:a").count() == 1

    # erledigte Operation gleichen Schluessels wird nicht wiederholt
    SyncOperation.objects.filter(pk=op.pk).update(status=OperationStatus.DONE)
    op3, created3 = _enqueue(payload={"n": 3})
    assert not created3 and op3.pk == op.pk
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.payload == {"n": 1}

    # fehlgeschlagene, verworfene oder blockierte Operation: wieder wartend, Zaehler und Fehler zurueckgesetzt,
    # neue Nutzdaten uebernommen
    for status in (OperationStatus.FAILED, OperationStatus.CANCELLED, OperationStatus.BLOCKED):
        SyncOperation.objects.filter(pk=op.pk).update(
            status=status,
            attempt_count=3,
            last_error="alter Fehler",
            blocked_reason="alter Grund",
            next_attempt_at=timezone.now() + timedelta(hours=1),
        )
        op4, created4 = _enqueue(payload={"n": 4})
        assert not created4 and op4.pk == op.pk
        op.refresh_from_db()
        assert op.status == OperationStatus.PENDING and op.attempt_count == 0
        assert op.last_error is None and op.blocked_reason is None
        assert op.payload == {"n": 4} and op.next_attempt_at <= timezone.now()
    assert SyncOperation.objects.count() == 1


def test_enqueue_mit_wartezeit_und_unbekannter_art(seeded, testhandler):
    aufrufe: list[int] = []
    testhandler(KIND_A, lambda op: aufrufe.append(op.pk) or {"ok": True})
    vorher = timezone.now()
    op, created = _enqueue(key="test:spaeter", delay_seconds=120)
    assert created and _zwischen(op.next_attempt_at, vorher, timezone.now(), 120)
    assert operations.run_pending() == [] and aufrufe == []
    _faellig(op)
    assert len(operations.run_pending()) == 1 and aufrufe == [op.pk]
    with pytest.raises(ValueError, match="unbekannte Operationsart"):
        _enqueue(kind="gibt_es_nicht", key="test:unbekannt")
    assert SyncOperation.objects.count() == 1


# --- 2. Ausgaenge der Handler ------------------------------------------------------------------------------


def test_ergebnis_dict_fuehrt_zu_done(seeded, testhandler):
    gesehen: list[SyncOperation] = []

    @testhandler(KIND_A)
    def _handler(op):
        gesehen.append(op)
        return {"paperless_id": 7}

    op, _ = _enqueue(payload={"x": 1})
    ergebnis = _run(op)
    assert ergebnis == {"op_id": op.pk, "done": True, "result": {"paperless_id": 7}}
    assert op.status == OperationStatus.DONE and op.result == {"paperless_id": 7}
    assert op.attempt_count == 1 and op.last_error is None
    assert op.locked_by is None and op.locked_at is None
    assert op.started_at is not None and op.finished_at is not None and op.finished_at >= op.started_at
    assert [g.pk for g in gesehen] == [op.pk] and gesehen[0].status == OperationStatus.RUNNING
    assert gesehen[0].payload == {"x": 1}
    # erledigt: kein zweiter Lauf
    assert _run(op) == {"op_id": op.pk, "reserved": False}
    assert op.attempt_count == 1


def test_handler_ohne_rueckgabe_liefert_leeres_ergebnis(seeded, testhandler):
    testhandler(KIND_A, lambda op: None)
    op, _ = _enqueue()
    assert _run(op)["done"] is True
    assert op.status == OperationStatus.DONE and op.result == {}


def test_skip_fuehrt_zu_skipped_mit_begruendung(seeded, testhandler):
    @testhandler(KIND_A)
    def _handler(op):
        raise Skip("bereits verknüpft")

    op, _ = _enqueue()
    assert _run(op) == {"op_id": op.pk, "skipped": "bereits verknüpft"}
    assert op.status == OperationStatus.SKIPPED and op.result == {"skipped": "bereits verknüpft"}
    assert op.finished_at is not None and op.locked_by is None and op.attempt_count == 1


def test_defer_stellt_zurueck_ohne_fehlversuch(seeded, testhandler):
    @testhandler(KIND_A)
    def _handler(op):
        raise Defer("Paperless verarbeitet noch", 120)

    op, _ = _enqueue()
    vorher = timezone.now()
    ergebnis = _run(op)
    nachher = timezone.now()
    assert ergebnis == {"op_id": op.pk, "deferred": "Paperless verarbeitet noch"}
    assert op.status == OperationStatus.PENDING and op.attempt_count == 0
    assert _zwischen(op.next_attempt_at, vorher, nachher, 120)
    assert op.last_error == "Paperless verarbeitet noch"
    assert op.locked_by is None and op.locked_at is None and op.finished_at is None
    # noch nicht faellig: der lokale Runner fasst sie nicht an
    assert operations.run_pending() == []
    # Standardwartezeit von Defer ohne Angabe: 300 Sekunden
    testhandler(KIND_A, _wirft(Defer("Sperre")))
    _faellig(op)
    vorher = timezone.now()
    _run(op)
    assert _zwischen(op.next_attempt_at, vorher, timezone.now(), 300) and op.attempt_count == 0


def test_retry_zaehlt_versuche_mit_zunehmender_wartezeit(seeded, testhandler):
    @testhandler(KIND_A)
    def _handler(op):
        raise Retry(f"Paperless nicht erreichbar (Versuch {op.attempt_count})")

    op, _ = _enqueue()
    assert op.max_attempts >= 3
    vorher = timezone.now()
    assert _run(op) == {"op_id": op.pk, "retry": True}
    nachher = timezone.now()
    assert op.status == OperationStatus.PENDING and op.attempt_count == 1
    assert _zwischen(op.next_attempt_at, vorher, nachher, 30)
    assert op.last_error == "Paperless nicht erreichbar (Versuch 1)"
    assert op.locked_by is None and op.finished_at is None
    assert operations.run_pending() == []  # Wartezeit wird eingehalten

    _faellig(op)
    vorher = timezone.now()
    _run(op)
    nachher = timezone.now()
    assert op.status == OperationStatus.PENDING and op.attempt_count == 2
    assert _zwischen(op.next_attempt_at, vorher, nachher, 60)
    assert op.last_error == "Paperless nicht erreichbar (Versuch 2)"

    # ausdrueckliche Wartezeit (zum Beispiel Retry-After) hat Vorrang vor dem Backoff
    testhandler(KIND_A, _wirft(Retry("Ratenbegrenzung", seconds=10)))
    _faellig(op)
    vorher = timezone.now()
    _run(op)
    assert op.attempt_count == 3 and _zwischen(op.next_attempt_at, vorher, timezone.now(), 10)


def test_retry_nach_hoechstzahl_der_versuche_fehlgeschlagen(seeded, testhandler):
    @testhandler(KIND_A)
    def _handler(op):
        raise Retry("Paperless-Fehler: HTTP 500")

    op, _ = _enqueue()
    SyncOperation.objects.filter(pk=op.pk).update(max_attempts=2)
    vorher = timezone.now()
    _run(op)
    assert op.status == OperationStatus.PENDING and op.attempt_count == 1
    assert _zwischen(op.next_attempt_at, vorher, timezone.now(), 30)
    _faellig(op)
    assert _run(op) == {"op_id": op.pk, "failed": True}
    assert op.status == OperationStatus.FAILED and op.attempt_count == 2
    assert op.last_error == "Paperless-Fehler: HTTP 500" and op.finished_at is not None
    assert op.locked_by is None
    # fehlgeschlagen: kein weiterer Lauf ohne Eingriff
    assert operations.run_pending() == []
    assert operations.summary()["failed"] == 1


def test_block_bleibt_sichtbar(seeded, testhandler):
    @testhandler(KIND_A)
    def _handler(op):
        raise Block("Zugriff verweigert (Token oder Rechte prüfen)")

    op, _ = _enqueue()
    ergebnis = _run(op)
    assert ergebnis == {"op_id": op.pk, "blocked": "Zugriff verweigert (Token oder Rechte prüfen)"}
    assert op.status == OperationStatus.BLOCKED
    assert op.blocked_reason == "Zugriff verweigert (Token oder Rechte prüfen)"
    assert op.last_error == op.blocked_reason and op.finished_at is not None
    assert operations.run_pending() == []
    assert operations.summary()["failed"] == 1


def test_unerwartete_ausnahme_wird_begrenzt_wiederholt(seeded, testhandler):
    @testhandler(KIND_A)
    def _handler(op):
        raise RuntimeError("Programmierfehler im Handler")

    op, _ = _enqueue()
    SyncOperation.objects.filter(pk=op.pk).update(max_attempts=2)
    vorher = timezone.now()
    assert _run(op) == {"op_id": op.pk, "retry": True}
    assert op.status == OperationStatus.PENDING and op.attempt_count == 1
    assert op.last_error == "RuntimeError: Programmierfehler im Handler"
    assert _zwischen(op.next_attempt_at, vorher, timezone.now(), 30)
    assert op.locked_by is None
    _faellig(op)
    assert _run(op) == {"op_id": op.pk, "failed": True}
    assert op.status == OperationStatus.FAILED and op.attempt_count == 2
    assert op.last_error == "RuntimeError: Programmierfehler im Handler" and op.finished_at is not None


def test_ohne_handler_blockiert(seeded):
    from apps.sync import flows

    flows.load_all()
    assert KIND_OHNE_HANDLER not in operations.HANDLERS
    op, _ = _enqueue(kind=KIND_OHNE_HANDLER, key="test:ohne-handler")
    assert _run(op) == {"op_id": op.pk, "blocked": True}
    assert op.status == OperationStatus.BLOCKED
    assert "kein Handler" in op.blocked_reason and KIND_OHNE_HANDLER in op.blocked_reason
    assert op.attempt_count == 1 and op.finished_at is not None


# --- 3. Reservierung ---------------------------------------------------------------------------------------


def test_laufende_operation_wird_nicht_erneut_reserviert(seeded, testhandler):
    aufrufe: list[int] = []
    testhandler(KIND_A, lambda op: aufrufe.append(op.pk) or {})
    op, _ = _enqueue()
    jetzt = timezone.now()
    SyncOperation.objects.filter(pk=op.pk).update(
        status=OperationStatus.RUNNING, locked_by="anderer-worker", locked_at=jetzt, heartbeat_at=jetzt
    )
    assert _run(op) == {"op_id": op.pk, "reserved": False}
    assert op.status == OperationStatus.RUNNING and op.locked_by == "anderer-worker"
    assert op.attempt_count == 0 and aufrufe == []
    assert operations.run_pending() == [] and aufrufe == []
    # unbekannte ID ebenso ohne Wirkung
    assert operations.run(op.pk + 1000) == {"op_id": op.pk + 1000, "reserved": False}


def test_release_stale_gibt_nur_alte_laufende_operationen_frei(seeded, testhandler):
    aufrufe: list[str] = []
    testhandler(KIND_A, lambda op: aufrufe.append(op.op_key) or {})
    alt, _ = _enqueue(key="test:alt")
    frisch, _ = _enqueue(key="test:frisch")
    fertig, _ = _enqueue(key="test:fertig")
    jetzt = timezone.now()
    SyncOperation.objects.filter(pk=alt.pk).update(
        status=OperationStatus.RUNNING,
        locked_by="verlorener-worker",
        locked_at=jetzt - timedelta(minutes=31),
        heartbeat_at=jetzt - timedelta(minutes=31),
        attempt_count=1,
    )
    SyncOperation.objects.filter(pk=frisch.pk).update(
        status=OperationStatus.RUNNING,
        locked_by="aktiver-worker",
        locked_at=jetzt - timedelta(minutes=1),
        heartbeat_at=jetzt - timedelta(minutes=1),
        attempt_count=1,
    )
    SyncOperation.objects.filter(pk=fertig.pk).update(
        status=OperationStatus.DONE, heartbeat_at=jetzt - timedelta(hours=2)
    )
    assert operations.release_stale() == 1
    alt.refresh_from_db()
    frisch.refresh_from_db()
    fertig.refresh_from_db()
    assert alt.status == OperationStatus.PENDING and alt.locked_by is None and alt.locked_at is None
    assert alt.next_attempt_at is not None and alt.next_attempt_at <= timezone.now()
    assert frisch.status == OperationStatus.RUNNING and frisch.locked_by == "aktiver-worker"
    assert fertig.status == OperationStatus.DONE
    # zweiter Aufruf: nichts mehr freizugeben; die freigegebene Operation laeuft danach normal weiter
    assert operations.release_stale() == 0
    assert len(operations.run_pending()) == 1 and aufrufe == ["test:alt"]
    alt.refresh_from_db()
    assert alt.status == OperationStatus.DONE and alt.attempt_count == 2
    frisch.refresh_from_db()
    assert frisch.status == OperationStatus.RUNNING


# --- 4. Dispatcher -----------------------------------------------------------------------------------------


def test_dispatch_due_beruecksichtigt_nur_faellige_wartende_operationen(seeded, testhandler, monkeypatch):
    aufrufe: list[str] = []
    testhandler(KIND_A, lambda op: aufrufe.append(op.op_key) or {})
    faellig, _ = _enqueue(key="test:faellig")
    _faellig(faellig)
    spaeter, _ = _enqueue(key="test:spaeter", delay_seconds=3600)
    erledigt, _ = _enqueue(key="test:erledigt")
    SyncOperation.objects.filter(pk=erledigt.pk).update(status=OperationStatus.DONE)
    laufend, _ = _enqueue(key="test:laufend")
    SyncOperation.objects.filter(pk=laufend.pk).update(status=OperationStatus.RUNNING)

    # Testmodus (JOB_DISPATCH none): der Dispatcher zaehlt die faelligen Operationen, versendet aber nichts an
    # Celery und fuehrt auch nichts lokal aus; dafuer gibt es run_pending
    assert settings.OBJEKTAKTE["JOB_DISPATCH"] == "none"
    assert operations.dispatch_due() == 1
    faellig.refresh_from_db()
    assert faellig.status == OperationStatus.PENDING and aufrufe == []

    # Betriebsmodus: genau die faellige Operation geht an den Celery-Task in der Queue io
    from apps.sync import tasks

    versendet: list[tuple] = []
    monkeypatch.setitem(settings.OBJEKTAKTE, "JOB_DISPATCH", "celery")
    monkeypatch.setattr(
        tasks.run_operation_task,
        "apply_async",
        lambda args=None, countdown=None, queue=None, **kw: versendet.append((tuple(args), countdown, queue)),
    )
    assert operations.dispatch_due() == 1
    assert versendet == [((faellig.pk,), None, "io")]
    # Begrenzung: mit limit 0 wird nichts versendet
    assert operations.dispatch_due(limit=0) == 0 and len(versendet) == 1
    # wird die Wartezeit faellig, kommt die zurueckgestellte Operation dazu (Reihenfolge nach Prioritaet, ID)
    _faellig(spaeter)
    assert operations.dispatch_due() == 2
    assert [v[0][0] for v in versendet[1:]] == [faellig.pk, spaeter.pk]
    # Einreihen im Betriebsmodus versendet sofort mit der Wartezeit als countdown
    neu, _ = _enqueue(key="test:neu", delay_seconds=45)
    assert versendet[-1] == ((neu.pk,), 45, "io")


# --- 5. Verwerfen und erneut anstossen ---------------------------------------------------------------------


def test_cancel_verwirft_und_protokolliert(seeded, objekt, admin_user, testhandler):
    aufrufe: list[int] = []
    testhandler(KIND_A, lambda op: aufrufe.append(op.pk) or {})
    doc = _dokument(objekt)
    op, _ = _enqueue(key="test:cancel", document=doc)
    assert operations.cancel(op, user=admin_user, reason="nicht mehr erforderlich") is True
    op.refresh_from_db()
    assert op.status == OperationStatus.CANCELLED and op.finished_at is not None
    assert op.blocked_reason == "nicht mehr erforderlich"
    ereignis = AuditEvent.objects.get(action="sync.operation_cancelled", entity_id=op.pk)
    assert ereignis.entity_type == "sync_operation" and ereignis.object_id == objekt.pk
    assert ereignis.user_id == admin_user.pk and ereignis.actor_type == "user"
    assert ereignis.before_state == {"status": "pending"}
    assert ereignis.after_state == {"status": "cancelled", "reason": "nicht mehr erforderlich"}
    assert operations.run_pending() == [] and aufrufe == []

    # blockiert und fehlgeschlagen lassen sich verwerfen, ohne Begruendung bleibt der Blockadegrund stehen
    blockiert, _ = _enqueue(key="test:blockiert")
    SyncOperation.objects.filter(pk=blockiert.pk).update(
        status=OperationStatus.BLOCKED, blocked_reason="kein Zugriff"
    )
    blockiert.refresh_from_db()
    assert operations.cancel(blockiert, user=admin_user) is True
    blockiert.refresh_from_db()
    assert blockiert.status == OperationStatus.CANCELLED and blockiert.blocked_reason == "kein Zugriff"

    # erledigte und laufende Operationen werden nicht verworfen, dazu gibt es auch kein Audit-Ereignis
    fertig, _ = _enqueue(key="test:fertig")
    SyncOperation.objects.filter(pk=fertig.pk).update(status=OperationStatus.DONE)
    fertig.refresh_from_db()
    assert operations.cancel(fertig, user=admin_user, reason="zu spät") is False
    fertig.refresh_from_db()
    assert fertig.status == OperationStatus.DONE and fertig.blocked_reason is None
    laufend, _ = _enqueue(key="test:laufend")
    SyncOperation.objects.filter(pk=laufend.pk).update(status=OperationStatus.RUNNING, locked_by="worker")
    laufend.refresh_from_db()
    assert operations.cancel(laufend, user=admin_user, reason="zu spät") is False
    laufend.refresh_from_db()
    assert laufend.status == OperationStatus.RUNNING and laufend.locked_by == "worker"
    assert not AuditEvent.objects.filter(
        action="sync.operation_cancelled", entity_id__in=[fertig.pk, laufend.pk]
    ).exists()
    assert AuditEvent.objects.filter(action="sync.operation_cancelled").count() == 2


def test_retry_now_setzt_fehlgeschlagene_operation_zurueck(seeded, objekt, admin_user, testhandler):
    aufrufe: list[int] = []
    testhandler(KIND_A, lambda op: aufrufe.append(op.pk) or {"erneut": True})
    doc = _dokument(objekt)
    op, _ = _enqueue(key="test:retry", document=doc)
    SyncOperation.objects.filter(pk=op.pk).update(
        status=OperationStatus.FAILED,
        attempt_count=5,
        last_error="Paperless nicht erreichbar",
        blocked_reason="alt",
        locked_by="worker",
        locked_at=timezone.now(),
        next_attempt_at=timezone.now() + timedelta(hours=1),
        finished_at=timezone.now(),
    )
    op.refresh_from_db()
    assert operations.run_pending() == []
    assert operations.retry_now(op, user=admin_user) is True
    op.refresh_from_db()
    assert op.status == OperationStatus.PENDING and op.attempt_count == 0
    # sofort faellig: die Wartezeit ist auf den Zeitpunkt des Anstosses gesetzt
    assert op.next_attempt_at is not None and op.next_attempt_at <= timezone.now()
    assert op.blocked_reason is None and op.locked_by is None and op.locked_at is None
    ereignis = AuditEvent.objects.get(action="sync.operation_retry", entity_id=op.pk)
    assert ereignis.entity_type == "sync_operation" and ereignis.object_id == objekt.pk
    assert ereignis.user_id == admin_user.pk
    # laeuft beim naechsten Durchgang und zaehlt von vorn
    assert len(operations.run_pending()) == 1 and aufrufe == [op.pk]
    op.refresh_from_db()
    assert op.status == OperationStatus.DONE and op.attempt_count == 1 and op.result == {"erneut": True}
    assert op.last_error is None


def test_retry_now_laesst_laufende_operation_unangetastet(seeded, admin_user, testhandler):
    aufrufe: list[str] = []
    testhandler(KIND_A, lambda op: aufrufe.append(op.op_key) or {})
    laufend, _ = _enqueue(key="test:laufend")
    jetzt = timezone.now()
    SyncOperation.objects.filter(pk=laufend.pk).update(
        status=OperationStatus.RUNNING,
        locked_by="aktiver-worker",
        locked_at=jetzt,
        heartbeat_at=jetzt,
        attempt_count=1,
    )
    laufend.refresh_from_db()
    # ein erneuter Anstoss waehrend der Ausfuehrung liefe parallel ein zweites Mal: kein Zuruecksetzen, kein Audit
    assert operations.retry_now(laufend, user=admin_user) is False
    laufend.refresh_from_db()
    assert laufend.status == OperationStatus.RUNNING and laufend.locked_by == "aktiver-worker"
    assert laufend.attempt_count == 1 and laufend.locked_at is not None
    assert operations.run_pending() == [] and aufrufe == []
    assert not AuditEvent.objects.filter(action="sync.operation_retry").exists()
    # erledigte Operation: ein erzwungener zweiter Lauf ist erlaubt, der Handler prueft selbst, ob noch etwas zu
    # tun ist
    fertig, _ = _enqueue(key="test:fertig")
    SyncOperation.objects.filter(pk=fertig.pk).update(status=OperationStatus.DONE, attempt_count=1)
    fertig.refresh_from_db()
    assert operations.retry_now(fertig, user=admin_user) is True
    assert len(operations.run_pending()) == 1 and aufrufe == ["test:fertig"]
    fertig.refresh_from_db()
    assert fertig.status == OperationStatus.DONE and fertig.attempt_count == 1
    assert AuditEvent.objects.filter(action="sync.operation_retry", entity_id=fertig.pk).count() == 1


# --- 6. Zusammenfassung ------------------------------------------------------------------------------------


def test_summary_zaehlt_je_status(seeded):
    assert operations.summary() == {"counts": {}, "queue": 0, "failed": 0, "oldest_pending_at": None}
    verteilung = {
        OperationStatus.PENDING: 2,
        OperationStatus.RUNNING: 1,
        OperationStatus.DONE: 3,
        OperationStatus.FAILED: 1,
        OperationStatus.BLOCKED: 2,
        OperationStatus.CANCELLED: 1,
        OperationStatus.SKIPPED: 1,
    }
    aelteste = None
    for status, anzahl in verteilung.items():
        for i in range(anzahl):
            op, _ = _enqueue(key=f"test:{status}:{i}")
            if status != OperationStatus.PENDING:
                SyncOperation.objects.filter(pk=op.pk).update(status=status)
            elif aelteste is None:
                aelteste = SyncOperation.objects.get(pk=op.pk).created_at
    zusammenfassung = operations.summary()
    assert zusammenfassung["counts"] == {str(k): v for k, v in verteilung.items()}
    assert zusammenfassung["queue"] == 3  # wartend und laufend
    assert zusammenfassung["failed"] == 3  # fehlgeschlagen und blockiert
    assert zusammenfassung["oldest_pending_at"] == aelteste


# --- 7. Prioritaet -----------------------------------------------------------------------------------------


def test_run_pending_fuehrt_hohe_prioritaet_zuerst_aus(seeded, testhandler):
    reihenfolge: list[str] = []
    testhandler(KIND_A, lambda op: reihenfolge.append(op.op_key) or {})
    testhandler(KIND_B, lambda op: reihenfolge.append(op.op_key) or {})
    _enqueue(key="test:p100-1", priority=100)
    _enqueue(kind=KIND_B, key="test:p50", priority=50)
    _enqueue(key="test:p100-2", priority=100)
    _enqueue(kind=KIND_B, key="test:p10", priority=10)
    _enqueue(key="test:p50-2", priority=50)
    ergebnisse = operations.run_pending()
    assert len(ergebnisse) == 5 and all(e.get("done") for e in ergebnisse)
    assert reihenfolge == ["test:p10", "test:p50", "test:p50-2", "test:p100-1", "test:p100-2"]
    assert not SyncOperation.objects.exclude(status=OperationStatus.DONE).exists()
    # Begrenzung des lokalen Runners
    _enqueue(key="test:a1")
    _enqueue(key="test:a2")
    assert len(operations.run_pending(limit=1)) == 1
    assert SyncOperation.objects.filter(status=OperationStatus.PENDING).count() == 1


# --- 8. Beat-Task ------------------------------------------------------------------------------------------


def test_dispatch_due_task_gibt_freigaben_und_versand_zurueck(seeded, testhandler):
    from apps.sync.tasks import dispatch_due_task

    testhandler(KIND_A, lambda op: {})
    assert dispatch_due_task() == {"released": 0, "dispatched": 0}
    faellig, _ = _enqueue(key="test:faellig")
    _faellig(faellig)
    haengend, _ = _enqueue(key="test:haengend")
    SyncOperation.objects.filter(pk=haengend.pk).update(
        status=OperationStatus.RUNNING,
        locked_by="verlorener-worker",
        locked_at=timezone.now() - timedelta(hours=1),
        heartbeat_at=timezone.now() - timedelta(hours=1),
    )
    spaeter, _ = _enqueue(key="test:spaeter", delay_seconds=3600)
    ergebnis = dispatch_due_task()
    assert ergebnis == {"released": 1, "dispatched": 2}
    haengend.refresh_from_db()
    assert haengend.status == OperationStatus.PENDING and haengend.locked_by is None
    spaeter.refresh_from_db()
    assert spaeter.status == OperationStatus.PENDING and spaeter.next_attempt_at > timezone.now()
    assert dispatch_due_task.name == "sync.dispatch_due"
