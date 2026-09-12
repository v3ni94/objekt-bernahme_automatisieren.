"""Operationsliste der Synchronisation (Auftrag Abschnitt 10): dauerhafte Operationen mit eindeutigem Schluessel,
Reservierung per UPDATE, begrenzte Wiederholungen mit zunehmender Wartezeit, sichtbare Fehlerwarteschlange.

Handler registrieren sich mit @handler(OperationKind.X) und erhalten die Operation; sie liefern ein Ergebnis-Dict
oder werfen Skip (nichts zu tun), Defer (spaeter erneut, kein Fehlversuch), Retry (vorübergehender Fehler,
zaehlt als Versuch) oder Block (dauerhaft nicht loesbar ohne Eingriff, sichtbar)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.sync import config
from apps.sync.models import OperationKind, OperationStatus, SyncOperation

logger = logging.getLogger(__name__)

HANDLERS: dict[str, Callable[[SyncOperation], dict | None]] = {}
BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 3600
STALE_MINUTES = 30


class OperationOutcome(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Skip(OperationOutcome):
    """Nichts zu tun (bereits erledigt oder Voraussetzung entfallen)."""


class Defer(OperationOutcome):
    """Voraussetzung fehlt noch (Verbindung, Sperre); kein Fehlversuch."""

    def __init__(self, reason: str, seconds: int = 300):
        super().__init__(reason)
        self.seconds = seconds


class Retry(OperationOutcome):
    """Voruebergehender Fehler; zaehlt als Versuch."""

    def __init__(self, reason: str, seconds: int | None = None):
        super().__init__(reason)
        self.seconds = seconds


class Block(OperationOutcome):
    """Dauerhaft nicht loesbar ohne Eingriff; bleibt sichtbar in der Fehlerliste."""


def handler(kind: str):
    def decorator(fn):
        HANDLERS[kind] = fn
        return fn

    return decorator


def op_key(kind: str, *parts) -> str:
    return ":".join([kind, *[str(p) for p in parts if p is not None]])[:200]


def enqueue(
    kind: str,
    *,
    system: str,
    key: str,
    document=None,
    link=None,
    payload: dict | None = None,
    desired_state: dict | None = None,
    source_system: str | None = None,
    source_revision: str | None = None,
    priority: int = 100,
    created_by=None,
    dispatch: bool = True,
    delay_seconds: int = 0,
) -> tuple[SyncOperation, bool]:
    """Legt die Operation idempotent an. Ein zweiter Aufruf mit gleichem Schluessel erzeugt keine zweite Zeile;
    eine abgeschlossene Operation gleichen Schluessels wird nicht wiederholt (dieselbe Quellrevision, gleiches
    Ergebnis), eine fehlgeschlagene oder verworfene wird erneut eingereiht."""
    if kind not in OperationKind.values:
        raise ValueError(f"unbekannte Operationsart {kind}")
    now = timezone.now()
    with transaction.atomic():
        op = SyncOperation.objects.select_for_update().filter(op_key=key).first()
        if op is None:
            op = SyncOperation.objects.create(
                op_key=key,
                kind=kind,
                system=system,
                document=document,
                link=link,
                payload=payload or {},
                desired_state=desired_state,
                source_system=source_system,
                source_revision=source_revision,
                priority=priority,
                max_attempts=config.operation_max_attempts(),
                next_attempt_at=now + timedelta(seconds=delay_seconds),
                created_by=created_by if getattr(created_by, "pk", None) else None,
            )
            created = True
        elif op.status in (OperationStatus.FAILED, OperationStatus.CANCELLED, OperationStatus.BLOCKED):
            op.status = OperationStatus.PENDING
            op.attempt_count = 0
            op.last_error = None
            op.blocked_reason = None
            op.payload = payload or op.payload
            op.desired_state = desired_state or op.desired_state
            op.next_attempt_at = now + timedelta(seconds=delay_seconds)
            op.save()
            created = False
        else:
            return op, False
    if dispatch:
        send(op, countdown=delay_seconds or None)
    return op, created


def send(op: SyncOperation, countdown: int | None = None) -> None:
    from django.conf import settings

    if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") != "celery":
        return
    from apps.sync.tasks import run_operation_task

    run_operation_task.apply_async(args=[op.pk], countdown=countdown, queue="io")


def reserve(op_id: int, *, worker: str = "") -> SyncOperation | None:
    now = timezone.now()
    updated = SyncOperation.objects.filter(
        Q(pk=op_id, status=OperationStatus.PENDING)
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    ).update(
        status=OperationStatus.RUNNING,
        locked_by=worker or "worker",
        locked_at=now,
        heartbeat_at=now,
        started_at=now,
    )
    if not updated:
        return None
    op = SyncOperation.objects.get(pk=op_id)
    op.attempt_count += 1
    op.save(update_fields=["attempt_count", "updated_at"])
    return op


def _finish(op: SyncOperation, status: str, **fields) -> None:
    fields.setdefault("finished_at", timezone.now())
    SyncOperation.objects.filter(pk=op.pk).update(status=status, locked_by=None, locked_at=None, **fields)


def _retry_delay(op: SyncOperation, seconds: int | None) -> int:
    if seconds:
        return min(int(seconds), BACKOFF_MAX_S)
    return min(BACKOFF_BASE_S * (2 ** max(op.attempt_count - 1, 0)), BACKOFF_MAX_S)


def run(op_id: int, *, worker: str = "") -> dict:
    """Fuehrt eine Operation aus und schreibt den Folgezustand; wird vom Celery-Task und vom Dispatcher genutzt."""
    op = reserve(op_id, worker=worker)
    if op is None:
        return {"op_id": op_id, "reserved": False}
    fn = HANDLERS.get(op.kind)
    if fn is None:
        from apps.sync import flows

        flows.load_all()
        fn = HANDLERS.get(op.kind)
    if fn is None:
        _finish(op, OperationStatus.BLOCKED, blocked_reason=f"kein Handler für {op.kind}")
        return {"op_id": op_id, "blocked": True}
    try:
        result = fn(op) or {}
    except Skip as exc:
        _finish(op, OperationStatus.SKIPPED, result={"skipped": exc.reason})
        return {"op_id": op_id, "skipped": exc.reason}
    except Defer as exc:
        SyncOperation.objects.filter(pk=op.pk).update(
            status=OperationStatus.PENDING,
            attempt_count=max(op.attempt_count - 1, 0),
            next_attempt_at=timezone.now() + timedelta(seconds=exc.seconds),
            last_error=exc.reason,
            locked_by=None,
            locked_at=None,
        )
        return {"op_id": op_id, "deferred": exc.reason}
    except Retry as exc:
        if op.attempt_count >= op.max_attempts:
            _finish(op, OperationStatus.FAILED, last_error=str(exc.reason)[:500])
            return {"op_id": op_id, "failed": True}
        SyncOperation.objects.filter(pk=op.pk).update(
            status=OperationStatus.PENDING,
            next_attempt_at=timezone.now() + timedelta(seconds=_retry_delay(op, exc.seconds)),
            last_error=str(exc.reason)[:500],
            locked_by=None,
            locked_at=None,
        )
        return {"op_id": op_id, "retry": True}
    except Block as exc:
        _finish(
            op,
            OperationStatus.BLOCKED,
            blocked_reason=str(exc.reason)[:255],
            last_error=str(exc.reason)[:500],
        )
        return {"op_id": op_id, "blocked": exc.reason}
    except Exception as exc:  # unerwarteter Fehler: sichtbar, keine stille Wiederholung ohne Grenze
        logger.exception("Synchronisationsoperation %s fehlgeschlagen", op.pk)
        if op.attempt_count >= op.max_attempts:
            _finish(op, OperationStatus.FAILED, last_error=f"{type(exc).__name__}: {exc}"[:500])
            return {"op_id": op_id, "failed": True}
        SyncOperation.objects.filter(pk=op.pk).update(
            status=OperationStatus.PENDING,
            next_attempt_at=timezone.now() + timedelta(seconds=_retry_delay(op, None)),
            last_error=f"{type(exc).__name__}: {exc}"[:500],
            locked_by=None,
            locked_at=None,
        )
        return {"op_id": op_id, "retry": True}
    _finish(op, OperationStatus.DONE, result=result, last_error=None)
    return {"op_id": op_id, "done": True, "result": result}


def dispatch_due(limit: int = 200) -> int:
    """Reiht faellige Operationen ein (Beat), damit Wiederholungen und verzoegerte Operationen ohne Celery-ETA laufen."""
    now = timezone.now()
    ids = list(
        SyncOperation.objects.filter(status=OperationStatus.PENDING)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by("priority", "id")
        .values_list("pk", flat=True)[:limit]
    )
    for pk in ids:
        send(SyncOperation(pk=pk))
    return len(ids)


def release_stale(minutes: int = STALE_MINUTES) -> int:
    """Haengende Operationen (Worker verloren) wieder freigeben."""
    limit = timezone.now() - timedelta(minutes=minutes)
    return SyncOperation.objects.filter(status=OperationStatus.RUNNING, heartbeat_at__lt=limit).update(
        status=OperationStatus.PENDING, locked_by=None, locked_at=None, next_attempt_at=timezone.now()
    )


def cancel(op: SyncOperation, *, user=None, reason: str = "") -> bool:
    """Verwirft eine wartende, blockierte oder fehlgeschlagene Operation. Laufende und abgeschlossene Operationen
    bleiben unveraendert; dann wird auch kein Audit-Ereignis geschrieben. Liefert, ob verworfen wurde."""
    from apps.audit.services import record

    before = {"status": op.status}
    updated = SyncOperation.objects.filter(
        pk=op.pk, status__in=[OperationStatus.PENDING, OperationStatus.BLOCKED, OperationStatus.FAILED]
    ).update(
        status=OperationStatus.CANCELLED,
        finished_at=timezone.now(),
        blocked_reason=(reason or op.blocked_reason),
    )
    if not updated:
        return False
    record(
        "sync.operation_cancelled",
        entity_type="sync_operation",
        entity_id=op.pk,
        object_id=op.document.object_id if op.document_id else None,
        before=before,
        after={"status": "cancelled", "reason": reason},
        actor=user,
    )
    return True


def retry_now(op: SyncOperation, *, user=None) -> bool:
    """Reiht eine Operation sofort erneut ein (Zaehler und Wartezeit zurueckgesetzt). Eine laufende Operation wird
    nicht angefasst, sonst liefe sie parallel ein zweites Mal; haengende Laeufe gibt release_stale frei. Liefert,
    ob die Operation eingereiht wurde."""
    from apps.audit.services import record

    updated = (
        SyncOperation.objects.filter(pk=op.pk)
        .exclude(status=OperationStatus.RUNNING)
        .update(
            status=OperationStatus.PENDING,
            attempt_count=0,
            next_attempt_at=timezone.now(),
            blocked_reason=None,
            locked_by=None,
            locked_at=None,
        )
    )
    if not updated:
        return False
    record(
        "sync.operation_retry",
        entity_type="sync_operation",
        entity_id=op.pk,
        object_id=op.document.object_id if op.document_id else None,
        actor=user,
    )
    send(op)
    return True


def summary() -> dict:
    from django.db.models import Count

    counts = {
        row["status"]: row["n"] for row in SyncOperation.objects.values("status").annotate(n=Count("id"))
    }
    oldest = (
        SyncOperation.objects.filter(status=OperationStatus.PENDING)
        .order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    return {
        "counts": counts,
        "queue": counts.get(OperationStatus.PENDING, 0) + counts.get(OperationStatus.RUNNING, 0),
        "failed": counts.get(OperationStatus.FAILED, 0) + counts.get(OperationStatus.BLOCKED, 0),
        "oldest_pending_at": oldest,
    }


def run_pending(limit: int = 500) -> list[dict]:
    """Lokaler Runner fuer Tests und Kommandos (JOB_DISPATCH none): fuehrt faellige Operationen der Reihe nach aus,
    bis keine mehr faellig ist oder die Grenze erreicht wurde."""
    results: list[dict] = []
    seen_rounds = 0
    while len(results) < limit:
        now = timezone.now()
        pk = (
            SyncOperation.objects.filter(status=OperationStatus.PENDING)
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
            .order_by("priority", "id")
            .values_list("pk", flat=True)
            .first()
        )
        if pk is None:
            break
        results.append(run(pk, worker="local"))
        seen_rounds += 1
    return results
