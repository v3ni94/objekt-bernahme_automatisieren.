#!/usr/bin/env bash
# Eingeschraenkter SSH-Einstieg fuer GitHub Actions (docs/betrieb/github-deploy.md).
# In ~/.ssh/authorized_keys des Deploy-Nutzers eingetragen als
#   command="/opt/objektakte/scripts/deploy_remote.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-actions-deploy
# Der Schluessel kann damit ausschliesslich "deploy <branch>" oder "rollback" ausloesen; jede andere Eingabe wird abgewiesen.
set -euo pipefail
cd /opt/objektakte
LOG=/srv/objektakte/deploy/remote.log
mkdir -p "$(dirname "$LOG")"
read -r ACTION BRANCH _ <<<"${SSH_ORIGINAL_COMMAND:-}"
case "$BRANCH" in *[!A-Za-z0-9._/-]*|"") [ "$ACTION" = "rollback" ] || { echo "Branch fehlt oder unzulaessig"; exit 2; } ;; esac
echo "$(date -Is) $ACTION ${BRANCH:-} von ${SSH_CLIENT:-unbekannt}" >> "$LOG"
case "$ACTION" in
  deploy)   exec scripts/deploy.sh "$BRANCH" ;;
  rollback) exec scripts/rollback.sh ;;
  *) echo "Nur 'deploy <branch>' oder 'rollback' erlaubt"; exit 2 ;;
esac
