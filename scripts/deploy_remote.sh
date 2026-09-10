#!/usr/bin/env bash
# Eingeschraenkter SSH-Einstieg fuer GitHub Actions (docs/betrieb/github-deploy.md).
# In ~/.ssh/authorized_keys des Deploy-Nutzers eingetragen als
#   command="/opt/objektakte/scripts/deploy_remote.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-actions-deploy
# Der Schluessel kann damit ausschliesslich "deploy <branch>", "rollback" oder "check" (Verbindungsprobe ohne
# Schreibwirkung) ausloesen; jede andere Eingabe wird abgewiesen.
set -euo pipefail
cd /opt/objektakte
LOG=/srv/objektakte/deploy/remote.log
mkdir -p "$(dirname "$LOG")"
read -r ACTION BRANCH _ <<<"${SSH_ORIGINAL_COMMAND:-}"
case "$BRANCH" in *[!A-Za-z0-9._/-]*|"") case "$ACTION" in rollback|check) ;; *) echo "Branch fehlt oder unzulaessig"; exit 2 ;; esac ;; esac
echo "$(date -Is) $ACTION ${BRANCH:-} von ${SSH_CLIENT:-unbekannt}" >> "$LOG"
case "$ACTION" in
  deploy)   exec scripts/deploy.sh "$BRANCH" ;;
  rollback) exec scripts/rollback.sh ;;
  check)
    echo "Verbindung ok: $(hostname) als $(whoami), Checkout $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short=12 HEAD)"
    [ -f .env ] && echo ".env vorhanden" || echo ".env fehlt noch (vor dem ersten Deployment anlegen)"
    docker compose version 2>/dev/null | head -1 || echo "docker compose nicht verfuegbar"
    ;;
  *) echo "Nur 'deploy <branch>', 'rollback' oder 'check' erlaubt"; exit 2 ;;
esac
