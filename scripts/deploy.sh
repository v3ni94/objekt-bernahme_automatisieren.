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
# Optionale Secrets (Paperless-ngx, 12.09.2026): leere Platzhalterdatei, falls nicht vorhanden, damit Compose die
# Secrets einbinden kann; den Inhalt setzt der Admin (docs/betrieb/paperless-sync.md). Leer bedeutet: Anbindung aus.
for s in paperless_token paperless_webhook_token; do
  if [ ! -f "/srv/objektakte/secrets/$s" ]; then
    (umask 077; : > "/srv/objektakte/secrets/$s") && echo "Secret-Platzhalter angelegt: $s (leer)" \
      || echo "Hinweis: /srv/objektakte/secrets/$s fehlt und konnte nicht angelegt werden (Rechte pruefen)"
  fi
done

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
# Seeds bei jedem Deployment: idempotent, legt neue Katalogschluessel, Regeln und Textbausteine an und
# ueberschreibt keine im Admin geaenderten Werte (Test "zweiter Lauf ohne Aenderung").
docker compose run --rm --no-deps web app-seed

# 5b. Tabellenrechte nachziehen (B-43): SQL aus dem Modellregister, als root ausgefuehrt
docker compose run --rm --no-deps -T web app-grants-sql \
  | docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" "$MARIADB_DATABASE"'

# 6. Container wechseln
docker compose up -d --remove-orphans

# 7. Auf Healthchecks warten. Der Sicherungsdienst prueft regulaer nur alle fuenf Minuten; waehrend der
# Startphase greift start_interval, die Grenze liegt trotzdem hoeher als die urspruenglichen 3 min (AB27).
wait_healthy 300

# 8. Laufenden Stand festschreiben, bevor geprueft wird: die Container laufen bereits auf der neuen
# Version, deshalb muss der Rollback-Stand auch dann stimmen, wenn nur der Smoke-Test scheitert.
[ "$CUR_TAG" != none ] && echo "$CUR_TAG" > "$DEPLOY_DIR/previous"
echo "$NEW_TAG" > "$DEPLOY_DIR/current"
printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "$NEW_TAG" "$CUR_TAG" "$(whoami)" "$($FIRST_RUN && echo FIRST-RUN || echo DEPLOY)" >> "$DEPLOY_DIR/tags.log"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$NEW_TAG/" .env
for img in web worker backup; do
  docker tag "objektakte/$img:$NEW_TAG" "objektakte/$img:current"
  # Das vorherige Image kann fehlen (Befund Deploy-Lauf 81: Backup-Images teilen sich den Erstellzeitpunkt, die
  # Aufraeumung hatte den Tag entfernt). Dann bleibt der bisherige previous-Tag stehen, der Deploy bricht nicht ab.
  if [ "$CUR_TAG" != none ]; then
    docker tag "objektakte/$img:$CUR_TAG" "objektakte/$img:previous" \
      || echo "Hinweis: objektakte/$img:$CUR_TAG nicht mehr vorhanden, previous-Tag unveraendert"
  fi
done

# 9. Innerer Zustand der Anwendung (unabhaengig von Traefik und Zertifikat)
echo "Bereitschaft der Anwendung im Container:"
docker compose exec -T web python -c "
import json, urllib.request
tok = open('/run/secrets/readyz_token').read().strip()
req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'Authorization': 'Bearer ' + tok})
with urllib.request.urlopen(req, timeout=10) as r:
    print(' ', r.status, json.dumps(json.loads(r.read().decode()), ensure_ascii=False)[:400])
" || echo "  Bereitschaftspruefung fehlgeschlagen"

# 10. Smoke-Test ueber Traefik (TLS, Anwendung)
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

# 11. Alte Images aufraeumen: die letzten N Deployments aus tags.log behalten, dazu die Staende aus current und
# previous sowie die Aliasse. Nicht nach Erstellzeitpunkt sortieren: unveraenderte Images (Backup) tragen fuer
# mehrere Tags denselben Zeitpunkt, die Reihenfolge waere zufaellig und traf den aktuellen Tag (Deploy-Lauf 81).
KEEP=${IMAGE_KEEP:-5}
KEEP_TAGS="$( { awk -F'\t' '{print $2}' "$DEPLOY_DIR/tags.log" 2>/dev/null | tail -n "$KEEP"; cat "$DEPLOY_DIR/current" "$DEPLOY_DIR/previous" 2>/dev/null; echo current; echo previous; echo dev; } | grep -v '^$' | sort -u | paste -sd'|' -)"
for img in web worker backup; do
  docker images "objektakte/$img" --format '{{.Tag}}' \
    | grep -vE "^(${KEEP_TAGS})$" \
    | xargs -r -I{} docker rmi "objektakte/$img:{}" || true
done
echo "Deployment $NEW_TAG abgeschlossen"
