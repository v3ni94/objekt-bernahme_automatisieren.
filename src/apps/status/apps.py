"""Startpruefung der Schema-Version (Umsetzungsplan M1 Schritt 6): ausstehende Migrationen werden beim Start
protokolliert, der Prozess laeuft weiter und /readyz/ meldet schema.ok = false. Migrationen laufen nie beim Start."""

from __future__ import annotations

import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class StatusConfig(AppConfig):
    name = "apps.status"
    verbose_name = "Status"

    def ready(self) -> None:
        if os.environ.get("OBJEKTAKTE_SKIP_SCHEMA_CHECK"):
            return
        if not any(marker in sys.argv[0] for marker in ("gunicorn", "celery")):
            return
        try:
            from .checks import check_schema

            result = check_schema()
            if not result.get("ok"):
                logger.error(
                    "Schema nicht aktuell: %s ausstehende Migrationen; scripts/deploy.sh ausfuehren",
                    result.get("pending"),
                )
        except Exception as exc:  # Datenbank noch nicht erreichbar: readyz meldet es
            logger.warning("Schema-Pruefung beim Start nicht moeglich: %s", exc.__class__.__name__)
