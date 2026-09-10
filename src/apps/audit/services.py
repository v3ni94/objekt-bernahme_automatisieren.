"""Schreibfunktion des Revisionsprotokolls (docs/architektur.md 9.5).

Regeln: Kein Klartext einer IBAN, kein Token, kein Geheimnis, kein Dokumentvolltext in before_state
und after_state. Sensible Schluessel werden durch Platzhalter ersetzt, Zeichenketten maskiert.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from typing import Any

from django.conf import settings
from django.utils import timezone

from objektakte.logging_json import request_id_var
from objektakte.masking import mask_text

from .models import AuditEvent

logger = logging.getLogger(__name__)

_SENSITIVE_KEY = re.compile(
    r"(iban(?!_last4|_hash|_masked)|token|password|passwd|secret|totp|api_key|apikey|refresh|credential)",
    re.IGNORECASE,
)
PLACEHOLDER = "[GESCHWAERZT]"
MAX_STRING = 2000


def scrub(value: Any) -> Any:
    """Entfernt Geheimnisse und maskiert Bank- und Ausweisdaten rekursiv."""
    if isinstance(value, dict):
        return {k: (PLACEHOLDER if _SENSITIVE_KEY.search(str(k)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return mask_text(value[:MAX_STRING], mode="log").text
    if isinstance(value, int | float | bool) or value is None:
        return value
    return scrub(str(value))


def _client_ip(request) -> bytes | None:
    if request is None:
        return None
    remote = request.META.get("REMOTE_ADDR")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    trusted = os.environ.get("TRUSTED_PROXY_CIDR", "")
    candidate = remote
    if forwarded and trusted and remote:
        try:
            if ipaddress.ip_address(remote) in ipaddress.ip_network(trusted, strict=False):
                candidate = forwarded.split(",")[0].strip()
        except ValueError:
            candidate = remote
    if not candidate:
        return None
    try:
        return ipaddress.ip_address(candidate).packed
    except ValueError:
        return None


def record(
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    *,
    object_id: int | None = None,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
    request=None,
    actor=None,
    actor_type: str | None = None,
) -> AuditEvent:
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        actor = user if (user is not None and getattr(user, "is_authenticated", False)) else None
    if actor_type is None:
        if actor is not None:
            actor_type = AuditEvent.ActorType.USER
        else:
            service = settings.OBJEKTAKTE.get("SERVICE_NAME", "web")
            actor_type = (
                AuditEvent.ActorType.WORKER if service.startswith("worker") else AuditEvent.ActorType.SYSTEM
            )
    request_id = request_id_var.get() or (
        getattr(request, "request_id", None) if request is not None else None
    )
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255] if request is not None else None
    event = AuditEvent.objects.create(
        occurred_at=timezone.now(),
        actor_type=actor_type,
        user_id=getattr(actor, "pk", None),
        user_email=getattr(actor, "email", None),
        action=action[:64],
        entity_type=entity_type[:48],
        entity_id=entity_id,
        object_id=object_id,
        before_state=scrub(before) if before is not None else None,
        after_state=scrub(after) if after is not None else None,
        reason=mask_text(reason, mode="log").text[:500] if reason else None,
        request_id=request_id,
        ip_address=_client_ip(request),
        user_agent=user_agent or None,
    )
    logger.info("audit %s %s#%s", action, entity_type, entity_id, extra={"audit_id": event.pk})
    return event
