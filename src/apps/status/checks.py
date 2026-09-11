"""Pruefungen fuer /readyz/ und die Statusseite (docs/architektur.md 10.4)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def check_database() -> dict:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def check_schema() -> dict:
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        applied = len(executor.loader.applied_migrations)
        return {"ok": not plan, "pending": len(plan), "applied": applied}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def check_redis() -> dict:
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        client.ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def check_disk() -> dict:
    data_dir: Path = settings.OBJEKTAKTE["DATA_DIR"]
    target = data_dir if data_dir.exists() else Path("/")
    usage = shutil.disk_usage(target)
    free_gb = usage.free / 1024**3
    reserve = settings.OBJEKTAKTE["DISK_RESERVE_GB"]
    return {
        "ok": free_gb >= reserve,
        "free_gb": round(free_gb, 1),
        "reserve_gb": reserve,
        "path": str(target),
    }


def heartbeats() -> dict:
    result: dict[str, dict] = {}
    stale = settings.OBJEKTAKTE["HEARTBEAT_STALE_SECONDS"]
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        for service in settings.OBJEKTAKTE["SERVICES_WITH_HEARTBEAT"]:
            raw = client.get(f"heartbeat:{service}")
            if raw is None:
                result[service] = {"ok": False, "age_s": None}
            else:
                age = int(time.time()) - int(raw)
                result[service] = {"ok": age <= stale, "age_s": age}
    except Exception as exc:
        for service in settings.OBJEKTAKTE["SERVICES_WITH_HEARTBEAT"]:
            result[service] = {"ok": False, "error": exc.__class__.__name__}
    return result


def backup_status() -> dict:
    path: Path = settings.OBJEKTAKTE["DATA_DIR"] / "backup" / "status.json"
    try:
        if not path.exists():
            return {"ok": False, "status": "keine status.json"}
    except OSError as exc:  # Verzeichnis nicht betretbar (Rechte): Befund statt Serverfehler
        return {"ok": False, "status": "status.json nicht lesbar", "error": exc.__class__.__name__}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age_h = (time.time() - path.stat().st_mtime) / 3600
        return {"ok": data.get("status") == "ok" and age_h < 26, "age_h": round(age_h, 1), **data}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def oauth_status() -> dict:
    try:
        from apps.drive.oauth import token_status

        status = token_status()
        return {**status, "status": status.get("text", status.get("status"))}
    except Exception as exc:  # Datenbank nicht erreichbar: readyz meldet das ueber database
        return {"ok": None, "status": "unbekannt", "error": exc.__class__.__name__}


def readiness() -> tuple[bool, dict]:
    report = {
        "database": check_database(),
        "schema": check_schema(),
        "redis": check_redis(),
        "disk": check_disk(),
        "oauth": oauth_status(),
    }
    ok = all(v["ok"] for k, v in report.items() if k != "oauth")
    return ok, report


def storage_usage() -> dict:
    """Belegung der Arbeitsverzeichnisse transit, ocr-cache, work, previews, lists in MB (G 9.3), fuenf Minuten im Cache."""
    from django.core.cache import cache

    cached = cache.get("status:storage_usage")
    if cached is not None:
        return cached
    data_dir: Path = settings.OBJEKTAKTE["DATA_DIR"]
    out: dict[str, float] = {}
    for name in ("transit", "ocr-cache", "work", "previews", "lists", "requests"):
        root = data_dir / name
        total = 0
        if root.exists():
            for p in root.rglob("*"):
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    continue
        out[name] = round(total / 1024**2, 1)
    cache.set("status:storage_usage", out, timeout=300)
    return out
