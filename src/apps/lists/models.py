"""Protokoll der Eigentuemer- und Mieterlisten (Fachentwurf D 5.3; Ergaenzung docs/architektur.md 5.6 Nr. 10)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

USER = settings.AUTH_USER_MODEL


class TriggerKind(models.TextChoices):
    RUN = "run", "Verarbeitungslauf"
    REVIEW_CONFIRM = "review_confirm", "Review-Bestätigung"
    MANUAL = "manual", "manuell"
    IMPORT_COMMIT = "import_commit", "Import übernommen"
    MASTERDATA_CHANGE = "masterdata_change", "Stammdatenänderung"
    SCHEDULED = "scheduled", "Zeitplan"


class GenerationStatus(models.TextChoices):
    DONE = "done", "fertig"
    FAILED = "failed", "fehlgeschlagen"
    SKIPPED_UNCHANGED = "skipped_unchanged", "unverändert übersprungen"


class ListGeneration(models.Model):
    object = models.ForeignKey(
        "objects.ManagedObject",
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="list_generations",
    )
    list_type = models.CharField(max_length=16)
    list_format = models.CharField(max_length=8)
    drive_node = models.ForeignKey(
        "drive.DriveNode",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="drive_node_id",
        related_name="+",
    )
    trigger_kind = models.CharField(max_length=24, choices=TriggerKind.choices)
    triggered_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="triggered_by", related_name="+"
    )
    rows_current = models.PositiveIntegerField(null=True, blank=True)
    rows_history = models.PositiveIntegerField(null=True, blank=True)
    rows_open = models.PositiveIntegerField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, null=True, blank=True)
    status = models.CharField(max_length=24, choices=GenerationStatus.choices)
    error_message = models.TextField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    generated_at = models.DateTimeField()

    class Meta:
        db_table = "list_generations"
        constraints = [
            models.CheckConstraint(
                condition=Q(list_type__in=["owner_list", "tenant_list"]), name="ck_list_gen_type"
            ),
            models.CheckConstraint(condition=Q(list_format__in=["xlsx", "pdf"]), name="ck_list_gen_format"),
            models.CheckConstraint(
                condition=Q(trigger_kind__in=TriggerKind.values), name="ck_list_gen_trigger"
            ),
            models.CheckConstraint(
                condition=Q(status__in=GenerationStatus.values), name="ck_list_gen_status"
            ),
        ]
        indexes = [
            models.Index(
                fields=["object", "list_type", "list_format", "generated_at"], name="ix_list_gen_object"
            )
        ]

    def __str__(self) -> str:
        return f"{self.list_type}/{self.list_format} Objekt {self.object_id} {self.status}"
