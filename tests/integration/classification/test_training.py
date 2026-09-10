"""Kaltstart und Training (E 3.4, 3.5, B-27): ohne Trainingsdaten liefert Stufe 2 keine Konfidenz; synthetische
Beispiele plus force ergeben ein aktives Modell mit Artefakt; Toleranzregel und Rollback; Statuszeile."""

from __future__ import annotations

import pytest

from apps.classification import stage2, training
from apps.classification.context import DocContext
from apps.documents.models import ClassifierModel

pytestmark = pytest.mark.django_db


def _ctx(text: str) -> DocContext:
    return DocContext(
        document_id=None,
        object_id=None,
        management_type="weg",
        filename="x.pdf",
        folder_code=None,
        text=text,
        head=text,
        page_count=1,
        pages={1: text},
    )


def test_kaltstart_ohne_modell(seeded, data_dir):
    result = stage2.predict(_ctx("Einzelabrechnung 2025 für Einheit WE03"))
    assert result.cold_start and result.top is None
    status = training.cold_start_status()
    assert status["cold_start"] and status["active_version"] is None
    ready, present = training.class_readiness([])
    assert not ready and present["05"] == 0


def test_training_mit_synthetischen_beispielen(seeded, data_dir):
    samples = training.collect_samples()
    assert samples and all(s.source == "seed" for s in samples)
    row = training.train(samples, force=True, notes="Test")
    assert (
        row is not None and row.is_active and (data_dir / "models" / row.version / "model_a.joblib").exists()
    )
    assert row.metrics["macro_f1"] is not None and row.metrics["macro_f1"] > 0.6
    result = stage2.predict(
        _ctx(
            "Einzelabrechnung 2025 für Einheit WE03, Eigentümer Mustermann, Abrechnungsergebnis Nachzahlung 312,40 EUR"
        )
    )
    assert not result.cold_start and result.top == "05" and result.p > 0.3
    assert result.document_type == "einzelabrechnung" and result.subfolder == "05"
    prot = stage2.predict(_ctx("Protokoll der Eigentümerversammlung vom 20.05.2026, TOP 1 Jahresabrechnung"))
    assert prot.top == "02"
    # zweites Modell mit schlechterer Metrik wird nicht aktiviert
    worse = ClassifierModel.objects.create(
        version="worse",
        algorithm="test",
        trained_at=row.trained_at,
        samples_count=1,
        metrics={"macro_f1": 0.1},
        artifact_path=row.artifact_path,
    )
    assert training.maybe_activate(worse) is False
    assert ClassifierModel.objects.get(pk=row.pk).is_active
    # Rollback setzt explizit aktiv
    training.rollback("worse")
    assert (
        ClassifierModel.objects.get(version="worse").is_active
        and not ClassifierModel.objects.get(pk=row.pk).is_active
    )
    status = training.cold_start_status()
    assert status["active_version"] == "worse" and not status["cold_start"]
