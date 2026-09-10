"""Import-Jobs in der Queue io (H 6.1): Einlesen, Erkennung, Uebernahme, Protokoll. Die Datenbank bleibt Quelle der Wahrheit."""

from __future__ import annotations

from celery import shared_task

from apps.imports import services
from apps.imports.models import ImportBatch


@shared_task(name="imports.parse_batch", queue="io")
def parse_batch_task(batch_id: int, profile_code: str | None = None, sheet: str | None = None) -> dict:
    batch = ImportBatch.objects.get(pk=batch_id)
    services.parse_batch(batch, profile_code=profile_code, sheet=sheet)
    return {"batch_id": batch_id, "rows_total": batch.rows_total}


@shared_task(name="imports.normalize_batch", queue="io")
def normalize_batch_task(batch_id: int, user_id: int | None = None) -> dict:
    from apps.accounts.models import User

    batch = ImportBatch.objects.get(pk=batch_id)
    user = User.objects.filter(pk=user_id).first() if user_id else None
    services.normalize_batch(batch, user=user)
    return {"batch_id": batch_id, "rows_total": batch.rows_total, "rows_uncertain": batch.rows_uncertain}


@shared_task(name="imports.commit_rows", queue="io")
def commit_rows_task(batch_id: int, decisions: dict, user_id: int | None = None) -> dict:
    from apps.accounts.models import User

    batch = ImportBatch.objects.get(pk=batch_id)
    user = User.objects.filter(pk=user_id).first() if user_id else None
    parsed = {int(k): services.Decision(**v) for k, v in decisions.items()}
    result = services.commit_rows(batch, parsed, user=user)
    write_protocol_task.delay(batch_id)
    return result


@shared_task(name="imports.write_protocol", queue="io")
def write_protocol_task(batch_id: int) -> str:
    from apps.imports.protocol import write_protocol

    return str(write_protocol(ImportBatch.objects.get(pk=batch_id)))
