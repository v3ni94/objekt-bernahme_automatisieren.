"""Drive-Abbild, Abgleichsprotokoll und OAuth-Tokens (Fachentwurf D 5.1, 5.2, 10.2; Ergaenzung 5.6 Nr. 11).

drive_nodes ist der persistente Cache der Folder-IDs (CR 4, 13); jede Zeile haengt an einer Fachentitaet,
nie nur an einem Namen. Ordner werden nicht geloescht, sondern erhalten den Status missing oder trashed.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Case, Q, Value, When

from objektakte.db import ConcatWS, TimestampedModel, ifnull_int, ifnull_str

USER = settings.AUTH_USER_MODEL


class NodeKind(models.TextChoices):
    DATA_ROOT = "data_root", "Wurzel 01_Daten"
    OBJECT_ROOT = "object_root", "Objektordner"
    MAIN_FOLDER = "main_folder", "Hauptordner 01 bis 06"
    SUBFOLDER = "subfolder", "Unterordner"
    OWNER_FILE_FOLDER = "owner_file_folder", "Eigentümerakte"
    OWNER_FILE_SUBFOLDER = "owner_file_subfolder", "Unterordner der Eigentümerakte"
    TENANT_FILE_FOLDER = "tenant_file_folder", "Mieterakte"
    TENANT_FILE_SUBFOLDER = "tenant_file_subfolder", "Unterordner der Mieterakte"
    LIST_FILE = "list_file", "Listen-Datei"


class NodeStatus(models.TextChoices):
    ACTIVE = "active", "aktiv"
    MISSING = "missing", "in Drive nicht gefunden"
    TRASHED = "trashed", "im Papierkorb"


class ListType(models.TextChoices):
    OWNER_LIST = "owner_list", "Eigentümerliste"
    TENANT_LIST = "tenant_list", "Mieterliste"


class ListFormat(models.TextChoices):
    XLSX = "xlsx", "Excel"
    PDF = "pdf", "PDF"


class DriveNode(TimestampedModel):
    object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="drive_nodes",
    )
    node_kind = models.CharField(max_length=24, choices=NodeKind.choices)
    category = models.ForeignKey(
        "documents.DocumentCategory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="category_code",
        related_name="+",
    )
    subfolder = models.ForeignKey(
        "documents.DocumentSubfolder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="subfolder_id",
        related_name="+",
    )
    owner_file = models.ForeignKey(
        "parties.OwnerFile",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="owner_file_id",
        related_name="drive_nodes",
    )
    tenant_file = models.ForeignKey(
        "parties.TenantFile",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="tenant_file_id",
        related_name="drive_nodes",
    )
    list_type = models.CharField(max_length=16, choices=ListType.choices, null=True, blank=True)
    list_format = models.CharField(max_length=8, choices=ListFormat.choices, null=True, blank=True)
    parent_node = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="parent_node_id",
        related_name="children",
    )
    drive_file_id = models.CharField(max_length=128, unique=True)
    drive_parent_id = models.CharField(max_length=128, null=True, blank=True)
    drive_name = models.CharField(max_length=255)
    expected_name = models.CharField(max_length=255, null=True, blank=True)
    mime_type = models.CharField(max_length=120, null=True, blank=True)
    is_folder = models.BooleanField(default=True)
    created_by_app = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=NodeStatus.choices, default=NodeStatus.ACTIVE)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    position_key = models.GeneratedField(
        expression=Case(
            When(
                status="active",
                then=ConcatWS(
                    Value("|"),
                    models.F("node_kind"),
                    ifnull_int("object_id"),
                    ifnull_str("category_id"),
                    ifnull_int("subfolder_id"),
                    ifnull_int("owner_file_id"),
                    ifnull_int("tenant_file_id"),
                    ifnull_str("list_type"),
                    ifnull_str("list_format"),
                ),
            ),
            default=Value(None),
        ),
        output_field=models.CharField(max_length=190, null=True),
        db_persist=True,
    )

    class Meta:
        db_table = "drive_nodes"
        constraints = [
            models.UniqueConstraint(fields=["position_key"], name="uq_drive_nodes_position"),
            models.CheckConstraint(condition=Q(node_kind__in=NodeKind.values), name="ck_drive_nodes_kind"),
            models.CheckConstraint(condition=Q(status__in=NodeStatus.values), name="ck_drive_nodes_status"),
            models.CheckConstraint(
                condition=(
                    ~Q(node_kind="list_file") & Q(list_type__isnull=True) & Q(list_format__isnull=True)
                )
                | (
                    Q(node_kind="list_file")
                    & Q(list_type__in=ListType.values)
                    & Q(list_format__in=ListFormat.values)
                ),
                name="ck_drive_nodes_list",
            ),
        ]
        indexes = [models.Index(fields=["object", "node_kind"], name="ix_drive_nodes_object_kind")]

    def __str__(self) -> str:
        return f"{self.node_kind} {self.drive_name}"


class SyncRunStatus(models.TextChoices):
    RUNNING = "running", "läuft"
    DONE = "done", "fertig"
    FAILED = "failed", "fehlgeschlagen"
    ABORTED = "aborted", "abgebrochen"


class DriveSyncRun(models.Model):
    object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="sync_runs",
    )
    dry_run = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=SyncRunStatus.choices, default=SyncRunStatus.RUNNING)
    triggered_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="triggered_by", related_name="+"
    )
    root_matches = models.PositiveSmallIntegerField(null=True, blank=True)
    file_count_before = models.PositiveIntegerField(null=True, blank=True)
    file_count_after = models.PositiveIntegerField(null=True, blank=True)
    actions_planned = models.PositiveIntegerField(default=0)
    actions_executed = models.PositiveIntegerField(default=0)
    no_changes = models.BooleanField(null=True, blank=True)
    summary = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "drive_sync_runs"
        constraints = [
            models.CheckConstraint(condition=Q(status__in=SyncRunStatus.values), name="ck_sync_runs_status")
        ]
        indexes = [models.Index(fields=["object", "started_at"], name="ix_sync_runs_object")]

    def __str__(self) -> str:
        return f"Abgleich {self.pk} ({'Probelauf' if self.dry_run else 'Ausführung'})"


class SyncActionType(models.TextChoices):
    INVENTORY = "inventory", "Inventur"
    FIND_ROOT = "find_root", "Objektordner suchen"
    REGISTER_FOLDER = "register_folder", "Ordner registrieren"
    CREATE_FOLDER = "create_folder", "Ordner anlegen"
    RENAME_FOLDER = "rename_folder", "Ordner umbenennen"
    MOVE_FILE = "move_file", "Datei verschieben"
    CREATE_REVIEW = "create_review", "Review-Fall anlegen"


class DriveSyncAction(models.Model):
    sync_run = models.ForeignKey(
        DriveSyncRun, on_delete=models.PROTECT, db_column="sync_run_id", related_name="actions"
    )
    seq_no = models.PositiveIntegerField()
    action_type = models.CharField(max_length=24, choices=SyncActionType.choices)
    drive_node = models.ForeignKey(
        DriveNode,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="drive_node_id",
        related_name="+",
    )
    target_drive_id = models.CharField(max_length=128, null=True, blank=True)
    parent_drive_id = models.CharField(max_length=128, null=True, blank=True)
    name_before = models.CharField(max_length=255, null=True, blank=True)
    name_after = models.CharField(max_length=255, null=True, blank=True)
    file_count_before = models.PositiveIntegerField(null=True, blank=True)
    file_count_after = models.PositiveIntegerField(null=True, blank=True)
    id_hash_before = models.CharField(
        max_length=64, null=True, blank=True, help_text="SHA-256 der sortierten Datei-IDs vor der Umbenennung"
    )
    id_hash_after = models.CharField(max_length=64, null=True, blank=True)
    planned = models.BooleanField(default=True)
    executed = models.BooleanField(default=False)
    executed_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=16, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "drive_sync_actions"
        constraints = [
            models.UniqueConstraint(fields=["sync_run", "seq_no"], name="uq_sync_actions_seq"),
            models.CheckConstraint(
                condition=Q(action_type__in=SyncActionType.values), name="ck_sync_actions_type"
            ),
            models.CheckConstraint(
                condition=Q(result__isnull=True) | Q(result__in=["ok", "skipped", "failed"]),
                name="ck_sync_actions_result",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sync_run_id}/{self.seq_no} {self.action_type}"


class OAuthToken(TimestampedModel):
    """OAuth-Tokens des technischen Kontos, Chiffrate mit TOKEN_KEY (D 10.2, docs/architektur.md 9.6)."""

    provider = models.CharField(max_length=24, default="google")
    account_email = models.CharField(max_length=254)
    scopes = models.CharField(max_length=500)
    storage_mode = models.CharField(max_length=16, default="db")
    access_token_encrypted = models.BinaryField(null=True, blank=True)
    refresh_token_encrypted = models.BinaryField(null=True, blank=True)
    key_version = models.PositiveSmallIntegerField(null=True, blank=True)
    access_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_obtained_at = models.DateTimeField(null=True, blank=True)
    last_refresh_at = models.DateTimeField(null=True, blank=True)
    last_refresh_status = models.CharField(max_length=16, null=True, blank=True)
    last_refresh_error = models.CharField(max_length=500, null=True, blank=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=16, default="active")
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )

    class Meta:
        db_table = "oauth_tokens"
        constraints = [
            models.UniqueConstraint(fields=["provider", "account_email"], name="uq_oauth_provider_account"),
            models.CheckConstraint(
                condition=Q(storage_mode__in=["db", "docker_secret"]), name="ck_oauth_mode"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["active", "expired", "revoked"]), name="ck_oauth_status"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider} {self.account_email} ({self.status})"


class TakeoverStatus(models.TextChoices):
    NEW = "new", "neu"
    LINKED = "linked", "Objekt zugeordnet"
    DONE = "done", "übernommen"
    FAILED = "failed", "fehlgeschlagen"


class TakeoverSource(TimestampedModel):
    """Altbestand (Auftrag 11.09.2026): Quellordner der bisherigen Ablage, die ordnerweise in Objekte uebernommen
    werden. Eine Zeile je Drive-Ordner, eindeutig ueber die Folder-ID; die Tabelle ist Arbeitsliste und Nachweis.
    In Drive wird nichts geloescht, Dateien werden von der Verarbeitung nur verschoben."""

    drive_folder_id = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    detected_object_number = models.CharField(max_length=8, null=True, blank=True)
    object = models.ForeignKey(
        "objects.ManagedObject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="object_id",
        related_name="takeover_sources",
    )
    status = models.CharField(max_length=16, choices=TakeoverStatus.choices, default=TakeoverStatus.NEW)
    files_registered = models.PositiveIntegerField(default=0)
    files_skipped = models.PositiveIntegerField(default=0)
    last_run_id = models.PositiveIntegerField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, null=True, blank=True)
    created_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="created_by", related_name="+"
    )

    class Meta:
        db_table = "takeover_sources"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=TakeoverStatus.values), name="ck_takeover_sources_status"
            )
        ]

    def __str__(self) -> str:
        return f"Altbestand {self.name or self.drive_folder_id}"

    @property
    def numeric(self) -> int | None:
        return int(self.detected_object_number) if self.detected_object_number else None
