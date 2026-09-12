"""Datenmodell der Synchronisation (Auftrag Paperless-ngx und Google Drive, 12.09.2026).

Trennung nach dem Auftrag: fachliches Dokument (documents.Document, jetzt mit uuid), Dateiversion
(DocumentVersion), Dateidarstellung und Speicherort (ExternalLink je System und Rolle), Operationsliste
(SyncOperation), Cursor der Änderungsprotokolle (SyncCursor), Bestandsmanifest (InventoryRun, InventoryItem),
Lernbeispiele und Regeln der Objektzuordnung (AssignmentExample, AssignmentRule). Dateien liegen nie als
BLOB in der Datenbank; Konflikte und Zuordnungsvorschläge sind Review-Fälle (review.ReviewCase)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from objektakte.db import TimestampedModel

USER = settings.AUTH_USER_MODEL


class SyncSystem(models.TextChoices):
    PAPERLESS = "paperless", "Paperless-ngx"
    DRIVE = "drive", "Google Drive"
    APP = "app", "Anwendung"


class LinkRole(models.TextChoices):
    ORIGINAL = "original", "Original"
    ARCHIVE = "archive", "OCR-Archivfassung"
    SNAPSHOT = "snapshot", "Exportfassung (Snapshot)"
    INDEX_STUB = "index_stub", "Indexbeleg"


class LinkState(models.TextChoices):
    PENDING = "pending", "vorgemerkt"
    LINKED = "linked", "verknüpft"
    SYNCED = "synced", "abgeglichen"
    CHANGED_REMOTE = "changed_remote", "extern geändert"
    CONFLICT = "conflict", "Konflikt"
    MISSING = "missing", "extern nicht gefunden"
    TRASHED = "trashed", "im Papierkorb"
    NO_ACCESS = "no_access", "kein Zugriff"
    TOMBSTONE = "tombstone", "Löschung bestätigt"


class DocumentVersion(TimestampedModel):
    """Inhaltsstand eines Dokuments (Original-Hash je Stand). Umbenennung oder Objektkorrektur erzeugt keine
    neue Version, nur eine echte Inhaltsänderung."""

    document = models.ForeignKey(
        "documents.Document", on_delete=models.PROTECT, db_column="document_id", related_name="versions"
    )
    version_no = models.PositiveSmallIntegerField()
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    original_name = models.CharField(max_length=255, null=True, blank=True)
    source_system = models.CharField(max_length=16, choices=SyncSystem.choices)
    source_external_id = models.CharField(max_length=128, null=True, blank=True)
    source_version = models.CharField(max_length=128, null=True, blank=True)
    label = models.CharField(max_length=64, null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )

    class Meta:
        db_table = "document_versions"
        constraints = [
            models.UniqueConstraint(fields=["document", "version_no"], name="uq_doc_versions_no"),
            models.UniqueConstraint(fields=["document", "sha256"], name="uq_doc_versions_hash"),
            models.CheckConstraint(
                condition=Q(source_system__in=SyncSystem.values), name="ck_doc_versions_source"
            ),
        ]

    def __str__(self) -> str:
        return f"Version {self.version_no} von Dokument {self.document_id}"


class ExternalLink(TimestampedModel):
    """Speicherort und Dateidarstellung eines Dokuments in einem externen System. Ein externes Objekt
    (system, external_id, role) gehört genau einem Dokument; ein Dokument hat je System und Rolle höchstens
    einen Eintrag. synced_fields ist der zuletzt gemeinsam abgeglichene Stand (Basis für Konflikterkennung)."""

    document = models.ForeignKey(
        "documents.Document", on_delete=models.PROTECT, db_column="document_id", related_name="sync_links"
    )
    version = models.ForeignKey(
        DocumentVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="version_id",
        related_name="links",
    )
    system = models.CharField(max_length=16, choices=SyncSystem.choices)
    role = models.CharField(max_length=16, choices=LinkRole.choices, default=LinkRole.ORIGINAL)
    external_id = models.CharField(max_length=128)
    external_version = models.CharField(max_length=128, null=True, blank=True)
    parent_external_id = models.CharField(max_length=128, null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, null=True, blank=True)
    checksum_md5 = models.CharField(max_length=32, null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    remote_modified_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    synced_fields = models.JSONField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=LinkState.choices, default=LinkState.PENDING)
    state_reason = models.CharField(max_length=255, null=True, blank=True)
    tombstone_at = models.DateTimeField(null=True, blank=True)
    tombstone_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="tombstone_by", related_name="+"
    )

    class Meta:
        db_table = "sync_links"
        constraints = [
            models.UniqueConstraint(fields=["system", "external_id", "role"], name="uq_sync_links_external"),
            models.UniqueConstraint(fields=["document", "system", "role"], name="uq_sync_links_document"),
            models.CheckConstraint(condition=Q(system__in=SyncSystem.values), name="ck_sync_links_system"),
            models.CheckConstraint(condition=Q(role__in=LinkRole.values), name="ck_sync_links_role"),
            models.CheckConstraint(condition=Q(state__in=LinkState.values), name="ck_sync_links_state"),
        ]
        indexes = [
            models.Index(fields=["system", "state"], name="ix_sync_links_state"),
            models.Index(fields=["checksum_sha256"], name="ix_sync_links_sha"),
        ]

    def __str__(self) -> str:
        return f"{self.system}:{self.external_id} ({self.role})"


class OperationKind(models.TextChoices):
    PAPERLESS_PUSH = "paperless_push", "Datei nach Paperless übertragen"
    PAPERLESS_AWAIT_TASK = "paperless_await_task", "Paperless-Aufgabe verfolgen"
    PAPERLESS_PUSH_META = "paperless_push_meta", "Metadaten nach Paperless schreiben"
    PAPERLESS_PULL = "paperless_pull", "Dokument aus Paperless übernehmen"
    PAPERLESS_PULL_META = "paperless_pull_meta", "Metadaten aus Paperless lesen"
    PAPERLESS_INDEX_STUB = "paperless_index_stub", "Indexbeleg in Paperless anlegen"
    DRIVE_SET_PROPS = "drive_set_props", "Drive-Kennzeichen setzen"
    DRIVE_EXPORT_SNAPSHOT = "drive_export_snapshot", "Google-Dokument exportieren"
    DRIVE_REGISTER = "drive_register", "Drive-Datei registrieren"
    ASSIGN_OBJECT = "assign_object", "Objekt zuordnen"


class OperationStatus(models.TextChoices):
    PENDING = "pending", "wartet"
    RUNNING = "running", "läuft"
    DONE = "done", "fertig"
    FAILED = "failed", "fehlgeschlagen"
    BLOCKED = "blocked", "blockiert"
    CANCELLED = "cancelled", "verworfen"
    SKIPPED = "skipped", "übersprungen"


class SyncOperation(TimestampedModel):
    """Dauerhafte Operationsliste. op_key ist der Idempotenzschlüssel (Art, Dokument, Quellrevision); eine
    erneut zugestellte Nachricht erzeugt keine zweite Operation. Verarbeitung über reserve (UPDATE pending zu
    running), begrenzte Wiederholungen mit zunehmender Wartezeit, nicht lösbare Operationen bleiben sichtbar."""

    op_key = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=32, choices=OperationKind.choices)
    system = models.CharField(max_length=16, choices=SyncSystem.choices)
    document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="document_id",
        related_name="sync_operations",
    )
    link = models.ForeignKey(
        ExternalLink,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="link_id",
        related_name="operations",
    )
    source_system = models.CharField(max_length=16, choices=SyncSystem.choices, null=True, blank=True)
    source_revision = models.CharField(max_length=128, null=True, blank=True)
    desired_state = models.JSONField(null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=OperationStatus.choices, default=OperationStatus.PENDING)
    priority = models.SmallIntegerField(default=100)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=80, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, null=True, blank=True)
    blocked_reason = models.CharField(max_length=255, null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )

    class Meta:
        db_table = "sync_operations"
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=OperationKind.values), name="ck_sync_ops_kind"),
            models.CheckConstraint(condition=Q(status__in=OperationStatus.values), name="ck_sync_ops_status"),
            models.CheckConstraint(condition=Q(system__in=SyncSystem.values), name="ck_sync_ops_system"),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="ix_sync_ops_due"),
            models.Index(fields=["document", "kind"], name="ix_sync_ops_document"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.op_key} ({self.status})"


class SyncCursor(TimestampedModel):
    """Dauerhafte Cursor der Änderungsprotokolle (Drive startPageToken, Paperless modified-Stand) und
    Verbindungsbefunde (Serverversion, Schema, Feld-IDs)."""

    system = models.CharField(max_length=16, choices=SyncSystem.choices)
    name = models.CharField(max_length=48)
    value = models.TextField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "sync_cursors"
        constraints = [
            models.UniqueConstraint(fields=["system", "name"], name="uq_sync_cursors_name"),
            models.CheckConstraint(condition=Q(system__in=SyncSystem.values), name="ck_sync_cursors_system"),
        ]

    def __str__(self) -> str:
        return f"{self.system}.{self.name}"


class InventoryStatus(models.TextChoices):
    PLANNED = "planned", "geplant"
    RUNNING = "running", "läuft"
    PAUSED = "paused", "pausiert"
    DONE = "done", "abgeschlossen"
    FAILED = "failed", "fehlgeschlagen"
    ABORTED = "aborted", "abgebrochen"


class InventoryRun(TimestampedModel):
    """Wiederaufnehmbarer Bestandslauf: Inventar beider Systeme, Trockenlauf, Pilotumfang, Fortschritt in
    page_state, Ausgangscursor vor dem Inventar (cursor_before), Zähler in counters."""

    kind = models.CharField(max_length=16, choices=SyncSystem.choices)
    dry_run = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=InventoryStatus.choices, default=InventoryStatus.PLANNED)
    scope = models.JSONField(null=True, blank=True)
    cursor_before = models.JSONField(null=True, blank=True)
    page_state = models.JSONField(null=True, blank=True)
    counters = models.JSONField(null=True, blank=True)
    started_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="started_by", related_name="+"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = "sync_inventory_runs"
        constraints = [
            models.CheckConstraint(condition=Q(status__in=InventoryStatus.values), name="ck_sync_inv_status"),
            models.CheckConstraint(condition=Q(kind__in=SyncSystem.values), name="ck_sync_inv_kind"),
        ]

    def __str__(self) -> str:
        return f"Bestandslauf {self.pk} {self.kind} ({self.status})"


class Disposition(models.TextChoices):
    PENDING = "pending", "offen"
    LINK_EXISTING = "link_existing", "vorhandenes Dokument verknüpft"
    IMPORT_NEW = "import_new", "neu übernommen"
    DUPLICATE = "duplicate", "Dublette verknüpft"
    EXPORT_SNAPSHOT = "export_snapshot", "Exportfassung"
    INDEX_STUB = "index_stub", "Indexbeleg"
    UNSUPPORTED = "unsupported", "nicht übertragbar"
    OUT_OF_SCOPE = "out_of_scope", "außerhalb des Umfangs"
    IN_PROGRESS = "in_progress", "in Verarbeitung"
    ERROR = "error", "Fehler"


class InventoryItem(TimestampedModel):
    """Bestandsmanifest: eine Zeile je gefundener Datei mit Quell-ID, Ziel, Prüfsumme, Objektbezug, Stand und
    Fehlergrund. Wird vor der Fortschrittsbestätigung des Laufs geschrieben."""

    run = models.ForeignKey(InventoryRun, on_delete=models.CASCADE, db_column="run_id", related_name="items")
    system = models.CharField(max_length=16, choices=SyncSystem.choices)
    external_id = models.CharField(max_length=128)
    parent_external_id = models.CharField(max_length=128, null=True, blank=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, null=True, blank=True)
    checksum_md5 = models.CharField(max_length=32, null=True, blank=True)
    remote_modified_at = models.DateTimeField(null=True, blank=True)
    object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="+",
    )
    document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="document_id",
        related_name="+",
    )
    operation = models.ForeignKey(
        SyncOperation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="operation_id",
        related_name="+",
    )
    disposition = models.CharField(max_length=16, choices=Disposition.choices, default=Disposition.PENDING)
    target_external_id = models.CharField(max_length=128, null=True, blank=True)
    error_message = models.CharField(max_length=500, null=True, blank=True)
    details = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "sync_inventory_items"
        constraints = [
            models.UniqueConstraint(fields=["run", "system", "external_id"], name="uq_sync_inv_items"),
            models.CheckConstraint(condition=Q(disposition__in=Disposition.values), name="ck_sync_inv_disp"),
        ]
        indexes = [models.Index(fields=["run", "disposition"], name="ix_sync_inv_items_disp")]

    def __str__(self) -> str:
        return f"{self.system}:{self.external_id} {self.disposition}"


class ExampleKind(models.TextChoices):
    CONFIRM = "confirm", "Vorschlag bestätigt"
    CORRECT = "correct", "Vorschlag korrigiert"
    REJECT = "reject", "Vorschlag verworfen"


class ExampleSource(models.TextChoices):
    HUMAN = "human", "manuelle Bestätigung"
    AUTO_VERIFIED = "auto_verified", "automatisch, später bestätigt"
    IMPORT_UNVERIFIED = "import_unverified", "Altbestand, ungeprüft"


class AssignmentExample(TimestampedModel):
    """Lernbeispiel der Objektzuordnung: vorherige Entscheidung, richtiges Ziel, Person, Zeitpunkt, Merkmale
    und Regelversion; negative Beispiele (reject, correct) bleiben erhalten."""

    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.PROTECT,
        db_column="document_id",
        related_name="assignment_examples",
    )
    kind = models.CharField(max_length=16, choices=ExampleKind.choices)
    source = models.CharField(max_length=24, choices=ExampleSource.choices, default=ExampleSource.HUMAN)
    previous_object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="previous_object_id",
        related_name="+",
    )
    target_object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="target_object_id",
        related_name="+",
    )
    proposed_object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="proposed_object_id",
        related_name="+",
    )
    decided_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="decided_by", related_name="+"
    )
    decided_at = models.DateTimeField()
    features = models.JSONField(null=True, blank=True)
    evidence = models.JSONField(null=True, blank=True)
    rule_version = models.CharField(max_length=40, null=True, blank=True)
    review_case = models.ForeignKey(
        "review.ReviewCase",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="review_case_id",
        related_name="+",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "sync_assignment_examples"
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=ExampleKind.values), name="ck_sync_examples_kind"),
            models.CheckConstraint(
                condition=Q(source__in=ExampleSource.values), name="ck_sync_examples_source"
            ),
        ]
        indexes = [models.Index(fields=["target_object", "kind"], name="ix_sync_examples_target")]

    def __str__(self) -> str:
        return f"Beispiel {self.kind} Dokument {self.document_id}"


class AssignmentRule(TimestampedModel):
    """Versionierte, begrenzte und abschaltbare Zuordnungsregel aus bestätigten Beispielen (zum Beispiel
    Lieferant plus objektspezifische Vertragsnummer). Eine Regel entsteht erst aus mehreren Bestätigungen und
    sortiert bestätigte Altdokumente nie um."""

    code = models.CharField(max_length=64)
    version = models.PositiveSmallIntegerField(default=1)
    kind = models.CharField(max_length=32, default="feature_combo")
    conditions = models.JSONField()
    object = models.ForeignKey(
        "objects.ManagedObject",
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="assignment_rules",
    )
    is_active = models.BooleanField(default=True)
    created_from = models.JSONField(null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )
    hit_count = models.PositiveIntegerField(default=0)
    last_hit_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="deactivated_by", related_name="+"
    )
    notes = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = "sync_assignment_rules"
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="uq_sync_rules_version")]
        indexes = [models.Index(fields=["is_active", "kind"], name="ix_sync_rules_active")]

    def __str__(self) -> str:
        return f"Regel {self.code} v{self.version}"
