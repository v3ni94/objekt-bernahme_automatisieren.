"""Listenerzeugung als Hintergrundjob in der Queue lists (H 5.6 Nr. 3)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="lists.generate", queue="lists", bind=True, max_retries=5)
def generate_lists_task(self, object_id: int, trigger: str = "run", user_id: int | None = None) -> dict:
    from apps.accounts.models import User
    from apps.drive import oauth
    from apps.lists.services import ListError, generate_lists
    from apps.objects.models import ManagedObject

    obj = ManagedObject.objects.filter(pk=object_id, deleted_at__isnull=True).first()
    if obj is None:
        return {"skipped": "object_missing"}
    user = User.objects.filter(pk=user_id).first() if user_id else None
    try:
        drive = oauth.get_adapter()
    except Exception:  # ohne Verbindung bleiben die Dateien lokal
        logger.exception("Keine Drive-Verbindung für die Listen von Objekt %s", object_id)
        drive = None
    try:
        results = generate_lists(
            obj, trigger=trigger, user=user, drive=drive, publish_files=drive is not None
        )
    except ListError as exc:
        # Sperre aktiv: spaeter erneut versuchen (Backoff F 2.5)
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1)) from exc
    return {r.list_type: r.status for r in results}
