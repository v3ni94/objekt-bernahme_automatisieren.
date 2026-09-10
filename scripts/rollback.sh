#!/usr/bin/env bash
# Rollback auf das vorherige Image mit einem Befehl (docs/betrieb.md 4.3, CR 0.1)
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR=/srv/objektakte/deploy
PREV=$(cat "$DEPLOY_DIR/previous")
CUR=$(cat "$DEPLOY_DIR/current")
echo "Rollback $CUR nach $PREV"
for img in web worker backup; do docker image inspect "objektakte/$img:$PREV" >/dev/null; done   # Images muessen lokal vorliegen
IMAGE_TAG="$PREV" docker compose up -d --no-build
echo "$CUR" > "$DEPLOY_DIR/previous"; echo "$PREV" > "$DEPLOY_DIR/current"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$PREV/" .env
printf '%s\t%s\t%s\t%s\tROLLBACK\n' "$(date -Is)" "$PREV" "$CUR" "$(whoami)" >> "$DEPLOY_DIR/tags.log"
docker compose ps
echo "Hinweis: Das Schema bleibt auf dem Stand des zurueckgenommenen Deployments (rueckwaertskompatibel). Schema-Rollback nur nach docs/betrieb.md Abschnitt 4.4."
