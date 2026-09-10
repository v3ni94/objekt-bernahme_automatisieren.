#!/bin/sh
# Schreibt die Crontab aus BACKUP_CRON, legt beim ersten Start eine status.json an und startet cron im Vordergrund.
set -eu
: "${BACKUP_CRON:=30 2 * * *}"
mkdir -p /backup
if [ ! -f /backup/status.json ]; then
  printf '{"last_run":null,"status":"started","warnings":"","offsite":"skipped","duration_s":0,"total_bytes":0}\n' > /backup/status.json
fi
# Umgebungsvariablen fuer den Cron-Job sichern (cron startet mit leerer Umgebung)
env | grep -E '^(TZ|DB_|BACKUP_|OFFSITE_|RCLONE_)' | sed 's/^/export /' > /etc/backup.env
echo "$BACKUP_CRON . /etc/backup.env && /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1" > /etc/cron.d/objektakte-backup
echo "" >> /etc/cron.d/objektakte-backup
chmod 0644 /etc/cron.d/objektakte-backup
echo "backup: Zeitplan '$BACKUP_CRON', Aufbewahrung ${BACKUP_RETENTION_DAYS:-?} Tage, Offsite ${OFFSITE_ENABLED:-false}"
exec cron -f
