"""OAuth-Ablauf ohne Google (injizierte Austausch- und Userinfo-Funktionen), Token-Verschluesselung, Refresh und Widerruf,
Eigentuemerakten-Ordner mit dem Fake, Oberflaeche und Befehle."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.drive import oauth
from apps.drive.adapter import InMemoryDriveAdapter
from apps.drive.folders import FolderError, ensure_owner_folder
from apps.drive.models import DriveNode, DriveSyncRun, OAuthToken
from apps.drive.reconcile import DriveConfig, reconcile_object
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import OwnerFile

pytestmark = pytest.mark.django_db


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.example")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "geheim-nur-im-test")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://uebernahme.example.test/auth/google/callback")
    monkeypatch.setenv("DRIVE_ACCOUNT_EMAIL", "ablage@example.test")


def _exchange(tokens):
    def fn(code, verifier):
        assert code == "code123" and verifier
        return dict(tokens)

    return fn


def test_autorisierungs_url_und_callback(env, admin_user):
    session: dict = {}
    url = oauth.build_authorization_url(session)
    q = parse_qs(urlparse(url).query)
    assert (
        q["access_type"] == ["offline"]
        and q["prompt"] == ["consent"]
        and q["include_granted_scopes"] == ["false"]
    )
    assert (
        q["code_challenge_method"] == ["S256"]
        and q["scope"] == [oauth.DRIVE_SCOPE]
        and q["state"] == [session[oauth.SESSION_KEY]["state"]]
    )
    tokens = {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600, "scope": oauth.DRIVE_SCOPE}
    token = oauth.handle_callback(
        session,
        code="code123",
        state=q["state"][0],
        user=admin_user,
        exchange=_exchange(tokens),
        userinfo=lambda t: {"email": "Ablage@example.test"},
    )
    assert token.status == "active" and token.account_email == "ablage@example.test"
    assert b"ref" != bytes(token.refresh_token_encrypted) and bytes(token.refresh_token_encrypted).startswith(
        b"v1:"
    )
    assert (
        oauth._decrypt(token.refresh_token_encrypted, "refresh") == "ref"
        and oauth._decrypt(token.access_token_encrypted, "access") == "acc"
    )
    assert AuditEvent.objects.filter(action="drive.authorize", after_state__result="ok").exists()
    status = oauth.token_status()
    assert status["ok"] and status["days_since_authorization"] == 0
    creds = oauth.get_credentials()
    assert creds.refresh_token == "ref" and creds.token == "acc"


@pytest.mark.parametrize(
    ("state_ok", "email", "scope", "refresh", "grund"),
    [
        (False, "ablage@example.test", oauth.DRIVE_SCOPE, "ref", "state"),
        (True, "privat@example.test", oauth.DRIVE_SCOPE, "ref", "nicht das technische Konto"),
        (True, "ablage@example.test", "https://www.googleapis.com/auth/drive.file", "ref", "Drive-Bereich"),
        (True, "ablage@example.test", oauth.DRIVE_SCOPE, None, "Refresh-Token"),
    ],
)
def test_callback_ablehnungen(env, admin_user, state_ok, email, scope, refresh, grund):
    session: dict = {}
    url = oauth.build_authorization_url(session)
    state = parse_qs(urlparse(url).query)["state"][0] if state_ok else "falsch"
    tokens = {"access_token": "acc", "refresh_token": refresh, "expires_in": 3600, "scope": scope}
    with pytest.raises(oauth.OAuthRejected, match=grund):
        oauth.handle_callback(
            session,
            code="code123",
            state=state,
            user=admin_user,
            exchange=_exchange(tokens),
            userinfo=lambda t: {"email": email},
        )
    assert OAuthToken.objects.count() == 0
    assert AuditEvent.objects.filter(action="drive.authorize", after_state__result="rejected").exists()


def _connected(env, admin_user):
    session: dict = {}
    url = oauth.build_authorization_url(session)
    state = parse_qs(urlparse(url).query)["state"][0]
    tokens = {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600, "scope": oauth.DRIVE_SCOPE}
    return oauth.handle_callback(
        session,
        code="code123",
        state=state,
        user=admin_user,
        exchange=_exchange(tokens),
        userinfo=lambda t: {"email": "ablage@example.test"},
    )


def test_refresh_erzwungen_und_widerruf(env, admin_user):
    token = _connected(env, admin_user)
    assert oauth.refresh_access_token(force=False)["status"] == "valid"

    def refresher(creds):
        creds.token = "acc2"
        creds.expiry = (timezone.now() + timedelta(hours=1)).replace(tzinfo=None)

    result = oauth.refresh_access_token(force=True, reason="daily_proof", refresher=refresher)
    assert result["ok"] and result["status"] == "refreshed"
    token.refresh_from_db()
    assert (
        oauth._decrypt(token.access_token_encrypted, "access") == "acc2" and token.last_refresh_status == "ok"
    )
    assert AuditEvent.objects.filter(action="drive.token_refresh", after_state__result="ok").count() == 1

    def revoked(creds):
        raise RuntimeError("invalid_grant: Token has been expired or revoked.")

    result = oauth.refresh_access_token(force=True, refresher=revoked)
    token.refresh_from_db()
    assert not result["ok"] and token.status == "revoked" and token.consecutive_failures == 1
    assert oauth.token_status()["status"] == "revoked" and oauth.get_credentials() is None
    assert oauth.refresh_access_token(force=True)["status"] == "revoked"
    rows = oauth.oauth_proof_rows()
    assert [r["action"] for r in rows] == ["drive.authorize", "drive.token_refresh", "drive.token_refresh"]


def test_eigentuemerakte_ordner(seeded):
    drive = InMemoryDriveAdapter()
    cfg = DriveConfig.from_settings(root_folder_id=drive.root_id)
    obj = ManagedObject.objects.create(
        object_number="623",
        city="Musterstadt",
        street="Musterweg",
        house_number="1",
        management_type="weg",
        is_test=True,
    )
    unit = Unit.objects.create(
        object=obj, unit_label="WE03", unit_label_normalized="WE3", unit_number="3", unit_type="apartment"
    )
    akte = OwnerFile.objects.create(object=obj, unit=unit, folder_name="WE03_Mustermann")
    with pytest.raises(FolderError):
        ensure_owner_folder(akte, drive=drive)
    reconcile_object(obj, drive=drive, dry_run=False, cfg=cfg)
    rows = ensure_owner_folder(akte, drive=drive)
    assert len(rows) == 12 and rows[0].node_kind == "owner_file_folder" and rows[0].created_by_app
    main05 = DriveNode.objects.get(object=obj, node_kind="main_folder", category_id="05")
    assert [n.name for n in drive.list_children(main05.drive_file_id)] == ["WE03_Mustermann"]
    subs = [n.name for n in drive.list_children(rows[0].drive_file_id)]
    assert subs[0] == "01_Stammdaten" and subs[-1] == "11_Sonstiges" and len(subs) == 11
    ops_before = len(drive.ops)
    rows2 = ensure_owner_folder(akte, drive=drive)
    assert len(rows2) == 12 and DriveNode.objects.filter(owner_file=akte, status="active").count() == 12
    assert not [op for op in drive.ops[ops_before:] if op[0] in ("create_folder", "rename", "move")]
    # manuelle Umbenennung in Drive wird uebernommen, nicht zurueckbenannt
    from apps.drive.folders import invalidate_owner_folder_cache

    invalidate_owner_folder_cache(akte.pk)
    drive.rename(rows[0].drive_file_id, "WE03_Mustermann (alt)")
    rows3 = ensure_owner_folder(akte, drive=drive)
    assert rows3[0].drive_name == "WE03_Mustermann (alt)" and rows3[0].expected_name == "WE03_Mustermann"
    assert drive.get(rows[0].drive_file_id).name == "WE03_Mustermann (alt)"


def test_oberflaeche_und_befehle(env, client_as, admin_user, clerk_user, monkeypatch):
    client = client_as(admin_user)
    page = client.get(reverse("drive_admin"))
    assert page.status_code == 200 and "nicht verbunden" in page.content.decode()
    _connected(env, admin_user)
    fake = InMemoryDriveAdapter()
    monkeypatch.setattr(oauth, "get_adapter", lambda: fake)
    from apps.drive import tasks as drive_tasks

    monkeypatch.setattr(drive_tasks.oauth, "get_adapter", lambda: fake)
    from apps.config import store

    store.set("drive.root_folder_id", fake.root_id, user=admin_user, reason="Test")
    obj = ManagedObject.objects.create(
        object_number="623",
        city="Musterstadt",
        street="Musterweg",
        house_number="1",
        management_type="weg",
        is_test=True,
    )
    fake.add_folder(fake.root_id, "623 Musterstadt, Musterweg 1")
    page = client.get(reverse("drive_admin"))
    assert (
        page.status_code == 200 and "aktiv" in page.content.decode() and fake.root_id in page.content.decode()
    )

    resp = client.post(reverse("object_reconcile", args=[obj.pk]), {"mode": "dry"})
    assert resp.status_code == 302
    run = DriveSyncRun.objects.get(object=obj)
    assert run.dry_run and run.status == "done"
    detail = client.get(reverse("sync_run_detail", args=[run.pk]))
    body = detail.content.decode()
    assert detail.status_code == 200 and "Planliste" in body and "05_Eigentümerakte" in body
    assert client.get(reverse("sync_run_json", args=[run.pk])).status_code == 200
    export = client.get(reverse("sync_run_export", args=[run.pk, "xlsx"]))
    assert export.status_code == 200 and export["Content-Disposition"].startswith("attachment")
    assert client.get(reverse("sync_run_list", args=[obj.pk])).status_code == 200

    resp = client.post(reverse("object_reconcile", args=[obj.pk]), {"mode": "execute"})
    assert (
        resp.status_code == 302
        and DriveSyncRun.objects.filter(object=obj, dry_run=False, status="done").exists()
    )
    obj.refresh_from_db()
    assert obj.drive_root_folder_id

    out = StringIO()
    call_command("drive_reconcile", "--object", "623", "--dry-run", stdout=out)
    assert "no_changes True" in out.getvalue()
    out = StringIO()
    call_command("drive_export_protocol", "--all", stdout=out)
    assert out.getvalue().strip().endswith(".xlsx")
    out = StringIO()
    call_command("drive_oauth_proof", stdout=out)
    assert "Kriterium erfüllt: nein" in out.getvalue()

    # Sachbearbeiter ohne drive.connect sieht die Verwaltungsseite nicht
    c2 = client_as(clerk_user)
    assert c2.get(reverse("drive_admin")).status_code == 403


def test_statusseite_zeigt_token(env, client_as, admin_user):
    _connected(env, admin_user)
    from apps.status.checks import oauth_status

    s = oauth_status()
    assert s["ok"] and s["status"] == "aktiv"
