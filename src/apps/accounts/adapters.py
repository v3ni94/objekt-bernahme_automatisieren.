"""allauth-Anpassungen: keine Selbstregistrierung, Sperre nach Fehlversuchen, verschluesselte TOTP-Daten."""

from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from allauth.mfa.adapter import DefaultMFAAdapter
from django import forms
from django.utils import timezone

from objektakte.crypto import FieldCipher


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request) -> bool:
        return False  # Nutzer legt nur ein Admin an (CR 15)

    def pre_authenticate(self, request, **credentials) -> None:
        from .models import User

        email = (credentials.get("email") or credentials.get("username") or "").strip().lower()
        if email:
            user = User.objects.filter(email=email, deleted_at__isnull=True).first()
            if user and user.is_locked:
                raise forms.ValidationError(
                    "Das Konto ist nach zu vielen Fehlversuchen vorübergehend gesperrt. "
                    f"Bitte in {max(1, int((user.locked_until - timezone.now()).total_seconds() // 60))} Minuten erneut versuchen."
                )
        super().pre_authenticate(request, **credentials)


class MFAAdapter(DefaultMFAAdapter):
    """TOTP-Geheimnisse und Wiederherstellungscodes werden mit TOTP_KEY verschluesselt (docs/architektur.md 9.6)."""

    cipher = FieldCipher("totp")

    def encrypt(self, text: str) -> str:
        return self.cipher.encrypt(text, aad="mfa_authenticator")

    def decrypt(self, encrypted_text: str) -> str:
        return self.cipher.decrypt(encrypted_text, aad="mfa_authenticator")
