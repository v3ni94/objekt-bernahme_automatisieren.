"""Konfiguration der Synchronisation (Schluessel paperless.* und sync.* aus apps.config.store, Secrets aus den
Settings). Alle Schreibzugriffe auf Paperless und Drive laufen ueber writes_allowed(obj): Hauptschalter, Modus
(readonly, pilot, full) und Pilotumfang entscheiden, ohne bereits gesicherte Daten anzutasten."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from apps.config import store

MODE_READONLY = "readonly"
MODE_PILOT = "pilot"
MODE_FULL = "full"


def enabled() -> bool:
    return bool(store.get("paperless.enabled", False))


def mode() -> str:
    value = store.get("paperless.mode", MODE_READONLY)
    return value if value in (MODE_READONLY, MODE_PILOT, MODE_FULL) else MODE_READONLY


def token() -> str:
    return (settings.OBJEKTAKTE.get("PAPERLESS_TOKEN") or "").strip()


def webhook_token() -> str:
    return (settings.OBJEKTAKTE.get("PAPERLESS_WEBHOOK_TOKEN") or "").strip()


def base_url() -> str | None:
    value = store.get("paperless.base_url", None)
    return value.rstrip("/") if isinstance(value, str) and value.strip() else None


def configured() -> bool:
    """Verbindung technisch konfiguriert (Adresse und Token vorhanden), unabhaengig vom Hauptschalter."""
    return bool(base_url() and token())


def active() -> bool:
    """Hauptschalter an und Verbindung konfiguriert."""
    return enabled() and configured()


def pilot_object_numbers() -> set[str]:
    raw = store.get("paperless.pilot_object_numbers", []) or []
    return {str(x).strip().lstrip("0") or "0" for x in raw if str(x).strip()}


def in_pilot_scope(obj) -> bool:
    if obj is None:
        return False
    if getattr(obj, "is_system_inbox", False):
        return True
    number = str(obj.object_number).lstrip("0") or "0"
    return number in pilot_object_numbers()


def writes_allowed(obj) -> bool:
    """Darf die Anwendung fuer dieses Objekt nach Paperless schreiben (Upload, Metadaten, Indexbelege)?"""
    if not active():
        return False
    m = mode()
    if m == MODE_FULL:
        return True
    if m == MODE_PILOT:
        return in_pilot_scope(obj)
    return False


def import_new_documents() -> bool:
    return bool(store.get("paperless.import_new_documents", True))


def webhook_enabled() -> bool:
    return bool(store.get("paperless.webhook_enabled", True))


def drive_changes_enabled() -> bool:
    return bool(store.get("sync.drive_changes_enabled", False))


def max_upload_bytes() -> int:
    return int(store.get("paperless.max_upload_mb", 100)) * 1024 * 1024


def operation_max_attempts() -> int:
    return int(store.get("sync.operation_max_attempts", 5))


def inventory_page_size() -> int:
    return int(store.get("sync.inventory_page_size", 200))


@dataclass(frozen=True)
class FieldNames:
    tag: str
    uuid: str
    object: str
    status: str
    drive: str


def field_names() -> FieldNames:
    return FieldNames(
        tag=str(store.get("paperless.tag_name", "MHV-Sync")),
        uuid=str(store.get("paperless.field_uuid_name", "MHV Dokument-UUID")),
        object=str(store.get("paperless.field_object_name", "MHV Objekt")),
        status=str(store.get("paperless.field_status_name", "MHV Zuordnung")),
        drive=str(store.get("paperless.field_drive_name", "MHV Drive-Link")),
    )


def assignment_thresholds() -> tuple[float, float]:
    return (
        float(store.get("sync.assignment_auto_min", 0.85)),
        float(store.get("sync.assignment_gap_min", 0.25)),
    )
