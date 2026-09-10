"""Revisionsprotokoll (Fachentwurf D 8.3 und 10.3). Append-only: Trigger gegen UPDATE und DELETE
liegen in der Migration 0002 (Beschluss B-43); die Anwendungskonten haben diese Rechte zusaetzlich nicht."""

from __future__ import annotations

from django.db import models


class AuditEvent(models.Model):
    class ActorType(models.TextChoices):
        USER = "user", "Nutzer"
        SYSTEM = "system", "System"
        WORKER = "worker", "Worker"

    occurred_at = models.DateTimeField(db_index=False)
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    user_id = models.BigIntegerField(
        null=True, blank=True
    )  # kein Fremdschluessel: Protokoll ueberlebt Loeschlaeufe
    user_email = models.CharField(max_length=254, null=True, blank=True)
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=48)
    entity_id = models.BigIntegerField(null=True, blank=True)
    object_id = models.BigIntegerField(null=True, blank=True)
    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField(null=True, blank=True)
    reason = models.CharField(max_length=500, null=True, blank=True)
    request_id = models.CharField(max_length=36, null=True, blank=True)
    ip_address = models.BinaryField(max_length=16, null=True, blank=True)
    user_agent = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "audit_events"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id", "occurred_at"], name="ix_audit_entity"),
            models.Index(fields=["user_id", "occurred_at"], name="ix_audit_user"),
            models.Index(fields=["object_id", "occurred_at"], name="ix_audit_object"),
            models.Index(fields=["action", "occurred_at"], name="ix_audit_action"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(actor_type__in=["user", "system", "worker"]), name="ck_audit_actor"
            )
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%d.%m.%Y %H:%M} {self.action} {self.entity_type}#{self.entity_id}"

    @property
    def ip_display(self) -> str:
        if not self.ip_address:
            return ""
        import ipaddress

        try:
            return str(ipaddress.ip_address(bytes(self.ip_address)))
        except ValueError:
            return ""


class IbanAccessLog(models.Model):
    """Zugriffsprotokoll auf entschluesselte IBAN (D 10.3). purpose ohne display_full (docs/architektur.md 9.3)."""

    accessed_at = models.DateTimeField()
    actor_type = models.CharField(max_length=16)
    user_id = models.BigIntegerField(null=True, blank=True)
    user_email = models.CharField(max_length=254, null=True, blank=True)
    entity_type = models.CharField(max_length=16)
    entity_id = models.BigIntegerField()
    purpose = models.CharField(max_length=24)
    request_id = models.CharField(max_length=36, null=True, blank=True)
    ip_address = models.BinaryField(max_length=16, null=True, blank=True)

    class Meta:
        db_table = "iban_access_log"
        indexes = [
            models.Index(fields=["entity_type", "entity_id", "accessed_at"], name="ix_iban_log_entity"),
            models.Index(fields=["user_id", "accessed_at"], name="ix_iban_log_user"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(actor_type__in=["user", "system"]), name="ck_iban_log_actor"
            ),
            models.CheckConstraint(
                condition=models.Q(entity_type__in=["owner", "tenant"]), name="ck_iban_log_entity"
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__in=["edit", "export", "rekey"]), name="ck_iban_log_purpose"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.accessed_at:%d.%m.%Y %H:%M} {self.purpose} {self.entity_type}#{self.entity_id}"
