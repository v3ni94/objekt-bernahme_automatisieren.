"""Zugriff auf Geheimnisse und Startparameter.

Regel aus docs/betrieb.md 3.7 und 3.8: Geheimnisse liegen als Dateien unter /run/secrets/ und werden
ueber Variablen mit dem Suffix _FILE referenziert. Ein Wert direkt in der Umgebungsvariable ist nur fuer
die lokale Entwicklung und Tests zulaessig. Die Werte werden nie protokolliert.
"""

from __future__ import annotations

import os
from pathlib import Path


class SecretMissing(RuntimeError):
    """Ein erforderliches Geheimnis fehlt."""


def read_secret(name: str, default: str | None = None, required: bool = False) -> str | None:
    """Liest NAME_FILE (Pfad) oder NAME (Wert). Reihenfolge: Datei vor Umgebungsvariable."""
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        path = Path(file_var)
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        elif required:
            raise SecretMissing(f"{name}_FILE verweist auf eine fehlende Datei")
    value = os.environ.get(name)
    if value:
        return value
    if required and default is None:
        raise SecretMissing(f"Geheimnis {name} fehlt (weder {name}_FILE noch {name} gesetzt)")
    return default


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} muss eine ganze Zahl sein, ist aber {raw!r}") from exc


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "ja", "on"}


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]
