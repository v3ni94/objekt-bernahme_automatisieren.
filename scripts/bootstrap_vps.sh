#!/usr/bin/env bash
# Einmalige Vorbereitung des VPS fuer die Objektakte (docs/betrieb.md 2.1, 3.3, 3.8; docs/betrieb/github-deploy.md).
#
# Aufruf auf dem VPS mit sudo-Rechten (als root oder als Nutzer mit sudo), nach dem Checkout nach /opt/objektakte:
#   sudo bash /opt/objektakte/scripts/bootstrap_vps.sh
#
# Was das Skript tut (idempotent, nichts wird ueberschrieben):
#   1. Deploy-Nutzer "deploy" mit Gruppen sudo und docker; vorhandene SSH-Schluessel des aufrufenden Nutzers
#      werden fuer deploy uebernommen, damit der Login mit demselben Schluessel weiter funktioniert
#   2. Verzeichnisse unter /srv/objektakte mit Rechten fuer APP_UID (Standard 10001)
#   3. Zufaellige Secrets unter /srv/objektakte/secrets (fehlende Dateien werden erzeugt, vorhandene bleiben)
#   4. Deploy Key (VPS liest von GitHub) und Aktionsschluessel (GitHub Actions ruft nur scripts/deploy_remote.sh)
#   5. Ausgabe der drei Werte fuer GitHub in /home/deploy/github-werte.txt (nur fuer deploy lesbar)
#
# Was das Skript bewusst nicht tut: SSH-Haertung (Passwort-Login aus, Root-Login aus), Firewall, Zeitzone.
# Diese Schritte stehen in docs/betrieb.md 2.1 und werden erst ausgefuehrt, wenn der Schluessel-Login als deploy
# in einer zweiten Sitzung geprueft ist.
set -euo pipefail

APP_UID="${APP_UID:-10001}"
DEPLOY_USER="deploy"
REPO_SSH="git@github.com:v3ni94/objekt-bernahme_automatisieren..git"
HOST_IP="${HOST_IP:-187.124.23.80}"
BASE=/srv/objektakte

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo ausfuehren: sudo bash $0"; exit 1
fi
CALLER="${SUDO_USER:-root}"

echo "== 1. Deploy-Nutzer"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "Objektakte Deploy" "$DEPLOY_USER"
  echo "   Nutzer $DEPLOY_USER angelegt (ohne Passwort; Login nur mit Schluessel)."
fi
getent group docker >/dev/null || groupadd docker
usermod -aG sudo,docker "$DEPLOY_USER"
DH="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DH/.ssh"
CALLER_HOME="$(getent passwd "$CALLER" | cut -d: -f6)"
if [ -s "$CALLER_HOME/.ssh/authorized_keys" ]; then
  touch "$DH/.ssh/authorized_keys"
  while IFS= read -r line; do
    [ -n "$line" ] && ! grep -qxF "$line" "$DH/.ssh/authorized_keys" && echo "$line" >> "$DH/.ssh/authorized_keys"
  done < "$CALLER_HOME/.ssh/authorized_keys"
  echo "   SSH-Schluessel von $CALLER fuer $DEPLOY_USER uebernommen."
else
  echo "   Hinweis: $CALLER hat keine authorized_keys; vor der SSH-Haertung einen Schluessel fuer $DEPLOY_USER hinterlegen (ssh-copy-id)."
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "$DH/.ssh/authorized_keys" 2>/dev/null || true
chmod 600 "$DH/.ssh/authorized_keys" 2>/dev/null || true
id "$DEPLOY_USER"

echo "== 2. Verzeichnisse unter $BASE"
mkdir -p "$BASE"/{db,redis,transit,work,ocr-cache,previews,models,lists,requests,imports,exports,backup/db,backup/volumes,backup/config,secrets,deploy}
chown -R "$APP_UID:$APP_UID" "$BASE"/{transit,work,ocr-cache,previews,models,lists,requests,imports,exports}
chmod 750 "$BASE"/{transit,work,ocr-cache,previews,models,lists,requests,imports,exports}
chmod 700 "$BASE/secrets" "$BASE/backup"
chown "$DEPLOY_USER:$DEPLOY_USER" "$BASE/deploy" && chmod 750 "$BASE/deploy"
mkdir -p /opt/objektakte && chown -R "$DEPLOY_USER:$DEPLOY_USER" /opt/objektakte
ls -la "$BASE" | sed 's/^/   /'

echo "== 3. Secrets unter $BASE/secrets (vorhandene Dateien bleiben unveraendert)"
cd "$BASE/secrets"
for n in db_root_password db_app_password db_worker_password db_migrate_password db_backup_password db_ro_password \
         redis_password iban_key iban_hmac_key token_key totp_key; do
  [ -s "$n" ] || printf '%s' "$(openssl rand -base64 32)" > "$n"
done
[ -s app_secret_key ] || printf '%s' "$(openssl rand -base64 48)" > app_secret_key
[ -s readyz_token ]   || printf '%s' "$(openssl rand -hex 32)"    > readyz_token
for n in google_client_secret openai_api_key anthropic_api_key smtp_password backup_age_recipient rclone.conf hvm_signature.jpg; do
  [ -f "$n" ] || : > "$n"
done
chmod 600 "$BASE"/secrets/*; chown root:root "$BASE"/secrets/*
echo "   Leer und spaeter zu fuellen: google_client_secret, openai_api_key, anthropic_api_key, smtp_password, backup_age_recipient, rclone.conf, hvm_signature.jpg"
echo "   Die Schluessel (iban_key, iban_hmac_key, token_key, totp_key, app_secret_key, db_root_password) gehoeren zusaetzlich in den Passwortmanager (V-21)."

echo "== 4. SSH-Schluessel fuer GitHub"
OUT="$DH/github-werte.txt"
: > "$OUT"; chmod 600 "$OUT"; chown "$DEPLOY_USER:$DEPLOY_USER" "$OUT"
# Wurde der Deploy Key fuer den ersten Checkout bereits als root erzeugt (docs/betrieb/github-deploy.md, Ablauf),
# wandert er zu deploy; so bleibt es bei einem einzigen Deploy Key im Repository.
if [ ! -f "$DH/.ssh/github_deploy" ] && [ -f "$CALLER_HOME/.ssh/github_deploy" ] && [ "$CALLER_HOME" != "$DH" ]; then
  mv "$CALLER_HOME/.ssh/github_deploy" "$CALLER_HOME/.ssh/github_deploy.pub" "$DH/.ssh/"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$DH/.ssh/github_deploy" "$DH/.ssh/github_deploy.pub"; chmod 600 "$DH/.ssh/github_deploy"
  echo "   Deploy Key von $CALLER nach $DH/.ssh uebernommen (bereits in GitHub eingetragen: Abschnitt A ueberspringen)."
fi
if [ ! -f "$DH/.ssh/github_deploy" ]; then
  sudo -u "$DEPLOY_USER" ssh-keygen -q -t ed25519 -N "" -C "objektakte-vps-deploy-key" -f "$DH/.ssh/github_deploy"
fi
# Host-Key von GitHub fuer deploy, gegen den veroeffentlichten Fingerabdruck geprueft; sonst scheitert
# jeder git fetch ohne Terminal mit "Host key verification failed".
GITHUB_ED25519_FP="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
if ! sudo -u "$DEPLOY_USER" ssh-keygen -F github.com -f "$DH/.ssh/known_hosts" >/dev/null 2>&1; then
  SCAN="$(ssh-keyscan -t ed25519 github.com 2>/dev/null)"
  FP="$(printf '%s\n' "$SCAN" | ssh-keygen -lf - 2>/dev/null | awk '{print $2}')"
  if [ -n "$SCAN" ] && [ "$FP" = "$GITHUB_ED25519_FP" ]; then
    printf '%s\n' "$SCAN" >> "$DH/.ssh/known_hosts"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$DH/.ssh/known_hosts"; chmod 600 "$DH/.ssh/known_hosts"
    echo "   Host-Key von github.com fuer $DEPLOY_USER eingetragen."
  else
    echo "   Warnung: Host-Key von github.com nicht eingetragen (nicht abrufbar oder abweichender Fingerabdruck ${FP:-keiner})."
  fi
fi
if ! grep -q "IdentityFile ~/.ssh/github_deploy" "$DH/.ssh/config" 2>/dev/null; then
  printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> "$DH/.ssh/config"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$DH/.ssh/config"; chmod 600 "$DH/.ssh/config"
fi
{
  echo "=== A. Deploy Key: GitHub, Repository, Settings, Deploy keys, Add deploy key (Titel objektakte-vps, ohne Schreibrecht) ==="
  cat "$DH/.ssh/github_deploy.pub"
  echo
} >> "$OUT"

if ! grep -q "github-actions-deploy" "$DH/.ssh/authorized_keys" 2>/dev/null; then
  TMPK="$(mktemp -d)"
  ssh-keygen -q -t ed25519 -N "" -C "github-actions-deploy" -f "$TMPK/gh_actions_deploy"
  echo "command=\"/opt/objektakte/scripts/deploy_remote.sh\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty $(cat "$TMPK/gh_actions_deploy.pub")" >> "$DH/.ssh/authorized_keys"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$DH/.ssh/authorized_keys"; chmod 600 "$DH/.ssh/authorized_keys"
  {
    echo "=== B. Secret DEPLOY_SSH_KEY (GitHub, Settings, Secrets and variables, Actions; vollstaendig mit BEGIN- und END-Zeile) ==="
    cat "$TMPK/gh_actions_deploy"
    echo
  } >> "$OUT"
  shred -u "$TMPK/gh_actions_deploy" "$TMPK/gh_actions_deploy.pub"; rmdir "$TMPK"
else
  echo "   Aktionsschluessel ist bereits in authorized_keys eingetragen; kein neuer privater Schluessel erzeugt." | tee -a "$OUT"
fi
{
  echo "=== C. Secret DEPLOY_KNOWN_HOSTS ==="
  ssh-keyscan -t ed25519 "$HOST_IP" 2>/dev/null
  echo
  echo "=== D. Weitere Secrets ==="
  echo "DEPLOY_HOST=$HOST_IP"
  echo "DEPLOY_USER=$DEPLOY_USER"
} >> "$OUT"

echo
echo "== Fertig. Naechste Schritte:"
echo "   1. Werte aus $OUT in GitHub eintragen (A als Deploy Key, B bis D als Actions-Secrets), danach die Datei loeschen:"
echo "      shred -u $OUT"
echo "   2. Als $DEPLOY_USER pruefen: ssh -T git@github.com   (erwartet: successfully authenticated)"
echo "   3. Checkout: git clone $REPO_SSH /opt/objektakte   (oder remote auf SSH umstellen, wenn schon vorhanden)"
echo "   4. .env aus .env.example anlegen (Werte aus dem Serverbefund), dann: scripts/deploy.sh --first-run <branch>"
echo "   5. SSH-Haertung und Firewall nach docs/betrieb.md 2.1, sobald der Login als $DEPLOY_USER in einer zweiten Sitzung geht."
