"""Verwaltungsobjekte und Einheiten (Fachentwurf D 3.1, 3.2; Ergaenzung docs/architektur.md 5.6 Nr. 12, 13).

Objektnummer: Ziffernfolge wie erfasst (2 bis 6 Stellen, Konfiguration drive.object_number_digits_*),
Eindeutigkeit ueber den Zahlenwert (082 und 82 sind dasselbe Objekt, D 3.1). Einheiten: unit_label frei,
unit_label_normalized fuer den Dublettenschutz (apps.objects.units).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Case, F, Q, Value, When
from django.db.models.functions import Cast

from objektakte.db import DATA_STATUS_CHOICES, DATA_STATUS_VALUES, ActiveManager, SoftDeleteModel


class ManagementType(models.TextChoices):
    WEG = "weg", "WEG"
    RENTAL = "rental", "Mietverwaltung"
    WEG_WITH_SE = "weg_with_se", "WEG mit Sondereigentumsverwaltung"


class ObjectStatus(models.TextChoices):
    NEW = "new", "neu"
    TAKEOVER = "takeover", "in Übernahme"
    ACTIVE = "active", "aktiv"
    ARCHIVED = "archived", "archiviert"


class UnitType(models.TextChoices):
    APARTMENT = "apartment", "Wohnung"
    COMMERCIAL = "commercial", "Gewerbe"
    PARKING = "parking", "Stellplatz"
    GARAGE = "garage", "Garage"
    UNDERGROUND_PARKING = "underground_parking", "Tiefgarage"
    CELLAR = "cellar", "Keller"
    OTHER = "other", "Sonstige"


class ManagedObject(SoftDeleteModel):
    """Tabelle objects. Der Klassenname vermeidet die Kollision mit dem Python-Typ object."""

    object_number = models.CharField(max_length=8, help_text="Ziffernfolge wie erfasst, z. B. 623 oder 82")
    object_number_numeric = models.GeneratedField(
        expression=Cast("object_number", output_field=models.PositiveIntegerField()),
        output_field=models.PositiveIntegerField(),
        db_persist=True,
    )
    name = models.CharField(max_length=160, null=True, blank=True)
    street = models.CharField(max_length=120, null=True, blank=True)
    house_number = models.CharField(max_length=20, null=True, blank=True)
    postal_code = models.CharField(max_length=10, null=True, blank=True)
    city = models.CharField(max_length=80, null=True, blank=True)
    management_type = models.CharField(max_length=16, choices=ManagementType.choices)
    status = models.CharField(max_length=16, choices=ObjectStatus.choices, default=ObjectStatus.NEW)
    takeover_from = models.DateField(null=True, blank=True)
    takeover_to = models.DateField(null=True, blank=True)
    fiscal_year_start_month = models.PositiveSmallIntegerField(null=True, blank=True)
    previous_manager_name = models.CharField(max_length=160, null=True, blank=True)
    previous_manager_street = models.CharField(max_length=120, null=True, blank=True)
    previous_manager_house_number = models.CharField(max_length=20, null=True, blank=True)
    previous_manager_postal_code = models.CharField(max_length=10, null=True, blank=True)
    previous_manager_city = models.CharField(max_length=80, null=True, blank=True)
    previous_manager_contact_person = models.CharField(max_length=160, null=True, blank=True)
    previous_manager_reference = models.CharField(max_length=80, null=True, blank=True)
    expected_unit_count = models.PositiveIntegerField(null=True, blank=True)
    sepa_used = models.BooleanField(null=True, blank=True)
    special_levies_in_period = models.BooleanField(null=True, blank=True)
    is_test = models.BooleanField(default=False)
    drive_root_folder_id = models.CharField(max_length=128, null=True, blank=True)
    drive_root_folder_name = models.CharField(max_length=255, null=True, blank=True)
    drive_root_verified_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="created_by",
        related_name="+",
    )
    active_key = models.GeneratedField(
        expression=Case(
            When(
                deleted_at__isnull=True,
                then=Cast("object_number", output_field=models.PositiveIntegerField()),
            ),
            default=Value(None),
        ),
        output_field=models.PositiveIntegerField(null=True),
        db_persist=True,
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "objects"
        ordering = ["object_number_numeric"]
        constraints = [
            models.UniqueConstraint(fields=["active_key"], name="uq_objects_number_active"),
            models.CheckConstraint(
                condition=Q(object_number__regex=r"^[0-9]+$"), name="ck_objects_number_digits"
            ),
            models.CheckConstraint(
                condition=Q(management_type__in=ManagementType.values), name="ck_objects_mgmt"
            ),
            models.CheckConstraint(condition=Q(status__in=ObjectStatus.values), name="ck_objects_status"),
            models.CheckConstraint(
                condition=Q(takeover_to__isnull=True)
                | Q(takeover_from__isnull=True)
                | Q(takeover_from__lte=F("takeover_to")),
                name="ck_objects_takeover",
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="ix_objects_status"),
            models.Index(fields=["drive_root_folder_id"], name="ix_objects_drive_root"),
        ]

    def __str__(self) -> str:
        return f"{self.object_number} {self.name or ''}".strip()

    @property
    def address(self) -> str:
        parts = [
            " ".join(p for p in [self.street, self.house_number] if p),
            " ".join(p for p in [self.postal_code, self.city] if p),
        ]
        return ", ".join(p for p in parts if p)


class Unit(SoftDeleteModel):
    object = models.ForeignKey(
        ManagedObject, on_delete=models.PROTECT, db_column="object_id", related_name="units"
    )
    unit_number = models.CharField(max_length=16, null=True, blank=True)
    unit_type = models.CharField(max_length=24, choices=UnitType.choices, default=UnitType.OTHER)
    unit_label = models.CharField(max_length=80)
    unit_label_normalized = models.CharField(max_length=80)
    co_ownership_share = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    co_ownership_share_base = models.PositiveIntegerField(null=True, blank=True)
    building = models.CharField(max_length=80, null=True, blank=True)
    location = models.CharField(max_length=120, null=True, blank=True)
    external_ref = models.CharField(max_length=32, null=True, blank=True)
    house_fee_monthly = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=16, default="active")
    data_status = models.CharField(max_length=16, choices=DATA_STATUS_CHOICES, default="incomplete")
    se_managed = models.BooleanField(null=True, blank=True, help_text="Sondereigentumsverwaltung durch uns")
    vacancy_confirmed = models.BooleanField(null=True, blank=True)
    notes = models.CharField(max_length=500, null=True, blank=True)
    active_key = models.GeneratedField(
        expression=Case(When(deleted_at__isnull=True, then=F("unit_label_normalized")), default=Value(None)),
        output_field=models.CharField(max_length=80, null=True),
        db_persist=True,
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "units"
        ordering = ["object_id", "unit_label_normalized"]
        constraints = [
            models.UniqueConstraint(fields=["object", "active_key"], name="uq_units_object_label_active"),
            models.CheckConstraint(condition=Q(unit_type__in=UnitType.values), name="ck_units_type"),
            models.CheckConstraint(condition=Q(status__in=["active", "inactive"]), name="ck_units_status"),
            models.CheckConstraint(
                condition=Q(data_status__in=DATA_STATUS_VALUES), name="ck_units_data_status"
            ),
        ]
        indexes = [
            models.Index(fields=["object", "unit_number"], name="ix_units_object_number"),
            models.Index(fields=["external_ref"], name="ix_units_external_ref"),
        ]

    def __str__(self) -> str:
        return self.unit_label
