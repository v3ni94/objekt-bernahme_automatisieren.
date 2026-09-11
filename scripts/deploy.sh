#!/usr/bin/env bash
# Deployment (docs/betrieb.md 4.2): scripts/deploy.sh [branch] | scripts/deploy.sh --first-run [branch]
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR=/srv/objektakte/deploy
FIRST_RUN=false
BRANCH=main
for arg in "$@"; do
  case "$arg" in
    --first-run) FIRST_RUN=true ;;
    *) BRANCH="$arg" ;;
  esac
done
envval() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | awk '{print $1}'; }
APP_DOMAIN=$(envval APP_DOMAIN)
[ -n "$APP_DOMAIN" ] || { echo "APP_DOMAIN fehlt in .env"; exit 1; }
mkdir -p "$DEPLOY_DIR"

wait_healthy() {   # wait_healthy <sekunden> [dienst ...]
  local end=$((SECONDS + $1)); shift
  local offen last=""
  while [ "$SECONDS" -lt "$end" ]; do
    offen="$(docker compose ps --format '{{.Name}} {{.Health}}' "$@" | grep -vE ' healthy$' || true)"
    [ -z "$offen" ] && return 0
    if [ "$offen" != "$last" ]; then echo "warte auf: $(echo "$offen" | awk '{print $1}' | tr '\n' ' ')"; last="$offen"; fi
    sleep 5
  done
  docker compose ps "$@"; echo "Healthchecks nicht gruen"; return 1
}

# 1. Code holen
git fetch --tags origin
git checkout "$BRANCH" && git pull --ff-only origin "$BRANCH"
NEW_TAG=$(git rev-parse --short=12 HEAD)
CUR_TAG=$(cat "$DEPLOY_DIR/current" 2>/dev/null || echo none)
echo "Deployment $CUR_TAG nach $NEW_TAG (first-run: $FIRST_RUN)"

# 2. Compose-Datei validieren, dann bauen (Tag = Git-SHA, beide Ziele plus Backup-Image)
export IMAGE_TAG="$NEW_TAG"
docker compose config --quiet
docker compose build --pull

# 3. Erstinstallation: Infrastruktur zuerst
if $FIRST_RUN; then
  docker compose up -d db redis backup
  wait_healthy 180 db redis
fi

# 4. Dump vor Migration (Pflicht), bei leerer Datenbank uebersprungen
TABLES=$(docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = \"$MARIADB_DATABASE\""' | tr -d '[:space:]')
if [ "${TABLES:-0}" -gt 0 ]; then
  docker compose exec -T backup /usr/local/bin/backup.sh || { echo "Dump fehlgeschlagen, Abbruch"; exit 1; }
else
  echo "Leere Datenbank, kein Dump vor der ersten Migration"
fi

# 5. Migration mit dem neuen Image; alte Container laufen weiter (rueckwaertskompatibel)
docker compose run --rm --no-deps web app-migrate
if $FIRST_RUN; then docker compose run --rm --no-deps web app-seed; fi

# 5b. Tabellenrechte nachziehen (B-43): SQL aus dem Modellregister, als root ausgefuehrt
docker compose run --rm --no-deps -T web app-grants-sql \
  | docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" "$MARIADB_DATABASE"'

# 6. Container wechseln
docker compose up -d --remove-orphans

# 7. Auf Healthchecks warten. Der Sicherungsdienst prueft regulaer nur alle fuenf Minuten; waehrend der
# Startphase greift start_interval, die Grenze liegt trotzdem hoeher als die urspruenglichen 3 min (AB27).
wait_healthy 300

# 8. Smoke-Test ueber Traefik (TLS, Anwendung)
# Traefik fordert das Zertifikat erst an, wenn die Domain zum ersten Mal angefragt wird, und liefert bis
# dahin sein Platzhalterzertifikat. curl wiederholt bei einem Zertifikatsfehler nicht von selbst, deshalb
# hier eine eigene Schleife (bis zu drei Minuten). Getrennt ausgewiesen: Antwort der Anwendung und Zertifikat.
echo "Smoke-Test gegen https://${APP_DOMAIN}/healthz/"
SMOKE=false
for versuch in $(seq 1 18); do
  if curl -fsS --max-time 10 -o /dev/null "https://${APP_DOMAIN}/healthz/"; then SMOKE=true; break; fi
  CODE=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "https://${APP_DOMAIN}/healthz/" || echo 000)
  echo "  Versuch ${versuch}: Anwendung antwortet mit ${CODE}, Zertifikat noch nicht gueltig"
  sleep 10
done
if ! $SMOKE; then
  echo "Smoke-Test fehlgeschlagen. Ausgeliefertes Zertifikat:"
  echo | openssl s_client -connect "${APP_DOMAIN}:443" -servername "${APP_DOMAIN}" 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates 2>/dev/null || echo "  kein Zertifikat lesbar"
  echo "Traefik-Meldungen zur Zertifikatsausstellung:"
  docker logs "$(docker ps --filter name=traefik --format '{{.Names}}' | head -1)" --tail 30 2>&1 \
    | grep -iE 'acme|certificate|objektakte' | tail -15 || echo "  keine Meldungen gefunden"
  exit 1
fi
curl -fsS -o /dev/null -w 'healthz %{http_code} tls %{ssl_verify_result}\n' "https://${APP_DOMAIN}/healthz/"
curl -fsS "https://${APP_DOMAIN}/readyz/" \
  -H "Authorization: Bearer $(docker compose exec -T web cat /run/secrets/readyz_token 2>/dev/null || echo none)" | head -c 400; echo

# 9. Tags fortschreiben
[ "$CUR_TAG" != none ] && echo "$CUR_TAG" > "$DEPLOY_DIR/previous"
echo "$NEW_TAG" > "$DEPLOY_DIR/current"
for img in web worker backup; do
  docker tag "objektakte/$img:$NEW_TAG" "objektakte/$img:current"
  [ "$CUR_TAG" != none ] && docker tag "objektakte/$img:$CUR_TAG" "objektakte/$img:previous"
done
printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "$NEW_TAG" "$CUR_TAG" "$(whoami)" "$($FIRST_RUN && echo FIRST-RUN || echo DEPLOY)" >> "$DEPLOY_DIR/tags.log"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$NEW_TAG/" .env

# 10. Alte Images aufraeumen, die letzten N behalten (current, previous, dev bleiben)
KEEP=${IMAGE_KEEP:-5}
for img in web worker backup; do
  docker images "objektakte/$img" --format '{{.Tag}} {{.CreatedAt}}' \
    | grep -vE '^(current|previous|dev) ' | sort -k2 -r | tail -n +$((KEEP + 1)) | awk '{print $1}' \
    | xargs -r -I{} docker rmi "objektakte/$img:{}" || true
done
echo "Deployment $NEW_TAG abgeschlossen"
