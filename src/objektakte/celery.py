"""Celery-Anwendung (Queues ocr, classify, ai, io, lists nach Beschluss B-13)."""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import beat_init, worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "objektakte.settings.production")

app = Celery("objektakte")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(related_name="tasks")


def _redis_client():
    try:
        import redis
        from django.conf import settings

        return redis.Redis.from_url(settings.CELERY_BROKER_URL)
    except Exception:  # pragma: no cover
        return None


@worker_ready.connect
def _on_worker_ready(sender=None, **kwargs) -> None:
    from objektakte.heartbeat import start_heartbeat_thread

    hostname = getattr(sender, "hostname", "") or ""
    service = hostname.split("@")[0] or os.environ.get("SERVICE_NAME", "worker")
    start_heartbeat_thread(service, redis_client=_redis_client())


@beat_init.connect
def _on_beat_init(sender=None, **kwargs) -> None:
    from objektakte.heartbeat import start_heartbeat_thread

    start_heartbeat_thread("beat", redis_client=_redis_client())
