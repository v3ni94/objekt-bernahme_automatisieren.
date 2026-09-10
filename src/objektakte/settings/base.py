"""Grundeinstellungen fuer alle Umgebungen.

Startparameter kommen aus .env (docs/betrieb.md 3.7), Geheimnisse aus /run/secrets ueber *_FILE-Variablen
(docs/betrieb.md 3.8). Fachliche Konfiguration liegt in app_settings (Beschluss B-06), nicht hier.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from celery.schedules import crontab

from objektakte.secrets import env_bool, env_int, env_list, env_str, read_secret

SRC_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = SRC_DIR.parent

# --- Sicherheit -------------------------------------------------------------------------------
SECRET_KEY = read_secret("APP_SECRET_KEY", default="nur-fuer-entwicklung-unsicher")
DEBUG = False
APP_DOMAIN = env_str("APP_DOMAIN", "localhost")
APP_BASE_URL = env_str("APP_BASE_URL", f"https://{APP_DOMAIN}")
ALLOWED_HOSTS = [APP_DOMAIN, "localhost", "127.0.0.1", "web"]
CSRF_TRUSTED_ORIGINS = [APP_BASE_URL] if APP_BASE_URL.startswith("http") else []
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
X_FRAME_OPTIONS = "SAMEORIGIN"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# auth.W004: Die E-Mail ist ueber die generierte Spalte active_key fuer nicht geloeschte Nutzer eindeutig (D 10.1);
# der Manager filtert geloeschte Zeilen aus.
# models.W036: allauth definiert auf mfa_authenticator eine bedingte Eindeutigkeit, die MariaDB nicht anlegt;
# die Anwendungslogik von allauth verhindert doppelte TOTP-Authenticatoren je Nutzer.
SILENCED_SYSTEM_CHECKS = ["auth.W004", "models.W036"]

# --- Anwendungen -------------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "apps.accounts",
    "apps.audit",
    "apps.config",
    "apps.status",
    "apps.ui",
    "apps.objects",
    "apps.parties",
    "apps.documents",
    "apps.drive",
    "apps.pipeline",
    "apps.classification",
    "apps.review",
    "apps.imports",
    "apps.requirements",
    "apps.lists",
    "apps.ai",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "objektakte.middleware.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.accounts.middleware.SessionLimitsMiddleware",
    "apps.accounts.middleware.RequireMFAMiddleware",
]

ROOT_URLCONF = "objektakte.urls"
WSGI_APPLICATION = "objektakte.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [SRC_DIR / "apps" / "ui" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.ui.context_processors.app_context",
            ],
        },
    },
]

# --- Datenbank (MariaDB, utf8mb4, strikter Modus; Nutzer je Dienst ueber DB_USER) ------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "HOST": env_str("DB_HOST", "127.0.0.1"),
        "PORT": env_str("DB_PORT", "3306"),
        "NAME": env_str("DB_NAME", "objektakte"),
        "USER": env_str("DB_USER", "app_rw"),
        "PASSWORD": read_secret("DB_PASSWORD", default=""),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'",
        },
        "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Redis: Cache, Sitzungen, Celery-Broker ------------------------------------------------------
_redis_password = read_secret("REDIS_PASSWORD", default="")
_redis_url = env_str("REDIS_URL", "redis://127.0.0.1:6379/0")
if _redis_password and "@" not in _redis_url:
    scheme, rest = _redis_url.split("://", 1)
    REDIS_URL = f"{scheme}://:{quote(_redis_password, safe='')}@{rest}"
else:
    REDIS_URL = _redis_url

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "objektakte",
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# --- Authentifizierung und Rollen (docs/architektur.md 9.1, 9.2) -----------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
PASSWORD_MIN_LENGTH = env_int("PASSWORD_MIN_LENGTH", 12)  # ANNAHME A31, Frage F28
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": PASSWORD_MIN_LENGTH},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/konto/login/"
LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/konto/login/"
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USER_MODEL_EMAIL_FIELD = "email"
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_SESSION_REMEMBER = False
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_LOGIN_ON_PASSWORD_RESET = False
ACCOUNT_PASSWORD_MIN_LENGTH = PASSWORD_MIN_LENGTH
ACCOUNT_REAUTHENTICATION_TIMEOUT = env_int("REAUTH_TIMEOUT_SECONDS", 900)  # Step-up 15 min, ANNAHME A31
LOGIN_MAX_ATTEMPTS = env_int("LOGIN_MAX_ATTEMPTS", 5)
LOGIN_LOCKOUT_MINUTES = env_int("LOGIN_LOCKOUT_MINUTES", 15)
ACCOUNT_RATE_LIMITS = {
    "login_failed": f"{LOGIN_MAX_ATTEMPTS}/{LOGIN_LOCKOUT_MINUTES}m/ip,{LOGIN_MAX_ATTEMPTS}/{LOGIN_LOCKOUT_MINUTES}m/key",
    "login": "30/m/ip",
    "reauthenticate": "10/m/user",
    "reset_password": "5/m/ip",
}
MFA_ADAPTER = "apps.accounts.adapters.MFAAdapter"
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "Objektuebernahme HVM"
MFA_RECOVERY_CODE_COUNT = 10  # ANNAHME A31
MFA_REQUIRED = env_bool("MFA_REQUIRED", True)  # TOTP fuer jede Rolle Pflicht (Ue17)

SESSION_IDLE_MINUTES = env_int("SESSION_IDLE_MINUTES", 480)  # ANNAHME A31, Frage F28
SESSION_ABSOLUTE_HOURS = env_int("SESSION_ABSOLUTE_HOURS", 12)
SESSION_COOKIE_AGE = SESSION_ABSOLUTE_HOURS * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True

# --- Sprache, Zeit ------------------------------------------------------------------------------
LANGUAGE_CODE = "de"
TIME_ZONE = env_str("TZ", "Europe/Berlin")
USE_I18N = True
USE_TZ = True
DATE_FORMAT = "d.m.Y"
DATETIME_FORMAT = "d.m.Y H:i"
SHORT_DATE_FORMAT = "d.m.Y"

# --- Statische Dateien ----------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = Path(env_str("STATIC_ROOT", str(REPO_DIR / "staticfiles")))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Celery (docs/architektur.md 6.10) ------------------------------------------------------------
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "io"
CELERY_TASK_QUEUES = {
    name: {"exchange": name, "routing_key": name} for name in ("ocr", "classify", "ai", "io", "lists")
}
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": env_int("JOBS_VISIBILITY_TIMEOUT_S", 3600)}
CELERY_TIMEZONE = TIME_ZONE
# Zeitplan (Serverzeit TZ). Uhrzeit des Konsistenzlaufs ist ANNAHME (nachts, ausserhalb der Verarbeitung), Frage F27.
CELERY_BEAT_SCHEDULE: dict = {
    # Nachtraining des lokalen Klassifikators (E 3.5), naechtlich 01:30 Serverzeit (ANNAHME), nie waehrend eines Objektlaufs
    "classifier-retrain-nightly": {
        "task": "classification.retrain",
        "schedule": crontab(hour=1, minute=30),
        "options": {"queue": "classify"},
    },
    # Drive-Ueberwachung (B-16, G 10): stuendlicher Lesetest, taeglicher erzwungener Refresh fuer den 8-Tage-Nachweis (T9)
    "drive-hourly-read-test": {
        "task": "drive.hourly_read_test",
        "schedule": crontab(minute=7),
        "options": {"queue": "io"},
    },
    "drive-daily-forced-refresh": {
        "task": "drive.daily_forced_refresh",
        "schedule": crontab(hour=4, minute=0),
        "options": {"queue": "io"},
    },
    # Sweeper der Verarbeitung jede Minute (E 10.4); zusaetzlich beim Start jedes Worker-Containers (objektakte.celery)
    "pipeline-sweep": {"task": "pipeline.sweep", "schedule": crontab(minute="*"), "options": {"queue": "io"}},
    "parties-check-assignment-consistency": {
        "task": "parties.check_assignment_consistency",
        "schedule": crontab(hour=3, minute=15),
        "options": {"queue": "io"},
    },
}

# --- Anwendungsweite Startparameter -------------------------------------------------------------
OBJEKTAKTE = {
    "SERVICE_NAME": env_str("SERVICE_NAME", "web"),
    "DATA_DIR": Path(env_str("DATA_DIR", "/data")),
    "DISK_RESERVE_GB": env_int("DISK_RESERVE_GB", 10),
    "HEARTBEAT_FILE": env_str("HEARTBEAT_FILE", "/tmp/heartbeat"),
    "READYZ_TOKEN": read_secret("READYZ_TOKEN", default=""),
    "SEED_DIR": REPO_DIR / "db" / "seeds",
    "CATALOG_FILE": SRC_DIR / "apps" / "config" / "catalog.json",
    "HEARTBEAT_STALE_SECONDS": 120,  # ANNAHME A19
    "JOB_DISPATCH": env_str("JOB_DISPATCH", "celery"),  # celery | none (Tests und lokaler Runner)
    "SERVICES_WITH_HEARTBEAT": env_list(
        "SERVICES_WITH_HEARTBEAT", ["worker-ocr", "worker-nlp", "worker-io", "beat"]
    ),
}

# --- Verschluesselung (docs/architektur.md 9.6): je Zweck ein Schluessel als Secret ----------------
FIELD_KEYS = {
    "totp": read_secret("TOTP_KEY", default=""),
    "token": read_secret("TOKEN_KEY", default=""),
    "iban": read_secret("IBAN_KEY", default=""),
    "iban_hmac": read_secret("IBAN_HMAC_KEY", default=""),
}

# --- Logging: JSON mit Maskierung ---------------------------------------------------------------
LOG_LEVEL = env_str("LOG_LEVEL", "INFO")
LOG_FORMAT = env_str("LOG_FORMAT", "json")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"mask": {"()": "objektakte.logging_json.MaskingFilter"}},
    "formatters": {
        "json": {"()": "objektakte.logging_json.JsonFormatter"},
        "text": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "text",
            "filters": ["mask"],
        }
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.server": {"level": "WARNING", "propagate": True},
        "django.security": {"level": "WARNING", "propagate": True},
        "celery": {"level": LOG_LEVEL, "propagate": True},
    },
}
