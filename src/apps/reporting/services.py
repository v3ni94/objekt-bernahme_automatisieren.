"""Reporting nach CR 13 und Fachentwurf H 7.3 (Referenzdefinition D 12.4): Kennzahlen je Objekt (Anteil 06_Sonstiges
brutto, aktuell und bereinigt; Vollstaendigkeitsstatus; offene Review-Faelle mit Alter; Trefferquote System; Eigentuemer-
akten; KI-Kosten; Importstand), Objektuebersicht als Tabelle und CSV-Export. Keine Diagramme in der ersten Ausbaustufe."""

from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.ai.models import AiCall
from apps.config import store
from apps.documents.models import Document, DocumentClassification, DocumentOwnerLink
from apps.imports.models import ImportBatch
from apps.objects.models import ManagedObject
from apps.parties.models import OwnerFile
from apps.pipeline.models import ProcessingRun
from apps.review.models import CaseStatus, ReviewCase, ReviewDecision

CLASSIFIED_STATUSES = ("classified", "filed", "review")


@dataclass
class ObjectKpi:
    object: ManagedObject
    docs_total: int = 0
    docs_classified: int = 0
    misc_current: int = 0
    misc_share_current: float | None = None
    misc_share_run: float | None = None
    misc_share_adjusted: float | None = None
    completeness_status: str = "not_evaluated"
    completeness_ratio: float | None = None
    missing_by_category: dict = field(default_factory=dict)
    review_open: int = 0
    review_by_type: dict = field(default_factory=dict)
    review_age_median: float | None = None
    review_age_max: float | None = None
    review_age_warning: bool = False
    hit_rate: float | None = None
    hit_rate_by_stage: dict = field(default_factory=dict)
    decisions: int = 0
    owner_files: int = 0
    owner_files_empty: int = 0
    owner_files_special: dict = field(default_factory=dict)
    ai_cost_eur: Decimal = Decimal("0")
    ai_calls: int = 0
    ai_share_pct: float | None = None
    ai_cost_month: Decimal = Decimal("0")
    imports: dict = field(default_factory=dict)
    last_run_at: object = None


def business_days(start: date, end: date) -> int:
    """Arbeitstage Montag bis Freitag zwischen zwei Daten (Feiertage nicht beruecksichtigt, ANNAHME)."""
    if end <= start:
        return 0
    days = 0
    d = start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def _pct(part: int, total: int) -> float | None:
    return round(100 * part / total, 2) if total else None


def object_kpi(obj: ManagedObject, *, today: date | None = None) -> ObjectKpi:
    today = today or timezone.localdate()
    k = ObjectKpi(object=obj)
    docs = Document.objects.filter(object=obj, deleted_at__isnull=True).exclude(
        status__in=("moved_out", "duplicate")
    )
    k.docs_total = docs.count()
    classified = docs.filter(status__in=CLASSIFIED_STATUSES)
    k.docs_classified = classified.count()
    k.misc_current = classified.filter(category_id="06").count()
    k.misc_share_current = _pct(k.misc_current, k.docs_classified)
    adjusted = DocumentClassification.objects.filter(
        document__in=classified, is_final=True, features__kpi_misc_adjusted=True
    ).count()
    k.misc_share_adjusted = _pct(adjusted, k.docs_classified)
    last_run = (
        ProcessingRun.objects.filter(object=obj, status__in=("done", "failed"))
        .order_by("-finished_at")
        .first()
    )
    if last_run is not None:
        k.misc_share_run = float(last_run.misc_share_pct) if last_run.misc_share_pct is not None else None
        k.last_run_at = last_run.finished_at
    # Vollstaendigkeit (M10)
    from apps.requirements.engine import summary

    s = summary(obj)
    k.completeness_status, k.completeness_ratio = s["status"], s["ratio"]
    k.missing_by_category = s.get("missing_by_category", {})
    # Review-Faelle
    open_cases = ReviewCase.objects.filter(object=obj, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS])
    k.review_open = open_cases.count()
    k.review_by_type = {
        r["case_type"]: r["c"] for r in open_cases.values("case_type").annotate(c=Count("id"))
    }
    ages = [business_days(c.created_at.date(), today) for c in open_cases.only("created_at")]
    if ages:
        k.review_age_median, k.review_age_max = float(statistics.median(ages)), float(max(ages))
        k.review_age_warning = k.review_age_max > int(store.get("reports.review_age_warning_days", 10))
    # Trefferquote System je Stufe des finalen Vorschlags
    decisions = ReviewDecision.objects.filter(
        review_case__object=obj, system_was_correct__isnull=False
    ).select_related("document")
    k.decisions = decisions.count()
    if k.decisions:
        correct = decisions.filter(system_was_correct=True).count()
        k.hit_rate = _pct(correct, k.decisions)
        stage_of_doc = {
            row["document_id"]: row["stage"]
            for row in DocumentClassification.objects.filter(
                document__object=obj, is_final=True, stage__in=(1, 2, 3)
            ).values("document_id", "stage")
        }
        by_stage: dict[str, list[bool]] = {}
        for d in decisions:
            stage = stage_of_doc.get(d.document_id)
            by_stage.setdefault(f"Stufe {stage}" if stage else "ohne Stufe", []).append(
                bool(d.system_was_correct)
            )
        k.hit_rate_by_stage = {s: _pct(sum(v), len(v)) for s, v in by_stage.items()}
    # Eigentuemerakten
    files = OwnerFile.active.filter(object=obj)
    k.owner_files = files.count()
    with_docs = set(
        DocumentOwnerLink.objects.filter(owner_file__object=obj, deleted_at__isnull=True).values_list(
            "owner_file_id", flat=True
        )
    )
    k.owner_files_empty = sum(1 for f in files if f.pk not in with_docs)
    k.owner_files_special = {
        f.folder_name: DocumentOwnerLink.objects.filter(owner_file=f, deleted_at__isnull=True).count()
        for f in files
        if f.file_kind in ("unknown_unit", "unassigned")
    }
    # KI-Kosten (CR 0.1)
    calls = AiCall.objects.filter(object=obj)
    agg = calls.aggregate(cost=Sum("cost_eur"), n=Count("id"))
    k.ai_cost_eur, k.ai_calls = agg["cost"] or Decimal("0"), agg["n"] or 0
    month_start = today.replace(day=1)
    k.ai_cost_month = calls.filter(requested_at__date__gte=month_start).aggregate(c=Sum("cost_eur"))[
        "c"
    ] or Decimal("0")
    stage3_docs = (
        DocumentClassification.objects.filter(document__in=classified, stage=3)
        .values("document_id")
        .distinct()
        .count()
    )
    k.ai_share_pct = _pct(stage3_docs, k.docs_classified)
    # Importstand
    agg = ImportBatch.objects.filter(object=obj).aggregate(
        batches=Count("id"),
        rows_total=Sum("rows_total"),
        rows_committed=Sum("rows_committed"),
        rows_uncertain=Sum("rows_uncertain"),
        rows_rejected=Sum("rows_rejected"),
    )
    k.imports = {key: (val or 0) for key, val in agg.items()}
    return k


def overview(*, today: date | None = None, statuses=("takeover", "active")) -> list[ObjectKpi]:
    objects = ManagedObject.active.filter(status__in=statuses).order_by("object_number_numeric")
    return [object_kpi(o, today=today) for o in objects]


CSV_COLUMNS = [
    "Objekt",
    "Bezeichnung",
    "Verwaltungsart",
    "Status",
    "Dokumente",
    "klassifiziert",
    "Anteil 06 brutto (letzter Lauf) %",
    "Anteil 06 aktuell %",
    "Anteil 06 bereinigt %",
    "Vollständigkeit",
    "Erfüllungsgrad %",
    "Review offen",
    "Review Alter Median (AT)",
    "Review Alter max (AT)",
    "Trefferquote System %",
    "Eigentümerakten",
    "Akten ohne Dokument",
    "KI-Kosten EUR",
    "KI-Kosten Monat EUR",
    "Anteil Stufe 3 %",
    "Importzeilen",
    "Import unsicher",
]


def _de(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal | float):
        return f"{value:.2f}".replace(".", ",")
    return str(value)


def overview_csv(rows: list[ObjectKpi]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for k in rows:
        o = k.object
        writer.writerow(
            [
                o.object_number,
                o.name or "",
                o.get_management_type_display(),
                o.get_status_display(),
                k.docs_total,
                k.docs_classified,
                _de(k.misc_share_run),
                _de(k.misc_share_current),
                _de(k.misc_share_adjusted),
                k.completeness_status,
                _de(k.completeness_ratio * 100 if k.completeness_ratio is not None else None),
                k.review_open,
                _de(k.review_age_median),
                _de(k.review_age_max),
                _de(k.hit_rate),
                k.owner_files,
                k.owner_files_empty,
                _de(k.ai_cost_eur),
                _de(k.ai_cost_month),
                _de(k.ai_share_pct),
                k.imports.get("rows_total", 0),
                k.imports.get("rows_uncertain", 0),
            ]
        )
    return buf.getvalue()


def review_queue_kpis() -> dict:
    """Gesamtsicht Review fuer die Statusseite (G 9.3): offen, aelter als sieben Tage, mit Kandidatenliste."""
    open_cases = ReviewCase.objects.filter(status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS])
    week_ago = timezone.now() - timedelta(days=7)
    return {
        "open": open_cases.count(),
        "older_7d": open_cases.filter(created_at__lt=week_ago).count(),
        "with_candidates": open_cases.filter(~Q(candidates=None)).exclude(candidates=[]).count(),
        "by_object": {
            r["object__object_number"]: r["c"]
            for r in open_cases.values("object__object_number")
            .annotate(c=Count("id"))
            .order_by("object__object_number")
        },
    }
