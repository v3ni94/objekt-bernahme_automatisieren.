"""Massenbearbeitung als Hintergrundjob (H 2.5, ANNAHME A-36) mit Fortschritt im Cache."""

from __future__ import annotations

from celery import shared_task
from django.core.cache import cache


def progress_key(bulk_key: str) -> str:
    return f"review:bulk:{bulk_key}"


@shared_task(name="review.bulk_execute", queue="io")
def bulk_execute_task(
    case_ids: list[int],
    user_id: int,
    overrides: dict | None,
    row_overrides: dict | None,
    exclude: list[int] | None,
    bulk_key: str,
) -> dict:
    from apps.accounts.models import User
    from apps.review import services

    user = User.objects.get(pk=user_id)
    cache.set(progress_key(bulk_key), {"status": "running", "total": len(case_ids)}, timeout=3600)
    result = services.bulk_execute(
        case_ids,
        user,
        overrides=overrides,
        row_overrides={int(k): v for k, v in (row_overrides or {}).items()},
        exclude=exclude,
        bulk_key=bulk_key,
    )
    cache.set(
        progress_key(bulk_key),
        {"status": "done", **{k: v for k, v in result.items() if k != "ok"}, "ok": len(result["ok"])},
        timeout=3600,
    )
    return {"bulk_key": bulk_key, "ok": len(result["ok"]), "failed": len(result["failed"])}
