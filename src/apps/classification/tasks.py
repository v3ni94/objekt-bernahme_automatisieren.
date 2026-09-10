"""Beat-Task Nachtraining (E 3.5): naechtlich, nie waehrend ein Objekt in Verarbeitung ist; Freigabe nach Toleranzregel."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="classification.retrain", queue="classify")
def retrain() -> dict:
    from apps.classification.training import retrain_if_allowed

    row = retrain_if_allowed()
    if row is None:
        return {"trained": False}
    return {"trained": True, "version": row.version, "active": row.is_active, "metrics": row.metrics}
