"""Ablaeufe der Synchronisation. Die Handler registrieren sich beim Import ueber operations.handler; load_all()
importiert alle Module, damit die Registrierung vor dem ersten Lauf vollstaendig ist."""

from __future__ import annotations

import importlib

MODULES = (
    "apps.sync.flows.paperless_push",
    "apps.sync.flows.paperless_pull",
    "apps.sync.flows.paperless_meta",
    "apps.sync.flows.index_stub",
    "apps.sync.flows.snapshot",
    "apps.sync.flows.drive_props",
    "apps.sync.flows.assign",
)


def load_all() -> None:
    for name in MODULES:
        importlib.import_module(name)
