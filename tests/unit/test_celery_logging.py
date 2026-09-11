"""Worker-Logs bleiben unter der Django-Logging-Konfiguration (T11: JSON je Zeile, Maskierung).

Celery uebernimmt standardmaessig den Root-Logger und schreibt Text. Auf dem Server waren die Worker-Meldungen
deshalb kein JSON (Deployment-Test T11 vom 11.09.2026)."""

from __future__ import annotations

import logging

from django.conf import settings


def test_celery_uebernimmt_den_root_logger_nicht():
    from objektakte.celery import app

    assert settings.CELERY_WORKER_HIJACK_ROOT_LOGGER is False
    assert app.conf.worker_hijack_root_logger is False


def test_root_logger_nutzt_die_konfigurierte_ausgabe():
    root = logging.getLogger()
    names = {h.get_name() or h.__class__.__name__ for h in root.handlers}
    assert names, "kein Handler am Root-Logger"
    assert settings.LOGGING["handlers"]["console"]["formatter"] in ("json", "text")
