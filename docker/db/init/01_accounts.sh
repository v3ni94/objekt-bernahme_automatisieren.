#!/bin/sh
# Laeuft einmalig beim ersten Start des MariaDB-Containers (leeres Datenverzeichnis), docs/betrieb.md 3.9.
# Legt die Konten an. Tabellenrechte fuer app_rw, app_worker und app_ro setzt deploy.sh nach jeder Migration
# ueber die von der Anwendung erzeugte SQL (app-grants-sql), weil die Tabellen erst dann existieren.
set -eu
pw() { cat "/run/secrets/$1"; }
# Datenbankname mit maskierten Unterstrichen fuer die Scratch-Datenbanken: im Datenbankteil eines GRANT sind _ und %
# Platzhalter wie in LIKE; nur maskiert gilt das Recht fuer genau diesen Namen (26.09.2026).
DB_MASK="$(printf '%s' "$MARIADB_DATABASE" | sed 's/_/\\_/g; s/%/\\%/g')"
mariadb -uroot -p"$(pw db_root_password)" <<SQL
CREATE USER IF NOT EXISTS 'app_migrate'@'%' IDENTIFIED BY '$(pw db_migrate_password)';
CREATE USER IF NOT EXISTS 'app_worker'@'%'  IDENTIFIED BY '$(pw db_worker_password)';
CREATE USER IF NOT EXISTS 'app_backup'@'%'  IDENTIFIED BY '$(pw db_backup_password)';
CREATE USER IF NOT EXISTS 'app_ro'@'%'      IDENTIFIED BY '$(pw db_ro_password)';
GRANT ALL PRIVILEGES ON \`${MARIADB_DATABASE}\`.* TO 'app_migrate'@'%';
GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, EVENT ON \`${MARIADB_DATABASE}\`.* TO 'app_backup'@'%';
-- Scratch-Datenbanken der Deployment-Tests (26.09.2026, docs/betrieb/deployment-test.md): T6 spielt den Dump als
-- app_backup in <db>_restore_probe ein, T8 migriert als app_migrate in <db>_probe; beide brauchen CREATE und DROP
-- auf genau diesen Namen (Unterstriche maskiert, sonst Platzhalter). Bestehende Installationen erhalten das Recht
-- ueber grants_sql bei jedem Deploy.
GRANT ALL PRIVILEGES ON \`${DB_MASK}\_restore\_probe\`.* TO 'app_backup'@'%';
GRANT ALL PRIVILEGES ON \`${DB_MASK}\_probe\`.* TO 'app_migrate'@'%';
-- app_rw wird vom Image angelegt (MARIADB_USER) und erhaelt zunaechst alle Rechte auf die Datenbank;
-- deploy.sh ersetzt diese durch Tabellenrechte, sobald das Schema steht.
FLUSH PRIVILEGES;
SQL
echo "Datenbankkonten angelegt"
