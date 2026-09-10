"""Nachlauf bestaetigender Entscheidungen (H 2.4 Regel 4): Listenerzeugung und Vollstaendigkeitspruefung des Objekts
werden entprellt angestossen. Bis M11 und M12 sammelt dieses Modul nur die Anforderungen (Zaehler je Objekt), damit
Tests die Entprellung pruefen koennen; die echten Jobs haengen sich hier ein."""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)
DEBOUNCE_SECONDS = 30
_handlers: list[Callable[[int, str], None]] = []


def register(handler: Callable[[int, str], None]) -> None:
    """Handler(object_id, bulk_key) wird je Objekt und Gruppe hoechstens einmal je Entprellfenster aufgerufen."""
    _handlers.append(handler)


def request_regeneration(object_id: int, *, bulk_key: str | None = None) -> bool:
    """Nach Commit: Listen und Vollstaendigkeit des Objekts neu erzeugen; Rueckgabe True, wenn neu angestossen."""
    key = f"review:regenerate:{object_id}:{bulk_key or 'single'}"
    if not cache.add(key, "1", timeout=DEBOUNCE_SECONDS):
        return False

    def _fire() -> None:
        for handler in list(_handlers):
            try:
                handler(object_id, bulk_key or "single")
            except Exception:  # Nachlauf darf die Entscheidung nicht rueckgaengig machen
                logger.exception("Nachlauf für Objekt %s fehlgeschlagen", object_id)

    transaction.on_commit(_fire)
    return True


def reset_debounce(object_id: int, bulk_key: str | None = None) -> None:
    cache.delete(f"review:regenerate:{object_id}:{bulk_key or 'single'}")
