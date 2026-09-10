"""Stufe 2: lokaler Textklassifikator (E 3.3, docs/architektur.md 6.3). TF-IDF ueber Zeichen-n-Gramme 3 bis 5 und
Wort-1-2-Gramme plus logistische Regression; Modell A Hauptkategorie, Modell B Unterordner plus Unterart in 05.
Kaltstart (B-27): ohne aktives Modell oder unter stage2_min_samples_per_class je Klasse liefert predict cold_start.
Interface LocalClassifier.predict(text, context) -> list[ScoredLabel]; Artefakte unter DATA_DIR/models/<version>/."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.classification.confidence import Stage2Result
from apps.classification.context import DocContext
from apps.config import store

logger = logging.getLogger(__name__)
CATEGORY_LABELS = ("01", "02", "03", "04", "05", "nicht_objektbezogen")


@dataclass
class ScoredLabel:
    label: str
    p: float


def models_dir() -> Path:
    return Path(settings.OBJEKTAKTE["DATA_DIR"]) / "models"


def context_tokens(ctx: DocContext) -> str:
    """Kontexttoken nach E 3.3, damit Verwaltungsart und Stufe-1-Signale als Merkmale wirken."""
    tokens = [f"__mgmt_{ctx.management_type}__"]
    if ctx.folder_code:
        tokens.append(f"__src_folder_{ctx.folder_code.split('/')[0]}__")
    if ctx.unit_ids:
        tokens.append("__has_unit__")
    if ctx.owner_ids:
        tokens.append("__has_owner__")
    if ctx.tenant_ids:
        tokens.append("__has_tenant__")
    if ctx.iban_found:
        tokens.append("__has_iban__")
    if ctx.period.year:
        tokens.append("__period_year__")
    if ctx.foreign_object_numbers and not ctx.own_object_marker:
        tokens.append("__foreign_marker__")
    return " ".join(tokens)


def input_text(ctx: DocContext) -> str:
    max_chars = int(store.get("classification.text_max_chars", 4000))
    text = " ".join((ctx.text or "").split())[:max_chars]
    return f"{context_tokens(ctx)} {text}"


class LocalClassifier:
    """Laedt das aktive Modell (classifier_models.is_active) aus dem Artefakt; predict liefert kalibrierte
    Wahrscheinlichkeiten je Klasse."""

    def __init__(self, version: str, artifact_dir: Path):
        import joblib

        self.version = version
        self.model_a = joblib.load(artifact_dir / "model_a.joblib")
        self.model_b = (
            joblib.load(artifact_dir / "model_b.joblib")
            if (artifact_dir / "model_b.joblib").exists()
            else None
        )
        self.labels = json.loads((artifact_dir / "labels.json").read_text(encoding="utf-8"))

    def predict(self, text: str) -> list[ScoredLabel]:
        probs = self.model_a.predict_proba([text])[0]
        ranked = sorted(zip(self.model_a.classes_, probs, strict=True), key=lambda x: -x[1])
        return [ScoredLabel(str(label), float(p)) for label, p in ranked]

    def predict_sub(self, text: str) -> list[ScoredLabel]:
        if self.model_b is None:
            return []
        probs = self.model_b.predict_proba([text])[0]
        ranked = sorted(zip(self.model_b.classes_, probs, strict=True), key=lambda x: -x[1])
        return [ScoredLabel(str(label), float(p)) for label, p in ranked]


_CACHE: dict[str, LocalClassifier] = {}


def active_classifier() -> LocalClassifier | None:
    from apps.documents.models import ClassifierModel

    row = ClassifierModel.objects.filter(is_active=True).order_by("-activated_at").first()
    if row is None:
        return None
    if row.version in _CACHE:
        return _CACHE[row.version]
    artifact = Path(row.artifact_path)
    if not artifact.is_absolute():
        artifact = models_dir() / artifact
    if not (artifact / "model_a.joblib").exists():
        logger.warning("Artefakt des aktiven Modells %s fehlt unter %s", row.version, artifact)
        return None
    clf = LocalClassifier(row.version, artifact)
    _CACHE.clear()
    _CACHE[row.version] = clf
    return clf


def stage3_enabled() -> bool:
    providers = store.get("ai.providers", {}) or {}
    return any((p or {}).get("enabled") for p in providers.values())


def predict(ctx: DocContext) -> Stage2Result:
    clf = active_classifier()
    if clf is None:
        return Stage2Result(cold_start=True)
    text = input_text(ctx)
    scored = clf.predict(text)
    if not scored:
        return Stage2Result(cold_start=True)
    top, p = scored[0].label, scored[0].p
    second = scored[1].p if len(scored) > 1 else 0.0
    result = Stage2Result(
        top=top
        if top in ("01", "02", "03", "04", "05", "06")
        else ("06" if top == "nicht_objektbezogen" else top),
        p=round(p, 4),
        gap=round(p - second, 4),
        labels=[(s.label, round(s.p, 4)) for s in scored[:5]],
        model_version=clf.version,
        cold_start=False,
    )
    if result.top == "05":
        sub = clf.predict_sub(text)
        if sub:
            parts = sub[0].label.split("/")
            if len(parts) == 3:
                result.subfolder, result.document_type = parts[1], parts[2]
            result.sub_p = round(sub[0].p, 4)
    return result
