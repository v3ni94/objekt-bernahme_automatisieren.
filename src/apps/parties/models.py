"""Eigentuemer, Mieter, Zuordnungen, Akten und Feldherkunft (Fachentwurf D 3.3 bis 3.10; Ergaenzung 5.6 Nr. 8, 13, 19).

Eigentum und Miete bestehen ausschliesslich aus zeitlich gueltigen Zuordnungszeilen (D Entscheidung 1).
IBAN: nur iban_last4 und iban_hash (HMAC), iban_encrypted bleibt leer, solange security.store_full_iban
falsch ist (Beschluss B-18). Ueberlappungspruefung und Stichtagsabfrage in apps.parties.services.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Case, F, Q, Value, When
from django.db.models.functions import Coalesce

from objektakte.db import (
    DATA_STATUS_CHOICES,
    DATA_STATUS_VALUES,
    ActiveManager,
    SoftDeleteModel,
    VarBinaryField,
)

USER = settings.AUTH_USER_MODEL


class PartyType(models.TextChoices):
    NATURAL_PERSON = "natural_person", "natürliche Person"
    LEGAL_ENTITY = "legal_entity", "juristische Person"
    COMMUNITY = "community", "Gemeinschaft"


class PartyBase(SoftDeleteModel):
    """Gemeinsame Spalten von owners und tenants (D 3.3, 3.6)."""

    type = models.CharField(max_length=16, choices=PartyType.choices)
    salutation = models.CharField(max_length=40, null=True, blank=True)
    first_name = models.CharField(max_length=120, null=True, blank=True)
    last_name = models.CharField(max_length=120, null=True, blank=True)
    company_name = models.CharField(max_length=200, null=True, blank=True)
    short_name = models.CharField(
        max_length=60, null=True, blank=True, help_text="Firmenkurzname für die Ordnerbenennung (B-20)"
    )
    search_name = models.CharField(max_length=240)
    email = models.CharField(max_length=254, null=True, blank=True)
    phone = models.CharField(max_length=40, null=True, blank=True)
    mobile = models.CharField(max_length=40, null=True, blank=True)
    iban_last4 = models.CharField(max_length=4, null=True, blank=True)
    iban_encrypted = VarBinaryField(max_length=160, null=True, blank=True)
    iban_key_version = models.PositiveSmallIntegerField(null=True, blank=True)
    iban_hash = VarBinaryField(max_length=32, null=True, blank=True)
    sepa_mandate_present = models.BooleanField(null=True, blank=True)
    sepa_mandate_reference = models.CharField(max_length=35, null=True, blank=True)
    notes = models.CharField(max_length=500, null=True, blank=True)
    data_status = models.CharField(max_length=16, choices=DATA_STATUS_CHOICES, default="incomplete")

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        if self.type == PartyType.NATURAL_PERSON:
            return " ".join(p for p in [self.first_name, self.last_name] if p) or self.search_name
        return self.company_name or self.last_name or self.search_name

    @property
    def iban_masked(self) -> str:
        """Anzeigeform nach D 3.3, gebildet aus iban_last4; nie aus einem Klartext."""
        return f"**** **** **** **** **{self.iban_last4[:2]} {self.iban_last4[2:]}" if self.iban_last4 else ""


class Owner(PartyBase):
    correspondence_street = models.CharField(max_length=120, null=True, blank=True)
    correspondence_house_number = models.CharField(max_length=20, null=True, blank=True)
    correspondence_postal_code = models.CharField(max_length=10, null=True, blank=True)
    correspondence_city = models.CharField(max_length=80, null=True, blank=True)
    correspondence_country = models.CharField(max_length=2, null=True, blank=True)
    correspondence_addition = models.CharField(max_length=120, null=True, blank=True)
    delivery_street = models.CharField(max_length=120, null=True, blank=True)
    delivery_house_number = models.CharField(max_length=20, null=True, blank=True)
    delivery_postal_code = models.CharField(max_length=10, null=True, blank=True)
    delivery_city = models.CharField(max_length=80, null=True, blank=True)
    delivery_country = models.CharField(max_length=2, null=True, blank=True)
    delivery_addition = models.CharField(max_length=120, null=True, blank=True)

    class Meta:
        db_table = "owners"
        ordering = ["search_name"]
        indexes = [
            models.Index(fields=["search_name"], name="ix_owners_search_name"),
            models.Index(fields=["last_name"], name="ix_owners_last_name"),
            models.Index(fields=["company_name"], name="ix_owners_company"),
            models.Index(fields=["iban_hash"], name="ix_owners_iban_hash"),
            models.Index(fields=["email"], name="ix_owners_email"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(type__in=PartyType.values), name="ck_owners_type"),
            models.CheckConstraint(
                condition=Q(data_status__in=DATA_STATUS_VALUES), name="ck_owners_data_status"
            ),
            models.CheckConstraint(
                condition=(Q(iban_encrypted__isnull=True) & Q(iban_key_version__isnull=True))
                | (Q(iban_encrypted__isnull=False) & Q(iban_key_version__isnull=False)),
                name="ck_owners_iban_pair",
            ),
        ]


class OwnerUnitAssignment(SoftDeleteModel):
    owner = models.ForeignKey(
        Owner, on_delete=models.PROTECT, db_column="owner_id", related_name="assignments"
    )
    unit = models.ForeignKey(
        "objects.Unit", on_delete=models.PROTECT, db_column="unit_id", related_name="owner_assignments"
    )
    valid_from = models.DateField(
        null=True, blank=True, help_text="Eigentumsbeginn einschließlich, leer bedeutet unbekannt"
    )
    valid_to = models.DateField(
        null=True, blank=True, help_text="Eigentumsende einschließlich, leer bedeutet aktuell"
    )
    share = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    source_document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_document_id",
        related_name="+",
    )
    source_import_row = models.ForeignKey(
        "imports.ImportRow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_import_row_id",
        related_name="+",
    )
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    data_status = models.CharField(max_length=16, choices=DATA_STATUS_CHOICES, default="incomplete")
    confirmed_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="confirmed_by", related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    balance_at_takeover = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.CharField(max_length=500, null=True, blank=True)
    is_current = models.GeneratedField(
        expression=Case(When(valid_to__isnull=True, then=Value(True)), default=Value(False)),
        output_field=models.BooleanField(),
        db_persist=True,
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "owner_unit_assignments"
        ordering = ["unit_id", "valid_from"]
        indexes = [
            models.Index(fields=["unit", "valid_from", "valid_to"], name="ix_oua_unit_period"),
            models.Index(fields=["owner", "valid_from"], name="ix_oua_owner_period"),
            models.Index(fields=["unit", "is_current"], name="ix_oua_unit_current"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(valid_from__isnull=True)
                | Q(valid_to__isnull=True)
                | Q(valid_from__lte=F("valid_to")),
                name="ck_oua_period",
            ),
            models.CheckConstraint(
                condition=Q(share__isnull=True) | (Q(share__gt=0) & Q(share__lte=1)), name="ck_oua_share"
            ),
            models.CheckConstraint(
                condition=Q(data_status__in=DATA_STATUS_VALUES), name="ck_oua_data_status"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.owner_id} an {self.unit_id} ({self.valid_from or '?'} bis {self.valid_to or 'heute'})"


class OwnerFileKind(models.TextChoices):
    UNIT_OWNER = "unit_owner", "Einheit und Eigentümer"
    OBJECT_OWNER = (
        "object_owner",
        "Eigentümer des Objekts",
    )  # Mietverwaltung: eine Akte je Objekt (12.09.2026)
    UNKNOWN_UNIT = "unknown_unit", "Einheit unbekannt"
    UNASSIGNED = "unassigned", "Unzugeordnet"


class OwnerFile(SoftDeleteModel):
    object = models.ForeignKey(
        "objects.ManagedObject", on_delete=models.PROTECT, db_column="object_id", related_name="owner_files"
    )
    unit = models.ForeignKey(
        "objects.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="unit_id",
        related_name="owner_files",
    )
    owner = models.ForeignKey(
        Owner,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="owner_id",
        related_name="unknown_unit_files",
        help_text="nur bei file_kind unknown_unit (B-24)",
    )
    file_kind = models.CharField(
        max_length=16, choices=OwnerFileKind.choices, default=OwnerFileKind.UNIT_OWNER
    )
    folder_name = models.CharField(max_length=255)
    name_basis = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, default="active")
    active_key = models.GeneratedField(
        expression=Case(When(deleted_at__isnull=True, then=F("folder_name")), default=Value(None)),
        output_field=models.CharField(max_length=255, null=True),
        db_persist=True,
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "owner_files"
        constraints = [
            models.UniqueConstraint(fields=["object", "active_key"], name="uq_owner_files_name_active"),
            models.CheckConstraint(
                condition=Q(file_kind__in=OwnerFileKind.values), name="ck_owner_files_kind"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["active", "closed"]), name="ck_owner_files_status"
            ),
        ]
        indexes = [models.Index(fields=["unit"], name="ix_owner_files_unit")]

    def __str__(self) -> str:
        return self.folder_name


class OwnerFileAssignment(models.Model):
    owner_file = models.ForeignKey(
        OwnerFile, on_delete=models.PROTECT, db_column="owner_file_id", related_name="file_assignments"
    )
    assignment = models.OneToOneField(
        OwnerUnitAssignment,
        on_delete=models.PROTECT,
        db_column="assignment_id",
        related_name="owner_file_assignment",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "owner_file_assignments"

    def __str__(self) -> str:
        return f"Akte {self.owner_file_id}: Zuordnung {self.assignment_id}"


class Tenant(PartyBase):
    class Meta:
        db_table = "tenants"
        ordering = ["search_name"]
        indexes = [
            models.Index(fields=["search_name"], name="ix_tenants_search_name"),
            models.Index(fields=["last_name"], name="ix_tenants_last_name"),
            models.Index(fields=["iban_hash"], name="ix_tenants_iban_hash"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(type__in=["natural_person", "legal_entity"]), name="ck_tenants_type"
            ),
            models.CheckConstraint(
                condition=Q(data_status__in=DATA_STATUS_VALUES), name="ck_tenants_data_status"
            ),
            models.CheckConstraint(
                condition=(Q(iban_encrypted__isnull=True) & Q(iban_key_version__isnull=True))
                | (Q(iban_encrypted__isnull=False) & Q(iban_key_version__isnull=False)),
                name="ck_tenants_iban_pair",
            ),
        ]


class DepositType(models.TextChoices):
    CASH_ACCOUNT = "cash_account", "Kautionskonto"
    SAVINGS_BOOK = "savings_book", "Sparbuch"
    BANK_GUARANTEE = "bank_guarantee", "Bankbürgschaft"
    PLEDGE = "pledge", "Verpfändung"
    INSURANCE = "insurance", "Kautionsversicherung"
    OTHER = "other", "sonstige"
    UNKNOWN = "unknown", "unbekannt"


class RentAdjustmentType(models.TextChoices):
    NONE = "none", "keine"
    GRADUATED = "graduated", "Staffel"
    INDEXED = "indexed", "Index"
    UNKNOWN = "unknown", "unbekannt"


_ZERO = Value(Decimal("0"), output_field=models.DecimalField(max_digits=12, decimal_places=2))


class Lease(SoftDeleteModel):
    object = models.ForeignKey(
        "objects.ManagedObject", on_delete=models.PROTECT, db_column="object_id", related_name="leases"
    )
    lease_reference = models.CharField(max_length=60, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    base_rent = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    utilities_prepayment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    heating_prepayment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_rent = models.GeneratedField(
        expression=Case(
            When(base_rent__isnull=True, then=Value(None)),
            default=F("base_rent")
            + Coalesce(F("utilities_prepayment"), _ZERO)
            + Coalesce(F("heating_prepayment"), _ZERO),
        ),
        output_field=models.DecimalField(max_digits=12, decimal_places=2, null=True),
        db_persist=True,
    )
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    deposit_type = models.CharField(max_length=24, choices=DepositType.choices, null=True, blank=True)
    deposit_notes = models.CharField(max_length=255, null=True, blank=True)
    rent_adjustment_type = models.CharField(
        max_length=16, choices=RentAdjustmentType.choices, default=RentAdjustmentType.UNKNOWN
    )
    persons_count = models.PositiveSmallIntegerField(null=True, blank=True)
    source_document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_document_id",
        related_name="+",
    )
    source_import_row = models.ForeignKey(
        "imports.ImportRow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_import_row_id",
        related_name="+",
    )
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    data_status = models.CharField(max_length=16, choices=DATA_STATUS_CHOICES, default="incomplete")
    notes = models.CharField(max_length=500, null=True, blank=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "leases"
        indexes = [models.Index(fields=["object", "start_date"], name="ix_leases_object_start")]
        constraints = [
            models.CheckConstraint(
                condition=Q(start_date__isnull=True)
                | Q(end_date__isnull=True)
                | Q(start_date__lte=F("end_date")),
                name="ck_leases_period",
            ),
            models.CheckConstraint(
                condition=(Q(base_rent__isnull=True) | Q(base_rent__gte=0))
                & (Q(utilities_prepayment__isnull=True) | Q(utilities_prepayment__gte=0))
                & (Q(heating_prepayment__isnull=True) | Q(heating_prepayment__gte=0))
                & (Q(deposit_amount__isnull=True) | Q(deposit_amount__gte=0)),
                name="ck_leases_amounts",
            ),
            models.CheckConstraint(
                condition=Q(deposit_type__isnull=True) | Q(deposit_type__in=DepositType.values),
                name="ck_leases_deposit_type",
            ),
            models.CheckConstraint(
                condition=Q(rent_adjustment_type__in=RentAdjustmentType.values), name="ck_leases_adjustment"
            ),
            models.CheckConstraint(
                condition=Q(data_status__in=DATA_STATUS_VALUES), name="ck_leases_data_status"
            ),
        ]

    def __str__(self) -> str:
        return self.lease_reference or f"Mietverhältnis {self.pk}"


class TenantRole(models.TextChoices):
    TENANT = "tenant", "Mieter"
    CO_TENANT = "co_tenant", "Mitmieter"
    GUARANTOR = "guarantor", "Bürge"


class TenantUnitAssignment(SoftDeleteModel):
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, db_column="tenant_id", related_name="assignments"
    )
    unit = models.ForeignKey(
        "objects.Unit", on_delete=models.PROTECT, db_column="unit_id", related_name="tenant_assignments"
    )
    lease = models.ForeignKey(
        Lease,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="lease_id",
        related_name="assignments",
    )
    role = models.CharField(max_length=16, choices=TenantRole.choices, default=TenantRole.TENANT)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    source_document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_document_id",
        related_name="+",
    )
    source_import_row = models.ForeignKey(
        "imports.ImportRow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_import_row_id",
        related_name="+",
    )
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    data_status = models.CharField(max_length=16, choices=DATA_STATUS_CHOICES, default="incomplete")
    confirmed_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="confirmed_by", related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=500, null=True, blank=True)
    is_current = models.GeneratedField(
        expression=Case(When(valid_to__isnull=True, then=Value(True)), default=Value(False)),
        output_field=models.BooleanField(),
        db_persist=True,
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "tenant_unit_assignments"
        indexes = [
            models.Index(fields=["unit", "valid_from", "valid_to"], name="ix_tua_unit_period"),
            models.Index(fields=["tenant", "valid_from"], name="ix_tua_tenant_period"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(role__in=TenantRole.values), name="ck_tua_role"),
            models.CheckConstraint(
                condition=Q(valid_from__isnull=True)
                | Q(valid_to__isnull=True)
                | Q(valid_from__lte=F("valid_to")),
                name="ck_tua_period",
            ),
            models.CheckConstraint(
                condition=Q(data_status__in=DATA_STATUS_VALUES), name="ck_tua_data_status"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tenant_id} in {self.unit_id}"


class TenantFile(SoftDeleteModel):
    object = models.ForeignKey(
        "objects.ManagedObject", on_delete=models.PROTECT, db_column="object_id", related_name="tenant_files"
    )
    unit = models.ForeignKey(
        "objects.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="unit_id",
        related_name="tenant_files",
    )
    file_kind = models.CharField(max_length=16, default="unit_tenant")
    folder_name = models.CharField(max_length=255)
    name_basis = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, default="active")
    active_key = models.GeneratedField(
        expression=Case(When(deleted_at__isnull=True, then=F("folder_name")), default=Value(None)),
        output_field=models.CharField(max_length=255, null=True),
        db_persist=True,
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "tenant_files"
        constraints = [
            models.UniqueConstraint(fields=["object", "active_key"], name="uq_tenant_files_name_active"),
            models.CheckConstraint(
                condition=Q(file_kind__in=["unit_tenant", "unknown_unit", "unassigned"]),
                name="ck_tenant_files_kind",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["active", "closed"]), name="ck_tenant_files_status"
            ),
        ]
        indexes = [models.Index(fields=["unit"], name="ix_tenant_files_unit")]

    def __str__(self) -> str:
        return self.folder_name


class TenantFileAssignment(models.Model):
    tenant_file = models.ForeignKey(
        TenantFile, on_delete=models.PROTECT, db_column="tenant_file_id", related_name="file_assignments"
    )
    assignment = models.OneToOneField(
        TenantUnitAssignment,
        on_delete=models.PROTECT,
        db_column="assignment_id",
        related_name="tenant_file_assignment",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_file_assignments"

    def __str__(self) -> str:
        return f"Akte {self.tenant_file_id}: Zuordnung {self.assignment_id}"


class ProvenanceEntity(models.TextChoices):
    OWNER = "owner", "Eigentümer"
    UNIT = "unit", "Einheit"
    OWNER_UNIT_ASSIGNMENT = "owner_unit_assignment", "Eigentümerzuordnung"
    TENANT = "tenant", "Mieter"
    LEASE = "lease", "Mietverhältnis"
    TENANT_UNIT_ASSIGNMENT = "tenant_unit_assignment", "Mieterzuordnung"
    OBJECT = "object", "Objekt"


class SourceKind(models.TextChoices):
    DOCUMENT = "document", "Dokument"
    IMPORT_ROW = "import_row", "Importzeile"
    MANUAL = "manual", "manuell"
    AI = "ai", "KI"
    SYSTEM = "system", "System"


class FieldProvenance(models.Model):
    """Herkunft und Bestaetigungsstatus je Stammdatenfeld (D 3.10). Polymorpher Verweis ohne Fremdschluessel."""

    entity_type = models.CharField(max_length=32, choices=ProvenanceEntity.choices)
    entity_id = models.PositiveBigIntegerField()
    field_name = models.CharField(max_length=64)
    source_kind = models.CharField(max_length=16, choices=SourceKind.choices)
    source_document = models.ForeignKey(
        "documents.Document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_document_id",
        related_name="+",
    )
    source_page_from = models.PositiveIntegerField(null=True, blank=True)
    source_page_to = models.PositiveIntegerField(null=True, blank=True)
    source_import_row = models.ForeignKey(
        "imports.ImportRow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_column="source_import_row_id",
        related_name="+",
    )
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=16, choices=DATA_STATUS_CHOICES)
    set_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, db_column="set_by", related_name="+"
    )
    set_at = models.DateTimeField()

    class Meta:
        db_table = "field_provenance"
        constraints = [
            models.UniqueConstraint(
                fields=["entity_type", "entity_id", "field_name"], name="uq_provenance_field"
            ),
            models.CheckConstraint(condition=Q(source_kind__in=SourceKind.values), name="ck_provenance_kind"),
            models.CheckConstraint(condition=Q(status__in=DATA_STATUS_VALUES), name="ck_provenance_status"),
            models.CheckConstraint(
                condition=Q(source_page_from__isnull=True)
                | Q(source_page_to__isnull=True)
                | Q(source_page_from__lte=F("source_page_to")),
                name="ck_provenance_pages",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}#{self.entity_id}.{self.field_name}: {self.status}"
