"""Ordnerabgleich ohne bestaetigten Wurzelordner erzeugt keinen Lauf, sondern fuehrt zur Einrichtung
(Befund 11.09.2026: fuenf fehlgeschlagene Laeufe mit "drive.root_folder_id ist nicht gesetzt")."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.drive import oauth
from apps.drive.models import DriveSyncRun
from apps.objects.models import ManagedObject

pytestmark = pytest.mark.django_db


def test_abgleich_ohne_wurzel_wird_abgewiesen(client_as, clerk_user, seeded, monkeypatch):
    monkeypatch.setattr(oauth, "token_status", lambda: {"status": "active", "ok": True})
    obj = ManagedObject.objects.create(
        object_number="900", city="Musterstadt", street="Musterweg", house_number="9", management_type="weg"
    )
    client = client_as(clerk_user)
    resp = client.post(reverse("object_reconcile", args=[obj.pk]), {"mode": "execute"}, follow=True)
    assert resp.redirect_chain[0][0] == reverse("drive_admin")
    assert not DriveSyncRun.objects.filter(object=obj).exists()
    assert any("Wurzelordner" in m.message for m in resp.context["messages"])
