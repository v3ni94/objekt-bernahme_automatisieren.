"""Erzwingung des zweiten Faktors und Sitzungsgrenzen (docs/architektur.md 9.2)."""

from __future__ import annotations

import time
from collections.abc import Callable

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

EXEMPT_PREFIXES = ("/konto/", "/healthz/", "/readyz/", "/static/")


class RequireMFAMiddleware:
    """Nutzer einer Pflichtrolle ohne aktiven zweiten Faktor duerfen nur die Einrichtungsseite aufrufen.

    Welche Rollen den zweiten Faktor brauchen, steht im Katalog (security.mfa_required_roles, Vorgabe
    nur admin). Alle anderen Rollen koennen ihn freiwillig unter Konto einrichten; ist er eingerichtet,
    fragt allauth ihn bei der Anmeldung ab.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith(EXEMPT_PREFIXES):
            if mfa_required_for(user):
                from allauth.mfa.utils import is_mfa_enabled

                if not is_mfa_enabled(user):
                    messages.info(
                        request, "Bitte richten Sie zuerst den zweiten Faktor (Authenticator-App) ein."
                    )
                    return redirect(reverse("mfa_activate_totp"))
        return self.get_response(request)


def mfa_required_roles() -> list[str]:
    from apps.config import store

    return list(store.get("security.mfa_required_roles") or [])


def mfa_required_for(user) -> bool:
    """Wahr, wenn die Rolle des Nutzers im Katalog als Pflichtrolle fuer den zweiten Faktor steht."""
    if user is None or getattr(user, "role_id", None) is None:
        return False
    return user.role.code in mfa_required_roles()


class SessionLimitsMiddleware:
    """Leerlaufzeit und absolute Sitzungsdauer aus .env (SESSION_IDLE_MINUTES, SESSION_ABSOLUTE_HOURS)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            now = int(time.time())
            login_at = request.session.get("objektakte_login_at")
            last = request.session.get("objektakte_last_activity", now)
            idle_limit = settings.SESSION_IDLE_MINUTES * 60
            absolute_limit = settings.SESSION_ABSOLUTE_HOURS * 3600
            expired = (now - last) > idle_limit or (
                login_at is not None and (now - login_at) > absolute_limit
            )
            if expired:
                logout(request)
                messages.info(request, "Die Sitzung ist abgelaufen. Bitte erneut anmelden.")
                return redirect(settings.LOGIN_URL)
            request.session["objektakte_last_activity"] = now
            if login_at is None:
                request.session["objektakte_login_at"] = now
        return self.get_response(request)
