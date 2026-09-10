"""Strukturierte JSON-Logs mit Maskierung (docs/betrieb.md 6.1, docs/architektur.md 10.4).

Pflichtfelder: ts, level, logger, service, msg, request_id bzw. task_id. Personenbezogene Texte
werden vor der Ausgabe mit objektakte.masking maskiert. Volltexte von Dokumenten gehoeren nie in Logs.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from datetime import UTC, datetime

from objektakte.masking import mask_text

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_STANDARD_ATTRS = set(logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__) | {"message", "asctime"}


def _current_task_id() -> str | None:
    try:
        from celery import current_task
    except Exception:  # pragma: no cover
        return None
    task = current_task
    if task is not None and getattr(task, "request", None) is not None:
        return getattr(task.request, "id", None)
    return None


class MaskingFilter(logging.Filter):
    """Maskiert Nachricht und Argumente; greift auch fuer Text-Formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover
            message = str(record.msg)
        record.msg = mask_text(message, mode="log").text
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str | None = None) -> None:
        super().__init__()
        self.service = service or os.environ.get("SERVICE_NAME", "web")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "msg": mask_text(record.getMessage(), mode="log").text,
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        task_id = _current_task_id()
        if task_id:
            payload["task_id"] = task_id
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if isinstance(value, str):
                value = mask_text(value, mode="log").text
            payload[key] = value
        if record.exc_info:
            payload["exc"] = mask_text(self.formatException(record.exc_info), mode="log").text
        return json.dumps(payload, ensure_ascii=False, default=str)
