"""Laufzeitkonfiguration app_settings (Fachentwurf D 4.2, Register docs/architektur.md 5.5).

Der Katalog (catalog.json) beschreibt Schluessel, Typ und Schema; die Seeds (db/seeds/app_settings.json)
liefern die Startwerte. Aenderungen laufen ueber apps.config.store.set und werden in audit_events protokolliert.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AppSetting(models.Model):
    class ValueType(models.TextChoices):
        STRING = "string"
        INTEGER = "integer"
        DECIMAL = "decimal"
        BOOLEAN = "boolean"
        LIST = "list"
        OBJECT = "object"

    key = models.CharField(max_length=128, primary_key=True)
    value = models.JSONField(null=True)
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    category = models.CharField(max_length=32)
    description = models.CharField(max_length=500)
    validation = models.JSONField(null=True, blank=True)
    is_secret = models.BooleanField(default=False)
    is_deprecated = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="updated_by",
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "app_settings"
        ordering = ["category", "key"]
        indexes = [models.Index(fields=["category"], name="ix_settings_category")]

    def __str__(self) -> str:
        return self.key
