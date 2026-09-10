"""Alarmierung per E-Mail (Fachentwurf G 9.5, optional): nur bei ALERTS_ENABLED und SMTP-Zugang (F27). Pruefintervall
fuenf Minuten ueber beat, jede Bedingung hoechstens einmal je sechs Stunden, Entwarnung wird gemeldet. Bedingungen:
Sicherung alt oder fehlgeschlagen, Token widerrufen oder Refresh zweimal fehlgeschlagen, Worker-Heartbeat aelter als
zehn Minuten bei nicht leerer Queue, Platz unter der Reserve, Fallback-Anbieter laenger als eine Stunde aktiv, Monats-
budget zu 80 Prozent, TLS-Zertifikat unter 14 Tagen, Ordnerabgleich mit Dateianzahl-Abweichung."""

from __future__ import annotations

import logging
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.config import store
from apps.status import checks

logger = logging.getLogger(__name__)
REPEAT_SECONDS = 6 * 3600
CERT_WARN_DAYS = 14


@dataclass
class Condition:
    key: str
    active: bool
    message: str


def _backup() -> Condition:
    b = checks.backup_status()
    max_age = int(settings.OBJEKTAKTE.get("BACKUP_MAX_AGE_HOURS", 26) or 26)
    age = b.get("age_h")
    active = (b.get("status") not in (None, "ok")) or (age is not None and age > max_age) or ("error" in b)
    return Condition(
        "backup",
        active,
        f"Sicherung: Status {b.get('status', b.get('error', 'unbekannt'))}, Alter {age} h (Schwelle {max_age} h)",
    )


def _token() -> Condition:
    t = checks.oauth_status()
    status = str(t.get("status") or "")
    failures = int(t.get("failures_in_row") or t.get("consecutive_failures") or 0)
    active = status == "revoked" or failures >= 2
    return Condition("token", active, f"Google-Token: Status {status}, Fehler in Folge {failures}")


def _heartbeats() -> Condition:
    hb = checks.heartbeats()
    from apps.pipeline.models import JobStatus, ProcessingJob

    queue_nonempty = ProcessingJob.objects.filter(status=JobStatus.PENDING).exists()
    stale = [
        name
        for name, h in hb.items()
        if name in ("worker", "worker-io") and (h.get("age_s") is None or h["age_s"] > 600)
    ]
    return Condition(
        "heartbeat",
        bool(stale) and queue_nonempty,
        f"Heartbeat älter als 10 min bei offener Queue: {', '.join(stale) or 'keiner'}",
    )


def _disk() -> Condition:
    d = checks.check_disk()
    return Condition(
        "disk",
        not d["ok"],
        f"Freier Platz {d['free_gb']} GB unter Reserve {d['reserve_gb']} GB ({d['path']})",
    )


def _fallback() -> Condition:
    from apps.ai.models import AiCall

    since = timezone.now() - timedelta(hours=1)
    recent = AiCall.objects.filter(requested_at__gte=since)
    fallback = recent.filter(fallback_used=True, status="ok").exists()
    primary_ok = recent.filter(fallback_used=False, status="ok").exists()
    earliest = (
        recent.filter(fallback_used=True)
        .order_by("requested_at")
        .values_list("requested_at", flat=True)
        .first()
    )
    long_enough = earliest is not None and earliest <= timezone.now() - timedelta(hours=1)
    return Condition(
        "fallback",
        fallback and not primary_ok and long_enough,
        "Stufe 3: Fallback-Anbieter seit über einer Stunde aktiv, Primäranbieter ohne Erfolg",
    )


def _budget() -> Condition:
    from apps.ai.router import month_costs

    budgets = store.get("ai.monthly_budget_eur", {}) or {}
    costs = month_costs()
    hits = []
    for provider, budget in budgets.items():
        if budget and float(costs.get(provider, 0)) >= 0.8 * float(budget):
            hits.append(f"{provider} {costs.get(provider)} von {budget} EUR")
    return Condition("budget", bool(hits), "Monatsbudget zu 80 Prozent ausgeschöpft: " + ", ".join(hits))


def _certificate(domain: str | None = None) -> Condition:
    domain = domain or settings.APP_DOMAIN
    if not domain or domain in ("localhost", "127.0.0.1", "web"):
        return Condition("certificate", False, "Zertifikatsprüfung ohne Domain übersprungen")
    try:
        ctx = ssl.create_default_context()
        with (
            socket.create_connection((domain, 443), timeout=5) as sock,
            ctx.wrap_socket(sock, server_hostname=domain) as tls,
        ):
            cert = tls.getpeercert()
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        days = (not_after - datetime.now(UTC)).days
        return Condition(
            "certificate", days < CERT_WARN_DAYS, f"TLS-Zertifikat {domain} läuft in {days} Tagen ab"
        )
    except Exception as exc:  # Netz oder Zertifikat nicht pruefbar: melden, nicht raten
        return Condition(
            "certificate", True, f"TLS-Zertifikat {domain} nicht prüfbar ({exc.__class__.__name__})"
        )


def _reconcile_mismatch() -> Condition:
    from apps.review.models import CaseStatus, ReviewCase

    n = ReviewCase.objects.filter(
        case_type="drive_structure",
        case_subtype="rename_count_mismatch",
        status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
    ).count()
    return Condition("reconcile", n > 0, f"Ordnerabgleich: {n} Fälle mit abweichender Dateianzahl offen")


def evaluate(*, check_certificate: bool = True) -> list[Condition]:
    conditions = [_backup(), _token(), _heartbeats(), _disk(), _fallback(), _budget(), _reconcile_mismatch()]
    if check_certificate:
        conditions.append(_certificate())
    return conditions


def enabled() -> bool:
    return bool(settings.OBJEKTAKTE.get("ALERTS_ENABLED")) and bool(settings.OBJEKTAKTE.get("ALERT_EMAIL_TO"))


def send(subject: str, body: str) -> bool:
    from django.core.mail import send_mail

    to = [a.strip() for a in str(settings.OBJEKTAKTE.get("ALERT_EMAIL_TO") or "").split(",") if a.strip()]
    if not to:
        return False
    send_mail(f"[Objektakte] {subject}", body, settings.DEFAULT_FROM_EMAIL or None, to, fail_silently=False)
    return True


def check_alerts(*, check_certificate: bool = True) -> dict:
    """Bedingungen pruefen, Meldung je Bedingung hoechstens einmal je sechs Stunden, Entwarnung bei Ruecknahme."""
    conditions = evaluate(check_certificate=check_certificate)
    sent, cleared = [], []
    for c in conditions:
        state_key, sent_key = f"alerts:active:{c.key}", f"alerts:sent:{c.key}"
        was_active = bool(cache.get(state_key))
        if c.active:
            cache.set(state_key, "1", timeout=7 * 24 * 3600)
            if enabled() and cache.add(sent_key, "1", timeout=REPEAT_SECONDS):
                try:
                    send(f"Warnung {c.key}", c.message)
                    sent.append(c.key)
                except Exception:
                    logger.exception("Alarm-E-Mail für %s fehlgeschlagen", c.key)
                    cache.delete(sent_key)
            logger.warning("Alarmbedingung %s: %s", c.key, c.message)
        elif was_active:
            cache.delete(state_key)
            cache.delete(sent_key)
            if enabled():
                try:
                    send(f"Entwarnung {c.key}", c.message)
                    cleared.append(c.key)
                except Exception:
                    logger.exception("Entwarnung für %s fehlgeschlagen", c.key)
    return {
        "active": [c.key for c in conditions if c.active],
        "sent": sent,
        "cleared": cleared,
        "enabled": enabled(),
    }


@shared_task(name="status.check_alerts", queue="io")
def check_alerts_task() -> dict:
    return check_alerts()
