#!/usr/bin/env bash
# Serverbefund fuer Meilenstein M0 (docs/betrieb.md Abschnitt 1, docs/umsetzungsplan.md 2.1 Schritte 2 bis 5).
#
# Liest ausschliesslich. Aendert nichts am Server. Kopiert keine Geheimnisse: Umgebungsvariablen
# werden nur mit Namen ausgegeben, acme.json wird nicht gelesen, Werte hinter password/secret/token
# werden geschwaerzt.
#
# Aufruf auf dem VPS als Deploy-Nutzer:
#   bash scripts/measure_server.sh [ausgabedatei]
# Standardausgabe: serverbefund-<datum>.md im aktuellen Verzeichnis. Die gepruefte Fassung wird
# ohne Geheimnisse als docs/betrieb/serverbefund.md in das Repository uebernommen.

set -u
OUT="${1:-serverbefund-$(date +%Y-%m-%d).md}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if sudo -n true 2>/dev/null; then SUDO="sudo -n"; fi
fi

redact() {
  sed -E 's/((password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[A-Za-z0-9_.-]*[=:][ ]*)[^ ,"]+/\1[GESCHWAERZT]/Ig'
}

out() { printf '%s\n' "$*" >> "$TMP"; }

section() { out ""; out "## $1"; out ""; }

run() {
  # run "Titel" befehl args...  -> Ueberschrift, Befehl und Ausgabe in einem Codeblock
  local title="$1"; shift
  out "### $title"; out ""; out '```text'
  out "\$ $*"
  if output="$("$@" 2>&1)"; then
    printf '%s\n' "$output" | redact >> "$TMP"
  else
    printf '%s\n' "$output" | redact >> "$TMP"
    out "(Rueckgabewert $?: Befehl nicht verfuegbar oder ohne Recht)"
  fi
  out '```'; out ""
}

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ Kopf
out "# Serverbefund (Meilenstein M0)"
out ""
out "Erhoben am $(date '+%d.%m.%Y %H:%M %Z') auf $(hostname) durch $(id -un). Nur Lesezugriffe. Geheimnisse geschwaerzt oder nicht gelesen."
[ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ] && out "" && out "Hinweis: kein sudo ohne Passwort verfuegbar; Abschnitte, die Root-Rechte brauchen (sshd -T, ufw, daemon.json), sind unvollstaendig und werden manuell nachgetragen."

# ------------------------------------------------------------------ 1 Ressourcen
section "1. Ressourcen (docs/betrieb.md 1.1)"
run "Kerne" nproc
have lscpu && run "CPU-Modell" sh -c "lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Core|Socket'"
run "Arbeitsspeicher" free -h
run "Platten" df -h
run "Blockgeraete" lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
run "I/O-Scheduler" sh -c 'for b in /sys/block/*/queue/scheduler; do echo "$b: $(cat "$b")"; done'
have lsb_release && run "Betriebssystem" lsb_release -a
run "Kernel" uname -r
run "Zeit und Zeitzone" timedatectl
run "Laufzeit" uptime

# ------------------------------------------------------------------ 2 Docker
section "2. Docker und Compose (docs/betrieb.md 1.2)"
if have docker; then
  run "Docker-Version" docker version
  run "Compose-Plugin" docker compose version
  run "Docker-Info" docker info --format '{{.ServerVersion}} | Storage: {{.Driver}} | Cgroup: {{.CgroupDriver}} v{{.CgroupVersion}} | LogDriver: {{.LoggingDriver}}'
  run "daemon.json" sh -c "$SUDO cat /etc/docker/daemon.json 2>/dev/null || echo 'keine daemon.json'"
  run "Laufende Container" docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  run "Netzwerke" docker network ls
  run "Volumes" docker volume ls
  run "Speicherbelegung Docker" docker system df
else
  out "Docker nicht gefunden oder fuer diesen Nutzer nicht nutzbar (Gruppe docker pruefen)."
fi

# ------------------------------------------------------------------ 3 Traefik
section "3. Traefik (docs/betrieb.md 1.3)"
TRAEFIK=""
if have docker; then
  TRAEFIK="$(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -i traefik | head -1 | cut -f1)"
fi
if [ -n "$TRAEFIK" ]; then
  out "Traefik-Container: \`$TRAEFIK\`"
  out ""
  run "Image" docker inspect "$TRAEFIK" --format '{{.Config.Image}}'
  run "Netzwerke des Traefik-Containers (Kandidaten fuer TRAEFIK_NETWORK)" docker inspect "$TRAEFIK" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'
  run "Kommandozeile (statische Konfiguration als Flags)" sh -c "docker inspect '$TRAEFIK' --format '{{json .Config.Cmd}}' | tr ',' '\n'"
  run "Entrypoint" docker inspect "$TRAEFIK" --format '{{json .Config.Entrypoint}}'
  run "Umgebungsvariablen (nur Namen)" sh -c "docker inspect '$TRAEFIK' --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -i '^TRAEFIK_' | sed 's/=.*//'"
  run "Labels des Traefik-Containers" sh -c "docker inspect '$TRAEFIK' --format '{{json .Config.Labels}}' | tr ',' '\n'"
  run "Mounts (Konfigurationsdateien, acme.json wird nicht gelesen)" docker inspect "$TRAEFIK" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Type}}){{"\n"}}{{end}}'
  run "Veroeffentlichte Ports" docker inspect "$TRAEFIK" --format '{{json .NetworkSettings.Ports}}'
  run "Compose-Projekt" docker inspect "$TRAEFIK" --format 'working_dir={{index .Config.Labels "com.docker.compose.project.working_dir"}} config_files={{index .Config.Labels "com.docker.compose.project.config_files"}}'
  run "Traefik-Version (Selbstauskunft)" docker exec "$TRAEFIK" traefik version

  # Statische Konfigurationsdateien aus den Mounts lesen (ohne acme.json)
  out "### Statische Konfigurationsdateien"; out ""
  docker inspect "$TRAEFIK" --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' 2>/dev/null | while read -r src; do
    [ -z "$src" ] && continue
    for f in "$src" "$src"/traefik.yml "$src"/traefik.yaml "$src"/traefik.toml; do
      case "$f" in *acme*|*.json) continue;; esac
      if [ -f "$f" ] && printf '%s' "$f" | grep -qiE 'traefik\.(yml|yaml|toml)$'; then
        out "Datei: \`$f\`"; out ""; out '```yaml'
        $SUDO cat "$f" 2>/dev/null | redact >> "$TMP" || out "(nicht lesbar)"
        out '```'; out ""
      fi
    done
    if [ -d "$src" ]; then
      for f in "$src"/*.yml "$src"/*.yaml "$src"/*.toml "$src"/dynamic/*.yml "$src"/dynamic/*.yaml "$src"/conf.d/*.yml; do
        [ -f "$f" ] || continue
        case "$f" in *traefik.yml|*traefik.yaml|*traefik.toml|*acme*) continue;; esac
        out "Dynamische Datei: \`$f\`"; out ""; out '```yaml'
        $SUDO cat "$f" 2>/dev/null | redact >> "$TMP" || out "(nicht lesbar)"
        out '```'; out ""
      done
    fi
  done

  # Ableitungen aus der Kommandozeile
  CMD="$(docker inspect "$TRAEFIK" --format '{{join .Config.Cmd " "}}' 2>/dev/null)"
  ENVS="$(docker inspect "$TRAEFIK" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep -i '^TRAEFIK_' | sed 's/=.*//')"
  ENTRYPOINTS="$(printf '%s' "$CMD" | grep -oiE '\-\-entrypoints\.[A-Za-z0-9_-]+\.address=[^ ]+' | sort -u)"
  ENTRYPOINT_NAMES="$(printf '%s\n' "$ENTRYPOINTS" | sed -E 's/--entrypoints\.([^.]+)\..*/\1/I' | sort -u | paste -sd' ' -)"
  RESOLVERS="$(printf '%s' "$CMD" | grep -oiE '\-\-certificatesresolvers\.[A-Za-z0-9_-]+' | sed -E 's/--certificatesresolvers\.//I' | sort -u | paste -sd' ' -)"
  [ -z "$RESOLVERS" ] && RESOLVERS="$(printf '%s\n' "$ENVS" | grep -oiE '^TRAEFIK_CERTIFICATESRESOLVERS_[A-Za-z0-9]+' | sed -E 's/TRAEFIK_CERTIFICATESRESOLVERS_//I' | tr 'A-Z' 'a-z' | sort -u | paste -sd' ' -)"
  EXPOSED="$(printf '%s' "$CMD" | grep -oiE '\-\-providers\.docker\.exposedbydefault=[a-z]+' | head -1)"
  CONSTRAINTS="$(printf '%s' "$CMD" | grep -oiE '\-\-providers\.docker\.constraints=[^ ]+' | head -1)"
  REDIRECT="$(printf '%s' "$CMD" | grep -oiE '\-\-entrypoints\.[A-Za-z0-9_-]+\.http\.redirections\.[^ ]+' | paste -sd' ' -)"
  NETS="$(docker inspect "$TRAEFIK" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null)"
else
  out "Kein laufender Traefik-Container gefunden (docker ps | grep -i traefik). Traefik-Werte manuell erheben."
  ENTRYPOINTS=""; ENTRYPOINT_NAMES=""; RESOLVERS=""; EXPOSED=""; CONSTRAINTS=""; REDIRECT=""; NETS=""
fi

# ------------------------------------------------------------------ 4 Host-Sicherheit
section "4. Host-Sicherheit, Ist-Zustand (docs/betrieb.md 1.4)"
run "SSH-Daemon (effektive Werte)" sh -c "$SUDO sshd -T 2>/dev/null | grep -iE '^(permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|port) ' || echo 'sshd -T nicht lesbar (Root-Recht noetig)'"
run "UFW" sh -c "$SUDO ufw status verbose 2>/dev/null || echo 'ufw nicht lesbar oder nicht installiert'"
run "Automatische Sicherheitsupdates" sh -c "cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null || echo 'keine 20auto-upgrades'; systemctl is-enabled unattended-upgrades 2>/dev/null || true"
run "Offene Ports" sh -c "($SUDO ss -tulpen 2>/dev/null || ss -tulen) | grep -i listen"
run "Deploy-Nutzer und Gruppen" id
run "Journal-Groesse" sh -c "journalctl --disk-usage 2>/dev/null || echo 'journalctl nicht verfuegbar'"
run "Verzeichnis /srv/objektakte" sh -c "ls -la /srv/objektakte 2>/dev/null || echo 'existiert noch nicht (wird in M1 angelegt)'"

# ------------------------------------------------------------------ 5 Ergebnisblatt
C="$(nproc 2>/dev/null || echo '?')"
M_GIB="$(free -b 2>/dev/null | awk '/^Mem:/ {printf "%.1f", $2/1024/1024/1024}')"
SWAP_GIB="$(free -b 2>/dev/null | awk '/^Swap:/ {printf "%.1f", $2/1024/1024/1024}')"
if df -B1 /srv >/dev/null 2>&1; then DF_TARGET=/srv; else DF_TARGET=/; fi
D_GIB="$(df -B1 "$DF_TARGET" 2>/dev/null | awk 'NR==2 {printf "%.1f", $4/1024/1024/1024}')"
SCHED="$(for b in /sys/block/*/queue/scheduler; do [ -f "$b" ] && cat "$b" | grep -oE '\[[a-z-]+\]' | tr -d '[]'; done 2>/dev/null | sort -u | paste -sd' ' -)"
CGROUP="$(docker info --format '{{.CgroupVersion}}' 2>/dev/null)"; [ -z "$CGROUP" ] && CGROUP="?"
COMPOSE_V="$(docker compose version --short 2>/dev/null)"; [ -z "$COMPOSE_V" ] && COMPOSE_V="?"
DOCKER_V="$(docker version --format '{{.Server.Version}}' 2>/dev/null)"; [ -z "$DOCKER_V" ] && DOCKER_V="?"
TZ_NOW="$(timedatectl show -p Timezone --value 2>/dev/null)"; [ -z "$TZ_NOW" ] && TZ_NOW="$(cat /etc/timezone 2>/dev/null || echo '?')"

section "5. Ergebnisblatt (Vorlage docs/betrieb.md 1.6, Werte aus diesem Lauf)"
out "| Feld | Wert | Verwendung |"
out "|---|---|---|"
out "| [C] Kerne | $C | OCR_PROCESSES = C minus 1 |"
out "| [M] Arbeitsspeicher GiB | ${M_GIB:-?} | Speicherformel docs/architektur.md 4.4 |"
out "| [S] Swap GiB | ${SWAP_GIB:-?} | ANNAHME A33 pruefen |"
out "| [D] freie Platte GiB ($DF_TARGET) | ${D_GIB:-?} | DISK_RESERVE_GB = max(10, 0,1 x D) |"
out "| [SCHEDULER] | ${SCHED:-?} | Wirkung von ionice (ANNAHME A-25) |"
out "| Docker-Version | $DOCKER_V | |"
out "| [COMPOSE_VERSION] | $COMPOSE_V | deploy.resources.limits |"
out "| [CGROUP_VERSION] | v$CGROUP | ANNAHME AB15 |"
out "| Zeitzone | $TZ_NOW | Ziel Europe/Berlin |"
out "| Traefik-Container | ${TRAEFIK:-nicht gefunden} | |"
out "| Traefik-Netze (Kandidaten TRAEFIK_NETWORK) | ${NETS:-?} | traefik.docker.network |"
out "| Entrypoints | ${ENTRYPOINT_NAMES:-manuell aus Konfigurationsdatei ermitteln} | TRAEFIK_ENTRYPOINT, TRAEFIK_ENTRYPOINT_INSECURE |"
out "| Cert-Resolver | ${RESOLVERS:-manuell aus Konfigurationsdatei ermitteln} | TRAEFIK_CERTRESOLVER |"
out "| exposedByDefault | ${EXPOSED:-nicht als Flag gesetzt (Standard des Providers pruefen)} | traefik.enable=true bleibt gesetzt |"
out "| Constraints | ${CONSTRAINTS:-keine als Flag gesetzt} | Zusatzlabel noetig? |"
out "| Globaler HTTP-Redirect | ${REDIRECT:-nicht als Flag gesetzt (Konfigurationsdatei pruefen)} | TRAEFIK_REDIRECT_ROUTER |"
out ""
[ -n "$ENTRYPOINTS" ] && { out "Entrypoint-Adressen:"; out ""; out '```text'; printf '%s\n' "$ENTRYPOINTS" >> "$TMP"; out '```'; out ""; }

section "6. Vorschlag fuer .env (serverabhaengige Werte, vor Uebernahme pruefen)"
OCR_P=$(( C > 1 ? C - 1 : 1 )) 2>/dev/null || OCR_P="?"
DB_MEM="$(awk -v m="${M_GIB:-0}" 'BEGIN { v = m * 0.15; if (v < 1) v = 1; printf "%.2fg", v }')"
DISK_RES="$(awk -v d="${D_GIB:-0}" 'BEGIN { v = d * 0.1; if (v < 10) v = 10; printf "%d", v }')"
out '```dotenv'
out "# aus dem Serverbefund vom $(date +%d.%m.%Y)"
out "TRAEFIK_NETWORK=            # einer der Kandidaten: ${NETS:-?}"
out "TRAEFIK_ENTRYPOINT=         # HTTPS-Entrypoint, Kandidaten: ${ENTRYPOINT_NAMES:-?}"
out "TRAEFIK_ENTRYPOINT_INSECURE= # HTTP-Entrypoint (nur fuer Redirect-Router)"
out "TRAEFIK_REDIRECT_ROUTER=    # true, wenn kein globaler Redirect vorhanden"
out "TRAEFIK_CERTRESOLVER=       # Kandidaten: ${RESOLVERS:-?}"
out "APP_DOMAIN=uebernahme.muellerhv.de"
out "OCR_PROCESSES=$OCR_P             # C minus 1, C = $C"
out "WORKER_CPUS=$OCR_P"
out "WORKER_MEM=                  # P x m_ocr + 0,5 GiB, m_ocr aus dem OCR-Probelauf (scripts/perf_probe.sh)"
out "DB_MEM=$DB_MEM                # max(1 GiB, 0,15 x M), M = ${M_GIB:-?} GiB"
out "DISK_RESERVE_GB=$DISK_RES            # max(10, 0,1 x D), D = ${D_GIB:-?} GiB"
out '```'
out ""
out "Randbedingung: Summe aller Speicherlimits hoechstens 0,85 x M (docs/architektur.md 4.4). Der OCR-Probelauf (scripts/perf_probe.sh) liefert m_ocr, t_ocr und e fuer die Prozessentscheidung."

cp "$TMP" "$OUT"
echo "Serverbefund geschrieben: $OUT"
echo "Bitte vor Uebernahme in das Repository auf Geheimnisse pruefen (Suche nach password, secret, token, E-Mail-Adressen)."
