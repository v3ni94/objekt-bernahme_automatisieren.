"""Audit ist append-only: UPDATE und DELETE schlagen fehl (Beschluss B-43); Geheimnisse werden geschwaerzt."""

import pytest
from django.db import DatabaseError, connection, transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record, scrub

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_update_auf_audit_events_schlaegt_fehl():
    e = record("test.action", entity_type="test", entity_id=1, after={"a": 1})
    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("UPDATE audit_events SET action = 'manipuliert' WHERE id = %s", [e.pk])
    e.refresh_from_db()
    assert e.action == "test.action"


def test_delete_auf_audit_events_schlaegt_fehl():
    e = record("test.action", entity_type="test", entity_id=2)
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=e.pk).delete()
    assert AuditEvent.objects.filter(pk=e.pk).exists()


def test_iban_access_log_ist_append_only():
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO iban_access_log (accessed_at, actor_type, entity_type, entity_id, purpose) "
            "VALUES (NOW(3), 'system', 'owner', 1, 'rekey')"
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("DELETE FROM iban_access_log")


def test_scrub_entfernt_geheimnisse_und_maskiert_iban():
    out = scrub(
        {
            "iban": "DE89370400440532013000",
            "iban_last4": "3000",
            "refresh_token": "abc",
            "note": "Konto DE89 3704 0044 0532 0130 00",
            "nested": {"password": "x", "ok": 1},
        }
    )
    assert (
        out["iban"] == "[GESCHWAERZT]"
        and out["iban_last4"] == "3000"
        and out["refresh_token"] == "[GESCHWAERZT]"
    )
    assert (
        "3704" not in out["note"]
        and out["nested"]["password"] == "[GESCHWAERZT]"
        and out["nested"]["ok"] == 1
    )


def test_record_setzt_akteur_und_request_id(rf, admin_user):
    request = rf.get("/x", HTTP_X_REQUEST_ID="req-123", REMOTE_ADDR="10.0.0.9")
    request.user = admin_user
    request.request_id = "req-123"
    e = record("setting.update", entity_type="app_setting", request=request, before={"v": 1}, after={"v": 2})
    assert e.user_email == admin_user.email and e.actor_type == "user"
    assert e.request_id == "req-123" and e.ip_display == "10.0.0.9"
