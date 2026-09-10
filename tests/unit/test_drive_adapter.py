"""Fake, Aufzeichnung, Backoff und Fehlerabbildung des Drive-Adapters (F 2.5, 2.8, 9.1)."""

from __future__ import annotations

import random

import pytest

from apps.drive.adapter import (
    FOLDER_MIME,
    AuthError,
    InMemoryDriveAdapter,
    NotFound,
    RateLimited,
    RecordingDriveAdapter,
    TransientError,
    count_tree,
)
from apps.drive.backoff import BackoffConfig, CallMetrics, RateLimiter, retry_call
from apps.drive.google_adapter import escape_q, map_http_error


def test_fake_baum_papierkorb_verknuepfung_und_zaehlung():
    d = InMemoryDriveAdapter()
    ids = d.load_scenario(
        d.root_id,
        {
            "623 Musterstadt": {
                "01_Legitimationsunterlagen": {},
                "Alt": {"a.pdf": "1", "Sub": {"b.pdf": "2", "~c.pdf": "3"}},
            }
        },
    )
    obj = ids["623 Musterstadt"]
    assert [n.name for n in d.list_children(obj, folders_only=True)] == ["01_Legitimationsunterlagen", "Alt"]
    n, h = count_tree(d, obj)
    assert n == 2 and len(h) == 64  # c.pdf im Papierkorb zaehlt nicht
    trashed = [x for x in d.list_children_including_trashed(ids["623 Musterstadt/Alt/Sub"]) if x.trashed]
    assert len(trashed) == 1
    sc = d.add_shortcut(d.root_id, "Link", obj)
    assert d.get(sc).shortcut_target_id == obj and d.get(sc).is_shortcut
    renamed = d.rename(ids["623 Musterstadt/Alt"], "Neu")
    assert (
        renamed.id == ids["623 Musterstadt/Alt"]
        and d.get(ids["623 Musterstadt/Alt/a.pdf"]).parent_id == ids["623 Musterstadt/Alt"]
    )
    assert count_tree(d, obj) == (n, h)  # Umbenennen aendert Zaehlung und Hash nicht
    assert d.get("gibt-es-nicht") is None
    with pytest.raises(NotFound):
        d.rename("gibt-es-nicht", "x")


def test_fake_upload_revisionen_und_fehlerinjektion(tmp_path):
    d = InMemoryDriveAdapter()
    f = tmp_path / "liste.xlsx"
    f.write_bytes(b"v1")
    node = d.upload(d.root_id, f, "00_Eigentuemerliste_623.xlsx", "application/x", {"sha256": "abc"})
    assert node.app_properties == {"sha256": "abc"} and node.size == 2
    f.write_bytes(b"v2")
    d.update_content(node.id, f, "application/x")
    assert d.revisions(node.id) == 2 and d.get(node.id).md5 != node.md5
    d.inject("create_folder", RateLimited("zu viel", status=403, reason="rateLimitExceeded"), times=2)
    with pytest.raises(RateLimited):
        d.create_folder(d.root_id, "x")
    with pytest.raises(RateLimited):
        d.create_folder(d.root_id, "x")
    assert d.create_folder(d.root_id, "x").mime_type == FOLDER_MIME


def test_recording_adapter_erkennt_schreibzugriffe():
    inner = InMemoryDriveAdapter()
    rec = RecordingDriveAdapter(inner)
    rec.get(inner.root_id)
    rec.list_children(inner.root_id)
    list(rec.walk(inner.root_id))
    assert rec.read_only and [c[0] for c in rec.calls] == ["get", "list_children", "walk"]
    rec.create_folder(inner.root_id, "neu")
    assert not rec.read_only and rec.write_calls[0][0] == "create_folder"


def test_backoff_403_403_200_mit_gefaelschter_uhr():
    waits: list[float] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimited("limit", status=403, reason="rateLimitExceeded")
        return "ok"

    metrics = CallMetrics()
    result = retry_call(
        fn,
        config=BackoffConfig(base_s=1, factor=2, max_delay_s=64, max_attempts=8),
        sleep=waits.append,
        rng=random.Random(1),
        metrics=metrics,
    )
    assert result == "ok" and calls["n"] == 3 and len(waits) == 2
    assert 0 <= waits[0] <= 1 and 0 <= waits[1] <= 2 and metrics.rate_limited == 2 and metrics.retries == 2


def test_backoff_retry_after_und_aufgabe():
    waits: list[float] = []

    def fn():
        raise RateLimited("limit", status=429, retry_after=7)

    with pytest.raises(RateLimited):
        retry_call(fn, config=BackoffConfig(max_attempts=3), sleep=waits.append, rng=random.Random(1))
    assert waits == [7, 7]

    def fn5xx():
        raise TransientError("503", status=503)

    m = CallMetrics()
    with pytest.raises(TransientError):
        retry_call(
            fn5xx, config=BackoffConfig(max_attempts=2), sleep=waits.append, rng=random.Random(1), metrics=m
        )
    assert m.failures == 1 and m.server_errors == 2


def test_auth_error_einmal_refresh():
    calls = {"n": 0, "refresh": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthError("401", status=401)
        return "ok"

    def refresh():
        calls["refresh"] += 1

    assert retry_call(fn, sleep=lambda s: None, on_auth_error=refresh) == "ok" and calls["refresh"] == 1

    def always():
        raise AuthError("401", status=401)

    with pytest.raises(AuthError):
        retry_call(always, sleep=lambda s: None, on_auth_error=refresh)


def test_ratenbegrenzer_wartet():
    now = {"t": 0.0}
    waits: list[float] = []

    def sleep(s):
        waits.append(s)
        now["t"] += s

    lim = RateLimiter(2.0, clock=lambda: now["t"], sleep=sleep, burst=2)
    assert lim.acquire() == 0 and lim.acquire() == 0
    w = lim.acquire()
    assert w > 0 and waits == [w]


class _Resp(dict):
    def __init__(self, status):
        super().__init__()
        self.status = status


class _HttpError(Exception):
    def __init__(self, status, reason, retry_after=None):
        self.resp = _Resp(status)
        if retry_after:
            self.resp["retry-after"] = str(retry_after)
        self.content = f'{{"error": {{"errors": [{{"reason": "{reason}"}}]}}}}'.encode()


def test_fehlerabbildung():
    assert isinstance(map_http_error(_HttpError(404, "notFound")), NotFound)
    assert isinstance(map_http_error(_HttpError(401, "authError")), AuthError)
    r = map_http_error(_HttpError(403, "userRateLimitExceeded"))
    assert isinstance(r, RateLimited)
    assert map_http_error(_HttpError(429, "rateLimitExceeded", retry_after=3)).retry_after == 3
    from apps.drive.adapter import PermanentError

    assert isinstance(map_http_error(_HttpError(403, "insufficientFilePermissions")), PermanentError)
    assert isinstance(map_http_error(_HttpError(503, "backendError")), TransientError)
    assert isinstance(map_http_error(_HttpError(400, "badRequest")), PermanentError)
    assert escape_q("O'Brien\\x") == "O\\'Brien\\\\x"
