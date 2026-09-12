"""Verbindungsbefund und Einrichtung der Kennzeichnung in Paperless-ngx: Serverversion, API-Version, Funktionen,
Tag und benutzerdefinierte Felder der Anwendung. Ergebnis liegt in sync_cursors (paperless, connection)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from django.utils import timezone

from apps.audit.services import record
from apps.sync import config, services
from apps.sync.models import SyncSystem

FIELD_TYPES = {"uuid": "string", "object": "string", "status": "string", "drive": "url"}


@dataclass
class SetupState:
    ok: bool
    message: str
    server_version: str | None = None
    api_version: int | None = None
    features: dict = field(default_factory=dict)
    tag_id: int | None = None
    field_ids: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    checked_at: str | None = None

    def as_meta(self) -> dict:
        return asdict(self)


def _by_name(rows: list[dict], name: str) -> dict | None:
    wanted = name.strip().casefold()
    return next((r for r in rows if str(r.get("name", "")).strip().casefold() == wanted), None)


def _failed_state(message: str) -> SetupState:
    """Befund ohne erfolgreiche Verbindung. Die zuletzt bekannte Kennzeichnung (Tag-ID, Feld-IDs, Serverstand)
    bleibt erhalten, damit laufende Operationen nach einer voruebergehenden Stoerung nicht ohne Felder und Tag
    weiterarbeiten; ok ist trotzdem False, bis ein Test wieder gelingt."""
    previous = services.connection_meta()
    return SetupState(
        ok=False,
        message=message,
        server_version=previous.get("server_version"),
        api_version=previous.get("api_version"),
        features=dict(previous.get("features") or {}),
        tag_id=previous.get("tag_id"),
        field_ids=dict(previous.get("field_ids") or {}),
        checked_at=timezone.now().isoformat(),
    )


def check(*, user=None, create: bool = False, request=None) -> SetupState:
    """Prueft die Verbindung und die Kennzeichnung; legt mit create fehlende Tags und Felder an (nicht im Modus
    readonly). Schreibt den Befund nach sync_cursors und ins Audit."""
    client = services.get_client()
    if client is None:
        state = _failed_state("Paperless ist nicht konfiguriert (Adresse oder Token fehlt)")
        services.set_cursor(SyncSystem.PAPERLESS, services.CONNECTION_CURSOR, None, state.as_meta())
        return state
    names = config.field_names()
    try:
        info = client.server_info()
        tags = list(client.list_tags())
        fields = list(client.list_custom_fields())
    except Exception as exc:  # jede Fehlerklasse des Clients wird als Befund gemeldet, nie verschluckt
        state = _failed_state(f"Verbindung fehlgeschlagen: {type(exc).__name__}: {exc}"[:500])
        services.set_cursor(SyncSystem.PAPERLESS, services.CONNECTION_CURSOR, None, state.as_meta())
        record(
            "sync.paperless_check",
            entity_type="sync",
            after={"ok": False, "error": state.message},
            actor=user,
            request=request,
        )
        return state
    can_write = create and config.mode() != config.MODE_READONLY
    created: list[str] = []
    tag = _by_name(tags, names.tag)
    if tag is None and can_write:
        tag = client.create_tag(names.tag)
        created.append(f"tag:{names.tag}")
    field_ids: dict[str, int] = {}
    missing: list[str] = []
    for key, name in (
        ("uuid", names.uuid),
        ("object", names.object),
        ("status", names.status),
        ("drive", names.drive),
    ):
        row = _by_name(fields, name)
        if row is None and can_write:
            row = client.create_custom_field(name, FIELD_TYPES[key])
            created.append(f"field:{name}")
        if row is None:
            missing.append(name)
        else:
            field_ids[key] = int(row["id"])
    if tag is None:
        missing.append(names.tag)
    features = dict(getattr(info, "features", {}) or {})
    state = SetupState(
        ok=not missing,
        message="Verbindung in Ordnung"
        if not missing
        else "Verbindung in Ordnung, Kennzeichnung unvollständig",
        server_version=getattr(info, "server_version", None),
        api_version=getattr(info, "api_version", None),
        features=features,
        tag_id=int(tag["id"]) if tag else None,
        field_ids=field_ids,
        missing=missing,
        checked_at=timezone.now().isoformat(),
    )
    services.set_cursor(
        SyncSystem.PAPERLESS, services.CONNECTION_CURSOR, state.server_version, state.as_meta()
    )
    record(
        "sync.paperless_setup" if created else "sync.paperless_check",
        entity_type="sync",
        after={
            "ok": state.ok,
            "server_version": state.server_version,
            "api_version": state.api_version,
            "created": created,
            "missing": missing,
        },
        actor=user,
        request=request,
    )
    return state


def state_from_cursor() -> SetupState | None:
    meta = services.connection_meta()
    if not meta:
        return None
    try:
        return SetupState(**{k: v for k, v in meta.items() if k in SetupState.__dataclass_fields__})
    except TypeError:
        return None
