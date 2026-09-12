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
#   deploy-tests <branch>     Deployment-Tests T2, T3, T11, T14 (nur lesend)
#   doc-status <branch>       Dokumente je Objekt und Status, offene und fehlgeschlagene Jobs (nur lesend)
#   config-set <branch> <schluessel=wert>  Konfigurationswert setzen (Wert als JSON: true, 5, "text"); Audit
#   altbestand-import <branch>  Quellordner aus db/seeds/altbestand_ordner.txt in die Altbestand-Tabelle
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
case "${ARG:-}" in *[!A-Za-z0-9@._+=-]*) echo "Argument unzulaessig"; exit 2 ;; esac
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
from django.db.models import Count
from apps.documents.models import Document
from apps.objects.models import ManagedObject
from apps.pipeline.models import ProcessingJob, ProcessingRun
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
for j in ProcessingJob.objects.filter(status="failed").order_by("-id")[:5]:
    print(f"  Fehler {j.job_type} Job {j.pk}: {(j.last_error or '')[:160]}")
for j in ProcessingJob.objects.filter(status="pending", last_error__startswith="wartet").order_by("-id")[:3]:
    print(f"  wartet {j.job_type} Job {j.pk}: {(j.last_error or '')[:120]}")
laeufe = ProcessingRun.objects.values("status").annotate(c=Count("id"))
print("Laeufe:", ", ".join(f"{r['status']}={r['c']}" for r in laeufe) or "keine")
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
    docker compose exec -T web python manage.py shell <<'PY'
from apps.drive.tasks import reconcile_all_task
result = reconcile_all_task(dry_run=False)
print({k: v for k, v in result.items() if k != "summary_file"})
PY
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
    value = raw
from django.core.exceptions import ValidationError
try:
    before = store.get(key)
    store.set(key, value, reason="Deploy-Workflow config-set")
except (store.UnknownSetting, ValidationError, ValueError) as exc:
    print(f"config-set abgelehnt: {exc}")
    raise SystemExit(2)
print(f"{key}: {before!r} -> {store.get(key)!r}")
PY
    ;;
  altbestand-import)
    # Quellordner der bisherigen Ablage in die Altbestand-Tabelle aufnehmen; idempotent, nur Folder-IDs und Namen
    docker compose exec -T web python manage.py altbestand_import --aufloesen
    ;;
  *) echo "Nur check, pull, befund, env-init, ps, logs, smoke, cert-retry, db-status, db-reset, first-run, deploy, rollback, create-admin, oauth-check, deploy-tests, doc-status, config-set oder altbestand-import erlaubt"; exit 2 ;;
esac
