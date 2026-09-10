"""Das eingecheckte Bezeichnerregister entspricht dem Modellregister (Beschluss B-01: CI prueft neue Bezeichner)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings

from apps.config.management.commands.bezeichnerregister import MARKER, render


@pytest.mark.django_db
def test_register_ist_aktuell():
    path = Path(settings.REPO_DIR) / "docs" / "architektur" / "bezeichnerregister.md"
    text = path.read_text(encoding="utf-8")
    assert MARKER in text, "Marker fehlt; manage.py bezeichnerregister --write ausführen"
    generated = text.split(MARKER, 1)[1].strip()
    expected = render().split(MARKER, 1)[1].strip()
    assert generated == expected, (
        "Register veraltet: manage.py bezeichnerregister --write ausführen und einchecken"
    )


def test_kein_altname_im_register():
    path = Path(settings.REPO_DIR) / "docs" / "architektur" / "bezeichnerregister.md"
    legacy = "05_" + "Sonst" + "iges"
    assert legacy not in path.read_text(encoding="utf-8")
