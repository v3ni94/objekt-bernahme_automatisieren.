#!/bin/sh
# Wiederherstellungsprobe ohne Eingriff in den Betrieb (Test T6, docs/betrieb.md 5.4.1, Fachentwurf G 7.4), 26.09.2026.
# Spielt die juengste Sicherung (oder den uebergebenen Dump) in die Scratch-Datenbank ${DB_NAME}_restore_probe ein,
# vergleicht die Zeilenzahlen der Kerntabellen mit der Produktion, prueft den Token-Status und loescht die
# Scratch-Datenbank am Ende wieder. Die konfigurierte Datenbank (DB_NAME) wird nie beschrieben: ist der Zielname gleich
# DB_NAME oder leer, bricht das Skript hart ab. Laeuft als DB_BACKUP_USER (app_backup), der dafuer alle Rechte auf die
# Scratch-Datenbank erhaelt (grants_sql und docker/db/init/01_accounts.sh); das Root-Passwort wird nicht gebraucht.
# DEFINER-Klauseln des Dumps (Trigger, Ereignisse) werden entfernt, sonst verlangt das Einspielen SUPER.
# Aufruf im Backup-Container:
#   docker compose exec -T backup restore_probe.sh [/backup/db/objektakte_TS.sql.gz]
# Ausgabe: Protokoll nach G 7.4 (Zeitstempel, Sicherungsdatei mit Pruefsumme, Zeilenzahlen, Abweichungen). Exit 0, wenn
# das Einspielen gelang und keine Kerntabelle in der Sicherung fehlt, sonst 1. Abweichende Zeilenzahlen sind kein
# Fehler des Einspielens (die Produktion laeuft seit dem Dump weiter); sie werden ausgewiesen und im Protokoll bewertet.
set -eu
[ -f /etc/backup.env ] && . /etc/backup.env
: "${DB_NAME:?DB_NAME fehlt}"
: "${DB_HOST:?DB_HOST fehlt}"
: "${DB_BACKUP_USER:?DB_BACKUP_USER fehlt}"
: "${DB_BACKUP_PASSWORD_FILE:?DB_BACKUP_PASSWORD_FILE fehlt}"

TARGET="${DB_NAME}_restore_probe"
# BACKUP_DIR nur fuer Tests ausserhalb des Containers (Standard /backup wie backup.sh)
BACKUP_DIR="${BACKUP_DIR:-/backup}"
if [ -z "$TARGET" ] || [ "$TARGET" = "$DB_NAME" ]; then
  echo "ABBRUCH: Zieldatenbank '$TARGET' waere die Produktionsdatenbank '$DB_NAME'"; exit 1
fi
case "$TARGET" in *[!A-Za-z0-9_]*) echo "ABBRUCH: unzulaessiger Datenbankname '$TARGET'"; exit 1 ;; esac

# Kerntabellen fuer den Vergleich (G 7.4 plus Verarbeitung, Verknuepfungen, Protokoll, Token, Migrationsstand)
TABLES="objects units owners tenants documents document_pages review_cases processing_runs processing_jobs sync_links audit_events oauth_tokens users django_migrations"

DUMP="${1:-}"
if [ -z "$DUMP" ]; then
  # Juengste Sicherung nach Zeitstempel im Namen (${DB_NAME}_JJJJMMTT_HHMMSS.sql.gz), nicht nach mtime
  DUMP="$(ls -1 "$BACKUP_DIR"/db/"${DB_NAME}"_*.sql.gz 2>/dev/null | sort | tail -n 1 || true)"
fi
[ -n "$DUMP" ] || { echo "ABBRUCH: keine Sicherung unter $BACKUP_DIR/db gefunden"; exit 1; }
[ -f "$DUMP" ] || { echo "ABBRUCH: Sicherung $DUMP nicht gefunden"; exit 1; }

MYSQL_PWD="$(cat "$DB_BACKUP_PASSWORD_FILE")"; export MYSQL_PWD
# q <sql> [datenbank]: eine Abfrage ohne Spaltenkopf im Stapelmodus
q() { mariadb -h "$DB_HOST" -u "$DB_BACKUP_USER" -N -B -e "$1" ${2:+"$2"}; }

START=$(date +%s)
RESTORED=0
cleanup() {
  # Scratch-Datenbank in jedem Fall entfernen, auch nach Abbruch; Fehler beim Loeschen brechen nichts mehr ab
  if q "DROP DATABASE IF EXISTS \`$TARGET\`" >/dev/null 2>&1; then
    echo "Scratch-Datenbank $TARGET geloescht"
  else
    echo "WARNUNG: Scratch-Datenbank $TARGET konnte nicht geloescht werden; von Hand entfernen"
  fi
}
trap cleanup EXIT
# /bin/sh im Backup-Image ist dash: der EXIT-Trap laeuft dort nach einem Signal (TERM bei docker compose stop oder
# deploy, HUP nach SSH-Abbruch, INT) nicht von selbst. Die Signale enden deshalb ausdruecklich mit exit, damit
# cleanup greift; nach KILL bleibt die Scratch-Datenbank stehen und wird beim naechsten deploy (Schritt 5b) oder
# beim naechsten Lauf ueber DROP IF EXISTS entfernt (docs/betrieb.md 5.4.1).
trap 'exit 1' HUP INT TERM

echo "# Wiederherstellungsprobe (T6, Protokoll nach G 7.4)"
echo
echo "| Feld | Inhalt |"
echo "|---|---|"
echo "| Datum, Beginn | $(date '+%d.%m.%Y %H:%M:%S %Z') |"
echo "| Durchfuehrender | Deploy-Aktion restore-probe (Person und Lauf-ID von Hand nachtragen) |"
TS="$(basename "$DUMP" .sql.gz | sed "s/^${DB_NAME}_//")"
SUM="$(grep -h "db/$(basename "$DUMP")" "$BACKUP_DIR/checksums_${TS}.txt" 2>/dev/null | awk '{print $1}' || true)"
[ -n "$SUM" ] && QUELLE="checksums_${TS}.txt" || { SUM="$(sha256sum "$DUMP" | awk '{print $1}')"; QUELLE="jetzt berechnet"; }
echo "| Verwendete Sicherung | $(basename "$DUMP"), $(du -h "$DUMP" | cut -f1), sha256 $SUM ($QUELLE) |"
echo "| Zielumgebung | Scratch-Datenbank $TARGET auf $DB_HOST (derselbe Datenbankserver, Produktion $DB_NAME bleibt unberuehrt) |"
echo "| Quelle | lokal ($BACKUP_DIR/db) |"

gzip -t "$DUMP" || { echo "| Ergebnis | ABBRUCH: Sicherung nicht lesbar (gzip -t) |"; exit 1; }

q "DROP DATABASE IF EXISTS \`$TARGET\`; CREATE DATABASE \`$TARGET\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" \
  || { echo "| Ergebnis | ABBRUCH: Scratch-Datenbank nicht anlegbar (Rechte fuer $DB_BACKUP_USER auf $TARGET pruefen) |"; exit 1; }
T0=$(date +%s)
# DEFINER entfernen (Trigger und Ereignisse tragen den Definer des Anlegers; ein anderer Definer braucht SUPER)
if zcat "$DUMP" | sed -E 's/DEFINER=`[^`]*`@`[^`]*`//g' | mariadb -h "$DB_HOST" -u "$DB_BACKUP_USER" "$TARGET"; then
  RESTORED=1
else
  echo "| Ergebnis | ABBRUCH: Einspielen in $TARGET fehlgeschlagen |"; exit 1
fi
echo "| Dauer Einspielen | $(( $(date +%s) - T0 )) s |"

exists() { q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '$1' AND table_name = '$2'"; }
echo
echo "## Vergleich der Zeilenzahlen (Produktion jetzt gegen Sicherung vom ${TS})"
echo
echo "| Tabelle | Produktion | Sicherung | Befund |"
echo "|---|---|---|---|"
ABW=0; FEHLT=0; GLEICH=0
for t in $TABLES; do
  if [ "$(exists "$DB_NAME" "$t")" != "1" ]; then
    echo "| $t | nicht vorhanden | | uebersprungen |"; continue
  fi
  P="$(q "SELECT COUNT(*) FROM \`$t\`" "$DB_NAME")"
  if [ "$(exists "$TARGET" "$t")" != "1" ]; then
    echo "| $t | $P | fehlt | FEHLT in der Sicherung |"; FEHLT=$((FEHLT + 1)); continue
  fi
  S="$(q "SELECT COUNT(*) FROM \`$t\`" "$TARGET")"
  if [ "$P" = "$S" ]; then
    echo "| $t | $P | $S | gleich |"; GLEICH=$((GLEICH + 1))
  else
    echo "| $t | $P | $S | abweichend ($((P - S)) seit dem Dump) |"; ABW=$((ABW + 1))
  fi
done

echo
echo "## Token-Status (oauth_tokens, nur Status und Anbieter, keine Chiffrate)"
echo
if [ "$(exists "$TARGET" oauth_tokens)" = "1" ]; then
  TOK="$(q "SELECT CONCAT(provider, ' ', status, ' x', COUNT(*)) FROM oauth_tokens GROUP BY provider, status ORDER BY provider, status" "$TARGET" | paste -sd ';' -)"
  echo "- Sicherung: ${TOK:-kein Token}"
  TOKP="$(q "SELECT CONCAT(provider, ' ', status, ' x', COUNT(*)) FROM oauth_tokens GROUP BY provider, status ORDER BY provider, status" "$DB_NAME" | paste -sd ';' -)"
  echo "- Produktion: ${TOKP:-kein Token}"
  echo "- Hinweis: Status active in der Sicherung heisst nutzbar nur bei identischem TOKEN_KEY (docs/betrieb.md 5.5)"
else
  echo "- Tabelle oauth_tokens fehlt in der Sicherung"
fi

echo
echo "## Abweichungen und Massnahmen"
echo
echo "- Tabellen gleich: $GLEICH, abweichend: $ABW, in der Sicherung fehlend: $FEHLT"
if [ "$FEHLT" -gt 0 ]; then
  echo "- FEHLER: Kerntabellen fehlen in der Sicherung; Sicherungslauf und Rechte von $DB_BACKUP_USER pruefen"
fi
if [ "$ABW" -gt 0 ]; then
  echo "- Abweichungen erklaeren sich durch Verarbeitung seit dem Dump ($TS); fuer den Nachweis identischer Zeilenzahlen die Probe unmittelbar nach einer Sicherung ohne laufende Verarbeitung wiederholen"
fi
echo "- Stichproben nach G 7.4 (Dokument oeffnen, Eigentuemerakte, Review-Fall) sind in der Anwendung von Hand zu ergaenzen"
echo "- Dauer gesamt: $(( $(date +%s) - START )) s, Ende $(date '+%d.%m.%Y %H:%M:%S %Z')"
[ "$RESTORED" -eq 1 ] && [ "$FEHLT" -eq 0 ]
