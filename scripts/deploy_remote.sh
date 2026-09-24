#!/usr/bin/env bash
# Eingeschraenkter SSH-Einstieg fuer GitHub Actions (docs/betrieb/github-deploy.md).
# In ~/.ssh/authorized_keys des Deploy-Nutzers eingetragen als
#   command="/opt/objektakte/scripts/deploy_remote.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-actions-deploy
# Erlaubte Eingaben (erstes Wort Aktion, zweites Wort Branch, drittes Wort Argument):
#   check <branch>            Verbindungsprobe ohne Schreibwirkung, zeigt zusaetzlich die Verzeichnisse
#   pull <branch>             Checkout aktualisieren, nichts starten
#   befund <branch>           Serverbefund (scripts/measure_server.sh, geschwaerzt) ausgeben
#   env-init <branch>         .env aus deploy/env.produktion anlegen, nie ueberschreiben
#   first-run <branch>        Erstinstallation (scripts/deploy.sh --first-run)
#   deploy <branch>           Deployment (scripts/deploy.sh)
#   rollback                  vorherige Version (scripts/rollback.sh)
#   create-admin <branch> <email>  Admin anlegen; Startpasswort nur in /home/deploy/admin-startpasswort.txt
#   ps <branch>               Zustand aller Container anzeigen (nur lesend)
#   smoke <branch>            Anwendung und Zertifikat ueber die Domain pruefen (nur lesend)
#   cert-retry <branch>       Neue Zertifikatsanforderung ausloesen (Web-Container neu aufbauen);
#                             hoechstens einmal je Stunde, Let's Encrypt begrenzt Fehlversuche
#   logs <branch> [dienst]    Letzte Logzeilen; ohne Dienst die der Anwendungsdienste (nur lesend)
#   db-status <branch>        Datenbankkonten und Tabellenzahl anzeigen (nur lesend)
#   db-reset <branch>         Datenverzeichnis der Datenbank leeren und neu initialisieren; bricht ab,
#                             sobald ein Schema vorhanden ist (Schutz gegen Datenverlust)
#   oauth-check <branch>      Google-Verbindung: Konfiguration ohne Geheimnisse und Probe der Client-Zugangsdaten
#   ai-check <branch> [probe] KI-Anbieter (Stufe 3): Konfiguration ohne Geheimnisse, Schluessel vorhanden, Preisliste,
#                             Modell beim Anbieter abrufbar; mit probe eine echte Klassifikation eines synthetischen
#                             Textes (kostet wenige Token, wird nicht in ai_calls protokolliert)
#   ai-reclassify <branch> [<objekt>][+echt]  Unklar-Faelle mit Grund Stufe 3 nicht freigegeben, KI nicht verfuegbar oder
#                             Kostenlimit erneut klassifizieren (ohne echt nur Vorschau; ohne Objekt alle Objekte)
#   deploy-tests <branch>     Deployment-Tests T2, T3, T11, T14 (nur lesend)
#   doc-status <branch> [nr]  Dokumente je Objekt und Status, offene und fehlgeschlagene Jobs (nur lesend);
#                             mit Objektnummer je Dokument Stufen, Entitaetenzaehler und Faelle, ohne Namen
#   reconcile-all <branch> [ohne-ordner]  Ordnerabgleich aller aktiven Objekte (legt fehlende Struktur an); ohne-ordner = nur Objekte ohne Objektordner
#   config-set <branch> <schluessel=wert>  Konfigurationswert setzen (Wert als JSON: true, 5, "text", {"a":1}); in der interaktiven Shell das Argument in einfache Anfuehrungszeichen setzen; Audit
#   altbestand-import <branch>  Quellordner aus db/seeds/altbestand_ordner.txt in die Altbestand-Tabelle
#   altbestand-objekte <branch> [echt]  Objekte fuer Altbestand-Quellen ohne Objekt anlegen (echt = anlegen, sonst Vorschau)
#   paperless-feld-alle <branch> [echt] Feld MHV Objekt fuer alle zugeordneten Speicherpfade setzen und Bestandslauf starten
#   paperless-feld-abgleich <branch> <objekt>[+echt]  Feld MHV Objekt je Dokument gegen die Zuordnung pruefen; echt uebernimmt Abweichungen (leer -> Eingang)
#   objekt-anschriften <branch> [echt|korrigieren|korrigieren+echt]  Weitere Anschriften (Eckobjekte) aus den Objektbezeichnungen ableiten (echt = speichern, sonst Vorschau; korrigieren = vom Kommando gesetzte Hauptanschriften neu ableiten)
#   zuordnung-pruefen <branch> [<objekt>][+echt][+details][+ohne-ki][+limit=N]  Gegenprobe der Objektzuordnung fuer Paperless-Feldimporte (Vorschau ohne KI; echt loest mit KI auf; laeuft im worker-io)
#   backup-voll <branch>      Volle Sicherung sofort (Datenbank und Fachverzeichnisse, rund 20 Minuten, nur in tmux); schreibt status.json
#   altbestand-aufarbeiten <branch> [echt] Alle Altbestand-Ordner mit Objekt aufarbeiten (echt = Celery-Aufgabe, sonst Vorschau)
#   verarbeitung-alle <branch> [echt][+ohne=133,216]  Verarbeitungslaeufe fuer alle Objekte mit offener Arbeit (echt = einreihen, sonst Vorschau; ohne = Objekte ausnehmen)
#   review-status <branch> [<objekt>]   Offene Pruefcenter-Faelle je Art und Unterart, Vorschlaege, KI-Nachklassifizierbarkeit (keine Personendaten)
#   sync-status <branch> [live]          Verbindung Drive, Anwendung, Paperless: Schalter, Verbindungstest, Cursor, Auffindbarkeit je Quelle, Operationen (lesend; live = echter Verbindungstest)
#   classifier-status <branch> [list|train|train+force|deactivate]  Stufe 2: Kaltstartstatus, Modelle, Training (train+force auch waehrend laufender Verarbeitung), Modell abschalten
#   sync-retry <branch> [echt|bestand+<id>]  Sync-Operationen mit Paperless-404 erneut einreihen (Vorschau ohne echt); Bestandslauf fortsetzen
#   jobs-bereinigen <branch> [echt] Ueberzaehlige wartende Wiederholungsjobs (#n) bereinigen (echt = ausfuehren, sonst Vorschau)
#   redis-status <branch>     Redis: Speicher, Schluesselzahl, Warteschlangenlaengen, groesste Schluessel (nur lesend)
#   env-set <branch> <SCHLUESSEL=wert[+SCHLUESSEL=wert]>  Freigegebene Betriebswerte in .env setzen (Ressourcen, Parallelitaet);
#                             wirksam erst mit deploy; Sicherung .env.bak
#                             aufnehmen (ohne Doppelte) und Namen aus Drive lesen
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
# config-set traegt JSON-Werte (Objekte, Listen, Zeichenketten) ohne Leerzeichen; der Wert geht nur als Umgebungsvariable
# an Python und wird nie von der Shell ausgewertet. Alle anderen Aktionen behalten den engen Zeichenvorrat.
if [ "$ACTION" = "config-set" ]; then
  case "${ARG:-}" in *[!A-Za-z0-9@._+=:,/{}\[\]\"-]*) echo "Argument unzulaessig (config-set: JSON ohne Leerzeichen; erlaubt sind Buchstaben, Ziffern, doppelte Anfuehrungszeichen und @._+=:,/{}[]-)"; exit 2 ;; esac
else
  case "${ARG:-}" in *[!A-Za-z0-9@._+=,-]*) echo "Argument unzulaessig"; exit 2 ;; esac
fi
echo "$(date -Is) $ACTION ${BRANCH:-} ${ARG:-} von ${SSH_CLIENT:-unbekannt}" >> "$LOG"
case "$ACTION" in
  deploy)    ensure_github_hostkey; ensure_network; exec scripts/deploy.sh "$BRANCH" ;;
  first-run) ensure_github_hostkey; ensure_network; exec scripts/deploy.sh --first-run "$BRANCH" ;;
  rollback)  exec scripts/rollback.sh ;;
  check)
    echo "Verbindung ok: $(hostname) als $(whoami), Checkout $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short=12 HEAD)"
    [ -f .env ] && echo ".env vorhanden" || echo ".env fehlt noch (Aktion env-init)"
    docker compose version 2>/dev/null | head -1 || echo "docker compose nicht verfuegbar"
    echo "Verzeichnisse:"
    echo "  Programm und Konfiguration: /opt/objektakte ($(du -sh /opt/objektakte 2>/dev/null | cut -f1))"
    echo "  Daten, Secrets, Sicherungen: /srv/objektakte ($(du -sh /srv/objektakte 2>/dev/null | cut -f1))"
    ls -1 /srv/objektakte 2>/dev/null | sed 's/^/    /' || echo "    /srv/objektakte nicht lesbar"
    echo "  Ablaufprotokoll der Fernaufrufe: $LOG"
    # Platzhalter-Secrets der Paperless-Anbindung (12.09.2026): deploy.sh legt sie ueber sudo -n an; ohne
    # passwortloses sudo muss der Admin sie einmalig als root anlegen (docs/betrieb/paperless-sync.md 3.2).
    if sudo -n true 2>/dev/null; then echo "  sudo ohne Passwort: ja"; else echo "  sudo ohne Passwort: nein"; fi
    for s in paperless_token paperless_webhook_token; do
      if sudo -n test -e "/srv/objektakte/secrets/$s" 2>/dev/null || [ -e "/srv/objektakte/secrets/$s" ]; then
        echo "  Secret-Datei $s: vorhanden"
      elif sudo -n true 2>/dev/null || [ -r /srv/objektakte/secrets ]; then
        echo "  Secret-Datei $s: fehlt (deploy legt sie leer an)"
      else
        echo "  Secret-Datei $s: von $(whoami) nicht pruefbar (Verzeichnis nur fuer root lesbar)"
      fi
    done
    # Nur Vorhandensein und Alter, nie der Inhalt: Workflow-Logs sind fuer jeden mit Repository-Zugang lesbar.
    if [ -f /home/deploy/admin-startpasswort.txt ]; then
      echo "  Startpasswort des ersten Admin: /home/deploy/admin-startpasswort.txt vorhanden, geschrieben am $(date -r /home/deploy/admin-startpasswort.txt '+%d.%m.%Y %H:%M') (lesen mit: sudo cat, danach shred -u)"
    else
      echo "  Startpasswort des ersten Admin: /home/deploy/admin-startpasswort.txt nicht vorhanden (nie geschrieben oder bereits geloescht)"
    fi
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
  smoke)
    dom="$(envval APP_DOMAIN)"
    echo "Anwendung ueber Traefik (ohne Zertifikatspruefung):"
    curl -sk -o /dev/null -w '  HTTPS %{http_code}\n' --max-time 10 "https://${dom}/healthz/" || echo "  keine Antwort"
    echo "Zertifikat:"
    echo | openssl s_client -connect "${dom}:443" -servername "${dom}" 2>/dev/null \
      | openssl x509 -noout -issuer -subject -dates 2>/dev/null | sed 's/^/  /' || echo "  kein Zertifikat lesbar"
    echo "Mit Zertifikatspruefung:"
    curl -fsS -o /dev/null -w '  HTTPS %{http_code} tls %{ssl_verify_result}\n' --max-time 10 "https://${dom}/healthz/" \
      || echo "  Zertifikat nicht gueltig"
    echo "Bereitschaft im Container (unabhaengig von Traefik):"
    docker compose exec -T web python -c "
import json, urllib.request
tok = open('/run/secrets/readyz_token').read().strip()
req = urllib.request.Request('http://127.0.0.1:8000/readyz/', headers={'Authorization': 'Bearer ' + tok})
with urllib.request.urlopen(req, timeout=10) as r:
    print(' ', r.status, json.dumps(json.loads(r.read().decode()), ensure_ascii=False)[:500])
" 2>&1 | sed 's/^/  /' || echo "  Bereitschaftspruefung fehlgeschlagen"
    echo "Traefik-Meldungen:"
    docker logs "$(docker ps --filter name=traefik --format '{{.Names}}' | head -1)" --tail 40 2>&1 \
      | grep -iE 'acme|certificate|objektakte' | tail -15 | sed 's/^/  /' || echo "  keine Meldungen"
    ;;
  cert-retry)
    # Traefik fordert ein Zertifikat nur an, wenn ein Router neu bekannt wird oder eine Anfrage eintrifft,
    # und wartet nach Fehlschlaegen. Ein Neuaufbau des Web-Containers meldet den Router neu an und loest
    # damit einen neuen Versuch aus. Nicht oefter als einmal je Stunde aufrufen: Let's Encrypt begrenzt
    # fehlgeschlagene Validierungen auf fuenf je Stunde und Name.
    dom="$(envval APP_DOMAIN)"
    [ -n "$dom" ] || { echo "APP_DOMAIN fehlt in .env"; exit 2; }
    echo "Web-Container neu aufbauen, damit Traefik den Router neu aufnimmt"
    docker compose up -d --force-recreate --no-deps web
    for i in $(seq 1 12); do
      sleep 10
      [ "$(docker compose ps web --format '{{.Health}}' 2>/dev/null)" = "healthy" ] && break
    done
    echo "Warte auf ein gueltiges Zertifikat fuer ${dom} (bis zu drei Minuten):"
    for i in $(seq 1 18); do
      if curl -fsS --max-time 10 -o /dev/null "https://${dom}/healthz/"; then
        echo "  Zertifikat gueltig (Versuch ${i})"
        echo | openssl s_client -connect "${dom}:443" -servername "${dom}" 2>/dev/null \
          | openssl x509 -noout -issuer -subject -dates 2>/dev/null | sed 's/^/  /'
        exit 0
      fi
      sleep 10
    done
    echo "  Zertifikat weiterhin nicht gueltig. Juengste Meldungen zur Ausstellung:"
    docker logs "$(docker ps --filter name=traefik --format '{{.Names}}' | head -1)" --tail 300 2>&1 \
      | grep -iE "acme|obtain" | grep -i "${dom}" | tail -3 | sed 's/^/  /'
    exit 1
    ;;
  ps)
    docker compose ps --all
    ;;
  logs)
    case "${ARG:-}" in
      "") set -- web worker worker-nlp worker-io beat ;;
      web|worker|worker-nlp|worker-io|beat|db|redis|backup|classifier) set -- "$ARG" ;;
      *) echo "Unbekannter Dienst: $ARG"; exit 2 ;;
    esac
    for svc in "$@"; do
      echo "===== $svc ====="
      docker compose logs --no-color --tail 40 "$svc" 2>&1 | tail -40
    done
    ;;
  db-status)
    docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" -N -e "
      SELECT CONCAT(\"Konto: \", user) FROM mysql.user WHERE user LIKE \"app_%\" ORDER BY user;
      SELECT CONCAT(\"Tabellen in $MARIADB_DATABASE: \", COUNT(*)) FROM information_schema.tables WHERE table_schema = \"$MARIADB_DATABASE\";"'
    ;;
  db-reset)
    TABLES="$(docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = \"$MARIADB_DATABASE\" AND table_name = \"django_migrations\""' 2>/dev/null | tr -d '[:space:]')"
    if [ "${TABLES:-1}" != "0" ]; then
      echo "Abbruch: Die Datenbank enthaelt bereits ein Schema (django_migrations vorhanden oder nicht pruefbar)."
      echo "Ein Zuruecksetzen wuerde Daten loeschen und ist nur vor der ersten erfolgreichen Migration vorgesehen."
      exit 2
    fi
    echo "Datenbank ist ohne Schema; Datenverzeichnis wird geleert und neu initialisiert."
    docker compose stop db
    docker compose run --rm --no-deps --user 0 --entrypoint sh db -c 'rm -rf /var/lib/mysql/* /var/lib/mysql/.[!.]* 2>/dev/null; ls -A /var/lib/mysql | wc -l'
    docker compose up -d db
    for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
      sleep 10
      state="$(docker compose ps db --format '{{.Health}}' 2>/dev/null)"
      [ "$state" = "healthy" ] && { echo "Datenbank neu initialisiert und healthy"; break; }
    done
    docker compose ps db
    ;;
  create-admin)
    [ -n "${ARG:-}" ] || { echo "E-Mail fehlt"; exit 2; }
    PW="$(openssl rand -base64 18)"
    # docker compose exec umgeht das ENTRYPOINT des Images; die Unterbefehle (app-*) liegen dort, also
    # wird das Entrypoint-Skript ausdruecklich aufgerufen.
    ADMIN_PASSWORD="$PW" docker compose exec -T -e ADMIN_PASSWORD web \
      /usr/local/bin/entrypoint.sh app-create-admin --email "$ARG" --no-input
    umask 077; printf 'Startpasswort fuer %s: %s\n' "$ARG" "$PW" > /home/deploy/admin-startpasswort.txt
    echo "Startpasswort liegt auf dem Server in /home/deploy/admin-startpasswort.txt (nach dem ersten Login loeschen: shred -u)."
    ;;
  oauth-check)
    # Konfiguration der Google-Verbindung pruefen, ohne Geheimnisse auszugeben, und die Client-Zugangsdaten
    # gegen den Token-Endpunkt proben: invalid_client heisst Client-ID und Secret passen nicht zusammen,
    # invalid_grant heisst die Zugangsdaten sind in Ordnung (nur der absichtlich ungueltige Probe-Token wird abgelehnt).
    docker compose exec -T web python manage.py shell <<'PY'
import json, urllib.error, urllib.parse, urllib.request
from apps.config import store
from apps.drive import oauth
try:
    cfg = oauth.client_config()["web"]
except Exception as exc:
    print("Konfiguration unvollstaendig:", exc)
    raise SystemExit(0)
cid, sec = cfg["client_id"], cfg["client_secret"]
print(f"Client-ID: {cid[:14]}... ({len(cid)} Zeichen, Projektnummer {cid.split('-')[0]})")
print(f"Secret: {sec[:7]}... ({len(sec)} Zeichen)")
print("Redirect:", cfg["redirect_uris"])
print("DRIVE_ACCOUNT_EMAIL:", oauth.account_email() or "(leer)")
print("drive.root_folder_id:", store.get("drive.root_folder_id") or "(leer)")
print("Token in der Datenbank:", oauth.token_status().get("status"))
data = urllib.parse.urlencode({"client_id": cid, "client_secret": sec, "grant_type": "refresh_token", "refresh_token": "probe"}).encode()
try:
    urllib.request.urlopen(urllib.request.Request(oauth.TOKEN_URI, data=data), timeout=15)
    print("PROBE: unerwartet erfolgreich")
except urllib.error.HTTPError as e:
    body = json.loads(e.read().decode() or "{}")
    err = body.get("error")
    if err == "invalid_client":
        print("PROBE: Google lehnt Client-ID oder Secret ab (invalid_client). Beide Werte aus derselben JSON-Datei des Clients setzen, Dienste mit --force-recreate neu starten.")
    elif err == "invalid_grant":
        print("PROBE: Client-Zugangsdaten in Ordnung (Google meldet nur den erwarteten invalid_grant der Probe). Ein 401 beim Verbinden haette dann eine andere Ursache.")
    else:
        print("PROBE:", e.code, err, body.get("error_description"))
except Exception as exc:
    print("PROBE nicht moeglich:", exc)
PY
    ;;
  ai-check)
    # KI-Anbieter (Stufe 3) pruefen, ohne Geheimnisse auszugeben: Konfiguration je Anbieter, Schluessel vorhanden,
    # Preisliste, Modell beim Anbieter abrufbar (kein Token verbraucht). Mit Argument "probe" eine echte Klassifikation
    # eines synthetischen Textes ohne Personenbezug ueber die Provider-Klasse (Maskierungspruefung, Schema), Kosten nach
    # Preisliste; der Aufruf wird nicht in ai_calls protokolliert. Laeuft im worker-io, weil dort die Stufe 3 arbeitet.
    docker compose exec -T -e AI_PROBE="${ARG:-}" worker-io python manage.py shell <<'PY'
import os, time
from decimal import Decimal
from apps.config import store
from apps.ai.provider import PriceList, ProviderConfig, api_key_for
order = store.get("ai.provider_order", []) or []
print("Reihenfolge (ai.provider_order):", order)
pl = PriceList.from_settings()
print(f"Preisliste: Version {pl.version}, Modelle: {', '.join(sorted(pl.prices)) or 'keine (mit Kostenlimit werden Aufrufe blockiert, ohne Kostenlimit mit 0 EUR gebucht)'}")
print("Schwellen: Aufruf unter", store.get("classification.threshold_stage3_call"), "| Ueberschreiben ab", store.get("classification.threshold_stage3_override"), "| Ablage ab", store.get("classification.threshold_auto_file"))
print("Nachklassifikation (ai.reclassify_enabled):", store.get("ai.reclassify_enabled"), "| Mietvertragsdaten (ai.extract_lease_facts):", store.get("ai.extract_lease_facts"))
from datetime import timedelta
from django.db.models import Count, Sum
from django.utils import timezone
from apps.ai.models import AiCall
seit = timezone.now() - timedelta(hours=24)
rows = AiCall.objects.filter(requested_at__gte=seit).values("provider", "purpose", "status").annotate(n=Count("id"), eur=Sum("cost_eur")).order_by("provider", "purpose", "status")
print("Aufrufe der letzten 24 Stunden (Anbieter/Zweck/Status):", ", ".join(f"{r['provider']}/{r['purpose']}/{r['status']}={r['n']} ({r['eur'] or 0} EUR)" for r in rows) or "keine")
for name in ("openai", "anthropic"):
    cfg = ProviderConfig.from_settings(name)
    key = api_key_for(name)
    endpoint = cfg.endpoint or os.environ.get(f"{name.upper()}_BASE_URL") or "(Standard des Anbieters)"
    schl = f"vorhanden ({len(key)} Zeichen)" if key else "FEHLT (Secret-Datei leer oder nicht eingebunden)"
    limit = cfg.cost_limit_eur_per_object if cfg.cost_limit_eur_per_object is not None else "keines"
    print(f"{name}: freigegeben={cfg.enabled} Modell={cfg.model or '(leer)'} Endpunkt={endpoint} Region={cfg.region or '(leer)'} "
          f"Timeout={cfg.timeout_s:g}s Versuche={cfg.max_attempts} Kostenlimit je Objekt={limit} EUR Schluessel={schl}")
    _pr = pl.prices.get(cfg.model or "")
    if cfg.enabled and cfg.model and not (_pr and (_pr[0] > 0 or _pr[1] > 0)):
        if cfg.cost_limit_eur_per_object is not None:
            print(f"  WARNUNG: Modell {cfg.model} steht nicht in ai.price_list oder mit 0 EUR; der Router blockiert Aufrufe (budget_blocked), bis ein Preis groesser 0 eingetragen ist.")
        else:
            print(f"  WARNUNG: Modell {cfg.model} steht nicht in ai.price_list oder mit 0 EUR und kein Kostenlimit gesetzt; Aufrufe laufen ohne Kostenbremse und werden mit 0 EUR gebucht.")
    if cfg.enabled and not key:
        print(f"  Hinweis: {name} ist freigegeben, aber ohne Schluessel; Aufrufe enden mit provider_error.")
cfg = ProviderConfig.from_settings("openai")
key = api_key_for("openai")
if not (key and cfg.model):
    print("PROBE openai uebersprungen: Schluessel oder Modell fehlt.")
    raise SystemExit(0)
from openai import OpenAI
try:
    from apps.ai.providers.openai_provider import resolve_base_url
except ImportError:  # Image aelter als Commit 3b3faba: gleiche Regel inline, damit die Probe vor dem Deploy laeuft
    def resolve_base_url(endpoint):
        return (endpoint or "").strip() or os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
base_url = resolve_base_url(cfg.endpoint)
client = OpenAI(api_key=key, base_url=base_url, timeout=20, max_retries=0)
try:
    m = client.models.retrieve(cfg.model)
    print(f"PROBE Schluessel und Modell: {m.id} ist ueber {base_url} abrufbar.")
except Exception as exc:
    print(f"PROBE Schluessel/Modell FEHLGESCHLAGEN: {type(exc).__name__}: {str(exc)[:240]}")
    raise SystemExit(0)
if os.environ.get("AI_PROBE") != "probe":
    print("Echte Klassifikationsprobe nur mit Argument probe (kostet wenige Token).")
    raise SystemExit(0)
from apps.ai.providers.openai_provider import OpenAIProvider
from apps.ai.schema import ClassificationRequest, taxonomy_from_catalog
req = ClassificationRequest(
    excerpt_masked=("Hausgeldabrechnung 2025 fuer die Einheit WE01. Abrechnungszeitraum 01.01.2025 bis 31.12.2025. "
                    "Gesamtkosten der Gemeinschaft, Verteilung nach Miteigentumsanteilen, Abrechnungsergebnis: Nachzahlung 184,20 EUR. "
                    "Bitte ueberweisen Sie den Betrag bis zum 30.04.2026 auf das Konto der Gemeinschaft [IBAN]."),
    filename_masked="Probe_Hausgeldabrechnung_2025_WE01.pdf",
    management_type="weg",
    taxonomy=taxonomy_from_catalog(),
    unit_label_patterns=["WE"],
    hints={"probe": True, "has_period_year": True, "has_unit": True},
)
started = time.monotonic()
try:
    out = OpenAIProvider().classify(req, cfg)
except Exception as exc:
    print(f"PROBE Klassifikation FEHLGESCHLAGEN: {type(exc).__name__}: {str(exc)[:240]}")
    raise SystemExit(0)
dauer = int((time.monotonic() - started) * 1000)
raw = out.raw
kosten = pl.cost(cfg.model, raw.tokens_in, raw.tokens_out) if raw else Decimal(0)
if out.error:
    print(f"PROBE Antwort verletzt das Schema: {out.error}; Token {raw.tokens_in if raw else '?'}/{raw.tokens_out if raw else '?'}, {dauer} ms")
    raise SystemExit(0)
r = out.result
print(f"PROBE Klassifikation ok: Kategorie {r.category} Unterordner {getattr(r, 'subfolder', None)} Dokumentart {getattr(r, 'document_type', None)} "
      f"Konfidenz {r.confidence} | Token ein {raw.tokens_in} aus {raw.tokens_out} | {dauer} ms | Kosten nach Preisliste {kosten} EUR")
print("Erwartung: Kategorie 05 (Abrechnung) mit hoher Konfidenz; sonst Modell oder Prompt pruefen.")
PY
    ;;
  ai-reclassify)
    # Nachklassifikationslauf (ai_reclassify): Unklar-Faelle mit Grund Stufe 3 nicht freigegeben, KI nicht verfuegbar
    # oder Kostenlimit erneut durch classify (und damit Stufe 3) schicken. Argumente mit + getrennt: Objektnummer,
    # echt, ohne=133,216 (Objekte ausnehmen), limit=200 (hoechstens so viele Dokumente, fuer Piloten), details (eine
    # Zeile je Dokument statt nur je Objekt), unterfall=manual_check (KI-Einstufung Sonstiges statt unter Schwelle,
    # auch unterfall=below_threshold,manual_check), alle (auch Faelle mit fruehem Stufe-3-Ergebnis, nach Aenderung
    # von Auftrag oder Regeln; fuer manual_check noetig). Ohne echt nur Vorschau. --force, weil der manuelle Lauf
    # unabhaengig von ai.reclassify_enabled erlaubt ist.
    obj=""; echt=""; args=(--force)
    IFS='+' read -r -a teile <<<"${ARG:-}"
    for t in "${teile[@]}"; do
      case "$t" in
        "") ;;
        echt) echt="1" ;;
        details) args+=(--details) ;;
        alle) args+=(--alle) ;;
        ohne=*) args+=(--ohne "${t#ohne=}") ;;
        limit=*) args+=(--limit "${t#limit=}") ;;
        unterfall=*) args+=(--unterfall "${t#unterfall=}") ;;
        *[!0-9]*) echo "Argument unbekannt: $t (erlaubt: Objektnummer, echt, ohne=NR,NR, limit=N, unterfall=A,B, alle, details)"; exit 2 ;;
        *) obj="$t" ;;
      esac
    done
    [ -n "$obj" ] && args+=(--object "$obj")
    [ -z "$echt" ] && args+=(--dry-run)
    echo "ai_reclassify ${args[*]} (Vorschau: $([ -z "$echt" ] && echo ja || echo nein))"
    docker compose exec -T web python manage.py ai_reclassify "${args[@]}"
    echo "$(date -Is) ai-reclassify ${ARG:-ohne-argument}" >> "$LOG"
    ;;
  classifier-status)
    # Lokaler Klassifikator (Stufe 2, B-27): ohne Argument Status der Kaltstartphase (aktives Modell, gewichtete Beispiele je
    # Klasse, Schwelle stage2_min_samples_per_class); "list" zeigt alle Modelle; "train" trainiert, wenn die Schwelle erreicht
    # ist und kein Lauf laeuft; "train+force" erzwingt das Training (auch unter der Schwelle und waehrend laufender
    # Verarbeitung). Laeuft im worker-nlp, weil dort das Modell geladen und der naechtliche Nachtrainingstask ausgefuehrt wird.
    case "${ARG:-status}" in
      status|list) docker compose exec -T worker-nlp python manage.py classifier "${ARG:-status}" ;;
      train)       docker compose exec -T worker-nlp python manage.py classifier train ;;
      train+force) docker compose exec -T worker-nlp python manage.py classifier train --force ;;
      deactivate)  docker compose exec -T worker-nlp python manage.py shell -c "from apps.documents.models import ClassifierModel; n = ClassifierModel.objects.filter(is_active=True).update(is_active=False); print(f'{n} Modell(e) deaktiviert; Stufe 2 arbeitet wieder in der Kaltstartphase (kein Neustart noetig, das aktive Modell wird je Aufruf aus der Datenbank gelesen)')" ;;
      *) echo "Argument unzulaessig (status, list, train, train+force, deactivate)"; exit 2 ;;
    esac
    ;;
  sync-retry)
    # Blockierte oder fehlgeschlagene Sync-Operationen erneut einreihen, deren Grund ein Paperless-404 waehrend eines
    # Neustarts oder Updates war (Traefik antwortet "404 page not found", Befund 20.09.2026). Kein direkter Versand:
    # der Beat (sync.dispatch_due) verteilt mit hoechstens 200 gleichzeitig unterwegs, damit Redis nicht volllaeuft
    # (Befund 14.09.2026). Argument "bestand+<id>" setzt einen fehlgeschlagenen oder pausierten Bestandslauf fort.
    # Ohne Argument nur Vorschau; "echt" fuehrt aus. "push400" zeigt fehlgeschlagene Uebertragungen nach Paperless
    # mit HTTP 400, "push400+echt" reiht sie einmal erneut ein (nach dem Deploy vom 20.09.2026 nennt der Fehlertext
    # dann den Grund des Servers, etwa den nicht unterstuetzten Dateityp).
    docker compose exec -T -e SYNC_ARG="${ARG:-}" web python manage.py shell <<'PY'
import os
from collections import Counter
from django.db.models import Q
from django.utils import timezone
from apps.sync.models import InventoryRun, InventoryStatus, OperationKind, OperationStatus, SyncOperation
arg = os.environ.get("SYNC_ARG", "")
if arg.startswith("push400"):
    qs = SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PUSH, status=OperationStatus.FAILED, last_error__icontains="HTTP 400")
    print("fehlgeschlagene Uebertragungen nach Paperless mit HTTP 400:", qs.count(),
          dict(Counter(qs.values_list("document__mime_type", flat=True))))
    if arg != "push400+echt":
        print("Vorschau; mit push400+echt werden sie einmal erneut eingereiht"); raise SystemExit(0)
    n = qs.update(status=OperationStatus.PENDING, attempt_count=0, next_attempt_at=timezone.now(), blocked_reason=None,
                  locked_by=None, locked_at=None, dispatched_at=None)
    print(f"{n} Operationen erneut eingereiht; Ergebnis in sync-status (Fehlgeschlagene Uebertragungen nach Dateityp)")
    raise SystemExit(0)
if arg.startswith("bestand+"):
    from apps.sync import inventory
    run = InventoryRun.objects.get(pk=int(arg.split("+", 1)[1]))
    print(f"Bestandslauf {run.pk} ({run.kind}) Status {run.status}, Zaehler {run.counters or {}}")
    if run.status not in (InventoryStatus.FAILED, InventoryStatus.PAUSED):
        print("nur fehlgeschlagene oder pausierte Laeufe lassen sich fortsetzen"); raise SystemExit(0)
    inventory.resume(run)
    run.refresh_from_db(); print("jetzt", run.status, "(Fortsetzung an der gespeicherten Seite, Schritt eingereiht)")
    raise SystemExit(0)
pattern = Q(blocked_reason__icontains="404 page not found") | Q(last_error__icontains="404 page not found")
qs = SyncOperation.objects.filter(status__in=[OperationStatus.BLOCKED, OperationStatus.FAILED]).filter(pattern)
je_art = Counter(qs.values_list("kind", flat=True))
print("betroffen (Paperless-404):", dict(je_art) or "keine")
if arg != "echt":
    print("Vorschau; mit Argument echt werden sie erneut eingereiht"); raise SystemExit(0)
n = qs.update(status=OperationStatus.PENDING, attempt_count=0, next_attempt_at=timezone.now(), blocked_reason=None,
              locked_by=None, locked_at=None, dispatched_at=None)
print(f"{n} Operationen erneut eingereiht; der Beat verteilt hoechstens 200 gleichzeitig, Fortschritt in sync-status")
PY
    ;;
  sync-status)
    # Verbindung Drive, Anwendung, Paperless lesend pruefen: Schalter (paperless.enabled, Modus, Pilotumfang, Webhook,
    # Drive-Aenderungsabgleich), letzter Verbindungstest, Abfragecursor, je Dokumentquelle und Status wie viele Dokumente in
    # Drive, in Paperless oder in beiden auffindbar sind, Verknuepfungen, Operationen je Art und Status mit maskierten
    # Fehlertexten, Bestandslaeufe. Mit Argument "live" wird die Paperless-Verbindung tatsaechlich getestet (nur lesend,
    # legt nichts an). Keine Personendaten, kein Token in der Ausgabe.
    docker compose exec -T -e SYNC_LIVE="${ARG:-}" web python manage.py shell <<'PY'
import os, re
from collections import Counter
from django.db.models import Count, Exists, OuterRef, Q
from apps.config import store
from apps.sync import config, operations, services
from apps.sync.models import ExternalLink, InventoryRun, LinkRole, OperationKind, OperationStatus, SyncOperation, SyncSystem
from apps.sync.paperless import setup as paperless_setup
from apps.documents.models import Document
from apps.objects.models import ManagedObject

def mask(text):
    """Dateinamen und lange Kennungen aus Fehlertexten entfernen (keine Personendaten in der Ausgabe)."""
    text = re.sub(r"\S+\.(pdf|PDF|docx?|xlsx?|jpe?g|png|tiff?|msg|eml|zip)\b", "[DATEI]", text or "")
    return re.sub(r"\b[0-9A-Za-z_-]{25,}\b", "[ID]", text)[:160]

live = os.environ.get("SYNC_LIVE") == "live"
base = config.base_url() or ""
host = re.sub(r"^https?://", "", base).split("/")[0] if base else "(nicht gesetzt)"
print("=== Schalter ===")
print(f"paperless.enabled={config.enabled()} | konfiguriert (Adresse und Token)={config.configured()} | Modus={config.mode()} | Adresse={host}")
print(f"Pilotobjekte={sorted(config.pilot_object_numbers()) or 'keine'} | Webhook aktiv={config.webhook_enabled()} Webhook-Token={'vorhanden' if config.webhook_token() else 'FEHLT'}")
print(f"Neue Paperless-Dokumente uebernehmen={config.import_new_documents()} nur mit Objekt={config.import_only_with_object()} | Abfrage alle {store.get('paperless.poll_interval_minutes')} min")
print(f"Drive-Aenderungsabgleich (sync.drive_changes_enabled)={config.drive_changes_enabled()} alle {store.get('sync.drive_changes_interval_minutes')} min")
print("Bewertung: Schreiben nach Paperless " + ("fuer alle Objekte erlaubt" if config.active() and config.mode() == "full" else ("nur fuer Pilotobjekte und Eingang erlaubt" if config.active() and config.mode() == "pilot" else "NICHT erlaubt (Hauptschalter, Konfiguration oder Modus readonly)")))

print("=== Verbindung Paperless ===")
state = paperless_setup.check(create=False) if live else paperless_setup.state_from_cursor()
if state is None:
    print("kein Verbindungstest hinterlegt (Seite /verwaltung/sync/ Verbindung pruefen oder Aktion mit Argument live)")
else:
    print(f"{'LIVE' if live else 'letzter Test'} {state.checked_at or ''}: ok={state.ok} {state.message} | Server {state.server_version} API {state.api_version} | fehlt: {state.missing or 'nichts'}")
    print("Funktionen:", ", ".join(f"{k}={v}" for k, v in sorted((state.features or {}).items())) or "unbekannt")
def cur(system, name):
    row = services.get_cursor(system, name)
    return f"{row.value or '-'} {row.meta or ''} (Stand {row.updated_at:%d.%m. %H:%M})" if row else "nie"
print("Paperless letzte Abfrage:", cur(SyncSystem.PAPERLESS, "last_poll"))
print("Paperless Aenderungscursor:", cur(SyncSystem.PAPERLESS, "modified_cursor"))
print("Drive letzte Aenderungsabfrage:", cur(SyncSystem.DRIVE, "last_changes_poll"))
print("Drive Seitencursor:", "gesetzt" if services.get_cursor(SyncSystem.DRIVE, "page_token") else "nie")

print("=== Dokumente: wo auffindbar (ohne geloeschte) ===")
pl_exists = ExternalLink.objects.filter(document=OuterRef("pk"), system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL)
docs = Document.objects.filter(deleted_at__isnull=True).annotate(has_pl=Exists(pl_exists))
rows = docs.values("source", "status").annotate(
    n=Count("id"),
    drive=Count("id", filter=Q(drive_file_id__isnull=False)),
    paperless=Count("id", filter=Q(has_pl=True)),
    beide=Count("id", filter=Q(drive_file_id__isnull=False, has_pl=True)),
).order_by("source", "status")
tot = Counter()
print("Quelle/Status: gesamt | in Drive | in Paperless verknuepft | in beiden")
for r in rows:
    print(f"  {r['source']}/{r['status']}: {r['n']} | {r['drive']} | {r['paperless']} | {r['beide']}")
    for k in ("n", "drive", "paperless", "beide"):
        tot[k] += r[k]
print(f"Summe: {tot['n']} | Drive {tot['drive']} | Paperless {tot['paperless']} | beide {tot['beide']} | weder noch {tot['n'] - tot['drive'] - tot['paperless'] + tot['beide']}")
filed = docs.filter(status="filed")
print(f"Abgelegt (filed) ohne Paperless-Verknuepfung: {filed.filter(has_pl=False).count()} von {filed.count()} | Paperless-Dokumente ohne Drive-Datei: {docs.filter(source='paperless', drive_file_id__isnull=True).exclude(status__in=['duplicate','error']).count()}")
ohne_link_meta = filed.filter(has_pl=True).exclude(sync_links__system=SyncSystem.PAPERLESS, sync_links__synced_fields__isnull=False).count()
print(f"Abgelegt mit Paperless-Verknuepfung, aber Drive-Link/Objekt noch nicht nach Paperless geschrieben (synced_fields leer): {ohne_link_meta}")

print("=== Verknuepfungen (sync_links) je System und Zustand ===")
for r in ExternalLink.objects.values("system", "role", "state").annotate(n=Count("id")).order_by("system", "role", "state"):
    print(f"  {r['system']}/{r['role']}/{r['state']}: {r['n']}")

print("=== Operationen je Art und Status ===")
for r in SyncOperation.objects.values("kind", "status").annotate(n=Count("id")).order_by("kind", "status"):
    print(f"  {r['kind']}/{r['status']}: {r['n']}")
s = operations.summary()
print(f"Warteschlange (pending+running): {s.get('queue')} | aelteste wartende: {s.get('oldest_pending') or s.get('oldest') or '-'}")
print("Blockiert, Gruende (Top 8):")
for reason, n in Counter(mask(x) for x in SyncOperation.objects.filter(status=OperationStatus.BLOCKED).values_list("blocked_reason", flat=True)).most_common(8):
    print(f"  {n}x {reason or '(ohne Grund)'}")
print("Fehlgeschlagen, letzte Fehler (Top 8, Dateinamen maskiert):")
for err, n in Counter(mask(x) for x in SyncOperation.objects.filter(status=OperationStatus.FAILED).values_list("last_error", flat=True)).most_common(8):
    print(f"  {n}x {err or '(ohne Text)'}")
fehl_push = SyncOperation.objects.filter(kind=OperationKind.PAPERLESS_PUSH, status=OperationStatus.FAILED)
if fehl_push.exists():
    print("Fehlgeschlagene Uebertragungen nach Paperless nach Dateityp:")
    for r in fehl_push.values("document__mime_type").annotate(n=Count("id")).order_by("-n")[:8]:
        print(f"  {r['n']}x {r['document__mime_type'] or '(ohne)'}")
    for op in fehl_push.select_related("document").order_by("-updated_at")[:5]:
        d = op.document
        groesse = f"{d.size_bytes} Byte" if d else "-"
        print(f"  Operation {op.pk}: {d.mime_type if d else '-'}, {groesse}, Versuche {op.attempt_count}: {mask(op.last_error or '')[:200]}")

print("=== Bestandslaeufe (letzte 5) ===")
for run in InventoryRun.objects.order_by("-created_at")[:5]:
    print(f"  {run.pk} {run.kind} dry_run={run.dry_run} {run.status} {run.counters or {}} {('Fehler: ' + mask(run.error_message)) if run.error_message else ''}")
inbox_obj = ManagedObject.objects.filter(is_system_inbox=True).first()
if inbox_obj:
    ic = {r['status']: r['n'] for r in docs.filter(object=inbox_obj).values('status').annotate(n=Count('id'))}
    print(f"Eingangsobjekt {inbox_obj.object_number}: {ic or 'keine Dokumente'}")
else:
    print("Eingangsobjekt: nicht angelegt")
PY
    ;;
  review-status)
    # Pruefcenter-Bestand: offene Faelle je Art/Unterart, wie viele einen maschinellen Vorschlag tragen, wie viele die
    # KI nachklassifizieren kann (below_threshold mit Dokument im Status review), Stufe-3-Status; optional je Objekt.
    docker compose exec -T -e OBJ="${ARG:-}" web python manage.py shell <<'PY'
import os
from collections import Counter
from django.db.models import Count, Q
from apps.review.models import ReviewCase, CaseStatus
offen = ReviewCase.objects.filter(status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS])
if os.environ.get("OBJ"):
    offen = offen.filter(object__object_number=os.environ["OBJ"])
gesamt = offen.count()
print(f"Offene Faelle: {gesamt}")
rows = (offen.values("case_type", "case_subtype")
        .annotate(n=Count("id"), mit_vorschlag=Count("id", filter=Q(proposed_action__isnull=False)),
                  mit_dokument=Count("id", filter=Q(document__isnull=False)))
        .order_by("-n"))
print("Art/Unterart: Anzahl | mit Vorschlag | mit Dokument")
for r in rows:
    print(f"  {r['case_type']}/{r['case_subtype'] or '-'}: {r['n']} | {r['mit_vorschlag']} | {r['mit_dokument']}")
bt = offen.filter(case_subtype="below_threshold", document__isnull=False)
ki = bt.filter(document__status="review").exclude(document__review_cases__status=CaseStatus.RESOLVED).distinct().count()
print(f"KI-nachklassifizierbar (below_threshold, Dokument im Status review, kein erledigter Fall): {ki} von {bt.count()}")
st = Counter()
for ctx in bt.values_list("context", flat=True):
    st[(ctx or {}).get("stage3_status") or "nie aufgerufen"] += 1
print("Stufe-3-Status der below_threshold-Faelle: " + ", ".join(f"{k}={v}" for k, v in st.most_common()))
akt = Counter()
for pa in offen.filter(proposed_action__isnull=False).values_list("proposed_action", flat=True):
    akt[(pa or {}).get("action") or "?"] += 1
print("Vorgeschlagene Aktionen: " + (", ".join(f"{k}={v}" for k, v in akt.most_common()) or "keine"))
je_obj = offen.values("object__object_number").annotate(n=Count("id")).order_by("-n")[:15]
print("Objekte mit den meisten offenen Faellen: " + ", ".join(f"{r['object__object_number']}={r['n']}" for r in je_obj))
PY
    ;;
  deploy-tests)
    # Deployment-Tests T2, T3, T11 und T14 (docs/betrieb/deployment-test.md), nur lesend
    dom="$(envval APP_DOMAIN)"
    echo "T2 Auf dem Host veroeffentlichte Ports der Anwendungscontainer (erwartet: keine; EXPOSE ohne Veroeffentlichung zaehlt nicht):"
    docker ps --format '  {{.Names}} {{.Ports}}' | grep '^  objektakte-' | grep -E '0\.0\.0\.0|:::' || echo "  keine"
    echo "  Veroeffentlichte Ports aller uebrigen Container (fremde Anwendungen, Traefik im Host-Netz erscheint nicht):"
    docker ps --format '  {{.Names}} {{.Ports}}' | grep -v '^  objektakte-' | grep -E '0\.0\.0\.0|:::' || echo "  keine"
    echo "T3 Isolation des Netzes data (erwartet: BLOCKIERT):"
    docker compose exec -T db bash -c 'timeout 5 bash -c "exec 3<>/dev/tcp/1.1.1.1/443" 2>/dev/null && echo "  db: ERREICHBAR" || echo "  db: BLOCKIERT"'
    docker compose exec -T redis sh -c 'timeout 5 wget -q -T 5 -O /dev/null https://1.1.1.1 2>/dev/null && echo "  redis: ERREICHBAR" || echo "  redis: BLOCKIERT"'
    echo "T11 Logs (erwartet: 0 IBAN-Treffer, Anwendungsmeldungen als JSON):"
    n=$(docker compose logs --no-color --tail 2000 web worker worker-nlp worker-io beat 2>/dev/null | grep -cE 'DE[0-9]{2}[0-9 ]{18,}' || true)
    echo "  IBAN-Treffer: $n"
    j=$(docker compose logs --no-color --tail 2000 worker 2>/dev/null | grep -c '| {"ts"' || true)
    echo "  JSON-Zeilen im Worker-Log (Stichprobe): $j"
    echo "T14 Healthcheck-Endpunkte:"
    curl -s -o /dev/null -w '  /healthz/ %{http_code} (erwartet 200)\n' --max-time 10 "https://${dom}/healthz/"
    echo "  /healthz/ Inhalt: $(curl -s --max-time 10 "https://${dom}/healthz/")"
    curl -s -o /dev/null -w '  /readyz/ ohne Token %{http_code} (erwartet 401)\n' --max-time 10 "https://${dom}/readyz/"
    ;;
  doc-status)
    # Dokumente je Objekt und Status, offene und fehlgeschlagene Jobs; keine Dateinamen, keine Personendaten
    docker compose exec -T web python manage.py shell <<'PY'
from django.db.models import Count, Q
from django.utils import timezone
from apps.documents.models import Document
from apps.objects.models import ManagedObject
from apps.pipeline.models import ProcessingJob, ProcessingRun
jetzt = timezone.now()
for obj in ManagedObject.objects.order_by("object_number_numeric"):
    rows = Document.objects.filter(object=obj, deleted_at__isnull=True).values("status").annotate(c=Count("id")).order_by("status")
    stat = ", ".join(f"{r['status']}={r['c']}" for r in rows) or "keine Dokumente"
    arch = " (archiviert)" if obj.deleted_at else ""
    drive = "Drive-Ordner zugeordnet" if obj.drive_root_folder_id else "kein Drive-Ordner"
    print(f"Objekt {obj.object_number}{arch}: {stat}; {drive}")
offen = ProcessingJob.objects.filter(status__in=["pending", "running"]).values("job_type", "status").annotate(c=Count("id"))
print("Jobs offen:", ", ".join(f"{r['job_type']}/{r['status']}={r['c']}" for r in offen) or "keine")
fehl = ProcessingJob.objects.filter(status="failed").values("job_type").annotate(c=Count("id"))
print("Jobs fehlgeschlagen:", ", ".join(f"{r['job_type']}={r['c']}" for r in fehl) or "keine")
rep = ProcessingJob.objects.filter(idempotency_key__contains="#").values("job_type", "status").annotate(c=Count("id")).order_by("job_type", "status")
print("Wiederholungsjobs (#n):", ", ".join(f"{r['job_type']}/{r['status']}={r['c']}" for r in rep) or "keine")
for t in ProcessingJob.objects.values("document_id", "object__object_number", "job_type").annotate(c=Count("id")).filter(c__gt=3).order_by("-c")[:8]:
    stati = ", ".join(f"{r['status']}={r['c']}" for r in ProcessingJob.objects.filter(document_id=t["document_id"], job_type=t["job_type"]).values("status").annotate(c=Count("id")).order_by("status"))
    print(f"Mehrfachjobs Dokument {t['document_id']} (Objekt {t['object__object_number']}) {t['job_type']}: {t['c']} Jobs ({stati})")
for j in ProcessingJob.objects.filter(status="failed").order_by("-id")[:5]:
    print(f"  Fehler {j.job_type} Job {j.pk}: {(j.last_error or '')[:160]}")
for j in ProcessingJob.objects.filter(status="pending", last_error__startswith="wartet").order_by("-id")[:3]:
    print(f"  wartet {j.job_type} Job {j.pk}: {(j.last_error or '')[:120]}")
# Objektsperren der Ablage: wer haelt sie, und lebt der Halter noch? (Befund 20.09.2026: 124 wartende Ablagen)
from django.core.cache import cache
sperren = []
for oid in ProcessingJob.objects.filter(status="pending", job_type="file_to_drive").values_list("object_id", flat=True).distinct():
    halter = cache.get(f"drive:write:{oid}")
    if halter is None:
        continue
    j = ProcessingJob.objects.filter(pk=halter).first() if str(halter).isdigit() else None
    info = f"Job {j.pk} {j.job_type} {j.status}, zuletzt {j.updated_at:%d.%m. %H:%M:%S}" if j else f"Halter {halter!r}"
    sperren.append(f"  Sperre Objekt {oid}: {info}")
print("Drive-Schreibsperren bei wartenden Ablagen:", len(sperren) or "keine")
for zeile in sperren[:10]:
    print(zeile)
laeufe = ProcessingRun.objects.values("status").annotate(c=Count("id"))
print("Laeufe:", ", ".join(f"{r['status']}={r['c']}" for r in laeufe) or "keine")
for run in ProcessingRun.objects.filter(status="running").select_related("object").order_by("id"):
    offen = ProcessingJob.objects.filter(object=run.object, status="pending")
    print(f"Lauf {run.pk} Objekt {run.object.object_number} laeuft seit {run.started_at:%d.%m. %H:%M}: Dokumente {run.documents_total}, offene Jobs {offen.count()} (am Lauf {offen.filter(run=run).count()}, ohne Lauf {offen.filter(run__isnull=True).count()}, an anderem Lauf {offen.exclude(run=run).exclude(run__isnull=True).count()}, nie versandt {offen.filter(dispatched_at__isnull=True).count()}), laufende Jobs {ProcessingJob.objects.filter(object=run.object, status='running').count()}")
    # nie versandte Jobs des Laufs: Art, Anteil Wiederholungsjobs, Faelligkeit und drei Beispiele (Herkunft klaeren)
    nie = offen.filter(dispatched_at__isnull=True)
    if nie.exists():
        arten = ", ".join(f"{r['job_type']}={r['c']}" for r in nie.values("job_type").annotate(c=Count("id")).order_by("-c"))
        faellig = nie.filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=jetzt)).count()
        print(f"  nie versandt je Art: {arten}; Wiederholungsjobs {nie.filter(idempotency_key__contains='#').count()}, faellig {faellig}, juengster angelegt {nie.order_by('-id').values_list('created_at', flat=True).first():%d.%m. %H:%M:%S}")
        for j in nie.order_by("-id")[:3]:
            basis = "-"
            if "#" in j.idempotency_key:
                b = ProcessingJob.objects.filter(idempotency_key=j.idempotency_key.split("#", 1)[0]).values_list("status", flat=True).first()
                basis = f"#{j.idempotency_key.split('#', 1)[1]} (Basisjob {b})"
            dok = j.document.status if j.document_id else "-"
            print(f"    Job {j.pk} {j.job_type} {basis} angelegt {j.created_at:%d.%m. %H:%M:%S} Versuch {j.attempt_count} naechster {j.next_attempt_at} Dokument {dok} Fehler {(j.last_error or '')[:100]!r}")
# Ordnerabgleiche je Objekt (letzte drei), Struktur in drive_nodes, offene Faelle nach Art; ohne Dateinamen
from apps.drive.models import DriveNode, DriveSyncRun
from apps.review.models import ReviewCase
for obj in ManagedObject.objects.filter(deleted_at__isnull=True).order_by("object_number_numeric"):
    knoten = DriveNode.objects.filter(object=obj, status="active").values("node_kind").annotate(c=Count("id"))
    print(f"Objekt {obj.object_number}: Drive-Wurzel {'ja' if obj.drive_root_folder_id else 'nein'}; Knoten "
          + (", ".join(f"{k['node_kind']}={k['c']}" for k in knoten) or "keine"))
    for run in DriveSyncRun.objects.filter(object=obj).order_by("-id")[:3]:
        s = run.summary or {}
        print(f"  Abgleich {run.pk} {run.status} dry_run={run.dry_run} trigger={s.get('trigger')} "
              f"aktionen={run.actions_executed} fehler={(run.error_message or '')[:160]!r} "
              f"hinweise={[h[:100] for h in (s.get('hints') or [])][:4]} blockiert={s.get('blocked_categories')}")
    faelle = ReviewCase.objects.filter(object=obj, status__in=["open", "in_progress"]).values("case_type", "case_subtype").annotate(c=Count("id")).order_by("-c")[:8]
    print("  offene Faelle:", ", ".join(f"{f['case_type']}/{f['case_subtype']}={f['c']}" for f in faelle) or "keine")
wartend = ProcessingJob.objects.filter(status="pending", last_error__startswith="wartet").values("job_type", "last_error").annotate(c=Count("id"))
for w in wartend:
    print(f"wartend {w['job_type']} x{w['c']}: {w['last_error'][:160]}")
PY
    # Mit Objektnummer als Argument: je Dokument Klassifikation, Stufen, Entitaeten (nur Zaehler) und Faelle.
    # Keine Dateinamen, keine Personennamen (Protokoll liegt bei GitHub).
    if [ -n "${ARG:-}" ]; then
      docker compose exec -T -e OBJ_NR="$ARG" web python manage.py shell <<'PY'
import os
from collections import Counter
from apps.documents.models import Document, DocumentClassification, DocumentEntity
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import OwnerFile, Tenant, TenantFile, TenantUnitAssignment
from apps.review.models import ReviewCase
nr = os.environ["OBJ_NR"]
obj = ManagedObject.objects.filter(object_number=nr).first()
if obj is None and nr.isdigit():
    obj = ManagedObject.objects.filter(object_number_numeric=int(nr)).first()
if obj is None:
    print(f"Objekt {nr} nicht gefunden")
else:
    print(f"Objekt {obj.object_number}: {obj.management_type}, Soll {obj.expected_unit_count}, Einheiten {Unit.active.filter(object=obj).count()}, "
          f"Mieterzuordnungen {TenantUnitAssignment.active.filter(unit__object=obj).count()}, "
          f"Eigentuemerakten {OwnerFile.active.filter(object=obj).count()} ({', '.join(sorted(set(OwnerFile.active.filter(object=obj).values_list('file_kind', flat=True))))}), "
          f"Mieterakten {TenantFile.active.filter(object=obj).count()}")
    for d in Document.objects.filter(object=obj, deleted_at__isnull=True).select_related("subfolder", "document_type").order_by("id"):
        final = DocumentClassification.objects.filter(document=d, is_final=True).order_by("-id").first()
        ents = Counter(DocumentEntity.objects.filter(document=d).values_list("entity_type", flat=True))
        matched = DocumentEntity.objects.filter(document=d, matched_tenant_id__isnull=False).count()
        cases = list(ReviewCase.objects.filter(document=d).values_list("case_type", "case_subtype", "status"))
        ext = (d.current_name or "").rsplit(".", 1)[-1].lower()[:5]
        sub = d.subfolder.code if d.subfolder else "-"
        typ = d.document_type.code if d.document_type else "-"
        print(f"  Dok {d.pk} .{ext} S{d.page_count or 0} {d.status} {d.category_id or '-'}/{sub} {typ} "
              f"conf={d.final_confidence} stufe={final.stage if final else '-'} prov={final.provider if final else '-'} "
              f"ent={dict(ents)} mieter_treffer={matched} faelle={[f'{c[0]}/{c[1]}:{c[2]}' for c in cases]}")
PY
    fi
    ;;
  reconcile-all)
    # Ordnerabgleich aller aktiven Objekte (echter Lauf): legt fehlende Struktur an, etwa die 15 Unterordner der
    # Stammakte nach der Katalogerweiterung vom 12.09.2026. Ergebnis je Objekt im Statusbereich und in drive_sync_runs.
    # Laeuft im worker-io (Warteschlange io, /data/exports beschreibbar); der Web-Container ist schreibgeschuetzt.
    # Argument "ohne-ordner": nur Objekte ohne registrierten Objektordner (schnell, kein Durchlauf aller Baeume).
    if [ "${ARG:-}" = "ohne-ordner" ]; then
      docker compose exec -T worker-io python manage.py shell <<'PY'
from apps.drive.models import DriveNode, NodeKind, NodeStatus
from apps.drive.tasks import reconcile_object_task
from apps.objects.models import ManagedObject
mit = set(DriveNode.objects.filter(node_kind=NodeKind.OBJECT_ROOT, status=NodeStatus.ACTIVE).values_list("object_id", flat=True))
objs = [o for o in ManagedObject.active.filter(status__in=["new", "takeover", "active"]).order_by("object_number_numeric") if o.pk not in mit]
print(f"Objekte ohne Objektordner: {len(objs)}")
for o in objs:
    r = reconcile_object_task(o.pk, False, None, trigger="bulk")
    print(o.object_number, r.get("status"), r.get("error") or "")
PY
    else
      docker compose exec -T worker-io python manage.py shell <<'PY'
from apps.drive.tasks import reconcile_all_task
result = reconcile_all_task(dry_run=False)
print(result)
PY
    fi
    ;;
  config-set)
    # Konfigurationswert aus dem Katalog setzen, Argument schluessel=wert; der Wert wird als JSON gelesen
    # (true, false, 5, "text"), sonst als Zeichenkette. Validierung und Audit (setting.update) wie im Admin-Formular.
    case "${ARG:-}" in *=*) ;; *) echo "Argument schluessel=wert fehlt"; exit 2 ;; esac
    docker compose exec -T -e CFG_ARG="$ARG" web python manage.py shell <<'PY'
import json, os
from apps.config import store
key, _, raw = os.environ["CFG_ARG"].partition("=")
try:
    value = json.loads(raw)
except json.JSONDecodeError:
    import re
    # Woerter ausserhalb von Zeichenketten (z. B. EUR_EINGABE statt einer Zahl) sind stehen gebliebene Platzhalter
    platzhalter = sorted({
        w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r'"[^"]*"', '""', raw))
        if w not in ("true", "false", "null")
    })
    if raw[:1] in "{[" and '"' in raw and platzhalter:
        print(
            f"config-set abgelehnt: {raw!r} ist kein gueltiges JSON, weil Platzhalter stehen geblieben sind: "
            + ", ".join(platzhalter)
            + ". Diese durch Zahlen ersetzen (Dezimalpunkt, ohne Anfuehrungszeichen), zum Beispiel "
            + "\"input_per_1k\":0.0001"
        )
        raise SystemExit(2)
    if raw[:1] in "{[" or raw.count(":") and not raw.startswith('"'):
        # Typischer Fehler in der interaktiven Shell: Anfuehrungszeichen entfernt, Klammern expandiert
        print(
            f"config-set abgelehnt: {raw!r} ist kein gueltiges JSON. Vermutlich hat die Shell die "
            "Anfuehrungszeichen entfernt oder die Klammern expandiert; das ganze Argument in einfache "
            "Anfuehrungszeichen setzen, zum Beispiel: d config-set $B 'ai.monthly_budget_eur={\"openai\":1500}'"
        )
        raise SystemExit(2)
    value = raw
from django.core.exceptions import ValidationError
try:
    before = store.get(key)
    store.set(key, value, reason="Deploy-Workflow config-set")
except (store.UnknownSetting, ValidationError, ValueError) as exc:
    print(f"config-set abgelehnt: {exc}")
    raise SystemExit(2)
print(f"{key}: {before!r} -> {store.get(key)!r}")
if key == "ai.price_list":
    nullpreise = [
        f"{m} ({feld})"
        for m, p in ((store.get(key) or {}).get("models") or {}).items()
        for feld in ("input_per_1k", "output_per_1k")
        if not (p or {}).get(feld)
    ]
    if nullpreise:
        print(
            "WARNUNG: Preis 0 oder fehlend bei "
            + ", ".join(nullpreise)
            + ". Der Router blockiert Aufrufe dieses Modells weiterhin (budget_blocked), bis beide Preise "
            "groesser 0 sind (EUR je 1.000 Token, Dezimalpunkt)."
        )
PY
    ;;
  altbestand-import)
    # Quellordner der bisherigen Ablage in die Altbestand-Tabelle aufnehmen; idempotent, nur Folder-IDs und Namen
    docker compose exec -T web python manage.py altbestand_import --aufloesen
    ;;
  altbestand-aufarbeiten)
    # Alle Altbestand-Ordner mit Objekt aufarbeiten; Argument "echt" reiht die Sammelaufarbeitung als Celery-Aufgabe ein
    if [ "${ARG:-}" = "echt" ]; then
      docker compose exec -T web python manage.py altbestand_aufarbeiten --echt --hintergrund
    else
      docker compose exec -T web python manage.py altbestand_aufarbeiten
    fi
    ;;
  verarbeitung-alle)
    # Verarbeitungslaeufe fuer alle Objekte mit offener Arbeit; "echt" reiht ein, sonst Vorschau; "ohne=133,216"
    # nimmt Objekte aus (23.09.2026: Sammelpfade aus Paperless erst nach Sichtung); Argumente mit "+" verbinden
    args=()
    IFS='+' read -r -a teile <<<"${ARG:-}"
    for t in "${teile[@]}"; do
      case "$t" in
        "") ;;
        echt) args+=(--echt) ;;
        ohne=*) n="${t#ohne=}"; case "$n" in *[!0-9,]*|"") echo "ohne braucht Objektnummern, durch Komma getrennt"; exit 2 ;; esac; args+=(--ohne "$n") ;;
        *) echo "Argument unbekannt: $t (erlaubt: echt, ohne=133,216)"; exit 2 ;;
      esac
    done
    docker compose exec -T web python manage.py verarbeitung_alle_starten "${args[@]}"
    ;;
  paperless-feld-alle)
    # Feld MHV Objekt fuer alle zugeordneten Paperless-Speicherpfade; Argument "echt" setzt und startet den Bestandslauf
    if [ "${ARG:-}" = "echt" ]; then
      docker compose exec -T web python manage.py paperless_feld_setzen --echt
    else
      docker compose exec -T web python manage.py paperless_feld_setzen
    fi
    ;;
  paperless-feld-abgleich)
    # Feld MHV Objekt in Paperless gegen die Zuordnung eines Objekts pruefen: "<objekt>" Vorschau, "<objekt>+echt"
    # uebernimmt Dokumente mit geleertem Feld in das Eingangsobjekt und mit anderer Nummer in dieses Objekt.
    obj=""; echt=""
    IFS='+' read -r -a teile <<<"${ARG:-}"
    for t in "${teile[@]}"; do
      case "$t" in
        "") ;;
        echt) echt="1" ;;
        *[!0-9]*) echo "Argument unbekannt: $t (erlaubt: Objektnummer, Objektnummer+echt)"; exit 2 ;;
        *) obj="$t" ;;
      esac
    done
    [ -n "$obj" ] || { echo "Objektnummer fehlt"; exit 2; }
    if [ -n "$echt" ]; then
      docker compose exec -T web python manage.py paperless_feld_abgleich "$obj" --echt
    else
      docker compose exec -T web python manage.py paperless_feld_abgleich "$obj"
    fi
    ;;
  zuordnung-pruefen)
    # Gegenprobe der Objektzuordnung: "<objekt>" beschraenkt, "echt" loest auf, "details" listet, "ohne-ki",
    # "limit=N" begrenzt die Aufloesungen je Lauf; Vorschau ruft keine KI
    args=()
    IFS='+' read -r -a teile <<<"${ARG:-}"
    for t in "${teile[@]}"; do
      case "$t" in
        "") ;;
        echt) args+=(--echt) ;;
        details) args+=(--details) ;;
        ohne-ki) args+=(--ohne-ki) ;;
        limit=*) n="${t#limit=}"; case "$n" in *[!0-9]*|"") echo "limit braucht eine Zahl"; exit 2 ;; esac; args+=(--limit "$n") ;;
        *[!0-9]*) echo "Argument unbekannt: $t (erlaubt: Objektnummer, echt, details, ohne-ki, limit=N)"; exit 2 ;;
        *) args+=(--objekt "$t") ;;
      esac
    done
    # Laeuft im worker-io: nur das Worker-Image enthaelt die Anbieterbibliothek der Stufe 3 (Befund 22.09.2026:
    # im Web-Container brach der Lauf mit fehlendem Modul ab); das Kommando haelt nach drei KI-Fehlversuchen an.
    docker compose exec -T worker-io python manage.py paperless_zuordnung_pruefen "${args[@]}"
    ;;
  backup-voll)
    # Volle Sicherung sofort im Backup-Container (Datenbank und Fachverzeichnisse, zuletzt 1.315 s); schreibt
    # status.json und macht den Healthcheck wieder gruen. Nur in tmux starten. Hintergrund 22.09.2026: der Cron-Lauf
    # blieb seit dem 10.09. aus, weil die Zeile in /etc/cron.d ohne Benutzerfeld geschrieben wurde.
    docker compose exec -T backup /usr/local/bin/backup.sh
    ;;
  objekt-anschriften)
    # Weitere Anschriften (Eckobjekte, mehrere Hausnummern) aus den Objektbezeichnungen ableiten; "echt" speichert.
    # "korrigieren" leitet Hauptanschriften, die ein frueherer Lauf aus der Bezeichnung gesetzt hat, mit der
    # aktuellen Erkennung neu ab (Vorschau), "korrigieren+echt" speichert die Korrekturen (22.09.2026).
    args=()
    IFS='+' read -r -a teile <<<"${ARG:-}"
    for t in "${teile[@]}"; do
      case "$t" in
        "") ;;
        echt) args+=(--echt) ;;
        korrigieren) args+=(--korrigieren) ;;
        *) echo "Argument unbekannt: $t (erlaubt: echt, korrigieren, korrigieren+echt)"; exit 2 ;;
      esac
    done
    docker compose exec -T web python manage.py objekt_anschriften_ergaenzen "${args[@]}"
    ;;
  altbestand-objekte)
    # Objekte fuer alle Altbestand-Quellen ohne Objekt anlegen; Argument "echt" legt an, sonst nur Vorschau
    if [ "${ARG:-}" = "echt" ]; then
      docker compose exec -T web python manage.py altbestand_objekte_anlegen --echt
    else
      docker compose exec -T web python manage.py altbestand_objekte_anlegen
    fi
    ;;
  jobs-bereinigen)
    # Ueberzaehlige wartende Wiederholungsjobs (#n) auf skipped setzen; Argument "echt" bereinigt, sonst Vorschau
    if [ "${ARG:-}" = "echt" ]; then
      docker compose exec -T web python manage.py jobs_bereinigen --echt
    else
      docker compose exec -T web python manage.py jobs_bereinigen
    fi
    ;;
  transit-bereinigen)
    # Transit-Kopien (Uploads, Paperless-Downloads) bereits in Drive abgelegter Dokumente loeschen; "echt" loescht, sonst Vorschau
    if [ "${ARG:-}" = "echt" ]; then
      docker compose exec -T web python manage.py transit_bereinigen --echt
    else
      docker compose exec -T web python manage.py transit_bereinigen
    fi
    ;;
  drive-dubletten)
    # Altkopien in Drive in den Papierkorb legen, die bei einer erneuten Ablage desselben Dokuments entstanden sind
    # (Nachklassifikation bis 23.09.2026: Zweitkopie hochgeladen, Altkopie blieb in 06/01). Quelle: Protokoll
    # drive.upload; jede Altkopie wird vor dem Papierkorb in Drive nachgelesen und ueber document_id oder sha256
    # bestaetigt. Nie endgueltig loeschen. Argumente mit + getrennt: echt, objekt=NR, limit=N. Ohne echt Vorschau.
    args=()
    IFS='+' read -r -a parts <<< "${ARG:-}"
    for p in "${parts[@]}"; do
      case "$p" in
        echt) args+=(--echt) ;;
        objekt=*) args+=(--objekt "${p#objekt=}") ;;
        limit=*) args+=(--limit "${p#limit=}") ;;
        '') ;;
        *) echo "Unbekanntes Argument: $p (erlaubt: echt, objekt=NR, limit=N)"; exit 2 ;;
      esac
    done
    docker compose exec -T web python manage.py drive_dubletten_bereinigen "${args[@]}"
    ;;
  worker-drosseln)
    # CPU-Last der laufenden Worker ohne Neustart senken. Argument: Kerne fuer den OCR-Worker, optional mit Anteil in
    # Prozent, z. B. "2" oder "2+50" (2 Kerne, je hoechstens 50 Prozent = Quote 1,0). Ablage- und Klassifikations-
    # Worker erhalten 1 Kern mal Anteil. Zuerst die harten Grenzen (docker update: --cpus als Zeitquote, cgroup,
    # sofort wirksam), dazu niedrige Prioritaet (--cpu-shares 128, andere Anwendungen gewinnen bei Konkurrenz),
    # danach die Poolgroesse ueber Celery pool_shrink/pool_grow (best effort). Dauerhaft ueber env-set
    # OCR_PROCESSES, WORKER_CPUS, WORKER_IO_CPUS, WORKER_NLP_CPUS (naechstes deploy).
    KERNE="${ARG%%+*}"; ANTEIL=100; case "$ARG" in *+*) ANTEIL="${ARG#*+}";; esac
    case "$KERNE" in ''|*[!0-9]*) echo "Argument Kerne (1 bis 8), optional +Anteil in Prozent, fehlt"; exit 2;; esac
    case "$ANTEIL" in ''|*[!0-9]*) echo "Anteil muss eine Zahl 10 bis 100 sein"; exit 2;; esac
    [ "$KERNE" -ge 1 ] && [ "$KERNE" -le 8 ] || { echo "Kerne ausserhalb 1 bis 8"; exit 2; }
    [ "$ANTEIL" -ge 10 ] && [ "$ANTEIL" -le 100 ] || { echo "Anteil ausserhalb 10 bis 100"; exit 2; }
    Q_OCR="$(awk -v k="$KERNE" -v a="$ANTEIL" 'BEGIN{printf "%.2f", k*a/100}')"
    Q_NEBEN="$(awk -v a="$ANTEIL" 'BEGIN{printf "%.2f", a/100}')"
    W_ID="$(docker compose ps -q worker)"; IO_ID="$(docker compose ps -q worker-io)"; NLP_ID="$(docker compose ps -q worker-nlp)"
    [ -n "$W_ID" ] || { echo "Worker-Container nicht gefunden"; exit 1; }
    docker update --cpus "$Q_OCR" --cpu-shares 128 "$W_ID" >/dev/null && echo "worker: Quote $Q_OCR Kerne ($KERNE Kerne zu $ANTEIL Prozent), Prioritaet niedrig"
    [ -n "$IO_ID" ] && docker update --cpus "$Q_NEBEN" --cpu-shares 128 "$IO_ID" >/dev/null && echo "worker-io: Quote $Q_NEBEN Kerne, Prioritaet niedrig"
    [ -n "$NLP_ID" ] && docker update --cpus "$Q_NEBEN" --cpu-shares 128 "$NLP_ID" >/dev/null && echo "worker-nlp: Quote $Q_NEBEN Kerne, Prioritaet niedrig"
    NAME="$(docker compose exec -T worker celery -A objektakte inspect ping -t 30 2>/dev/null | grep -oE 'worker-ocr@[^ :>]+' | head -1)"
    if [ -z "$NAME" ]; then
      echo "OCR-Worker antwortet nicht auf ping; Poolgroesse bleibt, die Grenzen gelten trotzdem"
    else
      AKTUELL="$(docker compose exec -T worker celery -A objektakte inspect stats -j -d "$NAME" -t 30 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(list(d.values())[0]["pool"]["max-concurrency"])' 2>/dev/null)"
      case "$AKTUELL" in ''|*[!0-9]*) echo "Poolgroesse nicht lesbar; Grenzen gelten trotzdem";;
        *) echo "OCR-Prozesse aktuell $AKTUELL, Ziel $KERNE ($NAME)"
           if [ "$KERNE" -lt "$AKTUELL" ]; then docker compose exec -T worker celery -A objektakte control pool_shrink "$((AKTUELL - KERNE))" -d "$NAME" -t 30 || true
           elif [ "$KERNE" -gt "$AKTUELL" ]; then docker compose exec -T worker celery -A objektakte control pool_grow "$((KERNE - AKTUELL))" -d "$NAME" -t 30 || true
           else echo "Poolgroesse unveraendert"; fi;;
      esac
    fi
    echo "$(date -Is) worker-drosseln Kerne $KERNE Anteil $ANTEIL (Quote $Q_OCR, Pool vorher ${AKTUELL:-unbekannt})" >> "$LOG"
    echo "Hinweis: dauerhaft ueber env-set OCR_PROCESSES=$KERNE+WORKER_CPUS=$Q_OCR+WORKER_IO_CPUS=$Q_NEBEN+WORKER_NLP_CPUS=$Q_NEBEN (wirksam mit deploy)"
    ;;
  worker-stop)
    # Verarbeitung sofort anhalten (15.09.2026, Server ueberlastet): OCR-, Klassifikations- und Ablage-Worker sowie Beat
    # stoppen; web, db, redis laufen weiter. Warteschlangen und Jobs bleiben erhalten (acks_late), laufende Jobs
    # werden nach dem Neustart wiederholt. Wiederanlauf mit worker-start.
    docker compose stop -t 20 worker worker-nlp worker-io beat
    docker compose ps --format "table {{.Service}}\t{{.State}}\t{{.Status}}"
    echo "$(date -Is) worker-stop" >> "$LOG"
    ;;
  worker-start)
    # Verarbeitung nach worker-stop wieder anlaufen lassen (dieselben Container, CPU-Grenzen aus worker-drosseln bleiben)
    docker compose start worker-io worker-nlp worker beat
    docker compose ps --format "table {{.Service}}\t{{.State}}\t{{.Status}}"
    echo "$(date -Is) worker-start" >> "$LOG"
    ;;
  last-status)
    # Momentaufnahme der Serverlast (nur lesend): Load, CPU je Container, gesetzte CPU-Grenzen, Top-Prozesse.
    # Zusaetzlich (15.09.2026): Kernzahl, CPU-Verteilung ueber 6 s inkl. Anteil des Hypervisors (st) und
    # CPU-Druck (PSI), weil ps nur Lebenszeit-Mittelwerte je Prozess zeigt und Load allein Verdraengung durch
    # den Wirt nicht sichtbar macht.
    uptime
    echo "Kerne sichtbar: $(nproc)"
    echo "--- CPU-Verteilung (vmstat, 3 Messungen je 2 s: us sy id wa st) ---"
    vmstat 2 3 2>/dev/null | tail -n 4
    echo "--- CPU-Druck (PSI, Anteil der Zeit mit wartenden Prozessen) ---"
    cat /proc/pressure/cpu 2>/dev/null || echo "nicht verfuegbar"
    echo "--- CPU und Speicher je Container (docker stats, eine Messung) ---"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null | sort -k2 -rh | head -20
    echo "--- gesetzte CPU-Grenzen (NanoCpus / 1e9) ---"
    for svc in worker worker-io worker-nlp web db; do
      ID="$(docker compose ps -q "$svc" 2>/dev/null)"; [ -n "$ID" ] && echo "$svc: $(docker inspect --format '{{.HostConfig.NanoCpus}}' "$ID" | awk '{printf "%.1f Kerne", $1/1e9}')"
    done
    echo "--- Top-Prozesse nach CPU (Host, Lebenszeit-Mittel laut ps) ---"
    ps -eo pcpu,pmem,comm --sort=-pcpu 2>/dev/null | awk '$3!="ps" && $3!="head" && $3!="top" && $3!="awk" {if (++c<=12) print}'
    echo "--- Top-Prozesse nach CPU (Host, aktuell ueber 3 s laut top) ---"
    # Begrenzung in awk statt head: head schliesst die Pipe vorzeitig, awk erhaelt SIGPIPE (Exit 141 bei pipefail)
    top -b -n 2 -d 3 -o %CPU 2>/dev/null | awk '/^top -/{n++} n==2{c++} n==2 && c<=20'
    echo "--- Zombie-Prozesse je Elternprozess (Top 5, mit Container) ---"
    ps -eo ppid,stat,comm 2>/dev/null | awk '$2 ~ /^Z/ {z[$1]++} END {for (p in z) print z[p], p}' | sort -rn | awk 'NR<=5' | while read -r n p; do
      cid="$(grep -oE 'docker-[0-9a-f]{64}' "/proc/$p/cgroup" 2>/dev/null | head -1 | cut -c8-19)"
      name="$(docker ps --format '{{.ID}} {{.Names}}' 2>/dev/null | awk -v c="$cid" '$1==c{print $2}')"
      echo "$n Zombies unter PID $p ($(ps -o comm= -p "$p" 2>/dev/null)) Container: ${name:-keiner}"
    done
    ;;
  work-bereinigen)
    # Arbeitsverzeichnisse unter work/ in einem Durchgang raeumen (15.09.2026, Platte voll): alles aelter als eine
    # Stunde, dessen Dokument nicht mehr hashed ist und keinen offenen Job hat; ein DB-Abgleich statt zwei Abfragen je
    # Verzeichnis wie im Minuten-Sweep. Laeuft im Worker-Container (work/ beschreibbar). "echt" loescht, sonst Vorschau.
    WB=0; [ "${ARG:-}" = "echt" ] && WB=1
    docker compose exec -T -e WB_ECHT="$WB" worker python manage.py shell <<'PY'
import os, shutil, time
from apps.documents.models import Document
from apps.pipeline import storage
from apps.pipeline.models import JobStatus, ProcessingJob
echt = os.environ.get("WB_ECHT") == "1"
root = storage.data_dir() / "work"
behalten = set(Document.objects.filter(status="hashed").exclude(sha256__isnull=True).values_list("sha256", flat=True))
behalten |= set(
    ProcessingJob.objects.filter(status__in=[JobStatus.PENDING, JobStatus.RUNNING], document__sha256__isnull=False)
    .values_list("document__sha256", flat=True).distinct()
)
frei_vorher = shutil.disk_usage(root).free / 1024**3
cutoff = time.time() - 3600
loeschbar = jung = benoetigt = geloescht = 0
for p in root.iterdir():
    if not p.is_dir():
        continue
    try:
        mtime = p.stat().st_mtime
    except OSError:
        continue
    if mtime > cutoff:
        jung += 1
        continue
    if not p.name.startswith("tmp-") and p.name in behalten:
        benoetigt += 1
        continue
    loeschbar += 1
    if echt:
        shutil.rmtree(p, ignore_errors=True)
        geloescht += 1
frei_nachher = shutil.disk_usage(root).free / 1024**3
print(f"work/: loeschbar {loeschbar}, juenger als 1 h {jung}, noch benoetigt (hashed oder offener Job) {benoetigt}, behalten-Menge {len(behalten)}")
print(f"{'geloescht' if echt else 'Vorschau, nichts geloescht'}: {geloescht}; frei vorher {frei_vorher:.1f} GB, nachher {frei_nachher:.1f} GB")
PY
    ;;
  redis-status)
    # Redis-Speicher und Warteschlangen (Broker und Cache teilen sich eine Instanz); Passwort nur ueber Umgebungsvariable
    docker compose exec -T redis sh -c '
      export REDISCLI_AUTH="$(cat /run/secrets/redis_password)"
      echo "== Speicher"
      redis-cli INFO memory | tr -d "\r" | grep -E "^(used_memory_human|used_memory_peak_human|used_memory_dataset|maxmemory_human|maxmemory_policy|mem_fragmentation_ratio):"
      echo "== Schluessel gesamt: $(redis-cli DBSIZE)"
      echo "== Warteschlangen (Nachrichten)"
      for q in control ocr classify io ai lists celery; do echo "  $q: $(redis-cli LLEN "$q")"; done
      echo "== unbestaetigte Nachrichten: $(redis-cli HLEN unacked) (Index $(redis-cli ZCARD unacked_index))"
      echo "== Cache-Schluessel (Django): $(redis-cli --scan --pattern ":1:*" | wc -l)"
      echo "== groesste Schluessel"
      redis-cli --bigkeys 2>/dev/null | grep -E "^(Biggest|\[|[0-9]+ [a-z]+ with)" | head -20
    '
    ;;
  disk-status)
    # Plattenbelegung der Datenverzeichnisse (nur lesend, keine Dateinamen). Die Datenverzeichnisse gehoeren dem
    # Container-Nutzer (uid 10001) und sind fuer den Deploy-Nutzer nicht lesbar, daher misst der Worker-Container
    # (mountet work, ocr-cache, transit) und der Web-Container (previews); df und docker system df vom Host.
    df -h /srv 2>/dev/null || df -h / || true
    echo "--- Datenverzeichnisse (aus dem Worker-Container) ---"
    docker compose exec -T worker sh -c '
      for d in work ocr-cache transit previews; do [ -d /data/$d ] && du -sh /data/$d 2>/dev/null; done
      W=/data/work
      echo "work/: Verzeichnisse $(find $W -mindepth 1 -maxdepth 1 -type d | wc -l), davon tmp- $(find $W -mindepth 1 -maxdepth 1 -type d -name "tmp-*" | wc -l), aelter als 48 h $(find $W -mindepth 1 -maxdepth 1 -type d -mmin +2880 | wc -l), aelter als 6 h $(find $W -mindepth 1 -maxdepth 1 -type d -mmin +360 | wc -l)"
      echo "transit/: Dateien $(find /data/transit -type f | wc -l)"
      echo "ocr-cache/: Verzeichnisse $(find /data/ocr-cache -mindepth 1 -maxdepth 1 -type d | wc -l)"
    ' 2>/dev/null || echo "Worker-Container nicht erreichbar"
    echo "--- Vorschaubilder (aus dem Web-Container) ---"
    docker compose exec -T web sh -c 'du -sh /data/previews 2>/dev/null; echo "previews/: Dokumente $(find /data/previews -mindepth 1 -maxdepth 1 -type d | wc -l), Bilder $(find /data/previews -type f -name "*.jpg" | wc -l)"' 2>/dev/null || echo "Web-Container nicht erreichbar"
    echo "--- Docker: Images, Container, Volumes, Build-Cache (ganzer Host) ---"
    docker system df 2>/dev/null || true
    echo "--- Docker-Volumes nach Groesse (ganzer Host, nur Namen) ---"
    docker system df -v 2>/dev/null | awk "/^VOLUME NAME/{f=1;next} f&&NF==0{f=0} f{print \$1, \$NF}" | sort -k2 -rh | head -12 || true
    ;;
  fehler-wiederaufnehmen)
    # Dokumente im Status Fehler gezielt wieder in die Kette nehmen, auch jenseits der drei automatischen
    # Wiederaufnahmen (nach einer Fehlerkorrektur im Code). Argumente mit + getrennt: objekt=503,
    # klasse=DoesNotExist,OperationalError (Fehlerklasse des letzten fehlgeschlagenen Jobs), echt. Ohne echt
    # Vorschau. Danach die Verarbeitung starten: verarbeitung-alle echt.
    args=()
    IFS='+' read -r -a parts <<< "${ARG:-}"
    for p in "${parts[@]}"; do
      case "$p" in
        echt) args+=(--echt) ;;
        objekt=*) args+=(--objekt "${p#objekt=}") ;;
        klasse=*) args+=(--fehlerklasse "${p#klasse=}") ;;
        '') ;;
        *) echo "Unbekanntes Argument: $p (erlaubt: objekt=NR, klasse=A,B, echt)"; exit 2 ;;
      esac
    done
    docker compose exec -T web python manage.py fehler_wiederaufnehmen "${args[@]}"
    ;;
  lauf-monitor)
    # Fortschritt der Verarbeitung live (nur lesend): alle N Sekunden (Argument, Standard 10, mindestens 5) eine Zeile
    # mit Laeufen nach Status, offenen Jobs nach Art, Fortschritt in Prozent, Durchsatz und Hochrechnung des Endes aus
    # der Abnahme der offenen Jobs seit Monitorstart; einmal je Minute die Dokumente der betroffenen Objekte nach
    # Status. Vorab die fehlgeschlagenen Jobs der Laeufe der letzten 24 h nach Art und Fehlerklasse (Meldungen ohne
    # Dateinamen). Endet von selbst, wenn nichts mehr offen ist; Strg+C beendet nur den Monitor, nie die Verarbeitung.
    SEK="${ARG:-10}"
    case "$SEK" in ''|*[!0-9]*) echo "Intervall muss aus Ziffern bestehen (Sekunden)"; exit 2 ;; esac
    docker compose exec -T -e MON_SEK="$SEK" web python manage.py shell <<'PY'
import os, re, time
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.db.models import Count
from django.utils import timezone
from apps.documents.models import Document
from apps.pipeline.models import JobStatus, ProcessingJob, ProcessingRun, RunStatus

TZ = ZoneInfo("Europe/Berlin")
sek = max(5, int(os.environ.get("MON_SEK") or 10))
max_ticks = int(os.environ.get("MON_TICKS") or 0)  # 0 = unbegrenzt (nur fuer Tests gesetzt)
OFFEN = (JobStatus.PENDING, JobStatus.RUNNING)
ERLEDIGT = (JobStatus.DONE, JobStatus.FAILED, JobStatus.SKIPPED)
LAUF_TXT = {"pending": "wartet", "running": "laeuft", "done": "fertig", "failed": "fehlgeschlagen", "aborted": "abgebrochen"}
DOK_TXT = {
    "registered": "registriert", "hashed": "Hash bekannt", "ocr_done": "Text erkannt",
    "classified": "klassifiziert (Ablage offen)", "filed": "abgelegt", "review": "in Pruefung",
    "duplicate": "Dubletten", "moved_out": "umgehaengt", "error": "Fehler",
}


def mask(text):
    text = re.sub(r"\S+\.(pdf|PDF|docx?|xlsx?|jpe?g|png|tiff?|msg|eml|zip)\b", "[DATEI]", text or "")
    return re.sub(r"\b[0-9a-f]{20,}\b", "[ID]", text)[:70]


def uhr(dt):
    return dt.astimezone(TZ).strftime("%H:%M:%S")


def tsd(n):
    return f"{n:,}".replace(",", ".")


seit = timezone.now() - timedelta(hours=24)
runs = ProcessingRun.objects.filter(created_at__gte=seit, dry_run=False)
run_ids = list(runs.values_list("id", flat=True))
obj_ids = list(runs.values_list("object_id", flat=True).distinct())
print(f"Laufmonitor, Intervall {sek} s, Laeufe der letzten 24 h: {len(run_ids)} in {len(obj_ids)} Objekten "
      "(Strg+C beendet nur den Monitor, die Verarbeitung laeuft weiter)", flush=True)
if not run_ids:
    print("Keine Laeufe in den letzten 24 h, nichts zu beobachten.")
    raise SystemExit
jobs = ProcessingJob.objects.filter(run_id__in=run_ids)
fails = Counter()
for j in jobs.filter(status=JobStatus.FAILED).only("job_type", "error_class", "last_error").iterator():
    letzte = (j.last_error or "").strip().splitlines()
    fails[(j.job_type, j.error_class or "-", mask(letzte[-1] if letzte else ""))] += 1
if fails:
    print(f"--- fehlgeschlagene Jobs dieser Laeufe: {sum(fails.values())} (Art, Fehlerklasse, Meldung ohne Dateinamen) ---")
    for (art, kl, msg), n in fails.most_common(8):
        print(f"  {n:5d}  {art:18s} {kl:30s} {msg}")
print("--- Verlauf (Zeit | Laeufe | Jobs | Fortschritt | Durchsatz | Hochrechnung) ---", flush=True)


def dokumente():
    c = Counter()
    for r in Document.objects.filter(object_id__in=obj_ids, deleted_at__isnull=True).values("status").annotate(c=Count("id")):
        c[r["status"]] = r["c"]
    teile = [f"{DOK_TXT.get(k, k)} {tsd(v)}" for k, v in c.most_common()]
    return "    Dokumente der Objekte: " + (", ".join(teile) or "keine")


start = timezone.now()
offen_start = None
tick = 0
try:
    while True:
        jetzt = timezone.now()
        laeufe = Counter({r["status"]: r["c"] for r in runs.values("status").annotate(c=Count("id"))})
        st = Counter({r["status"]: r["c"] for r in jobs.values("status").annotate(c=Count("id"))})
        je_art = {r["job_type"]: r["c"] for r in jobs.filter(status__in=OFFEN).values("job_type").annotate(c=Count("id"))}
        offen = sum(je_art.values())
        erledigt = sum(st.get(s, 0) for s in ERLEDIGT)
        gesamt = offen + erledigt
        if offen_start is None:
            offen_start = offen
        minuten = (jetzt - start).total_seconds() / 60
        fertig_seit_start = jobs.filter(status__in=ERLEDIGT, finished_at__gte=start).count()
        durchsatz = fertig_seit_start / minuten if minuten >= 0.5 else None
        abnahme = (offen_start - offen) / minuten if minuten >= 1 else None
        if offen == 0:
            prognose = "nichts mehr offen"
        elif abnahme is None:
            prognose = "Hochrechnung ab 1 min"
        elif abnahme <= 0:
            prognose = "keine Abnahme messbar (neue Jobs kommen nach)"
        else:
            ende = jetzt + timedelta(minutes=offen / abnahme)
            prognose = f"fertig ca. {uhr(ende)} (Abnahme {abnahme:.1f} Jobs/min)"
        lauf_txt = " ".join(f"{LAUF_TXT.get(k, k)} {v}" for k, v in sorted(laeufe.items()))
        arten = ", ".join(f"{k} {tsd(v)}" for k, v in sorted(je_art.items(), key=lambda kv: -kv[1])[:4])
        prozent = f"{100 * erledigt / gesamt:.1f} %".replace(".", ",") if gesamt else "-"
        ds = f"{durchsatz:.1f} Jobs/min" if durchsatz is not None else "Durchsatz ab 30 s"
        print(f"{uhr(jetzt)} | Laeufe: {lauf_txt} | Jobs offen {tsd(offen)}" + (f" ({arten})" if arten else "")
              + f", erledigt {tsd(erledigt)}, davon fehlgeschlagen {tsd(st.get('failed', 0))} | {prozent} | {ds} | {prognose}",
              flush=True)
        if tick % 6 == 0:
            print(dokumente(), flush=True)
        tick += 1
        if offen == 0 and not (laeufe.get("pending") or laeufe.get("running")):
            print("Nichts mehr offen, Monitor beendet.", flush=True)
            break
        if max_ticks and tick >= max_ticks:
            break
        time.sleep(sek)
except (KeyboardInterrupt, BrokenPipeError):
    pass
PY
    ;;
  env-set)
    # Betriebswerte in .env setzen; mehrere Paare mit + getrennt (OCR_PROCESSES=6+IO_CONCURRENCY=12). Nur freigegebene
    # Schluessel (Ressourcen, Parallelitaet), Wert aus Ziffern, Buchstaben, Punkt, Unterstrich und Bindestrich.
    # Kommentar hinter dem Wert bleibt erhalten. Wirksam erst mit deploy. Sicherung .env.bak vor der ersten Aenderung.
    case "${ARG:-}" in *=*) ;; *) echo "Argument SCHLUESSEL=wert fehlt"; exit 2 ;; esac
    cp .env .env.bak && chmod 600 .env.bak
    IFS='+' read -r -a PAARE <<<"$ARG"
    for PAAR in "${PAARE[@]}"; do
      KEY="${PAAR%%=*}"; VAL="${PAAR#*=}"
      case "$KEY" in
        REDIS_MEM|REDIS_MAXMEMORY|REDIS_CPUS|OCR_PROCESSES|IO_CONCURRENCY|NLP_CONCURRENCY|GUNICORN_TIMEOUT|GUNICORN_WORKERS| \
        WORKER_MEM|WORKER_CPUS|WORKER_NLP_MEM|WORKER_NLP_CPUS|WORKER_IO_MEM|WORKER_IO_CPUS|WEB_MEM|WEB_CPUS|DB_MEM|DB_CPUS| \
        DB_MAX_CONNECTIONS|DB_INNODB_BUFFER_POOL|JOBS_VISIBILITY_TIMEOUT_S|LOG_MAX_SIZE|LOG_MAX_FILE) ;;
        *) echo "Schluessel $KEY ist fuer env-set nicht freigegeben"; exit 2 ;;
      esac
      case "$VAL" in ""|*[!A-Za-z0-9._-]*) echo "unzulaessiger Wert fuer $KEY"; exit 2 ;; esac
      grep -qE "^$KEY=" .env || { echo "$KEY fehlt in .env"; exit 2; }
      OLD="$(envval "$KEY")"
      sed -i -E "s|^($KEY=)[^ #]*|\1$VAL|" .env
      NEU="$(envval "$KEY")"
      [ "$NEU" = "$VAL" ] || { echo "Schreiben fehlgeschlagen ($KEY=$NEU)"; exit 1; }
      echo "$KEY: ${OLD:-leer} -> $NEU"
      echo "$(date -Is) env-set $KEY ${OLD:-leer} -> $NEU" >> "$LOG"
    done
    echo "wirksam mit der Aktion deploy; Sicherung .env.bak"
    ;;
  *) echo "Nur check, pull, befund, env-init, ps, logs, smoke, cert-retry, db-status, db-reset, first-run, deploy, rollback, create-admin, oauth-check, deploy-tests, doc-status, config-set, altbestand-import, altbestand-objekte, altbestand-aufarbeiten, verarbeitung-alle, paperless-feld-alle, redis-status, env-set, jobs-bereinigen, disk-status, lauf-monitor, fehler-wiederaufnehmen, transit-bereinigen, drive-dubletten, worker-drosseln, work-bereinigen, last-status, worker-stop, worker-start, ai-check, ai-reclassify, review-status, sync-status, sync-retry, classifier-status, zuordnung-pruefen, objekt-anschriften, paperless-feld-abgleich, backup-voll oder reconcile-all erlaubt"; exit 2 ;;
esac
