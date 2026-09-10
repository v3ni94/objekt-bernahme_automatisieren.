"""Gemeinsame Fixtures. Integrationstests laufen gegen MariaDB (docs/architektur.md 3.1)."""

from __future__ import annotations

import pytest
from django.core.management import call_command


@pytest.fixture
def seeded(request):
    marker = request.node.get_closest_marker("django_db")
    if marker is not None and marker.kwargs.get("transaction"):
        request.getfixturevalue("transactional_db")
    else:
        request.getfixturevalue("db")
    call_command("seed", verbosity=0)


@pytest.fixture
def admin_user(seeded):
    from apps.accounts.models import Role, User

    return User.objects.create_user(
        "admin@example.test",
        "Startpasswort-12x",
        role=Role.objects.get(code="admin"),
        display_name="Admin Test",
    )


@pytest.fixture
def clerk_user(seeded):
    from apps.accounts.models import Role, User

    return User.objects.create_user(
        "sb@example.test",
        "Startpasswort-12x",
        role=Role.objects.get(code="sachbearbeiter"),
        display_name="Sach Bearbeiter",
    )


@pytest.fixture
def totp_for(db):
    """Richtet fuer einen Nutzer einen zweiten Faktor ein (allauth-Authenticator), damit die MFA-Sperre nicht greift."""
    from allauth.mfa.adapter import get_adapter
    from allauth.mfa.models import Authenticator

    def _make(user):
        adapter = get_adapter()
        Authenticator.objects.create(
            user=user, type=Authenticator.Type.TOTP, data={"secret": adapter.encrypt("JBSWY3DPEHPK3PXP")}
        )
        return user

    return _make


@pytest.fixture
def client_as(client, totp_for):
    def _login(user):
        totp_for(user)
        client.force_login(user)
        return client

    return _login
