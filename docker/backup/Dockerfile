# Backup-Container (docs/betrieb.md 5.2): Dump, Archive, Aufbewahrung, optional verschluesselte Offsite-Kopie.
# Paketnamen zum Umsetzungszeitpunkt pruefen (ANNAHME AB19).
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      mariadb-client tar gzip jq age rclone cron ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY docker/backup/backup.sh docker/backup/restore.sh docker/backup/entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh /usr/local/bin/entrypoint.sh
VOLUME ["/backup"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
