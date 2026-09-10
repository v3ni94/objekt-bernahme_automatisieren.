#!/usr/bin/env bash
# Eingeschraenkter SSH-Einstieg fuer GitHub Actions (docs/betrieb/github-deploy.md).
# In ~/.ssh/authorized_keys des Deploy-Nutzers eingetragen als
#   command="/opt/objektakte/scripts/deploy_remote.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-actions-deploy
# Erlaubte Eingaben (erstes Wort Aktion, zweites Wort Branch, drittes Wort Argument):
#   check <branch>            Verbindungsprobe ohne Schreibwirkung
#   pull <branch>             Checkout aktualisieren, nichts starten
#   befund <branch>           Serverbefund (scripts/measure_server.sh, geschwaerzt) ausgeben
#   env-init <branch>         .env aus deploy/env.produktion anlegen, nie ueberschreiben
#   first-run <branch>        Erstinstallation (scripts/deploy.sh --first-run)
#   deploy <branch>           Deployment (scripts/deploy.sh)
#   rollback                  vorherige Version (scripts/rollback.sh)
#   create-admin <branch> <email>  Admin anlegen; Startpasswort nur in /home/deploy/admin-startpasswort.txt
# Jede andere Eingabe wird abgewiesen.
set -euo pipefail
cd /opt/objektakte
LOG=/srv/objektakte/deploy/remote.log
mkdir -p "$(dirname "$LOG")"
read -r ACTION BRANCH ARG _ <<<"${SSH_ORIGINAL_COMMAND:-}"
envval() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | awk '{print $1}'; }
# Host-Key von GitHub fuer den Deploy-Nutzer, geprueft gegen den von GitHub veroeffentlichten Fingerabdruck.
# Ohne diesen Eintrag scheitert git fetch mit "Host key verification failed", weil der Aufruf kein Terminal hat.
GITHUB_ED25519_FP="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
ensure_github_hostkey() {
  mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/known_hosts && chmod 600 ~/.ssh/known_hosts
  if ssh-keygen -F github.com -f ~/.ssh/known_hosts >/dev/null 2>&1; then return 0; fi
  local scan; scan="$(ssh-keyscan -t ed25519 github.com 2>/dev/null)"
  [ -n "$scan" ] || { echo "Host-Key von github.com nicht abrufbar"; return 1; }
  local fp; fp="$(printf '%s\n' "$scan" | ssh-keygen -lf - | awk '{print $2}')"
  [ "$fp" = "$GITHUB_ED25519_FP" ] || { echo "Host-Key von github.com weicht ab ($fp), nichts eingetragen"; return 1; }
  printf '%s\n' "$scan" >> ~/.ssh/known_hosts
  echo "Host-Key von github.com eingetragen ($fp)"
}
ensure_network() {
  # Traefik laeuft im Host-Netz (Serverbefund); das Proxy-Netz wird mit festem Subnetz angelegt, damit
  # TRUSTED_PROXY_CIDR stimmt. Vorhandenes Netz bleibt unveraendert.
  local net cidr; net="$(envval TRAEFIK_NETWORK)"; cidr="$(envval TRUSTED_PROXY_CIDR)"
  [ -n "$net" ] || { echo "TRAEFIK_NETWORK fehlt in .env"; return 1; }
  if docker network inspect "$net" >/dev/null 2>&1; then
    echo "Netz $net vorhanden: $(docker network inspect "$net" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}')"
  else
    docker network create --driver bridge ${cidr:+--subnet "$cidr"} "$net" >/dev/null
    echo "Netz $net angelegt${cidr:+ mit Subnetz $cidr}"
  fi
}
case "$BRANCH" in *[!A-Za-z0-9._/-]*|*..*|"") case "$ACTION" in rollback|check) ;; *) echo "Branch fehlt oder unzulaessig"; exit 2 ;; esac ;; esac
case "${ARG:-}" in *[!A-Za-z0-9@._+-]*) echo "Argument unzulaessig"; exit 2 ;; esac
echo "$(date -Is) $ACTION ${BRANCH:-} ${ARG:-} von ${SSH_CLIENT:-unbekannt}" >> "$LOG"
case "$ACTION" in
  deploy)    ensure_github_hostkey; ensure_network; exec scripts/deploy.sh "$BRANCH" ;;
  first-run) ensure_github_hostkey; ensure_network; exec scripts/deploy.sh --first-run "$BRANCH" ;;
  rollback)  exec scripts/rollback.sh ;;
  check)
    echo "Verbindung ok: $(hostname) als $(whoami), Checkout $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short=12 HEAD)"
    [ -f .env ] && echo ".env vorhanden" || echo ".env fehlt noch (Aktion env-init)"
    docker compose version 2>/dev/null | head -1 || echo "docker compose nicht verfuegbar"
    ;;
  pull)
    ensure_github_hostkey
    git fetch --tags origin
    git checkout "$BRANCH" && git pull --ff-only origin "$BRANCH"
    echo "Checkout jetzt: $(git log -1 --format='%h %s')"
    ;;
  befund)
    OUT="$(mktemp)"
    bash scripts/measure_server.sh "$OUT" >/dev/null 2>&1 || echo "Hinweis: Serverbefund mit Warnungen (Ausgabe folgt)"
    cat "$OUT"; rm -f "$OUT"
    ;;
  env-init)
    if [ -f .env ]; then
      echo ".env ist vorhanden und wird nicht ueberschrieben. Abweichungen zur Vorlage:"
      diff -u deploy/env.produktion .env || true
    else
      [ -f deploy/env.produktion ] || { echo "deploy/env.produktion fehlt im Checkout"; exit 2; }
      cp deploy/env.produktion .env && chmod 600 .env
      echo ".env aus deploy/env.produktion angelegt ($(grep -c '' .env) Zeilen)"
    fi
    ensure_network
    docker compose config --quiet && echo "Compose-Datei mit dieser .env gueltig"
    ;;
  create-admin)
    [ -n "${ARG:-}" ] || { echo "E-Mail fehlt"; exit 2; }
    PW="$(openssl rand -base64 18)"
    ADMIN_PASSWORD="$PW" docker compose exec -T -e ADMIN_PASSWORD web app-create-admin --email "$ARG" --no-input
    umask 077; printf 'Startpasswort fuer %s: %s\n' "$ARG" "$PW" > /home/deploy/admin-startpasswort.txt
    echo "Startpasswort liegt auf dem Server in /home/deploy/admin-startpasswort.txt (nach dem ersten Login loeschen: shred -u)."
    ;;
  *) echo "Nur check, pull, befund, env-init, first-run, deploy, rollback oder create-admin erlaubt"; exit 2 ;;
esac
