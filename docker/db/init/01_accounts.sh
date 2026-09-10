#!/bin/sh
# Laeuft einmalig beim ersten Start des MariaDB-Containers (leeres Datenverzeichnis), docs/betrieb.md 3.9.
# Legt die Konten an. Tabellenrechte fuer app_rw, app_worker und app_ro setzt deploy.sh nach jeder Migration
# ueber die von der Anwendung erzeugte SQL (app-grants-sql), weil die Tabellen erst dann existieren.
set -eu
pw() { cat "/run/secrets/$1"; }
mariadb -uroot -p"$(pw db_root_password)" <<SQL
CREATE USER IF NOT EXISTS 'app_migrate'@'%' IDENTIFIED BY '$(pw db_migrate_password)';
CREATE USER IF NOT EXISTS 'app_worker'@'%'  IDENTIFIED BY '$(pw db_worker_password)';
CREATE USER IF NOT EXISTS 'app_backup'@'%'  IDENTIFIED BY '$(pw db_backup_password)';
CREATE USER IF NOT EXISTS 'app_ro'@'%'      IDENTIFIED BY '$(pw db_ro_password)';
GRANT ALL PRIVILEGES ON \`${MARIADB_DATABASE}\`.* TO 'app_migrate'@'%';
GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, EVENT ON \`${MARIADB_DATABASE}\`.* TO 'app_backup'@'%';
-- app_rw wird vom Image angelegt (MARIADB_USER) und erhaelt zunaechst alle Rechte auf die Datenbank;
-- deploy.sh ersetzt diese durch Tabellenrechte, sobald das Schema steht.
FLUSH PRIVILEGES;
SQL
echo "Datenbankkonten angelegt"
