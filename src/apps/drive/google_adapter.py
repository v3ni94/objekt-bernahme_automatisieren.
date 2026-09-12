"""Echter Drive-Client (Fachentwurf F 2.3 bis 2.6, docs/architektur.md 7.2).

Alle Aufrufe mit supportsAllDrives und includeItemsFromAllDrives, Felderauswahl, Escaping in q, Paginierung bis leerer
Token, Backoff und Ratenbegrenzung (apps.drive.backoff), Metrikzaehler, strukturierte Protokollzeilen ohne Inhalte und
Token. Der HTTP-Transport ist injizierbar (Tests mit HttpMockSequence). Kein Aufruf loescht endgueltig.

Synchronisation (12.09.2026): Aenderungsprotokoll ueber changes.getStartPageToken und changes.list (Cursor je
Ablage), appProperties setzen, Export von Google-Dokumenten, Revisionsangaben (headRevisionId, version,
sha256Checksum, driveId). Ein ungueltiger Cursor (404) wird als DriveCursorInvalid gemeldet.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from apps.drive.adapter import (
    FOLDER_MIME,
    AuthError,
    ChangePage,
    DriveChange,
    DriveCursorInvalid,
    DriveNode,
    NotFound,
    PermanentError,
    RateLimited,
    TransientError,
    normalize_app_properties,
    revision_info,
    sort_deterministic,
)
from apps.drive.backoff import BackoffConfig, CallMetrics, RateLimiter, retry_call

logger = logging.getLogger(__name__)
FIELDS = (
    "id, name, mimeType, parents, trashed, size, md5Checksum, sha256Checksum, modifiedTime, createdTime, "
    "shortcutDetails, appProperties, headRevisionId, version, driveId"
)
LIST_FIELDS = f"nextPageToken, files({FIELDS})"
CHANGE_FIELDS = f"nextPageToken, newStartPageToken, changes(fileId, removed, time, driveId, file({FIELDS}))"
PAGE_SIZE = 1000  # ANNAHME A-24: API-Hoechstwert zum Umsetzungszeitpunkt pruefen
RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}
NO_RETRY_403 = {
    "insufficientFilePermissions",
    "storageQuotaExceeded",
    "forbidden",
    "appNotAuthorizedToFile",
    "cannotModifyInheritedPermission",
}


def escape_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _to_node(item: dict) -> DriveNode:
    size = item.get("size")
    shortcut = item.get("shortcutDetails") or {}
    return DriveNode(
        id=item["id"],
        name=item.get("name", ""),
        mime_type=item.get("mimeType", ""),
        parents=tuple(item.get("parents") or []),
        trashed=bool(item.get("trashed", False)),
        size=int(size) if size is not None else None,
        md5=item.get("md5Checksum"),
        modified_time=item.get("modifiedTime", ""),
        created_time=item.get("createdTime", ""),
        shortcut_target_id=shortcut.get("targetId"),
        app_properties=dict(item.get("appProperties") or {}),
        sha256=item.get("sha256Checksum"),
        head_revision_id=item.get("headRevisionId"),
        version=str(item["version"]) if item.get("version") is not None else None,
        drive_id=item.get("driveId"),
    )


def _to_change(item: dict) -> DriveChange:
    """Eintrag von changes.list; bei removed=True oder entfallenem Zugriff fehlt file."""
    file_item = item.get("file")
    return DriveChange(
        file_id=item["fileId"],
        removed=bool(item.get("removed", False)),
        time=item.get("time", ""),
        node=_to_node(file_item) if file_item else None,
        drive_id=item.get("driveId") or (file_item or {}).get("driveId"),
    )


def map_http_error(exc: Exception) -> Exception:
    """Uebersetzt googleapiclient.errors.HttpError in die Fehlerklassen des Adapters (F 2.5)."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        return TransientError(str(exc))
    reason = ""
    try:
        payload = json.loads(getattr(exc, "content", b"") or b"{}")
        errors = payload.get("error", {}).get("errors") or []
        reason = errors[0].get("reason", "") if errors else payload.get("error", {}).get("status", "")
    except Exception:
        reason = ""
    retry_after = None
    headers = getattr(getattr(exc, "resp", None), "get", None)
    if callable(headers):
        try:
            ra = exc.resp.get("retry-after")
            retry_after = float(ra) if ra else None
        except Exception:
            retry_after = None
    if status == 404:
        return NotFound(str(exc), status=404, reason=reason or "notFound")
    if status == 401:
        return AuthError(str(exc), status=401, reason=reason or "unauthorized")
    if status == 429 or (status == 403 and reason in RETRY_REASONS):
        return RateLimited(str(exc), status=status, reason=reason, retry_after=retry_after)
    if status == 403:
        return PermanentError(str(exc), status=403, reason=reason or "forbidden")
    if status >= 500:
        return TransientError(str(exc), status=status, reason=reason, retry_after=retry_after)
    return PermanentError(str(exc), status=status, reason=reason)


class GoogleDriveAdapter:
    def __init__(
        self,
        service,
        *,
        drive_id: str | None = None,
        backoff: BackoffConfig | None = None,
        rate_per_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_auth_error: Callable[[], None] | None = None,
        chunk_bytes: int = 8 * 1024 * 1024,
        resumable_threshold: int = 5 * 1024 * 1024,
    ) -> None:
        self.service = service
        self.drive_id = drive_id
        self.backoff = backoff or BackoffConfig()
        self.limiter = RateLimiter(rate_per_s, clock=clock, sleep=sleep)
        self._sleep = sleep
        self.metrics = CallMetrics()
        self.on_auth_error = on_auth_error
        self.chunk_bytes = chunk_bytes
        self.resumable_threshold = resumable_threshold

    # --- Ausfuehrung mit Backoff -----------------------------------------------------------------
    def _execute(self, op: str, request):
        from googleapiclient.errors import HttpError

        def call():
            self.limiter.acquire()
            started = time.monotonic()
            try:
                result = request.execute()
            except HttpError as exc:
                mapped = map_http_error(exc)
                logger.info(
                    "drive %s status=%s reason=%s dauer_ms=%d",
                    op,
                    getattr(exc.resp, "status", "?"),
                    mapped.reason,
                    (time.monotonic() - started) * 1000,
                )
                raise mapped from exc
            except (OSError, TimeoutError) as exc:
                raise TransientError(str(exc)) from exc
            logger.info("drive %s status=200 dauer_ms=%d", op, (time.monotonic() - started) * 1000)
            return result

        return retry_call(
            call,
            config=self.backoff,
            sleep=self._sleep,
            metrics=self.metrics,
            on_auth_error=self.on_auth_error,
        )

    def _list_params(self) -> dict:
        params = {
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "pageSize": PAGE_SIZE,
            "fields": LIST_FIELDS,
        }
        if self.drive_id:
            params.update({"corpora": "drive", "driveId": self.drive_id})
        else:
            params["corpora"] = "user"
        return params

    def _list(self, q: str) -> list[DriveNode]:
        items: list[dict] = []
        token = None
        while True:
            params = {**self._list_params(), "q": q}
            if token:
                params["pageToken"] = token
            page = self._execute("files.list", self.service.files().list(**params))
            items.extend(page.get("files", []))
            token = page.get("nextPageToken")
            if not token:
                break
        return sort_deterministic([_to_node(i) for i in items])

    # --- Schnittstelle ---------------------------------------------------------------------------
    def get(self, file_id: str) -> DriveNode | None:
        try:
            item = self._execute(
                "files.get", self.service.files().get(fileId=file_id, fields=FIELDS, supportsAllDrives=True)
            )
        except NotFound:
            return None
        return _to_node(item)

    def list_children(self, parent_id: str, folders_only: bool = False) -> list[DriveNode]:
        q = f"'{escape_q(parent_id)}' in parents and trashed = false"
        if folders_only:
            q += f" and (mimeType = '{FOLDER_MIME}' or mimeType = 'application/vnd.google-apps.shortcut')"
        return self._list(q)

    def list_children_including_trashed(self, parent_id: str) -> list[DriveNode]:
        return self._list(f"'{escape_q(parent_id)}' in parents")

    def create_folder(self, parent_id: str, name: str) -> DriveNode:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        return _to_node(
            self._execute(
                "files.create", self.service.files().create(body=body, fields=FIELDS, supportsAllDrives=True)
            )
        )

    def rename(self, file_id: str, new_name: str) -> DriveNode:
        return _to_node(
            self._execute(
                "files.update",
                self.service.files().update(
                    fileId=file_id, body={"name": new_name}, fields=FIELDS, supportsAllDrives=True
                ),
            )
        )

    def trash(self, file_id: str) -> None:
        """In den Papierkorb von Drive (30 Tage wiederherstellbar); files.delete wird nie aufgerufen."""
        self._execute(
            "files.update",
            self.service.files().update(
                fileId=file_id, body={"trashed": True}, fields="id,trashed", supportsAllDrives=True
            ),
        )

    def move(self, file_id: str, from_parent_id: str, to_parent_id: str) -> DriveNode:
        return _to_node(
            self._execute(
                "files.update",
                self.service.files().update(
                    fileId=file_id,
                    addParents=to_parent_id,
                    removeParents=from_parent_id,
                    fields=FIELDS,
                    supportsAllDrives=True,
                ),
            )
        )

    def copy(self, file_id: str, to_parent_id: str, new_name: str) -> DriveNode:
        body = {"name": new_name, "parents": [to_parent_id]}
        return _to_node(
            self._execute(
                "files.copy",
                self.service.files().copy(fileId=file_id, body=body, fields=FIELDS, supportsAllDrives=True),
            )
        )

    def upload(
        self, parent_id: str, local_path: Path, name: str, mime_type: str, app_properties: dict | None = None
    ) -> DriveNode:
        from googleapiclient.http import MediaFileUpload

        size = Path(local_path).stat().st_size
        media = MediaFileUpload(
            str(local_path),
            mimetype=mime_type,
            resumable=size >= self.resumable_threshold,
            chunksize=self.chunk_bytes,
        )
        body = {"name": name, "parents": [parent_id], "appProperties": app_properties or {}}
        request = self.service.files().create(
            body=body, media_body=media, fields=FIELDS, supportsAllDrives=True
        )
        if not media.resumable():
            return _to_node(self._execute("files.create", request))
        response = None
        while response is None:
            _status, response = retry_call(
                lambda: request.next_chunk(), config=self.backoff, sleep=self._sleep, metrics=self.metrics
            )
        return _to_node(response)

    def update_content(self, file_id: str, local_path: Path, mime_type: str) -> DriveNode:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
        return _to_node(
            self._execute(
                "files.update",
                self.service.files().update(
                    fileId=file_id, media_body=media, fields=FIELDS, supportsAllDrives=True
                ),
            )
        )

    def download(self, file_id: str, target_path: Path) -> None:
        import io

        from googleapiclient.http import MediaIoBaseDownload

        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "wb") as fh:
            buffer = io.BufferedWriter(fh)  # type: ignore[arg-type]
            downloader = MediaIoBaseDownload(
                buffer,
                self.service.files().get_media(fileId=file_id, supportsAllDrives=True),
                chunksize=self.chunk_bytes,
            )
            done = False
            while not done:
                _status, done = retry_call(
                    lambda: downloader.next_chunk(),
                    config=self.backoff,
                    sleep=self._sleep,
                    metrics=self.metrics,
                )
            buffer.flush()

    def walk(self, folder_id: str) -> Iterator[tuple[DriveNode, list[str]]]:
        yield from self._walk(folder_id, [folder_id])

    def _walk(self, folder_id: str, path: list[str]) -> Iterator[tuple[DriveNode, list[str]]]:
        for node in self.list_children(folder_id):
            yield node, list(path)
            if node.is_folder:
                yield from self._walk(node.id, [*path, node.id])

    # --- Synchronisation (12.09.2026) -----------------------------------------------------------
    def start_page_token(self, drive_id: str | None = None) -> str:
        """changes.getStartPageToken; driveId wie bei den Listenaufrufen aus der Konfiguration (drive.root_drive_id),
        wenn kein Parameter uebergeben wird."""
        params: dict = {"supportsAllDrives": True}
        effective = drive_id or self.drive_id
        if effective:
            params["driveId"] = effective
        result = self._execute(
            "changes.getStartPageToken", self.service.changes().getStartPageToken(**params)
        )
        token = result.get("startPageToken")
        if not token:
            raise TransientError("changes.getStartPageToken lieferte keinen startPageToken")
        return str(token)

    def list_changes(
        self, page_token: str, *, drive_id: str | None = None, page_size: int = 1000
    ) -> ChangePage:
        """Eine Seite von changes.list. Der Aufrufer folgt next_page_token, bis new_start_page_token vorliegt.
        404 bedeutet abgelaufener oder ungueltiger Cursor und wird als DriveCursorInvalid gemeldet."""
        params: dict = {
            "pageToken": page_token,
            "pageSize": page_size,
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "includeRemoved": True,
            "restrictToMyDrive": False,
            "spaces": "drive",
            "fields": CHANGE_FIELDS,
        }
        effective = drive_id or self.drive_id
        if effective:
            params["driveId"] = effective
        try:
            page = self._execute("changes.list", self.service.changes().list(**params))
        except NotFound as exc:
            raise DriveCursorInvalid(
                f"Cursor des Aenderungsprotokolls ungueltig oder abgelaufen: {exc}",
                status=exc.status,
                reason=exc.reason,
            ) from exc
        return ChangePage(
            changes=[_to_change(c) for c in page.get("changes", [])],
            next_page_token=page.get("nextPageToken") or None,
            new_start_page_token=page.get("newStartPageToken") or None,
        )

    def set_app_properties(self, file_id: str, properties: dict) -> DriveNode:
        """files.update mit appProperties; None-Werte werden als null gesendet und entfernen den Schluessel."""
        body = {"appProperties": normalize_app_properties(properties)}
        return _to_node(
            self._execute(
                "files.update",
                self.service.files().update(fileId=file_id, body=body, fields=FIELDS, supportsAllDrives=True),
            )
        )

    def export(self, file_id: str, mime_type: str, target_path: Path) -> Path:
        """files.export_media fuer Google-Dokumente in Bloecken wie download; andere Dateien sind nicht exportierbar."""
        import io

        from googleapiclient.http import MediaIoBaseDownload

        node = self.get(file_id)
        if node is None:
            raise NotFound(f"{file_id} nicht gefunden", status=404, reason="notFound")
        if not node.is_google_doc:
            raise PermanentError(
                f"{file_id} ist kein Google-Dokument ({node.mime_type}); export nicht moeglich, download verwenden",
                status=403,
                reason="fileNotExportable",
            )
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "wb") as fh:
            buffer = io.BufferedWriter(fh)  # type: ignore[arg-type]
            downloader = MediaIoBaseDownload(
                buffer,
                self.service.files().export_media(fileId=file_id, mimeType=mime_type),
                chunksize=self.chunk_bytes,
            )
            done = False
            while not done:
                _status, done = retry_call(
                    lambda: downloader.next_chunk(),
                    config=self.backoff,
                    sleep=self._sleep,
                    metrics=self.metrics,
                )
            buffer.flush()
        return Path(target_path)

    def get_revision_info(self, file_id: str) -> dict:
        """headRevisionId, version, modifiedTime, md5, sha256 und size aus files.get."""
        node = self.get(file_id)
        if node is None:
            raise NotFound(f"{file_id} nicht gefunden", status=404, reason="notFound")
        return revision_info(node)


def build_service(credentials, http=None):
    """Discovery-Client fuer Drive v3; http nur in Tests (HttpMockSequence)."""
    from googleapiclient.discovery import build

    if http is not None:
        return build("drive", "v3", http=http, static_discovery=True)
    return build("drive", "v3", credentials=credentials, cache_discovery=False, static_discovery=True)
