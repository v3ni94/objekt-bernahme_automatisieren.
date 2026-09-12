"""Paperless-ngx-Anbindung der Synchronisation: HTTP-Client, Fehlerklassen und In-Memory-Fake.

Der Client kapselt die REST-Schnittstelle (Token-Authentifizierung, API-Versionsheader, Paginierung, Backoff,
streamende Downloads). Der Fake bildet dieselben Methoden im Speicher ab und dient Tests und Trockenlaeufen.
"""

from apps.sync.paperless.client import Page, PaperlessClient, ServerInfo, from_settings
from apps.sync.paperless.errors import (
    PaperlessAuthError,
    PaperlessError,
    PaperlessNotFound,
    PaperlessRateLimited,
    PaperlessSchemaError,
    PaperlessUnavailable,
    PaperlessUnsupported,
)
from apps.sync.paperless.fake import FakePaperless

__all__ = [
    "FakePaperless",
    "Page",
    "PaperlessAuthError",
    "PaperlessClient",
    "PaperlessError",
    "PaperlessNotFound",
    "PaperlessRateLimited",
    "PaperlessSchemaError",
    "PaperlessUnavailable",
    "PaperlessUnsupported",
    "ServerInfo",
    "from_settings",
]
