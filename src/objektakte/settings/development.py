"""Lokale Entwicklung: Debug an, Text-Logs, Cache und Sitzungen ohne Redis-Zwang."""

from .base import *  # noqa: F401,F403
from .base import LOGGING, OBJEKTAKTE, REPO_DIR, STORAGES, env_str

DEBUG = True
ALLOWED_HOSTS = ["*"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_PROXY_SSL_HEADER = None
OBJEKTAKTE["DATA_DIR"] = REPO_DIR / "data"
LOGGING["handlers"]["console"]["formatter"] = (
    env_str("LOG_FORMAT", "text") if env_str("LOG_FORMAT", "text") in ("text", "json") else "text"
)
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
