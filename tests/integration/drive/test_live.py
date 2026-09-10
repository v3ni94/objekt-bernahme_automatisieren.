"""Optionaler Live-Test gegen ein echtes Test-Wurzelverzeichnis (F 9.3). Laeuft nur mit DRIVE_LIVE_TEST_ROOT_ID und
DRIVE_LIVE_TEST_TOKEN_FILE (JSON mit client_id, client_secret, refresh_token). Arbeitet nur in TESTLAUF_<Zeitstempel>,
bricht bei Verwechslung mit der Produktivwurzel ab und raeumt in den Papierkorb (nichts wird endgueltig geloescht)."""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.live]

ROOT = os.environ.get("DRIVE_LIVE_TEST_ROOT_ID")
TOKEN_FILE = os.environ.get("DRIVE_LIVE_TEST_TOKEN_FILE")


@pytest.mark.skipif(
    not ROOT or not TOKEN_FILE,
    reason="Live-Test nur mit DRIVE_LIVE_TEST_ROOT_ID und DRIVE_LIVE_TEST_TOKEN_FILE",
)
def test_live_abgleich(seeded):
    from google.oauth2.credentials import Credentials

    from apps.config import store
    from apps.drive.adapter import FOLDER_MIME, RecordingDriveAdapter
    from apps.drive.google_adapter import GoogleDriveAdapter, build_service
    from apps.drive.reconcile import DriveConfig, reconcile_object
    from apps.objects.models import ManagedObject

    assert ROOT != store.get("drive.root_folder_id"), "Test-Wurzel darf nicht die Produktivwurzel sein"
    data = json.loads(Path(TOKEN_FILE).read_text(encoding="utf-8"))
    creds = Credentials(
        None,
        refresh_token=data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=data["client_id"],
        client_secret=data["client_secret"],
    )
    adapter = GoogleDriveAdapter(build_service(creds))
    children = adapter.list_children(ROOT, folders_only=True)
    assert all(c.name.startswith("TESTLAUF_") for c in children), "Test-Wurzel enthält fremde Ordner"
    run_folder = adapter.create_folder(
        ROOT, f"TESTLAUF_{datetime.now(UTC):%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"
    )
    try:
        obj = ManagedObject.objects.create(
            object_number="700",
            city="Musterstadt",
            street="Musterweg",
            house_number="7",
            management_type="weg",
            is_test=True,
        )
        cfg = DriveConfig.from_settings(root_folder_id=run_folder.id)
        rec = RecordingDriveAdapter(adapter)
        dry = reconcile_object(obj, drive=rec, dry_run=True, cfg=cfg)
        assert dry.status == "done" and rec.read_only
        run = reconcile_object(obj, drive=adapter, dry_run=False, cfg=cfg)
        assert run.status == "done" and run.actions_executed >= 7
        second = reconcile_object(obj, drive=RecordingDriveAdapter(adapter), dry_run=False, cfg=cfg)
        assert second.no_changes is True
        names = [c.name for c in adapter.list_children(run_folder.id) if c.mime_type == FOLDER_MIME]
        assert names == ["700 Musterstadt, Musterweg 7"]
        print({"metrics": adapter.metrics.__dict__})
    finally:
        # Aufraeumen nur in den Papierkorb (files.update trashed=true)
        adapter._execute(
            "files.update",
            adapter.service.files().update(
                fileId=run_folder.id, body={"trashed": True}, supportsAllDrives=True
            ),
        )
