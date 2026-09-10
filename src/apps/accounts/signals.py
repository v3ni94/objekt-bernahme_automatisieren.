"""Login-Ereignisse: Fehlversuche zaehlen, Sperre setzen, Audit schreiben."""

from __future__ import annotations

import datetime as dt
import time

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from apps.audit.services import record

from .models import User


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs) -> None:
    if user.failed_login_count or user.locked_until:
        User.objects.filter(pk=user.pk).update(failed_login_count=0, locked_until=None)
    if request is not None and hasattr(request, "session"):
        request.session["objektakte_login_at"] = int(time.time())
        request.session["objektakte_last_activity"] = int(time.time())
    record("auth.login", entity_type="user", entity_id=user.pk, request=request, actor=user)


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs) -> None:
    if user is not None:
        record("auth.logout", entity_type="user", entity_id=user.pk, request=request, actor=user)


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs) -> None:
    email = (credentials.get("email") or credentials.get("username") or "").strip().lower()
    user = User.objects.filter(email=email, deleted_at__isnull=True).first() if email else None
    if user is not None:
        count = user.failed_login_count + 1
        locked_until = user.locked_until
        if count >= settings.LOGIN_MAX_ATTEMPTS:
            locked_until = timezone.now() + dt.timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            count = 0
        User.objects.filter(pk=user.pk).update(failed_login_count=count, locked_until=locked_until)
    # Kein Passwort, keine Rohdaten im Protokoll; die E-Mail ist zur Erkennung von Angriffen noetig
    record(
        "auth.login_failed",
        entity_type="user",
        entity_id=user.pk if user else None,
        request=request,
        actor=None,
        after={"email": email[:254], "locked": bool(user and user.locked_until)},
    )
