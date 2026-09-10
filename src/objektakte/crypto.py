"""Feldverschluesselung AES-256-GCM (docs/architektur.md 9.6).

Je Zweck ein eigener Schluessel (FIELD_KEYS: totp, token, iban, iban_hmac) aus einem Docker Secret,
32 Byte, base64-kodiert (openssl rand -base64 32). Chiffrate tragen die Schluesselversion als Praefix
("v1:"), damit eine Rotation zeilenweise umschluesseln kann. Tabellen- und Spaltenname oder ein anderer
Zweckbezeichner gehen als zusaetzlich authentifizierte Daten (AAD) ein.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

from objektakte.secrets import SecretMissing


class DecryptionError(ValueError):
    """Chiffrat nicht lesbar (falscher Schluessel, falsche Version oder manipuliert)."""


def _key_bytes(purpose: str) -> bytes:
    raw = (settings.FIELD_KEYS or {}).get(purpose, "")
    if not raw:
        if settings.DEBUG:
            # Nur Entwicklung: deterministischer Schluessel aus SECRET_KEY, nie in Produktion
            return hashlib.sha256(f"{settings.SECRET_KEY}:{purpose}".encode()).digest()
        raise SecretMissing(f"Schluessel fuer Zweck {purpose} fehlt (Secret {purpose.upper()}_KEY)")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:
        key = raw.encode("utf-8")
    if len(key) != 32:
        # Schluessel anderer Laenge werden auf 32 Byte abgeleitet, damit ein Tippfehler nicht still versagt
        key = hashlib.sha256(key).digest()
    return key


class FieldCipher:
    def __init__(self, purpose: str, version: int = 1) -> None:
        self.purpose = purpose
        self.version = version

    def encrypt(self, plaintext: str | bytes, aad: str = "") -> str:
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        nonce = os.urandom(12)
        ct = AESGCM(_key_bytes(self.purpose)).encrypt(nonce, data, f"{self.purpose}:{aad}".encode())
        return f"v{self.version}:" + base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str, aad: str = "") -> str:
        return self.decrypt_bytes(token, aad).decode("utf-8")

    def decrypt_bytes(self, token: str, aad: str = "") -> bytes:
        try:
            version, payload = token.split(":", 1)
            if not version.startswith("v") or int(version[1:]) != self.version:
                raise DecryptionError(f"Schluesselversion {version} nicht aktiv")
            blob = base64.b64decode(payload)
            nonce, ct = blob[:12], blob[12:]
            return AESGCM(_key_bytes(self.purpose)).decrypt(nonce, ct, f"{self.purpose}:{aad}".encode())
        except DecryptionError:
            raise
        except (InvalidTag, ValueError) as exc:
            raise DecryptionError("Chiffrat nicht lesbar") from exc
