"""Trigger gegen UPDATE und DELETE auf audit_events und iban_access_log (Beschluss B-43, docs/architektur.md 9.5).

MariaDB-spezifisch (SIGNAL SQLSTATE). Rueckwaerts: Trigger entfernen. Die Anwendungskonten haben die
Rechte UPDATE und DELETE auf diese Tabellen zusaetzlich nicht (docker/db/init/02_grants.sql).
"""

from django.db import migrations

TABLES = ("audit_events", "iban_access_log")


def _create(table: str) -> str:
    return f"""
CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table}
FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} ist append-only: UPDATE nicht erlaubt';
CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table}
FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} ist append-only: DELETE nicht erlaubt';
"""


def _drop(table: str) -> str:
    return f"""
DROP TRIGGER IF EXISTS trg_{table}_no_update;
DROP TRIGGER IF EXISTS trg_{table}_no_delete;
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(sql=_create(table), reverse_sql=_drop(table)) for table in TABLES]
