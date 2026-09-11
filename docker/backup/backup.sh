#!/bin/sh
# Taegliche Sicherung (docs/betrieb.md 5.2): atomares Schreiben, Integritaetspruefung, Aufraeumen nur nach Erfolg.
set -eu
[ -f /etc/backup.env ] && . /etc/backup.env
TS=$(date +%Y%m%d_%H%M%S); START=$(date +%s)
DBPW=$(cat "$DB_BACKUP_PASSWORD_FILE")
mkdir -p /backup/db /backup/volumes /backup/config
STATUS=ok; WARN=""
DUMP="/backup/db/${DB_NAME}_${TS}.sql.gz"

# 1. Datenbank
if MYSQL_PWD="$DBPW" mariadb-dump -h "$DB_HOST" -u "$DB_BACKUP_USER" \
     --single-transaction --routines --triggers --events --hex-blob "$DB_NAME" \
     | gzip -6 > "${DUMP}.part" && gzip -t "${DUMP}.part"; then
  mv "${DUMP}.part" "$DUMP"
else
  STATUS=failed; rm -f "${DUMP}.part"
fi

# 2. Fachverzeichnisse (Beschluss B-14)
for d in transit ocr-cache models lists requests imports exports; do
  [ -d "/src/$d" ] || { WARN="$WARN $d:fehlt"; continue; }
  ARCH="/backup/volumes/${d}_${TS}.tar.gz"
  set +e
  tar --create --gzip --file "${ARCH}.part" --warning=no-file-changed -C /src "$d"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then mv "${ARCH}.part" "$ARCH"
  elif [ "$rc" -eq 1 ]; then WARN="$WARN $d:changed"; mv "${ARCH}.part" "$ARCH"
  else STATUS=failed; rm -f "${ARCH}.part"; fi
done

# 3. Konfiguration ohne Geheimnisse
[ -f /src/config/.env ] && cp /src/config/.env "/backup/config/env_${TS}"
[ -f /src/config/docker-compose.yml ] && cp /src/config/docker-compose.yml "/backup/config/docker-compose_${TS}.yml"
[ -d /src/deploy ] && cp -r /src/deploy "/backup/config/deploy_${TS}"

# 4. Pruefsummen
( cd /backup && sha256sum db/*_${TS}.sql.gz volumes/*_${TS}.tar.gz 2>/dev/null ) > "/backup/checksums_${TS}.txt" || true

# 5. Offsite-Kopie, nur verschluesselt und nur nach Erfolg
OFFSITE=skipped
if [ "${OFFSITE_ENABLED:-false}" = "true" ] && [ "$STATUS" = ok ]; then
  OFFSITE=ok
  for f in "$DUMP" /backup/volumes/*_${TS}.tar.gz "/backup/checksums_${TS}.txt"; do
    [ -f "$f" ] || continue
    if age -r "$(cat "$BACKUP_ENCRYPT_RECIPIENT_FILE")" -o "${f}.age" "$f" \
       && rclone copy "${f}.age" "$OFFSITE_REMOTE/$(date +%Y/%m)/"; then rm -f "${f}.age"; else OFFSITE=failed; fi
  done
fi

# 6. Aufraeumen nur nach Erfolg
if [ "$STATUS" = ok ] && [ -n "${BACKUP_RETENTION_DAYS:-}" ]; then
  find /backup/db /backup/volumes /backup/config -mindepth 1 -mtime "+${BACKUP_RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true
  find /backup -maxdepth 1 -name 'checksums_*' -mtime "+${BACKUP_RETENTION_DAYS}" -delete 2>/dev/null || true
fi

# 7. Status
jq -n --arg ts "$TS" --arg st "$STATUS" --arg warn "$WARN" --arg off "$OFFSITE" \
      --argjson dur $(( $(date +%s) - START )) \
      --argjson size "$(du -sb /backup | cut -f1)" \
      '{last_run:$ts,status:$st,warnings:$warn,offsite:$off,duration_s:$dur,total_bytes:$size}' > /backup/status.json.tmp
mv /backup/status.json.tmp /backup/status.json
chmod 644 /backup/status.json
echo "backup ${TS}: ${STATUS}${WARN:+ (Warnungen:$WARN)} offsite=${OFFSITE}"
[ "$STATUS" = ok ]
