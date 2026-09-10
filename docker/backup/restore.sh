#!/bin/sh
# Datenbank aus einem Dump wiederherstellen (docs/betrieb.md 5.4 Schritt 3), im Backup-Container aufrufen:
#   docker compose exec -T backup restore.sh /backup/db/objektakte_[TS].sql.gz
# Fachverzeichnisse werden auf dem Host zurueckgespielt (Schritt 4), nicht hier.
set -eu
[ -f /etc/backup.env ] && . /etc/backup.env
DUMP="${1:?Pfad zum Dump fehlt}"
ROOTPW_FILE="${DB_ROOT_PASSWORD_FILE:-/run/secrets/db_root_password}"
[ -f "$ROOTPW_FILE" ] || { echo "Root-Passwort nicht verfuegbar; Wiederherstellung ueber den db-Container ausfuehren (docs/betrieb.md 5.4)"; exit 1; }
gzip -t "$DUMP"
echo "Spiele $DUMP in $DB_NAME auf $DB_HOST ein ..."
zcat "$DUMP" | MYSQL_PWD="$(cat "$ROOTPW_FILE")" mariadb -h "$DB_HOST" -uroot "$DB_NAME"
echo "Wiederherstellung abgeschlossen. Danach: app-migrate --check, docker compose up -d, Smoke-Test."
