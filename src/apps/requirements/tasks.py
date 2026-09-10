"""Hintergrundjobs der Vollstaendigkeitspruefung (H 3.5): Bewertung je Objekt nach Verarbeitungslauf, Review-
Entscheidung (entprellt), Import-Uebernahme, Stammdatenaenderung, manuell und naechtlich fuer Objekte in Uebernahme."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="requirements.evaluate_object", queue="io")
def evaluate_object_task(object_id: int, trigger: str = "auto") -> dict:
    from apps.objects.models import ManagedObject
    from apps.requirements.engine import evaluate_object

    obj = ManagedObject.objects.filter(pk=object_id, deleted_at__isnull=True).first()
    if obj is None:
        return {"skipped": "object_missing"}
    return evaluate_object(obj, trigger=trigger)


@shared_task(name="requirements.evaluate_nightly", queue="io")
def evaluate_nightly() -> dict:
    from apps.objects.models import ManagedObject

    count = 0
    for obj in ManagedObject.active.filter(status="takeover"):
        try:
            evaluate_object_task(obj.pk, "nightly")
            count += 1
        except Exception:  # ein Objekt darf den Lauf der anderen nicht verhindern
            logger.exception("Nächtliche Vollständigkeitsprüfung für Objekt %s fehlgeschlagen", obj.pk)
    return {"objects": count}


def trigger_evaluation(object_id: int, trigger: str) -> None:
    """Bewertung anstossen, ohne den Aufrufer bei fehlendem Broker scheitern zu lassen."""
    try:
        evaluate_object_task.delay(object_id, trigger)
    except Exception:  # Broker nicht erreichbar: der naechste Auslöser holt die Bewertung nach
        logger.exception("Vollständigkeitsprüfung für Objekt %s konnte nicht angestoßen werden", object_id)
