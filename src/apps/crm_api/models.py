"""Zugangstoken der CRM-Schnittstelle (Schnittstellenvertrag M29 Stufe 3) und Uploads aus dem CRM.

Der Klartext eines Tokens wird genau einmal bei der Anlage ausgegeben (Kommando crm_token anlegen) und nie
gespeichert; die Tabelle haelt nur den SHA-256-Hash. Gesperrte Tokens bleiben als Nachweis stehen.
"""

from __future__ import annotations

from django.db import models

from objektakte.db import TimestampedModel

SCOPE_OBJECTS = "objects:read"
SCOPE_DOCUMENTS = "documents:read"
SCOPE_PERSONS = "persons:read"
# Upload aus dem CRM in die Verarbeitung (26.09.2026); nur mit Schalter sync.crm_uploads_enabled
SCOPE_DOCUMENTS_WRITE = "documents:write"
SCOPES = (SCOPE_OBJECTS, SCOPE_DOCUMENTS, SCOPE_PERSONS, SCOPE_DOCUMENTS_WRITE)


class CrmApiToken(TimestampedModel):
    name = models.CharField(max_length=80, help_text="Bezeichnung des Abnehmers, z. B. crm")
    token_hash = models.CharField(max_length=64, unique=True, help_text="SHA-256 hex des Klartexts")
    scopes = models.JSONField(
        default=list, help_text="Liste aus objects:read, documents:read, persons:read, documents:write"
    )
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


class CrmUpload(TimestampedModel):
    """Herkunft eines Dokuments aus dem CRM: Kennung des CRM-Dokuments (Idempotenz des Uploads) und die Hinweise
    des Hochladenden (Einheiten, Personen, Ticket). Die Hinweise tragen nur Kennungen und Bezeichnungen aus dem CRM,
    keine Namen oder Kontaktdaten; sie werden fuer die Zuordnung zu Eigentuemer- und Mieterakten vorgehalten."""

    document = models.OneToOneField(
        "documents.Document", on_delete=models.PROTECT, db_column="document_id", related_name="crm_upload"
    )
    crm_document_id = models.CharField(max_length=64, unique=True)
    hints = models.JSONField(default=dict, blank=True)
    token = models.ForeignKey(
        CrmApiToken, null=True, blank=True, on_delete=models.SET_NULL, db_column="token_id", related_name="+"
    )

    class Meta:
        db_table = "crm_uploads"
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"CRM-Upload {self.crm_document_id} zu Dokument {self.document_id}"
