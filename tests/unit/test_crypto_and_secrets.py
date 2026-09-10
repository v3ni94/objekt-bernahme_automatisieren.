import base64

import pytest

from objektakte import secrets as s
from objektakte.crypto import DecryptionError, FieldCipher

pytestmark = pytest.mark.django_db(transaction=False)


def test_secret_aus_datei_hat_vorrang(monkeypatch, tmp_path):
    f = tmp_path / "k"
    f.write_text("aus-datei\n", encoding="utf-8")
    monkeypatch.setenv("DEMO_KEY_FILE", str(f))
    monkeypatch.setenv("DEMO_KEY", "aus-env")
    assert s.read_secret("DEMO_KEY") == "aus-datei"


def test_secret_fehlt_required(monkeypatch):
    monkeypatch.delenv("NIX_FILE", raising=False)
    monkeypatch.delenv("NIX", raising=False)
    with pytest.raises(s.SecretMissing):
        s.read_secret("NIX", required=True)


def test_env_helpers(monkeypatch):
    monkeypatch.setenv("A", "12")
    monkeypatch.setenv("B", "ja")
    monkeypatch.setenv("C", "x, y ,,z")
    assert s.env_int("A", 1) == 12 and s.env_bool("B") is True and s.env_list("C") == ["x", "y", "z"]
    monkeypatch.setenv("A", "zwoelf")
    with pytest.raises(ValueError):
        s.env_int("A", 1)


def test_feldverschluesselung_roundtrip_und_aad(settings):
    settings.FIELD_KEYS = {"totp": base64.b64encode(b"k" * 32).decode()}
    c = FieldCipher("totp")
    token = c.encrypt("JBSWY3DPEHPK3PXP", aad="mfa")
    assert token.startswith("v1:") and "JBSWY" not in token
    assert c.decrypt(token, aad="mfa") == "JBSWY3DPEHPK3PXP"
    with pytest.raises(DecryptionError):
        c.decrypt(token, aad="anderer-zweck")
    with pytest.raises(DecryptionError):
        c.decrypt("v2:" + token[3:], aad="mfa")
