"""Gemeinsame Dienste der Synchronisation: Fabriken fuer die externen Clients (in Tests per monkeypatch ersetzbar),
Verbindungsbefund aus sync_cursors, lokale Dateikopie eines Dokuments, Beschriftungen fuer Paperless-Felder."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from django.utils import timezone

from apps.sync import config
from apps.sync.models import SyncCursor, SyncSystem

CONNECTION_CURSOR = "connection"


def get_client():
    """Paperless-Client aus der Konfiguration; None ohne Adresse oder Token. Tests ersetzen diese Funktion."""
    if not config.configured():
        return None
    from apps.sync.paperless.client import from_settings

    return from_settings()


def get_drive():
    """Drive-Adapter der bestehenden Verbindung (oder None)."""
    from apps.drive import oauth

    return oauth.get_adapter()


def connection_meta() -> dict:
    row = SyncCursor.objects.filter(system=SyncSystem.PAPERLESS, name=CONNECTION_CURSOR).first()
    return dict(row.meta or {}) if row else {}


def set_cursor(system: str, name: str, value: str | None, meta: dict | None = None) -> SyncCursor:
    row, _ = SyncCursor.objects.update_or_create(
        system=system, name=name, defaults={"value": value, "meta": meta if meta is not None else None}
    )
    return row


def get_cursor(system: str, name: str) -> SyncCursor | None:
    return SyncCursor.objects.filter(system=system, name=name).first()


def interval_elapsed(system: str, name: str, minutes: int) -> bool:
    row = get_cursor(system, name)
    if row is None or not row.value:
        return True
    try:
        last = timezone.datetime.fromisoformat(row.value)
    except ValueError:
        return True
    if timezone.is_naive(last):
        last = timezone.make_aware(last)
    return (timezone.now() - last).total_seconds() >= minutes * 60


def mark_now(system: str, name: str, meta: dict | None = None) -> None:
    set_cursor(system, name, timezone.now().isoformat(), meta)


def object_label(obj) -> str:
    """Objektbezug fuer Paperless: Nummer, Ort und Strasse (keine Personendaten)."""
    if obj is None:
        return ""
    if getattr(obj, "is_system_inbox", False):
        return "Eingang (nicht zugeordnet)"
    parts = [obj.object_number]
    if obj.city:
        parts.append(obj.city)
    street = " ".join(p for p in [obj.street, obj.house_number] if p)
    if street:
        parts.append(street)
    return " ".join(parts[:1]) + (", ".join([""] + parts[1:]) if len(parts) > 1 else "")


def drive_link(file_id: str | None) -> str | None:
    return f"https://drive.google.com/file/d/{file_id}/view" if file_id else None


def assignment_status(doc) -> str:
    if getattr(doc.object, "is_system_inbox", False):
        return "Eingang"
    if doc.status == "filed":
        return "zugeordnet und abgelegt"
    if doc.status in ("review",):
        return "in Prüfung"
    return "zugeordnet"


@contextlib.contextmanager
def local_copy(doc) -> Iterator[Path]:
    """Liefert einen Pfad zur Originaldatei: Arbeitsverzeichnis, Transitdatei oder Download aus Drive in ein
    temporaeres Verzeichnis (wird danach entfernt)."""
    from apps.pipeline import storage

    if doc.sha256:
        work = storage.work_dir(doc.sha256)
        for candidate in sorted(work.glob("original.*")) if work.exists() else []:
            yield candidate
            return
    if doc.source_path and Path(doc.source_path).exists():
        yield Path(doc.source_path)
        return
    if not doc.drive_file_id:
        raise FileNotFoundError("keine lokale Datei und keine Drive-Datei")
    drive = get_drive()
    if drive is None:
        raise ConnectionError("keine Google-Verbindung für den Download")
    tmp = Path(tempfile.mkdtemp(prefix="sync-"))
    try:
        suffix = Path(doc.current_name or doc.original_name or "datei").suffix or ".bin"
        target = tmp / f"original{suffix}"
        drive.download(doc.drive_file_id, target)
        yield target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
