"""Protokoll externer KI-Aufrufe (Fachentwurf D 11.1; Ergaenzung docs/architektur.md 5.6 Nr. 5, Beschluss B-07).

Prompts werden nicht gespeichert, nur prompt_hash und masked_entities_count als Nachweis der Maskierung.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q


class AiPurpose(models.TextChoices):
    CLASSIFY = "classify", "Klassifikation"
    EXTRACT_ENTITIES = "extract_entities", "Entitäten"
    PARSE_LIST = "parse_list", "Liste einlesen"
    OTHER = "other", "sonstiges"


class AiProvider(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    ANTHROPIC = "anthropic", "Anthropic"


class AiCallStatus(models.TextChoices):
    OK = "ok", "ok"
    ERROR = "error", "Fehler"
    TIMEOUT = "timeout", "Zeitüberschreitung"
    RATE_LIMITED = "rate_limited", "Ratenbegrenzung"
    BUDGET_BLOCKED = "budget_blocked", "Kostenlimit"
    SCHEMA_ERROR = "schema_error", "Antwortschema verletzt"
    PROVIDER_ERROR = "provider_error", "Anbieterfehler"
    BLOCKED_BY_MASK_CHECK = "blocked_by_mask_check", "durch Maskierungsprüfung gesperrt"


class AiCall(models.Model):
    object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="ai_calls",
    )
    document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_id",
        related_name="ai_calls",
    )
    run = models.ForeignKey(
        "pipeline.ProcessingRun",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="run_id",
        related_name="ai_calls",
    )
    job = models.ForeignKey(
        "pipeline.ProcessingJob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="job_id",
        related_name="ai_calls",
    )
    purpose = models.CharField(max_length=24, choices=AiPurpose.choices)
    provider = models.CharField(max_length=24, choices=AiProvider.choices)
    model = models.CharField(max_length=80)
    endpoint = models.CharField(max_length=255, null=True, blank=True)
    region = models.CharField(max_length=40, null=True, blank=True)
    page_from = models.PositiveIntegerField(null=True, blank=True)
    page_to = models.PositiveIntegerField(null=True, blank=True)
    prompt_hash = models.CharField(max_length=64, null=True, blank=True)
    prompt_chars = models.PositiveIntegerField(null=True, blank=True)
    masked_entities_count = models.PositiveSmallIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(null=True, blank=True)
    tokens_out = models.PositiveIntegerField(null=True, blank=True)
    cost_eur = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    price_list_version = models.CharField(max_length=24, null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=AiCallStatus.choices)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_message = models.CharField(max_length=1000, null=True, blank=True)
    fallback_used = models.BooleanField(default=False)
    fallback_of_call = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="fallback_of_call_id",
        related_name="fallbacks",
    )
    response_summary = models.JSONField(null=True, blank=True)
    requested_at = models.DateTimeField()

    class Meta:
        db_table = "ai_calls"
        constraints = [
            models.CheckConstraint(condition=Q(purpose__in=AiPurpose.values), name="ck_ai_calls_purpose"),
            models.CheckConstraint(condition=Q(provider__in=AiProvider.values), name="ck_ai_calls_provider"),
            models.CheckConstraint(condition=Q(status__in=AiCallStatus.values), name="ck_ai_calls_status"),
        ]
        indexes = [
            models.Index(fields=["object", "requested_at"], name="ix_ai_calls_object_time"),
            models.Index(fields=["provider", "requested_at"], name="ix_ai_calls_provider_time"),
            models.Index(fields=["status", "requested_at"], name="ix_ai_calls_status"),
        ]

    def __str__(self) -> str:
        return f"{self.provider} {self.model} {self.status}"
