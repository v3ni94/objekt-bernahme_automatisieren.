"""Konfigurationscache: die Invalidierung darf ein nicht erreichbares oder volles Cache-Backend nicht zum Abbruch
machen (14.09.2026: app-seed im Deployment brach mit Redis OutOfMemoryError ab)."""

from __future__ import annotations

import logging

from apps.config import store


def test_invalidate_warnt_bei_cache_fehler_statt_abzubrechen(monkeypatch, caplog):
    def _voll(*args, **kwargs):
        raise RuntimeError("command not allowed when used memory > 'maxmemory'.")

    monkeypatch.setattr(store.cache, "incr", _voll)
    monkeypatch.setattr(store.cache, "set", _voll)
    with caplog.at_level(logging.WARNING, logger="apps.config.store"):
        store.invalidate()
    assert any("nicht invalidiert" in r.getMessage() for r in caplog.records)

    # ValueError (Schluessel fehlt) setzt die Version neu, auch das darf am Backend scheitern
    def _fehlt(*args, **kwargs):
        raise ValueError("kein Schluessel")

    monkeypatch.setattr(store.cache, "incr", _fehlt)
    store.invalidate()
