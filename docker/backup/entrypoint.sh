#!/bin/sh
# Schreibt die Crontab aus BACKUP_CRON, legt beim ersten Start eine status.json an und startet cron im Vordergrund.
set -eu
: "${BACKUP_CRON:=30 2 * * *}"
mkdir -p /backup /backup/db /backup/volumes /backup/config
if [ ! -f /backup/status.json ]; then
  printf '{"last_run":null,"status":"started","warnings":"","offsite":"skipped","duration_s":0,"total_bytes":0}\n' > /backup/status.json
fi
# Die Anwendung (APP_UID) liest nur status.json: Verzeichnis betretbar (711), Datei lesbar (644); die
# Sicherungen selbst bleiben unter 700 (Dumps enthalten den gesamten Datenbestand).
chmod 711 /backup
chmod 700 /backup/db /backup/volumes /backup/config
chmod 644 /backup/status.json
# Umgebungsvariablen fuer den Cron-Job sichern (cron startet mit leerer Umgebung).
# Werte kommen in einfache Anfuehrungszeichen, sonst zerfaellt ein Wert mit Leerzeichen beim Einlesen
# (BACKUP_CRON enthaelt regulaer Leerzeichen, zum Beispiel "30 2 * * *"); enthaltene Anfuehrungszeichen
# werden abgesichert. Danach wird die Datei einmal probeweise eingelesen, damit ein Fehler sofort auffaellt
# und nicht erst beim ersten Sicherungslauf.
: > /etc/backup.env
chmod 600 /etc/backup.env
for var in $(env | grep -E '^(TZ|DB_|BACKUP_|OFFSITE_|RCLONE_)' | cut -d= -f1); do
  eval "value=\${$var}"
  escaped=$(printf '%s' "$value" | sed "s/'/'\\\\''/g")
  printf "export %s='%s'\n" "$var" "$escaped" >> /etc/backup.env
done
if ! sh -c '. /etc/backup.env' >/dev/null 2>&1; then
  echo "backup: /etc/backup.env ist nicht einlesbar"; cat /etc/backup.env; exit 1
fi
echo "$BACKUP_CRON . /etc/backup.env && /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1" > /etc/cron.d/objektakte-backup
echo "" >> /etc/cron.d/objektakte-backup
chmod 0644 /etc/cron.d/objektakte-backup
echo "backup: Zeitplan '$BACKUP_CRON', Aufbewahrung ${BACKUP_RETENTION_DAYS:-?} Tage, Offsite ${OFFSITE_ENABLED:-false}"
exec cron -f
