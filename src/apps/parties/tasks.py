"""Naechtlicher Konsistenzlauf der Stammdaten (Fachentwurf D 3.4, Umsetzungsplan M2 Schritt 6).

Ueberlappende Zuordnungen derselben Eigentuemer-Einheit-Kombination werden als Review-Fall data_consistency
(Untertyp assignment_overlap) gemeldet, je Paar hoechstens ein offener Fall. Nichts wird geaendert oder geloescht.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.parties.services import find_overlaps
from apps.review.models import CaseStatus, CaseType, ReviewCase

logger = logging.getLogger(__name__)


def run_consistency_check() -> dict[str, int]:
    overlaps = find_overlaps()
    created = 0
    for a, b in overlaps:
        key = f"assignment_overlap:{min(a.pk, b.pk)}:{max(a.pk, b.pk)}"
        if ReviewCase.objects.filter(
            case_type=CaseType.DATA_CONSISTENCY,
            batch_key=key,
            status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        ).exists():
            continue
        ReviewCase.objects.create(
            object=a.unit.object,
            case_type=CaseType.DATA_CONSISTENCY,
            case_subtype="assignment_overlap",
            batch_key=key,
            priority=50,
            candidates=[
                {
                    "assignment_id": x.pk,
                    "owner_id": x.owner_id,
                    "unit_id": x.unit_id,
                    "valid_from": x.valid_from.isoformat() if x.valid_from else None,
                    "valid_to": x.valid_to.isoformat() if x.valid_to else None,
                }
                for x in (a, b)
            ],
            context={
                "unit_label": a.unit.unit_label,
                "owner": str(a.owner),
                "rule": "D 3.4 keine Überlappung je Eigentümer und Einheit",
            },
        )
        created += 1
    logger.info("Konsistenzlauf Zuordnungen: %s Überlappungen, %s neue Fälle", len(overlaps), created)
    return {"overlaps": len(overlaps), "cases_created": created}


@shared_task(name="parties.check_assignment_consistency", queue="io")
def check_assignment_consistency() -> dict[str, int]:
    return run_consistency_check()
