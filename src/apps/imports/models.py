"""Importlaeufe, Importzeilen und Spaltenprofile (Fachentwurf D 9.1, 9.2; Ergaenzung 5.6 Nr. 14).

raw_data und raw_text enthalten personenbezogene Daten der Quelle; vollstaendige IBAN werden vor dem
Speichern maskiert (D 9.2). Uebernahme in die Stammdaten nur nach Bestaetigung im Review Center (CR 15).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from objektakte.db import TimestampedModel

USER = settings.AUTH_USER_MODEL


class ImportKind(models.TextChoices):
    OWNER_LIST = "owner_list", "Eigentümerliste"
    TENANT_LIST = "tenant_list", "Mieterliste"
    MIXED = "mixed", "gemischt"


class SourceFormat(models.TextChoices):
    PDF_SCAN = "pdf_scan", "PDF (Scan)"
    PDF_DIGITAL = "pdf_digital", "PDF (digital)"
    XLSX = "xlsx", "Excel"
    CSV = "csv", "CSV"
    IMMOWARE24 = "immoware24", "Immoware24-Export"
    DOMUS = "domus", "Domus-Export"
    OTHER = "other", "sonstige"


class BatchStatus(models.TextChoices):
    UPLOADED = "uploaded", "hochgeladen"
    PARSED = "parsed", "eingelesen"
    IN_REVIEW = "in_review", "in Prüfung"
    COMMITTED = "committed", "übernommen"
    PARTIALLY_COMMITTED = "partially_committed", "teilweise übernommen"
    REJECTED = "rejected", "abgelehnt"
    FAILED = "failed", "fehlgeschlagen"


class ImportBatch(TimestampedModel):
    object = models.ForeignKey(
        "objects.ManagedObject",
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="import_batches",
    )
    import_kind = models.CharField(max_length=16, choices=ImportKind.choices)
    source_format = models.CharField(max_length=24, choices=SourceFormat.choices)
    parser_profile = models.CharField(max_length=48)
    parser_version = models.CharField(max_length=24)
    source_document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_document_id",
        related_name="+",
    )
    source_sha256 = models.CharField(max_length=64)
    source_file_name = models.CharField(max_length=255)
    status = models.CharField(max_length=24, choices=BatchStatus.choices, default=BatchStatus.UPLOADED)
    rows_total = models.PositiveIntegerField(null=True, blank=True)
    rows_uncertain = models.PositiveIntegerField(null=True, blank=True)
    rows_confirmed = models.PositiveIntegerField(null=True, blank=True)
    rows_rejected = models.PositiveIntegerField(null=True, blank=True)
    rows_committed = models.PositiveIntegerField(null=True, blank=True)
    column_mapping = models.JSONField(null=True, blank=True)
    ai_call = models.ForeignKey(
        "ai.AiCall", null=True, blank=True, on_delete=models.PROTECT, db_column="ai_call_id", related_name="+"
    )
    error_message = models.TextField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="uploaded_by", related_name="+"
    )
    committed_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="committed_by", related_name="+"
    )
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "import_batches"
        constraints = [
            models.UniqueConstraint(
                fields=["object", "source_sha256", "parser_version"], name="uq_import_object_hash"
            ),
            models.CheckConstraint(condition=Q(import_kind__in=ImportKind.values), name="ck_import_kind"),
            models.CheckConstraint(
                condition=Q(source_format__in=SourceFormat.values), name="ck_import_format"
            ),
            models.CheckConstraint(condition=Q(status__in=BatchStatus.values), name="ck_import_status"),
        ]
        indexes = [models.Index(fields=["status"], name="ix_import_status")]

    def __str__(self) -> str:
        return f"Import {self.pk} {self.source_file_name} ({self.status})"


class RowStatus(models.TextChoices):
    PARSED = "parsed", "eingelesen"
    UNCERTAIN = "uncertain", "unsicher"
    CONFIRMED = "confirmed", "bestätigt"
    REJECTED = "rejected", "abgelehnt"
    COMMITTED = "committed", "übernommen"
    DUPLICATE = "duplicate", "Dublette"


class TargetEntity(models.TextChoices):
    OWNER = "owner", "Eigentümer"
    UNIT = "unit", "Einheit"
    OWNER_UNIT_ASSIGNMENT = "owner_unit_assignment", "Eigentümerzuordnung"
    TENANT = "tenant", "Mieter"
    LEASE = "lease", "Mietverhältnis"
    TENANT_UNIT_ASSIGNMENT = "tenant_unit_assignment", "Mieterzuordnung"


class ImportRow(TimestampedModel):
    batch = models.ForeignKey(
        ImportBatch, on_delete=models.PROTECT, db_column="batch_id", related_name="rows"
    )
    row_no = models.PositiveIntegerField()
    sub_index = models.PositiveSmallIntegerField(default=0)
    page_no = models.PositiveIntegerField(null=True, blank=True)
    raw_data = models.JSONField()
    raw_text = models.TextField(null=True, blank=True)
    parsed_fields = models.JSONField()
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=16, choices=RowStatus.choices, default=RowStatus.PARSED)
    target_entity_type = models.CharField(max_length=32, choices=TargetEntity.choices, null=True, blank=True)
    matched_owner = models.ForeignKey(
        "parties.Owner",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="matched_owner_id",
        related_name="+",
    )
    matched_unit = models.ForeignKey(
        "objects.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="matched_unit_id",
        related_name="+",
    )
    matched_tenant = models.ForeignKey(
        "parties.Tenant",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="matched_tenant_id",
        related_name="+",
    )
    committed_owner = models.ForeignKey(
        "parties.Owner",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="committed_owner_id",
        related_name="+",
    )
    committed_unit = models.ForeignKey(
        "objects.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="committed_unit_id",
        related_name="+",
    )
    committed_assignment = models.ForeignKey(
        "parties.OwnerUnitAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="committed_assignment_id",
        related_name="+",
    )
    committed_tenant = models.ForeignKey(
        "parties.Tenant",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="committed_tenant_id",
        related_name="+",
    )
    committed_lease = models.ForeignKey(
        "parties.Lease",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="committed_lease_id",
        related_name="+",
    )
    committed_targets = models.JSONField(null=True, blank=True)
    review_case = models.ForeignKey(
        "review.ReviewCase",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="review_case_id",
        related_name="+",
    )
    uncertainty_reasons = models.JSONField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="confirmed_by", related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = "import_rows"
        constraints = [
            models.UniqueConstraint(fields=["batch", "row_no", "sub_index"], name="uq_import_rows_position"),
            models.CheckConstraint(condition=Q(status__in=RowStatus.values), name="ck_import_rows_status"),
            models.CheckConstraint(
                condition=Q(target_entity_type__isnull=True) | Q(target_entity_type__in=TargetEntity.values),
                name="ck_import_rows_target",
            ),
        ]
        indexes = [models.Index(fields=["batch", "status"], name="ix_import_rows_status")]

    def __str__(self) -> str:
        return f"Import {self.batch_id} Zeile {self.row_no}.{self.sub_index}"


class ImportColumnProfile(TimestampedModel):
    """Benanntes Spaltenprofil fuer weitere Objekte derselben Vorverwaltung (H 6.3, Ergaenzung Nr. 14)."""

    name = models.CharField(max_length=80, unique=True)
    source_format = models.CharField(max_length=24, choices=SourceFormat.choices)
    parser_profile = models.CharField(max_length=48)
    column_mapping = models.JSONField()
    previous_manager_name = models.CharField(max_length=160, null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )

    class Meta:
        db_table = "import_column_profiles"

    def __str__(self) -> str:
        return self.name
