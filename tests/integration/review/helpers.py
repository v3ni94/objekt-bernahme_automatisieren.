from __future__ import annotations

from tests.integration.classification.conftest import make_document

from apps.pipeline.runs import start_run
from apps.review.models import ReviewCase

H623 = "Objekt 623 Düsseldorf, Joachimstraße 49\n"


def make_case_document(welt, run_all, entry: dict, *, number="623", source="upload", parent_id=None):
    """Dokument durch die Pipeline laufen lassen und den entstandenen Fall zurueckgeben."""
    obj = welt["objects"][number]
    doc = make_document(obj, entry, source=source, drive=welt["drive"], parent_id=parent_id)
    start_run(obj)
    run_all(obj)
    doc.refresh_from_db()
    case = ReviewCase.objects.filter(document=doc).order_by("id").first()
    return doc, case
