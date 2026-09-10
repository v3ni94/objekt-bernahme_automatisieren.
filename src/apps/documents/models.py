"""Katalog, Aufbewahrung, Dokumente, Seiten, Entitaeten, Klassifikationen und Aktenverknuepfungen
(Fachentwurf D 4.1, 4.3, 6.1 bis 6.6; Ergaenzung docs/architektur.md 5.6 Nr. 1, 2, 15).

Kategorien und Unterordner tragen die CR-Ordnernamen nur als Seed (db/seeds/), der Code arbeitet mit Codes (B-12).
Seitentext liegt ausschliesslich maskiert vor (D 1, Volltext).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Case, F, Q, Value, When

from objektakte.db import ConcatWS, SoftDeleteModel, TimestampedModel, ifnull_int

USER = settings.AUTH_USER_MODEL


# ---------------------------------------------------------------- Katalog (D 4.1)
class CategoryScope(models.TextChoices):
    OBJECT = "object", "Objekt"
    OWNER = "owner", "Eigentümer"
    TENANT = "tenant", "Mieter"
    MISC = "misc", "Sonstiges"


class DocumentCategory(TimestampedModel):
    code = models.CharField(max_length=2, primary_key=True)
    folder_name = models.CharField(max_length=80, unique=True)
    display_name = models.CharField(max_length=80)
    scope = models.CharField(max_length=16, choices=CategoryScope.choices)
    sort_order = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "document_categories"
        ordering = ["sort_order"]
        constraints = [
            models.CheckConstraint(condition=Q(scope__in=CategoryScope.values), name="ck_categories_scope")
        ]

    def __str__(self) -> str:
        return self.folder_name


class DocumentSubfolder(TimestampedModel):
    category = models.ForeignKey(
        DocumentCategory, on_delete=models.PROTECT, db_column="category_code", related_name="subfolders"
    )
    code = models.CharField(max_length=2)
    folder_name = models.CharField(max_length=80)
    display_name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "document_subfolders"
        ordering = ["category_id", "sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["category", "code"], name="uq_subfolders_code"),
            models.UniqueConstraint(fields=["category", "folder_name"], name="uq_subfolders_name"),
        ]

    def __str__(self) -> str:
        return f"{self.category_id}/{self.folder_name}"


class DocumentType(TimestampedModel):
    category = models.ForeignKey(
        DocumentCategory, on_delete=models.PROTECT, db_column="category_code", related_name="document_types"
    )
    subfolder = models.ForeignKey(
        DocumentSubfolder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="subfolder_id",
        related_name="document_types",
    )
    code = models.CharField(max_length=48, unique=True)
    name = models.CharField(max_length=120)
    requires_period = models.BooleanField(default=False)
    requires_owner = models.BooleanField(default=False)
    requires_tenant = models.BooleanField(default=False)
    keywords = models.JSONField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "document_types"
        ordering = ["category_id", "subfolder_id", "name"]
        indexes = [models.Index(fields=["category", "subfolder"], name="ix_doctypes_category")]

    def __str__(self) -> str:
        return self.name


class RetentionTrigger(models.TextChoices):
    DOCUMENT_DATE = "document_date", "Dokumentdatum"
    PERIOD_END = "period_end", "Ende des Zeitraums"
    ASSIGNMENT_END = "assignment_end", "Ende der Zuordnung"


class RetentionPolicy(TimestampedModel):
    """Aufbewahrungsfristen (D 4.3): Standard leer, Loeschung nur mit Frist und Freigabe."""

    category = models.ForeignKey(
        DocumentCategory,
        on_delete=models.PROTECT,
        db_column="category_code",
        related_name="retention_policies",
    )
    subfolder = models.ForeignKey(
        DocumentSubfolder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="subfolder_id",
        related_name="+",
    )
    document_type = models.ForeignKey(
        DocumentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_type_id",
        related_name="+",
    )
    retention_years = models.PositiveSmallIntegerField(null=True, blank=True)
    retention_basis = models.CharField(max_length=500, null=True, blank=True)
    trigger_event = models.CharField(max_length=24, choices=RetentionTrigger.choices, null=True, blank=True)
    is_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="approved_by", related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="updated_by", related_name="+"
    )

    class Meta:
        db_table = "retention_policies"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "subfolder", "document_type"], name="uq_retention_scope"
            ),
            models.CheckConstraint(
                condition=Q(trigger_event__isnull=True) | Q(trigger_event__in=RetentionTrigger.values),
                name="ck_retention_trigger",
            ),
            models.CheckConstraint(
                condition=Q(is_approved=False)
                | (
                    Q(approved_by__isnull=False)
                    & Q(approved_at__isnull=False)
                    & Q(retention_years__isnull=False)
                ),
                name="ck_retention_approval",
            ),
        ]

    def __str__(self) -> str:
        return f"Aufbewahrung {self.category_id}: {self.retention_years or 'offen'}"


# ---------------------------------------------------------------- Dokumente (D 6.1)
class DocumentSource(models.TextChoices):
    DRIVE_EXISTING = "drive_existing", "Drive-Bestand"
    UPLOAD = "upload", "Upload"
    IMPORT = "import", "Import"
    GENERATED = "generated", "erzeugt"
    MOVED_IN = "moved_in", "aus anderem Objekt übernommen"


class DocumentStatus(models.TextChoices):
    REGISTERED = "registered", "registriert"
    HASHED = "hashed", "Hash bekannt"
    OCR_DONE = "ocr_done", "Text erkannt"
    CLASSIFIED = "classified", "klassifiziert"
    FILED = "filed", "abgelegt"
    REVIEW = "review", "in Prüfung"
    DUPLICATE = "duplicate", "Dublette"
    MOVED_OUT = "moved_out", "in anderes Objekt übernommen"
    ERROR = "error", "Fehler"


class OriginKind(models.TextChoices):
    DIGITAL = "digital", "digital"
    SCAN = "scan", "Scan"
    MIXED = "mixed", "gemischt"


class Decider(models.TextChoices):
    STAGE1 = "stage1", "Stufe 1"
    STAGE2 = "stage2", "Stufe 2"
    STAGE3 = "stage3", "Stufe 3"
    HUMAN = "human", "Mensch"


class Document(SoftDeleteModel):
    object = models.ForeignKey(
        "objects.ManagedObject", on_delete=models.PROTECT, db_column="object_id", related_name="documents"
    )
    sha256 = models.CharField(
        max_length=64, null=True, blank=True, help_text="leer bis Status hashed (Ergänzung Nr. 2)"
    )
    drive_md5 = models.CharField(max_length=32, null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=120)
    original_name = models.CharField(max_length=255)
    current_name = models.CharField(max_length=255)
    source = models.CharField(max_length=16, choices=DocumentSource.choices)
    source_path = models.CharField(max_length=1000, null=True, blank=True)
    drive_file_id = models.CharField(max_length=128, null=True, blank=True)
    drive_node = models.ForeignKey(
        "drive.DriveNode",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="drive_node_id",
        related_name="documents",
    )
    target_drive_node = models.ForeignKey(
        "drive.DriveNode",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="target_drive_node_id",
        related_name="+",
    )
    drive_moved_at = models.DateTimeField(null=True, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    origin_kind = models.CharField(max_length=16, choices=OriginKind.choices, null=True, blank=True)
    ocr_cache_key = models.CharField(max_length=160, null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=DocumentStatus.choices, default=DocumentStatus.REGISTERED
    )
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="duplicate_of_document_id",
        related_name="duplicates",
    )
    category = models.ForeignKey(
        DocumentCategory,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="category_code",
        related_name="+",
    )
    subfolder = models.ForeignKey(
        DocumentSubfolder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="subfolder_id",
        related_name="+",
    )
    document_type = models.ForeignKey(
        DocumentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_type_id",
        related_name="+",
    )
    document_date = models.DateField(null=True, blank=True)
    period_year = models.PositiveSmallIntegerField(null=True, blank=True)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    final_confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    final_decided_by = models.CharField(max_length=16, choices=Decider.choices, null=True, blank=True)
    classifier_version = models.CharField(max_length=40, null=True, blank=True)
    is_master_with_segments = models.BooleanField(default=False)
    error_message = models.TextField(null=True, blank=True)
    first_seen_at = models.DateTimeField()
    filed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents"
        constraints = [
            models.UniqueConstraint(fields=["object", "sha256"], name="uq_documents_object_hash"),
            models.UniqueConstraint(
                fields=["object", "drive_file_id"], name="uq_documents_object_drive_file"
            ),
            models.CheckConstraint(condition=Q(source__in=DocumentSource.values), name="ck_documents_source"),
            models.CheckConstraint(
                condition=Q(origin_kind__isnull=True) | Q(origin_kind__in=OriginKind.values),
                name="ck_documents_origin",
            ),
            models.CheckConstraint(condition=Q(status__in=DocumentStatus.values), name="ck_documents_status"),
            models.CheckConstraint(
                condition=Q(final_decided_by__isnull=True) | Q(final_decided_by__in=Decider.values),
                name="ck_documents_decider",
            ),
            models.CheckConstraint(
                condition=Q(period_from__isnull=True)
                | Q(period_to__isnull=True)
                | Q(period_from__lte=F("period_to")),
                name="ck_documents_period",
            ),
        ]
        indexes = [
            models.Index(fields=["drive_file_id"], name="ix_documents_drive_file"),
            models.Index(fields=["object", "status"], name="ix_documents_object_status"),
            models.Index(fields=["category", "subfolder"], name="ix_documents_category"),
            models.Index(fields=["period_year"], name="ix_documents_period"),
        ]

    def __str__(self) -> str:
        return self.current_name


class TextSource(models.TextChoices):
    TEXT_LAYER = "text_layer", "Textebene"
    OCR = "ocr", "OCR"
    MIXED = "mixed", "gemischt"
    EMPTY = "empty", "leer"


class DocumentPage(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.PROTECT, db_column="document_id", related_name="pages"
    )
    page_no = models.PositiveIntegerField()
    text_source = models.CharField(max_length=16, choices=TextSource.choices)
    is_scan = models.BooleanField()
    text_content = models.TextField(null=True, blank=True, help_text="maskierter Seitentext")
    text_hash = models.CharField(max_length=64, null=True, blank=True)
    char_count = models.PositiveIntegerField(null=True, blank=True)
    word_count = models.PositiveIntegerField(null=True, blank=True)
    ocr_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    ocr_engine = models.CharField(max_length=40, null=True, blank=True)
    ocr_language = models.CharField(max_length=8, null=True, blank=True)
    rotation_deg = models.SmallIntegerField(null=True, blank=True)
    masked_entities_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_pages"
        constraints = [
            models.UniqueConstraint(fields=["document", "page_no"], name="uq_pages_document_page"),
            models.CheckConstraint(condition=Q(text_source__in=TextSource.values), name="ck_pages_source"),
            models.CheckConstraint(
                condition=Q(ocr_confidence__isnull=True)
                | (Q(ocr_confidence__gte=0) & Q(ocr_confidence__lte=100)),
                name="ck_pages_confidence",
            ),
        ]
        indexes = [models.Index(fields=["text_hash"], name="ix_pages_text_hash")]

    def __str__(self) -> str:
        return f"{self.document_id} S. {self.page_no}"


class EntityType(models.TextChoices):
    PERSON_NAME = "person_name", "Personenname"
    COMPANY_NAME = "company_name", "Firmenname"
    UNIT_LABEL = "unit_label", "Einheit"
    AMOUNT = "amount", "Betrag"
    DATE = "date", "Datum"
    PERIOD = "period", "Zeitraum"
    IBAN = "iban", "IBAN"
    MANDATE_REF = "mandate_ref", "Mandatsreferenz"
    ADDRESS = "address", "Adresse"
    OBJECT_NUMBER = "object_number", "Objektnummer"
    ID_DOCUMENT_NUMBER = "id_document_number", "Ausweisnummer"


class DocumentEntity(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.PROTECT, db_column="document_id", related_name="entities"
    )
    page_no = models.PositiveIntegerField()
    entity_type = models.CharField(max_length=24, choices=EntityType.choices)
    value_text = models.CharField(
        max_length=255, null=True, blank=True, help_text="bei iban nur maskierte Form"
    )
    value_normalized = models.CharField(max_length=255, null=True, blank=True)
    iban_last4 = models.CharField(max_length=4, null=True, blank=True)
    iban_hash = models.CharField(max_length=64, null=True, blank=True, help_text="HMAC-SHA256 hex")
    char_from = models.PositiveIntegerField(null=True, blank=True)
    char_to = models.PositiveIntegerField(null=True, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
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
    match_confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_entities"
        constraints = [
            models.CheckConstraint(condition=Q(entity_type__in=EntityType.values), name="ck_entities_type"),
            models.CheckConstraint(
                condition=~Q(entity_type="iban")
                | Q(value_text__isnull=True)
                | ~Q(value_text__regex=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$"),
                name="ck_entities_iban_masked",
            ),
        ]
        indexes = [
            models.Index(fields=["document", "page_no"], name="ix_entities_doc_page"),
            models.Index(fields=["entity_type", "value_normalized"], name="ix_entities_type_value"),
            models.Index(fields=["iban_hash"], name="ix_entities_iban_hash"),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}: {self.value_normalized or self.value_text or ''}"


class Provider(models.TextChoices):
    RULES = "rules", "Regelwerk"
    LOCAL_MODEL = "local_model", "lokales Modell"
    OPENAI = "openai", "OpenAI"
    ANTHROPIC = "anthropic", "Anthropic"
    HUMAN = "human", "Mensch"


class ScopeDecision(models.TextChoices):
    OBJECT = "object", "Objekt"
    ACCOUNTING = "accounting", "Buchhaltung"
    OWNER = "owner", "Eigentümer"
    TENANT = "tenant", "Mieter"
    UNCLEAR = "unclear", "unklar"


class DocumentClassification(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.PROTECT, db_column="document_id", related_name="classifications"
    )
    page_from = models.PositiveIntegerField(null=True, blank=True)
    page_to = models.PositiveIntegerField(null=True, blank=True)
    stage = models.PositiveSmallIntegerField(help_text="1 Regelwerk, 2 lokal, 3 externe KI, 4 Mensch")
    provider = models.CharField(max_length=24, choices=Provider.choices)
    model = models.CharField(max_length=80, null=True, blank=True)
    category = models.ForeignKey(
        DocumentCategory,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="category_code",
        related_name="+",
    )
    subfolder = models.ForeignKey(
        DocumentSubfolder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="subfolder_id",
        related_name="+",
    )
    document_type = models.ForeignKey(
        DocumentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_type_id",
        related_name="+",
    )
    period_year = models.PositiveSmallIntegerField(null=True, blank=True)
    scope_decision = models.CharField(max_length=16, choices=ScopeDecision.choices, null=True, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4)
    reasoning = models.TextField(null=True, blank=True)
    owner_candidates = models.JSONField(null=True, blank=True)
    features = models.JSONField(null=True, blank=True)
    ai_call = models.ForeignKey(
        "ai.AiCall",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="ai_call_id",
        related_name="classifications",
    )
    tokens_in = models.PositiveIntegerField(null=True, blank=True)
    tokens_out = models.PositiveIntegerField(null=True, blank=True)
    cost_eur = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    is_final = models.BooleanField(default=False)
    decided_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="decided_by", related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_classifications"
        constraints = [
            models.CheckConstraint(condition=Q(stage__gte=1) & Q(stage__lte=4), name="ck_class_stage"),
            models.CheckConstraint(condition=Q(provider__in=Provider.values), name="ck_class_provider"),
            models.CheckConstraint(
                condition=Q(scope_decision__isnull=True) | Q(scope_decision__in=ScopeDecision.values),
                name="ck_class_scope",
            ),
            models.CheckConstraint(
                condition=Q(confidence__gte=0) & Q(confidence__lte=1), name="ck_class_confidence"
            ),
            models.CheckConstraint(
                condition=(Q(page_from__isnull=True) & Q(page_to__isnull=True))
                | (Q(page_from__gte=1) & Q(page_to__gte=F("page_from"))),
                name="ck_class_pages",
            ),
        ]
        indexes = [
            models.Index(fields=["document", "stage"], name="ix_class_document_stage"),
            models.Index(fields=["document", "is_final"], name="ix_class_document_final"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} Stufe {self.stage} {self.provider} {self.confidence}"


class LinkKind(models.TextChoices):
    WHOLE_DOCUMENT = "whole_document", "gesamtes Dokument"
    PAGE_RANGE = "page_range", "Seitenbereich"


class LinkStatus(models.TextChoices):
    SUGGESTED = "suggested", "vorgeschlagen"
    CONFIRMED = "confirmed", "bestätigt"
    REJECTED = "rejected", "abgelehnt"


class _LinkBase(SoftDeleteModel):
    document = models.ForeignKey(
        Document, on_delete=models.PROTECT, db_column="document_id", related_name="%(class)ss"
    )
    link_kind = models.CharField(max_length=16, choices=LinkKind.choices)
    page_from = models.PositiveIntegerField(null=True, blank=True)
    page_to = models.PositiveIntegerField(null=True, blank=True)
    unit = models.ForeignKey(
        "objects.Unit", null=True, blank=True, on_delete=models.PROTECT, db_column="unit_id", related_name="+"
    )
    subfolder = models.ForeignKey(
        DocumentSubfolder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="subfolder_id",
        related_name="+",
    )
    document_type = models.ForeignKey(
        DocumentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_type_id",
        related_name="+",
    )
    period_year = models.PositiveSmallIntegerField(null=True, blank=True)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    document_date = models.DateField(null=True, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=16, choices=LinkStatus.choices, default=LinkStatus.SUGGESTED)
    classification = models.ForeignKey(
        DocumentClassification,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="classification_id",
        related_name="+",
    )
    review_case = models.ForeignKey(
        "review.ReviewCase",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="review_case_id",
        related_name="+",
    )
    drive_copy_node = models.ForeignKey(
        "drive.DriveNode",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="drive_copy_node_id",
        related_name="+",
    )
    drive_copy_file_id = models.CharField(max_length=128, null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )
    confirmed_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="confirmed_by", related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class DocumentOwnerLink(_LinkBase):
    owner = models.ForeignKey(
        "parties.Owner",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="owner_id",
        related_name="document_links",
    )
    assignment = models.ForeignKey(
        "parties.OwnerUnitAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="assignment_id",
        related_name="document_links",
    )
    owner_file = models.ForeignKey(
        "parties.OwnerFile",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="owner_file_id",
        related_name="document_links",
    )
    active_key = models.GeneratedField(
        expression=Case(
            When(
                deleted_at__isnull=True,
                then=ConcatWS(
                    Value("|"),
                    ifnull_int("page_from"),
                    ifnull_int("page_to"),
                    ifnull_int("assignment_id"),
                    ifnull_int("owner_id"),
                    ifnull_int("unit_id"),
                ),
            ),
            default=Value(None),
        ),
        output_field=models.CharField(max_length=80, null=True),
        db_persist=True,
    )

    class Meta:
        db_table = "document_owner_links"
        constraints = [
            models.UniqueConstraint(fields=["document", "active_key"], name="uq_dol_document_segment"),
            models.CheckConstraint(condition=Q(link_kind__in=LinkKind.values), name="ck_dol_kind"),
            models.CheckConstraint(
                condition=(
                    Q(link_kind="whole_document") & Q(page_from__isnull=True) & Q(page_to__isnull=True)
                )
                | (Q(link_kind="page_range") & Q(page_from__gte=1) & Q(page_to__gte=F("page_from"))),
                name="ck_dol_pages",
            ),
            models.CheckConstraint(
                condition=Q(owner__isnull=False) | Q(unit__isnull=False) | Q(assignment__isnull=False),
                name="ck_dol_reference",
            ),
            models.CheckConstraint(condition=Q(status__in=LinkStatus.values), name="ck_dol_status"),
            models.CheckConstraint(
                condition=Q(period_from__isnull=True)
                | Q(period_to__isnull=True)
                | Q(period_from__lte=F("period_to")),
                name="ck_dol_period",
            ),
        ]
        indexes = [
            models.Index(fields=["owner_file", "status"], name="ix_dol_owner_file"),
            models.Index(fields=["owner", "period_year"], name="ix_dol_owner_year"),
            models.Index(fields=["unit", "period_year"], name="ix_dol_unit_year"),
            models.Index(fields=["document", "page_from"], name="ix_dol_document_page"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} zu Akte {self.owner_file_id} ({self.link_kind})"


class DocumentTenantLink(_LinkBase):
    tenant = models.ForeignKey(
        "parties.Tenant",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="tenant_id",
        related_name="document_links",
    )
    assignment = models.ForeignKey(
        "parties.TenantUnitAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="assignment_id",
        related_name="document_links",
    )
    lease = models.ForeignKey(
        "parties.Lease",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="lease_id",
        related_name="document_links",
    )
    tenant_file = models.ForeignKey(
        "parties.TenantFile",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="tenant_file_id",
        related_name="document_links",
    )
    active_key = models.GeneratedField(
        expression=Case(
            When(
                deleted_at__isnull=True,
                then=ConcatWS(
                    Value("|"),
                    ifnull_int("page_from"),
                    ifnull_int("page_to"),
                    ifnull_int("assignment_id"),
                    ifnull_int("tenant_id"),
                    ifnull_int("unit_id"),
                ),
            ),
            default=Value(None),
        ),
        output_field=models.CharField(max_length=80, null=True),
        db_persist=True,
    )

    class Meta:
        db_table = "document_tenant_links"
        constraints = [
            models.UniqueConstraint(fields=["document", "active_key"], name="uq_dtl_document_segment"),
            models.CheckConstraint(condition=Q(link_kind__in=LinkKind.values), name="ck_dtl_kind"),
            models.CheckConstraint(
                condition=(
                    Q(link_kind="whole_document") & Q(page_from__isnull=True) & Q(page_to__isnull=True)
                )
                | (Q(link_kind="page_range") & Q(page_from__gte=1) & Q(page_to__gte=F("page_from"))),
                name="ck_dtl_pages",
            ),
            models.CheckConstraint(
                condition=Q(tenant__isnull=False)
                | Q(unit__isnull=False)
                | Q(assignment__isnull=False)
                | Q(lease__isnull=False),
                name="ck_dtl_reference",
            ),
            models.CheckConstraint(condition=Q(status__in=LinkStatus.values), name="ck_dtl_status"),
        ]
        indexes = [
            models.Index(fields=["tenant_file", "status"], name="ix_dtl_tenant_file"),
            models.Index(fields=["tenant", "period_year"], name="ix_dtl_tenant_year"),
            models.Index(fields=["unit", "period_year"], name="ix_dtl_unit_year"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} zu Mieterakte {self.tenant_file_id} ({self.link_kind})"


# ---------------------------------------------------------------- Ergaenzung Nr. 1: Regeln, Modelle, Trainingsmenge
class RuleKind(models.TextChoices):
    FILENAME = "filename", "Dateiname"
    FOLDER = "folder", "Herkunftsordner"
    KEYWORD = "keyword", "Stichwort im Text"
    ENTITY = "entity", "erkannte Entität"
    PERIOD = "period", "Zeitbezug"
    COMPOSITE = "composite", "zusammengesetzte Regel (Definition nach E 2.2)"


class ClassificationRule(TimestampedModel):
    """Regelwerk der Stufe 1 (docs/architektur.md 6.2); Ziele als Codes, nie als Ordnernamen (B-12)."""

    code = models.CharField(max_length=48, unique=True)
    name = models.CharField(max_length=120)
    rule_kind = models.CharField(max_length=24, choices=RuleKind.choices)
    pattern = models.TextField(help_text="regulärer Ausdruck oder Stichwortliste, je rule_kind")
    target_category = models.ForeignKey(
        DocumentCategory,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="target_category_code",
        related_name="+",
    )
    target_subfolder = models.ForeignKey(
        DocumentSubfolder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="target_subfolder_id",
        related_name="+",
    )
    target_document_type = models.ForeignKey(
        DocumentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="target_document_type_id",
        related_name="+",
    )
    scope_decision = models.CharField(max_length=16, choices=ScopeDecision.choices, null=True, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=4, default=1)
    is_hard = models.BooleanField(default=False, help_text="harter Treffer mit Konfidenz 1,0")
    management_types = models.JSONField(
        null=True, blank=True, help_text="Verwaltungsarten, für die die Regel gilt; leer bedeutet alle"
    )
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    version = models.PositiveSmallIntegerField(default=1, help_text="Version aus der Regeldatei (E 2.2)")
    definition = models.JSONField(
        null=True,
        blank=True,
        help_text="vollständige Regel (scope, when, then, examples) nach E 2.2; Ziele als Codes (B-12)",
    )

    class Meta:
        db_table = "classification_rules"
        ordering = ["sort_order", "code"]
        constraints = [
            models.CheckConstraint(condition=Q(rule_kind__in=RuleKind.values), name="ck_rules_kind"),
            models.CheckConstraint(condition=Q(weight__gte=0) & Q(weight__lte=1), name="ck_rules_weight"),
        ]

    def __str__(self) -> str:
        return self.code


class ClassifierModel(models.Model):
    version = models.CharField(max_length=40, unique=True)
    algorithm = models.CharField(max_length=48)
    trained_at = models.DateTimeField()
    samples_count = models.PositiveIntegerField()
    metrics = models.JSONField(null=True, blank=True)
    artifact_path = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="activated_by", related_name="+"
    )
    notes = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "classifier_models"
        ordering = ["-trained_at"]

    def __str__(self) -> str:
        return self.version


class LabelSource(models.TextChoices):
    REVIEW_DECISION = "review_decision", "Review-Entscheidung"
    SEED = "seed", "Startmenge"
    RULES_HIGH_CONFIDENCE = "rules_high_confidence", "Regelwerk mit hoher Konfidenz"
    MANUAL = "manual", "manuell"


class TrainingSample(models.Model):
    document = models.ForeignKey(
        Document,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_id",
        related_name="training_samples",
    )
    page_from = models.PositiveIntegerField(null=True, blank=True)
    page_to = models.PositiveIntegerField(null=True, blank=True)
    text_hash = models.CharField(max_length=64, null=True, blank=True)
    label_category = models.ForeignKey(
        DocumentCategory,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_category_code",
        related_name="+",
    )
    label_subfolder = models.ForeignKey(
        DocumentSubfolder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_subfolder_id",
        related_name="+",
    )
    label_document_type = models.ForeignKey(
        DocumentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="label_document_type_id",
        related_name="+",
    )
    label_scope = models.CharField(max_length=16, choices=ScopeDecision.choices, null=True, blank=True)
    label_source = models.CharField(max_length=24, choices=LabelSource.choices)
    weight = models.DecimalField(max_digits=5, decimal_places=4, default=1)
    review_decision = models.ForeignKey(
        "review.ReviewDecision",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="review_decision_id",
        related_name="+",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "training_samples"
        constraints = [
            models.CheckConstraint(condition=Q(label_source__in=LabelSource.values), name="ck_samples_source")
        ]
        indexes = [models.Index(fields=["text_hash"], name="ix_samples_text_hash")]

    def __str__(self) -> str:
        return f"Trainingsbeispiel {self.pk} ({self.label_source})"
