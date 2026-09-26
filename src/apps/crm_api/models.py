"""Zugangstoken der lesenden CRM-Schnittstelle (Schnittstellenvertrag M29 Stufe 3).

Der Klartext eines Tokens wird genau einmal bei der Anlage ausgegeben (Kommando crm_token anlegen) und nie
gespeichert; die Tabelle haelt nur den SHA-256-Hash. Gesperrte Tokens bleiben als Nachweis stehen.
"""

from __future__ import annotations

from django.db import models

from objektakte.db import TimestampedModel

SCOPE_OBJECTS = "objects:read"
SCOPE_DOCUMENTS = "documents:read"
SCOPE_PERSONS = "persons:read"
SCOPES = (SCOPE_OBJECTS, SCOPE_DOCUMENTS, SCOPE_PERSONS)


class CrmApiToken(TimestampedModel):
    name = models.CharField(max_length=80, help_text="Bezeichnung des Abnehmers, z. B. crm")
    token_hash = models.CharField(max_length=64, unique=True, help_text="SHA-256 hex des Klartexts")
    scopes = models.JSONField(default=list, help_text="Liste aus objects:read, documents:read, persons:read")
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "crm_api_tokens"
        ordering = ["pk"]
        indexes = [models.Index(fields=["name", "is_active"], name="ix_crm_tokens_name")]

    def __str__(self) -> str:
        return f"CRM-Token {self.pk} {self.name} ({'aktiv' if self.is_active else 'gesperrt'})"

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])
