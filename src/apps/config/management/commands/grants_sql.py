"""Erzeugt die Tabellenrechte fuer die Datenbankkonten aus dem Modellregister (docs/architektur.md 9.4, B-43).

Regeln:
- app_rw (web, beat): SELECT, INSERT, UPDATE, DELETE auf allen Tabellen; auf append-only Tabellen nur SELECT, INSERT
- app_worker: SELECT auf allen Tabellen; INSERT, UPDATE auf Verarbeitungs-, Dokument-, Review-Fall- und Protokolltabellen;
  zusaetzlich DELETE auf den Tabellen, deren Inhalt ein Job vollstaendig neu aufbaut (WORKER_DELETE);
  INSERT auf audit_events; kein Schreibrecht auf Stammdaten, Nutzer und Konfiguration
- app_ro: SELECT auf allen Tabellen ohne Chiffratspalten (Tabellen mit Chiffraten werden ausgelassen)
deploy.sh fuehrt die Ausgabe nach jeder Migration als root aus; GRANT ist idempotent.
"""

from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection

APPEND_ONLY = {"audit_events", "iban_access_log"}
# Tabellen, auf die der Worker schreiben darf (wird je Meilenstein erweitert; M2 fuegt die Fachtabellen hinzu)
WORKER_WRITE = {
    "documents",
    "document_pages",
    "document_entities",
    "document_classifications",
    "document_owner_links",
    "document_tenant_links",
    "processing_runs",
    "processing_jobs",
    "processing_job_events",
    "object_progress",
    "review_cases",
    "ai_calls",
    "drive_nodes",
    "drive_sync_runs",
    "drive_sync_actions",
    "list_generations",
    "completeness_findings",
    "import_batches",
    "import_rows",
    "training_samples",
    "classifier_models",
    # Akten entstehen auch im Worker: decide (Eigentuemer- und Mieterakte bei der Ablage, F 6.3) und die
    # Akten-Vorlage nach dem Ordnerabgleich (ensure_unit_files). Befund 11.09.2026: decide scheiterte mit
    # "INSERT command denied ... owner_files", sobald ein Dokument einer Eigentuemergruppe zugeordnet wurde.
    "owner_files",
    "owner_file_assignments",
    "tenant_files",
    "tenant_file_assignments",
    # Ordnerabgleich im Worker schreibt den erkannten Objektordner an das Objekt
    "objects",
    # Synchronisation Paperless und Drive (12.09.2026): Operationen, Verknuepfungen, Cursor, Bestandslaeufe und
    # automatische Zuordnungsbeispiele entstehen in den Workern (Queue io)
    "document_versions",
    "sync_links",
    "sync_operations",
    "sync_cursors",
    "sync_inventory_runs",
    "sync_inventory_items",
    "sync_assignment_examples",
    "sync_assignment_rules",
}
WORKER_INSERT_ONLY = {"audit_events"}
# Tabellen, deren Inhalt ein Job je Dokument oder Lauf vollstaendig ersetzt: der alte Bestand wird geloescht und
# unmittelbar neu geschrieben. Ohne DELETE bricht der Job ab (Fehler 1142), weil sonst die Eindeutigkeit von
# Seite und Dokument verletzt wuerde. Wiederaufnahme nach Abbruch setzt genau dieses Verhalten voraus.
WORKER_DELETE = {
    "document_pages",  # pipeline.merge_pages
    "document_entities",  # pipeline.extract_entities
    "document_owner_links",  # classification.decide und review.bulk_execute, jeweils Status suggested
    "document_tenant_links",  # dito
    "completeness_findings",  # requirements.engine, veraltete Befunde
    "drive_sync_actions",  # drive.reconcile, Aktionen eines verworfenen Plans
    "import_rows",  # imports.services, Neuaufbau einer Stapeldatei
}
# Tabellen mit Chiffraten: kein Lesezugriff fuer app_ro
ENCRYPTED = {"oauth_tokens", "mfa_authenticator", "owners", "tenants", "users"}


class Command(BaseCommand):
    help = "GRANT-Anweisungen fuer app_rw, app_worker und app_ro ausgeben"

    def add_arguments(self, parser):
        parser.add_argument("--database", default=None, help="Datenbankname (Standard: aus den Settings)")

    def handle(self, *args, **options):
        db = options["database"] or connection.settings_dict["NAME"]
        # Modellregister und Datenbank zusammen: das Modellregister kennt auch Tabellen, die die Anwendungskennung
        # noch nicht sehen darf (frisch migriert, Rechte fehlen noch), die Datenbank kennt Tabellen ohne Modell
        # (Fremdschluesselketten, Bibliothek). Befund 11.09.2026: takeover_sources fehlte nach der Migration, weil
        # app_rw die neue Tabelle in der Introspektion nicht sah und deshalb kein Recht erhielt.
        model_tables = {m._meta.db_table for m in apps.get_models()} | {"django_migrations"}
        try:
            seen = set(connection.introspection.table_names())
        except Exception:  # ohne Verbindung (Vorschau) nur aus dem Modellregister
            seen = set()
        tables = sorted(seen | model_tables)
        lines = [f"-- erzeugt von grants_sql fuer Datenbank {db}; idempotent"]
        # Das Image legt app_rw mit allen Rechten auf die Datenbank an; diese werden hier durch Tabellenrechte
        # ersetzt. REVOKE bricht mit Fehler 1141 ab, wenn kein datenbankweites Recht (mehr) existiert, also ab
        # dem zweiten Lauf. Ein vorangestelltes GRANT stellt sicher, dass immer genau ein solches Recht besteht;
        # es wird unmittelbar danach mit entzogen, sodass am Ende nur die Tabellenrechte gelten.
        lines.append(f"GRANT SELECT ON `{db}`.* TO 'app_rw'@'%';")
        lines.append(f"REVOKE ALL PRIVILEGES ON `{db}`.* FROM 'app_rw'@'%';")
        for t in tables:
            rights_rw = "SELECT, INSERT" if t in APPEND_ONLY else "SELECT, INSERT, UPDATE, DELETE"
            lines.append(f"GRANT {rights_rw} ON `{db}`.`{t}` TO 'app_rw'@'%';")
            if t in WORKER_INSERT_ONLY:
                lines.append(f"GRANT SELECT, INSERT ON `{db}`.`{t}` TO 'app_worker'@'%';")
            elif t in WORKER_WRITE:
                rights_worker = (
                    "SELECT, INSERT, UPDATE, DELETE" if t in WORKER_DELETE else "SELECT, INSERT, UPDATE"
                )
                lines.append(f"GRANT {rights_worker} ON `{db}`.`{t}` TO 'app_worker'@'%';")
            else:
                lines.append(f"GRANT SELECT ON `{db}`.`{t}` TO 'app_worker'@'%';")
            if t not in ENCRYPTED:
                lines.append(f"GRANT SELECT ON `{db}`.`{t}` TO 'app_ro'@'%';")
        lines.append("FLUSH PRIVILEGES;")
        self.stdout.write("\n".join(lines) + "\n")
