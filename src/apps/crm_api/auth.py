"""Bearer-Token mit Scopes fuer die CRM-Schnittstelle: fehlender oder falscher Token 401, fehlender Scope 403,
lesend nur GET (Schnittstellenvertrag M29 Stufe 3); der Upload (documents:write) nimmt POST an."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.audit.services import record
from apps.crm_api.models import CrmApiToken

# Kennung vor dem Zufallsteil, damit ein Fund in Logs oder Dateien als Token erkennbar ist (kein Geheimnis)
TOKEN_PREFIX = "oak_"  # noqa: S105
LAST_USED_RESOLUTION = timedelta(seconds=60)  # last_used_at hoechstens einmal je Minute schreiben


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def json_response(data: dict, status: int = 200) -> JsonResponse:
    return JsonResponse(
        data,
        status=status,
        json_dumps_params={"ensure_ascii": False},
        content_type="application/json; charset=utf-8",
    )


def error(status: int, message: str) -> JsonResponse:
    resp = json_response({"error": message}, status=status)
    if status == 401:
        resp["WWW-Authenticate"] = 'Bearer realm="objektakte"'
    return resp


def _bearer(request) -> str | None:
    header = request.META.get("HTTP_AUTHORIZATION") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def authenticate(request) -> CrmApiToken | None:
    raw = _bearer(request)
    if raw is None:
        return None
    token = CrmApiToken.objects.filter(token_hash=hash_token(raw), is_active=True).first()
    if token is None:
        return None
    now = timezone.now()
    if token.last_used_at is None or now - token.last_used_at >= LAST_USED_RESOLUTION:
        # Abfrage ohne save(), damit updated_at nicht bei jedem Abruf wandert
        CrmApiToken.objects.filter(pk=token.pk).filter(
            Q(last_used_at__isnull=True) | Q(last_used_at__lt=now - LAST_USED_RESOLUTION)
        ).update(last_used_at=now)
        token.last_used_at = now
    return token


def _denied(request, reason: str, token: CrmApiToken | None = None) -> None:
    record(
        "auth.denied",
        entity_type="crm_api",
        entity_id=token.pk if token is not None else None,
        request=request,
        after={"path": request.path, "reason": reason},
    )


def require_scope(scope: str, methods: tuple[str, ...] = ("GET", "HEAD")):
    """Dekorator fuer die Endpunkte: erlaubte Methode, gueltiger Token, Scope vorhanden."""
    allow = ", ".join(m for m in methods if m != "HEAD")

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                resp = error(405, f"nur {allow}")
                resp["Allow"] = allow
                return resp
            token = authenticate(request)
            if token is None:
                _denied(request, "token")
                return error(401, "nicht autorisiert")
            if not token.has_scope(scope):
                _denied(request, f"scope {scope}", token)
                return error(403, f"Scope {scope} fehlt")
            request.crm_token = token
            return view(request, *args, **kwargs)

        # Sitzungslose Schnittstelle mit Bearer-Token (kein Cookie): die CSRF-Pruefung entfaellt, damit andere
        # Methoden mit 405 statt mit der CSRF-Fehlerseite beantwortet werden
        return csrf_exempt(wrapped)

    return decorator
