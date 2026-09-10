"""Review-Faelle, Entscheidungen und gespeicherte Sichten (Fachentwurf D 8.1, 8.2; Ergaenzung 5.6 Nr. 6, 7).

Fallarten nach Beschluss B-09: der fachliche Grund bestimmt case_type, unclear nur ohne fachlichen Grund.
data_consistency ist eine Ergaenzung aus M2 fuer den naechtlichen Konsistenzlauf (docs/architektur/datenmodell.md).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from objektakte.db import TimestampedModel

USER = settings.AUTH_USER_MODEL


class CaseType(models.TextChoices):
    OWNER_CANDIDATES = "owner_candidates", "Eigentümer mehrdeutig"
    MISSING_METADATA = "missing_metadata", "Pflichtmetadatum fehlt"
    MOVE_PROPOSAL = "move_proposal", "Verschiebevorschlag"
    DRIVE_STRUCTURE = "drive_structure", "Ordnerstruktur"
    IMPORT_ROW_UNCERTAIN = "import_row_uncertain", "Importzeile unsicher"
    IMPORT_CANDIDATE = "import_candidate", "Liste erkannt"
    DUPLICATE_OBJECT_NUMBER = "duplicate_object_number", "Objektnummer doppelt"
    DATA_CONSISTENCY = "data_consistency", "Stammdaten widersprüchlich"
    UNCLEAR = "unclear", "unklar"


class CaseStatus(models.TextChoices):
    OPEN = "open", "offen"
    IN_PROGRESS = "in_progress", "in Bearbeitung"
    RESOLVED = "resolved", "erledigt"
    DISMISSED = "dismissed", "verworfen"


class ReviewCase(TimestampedModel):
    object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="review_cases",
    )
    case_type = models.CharField(max_length=32, choices=CaseType.choices)
    case_subtype = models.CharField(max_length=32, null=True, blank=True)
    document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_id",
        related_name="review_cases",
    )
    page_from = models.PositiveIntegerField(null=True, blank=True)
    page_to = models.PositiveIntegerField(null=True, blank=True)
    owner_link = models.ForeignKey(
        "documents.DocumentOwnerLink",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="owner_link_id",
        related_name="+",
    )
    import_row = models.ForeignKey(
        "imports.ImportRow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="import_row_id",
        related_name="+",
    )
    drive_node = models.ForeignKey(
        "drive.DriveNode",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="drive_node_id",
        related_name="+",
    )
    misc_subfolder = models.ForeignKey(
        "documents.DocumentSubfolder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="misc_subfolder_id",
        related_name="+",
    )
    candidates = models.JSONField(null=True, blank=True)
    proposed_action = models.JSONField(null=True, blank=True)
    context = models.JSONField(null=True, blank=True)
    batch_key = models.CharField(max_length=120, null=True, blank=True)
    priority = models.SmallIntegerField(default=100)
    status = models.CharField(max_length=16, choices=CaseStatus.choices, default=CaseStatus.OPEN)
    assigned_to = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="assigned_to", related_name="+"
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="resolved_by", related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.JSONField(null=True, blank=True)
    created_by_run = models.ForeignKey(
        "pipeline.ProcessingRun",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="created_by_run_id",
        related_name="+",
    )

    class Meta:
        db_table = "review_cases"
        constraints = [
            models.CheckConstraint(condition=Q(case_type__in=CaseType.values), name="ck_review_type"),
            models.CheckConstraint(condition=Q(status__in=CaseStatus.values), name="ck_review_status"),
            models.CheckConstraint(
                condition=Q(page_from__isnull=True)
                | Q(page_to__isnull=True)
                | Q(page_from__lte=F("page_to")),
                name="ck_review_pages",
            ),
        ]
        indexes = [
            models.Index(fields=["object", "status", "case_type"], name="ix_review_object_status"),
            models.Index(fields=["status", "priority", "created_at"], name="ix_review_queue"),
            models.Index(fields=["batch_key"], name="ix_review_batch"),
            models.Index(fields=["assigned_to", "status"], name="ix_review_assigned"),
        ]

    def __str__(self) -> str:
        return f"Fall {self.pk} {self.case_type} ({self.status})"


class DecisionType(models.TextChoices):
    CONFIRM = "confirm", "bestätigen"
    CORRECT = "correct", "korrigieren"
    REJECT = "reject", "ablehnen"
    MOVE = "move", "verschieben"
    ASSIGN_OWNER = "assign_owner", "Eigentümer zuordnen"
    ASSIGN_TENANT = "assign_tenant", "Mieter zuordnen"
    SPLIT = "split", "aufteilen"
    MERGE = "merge", "zusammenführen"
    SELECT_FOLDER = "select_folder", "Ordner wählen"
    TRANSFER_OBJECT = "transfer_object", "in anderes Objekt übernehmen"
    REVERT = "revert", "zurücknehmen"


class ReviewDecision(models.Model):
    review_case = models.ForeignKey(
        ReviewCase, on_delete=models.PROTECT, db_column="review_case_id", related_name="decisions"
    )
    document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_id",
        related_name="+",
    )
    page_from = models.PositiveIntegerField(null=True, blank=True)
    page_to = models.PositiveIntegerField(null=True, blank=True)
    decision_type = models.CharField(max_length=24, choices=DecisionType.choices)
    decided_by = models.ForeignKey(USER, on_delete=models.PROTECT, db_column="decided_by", related_name="+")
    decided_at = models.DateTimeField()
    is_bulk = models.BooleanField(default=False)
    bulk_key = models.CharField(max_length=120, null=True, blank=True)
    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField()
    features_snapshot = models.JSONField(null=True, blank=True)
    text_hashes = models.JSONField(null=True, blank=True)
    label_category = models.ForeignKey(
        "documents.DocumentCategory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_category_code",
        related_name="+",
    )
    label_subfolder = models.ForeignKey(
        "documents.DocumentSubfolder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_subfolder_id",
        related_name="+",
    )
    label_document_type = models.ForeignKey(
        "documents.DocumentType",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_document_type_id",
        related_name="+",
    )
    label_owner = models.ForeignKey(
        "parties.Owner",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_owner_id",
        related_name="+",
    )
    label_unit = models.ForeignKey(
        "objects.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_unit_id",
        related_name="+",
    )
    label_assignment = models.ForeignKey(
        "parties.OwnerUnitAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_assignment_id",
        related_name="+",
    )
    label_period_year = models.PositiveSmallIntegerField(null=True, blank=True)
    label_scope = models.CharField(max_length=16, null=True, blank=True)
    system_was_correct = models.BooleanField(null=True, blank=True)
    used_for_training = models.BooleanField(default=False)
    training_export_ref = models.CharField(max_length=80, null=True, blank=True)
    comment = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "review_decisions"
        constraints = [
            models.CheckConstraint(
                condition=Q(decision_type__in=DecisionType.values), name="ck_decisions_type"
            ),
            models.CheckConstraint(
                condition=Q(label_scope__isnull=True)
                | Q(label_scope__in=["object", "accounting", "owner", "tenant"]),
                name="ck_decisions_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["used_for_training", "label_category"], name="ix_decisions_training"),
            models.Index(fields=["decided_by", "decided_at"], name="ix_decisions_user"),
        ]

    def __str__(self) -> str:
        return f"Entscheidung {self.pk} {self.decision_type} zu Fall {self.review_case_id}"


class ReviewSavedFilter(TimestampedModel):
    user = models.ForeignKey(
        USER, on_delete=models.PROTECT, db_column="user_id", related_name="review_filters"
    )
    name = models.CharField(max_length=80)
    filters = models.JSONField()
    is_shared = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        db_table = "review_saved_filters"
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="uq_review_filters_user_name")]

    def __str__(self) -> str:
        return self.name
