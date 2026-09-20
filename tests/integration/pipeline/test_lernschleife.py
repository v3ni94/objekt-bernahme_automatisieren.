"""Harte Regeltreffer ohne Pruefbedarf werden Trainingsbeispiel fuer Stufe 2 (E 3.4 Nr. 1, angebunden 20.09.2026):
vorher blieb der lokale Klassifikator trotz Tausender Ablagen in der Kaltstartphase."""

from __future__ import annotations

import pytest

from apps.documents import ingest
from apps.documents.models import LabelSource, TrainingSample

from .conftest import page_lines

pytestmark = pytest.mark.django_db


def test_harter_regeltreffer_wird_trainingsbeispiel(objekt, stammdaten, pdf_factory, run_all):
    text = page_lines(
        "V",
        1,
        1,
        extra=["Protokoll der Eigentümerversammlung vom 20.05.2026", "Wirtschaftsjahr 2026", "TOP 7 bauliche Veränderung"],
    )
    pdf = pdf_factory("protokoll.pdf", [text])
    doc, _ = ingest.ingest_upload(objekt, filename="protokoll.pdf", data=pdf.read_bytes())
    run_all(objekt)
    doc.refresh_from_db()
    assert doc.category_id == "02" and doc.final_decided_by == "stage1", (doc.category_id, doc.final_decided_by)
    assert doc.status == "filed", doc.status  # ohne Pruefbedarf abgelegt, sonst wuerde nicht gelernt
    samples = list(TrainingSample.objects.filter(document=doc))
    assert len(samples) == 1
    sample = samples[0]
    assert sample.label_source == LabelSource.RULES_HIGH_CONFIDENCE
    assert sample.label_category_id == "02" and float(sample.weight) > 0
    # zweiter Lauf derselben Entscheidung legt kein zweites Beispiel an
    run_all(objekt)
    assert TrainingSample.objects.filter(document=doc).count() == 1
