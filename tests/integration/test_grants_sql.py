"""Rechte-SQL aus dem Modellregister (B-43): Aufbau und Wiederholbarkeit.

deploy.sh fuehrt die Ausgabe nach jeder Migration als root aus. Sie muss deshalb auch dann fehlerfrei
laufen, wenn die datenbankweiten Rechte bereits durch Tabellenrechte ersetzt wurden (erster Lauf).
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection

from apps.config.management.commands import grants_sql as GRANTS

pytestmark = pytest.mark.django_db


def _sql() -> list[str]:
    out = StringIO()
    call_command("grants_sql", stdout=out)
    return [z for z in out.getvalue().splitlines() if z and not z.startswith("--")]


def test_revoke_hat_immer_ein_recht_zum_entziehen():
    """Vor dem REVOKE steht ein datenbankweites GRANT, sonst bricht der zweite Lauf mit Fehler 1141 ab."""
    zeilen = _sql()
    grant_idx = next(
        i for i, z in enumerate(zeilen) if z.startswith("GRANT SELECT ON") and ".* TO 'app_rw'" in z
    )
    revoke_idx = next(i for i, z in enumerate(zeilen) if z.startswith("REVOKE ALL PRIVILEGES"))
    assert grant_idx < revoke_idx, "Das GRANT muss vor dem REVOKE stehen"
    assert revoke_idx == grant_idx + 1, "Zwischen GRANT und REVOKE darf keine weitere Anweisung stehen"


def test_tabellenrechte_fuer_alle_tabellen_und_append_only():
    zeilen = _sql()
    tabellen = set(connection.introspection.table_names())
    assert tabellen, "Die Testdatenbank enthaelt keine Tabellen"
    for tabelle in tabellen:
        assert any(f"`{tabelle}` TO 'app_rw'@'%'" in z for z in zeilen), f"app_rw fehlt fuer {tabelle}"
    # Append-only: kein UPDATE oder DELETE
    for z in zeilen:
        if "`audit_events`" in z or "`iban_access_log`" in z:
            assert "UPDATE" not in z and "DELETE" not in z, z
    # Chiffrate: kein Leserecht fuer die Auswertungsrolle
    assert not any("`users` TO 'app_ro'" in z for z in zeilen)
    assert not any("`owners` TO 'app_ro'" in z for z in zeilen)
    assert zeilen[-1] == "FLUSH PRIVILEGES;"


def test_worker_darf_die_von_ihm_neu_aufgebauten_tabellen_leeren():
    """Jobs ersetzen Seiten, Entitaeten und Verknuepfungen vollstaendig; ohne DELETE bricht der Job mit 1142 ab."""
    zeilen = _sql()
    for tabelle in GRANTS.WORKER_DELETE:
        treffer = [z for z in zeilen if f"`{tabelle}` TO 'app_worker'@'%'" in z]
        assert treffer, f"kein Recht fuer app_worker auf {tabelle}"
        assert all("DELETE" in z for z in treffer), f"DELETE fehlt fuer app_worker auf {tabelle}: {treffer}"
    # Gegenprobe: Tabellen ohne Neuaufbau bleiben ohne DELETE, damit das Rechtemodell eng bleibt.
    for tabelle in ("documents", "processing_runs", "audit_events"):
        for z in [z for z in zeilen if f"`{tabelle}` TO 'app_worker'@'%'" in z]:
            assert "DELETE" not in z, z


def test_jede_loeschbare_tabelle_ist_auch_schreibbar():
    assert GRANTS.WORKER_DELETE <= GRANTS.WORKER_WRITE
