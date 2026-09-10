"""Kaltstart und Nachtraining (E 3.4, 3.5): training_samples aus Regel-Labels, synthetischen Beispielen, KI-Labels und
Review-Entscheidungen mit Gewichten (classification.label_weights); Scharfschaltung ab stage2_min_samples_per_class
gewichteten Beispielen je Klasse; Kreuzvalidierung mit Makro-F1; Aktivierung nur bei nicht schlechterer Makro-F1
(retrain_f1_tolerance); Rollback ueber classifier_models.is_active."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.classification.stage2 import CATEGORY_LABELS, models_dir
from apps.config import store
from apps.documents.models import ClassifierModel, Document, TrainingSample

logger = logging.getLogger(__name__)
SOURCE_WEIGHT_KEY = {
    "rules_high_confidence": "rule",
    "seed": "synthetic",
    "manual": "review",
    "review_decision": "review",
}


@dataclass
class Sample:
    text: str
    label_a: str
    label_b: str | None
    weight: float
    source: str


def label_a_for(category_code: str | None, scope: str | None = None) -> str:
    if category_code == "06" or scope == "unclear":
        return "nicht_objektbezogen"
    return category_code or "nicht_objektbezogen"


def add_rule_sample(
    doc: Document,
    text: str,
    *,
    category: str,
    subfolder: str | None,
    document_type: str | None,
    weight: float | None = None,
) -> TrainingSample | None:
    """Harte Stufe-1-Treffer werden Trainingsbeispiel mit label_source rules_high_confidence (E 3.4 Nr. 1)."""
    from apps.documents.models import DocumentSubfolder, DocumentType

    if category == "06":
        return None  # Ablagen in 06 ohne Bestaetigung werden nie trainiert (E 3.5)
    weights = store.get("classification.label_weights", {}) or {}
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if TrainingSample.objects.filter(
        document=doc, text_hash=text_hash, label_source="rules_high_confidence"
    ).exists():
        return None
    return TrainingSample.objects.create(
        document=doc,
        text_hash=text_hash,
        label_category_id=category,
        label_subfolder=DocumentSubfolder.objects.filter(category_id=category, code=subfolder).first()
        if subfolder
        else None,
        label_document_type=DocumentType.objects.filter(code=document_type).first()
        if document_type
        else None,
        label_source="rules_high_confidence",
        weight=weight if weight is not None else float(weights.get("rule", 0.6)),
    )


def collect_samples() -> list[Sample]:
    """Trainingsmenge: Texte aus dem maskierten Seitentext (erste Seiten) je Dokument bzw. aus dem synthetischen Korpus
    (features in text_hash-Spalte referenziert); Review-Labels ueberschreiben Regel- und KI-Labels desselben Dokuments."""
    from apps.classification.context import build_context
    from apps.classification.stage2 import input_text
    from apps.classification.synthetic import synthetic_samples

    samples: list[Sample] = []
    by_doc: dict[int, list[TrainingSample]] = {}
    for ts in TrainingSample.objects.filter(is_active=True).select_related(
        "document", "label_category", "label_subfolder", "label_document_type"
    ):
        if ts.document_id is None:
            continue
        by_doc.setdefault(ts.document_id, []).append(ts)
    for _doc_id, rows in by_doc.items():
        reviews = [r for r in rows if r.label_source in ("review_decision", "manual")]
        chosen = reviews or rows
        doc = chosen[0].document
        try:
            text = input_text(build_context(doc))
        except Exception:  # Dokument ohne Seiten oder geloescht
            logger.debug("Trainingsbeispiel ohne Text: Dokument %s", doc.pk, exc_info=True)
            continue
        for r in chosen:
            label_b = None
            if r.label_category_id == "05" and r.label_subfolder_id and r.label_document_type_id:
                label_b = f"05/{r.label_subfolder.code}/{r.label_document_type.code}"
            samples.append(
                Sample(text, label_a_for(r.label_category_id), label_b, float(r.weight), r.label_source)
            )
    samples.extend(synthetic_samples())
    return samples


def class_readiness(samples: list[Sample]) -> tuple[bool, dict[str, float]]:
    minimum = float(store.get("classification.stage2_min_samples_per_class", 15))
    weights: Counter = Counter()
    for s in samples:
        weights[s.label_a] += s.weight
    present = {label: round(weights.get(label, 0.0), 2) for label in CATEGORY_LABELS}
    ready = (
        all(present[label] >= minimum for label in CATEGORY_LABELS if label != "nicht_objektbezogen")
        and len([label for label in present if present[label] > 0]) >= 2
    )
    return ready, present


def _pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    features = FeatureUnion(
        [
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True, max_features=200_000
                ),
            ),
            (
                "word",
                TfidfVectorizer(
                    analyzer="word", ngram_range=(1, 2), min_df=1, sublinear_tf=True, max_features=100_000
                ),
            ),
        ]
    )
    return Pipeline(
        [("features", features), ("clf", LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced"))]
    )


def train(
    samples: list[Sample], *, version: str | None = None, force: bool = False, notes: str | None = None
) -> ClassifierModel | None:
    """Trainiert Modell A und B, bewertet per Kreuzvalidierung (Makro-F1) und legt das Artefakt an; Aktivierung nach
    Toleranzregel. Rueckgabe None, wenn die Kaltstartschwelle nicht erreicht ist und force falsch ist."""
    import joblib
    import numpy as np
    from sklearn.metrics import f1_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    ready, present = class_readiness(samples)
    if not ready and not force:
        logger.info("Kaltstart: Klassen unter der Schwelle %s", present)
        return None
    texts = [s.text for s in samples]
    labels_a = [s.label_a for s in samples]
    weights = np.array([s.weight for s in samples])
    model_a = _pipeline()
    folds = min(5, min(Counter(labels_a).values()))
    metrics: dict = {"classes": present, "samples": len(samples)}
    if folds >= 2:
        cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=7)
        pred = cross_val_predict(model_a, texts, labels_a, cv=cv, params={"clf__sample_weight": weights})
        metrics["macro_f1"] = round(float(f1_score(labels_a, pred, average="macro")), 4)
        metrics["accuracy"] = round(float(np.mean(np.array(pred) == np.array(labels_a))), 4)
    else:
        metrics["macro_f1"] = None
    model_a.fit(texts, labels_a, clf__sample_weight=weights)
    version = version or timezone.now().strftime("%Y%m%d-%H%M%S")
    target = models_dir() / version
    target.mkdir(parents=True, exist_ok=True)
    joblib.dump(model_a, target / "model_a.joblib")
    sub = [(s.text, s.label_b, s.weight) for s in samples if s.label_b]
    if len({lb for _, lb, _ in sub}) >= 2:
        model_b = _pipeline()
        model_b.fit(
            [t for t, _, _ in sub],
            [lb for _, lb, _ in sub],
            clf__sample_weight=np.array([w for _, _, w in sub]),
        )
        joblib.dump(model_b, target / "model_b.joblib")
        metrics["model_b_classes"] = len({lb for _, lb, _ in sub})
    (target / "labels.json").write_text(
        json.dumps({"a": sorted(set(labels_a)), "b": sorted({lb for _, lb, _ in sub})}, ensure_ascii=False),
        encoding="utf-8",
    )
    (target / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    row = ClassifierModel.objects.create(
        version=version,
        algorithm="tfidf_char3-5_word1-2_logreg",
        trained_at=timezone.now(),
        samples_count=len(samples),
        metrics=metrics,
        artifact_path=str(target.relative_to(models_dir())),
        notes=notes,
    )
    maybe_activate(row)
    return row


def maybe_activate(row: ClassifierModel, *, user=None, force: bool = False) -> bool:
    """Aktivierung nur, wenn Makro-F1 nicht schlechter als aktives Modell minus Toleranz (E 3.5)."""
    tolerance = float(store.get("classification.retrain_f1_tolerance", 0.01))
    active = ClassifierModel.objects.filter(is_active=True).exclude(pk=row.pk).first()
    new_f1 = (row.metrics or {}).get("macro_f1")
    old_f1 = (active.metrics or {}).get("macro_f1") if active else None
    if (
        not force
        and active is not None
        and new_f1 is not None
        and old_f1 is not None
        and new_f1 < old_f1 - tolerance
    ):
        logger.info(
            "Modell %s nicht aktiviert: Makro-F1 %s unter %s minus Toleranz", row.version, new_f1, old_f1
        )
        return False
    with transaction.atomic():
        ClassifierModel.objects.filter(is_active=True).update(is_active=False)
        row.is_active = True
        row.activated_at = timezone.now()
        row.activated_by = user
        row.save(update_fields=["is_active", "activated_at", "activated_by"])
    from apps.classification import stage2

    stage2._CACHE.clear()
    return True


def rollback(version: str, *, user=None) -> ClassifierModel:
    row = ClassifierModel.objects.get(version=version)
    maybe_activate(row, user=user, force=True)
    return row


def retrain_if_allowed(*, force: bool = False) -> ClassifierModel | None:
    """Nachtraining nie waehrend ein Objekt in Verarbeitung ist (E 3.5)."""
    from apps.pipeline.models import ProcessingRun, RunStatus

    if ProcessingRun.objects.filter(status=RunStatus.RUNNING).exists() and not force:
        logger.info("Nachtraining verschoben: Verarbeitung läuft")
        return None
    return train(collect_samples(), force=force)


def cold_start_status() -> dict:
    """Fuer die Statusseite (B-27): aktives Modell, Beispiele je Klasse, Schwelle."""
    active = ClassifierModel.objects.filter(is_active=True).first()
    counts: Counter = Counter()
    for ts in TrainingSample.objects.filter(is_active=True).values("label_category_id", "weight"):
        counts[label_a_for(ts["label_category_id"])] += float(ts["weight"])
    minimum = float(store.get("classification.stage2_min_samples_per_class", 15))
    return {
        "active_version": active.version if active else None,
        "macro_f1": (active.metrics or {}).get("macro_f1") if active else None,
        "weighted_samples": {k: round(v, 1) for k, v in counts.items()},
        "minimum_per_class": minimum,
        "cold_start": active is None,
    }
