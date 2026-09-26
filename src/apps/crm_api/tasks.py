"""Versand der CRM-Webhooks (Queue io): ein Versuch plus hoechstens drei Wiederholungen mit wachsendem Abstand."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.crm_api import webhooks

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_S = 30  # 30 s, 2 min, 8 min


def backoff_seconds(retries: int) -> int:
    return BACKOFF_BASE_S * (4**retries)


@shared_task(name="crm_api.send_webhook", queue="io", bind=True, max_retries=MAX_RETRIES)
def send_webhook(self, body: str, event: str) -> dict:
    cfg = webhooks.config()
    if not cfg.enabled:  # zwischen Einreihen und Versand abgeschaltet
        return {"status": "disabled"}
    try:
        http_status = webhooks.deliver(body, event, cfg)
    except webhooks.DeliveryError as exc:
        if exc.retryable and self.request.retries < self.max_retries:
            logger.info("CRM-Webhook %s: %s, Wiederholung %s", event, exc, self.request.retries + 1)
            raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries)) from exc
        logger.warning("CRM-Webhook %s endgültig fehlgeschlagen: %s", event, exc)
        return {"status": "failed", "error": str(exc), "retries": self.request.retries}
    return {"status": "sent", "http_status": http_status}
