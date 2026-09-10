"""Nur fuer collectstatic im Image-Build: keine Geheimnisse, keine Datenbank."""

from .base import *  # noqa: F401,F403

SECRET_KEY = "build-only-nicht-fuer-den-betrieb"  # noqa: S105
STATIC_ROOT = "/app/staticfiles"
