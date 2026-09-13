"""Speicherpfade in Paperless als Quelle der Objektzuordnung fuer den Altbestand (Anforderung 13.09.2026): Der
fuehrende Zahlenblock eines Speicherpfads („82 – Shalomweg 3, Hueckelhoven“) wird als Objektnummer gelesen und
gegen die aktiven Objekte der Anwendung geprueft. Je Pfad kann das Feld MHV Objekt fuer alle Dokumente ohne Wert in
Paketen gesetzt werden (bulk_edit, kein Download). Vorhandene Werte werden nie ueberschrieben; abweichende Werte
bleiben als Befund sichtbar. Danach startet ein echter Bestandslauf fuer genau diese Objektnummer, damit die
Uebernahme auch ohne Webhook anlaeuft. Stand je Pfad in SyncCursor (system paperless, name field_fill:<id>)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from apps.audit.services import record
from apps.objects.models import ManagedObject
from apps.sync import config, inventory, services
from apps.sync.models import SyncCursor, SyncSystem
from apps.sync.paperless.errors import PaperlessError

logger = logging.getLogger(__name__)

STATE_PREFIX = "field_fill:"
LIST_CURSOR = "storage_paths"
LIST_MAX_AGE_SECONDS = 3600
CHUNK = 100
_LEADING_NUMBER = re.compile(r"^\s*(\d{1,6})(?!\d)")


class StoragePathError(Exception):
    pass


def derive_object_number(name: str | None) -> str | None:
    """Objektnummer aus dem fuehrenden Zahlenblock eines Pfadnamens, ohne fuehrende Nullen; None ohne Zahl."""
    match = _LEADING_NUMBER.match(name or "")
    if not match:
        return None
    return str(int(match.group(1)))


def object_for_number(number: str | None) -> ManagedObject | None:
    if not number or not str(number).isdigit():
        return None
    return (
        ManagedObject.active.filter(object_number_numeric=int(number), is_system_inbox=False)
        .order_by("id")
        .first()
    )


def _client():
    client = services.get_client()
    if client is None:
        raise StoragePathError("Paperless nicht konfiguriert (Adresse oder Token fehlt)")
    return client


def _field_id() -> int:
    field_id = (services.connection_meta().get("field_ids") or {}).get("object")
    if not field_id:
        raise StoragePathError(
            "Kennzeichnung nicht eingerichtet: Feld MHV Objekt fehlt (Verbindung prüfen, Kennzeichnung einrichten)"
        )
    return int(field_id)


def _field_value(doc: dict, field_id: int):
    for cf in doc.get("custom_fields") or []:
        if int(cf.get("field", 0)) == field_id:
            return cf.get("value")
    return None


def _number_of(value) -> str | None:
    text = str(value or "").split(",")[0].split(" ")[0].strip()
    return str(int(text)) if text.isdigit() else None


# ---------------------------------------------------------------- Uebersicht
@dataclass
class PathRow:
    id: int
    name: str
    path: str
    document_count: int
    number: str | None
    object: ManagedObject | None
    state: dict = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return self.object is not None


def states() -> dict[int, dict]:
    rows = SyncCursor.objects.filter(system=SyncSystem.PAPERLESS, name__startswith=STATE_PREFIX)
    out: dict[int, dict] = {}
    for row in rows:
        try:
            out[int(row.name.removeprefix(STATE_PREFIX))] = dict(row.meta or {})
        except ValueError:
            continue
    return out


def _set_state(path_id: int, meta: dict) -> None:
    services.set_cursor(SyncSystem.PAPERLESS, f"{STATE_PREFIX}{int(path_id)}", str(path_id), meta)


def cached_paths(*, refresh: bool = False) -> tuple[list[dict], str | None]:
    """Speicherpfade aus dem Zwischenspeicher (Cursor storage_paths) oder frisch aus Paperless. Liefert die Liste und
    den Zeitpunkt des Lesens. Die vollstaendige Liste mit Dokumentzaehlern ist auf grossen Instanzen langsam, daher
    wird sie hoechstens einmal je LIST_MAX_AGE_SECONDS oder auf Wunsch neu gelesen."""
    row = services.get_cursor(SyncSystem.PAPERLESS, LIST_CURSOR)
    if row is not None and not refresh and row.value:
        try:
            age = (timezone.now() - timezone.datetime.fromisoformat(row.value)).total_seconds()
        except ValueError:
            age = LIST_MAX_AGE_SECONDS + 1
        if age <= LIST_MAX_AGE_SECONDS:
            return list((row.meta or {}).get("paths") or []), row.value
    client = _client()
    try:
        paths = client.list_storage_paths()
    except PaperlessError as exc:
        raise StoragePathError(f"Speicherpfade konnten nicht gelesen werden: {exc}") from exc
    slim = [
        {
            "id": int(p["id"]),
            "name": str(p.get("name") or ""),
            "path": str(p.get("path") or ""),
            "document_count": int(p.get("document_count") or 0),
        }
        for p in paths
    ]
    now = timezone.now().isoformat()
    services.set_cursor(SyncSystem.PAPERLESS, LIST_CURSOR, now, {"paths": slim, "count": len(slim)})
    return slim, now


def overview(*, refresh: bool = False) -> tuple[list[PathRow], str | None]:
    paths, read_at = cached_paths(refresh=refresh)
    known = states()
    rows: list[PathRow] = []
    for p in paths:
        number = derive_object_number(p["name"])
        rows.append(
            PathRow(
                id=p["id"],
                name=p["name"],
                path=p["path"],
                document_count=p["document_count"],
                number=number,
                object=object_for_number(number),
                state=known.get(p["id"], {}),
            )
        )
    rows.sort(key=lambda r: (not r.matched, r.number is None, int(r.number or 0), r.name.casefold()))
    return rows, read_at


# ---------------------------------------------------------------- Vorschau und Befuellung
@dataclass
class FillPlan:
    path_id: int
    path_name: str
    number: str
    object: ManagedObject
    field_id: int
    missing: list[int] = field(default_factory=list)
    same: list[int] = field(default_factory=list)
    other: dict[int, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.missing) + len(self.same) + len(self.other)

    def summary(self) -> dict:
        return {
            "path_id": self.path_id,
            "path_name": self.path_name,
            "object_number": self.object.object_number,
            "total": self.total,
            "missing": len(self.missing),
            "same": len(self.same),
            "other": len(self.other),
            "other_ids": sorted(self.other)[:50],
        }


def plan(path_id: int) -> FillPlan:
    """Liest die Dokumente eines Speicherpfads (nur id und custom_fields) und teilt sie in ohne Wert, gleicher Wert
    und abweichender Wert ein. Schreibt nichts."""
    client = _client()
    try:
        path = client.get_storage_path(int(path_id))
    except PaperlessError as exc:
        raise StoragePathError(f"Speicherpfad {path_id} nicht lesbar: {exc}") from exc
    number = derive_object_number(path.get("name"))
    if number is None:
        raise StoragePathError(f"Speicherpfad „{path.get('name')}“ beginnt nicht mit einer Objektnummer")
    obj = object_for_number(number)
    if obj is None:
        raise StoragePathError(
            f"Kein aktives Objekt mit der Nummer {number} angelegt (Speicherpfad „{path.get('name')}“)"
        )
    field_id = _field_id()
    result = FillPlan(
        path_id=int(path_id),
        path_name=str(path.get("name") or ""),
        number=number,
        object=obj,
        field_id=field_id,
    )
    try:
        docs = client.list_documents(storage_path=int(path_id), fields=["id", "custom_fields"], ordering="id")
        for doc in docs:
            doc_id = int(doc["id"])
            value = _field_value(doc, field_id)
            if value is None or str(value).strip() == "":
                result.missing.append(doc_id)
            elif _number_of(value) == str(obj.object_number_numeric):
                result.same.append(doc_id)
            else:
                result.other[doc_id] = str(value)
    except PaperlessError as exc:
        raise StoragePathError(f"Dokumente des Speicherpfads {path_id} nicht lesbar: {exc}") from exc
    return result


def fill(path_id: int, *, user=None, request=None, start_inventory: bool = True) -> dict:
    """Setzt das Feld MHV Objekt fuer alle Dokumente des Pfads ohne Wert in Paketen und startet danach den echten
    Bestandslauf fuer die Objektnummer. Abweichende Werte bleiben unveraendert und werden gezaehlt."""
    if not config.active():
        raise StoragePathError("Hauptschalter paperless.enabled aus oder Paperless nicht konfiguriert")
    client = _client()
    p = plan(path_id)
    if not config.writes_allowed(p.object):
        raise StoragePathError(
            f"Objekt {p.object.object_number} liegt außerhalb des Modus oder Pilotumfangs (paperless.mode)"
        )
    started = timezone.now().isoformat()
    _set_state(path_id, {"status": "running", "started_at": started, **p.summary(), "set": 0})
    done = 0
    try:
        for start in range(0, len(p.missing), CHUNK):
            chunk = p.missing[start : start + CHUNK]
            client.bulk_edit(
                chunk,
                "modify_custom_fields",
                {"add_custom_fields": {str(p.field_id): p.object.object_number}, "remove_custom_fields": []},
            )
            done += len(chunk)
            _set_state(path_id, {"status": "running", "started_at": started, **p.summary(), "set": done})
    except PaperlessError as exc:
        meta = {
            "status": "failed",
            "started_at": started,
            "finished_at": timezone.now().isoformat(),
            **p.summary(),
            "set": done,
            "error": str(exc)[:500],
        }
        _set_state(path_id, meta)
        record(
            "sync.paperless_field_fill",
            entity_type="object",
            entity_id=p.object.pk,
            object_id=p.object.pk,
            after=meta,
            actor=user,
            request=request,
        )
        raise StoragePathError(f"Abbruch nach {done} von {len(p.missing)} Dokumenten: {exc}") from exc
    meta = {
        "status": "done",
        "started_at": started,
        "finished_at": timezone.now().isoformat(),
        **p.summary(),
        "set": done,
    }
    if start_inventory and p.total:
        try:
            run = inventory.start(
                SyncSystem.PAPERLESS,
                dry_run=False,
                scope={"object_numbers": [p.object.object_number]},
                user=user,
                request=request,
            )
            meta["inventory_run_id"] = run.pk
        except inventory.InventoryError as exc:
            meta["inventory_error"] = str(exc)
    _set_state(path_id, meta)
    record(
        "sync.paperless_field_fill",
        entity_type="object",
        entity_id=p.object.pk,
        object_id=p.object.pk,
        after=meta,
        actor=user,
        request=request,
    )
    return meta


def dispatch_fill(path_id: int, *, user=None, request=None) -> dict | None:
    """Befuellung im Hintergrund (Celery, Warteschlange io); ohne Celery sofort. Liefert das Ergebnis, wenn es
    sofort vorliegt, sonst None."""
    p = plan(path_id)  # Vorpruefung: Nummer, Objekt, Kennzeichnung, Lesbarkeit
    if not config.writes_allowed(p.object):
        raise StoragePathError(
            f"Objekt {p.object.object_number} liegt außerhalb des Modus oder Pilotumfangs (paperless.mode)"
        )
    if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "celery":
        from apps.sync.tasks import storage_path_fill_task

        _set_state(
            path_id, {"status": "queued", "queued_at": timezone.now().isoformat(), **p.summary(), "set": 0}
        )
        storage_path_fill_task.apply_async(args=[int(path_id), getattr(user, "pk", None)], queue="io")
        return None
    return fill(path_id, user=user, request=request)
