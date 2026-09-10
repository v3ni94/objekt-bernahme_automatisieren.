"""Heartbeat fuer Worker und Beat: Datei fuer den Docker-Healthcheck, Redis-Schluessel fuer die Statusseite.

docs/betrieb.md 3.5 (Healthcheck-Datei /tmp/heartbeat) und docs/architektur.md 10.4 (Statusseite).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def touch_heartbeat(service: str, path: str, redis_client=None, ttl_seconds: int = 120) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).touch()
    if redis_client is not None:
        try:
            redis_client.set(f"heartbeat:{service}", str(int(time.time())), ex=ttl_seconds)
        except Exception as exc:  # Redis-Ausfall darf den Prozess nicht stoppen
            logger.warning("Heartbeat nach Redis fehlgeschlagen: %s", exc.__class__.__name__)


def start_heartbeat_thread(
    service: str, path: str | None = None, interval: int = 30, redis_client=None
) -> threading.Thread:
    path = path or os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat")

    def run() -> None:
        while True:
            touch_heartbeat(service, path, redis_client)
            time.sleep(interval)

    thread = threading.Thread(target=run, name=f"heartbeat-{service}", daemon=True)
    thread.start()
    return thread
