"""Kontoabfrage nach dem Token-Tausch laeuft ueber Drive about.get, nicht ueber Userinfo (Befund 11.09.2026:
Userinfo antwortete 401, weil nur der Drive-Bereich erteilt wird)."""

from __future__ import annotations

import pytest

from apps.drive import oauth


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def test_kontoabfrage_nutzt_drive_about(monkeypatch):
    aufrufe = []

    def fake_get(url, headers, timeout):
        aufrufe.append((url, headers["Authorization"]))
        return _Resp(200, {"user": {"emailAddress": "Ablage@example.test", "displayName": "Ablage"}})

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    info = oauth.default_userinfo("token-1")
    assert info == {"email": "Ablage@example.test", "name": "Ablage"}
    assert (
        aufrufe[0][0].startswith("https://www.googleapis.com/drive/v3/about")
        and "userinfo" not in aufrufe[0][0]
    )
    assert aufrufe[0][1] == "Bearer token-1"


def test_kontoabfrage_meldet_ablehnung(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: _Resp(401, {}))
    with pytest.raises(oauth.OAuthRejected, match="Drive antwortete 401"):
        oauth.default_userinfo("token-1")
