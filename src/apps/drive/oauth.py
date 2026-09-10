"""OAuth-Verbindung des technischen Kontos, Token-Ablage und Refresh (Fachentwurf F 2.1, 2.2; G 10; Beschluss B-16).

Parameter: access_type offline, prompt consent, include_granted_scopes false, state an die Admin-Sitzung gebunden, PKCE.
Nur das Konto DRIVE_ACCOUNT_EMAIL wird akzeptiert. Access- und Refresh-Token liegen mit TOKEN_KEY verschluesselt in
oauth_tokens (storage_mode db). Refresh serialisiert ueber Redis-Lock drive:token:refresh; invalid_grant setzt revoked.
Ablauf, Flow-Erzeugung und Userinfo-Abruf sind injizierbar, damit Tests ohne Google laufen.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.audit.services import record
from apps.drive.models import OAuthToken
from objektakte.crypto import DecryptionError, FieldCipher
from objektakte.secrets import read_secret

logger = logging.getLogger(__name__)
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 (Endpunkt, kein Geheimnis)
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
USERINFO_URI = "https://www.googleapis.com/oauth2/v3/userinfo"
SESSION_KEY = "drive_oauth"
PROVIDER = "google"
cipher = FieldCipher("token")


class OAuthConfigError(Exception):
    pass


class OAuthRejected(Exception):
    pass


def client_config() -> dict:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    secret = read_secret("GOOGLE_CLIENT_SECRET", default="")
    redirect = os.environ.get("GOOGLE_REDIRECT_URI", "")
    if not client_id or not secret or not redirect:
        raise OAuthConfigError(
            "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET (Secret) und GOOGLE_REDIRECT_URI müssen gesetzt sein"
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [redirect],
        }
    }


def account_email() -> str:
    return os.environ.get("DRIVE_ACCOUNT_EMAIL", "").strip().lower()


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def build_authorization_url(session) -> str:
    """Autorisierungs-URL nach B-16; state und PKCE-Verifier landen in der Admin-Sitzung."""
    cfg = client_config()["web"]
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce()
    session[SESSION_KEY] = {"state": state, "verifier": verifier, "started": timezone.now().isoformat()}
    from urllib.parse import urlencode

    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uris"][0],
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": account_email() or None,
    }
    return f"{AUTH_URI}?{urlencode({k: v for k, v in params.items() if v})}"


def default_token_exchange(code: str, verifier: str) -> dict:
    """Tauscht den Code gegen Tokens (Standardimplementierung ueber requests)."""
    import requests

    cfg = client_config()["web"]
    resp = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri": cfg["redirect_uris"][0],
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise OAuthRejected(f"Token-Endpunkt antwortete {resp.status_code}")
    return resp.json()


def default_userinfo(access_token: str) -> dict:
    import requests

    resp = requests.get(USERINFO_URI, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    if resp.status_code != 200:
        raise OAuthRejected(f"Userinfo antwortete {resp.status_code}")
    return resp.json()


def handle_callback(
    session,
    *,
    code: str,
    state: str,
    user=None,
    request=None,
    exchange: Callable[[str, str], dict] = default_token_exchange,
    userinfo: Callable[[str], dict] = default_userinfo,
) -> OAuthToken:
    pending = session.pop(SESSION_KEY, None) if hasattr(session, "pop") else None
    if not pending or not secrets.compare_digest(str(pending.get("state", "")), state or ""):
        record(
            "drive.authorize",
            entity_type="oauth_token",
            request=request,
            actor=user,
            after={"result": "rejected", "reason": "state_mismatch"},
        )
        raise OAuthRejected("Ungültiger state, Ablauf neu starten")
    tokens = exchange(code, pending["verifier"])
    granted = set((tokens.get("scope") or DRIVE_SCOPE).split())
    if DRIVE_SCOPE not in granted:
        record(
            "drive.authorize",
            entity_type="oauth_token",
            request=request,
            actor=user,
            after={"result": "rejected", "reason": "scope_missing", "scopes": sorted(granted)},
        )
        raise OAuthRejected("Der Drive-Bereich wurde nicht erteilt")
    info = userinfo(tokens["access_token"])
    email = (info.get("email") or "").strip().lower()
    if not email or email != account_email():
        record(
            "drive.authorize",
            entity_type="oauth_token",
            request=request,
            actor=user,
            after={"result": "rejected", "reason": "wrong_account", "account": email},
        )
        raise OAuthRejected(f"Konto {email or 'unbekannt'} ist nicht das technische Konto")
    if not tokens.get("refresh_token"):
        record(
            "drive.authorize",
            entity_type="oauth_token",
            request=request,
            actor=user,
            after={"result": "rejected", "reason": "no_refresh_token"},
        )
        raise OAuthRejected("Google hat keinen Refresh-Token ausgegeben (prompt=consent prüfen)")
    now = timezone.now()
    expires = now + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    token, _ = OAuthToken.objects.update_or_create(
        provider=PROVIDER,
        account_email=email,
        defaults={
            "scopes": " ".join(sorted(granted)),
            "storage_mode": "db",
            "access_token_encrypted": cipher.encrypt(tokens["access_token"], aad="access").encode("ascii"),
            "refresh_token_encrypted": cipher.encrypt(tokens["refresh_token"], aad="refresh").encode("ascii"),
            "key_version": cipher.version,
            "access_expires_at": expires,
            "refresh_obtained_at": now,
            "last_refresh_at": now,
            "last_refresh_status": "ok",
            "last_refresh_error": None,
            "consecutive_failures": 0,
            "status": "active",
            "created_by": user,
        },
    )
    record(
        "drive.authorize",
        entity_type="oauth_token",
        entity_id=token.pk,
        request=request,
        actor=user,
        after={"result": "ok", "account": email, "scopes": sorted(granted)},
    )
    return token


# ---------------------------------------------------------------- Zugriff und Refresh
def current_token() -> OAuthToken | None:
    return OAuthToken.objects.filter(provider=PROVIDER).order_by("-updated_at").first()


def _decrypt(blob, aad: str) -> str | None:
    if not blob:
        return None
    try:
        return cipher.decrypt(bytes(blob).decode("ascii"), aad=aad)
    except (DecryptionError, UnicodeDecodeError):
        return None


def get_credentials(token: OAuthToken | None = None):
    """google.oauth2.credentials.Credentials aus der Ablage; None ohne aktives Token."""
    from google.oauth2.credentials import Credentials

    token = token or current_token()
    if token is None or token.status != "active":
        return None
    cfg = client_config()["web"]
    refresh = _decrypt(token.refresh_token_encrypted, "refresh")
    if not refresh:
        return None
    expiry = token.access_expires_at.astimezone(UTC).replace(tzinfo=None) if token.access_expires_at else None
    return Credentials(
        token=_decrypt(token.access_token_encrypted, "access"),
        refresh_token=refresh,
        token_uri=TOKEN_URI,
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scopes=token.scopes.split(),
        expiry=expiry,
    )


def _lock():
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        return client.lock("drive:token:refresh", timeout=30, blocking_timeout=20)
    except Exception:
        return None


def persist_credentials(token: OAuthToken, creds, *, status: str = "ok") -> None:
    token.access_token_encrypted = (
        cipher.encrypt(creds.token or "", aad="access").encode("ascii")
        if creds.token
        else token.access_token_encrypted
    )
    if creds.expiry:
        token.access_expires_at = (
            creds.expiry.replace(tzinfo=UTC) if creds.expiry.tzinfo is None else creds.expiry
        )
    token.last_refresh_at = timezone.now()
    token.last_refresh_status = status
    token.last_refresh_error = None
    token.consecutive_failures = 0
    token.status = "active"
    token.save()


def refresh_access_token(
    *, force: bool = False, reason: str = "scheduled", refresher: Callable | None = None, actor=None
) -> dict:
    """Erneuert den Access-Token; force erneuert auch bei gueltigem Token (taeglicher Nachweis, T9)."""
    token = current_token()
    if token is None:
        return {"ok": False, "status": "missing"}
    if token.status == "revoked":
        return {"ok": False, "status": "revoked"}
    if (
        not force
        and token.access_expires_at
        and token.access_expires_at > timezone.now() + timedelta(minutes=5)
    ):
        return {"ok": True, "status": "valid", "expires_at": token.access_expires_at.isoformat()}
    lock = _lock()
    acquired = lock.acquire() if lock is not None else True
    try:
        creds = get_credentials(token)
        if creds is None:
            return {"ok": False, "status": "no_credentials"}
        try:
            if refresher is not None:
                refresher(creds)
            else:
                from google.auth.transport.requests import Request

                creds.refresh(Request())
        except Exception as exc:
            message = str(exc)
            token.consecutive_failures += 1
            token.last_refresh_status = "failed"
            token.last_refresh_error = message[:500]
            if "invalid_grant" in message:
                token.status = "revoked"
            token.save(
                update_fields=[
                    "consecutive_failures",
                    "last_refresh_status",
                    "last_refresh_error",
                    "status",
                    "updated_at",
                ]
            )
            record(
                "drive.token_refresh",
                entity_type="oauth_token",
                entity_id=token.pk,
                actor=actor,
                actor_type=None if actor else "system",
                after={
                    "result": "failed",
                    "reason": reason,
                    "error_class": type(exc).__name__,
                    "revoked": token.status == "revoked",
                },
            )
            return {"ok": False, "status": token.status, "error": type(exc).__name__}
        persist_credentials(token, creds)
        record(
            "drive.token_refresh",
            entity_type="oauth_token",
            entity_id=token.pk,
            actor=actor,
            actor_type=None if actor else "system",
            after={
                "result": "ok",
                "reason": reason,
                "forced": force,
                "expires_at": token.access_expires_at.isoformat() if token.access_expires_at else None,
            },
        )
        return {
            "ok": True,
            "status": "refreshed",
            "expires_at": token.access_expires_at.isoformat() if token.access_expires_at else None,
        }
    finally:
        if lock is not None and acquired:
            try:
                lock.release()
            except Exception:  # Lock bereits abgelaufen
                logger.debug("Refresh-Lock konnte nicht freigegeben werden")


def token_status() -> dict:
    """Anzeige fuer Statusseite und readyz (F 2.2)."""
    token = current_token()
    if token is None:
        return {"ok": None, "status": "not_connected", "text": "nicht verbunden"}
    days = (timezone.now() - token.refresh_obtained_at).days if token.refresh_obtained_at else None
    return {
        "ok": token.status == "active" and token.consecutive_failures == 0,
        "status": token.status,
        "account": token.account_email,
        "scopes": token.scopes,
        "last_refresh_at": token.last_refresh_at,
        "last_refresh_status": token.last_refresh_status,
        "last_refresh_error": token.last_refresh_error,
        "consecutive_failures": token.consecutive_failures,
        "authorized_at": token.refresh_obtained_at,
        "days_since_authorization": days,
        "access_expires_at": token.access_expires_at,
        "warning": token.consecutive_failures > 0,
        "text": {"active": "aktiv", "expired": "abgelaufen", "revoked": "widerrufen"}.get(
            token.status, token.status
        ),
    }


def get_adapter():
    """GoogleDriveAdapter mit Backoff und Ratenbegrenzung aus app_settings; None ohne Verbindung."""
    from apps.config import store
    from apps.drive.backoff import BackoffConfig
    from apps.drive.google_adapter import GoogleDriveAdapter, build_service

    token = current_token()
    creds = get_credentials(token)
    if creds is None:
        return None

    def on_auth_error():
        refresh_access_token(force=True, reason="401")

    service = build_service(creds)
    return GoogleDriveAdapter(
        service,
        drive_id=store.get("drive.root_drive_id"),
        backoff=BackoffConfig.from_setting(store.get("drive.backoff")),
        rate_per_s=float(store.get("drive.max_requests_per_second", 5)),
        on_auth_error=on_auth_error,
        chunk_bytes=int(store.get("drive.upload_chunk_bytes", 8 * 1024 * 1024)),
        resumable_threshold=int(store.get("drive.resumable_threshold_bytes", 5 * 1024 * 1024)),
    )


def oauth_proof_rows(days: int = 8) -> list[dict]:
    """Nachweiskette T9: drive.token_refresh und drive.authorize der letzten Tage (ohne Chiffrate)."""
    from apps.audit.models import AuditEvent

    since = timezone.now() - timedelta(days=days + 1)
    rows = []
    for e in AuditEvent.objects.filter(
        action__in=["drive.token_refresh", "drive.authorize"], occurred_at__gte=since
    ).order_by("occurred_at"):
        rows.append(
            {
                "occurred_at": e.occurred_at.strftime("%d.%m.%Y %H:%M"),
                "action": e.action,
                "result": (e.after_state or {}).get("result"),
                "reason": (e.after_state or {}).get("reason"),
            }
        )
    return rows


def days_without_reauthorization() -> int | None:
    token = current_token()
    if token is None or token.refresh_obtained_at is None:
        return None
    return (timezone.now() - token.refresh_obtained_at).days


def _now_utc() -> datetime:
    return datetime.now(UTC)
