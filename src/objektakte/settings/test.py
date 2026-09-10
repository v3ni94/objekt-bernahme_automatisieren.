"""Tests: MariaDB (kein SQLite, docs/architektur.md 3.1), lokaler Cache, schnelle Hasher."""

from .base import *  # noqa: F401,F403
from .base import DATABASES, LOGGING, OBJEKTAKTE, REPO_DIR, STORAGES, env_str

DEBUG = False
ALLOWED_HOSTS = ["*"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_PROXY_SSL_HEADER = None

DATABASES["default"].update(
    {
        "HOST": env_str("TEST_DB_HOST", "127.0.0.1"),
        "PORT": env_str("TEST_DB_PORT", "3306"),
        "NAME": env_str("TEST_DB_NAME", "objektakte_dev"),
        "USER": env_str("TEST_DB_USER", "objektakte_test"),
        "PASSWORD": env_str("TEST_DB_PASSWORD", "testpasswort"),
    }
)
DATABASES["default"]["TEST"] = {
    "NAME": "test_objektakte",
    "CHARSET": "utf8mb4",
    "COLLATION": "utf8mb4_unicode_ci",
}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}}
SESSION_ENGINE = "django.contrib.sessions.backends.db"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
CELERY_TASK_ALWAYS_EAGER = True
OBJEKTAKTE["DATA_DIR"] = REPO_DIR / "data"
OBJEKTAKTE["READYZ_TOKEN"] = "test-readyz-token"  # noqa: S105
FIELD_KEYS = {
    "totp": "dGVzdC1zY2hsdWVzc2VsLXRvdHAtMzItYnl0ZXMtbGFuZw==",
    "token": "dGVzdC1zY2hsdWVzc2VsLXRva2VuLTMyLWJ5dGVzLWxhbmc=",
    "iban": "dGVzdC1zY2hsdWVzc2VsLWliYW4tMzItYnl0ZXMtbGFuZ2c=",
    "iban_hmac": "dGVzdC1obWFjLXNjaGx1ZXNzZWwtMzItYnl0ZXMtbGFuZw==",
}
LOGGING["handlers"]["console"]["formatter"] = "text"

STATIC_ROOT = REPO_DIR / ".static_test"
STATIC_ROOT.mkdir(exist_ok=True)
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
