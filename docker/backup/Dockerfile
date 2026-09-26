# Backup-Container (docs/betrieb.md 5.2): Dump, Archive, Aufbewahrung, optional verschluesselte Offsite-Kopie.
# Paketnamen zum Umsetzungszeitpunkt pruefen (ANNAHME AB19).
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
# procps liefert pgrep und ps: der Healthcheck prueft damit, ob cron laeuft; ohne das Paket schlaegt er
# dauerhaft fehl und der Container bleibt nach jedem Deployment 'starting' (beobachtet am 11.09.2026).
RUN apt-get update && apt-get install -y --no-install-recommends \
      mariadb-client tar gzip jq age rclone cron procps ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*
# Werkzeuge des Healthchecks und der Sicherung zur Bauzeit pruefen
RUN pgrep --version >/dev/null && mariadb-dump --version >/dev/null && age --version >/dev/null \
 && jq --version >/dev/null && rclone --version >/dev/null
# restore_probe.sh (26.09.2026): Wiederherstellungsprobe T6 in eine Scratch-Datenbank, docs/betrieb.md 5.4.1
COPY docker/backup/backup.sh docker/backup/restore.sh docker/backup/restore_probe.sh docker/backup/entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh /usr/local/bin/restore_probe.sh /usr/local/bin/entrypoint.sh
VOLUME ["/backup"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
