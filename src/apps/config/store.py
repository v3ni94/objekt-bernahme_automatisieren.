"""Typisierter Zugriff auf app_settings mit Cache (docs/architektur.md 3.1, Konfiguration).

get(key) liest aus dem Cache (Versionsschluessel), faellt auf die Datenbank und zuletzt auf den Seed-Wert
zurueck. set(key, value, user, reason) validiert gegen das Schema des Katalogs, schreibt, protokolliert und
macht den Cache ungueltig.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import jsonschema
from django.conf import settings as dj_settings
from django.core.cache import cache
from django.db import transaction

from .models import AppSetting

VERSION_KEY = "settings:version"
KEY_PREFIX = "settings:v{version}:{key}"


class UnknownSetting(KeyError):
    pass


class InvalidSetting(ValueError):
    pass


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, Any]]:
    path = dj_settings.OBJEKTAKTE["CATALOG_FILE"]
    data = json.loads(path.read_text(encoding="utf-8"))
    return {entry["key"]: entry for entry in data["settings"]}


@lru_cache(maxsize=1)
def seeds() -> dict[str, Any]:
    path = dj_settings.OBJEKTAKTE["SEED_DIR"] / "app_settings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def categories() -> list[str]:
    seen: list[str] = []
    for entry in catalog().values():
        if entry["category"] not in seen:
            seen.append(entry["category"])
    return seen


def validate(key: str, value: Any) -> None:
    entry = catalog().get(key)
    if entry is None:
        raise UnknownSetting(key)
    schema = entry.get("schema")
    if schema:
        try:
            jsonschema.validate(value, schema)
        except jsonschema.ValidationError as exc:
            raise InvalidSetting(f"{key}: {exc.message}") from exc


def _version() -> int:
    version = cache.get(VERSION_KEY)
    if version is None:
        version = 1
        cache.set(VERSION_KEY, version, None)
    return int(version)


def invalidate() -> None:
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        cache.set(VERSION_KEY, 2, None)


def get(key: str, default: Any = None) -> Any:
    if key not in catalog():
        raise UnknownSetting(key)
    cache_key = KEY_PREFIX.format(version=_version(), key=key)
    sentinel = object()
    cached = cache.get(cache_key, sentinel)
    if cached is not sentinel:
        return cached
    row = AppSetting.objects.filter(key=key).values_list("value", flat=True).first()
    value = row if row is not None else seeds().get(key, default)
    cache.set(cache_key, value, 300)
    return value


def get_many(category: str) -> dict[str, Any]:
    return {k: get(k) for k, entry in catalog().items() if entry["category"] == category}


@transaction.atomic
def set(key: str, value: Any, *, user=None, reason: str | None = None, request=None) -> AppSetting:  # noqa: A001
    from apps.audit.services import record

    validate(key, value)
    entry = catalog()[key]
    row, created = AppSetting.objects.select_for_update().get_or_create(
        key=key,
        defaults={
            "value": value,
            "value_type": entry["value_type"],
            "category": entry["category"],
            "description": entry["description"],
            "validation": entry.get("schema"),
            "updated_by": user,
        },
    )
    before = None if created else row.value
    if not created:
        row.value = value
        row.updated_by = user
        row.description = entry["description"]
        row.validation = entry.get("schema")
        row.save(update_fields=["value", "updated_by", "description", "validation", "updated_at"])
    record(
        "setting.update",
        entity_type="app_setting",
        request=request,
        actor=user,
        reason=reason,
        before={"key": key, "value": before},
        after={"key": key, "value": value},
    )
    invalidate()
    return row


def seed_missing(*, force: bool = False, actor=None) -> tuple[int, int]:
    """Legt fehlende Schluessel aus den Seeds an; mit force werden vorhandene ueberschrieben."""
    from apps.audit.services import record

    created = updated = 0
    for key, value in seeds().items():
        entry = catalog().get(key)
        if entry is None:
            raise UnknownSetting(f"Seed-Schluessel {key} fehlt im Katalog")
        validate(key, value)
        row = AppSetting.objects.filter(key=key).first()
        if row is None:
            AppSetting.objects.create(
                key=key,
                value=value,
                value_type=entry["value_type"],
                category=entry["category"],
                description=entry["description"],
                validation=entry.get("schema"),
                is_secret=entry.get("is_secret", False),
            )
            created += 1
        elif force and row.value != value:
            record(
                "setting.update",
                entity_type="app_setting",
                actor=actor,
                reason="seed --force",
                before={"key": key, "value": row.value},
                after={"key": key, "value": value},
            )
            row.value = value
            row.save(update_fields=["value", "updated_at"])
            updated += 1
    for key, entry in catalog().items():
        AppSetting.objects.filter(key=key).exclude(description=entry["description"]).update(
            description=entry["description"]
        )
    invalidate()
    return created, updated
