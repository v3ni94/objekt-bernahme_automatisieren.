"""HTTP-Client fuer Paperless-ngx (Auftrag Synchronisation Paperless-ngx und Google Drive, 12.09.2026).

Grundlage ist die offizielle REST-Dokumentation (Entwicklungsstand): Authentifizierung ueber den Header
"Authorization: Token <token>", API-Version ueber "Accept: application/json; version=<n>" (9 und 10, Standard 10),
Antwortheader X-Api-Version und X-Version. Alle Aufrufe laufen mit Zeitlimit; 429, 5xx und Verbindungsfehler
werden mit zunehmender Wartezeit wiederholt (BackoffConfig aus apps.drive.backoff, Retry-After wird beachtet).
Uploads (post_document, update_version) werden nach Verbindungsabbruch oder 5xx nicht blind wiederholt, weil der
Server die Datei bereits angenommen haben kann; dort entscheidet der Aufrufer. Downloads werden in Bloecken auf
eine temporaere Datei geschrieben und erst danach umbenannt. Protokollzeilen enthalten nie das Token.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests

from apps.drive.backoff import BackoffConfig
from apps.sync.paperless.errors import (
    PaperlessAuthError,
    PaperlessError,
    PaperlessNotFound,
    PaperlessRateLimited,
    PaperlessSchemaError,
    PaperlessUnavailable,
    PaperlessUnsupported,
)

logger = logging.getLogger(__name__)

SUPPORTED_API_VERSIONS = (9, 10)
DEFAULT_API_VERSION = 10
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
ID_FILTER_CHUNK = 100  # Hoechstzahl der IDs je id__in-Abfrage, damit die URL kurz bleibt
MASKED = "Token ***"

# Wiederholungsstrategien je Aufruf
RETRY_FULL = "full"  # 429, 5xx und Verbindungsfehler
RETRY_RATE_LIMIT_ONLY = "rate_limit_only"  # nur 429; Uploads
PROBE_MAX_SECONDS = 10.0  # Zeitlimit je Zusatzabfrage (Schema, Status, ui_settings): Verbindung und Inhalt
PROBE_MAX_BYTES = 8_000_000


@dataclass(frozen=True)
class ServerInfo:
    """Ergebnis von server_info(): Serverversion, ausgehandelte API-Version, erkannte Funktionen."""

    server_version: str | None
    api_version: int | None
    features: dict[str, bool] = field(default_factory=dict)
    endpoints_seen: dict[str, int | None] = field(default_factory=dict)

    def supports(self, feature: str) -> bool:
        return bool(self.features.get(feature, False))


@dataclass(frozen=True)
class Page:
    """Eine Ergebnisseite einer paginierten Liste; number ist die Seitennummer fuer die Wiederaufnahme."""

    number: int
    count: int
    results: list[dict]
    has_next: bool

    @property
    def next_number(self) -> int | None:
        return self.number + 1 if self.has_next else None


def mask_headers(headers: dict | None) -> dict:
    """Kopie der Header fuer Protokolle; Authorization wird maskiert."""
    safe = dict(headers or {})
    for key in list(safe):
        if key.lower() == "authorization":
            safe[key] = MASKED
    return safe


def parse_retry_after(value: str | None, *, now: Callable[[], datetime] | None = None) -> float | None:
    """Retry-After als Sekunden (Ganzzahl, Dezimalzahl oder HTTP-Datum); None, wenn nicht lesbar."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = (now or (lambda: datetime.now(UTC)))()
    return max(0.0, (when - current).total_seconds())


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return str(value)


def _as_int_list(value: Any) -> list[int]:
    """related_document (Version 9, einzelner Wert oder kommagetrennt) oder related_document_ids (Version 10)."""
    if value is None or value == "":
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, Iterable):
        parts = list(value)
    else:
        return []
    result: list[int] = []
    for part in parts:
        try:
            result.append(int(part))
        except (TypeError, ValueError):
            continue
    return result


def normalize_task(raw: dict | None) -> dict | None:
    """Vereinheitlicht eine Aufgabe aus /api/tasks/ ueber die API-Versionen 9 und 10."""
    if raw is None:
        return None
    related = raw.get("related_document_ids")
    if related is None:
        related = raw.get("related_document")
    return {
        "task_id": raw.get("task_id"),
        "status": raw.get("status"),
        "result": raw.get("result"),
        "related_document_ids": _as_int_list(related),
        "task_type": raw.get("task_type"),
        "date_done": raw.get("date_done"),
        "raw": dict(raw),
    }


def _http_error(response: requests.Response, method: str, path: str) -> PaperlessError:
    """Bildet eine fehlgeschlagene Antwort auf die Fehlerklassen ab; die Meldung enthaelt keine Header."""
    status = int(response.status_code)
    payload: object = None
    try:
        payload = response.json()
    except Exception:
        try:
            payload = (response.text or "")[:500]
        except Exception:
            payload = None
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or payload.get("error") or "")[:300]
    elif isinstance(payload, str):
        detail = payload[:300]
    message = f"Paperless {method} {path}: HTTP {status}" + (f" ({detail})" if detail else "")
    kwargs: dict[str, Any] = {"status_code": status, "payload": payload}
    if status in (401, 403):
        return PaperlessAuthError(message, **kwargs)
    if status == 404:
        return PaperlessNotFound(message, **kwargs)
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        return PaperlessRateLimited(message, retry_after=retry_after, **kwargs)
    if status >= 500:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        return PaperlessUnavailable(message, retry_after=retry_after, **kwargs)
    return PaperlessError(message, **kwargs)


class PaperlessClient:
    """REST-Client; session ist injizierbar (Tests mit Attrappe), backoff steuert die Wiederholungen."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        api_version: int = DEFAULT_API_VERSION,
        timeout_s: float = 30,
        verify_tls: bool = True,
        page_size: int = 100,
        session: Any = None,
        backoff: BackoffConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        features: dict[str, bool] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url fehlt")
        if not token:
            raise ValueError("Token fehlt")
        if api_version not in SUPPORTED_API_VERSIONS:
            raise ValueError(
                f"API-Version {api_version} wird nicht unterstuetzt (erlaubt: {SUPPORTED_API_VERSIONS})"
            )
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.api_version = int(api_version)
        self.timeout_s = float(timeout_s)
        self.verify_tls = bool(verify_tls)
        self.page_size = max(1, int(page_size))
        self.session = session if session is not None else requests.Session()
        self.backoff = backoff or BackoffConfig(base_s=1.0, factor=2.0, max_delay_s=60.0, max_attempts=5)
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._feature_overrides = dict(features or {})
        self._server_info: ServerInfo | None = None
        self.endpoints_seen: dict[str, int | None] = {}
        self.stats = {"requests": 0, "retries": 0, "rate_limited": 0, "server_errors": 0, "waited_s": 0.0}

    # --- Transport -------------------------------------------------------------------------------
    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Token {self._token}",
            "Accept": f"application/json; version={self.api_version}",
        }
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | list | None = None,
        data: Any = None,
        files: Any = None,
        json_body: Any = None,
        stream: bool = False,
        headers: dict | None = None,
        retry: str = RETRY_FULL,
        accept_status: tuple[int, ...] = (),
        timeout: float | None = None,
    ) -> requests.Response:
        """Fuehrt einen Aufruf mit Zeitlimit aus und wiederholt nach Strategie; wirft die Fehlerklassen."""
        url = self._url(path)
        timeout_s = self.timeout_s if timeout is None else min(self.timeout_s, timeout)
        attempt = 0
        while True:
            attempt += 1
            self.stats["requests"] += 1
            started = time.monotonic()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    files=files,
                    json=json_body,
                    headers=self._headers(headers),
                    timeout=timeout_s,
                    verify=self.verify_tls,
                    stream=stream,
                    allow_redirects=False,
                )
            except (requests.exceptions.RequestException, OSError, TimeoutError) as exc:
                error: PaperlessError = PaperlessUnavailable(
                    f"Paperless {method} {path}: Verbindungsfehler ({exc.__class__.__name__})"
                )
                logger.warning("paperless %s %s verbindungsfehler versuch=%d", method, path, attempt)
                if retry == RETRY_FULL and self._wait_before_retry(attempt, error):
                    continue
                raise error from exc
            status = int(response.status_code)
            self.endpoints_seen[path] = status
            logger.info(
                "paperless %s %s status=%s dauer_ms=%d",
                method,
                path,
                status,
                (time.monotonic() - started) * 1000,
            )
            if status < 400 or status in accept_status:
                return response
            error = _http_error(response, method, path)
            _close_quietly(response)
            if isinstance(error, PaperlessRateLimited):
                self.stats["rate_limited"] += 1
                retryable = retry in (RETRY_FULL, RETRY_RATE_LIMIT_ONLY)
            elif isinstance(error, PaperlessUnavailable):
                self.stats["server_errors"] += 1
                retryable = retry == RETRY_FULL
            else:
                retryable = False
            if retryable and self._wait_before_retry(attempt, error):
                continue
            raise error

    def _wait_before_retry(self, attempt: int, error: PaperlessError) -> bool:
        """Wartet vor dem naechsten Versuch; False, wenn die Hoechstzahl der Versuche erreicht ist."""
        cfg = self.backoff
        if attempt >= cfg.max_attempts:
            return False
        cap = min(cfg.max_delay_s, cfg.base_s * (cfg.factor ** (attempt - 1)))
        delay = error.retry_after if error.retry_after is not None else self._rng.uniform(0, cap)
        delay = min(delay, cfg.max_delay_s) if error.retry_after is None else delay
        self.stats["retries"] += 1
        self.stats["waited_s"] += delay
        self._sleep(delay)
        return True

    def _json(self, response: requests.Response, path: str) -> Any:
        try:
            return response.json()
        except Exception as exc:
            raise PaperlessSchemaError(
                f"Paperless {path}: Antwort ist kein JSON", status_code=response.status_code
            ) from exc

    def _get_json(self, path: str, *, params: dict | list | None = None) -> Any:
        return self._json(self._request("GET", path, params=params), path)

    def _list_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Sammelt alle Seiten einer paginierten Liste (Stammdaten wie Tags oder Felder)."""
        items: list[dict] = []
        for page in self._iter_pages(path, params or {}, page_size=self.page_size):
            items.extend(page.results)
        return items

    def _iter_pages(self, path: str, params: dict, *, page_size: int, start_page: int = 1) -> Iterator[Page]:
        number = max(1, int(start_page))
        while True:
            query = {k: v for k, v in params.items() if v is not None}
            query.update({"page": number, "page_size": page_size})
            payload = self._get_json(path, params=query)
            if isinstance(payload, list):
                yield Page(number=number, count=len(payload), results=list(payload), has_next=False)
                return
            if not isinstance(payload, dict) or "results" not in payload:
                raise PaperlessSchemaError(f"Paperless {path}: Antwort ohne results")
            results = payload.get("results") or []
            has_next = bool(payload.get("next"))
            yield Page(
                number=number,
                count=int(payload.get("count") or len(results)),
                results=results,
                has_next=has_next,
            )
            if not has_next:
                return
            number += 1

    # --- Serverinformationen ---------------------------------------------------------------------
    def server_info(self, *, refresh: bool = False) -> ServerInfo:
        """Liest Version und Funktionen des Servers; Ergebnis wird je Client zwischengespeichert."""
        if self._server_info is not None and not refresh:
            return self._server_info
        response = self._request("GET", "/api/documents/", params={"page_size": 1, "page": 1})
        server_version = _header(response, "X-Version")
        negotiated = _header(response, "X-Api-Version")
        try:
            api_version = int(negotiated) if negotiated else self.api_version
        except ValueError:
            api_version = self.api_version
        sample_keys: set[str] = set()
        try:
            payload = self._json(response, "/api/documents/")
            first = (payload.get("results") or [None])[0] if isinstance(payload, dict) else None
            if isinstance(first, dict):
                sample_keys = set(first)
        except PaperlessSchemaError:
            pass

        custom_fields = self._probe("/api/custom_fields/", params={"page_size": 1}) is not None
        schema = self._probe("/api/schema/")
        schema_paths: set[str] = set()
        if isinstance(schema, dict) and isinstance(schema.get("paths"), dict):
            schema_paths = set(schema["paths"])
        if not server_version:
            ui = self._probe("/api/ui_settings/")
            if isinstance(ui, dict) and ui.get("version"):
                server_version = str(ui["version"])
        self._probe("/api/status/")

        features = {
            "document_versions": any("update_version" in p for p in schema_paths)
            or bool({"versions", "root_document"} & sample_keys),
            "custom_fields": custom_fields,
            "bulk_edit": any("bulk_edit" in p for p in schema_paths) if schema_paths else True,
            "tasks_v10": api_version >= 10,
            "schema_available": bool(schema_paths),
        }
        features.update(self._feature_overrides)
        self._server_info = ServerInfo(
            server_version=server_version,
            api_version=api_version,
            features=features,
            endpoints_seen=dict(self.endpoints_seen),
        )
        return self._server_info

    def _probe(
        self,
        path: str,
        *,
        params: dict | None = None,
        max_seconds: float = PROBE_MAX_SECONDS,
        max_bytes: int = PROBE_MAX_BYTES,
    ) -> Any:
        """GET ohne Wiederholung; None bei 404, 403 oder unlesbarer Antwort (Endpunkt fehlt oder ist gesperrt).
        Der Inhalt wird gestreamt und bei Ueberschreiten von Gesamtzeit oder Groesse verworfen: das OpenAPI-Schema
        (/api/schema/) erzeugt Paperless erst bei Abruf und liefert es auf kleinen Servern sehr langsam aus; ohne
        diese Grenze haengt der Verbindungstest minutenlang (Beobachtung vom 12.09.2026)."""
        try:
            response = self._request(
                "GET", path, params=params, stream=True, retry=RETRY_RATE_LIMIT_ONLY, timeout=max_seconds
            )
        except PaperlessNotFound:
            return None
        except PaperlessAuthError as exc:
            if exc.status_code == 401:
                raise
            return None
        except PaperlessUnavailable:
            return None
        started = time.monotonic()
        chunks: list[bytes] = []
        size = 0
        try:
            iterator = (
                response.iter_content(chunk_size=65536)
                if hasattr(response, "iter_content")
                else [response.content]
            )
            for chunk in iterator:
                if not chunk:
                    continue
                size += len(chunk)
                chunks.append(chunk)
                if size > max_bytes or time.monotonic() - started > max_seconds:
                    logger.warning(
                        "paperless GET %s verworfen: %d Byte nach %.1f s (Grenze %d Byte, %.0f s)",
                        path,
                        size,
                        time.monotonic() - started,
                        max_bytes,
                        max_seconds,
                    )
                    _close_quietly(response)
                    return None
        except Exception:
            _close_quietly(response)
            return None
        try:
            return json.loads(b"".join(chunks))
        except Exception:
            return None

    # --- Dokumente ---------------------------------------------------------------------------------
    def _document_params(
        self,
        *,
        modified_after: datetime | str | None,
        added_after: datetime | str | None,
        ordering: str,
        fields: Iterable[str] | None,
    ) -> dict:
        return {
            "modified__gt": _iso(modified_after),
            "added__gt": _iso(added_after),
            "ordering": ordering,
            "fields": ",".join(fields) if fields else None,
        }

    def iter_pages(
        self,
        *,
        modified_after: datetime | str | None = None,
        added_after: datetime | str | None = None,
        ids: Iterable[int] | None = None,
        ordering: str = "id",
        fields: Iterable[str] | None = None,
        page_size: int | None = None,
        start_page: int = 1,
    ) -> Iterator[Page]:
        """Seitenweise Dokumentliste mit Seitennummer, damit ein Inventar nach Abbruch fortgesetzt werden kann."""
        params = self._document_params(
            modified_after=modified_after, added_after=added_after, ordering=ordering, fields=fields
        )
        size = int(page_size or self.page_size)
        if ids is None:
            yield from self._iter_pages("/api/documents/", params, page_size=size, start_page=start_page)
            return
        id_list = sorted({int(i) for i in ids})
        if not id_list:
            return
        for offset in range(0, len(id_list), ID_FILTER_CHUNK):
            chunk = id_list[offset : offset + ID_FILTER_CHUNK]
            chunk_params = {**params, "id__in": ",".join(str(i) for i in chunk)}
            yield from self._iter_pages(
                "/api/documents/", chunk_params, page_size=size, start_page=start_page
            )

    def list_documents(
        self,
        *,
        modified_after: datetime | str | None = None,
        added_after: datetime | str | None = None,
        ids: Iterable[int] | None = None,
        ordering: str = "id",
        fields: Iterable[str] | None = None,
        page_size: int | None = None,
    ) -> Iterator[dict]:
        """Alle Dokumente der Filterung, Seite fuer Seite nachgeladen, bis next leer ist."""
        for page in self.iter_pages(
            modified_after=modified_after,
            added_after=added_after,
            ids=ids,
            ordering=ordering,
            fields=fields,
            page_size=page_size,
        ):
            yield from page.results

    def find_by_custom_field(self, field_name: str, value: str) -> dict | None:
        """Sucht ein Dokument ueber den exakten Wert eines benutzerdefinierten Feldes (custom_field_query, ab
        Paperless-ngx 2.13). Liefert das erste Dokument oder None; ein Server ohne diesen Filter meldet
        PaperlessUnsupported statt eines stillen Fehltreffers."""
        query = json.dumps([field_name, "exact", str(value)])
        try:
            payload = self._get_json(
                "/api/documents/",
                params={
                    "custom_field_query": query,
                    "page_size": 2,
                    "fields": "id,title,modified,tags,mime_type",
                },
            )
        except PaperlessError as exc:
            if exc.status_code == 400:
                raise PaperlessUnsupported(
                    "Der Paperless-Server unterstützt den Filter custom_field_query nicht"
                ) from exc
            raise
        results = payload.get("results") if isinstance(payload, dict) else payload
        if not results:
            return None
        return dict(results[0])

    def get_document(self, document_id: int) -> dict:
        return self._get_json(f"/api/documents/{int(document_id)}/")

    def get_metadata(self, document_id: int) -> dict:
        return self._get_json(f"/api/documents/{int(document_id)}/metadata/")

    def get_notes(self, document_id: int) -> list[dict]:
        payload = self._get_json(f"/api/documents/{int(document_id)}/notes/")
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        if not isinstance(payload, list):
            raise PaperlessSchemaError("Paperless notes: Liste erwartet")
        return payload

    def download(self, document_id: int, target: Path, *, original: bool = True) -> Path:
        """Laedt die Datei streamend nach target (Original oder Archivfassung)."""
        params = {"original": "true"} if original else None
        return self._stream_to_file(f"/api/documents/{int(document_id)}/download/", target, params=params)

    def download_thumbnail(self, document_id: int, target: Path) -> Path:
        return self._stream_to_file(f"/api/documents/{int(document_id)}/thumb/", target)

    def _stream_to_file(self, path: str, target: Path, *, params: dict | None = None) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        response = self._request("GET", path, params=params, stream=True, headers={"Accept": "*/*"})
        try:
            with open(tmp, "wb") as fh:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    if chunk:
                        fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
        except (requests.exceptions.RequestException, OSError) as exc:
            _remove_quietly(tmp)
            raise PaperlessUnavailable(
                f"Paperless {path}: Uebertragung abgebrochen ({exc.__class__.__name__})"
            ) from exc
        finally:
            _close_quietly(response)
        os.replace(tmp, target)
        return target

    def post_document(
        self,
        path: Path,
        *,
        title: str,
        created: datetime | str | None = None,
        tags: Iterable[int] = (),
        custom_fields: dict[int, Any] | Iterable[int] | None = None,
        document_type: int | None = None,
        correspondent: int | None = None,
        filename: str | None = None,
        storage_path: int | None = None,
        archive_serial_number: int | None = None,
    ) -> str:
        """Laedt eine Datei hoch und liefert die Task-UUID. Nach Verbindungsabbruch keine Wiederholung."""
        path = Path(path)
        form: list[tuple[str, Any]] = [("title", title)]
        if created is not None:
            form.append(("created", _iso(created)))
        if document_type is not None:
            form.append(("document_type", str(int(document_type))))
        if correspondent is not None:
            form.append(("correspondent", str(int(correspondent))))
        if storage_path is not None:
            form.append(("storage_path", str(int(storage_path))))
        if archive_serial_number is not None:
            form.append(("archive_serial_number", str(int(archive_serial_number))))
        for tag in tags:
            form.append(("tags", str(int(tag))))
        if custom_fields:
            if isinstance(custom_fields, dict):
                # Zuordnung Feld-ID zu Wert als JSON-Objekt (neuere Server); Liste nur IDs ohne Wert
                form.append(("custom_fields", json.dumps({str(int(k)): v for k, v in custom_fields.items()})))
            else:
                for field_id in custom_fields:
                    form.append(("custom_fields", str(int(field_id))))
        with open(path, "rb") as fh:
            files = {"document": (filename or path.name, fh)}
            response = self._request(
                "POST", "/api/documents/post_document/", data=form, files=files, retry=RETRY_RATE_LIMIT_ONLY
            )
        return self._task_id_from(response, "/api/documents/post_document/")

    def _task_id_from(self, response: requests.Response, path: str) -> str:
        payload = self._json(response, path)
        if isinstance(payload, str) and payload:
            return payload
        if isinstance(payload, dict) and payload.get("task_id"):
            return str(payload["task_id"])
        raise PaperlessSchemaError(f"Paperless {path}: keine Task-UUID in der Antwort", payload=payload)

    def update_version(self, document_id: int, path: Path, label: str | None = None) -> str:
        """Neue Dateiversion eines Dokuments (nur Server mit Dokumentversionen); liefert die Task-UUID."""
        if not self.server_info().supports("document_versions"):
            raise PaperlessUnsupported("Der Paperless-Server bietet keine Dokumentversionen an")
        path = Path(path)
        form: list[tuple[str, Any]] = []
        if label:
            form.append(("version_label", label))
        with open(path, "rb") as fh:
            files = {"document": (path.name, fh)}
            response = self._request(
                "POST",
                f"/api/documents/{int(document_id)}/update_version/",
                data=form or None,
                files=files,
                retry=RETRY_RATE_LIMIT_ONLY,
            )
        return self._task_id_from(response, "update_version")

    def get_task(self, task_id: str) -> dict | None:
        """Aufgabe zur UUID, normalisiert ueber die API-Versionen; None, wenn unbekannt."""
        payload = self._get_json("/api/tasks/", params={"task_id": task_id})
        items: list = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = list(payload.get("results") or [])
        if not items:
            return None
        return normalize_task(items[0])

    def patch_document(self, document_id: int, **fields: Any) -> dict:
        """Teilaktualisierung (title, tags, custom_fields, correspondent, document_type, ...)."""
        if not fields:
            raise ValueError("patch_document ohne Felder")
        response = self._request("PATCH", f"/api/documents/{int(document_id)}/", json_body=fields)
        return self._json(response, "patch_document")

    def bulk_edit(self, document_ids: Iterable[int], method: str, parameters: dict | None = None) -> dict:
        """POST /api/documents/bulk_edit/ mit method und parameters; Antwort des Servers als dict."""
        body = {
            "documents": [int(i) for i in document_ids],
            "method": method,
            "parameters": dict(parameters or {}),
        }
        response = self._request("POST", "/api/documents/bulk_edit/", json_body=body)
        payload = self._json(response, "bulk_edit")
        return payload if isinstance(payload, dict) else {"result": payload}

    def add_tags(self, document_id: int, tag_ids: Iterable[int]) -> dict:
        """Ergaenzt Tags in einem Aufruf; vorhandene Tags bleiben erhalten."""
        ids = [int(t) for t in tag_ids]
        if not ids:
            return {"result": "OK"}
        return self.bulk_edit([document_id], "modify_tags", {"add_tags": ids, "remove_tags": []})

    def set_custom_field_values(self, document_id: int, values: dict[int, Any]) -> dict:
        """Setzt Werte benutzerdefinierter Felder in einem Aufruf; andere Felder bleiben unveraendert."""
        if not values:
            return {"result": "OK"}
        return self.bulk_edit(
            [document_id],
            "modify_custom_fields",
            {"add_custom_fields": {str(int(k)): v for k, v in values.items()}, "remove_custom_fields": []},
        )

    # --- Stammdaten --------------------------------------------------------------------------------
    def list_tags(self) -> list[dict]:
        return self._list_all("/api/tags/")

    def _find_named(self, path: str, name: str) -> dict | None:
        """Sucht einen Stammdateneintrag ueber den Namensfilter name__iexact (kleine Antwort, ohne die
        Dokumentzaehler der vollstaendigen Liste, die auf grossen Instanzen in Zeitueberschreitungen laeuft).
        Ignoriert der Server den Filter, wird die vollstaendige Liste gelesen."""
        wanted = name.strip().casefold()
        payload = self._get_json(path, params={"name__iexact": name, "page_size": 10, "page": 1})
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        rows = list(results or [])
        for row in rows:
            if str(row.get("name", "")).strip().casefold() == wanted:
                return row
        total = payload.get("count") if isinstance(payload, dict) else None
        if isinstance(total, int) and total > len(rows):
            for row in self._list_all(path):
                if str(row.get("name", "")).strip().casefold() == wanted:
                    return row
        return None

    def find_tag(self, name: str) -> dict | None:
        return self._find_named("/api/tags/", name)

    def find_custom_field(self, name: str) -> dict | None:
        return self._find_named("/api/custom_fields/", name)

    def create_tag(self, name: str, **extra: Any) -> dict:
        response = self._request("POST", "/api/tags/", json_body={"name": name, **extra})
        return self._json(response, "/api/tags/")

    def list_custom_fields(self) -> list[dict]:
        return self._list_all("/api/custom_fields/")

    def create_custom_field(self, name: str, data_type: str, extra_data: dict | None = None) -> dict:
        body: dict[str, Any] = {"name": name, "data_type": data_type}
        if extra_data is not None:
            body["extra_data"] = extra_data
        response = self._request("POST", "/api/custom_fields/", json_body=body)
        return self._json(response, "/api/custom_fields/")

    def list_document_types(self) -> list[dict]:
        return self._list_all("/api/document_types/")

    def get_correspondent(self, correspondent_id: int) -> dict | None:
        """Ein Korrespondent ueber seine Kennung (kleine Antwort statt der vollstaendigen Liste)."""
        try:
            return self._get_json(f"/api/correspondents/{int(correspondent_id)}/")
        except PaperlessNotFound:
            return None

    def list_correspondents(self) -> list[dict]:
        return self._list_all("/api/correspondents/")


def _header(response: requests.Response, name: str) -> str | None:
    try:
        value = response.headers.get(name)
    except Exception:
        return None
    if value is None:
        # Attrappen ohne CaseInsensitiveDict
        try:
            lowered = {str(k).lower(): v for k, v in dict(response.headers).items()}
        except Exception:
            return None
        value = lowered.get(name.lower())
    return str(value) if value is not None else None


def _close_quietly(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            logger.debug("paperless antwort schliessen fehlgeschlagen: %s", exc.__class__.__name__)


def _remove_quietly(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def from_settings(*, session: Any = None, backoff: BackoffConfig | None = None) -> PaperlessClient | None:
    """Client aus app_settings (paperless.*) und dem Secret PAPERLESS_TOKEN; None ohne base_url oder Token."""
    from django.conf import settings as dj_settings

    from apps.config import store

    def setting(key: str, default: Any) -> Any:
        try:
            value = store.get(key, default)
        except store.UnknownSetting:
            return default
        return default if value is None else value

    base_url = str(setting("paperless.base_url", "") or "").strip()
    token = str(dj_settings.OBJEKTAKTE.get("PAPERLESS_TOKEN") or "").strip()
    if not base_url or not token:
        return None
    try:
        api_version = int(setting("paperless.api_version", DEFAULT_API_VERSION))
    except (TypeError, ValueError):
        api_version = DEFAULT_API_VERSION
    return PaperlessClient(
        base_url,
        token,
        api_version=api_version if api_version in SUPPORTED_API_VERSIONS else DEFAULT_API_VERSION,
        timeout_s=float(setting("paperless.timeout_seconds", 30)),
        verify_tls=bool(setting("paperless.verify_tls", True)),
        page_size=int(setting("paperless.page_size", 100)),
        session=session,
        backoff=backoff,
    )
