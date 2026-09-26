"""Ausgehende Webhooks an das CRM (Schnittstellenvertrag M29 Stufe 3): document.filed und object.taken_over.

Abschaltbar: ohne CRM_WEBHOOK_URL oder ohne CRM_WEBHOOK_SECRET wird nichts eingereiht. Der Body wird beim
Ausloesen einmal als JSON erzeugt und unveraendert versendet; die Signatur ist HMAC-SHA256 ueber genau diese Bytes
(Kopfzeile X-Objektakte-Signature: sha256=<hex>). Versand nach dem Commit ueber Celery (Queue io, 3 Wiederholungen
mit Backoff); jeder Fehler beim Einreihen wird protokolliert und blockiert die Ablage nie.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

EVENT_DOCUMENT_FILED = "document.filed"
EVENT_OBJECT_TAKEN_OVER = "object.taken_over"
SIGNATURE_HEADER = "X-Objektakte-Signature"
EVENT_HEADER = "X-Objektakte-Event"
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class DeliveryError(Exception):
    """Versand fehlgeschlagen; retryable sagt, ob eine Wiederholung sinnvoll ist."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    secret: str
    timeout_s: int

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.secret)


def config() -> WebhookConfig:
    return WebhookConfig(
        url=(getattr(settings, "CRM_WEBHOOK_URL", "") or "").strip(),
        secret=(getattr(settings, "CRM_WEBHOOK_SECRET", "") or "").strip(),
        timeout_s=int(getattr(settings, "CRM_WEBHOOK_TIMEOUT_S", 10) or 10),
    )


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def build_body(event: str, object_number: str, document: dict | None = None) -> str:
    from apps.crm_api.data import iso

    payload = {
        "event": event,
        "occurred_at": iso(timezone.now()),
        "object_number": object_number,
        "document": document,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _relevant(obj) -> bool:
    return obj is not None and not obj.is_system_inbox and not obj.is_test


def _enqueue(event: str, body: str) -> None:
    from apps.crm_api.tasks import send_webhook

    def _send() -> None:
        try:
            send_webhook.apply_async(args=[body, event], queue="io")
        except Exception:  # Broker nicht erreichbar: Ablage bleibt gueltig, das CRM gleicht ueber die API ab
            logger.exception("CRM-Webhook %s konnte nicht eingereiht werden", event)

    transaction.on_commit(_send)


def document_filed(doc) -> bool:
    """Nach der endgueltigen Ablage in Drive (Status filed). Rueckgabe True, wenn eingereiht."""
    try:
        if not config().enabled or doc.status != "filed" or not _relevant(doc.object):
            return False
        from apps.crm_api.data import document_row

        _enqueue(
            EVENT_DOCUMENT_FILED,
            build_body(EVENT_DOCUMENT_FILED, doc.object.object_number, document_row(doc)),
        )
        return True
    except Exception:  # der Webhook darf die Ablage nie scheitern lassen
        logger.exception(
            "CRM-Webhook document.filed fuer Dokument %s nicht ausloesbar", getattr(doc, "pk", None)
        )
        return False


def object_taken_over(obj) -> bool:
    """Wenn die Uebernahme abgeschlossen ist (Objektstatus wechselt von neu oder in Uebernahme auf aktiv)."""
    try:
        if not config().enabled or not _relevant(obj):
            return False
        _enqueue(EVENT_OBJECT_TAKEN_OVER, build_body(EVENT_OBJECT_TAKEN_OVER, obj.object_number))
        return True
    except Exception:
        logger.exception(
            "CRM-Webhook object.taken_over fuer Objekt %s nicht ausloesbar", getattr(obj, "pk", None)
        )
        return False


def deliver(body: str, event: str, cfg: WebhookConfig | None = None) -> int:
    """Ein Versandversuch. Rueckgabe HTTP-Status bei Erfolg (2xx), sonst DeliveryError."""
    import requests

    cfg = cfg or config()
    raw = body.encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        SIGNATURE_HEADER: sign(raw, cfg.secret),
        EVENT_HEADER: event,
        "User-Agent": "objektakte-webhook/1",
    }
    try:
        resp = requests.post(cfg.url, data=raw, headers=headers, timeout=cfg.timeout_s, allow_redirects=False)
    except requests.RequestException as exc:
        raise DeliveryError(f"Verbindung: {type(exc).__name__}") from exc
    if 200 <= resp.status_code < 300:
        return resp.status_code
    raise DeliveryError(f"HTTP {resp.status_code}", retryable=resp.status_code in RETRYABLE_STATUS)
