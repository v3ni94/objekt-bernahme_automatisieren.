"""Vollstaendigkeitspruefung und Nachforderung (Fachentwurf D 11.2; Ergaenzung docs/architektur.md 5.6 Nr. 14).

Der Pruefkatalog completeness_checks ist datengetrieben (CR 12, Frage F10: minimaler Neubau). Nachforderungen
sind Entwuerfe mit Freigabe (demands.approve); Versand erfolgt nie durch das System.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from objektakte.db import ConcatWS, TimestampedModel, ifnull_int

USER = settings.AUTH_USER_MODEL


class ScopeType(models.TextChoices):
    OBJECT = "object", "Objekt"
    UNIT = "unit", "Einheit"
    ASSIGNMENT = "assignment", "Zuordnung"


class FindingStatus(models.TextChoices):
    MISSING = "missing", "fehlt"
    PARTIAL = "partial", "teilweise"
    FULFILLED = "fulfilled", "erfüllt"
    NOT_APPLICABLE = "not_applicable", "nicht anwendbar"


class RequestTextBlock(TimestampedModel):
    code = models.CharField(max_length=48, unique=True)
    title = models.CharField(max_length=120)
    text = models.TextField()
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "request_text_blocks"
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.title


class CompletenessCheck(TimestampedModel):
    code = models.CharField(max_length=48, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, null=True, blank=True)
    scope_type = models.CharField(max_length=24, choices=ScopeType.choices)
    management_types = models.JSONField(
        null=True, blank=True, help_text="Verwaltungsarten, für die der Prüfpunkt gilt; leer bedeutet alle"
    )
    period_based = models.BooleanField(default=False)
    evidence_document_types = models.JSONField(
        null=True, blank=True, help_text="Codes aus document_types, die den Punkt belegen"
    )
    request_text_block = models.ForeignKey(
        RequestTextBlock,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="request_text_block_id",
        related_name="checks",
    )
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "completeness_checks"
        ordering = ["sort_order", "code"]
        constraints = [
            models.CheckConstraint(condition=Q(scope_type__in=ScopeType.values), name="ck_checks_scope")
        ]

    def __str__(self) -> str:
        return self.name


class CompletenessFinding(TimestampedModel):
    object = models.ForeignKey(
        "objects.ManagedObject", on_delete=models.PROTECT, db_column="object_id", related_name="findings"
    )
    check_code = models.CharField(max_length=48)
    scope_type = models.CharField(max_length=24, choices=ScopeType.choices)
    unit = models.ForeignKey(
        "objects.Unit", null=True, blank=True, on_delete=models.PROTECT, db_column="unit_id", related_name="+"
    )
    assignment = models.ForeignKey(
        "parties.OwnerUnitAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="assignment_id",
        related_name="+",
    )
    period_year = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=FindingStatus.choices)
    evidence_document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="evidence_document_id",
        related_name="+",
    )
    evidence_link = models.ForeignKey(
        "documents.DocumentOwnerLink",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="evidence_link_id",
        related_name="+",
    )
    details = models.JSONField(null=True, blank=True)
    include_in_request = models.BooleanField(default=True)
    manual_status = models.CharField(
        max_length=16, null=True, blank=True, help_text="Negativerklärung: fulfilled oder not_applicable"
    )
    manual_reason = models.CharField(max_length=500, null=True, blank=True)
    manual_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="manual_by", related_name="+"
    )
    manual_at = models.DateTimeField(null=True, blank=True)
    last_evaluated_at = models.DateTimeField()
    position_key = models.GeneratedField(
        expression=ConcatWS(
            models.Value("|"),
            models.F("check_code"),
            models.F("scope_type"),
            ifnull_int("unit_id"),
            ifnull_int("assignment_id"),
            ifnull_int("period_year"),
        ),
        output_field=models.CharField(max_length=120),
        db_persist=True,
    )

    class Meta:
        db_table = "completeness_findings"
        constraints = [
            models.UniqueConstraint(fields=["object", "position_key"], name="uq_findings_position"),
            models.CheckConstraint(condition=Q(scope_type__in=ScopeType.values), name="ck_findings_scope"),
            models.CheckConstraint(condition=Q(status__in=FindingStatus.values), name="ck_findings_status"),
            models.CheckConstraint(
                condition=Q(manual_status__isnull=True)
                | Q(manual_status__in=["fulfilled", "not_applicable"]),
                name="ck_findings_manual",
            ),
        ]
        indexes = [models.Index(fields=["object", "status"], name="ix_findings_status")]

    def __str__(self) -> str:
        return f"{self.object_id} {self.check_code}: {self.status}"


class RequestStatus(models.TextChoices):
    DRAFT = "draft", "Entwurf"
    APPROVED = "approved", "freigegeben"
    SENT = "sent", "versendet (manuell)"
    WITHDRAWN = "withdrawn", "zurückgezogen"


class DocumentRequest(TimestampedModel):
    """Nachforderung an die Vorverwaltung; Freigabe durch demands.approve, Versand ausserhalb des Systems."""

    object = models.ForeignKey(
        "objects.ManagedObject",
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="document_requests",
    )
    status = models.CharField(max_length=16, choices=RequestStatus.choices, default=RequestStatus.DRAFT)
    recipient_name = models.CharField(max_length=160)
    recipient_street = models.CharField(max_length=120, null=True, blank=True)
    recipient_house_number = models.CharField(max_length=20, null=True, blank=True)
    recipient_postal_code = models.CharField(max_length=10, null=True, blank=True)
    recipient_city = models.CharField(max_length=80, null=True, blank=True)
    recipient_contact_person = models.CharField(max_length=160, null=True, blank=True)
    reference = models.CharField(max_length=80, null=True, blank=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    finding_ids = models.JSONField(null=True, blank=True)
    file_path = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )
    approved_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="approved_by", related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="withdrawn_by", related_name="+"
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "document_requests"
        constraints = [
            models.CheckConstraint(condition=Q(status__in=RequestStatus.values), name="ck_requests_status")
        ]
        indexes = [models.Index(fields=["object", "status"], name="ix_requests_object_status")]

    def __str__(self) -> str:
        return f"Nachforderung {self.pk} Objekt {self.object_id} ({self.status})"
