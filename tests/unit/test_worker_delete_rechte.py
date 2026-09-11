"""Jede Loeschstelle ausserhalb der Web-Ansichten muss fuer app_worker erlaubt sein (Annahme A32).

Hintergrund: Am 11.09.2026 brach pipeline.merge_pages auf dem Server mit Fehler 1142 ab, weil app_worker
kein DELETE auf document_pages hatte. Die Rechteliste war nie gegen den Quellcode geprueft. Dieser Test
findet Aufrufe der Form `Modell.objects...delete()` in allen Modulen, die aus Jobs erreichbar sind, und
verlangt, dass die zugehoerige Tabelle in WORKER_DELETE steht. Views laufen als app_rw und bleiben aussen vor.
"""

from __future__ import annotations

import ast
from pathlib import Path

import django
from django.conf import settings

from apps.config.management.commands.grants_sql import WORKER_DELETE

# Nur Web-Ansichten und Formulare laufen ausschliesslich unter app_rw; alles andere kann in einem Job landen.
NUR_WEB = {"views.py", "forms.py", "urls.py", "admin.py", "apps.py"}


def _modelltabellen() -> dict[str, str]:
    django.setup()
    from django.apps import apps as django_apps

    return {m.__name__: m._meta.db_table for m in django_apps.get_models()}


def _wurzelname(knoten: ast.AST) -> str | None:
    """Linkester Name einer Attributkette: DocumentPage.objects.filter(...).delete() ergibt DocumentPage."""
    while True:
        if isinstance(knoten, ast.Name):
            return knoten.id
        if isinstance(knoten, ast.Attribute):
            knoten = knoten.value
        elif isinstance(knoten, ast.Call):
            knoten = knoten.func
        else:
            return None


def _loeschstellen() -> list[tuple[str, int, str]]:
    treffer: list[tuple[str, int, str]] = []
    wurzel = Path(settings.REPO_DIR) / "src" / "apps"
    for pfad in sorted(wurzel.rglob("*.py")):
        if pfad.name in NUR_WEB or "migrations" in pfad.parts or "tests" in pfad.parts:
            continue
        baum = ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
        for knoten in ast.walk(baum):
            if (
                isinstance(knoten, ast.Call)
                and isinstance(knoten.func, ast.Attribute)
                and knoten.func.attr == "delete"
            ):
                name = _wurzelname(knoten.func.value)
                if name:
                    treffer.append((str(pfad.relative_to(settings.REPO_DIR)), knoten.lineno, name))
    return treffer


def test_jede_loeschstelle_in_jobmodulen_ist_fuer_den_worker_erlaubt():
    tabellen = _modelltabellen()
    fehler = []
    for pfad, zeile, name in _loeschstellen():
        tabelle = tabellen.get(name)
        if tabelle is None:  # kein Modell, etwa cache.delete oder eine lokale Variable
            continue
        if tabelle not in WORKER_DELETE:
            fehler.append(f"{pfad}:{zeile} löscht {tabelle} ({name}), fehlt in WORKER_DELETE")
    assert not fehler, "Rechteliste unvollständig:\n" + "\n".join(fehler)


def test_worker_delete_enthaelt_nur_bekannte_tabellen():
    tabellen = set(_modelltabellen().values())
    unbekannt = WORKER_DELETE - tabellen
    assert not unbekannt, f"WORKER_DELETE nennt Tabellen ohne Modell: {sorted(unbekannt)}"
