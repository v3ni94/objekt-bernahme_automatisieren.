"""Fehlerklassen des Paperless-ngx-Clients.

Die Abbildung folgt dem HTTP-Status: 401 und 403 Berechtigung, 404 nicht gefunden, 429 Ratenbegrenzung mit
Wartezeit aus Retry-After, 5xx sowie Verbindungsfehler und Zeitueberschreitungen als voruebergehend nicht
verfuegbar. PaperlessUnsupported meldet Funktionen, die der angebundene Server nicht anbietet,
PaperlessSchemaError unerwartete Antwortstrukturen. Meldungen enthalten nie das Token.
"""

from __future__ import annotations


class PaperlessError(Exception):
    """Basisklasse aller Fehler des Clients; status_code ist None bei Verbindungsfehlern."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        payload: object = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.payload = payload


class PaperlessAuthError(PaperlessError):
    """401 oder 403: Token ungueltig oder Berechtigung fehlt."""


class PaperlessNotFound(PaperlessError):
    """404: Dokument, Aufgabe oder Endpunkt nicht vorhanden."""


class PaperlessRateLimited(PaperlessError):
    """429: Ratenbegrenzung; retry_after in Sekunden, falls der Server Retry-After liefert."""


class PaperlessUnavailable(PaperlessError):
    """5xx, Verbindungsfehler oder Zeitueberschreitung; Wiederholung mit Backoff moeglich."""


class PaperlessUnsupported(PaperlessError):
    """Der angebundene Server bietet die angeforderte Funktion nicht an (zum Beispiel Dokumentversionen)."""


class PaperlessSchemaError(PaperlessError):
    """Antwort nicht als JSON lesbar oder ohne die erwarteten Felder."""
