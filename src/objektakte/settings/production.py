"""Produktion hinter Traefik (docs/betrieb.md 3.5). Alle Werte aus .env und Secrets."""

from objektakte.secrets import SecretMissing, read_secret

from .base import *  # noqa: F401,F403

DEBUG = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# HSTS setzt Traefik ueber die Middleware-Labels (docs/betrieb.md 2.3); hier keine doppelte Ausgabe.
SECURE_HSTS_SECONDS = 0

if read_secret("APP_SECRET_KEY", default=None) is None:
    raise SecretMissing("APP_SECRET_KEY fehlt (Secret app_secret_key)")
