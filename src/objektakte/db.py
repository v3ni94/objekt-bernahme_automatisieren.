"""Datenbankhilfen fuer MariaDB: feste Binaerspalten und CONCAT_WS fuer generierte Schluessel (Fachentwurf D 1)."""

from __future__ import annotations

from django.db import models
from django.db.models import Func, Value
from django.db.models.functions import Coalesce


class VarBinaryField(models.BinaryField):
    """VARBINARY(n) statt LONGBLOB, damit die Spalte indexierbar ist (iban_hash, iban_encrypted)."""

    def db_type(self, connection):
        if connection.vendor == "mysql":
            return f"varbinary({self.max_length})"
        return super().db_type(connection)


class ConcatWS(Func):
    function = "CONCAT_WS"
    output_field = models.CharField()


def ifnull_int(name: str):
    return Coalesce(models.F(name), Value(0), output_field=models.IntegerField())


def ifnull_str(name: str):
    return Coalesce(models.F(name), Value(""), output_field=models.CharField())


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(TimestampedModel):
    """Soft-Delete nach D 1: deleted_at, deleted_by, delete_reason; Eindeutigkeit ueber active_key je Modell."""

    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="deleted_by",
        related_name="+",
    )
    delete_reason = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


DATA_STATUS_CHOICES = [
    ("confirmed", "bestätigt"),
    ("ai_suggested", "KI-Vorschlag"),
    ("incomplete", "unvollständig"),
]
DATA_STATUS_VALUES = [c[0] for c in DATA_STATUS_CHOICES]
