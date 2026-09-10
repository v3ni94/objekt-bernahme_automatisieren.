"""Verarbeitungslaeufe, Jobs und Fortschritt (Fachentwurf D 7.1 bis 7.3; Ergaenzung 5.6 Nr. 3, 4).

Die Datenbank ist Quelle der Wahrheit, die Queue transportiert nur Job-IDs (D Entscheidung 5).
Idempotenzschluessel je Jobtyp nach docs/architektur.md 5.4.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from objektakte.db import TimestampedModel

USER = settings.AUTH_USER_MODEL


class RunType(models.TextChoices):
    FULL = "full", "Vollverarbeitung"
    INCREMENTAL = "incremental", "Nachlauf"
    RECONCILE_DRIVE = "reconcile_drive", "Ordnerabgleich"
    REGENERATE_LISTS = "regenerate_lists", "Listen erzeugen"
    RECLASSIFY = "reclassify", "Neu klassifizieren"


class RunStatus(models.TextChoices):
    PENDING = "pending", "wartet"
    RUNNING = "running", "läuft"
    DONE = "done", "fertig"
    FAILED = "failed", "fehlgeschlagen"
    ABORTED = "aborted", "abgebrochen"


class ProcessingRun(TimestampedModel):
    object = models.ForeignKey(
        "objects.ManagedObject",
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="processing_runs",
    )
    run_type = models.CharField(max_length=24, choices=RunType.choices)
    status = models.CharField(max_length=16, choices=RunStatus.choices, default=RunStatus.PENDING)
    dry_run = models.BooleanField(default=False)
    triggered_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="triggered_by", related_name="+"
    )
    worker_count = models.PositiveSmallIntegerField(null=True, blank=True)
    documents_total = models.PositiveIntegerField(null=True, blank=True)
    documents_done = models.PositiveIntegerField(null=True, blank=True)
    documents_failed = models.PositiveIntegerField(null=True, blank=True)
    documents_skipped = models.PositiveIntegerField(null=True, blank=True)
    pages_total = models.PositiveIntegerField(null=True, blank=True)
    pages_done = models.PositiveIntegerField(null=True, blank=True)
    documents_misc = models.PositiveIntegerField(null=True, blank=True)
    misc_share_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    pages_per_minute = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    ram_peak_mb = models.PositiveIntegerField(null=True, blank=True)
    ai_calls_count = models.PositiveIntegerField(null=True, blank=True)
    ai_cost_eur = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    ai_fallback_count = models.PositiveIntegerField(null=True, blank=True)
    review_cases_created = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "processing_runs"
        constraints = [
            models.CheckConstraint(condition=Q(run_type__in=RunType.values), name="ck_runs_type"),
            models.CheckConstraint(condition=Q(status__in=RunStatus.values), name="ck_runs_status"),
        ]
        indexes = [
            models.Index(fields=["object", "started_at"], name="ix_runs_object_started"),
            models.Index(fields=["status"], name="ix_runs_status"),
        ]

    def __str__(self) -> str:
        return f"Lauf {self.pk} {self.run_type} ({self.status})"


class JobType(models.TextChoices):
    DISCOVER = "discover", "Inventur"
    HASH = "hash", "Download und Hash"
    ANALYZE_PAGES = "analyze_pages", "Seitenanalyse"
    OCR_CHUNK = "ocr_chunk", "OCR je Block"
    MERGE_PAGES = "merge_pages", "Seiten zusammenführen"
    RENDER_PREVIEWS = "render_previews", "Vorschauen"
    EXTRACT_ENTITIES = "extract_entities", "Entitäten erkennen"
    CLASSIFY = "classify", "Klassifikation Stufe 1 und 2"
    CLASSIFY_AI = "classify_ai", "Klassifikation Stufe 3"
    DECIDE = "decide", "Entscheidung"
    FILE_TO_DRIVE = "file_to_drive", "Ablage in Drive"
    LINK_SEGMENTS = "link_segments", "Seitenbereiche verknüpfen"
    GENERATE_LISTS = "generate_lists", "Listen erzeugen"
    EVALUATE_COMPLETENESS = "evaluate_completeness", "Vollständigkeit prüfen"
    RECONCILE_DRIVE = "reconcile_drive", "Ordnerabgleich"
    TRAIN_CLASSIFIER = "train_classifier", "Klassifikator trainieren"
    SWEEP = "sweep", "Sweeper"


class JobStatus(models.TextChoices):
    PENDING = "pending", "wartet"
    RUNNING = "running", "läuft"
    DONE = "done", "fertig"
    FAILED = "failed", "fehlgeschlagen"
    SKIPPED = "skipped", "übersprungen"


class ProcessingJob(TimestampedModel):
    run = models.ForeignKey(
        ProcessingRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="run_id",
        related_name="jobs",
    )
    object = models.ForeignKey(
        "objects.ManagedObject",
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="processing_jobs",
    )
    document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_id",
        related_name="jobs",
    )
    job_type = models.CharField(max_length=24, choices=JobType.choices)
    idempotency_key = models.CharField(max_length=160, unique=True)
    status = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.PENDING)
    priority = models.SmallIntegerField(default=100)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    locked_by = models.CharField(max_length=80, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    pages_processed = models.PositiveIntegerField(null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    error_class = models.CharField(max_length=80, null=True, blank=True)
    skip_reason = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "processing_jobs"
        constraints = [
            models.CheckConstraint(condition=Q(job_type__in=JobType.values), name="ck_jobs_type"),
            models.CheckConstraint(condition=Q(status__in=JobStatus.values), name="ck_jobs_status"),
            models.CheckConstraint(
                condition=Q(attempt_count__lte=models.F("max_attempts") + 1), name="ck_jobs_attempts"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at", "priority", "id"], name="ix_jobs_pickup"),
            models.Index(fields=["run", "status"], name="ix_jobs_run_status"),
            models.Index(fields=["status", "heartbeat_at"], name="ix_jobs_stale"),
        ]

    def __str__(self) -> str:
        return self.idempotency_key


class ProcessingJobEvent(models.Model):
    job = models.ForeignKey(
        ProcessingJob, on_delete=models.PROTECT, db_column="job_id", related_name="events"
    )
    from_status = models.CharField(max_length=16, null=True, blank=True)
    to_status = models.CharField(max_length=16)
    worker_id = models.CharField(max_length=80, null=True, blank=True)
    attempt_no = models.PositiveSmallIntegerField(null=True, blank=True)
    message = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "processing_job_events"
        indexes = [models.Index(fields=["job", "created_at"], name="ix_job_events_job")]

    def __str__(self) -> str:
        return f"{self.job_id}: {self.from_status or '-'} nach {self.to_status}"


class ObjectProgress(models.Model):
    """Fortschrittszaehler je Objekt ohne Aggregation ueber processing_jobs (Ergaenzung Nr. 4, Ue18)."""

    object = models.OneToOneField(
        "objects.ManagedObject", on_delete=models.PROTECT, db_column="object_id", related_name="progress"
    )
    documents_total = models.PositiveIntegerField(default=0)
    documents_hashed = models.PositiveIntegerField(default=0)
    documents_ocr_done = models.PositiveIntegerField(default=0)
    documents_classified = models.PositiveIntegerField(default=0)
    documents_filed = models.PositiveIntegerField(default=0)
    documents_review = models.PositiveIntegerField(default=0)
    documents_duplicate = models.PositiveIntegerField(default=0)
    documents_error = models.PositiveIntegerField(default=0)
    documents_misc = models.PositiveIntegerField(default=0)
    pages_total = models.PositiveIntegerField(default=0)
    pages_done = models.PositiveIntegerField(default=0)
    review_open = models.PositiveIntegerField(default=0)
    last_run = models.ForeignKey(
        ProcessingRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="last_run_id",
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "object_progress"

    def __str__(self) -> str:
        return f"Fortschritt Objekt {self.object_id}: {self.documents_filed}/{self.documents_total}"
