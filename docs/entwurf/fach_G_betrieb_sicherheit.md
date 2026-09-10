# Fachentwurf G: Betrieb, Deployment und Sicherheit (CR-05, Abschnitte 0.1, 10, 14, 15)

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Stand: 10.09.2026. Faktenbasis: CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md) und Befundakte (docs/entwurf/befundakte.md). Alles, was nicht aus diesen beiden Quellen stammt, ist als Vorschlag oder als ANNAHME gekennzeichnet. Bezeichner von Tabellen, Diensten und Variablen sind mit den Nachbarentwürfen abgestimmt (Datenmodell D: `oauth_tokens`, `audit_events`, `iban_access_log`, `app_settings`, `processing_jobs`, `review_cases`; Pipeline E: Verzeichnisse `transit`, `work`, `ocr-cache`, `models`; Stack A: Dienste `web`, `worker`, `worker-io`, `beat`).

Der Zielserver war aus der Entwicklungsumgebung nicht erreichbar (Befund Abschnitt 2). Deshalb enthält dieser Entwurf keine Serverwerte, sondern eine Ausleseanleitung (Abschnitt 1) und eine Compose-Datei, in der alle serverabhängigen Werte Variablen sind. Versionsnummern von Images und Bibliotheken werden nicht genannt; sie sind zum Umsetzungszeitpunkt als aktuelle LTS bzw. stabile Version zu prüfen und in `.env` einzutragen.

## 0. Ergebnis und Empfehlung

| Nr. | Entscheidung | Begründung (kurz) |
|---|---|---|
| 1 | Sieben Dienste in einer `docker-compose.yml`: `web`, `worker`, `worker-io`, `beat`, `redis`, `db`, `backup`; optional `classifier` über ein Compose-Profil. | CR Abschnitt 7 nennt web, worker, queue, db und optional classifier. `worker-io` und `beat` sind Vorschläge aus Stack A (Netzwerkarbeit belegt keine OCR-Prozesse, periodische Aufgaben brauchen einen Zeitgeber). `backup` folgt aus CR Abschnitt 0.1. |
| 2 | Zwei interne Netzwerke: `data` (Docker `internal: true`, kein Internetzugang) für `db` und `redis`, `egress` für ausgehende Verbindungen zu Google, OpenAI und Anthropic. Nur `web` hängt zusätzlich im vorhandenen Traefik-Netzwerk `${TRAEFIK_NETWORK}`. Kein Dienst bindet Host-Ports. | CR Abschnitt 0.1: Traefik ist der einzige öffentlich erreichbare Dienst. Datenbank und Queue erreichen damit nie das Internet, auch nicht bei Fehlkonfiguration. |
| 3 | Datenbank MariaDB (Image-Tag als Variable, aktuelle LTS zum Umsetzungszeitpunkt prüfen). | Freie Lizenzlage ohne Zwei-Lizenzen-Modell, `mariadb-dump` und Healthcheck-Skript im offiziellen Image, gleichwertige Unterstützung im gewählten Stack. MySQL bleibt als Alternative ohne Umbau des Schemas möglich. |
| 4 | Alle persistenten Daten unter `/srv/objektakte/` (db, redis, transit, work, ocr-cache, models, backup, secrets). `db` und `redis` als benannte Volumes, die per `driver_opts` auf diese Pfade gebunden sind; Arbeitsverzeichnisse als Bind-Mounts. | CR Abschnitt 0.1: nie im Container. Ein Wurzelpfad vereinfacht Backup, Plattenüberwachung und Wiederherstellung. |
| 5 | Geheimnisse als dateibasierte Docker Secrets unter `/srv/objektakte/secrets/` (Rechte 0600, Eigentümer root). `.env` enthält nur nicht geheime Konfiguration und die Serverwerte. `.env.example` liegt im Repository, `.env` und `secrets/` nie. | Secrets erscheinen so weder in `docker inspect` noch in `docker compose config`. Auf einem einzelnen Host mit Root-Zugriff ist der Schutz gleichwertig zu `.env`, aber die Leckpfade sind weniger. |
| 6 | Ressourcenlimits als Variablen mit dokumentierter Formel aus gemessenen Werten C (Kerne), M (RAM), D (Platte). Worker erhält P = max(1, C-1) OCR-Prozesse und ein hartes CPU-Limit, `web` ein hohes CPU-Gewicht. | CR Abschnitt 0.1 und 7: messen statt annehmen; OCR darf die Web-App nicht verdrängen. |
| 7 | Backup: Container `backup` mit Cron, täglich `mariadb-dump` plus Tar der Arbeitsverzeichnisse nach `/srv/objektakte/backup/`, Aufbewahrung in Tagen konfigurierbar, Statusdatei für die Statusseite, Wiederherstellung als Skript und Anleitung. Offsite-Kopie verschlüsselt als Empfehlung. | CR Abschnitt 0.1 und 14. Dumps enthalten Namen und Adressen im Klartext, deshalb Verschlüsselung vor jedem Transport außer Haus. |
| 8 | Deployment über `scripts/deploy.sh` (git pull, build mit Git-SHA als Image-Tag, Dump, Migration, up, Smoke-Test), Rollback über `scripts/rollback.sh` mit einem Befehl auf das zuvor laufende Image. Migrationen nur rückwärtskompatibel (Erweitern und Zusammenziehen), Undo-Skript je Migration. | CR Abschnitt 0 (reversible Migrationen) und 0.1 (Rollback mit einem Befehl). |
| 9 | Feldverschlüsselung AES-256-GCM mit je einem Schlüssel pro Zweck (`IBAN_KEY`, `TOKEN_KEY`, `TOTP_KEY`, `IBAN_HMAC_KEY`) und Schlüsselversion je Datensatz; Rotation als dokumentierter Job. | CR Abschnitt 3 und 10; abgestimmt mit Datenmodell D Abschnitt 10.4. |
| 10 | Zwei Rollen (Admin, Sachbearbeiter), TOTP für alle Nutzer verpflichtend, Google-Workspace-Login optional und auf die Domain `muellerhv.de` begrenzt, IBAN-Klartext in keiner Oberfläche für keine Rolle, jede Review-Aktion in `audit_events`. | CR Abschnitt 10 und 15. |
| 11 | DSGVO-Voraussetzungen (AVV, EU-Region, Trainings-Opt-out, Verzeichnis der Verarbeitungstätigkeiten, Aufbewahrungsfristen) werden als Checkliste des Auftraggebers geführt und sind Freigabekriterium vor dem ersten externen KI-Aufruf mit Produktivdaten. | CR Abschnitt 0.1 und 10. |

Reihenfolge der Umsetzung im Betriebsteil: (1) Serverbefund nach Abschnitt 1 erheben, (2) Host härten nach Abschnitt 6, (3) Verzeichnisse und Secrets anlegen nach Abschnitt 2 und 4, (4) Compose starten und Deployment-Test nach Abschnitt 14 durchführen, (5) Google-OAuth nach Abschnitt 10 verbinden und den 8-Tage-Nachweis starten, (6) Backup-Wiederherstellung einmal proben.

## 1. Serverbefund vor der Umsetzung (Ausleseanleitung)

Die folgenden Befehle führt der Auftraggeber oder der Entwickler nach SSH-Zugang aus. Die Ergebnisse werden in das Ergebnisblatt (Abschnitt 1.5) und anschließend in `.env` übertragen. Keine Änderung am Server in diesem Schritt, nur Lesen. Befehle, die `sudo` brauchen, sind gekennzeichnet.

### 1.1 Ressourcen

```bash
# Kerne, Arbeitsspeicher, Platte
nproc
lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Core|Socket'
free -h
df -h
df -h /srv 2>/dev/null || echo "/srv existiert noch nicht, liegt dann auf der Wurzelpartition"
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
# Betriebssystem und Zeit
lsb_release -a
timedatectl
uptime
```

Aufzuschreiben: C = Ausgabe von `nproc`, M = Spalte total von `free -h` (Zeile Mem, in GiB), D = Spalte Avail von `df -h` für die Partition, auf der `/srv` liegt, S = Swap (Zeile Swap).

### 1.2 Docker und Compose

```bash
docker version
docker compose version
docker info --format '{{.ServerVersion}} | Storage: {{.Driver}} | Cgroup: {{.CgroupDriver}} v{{.CgroupVersion}} | LogDriver: {{.LoggingDriver}}'
sudo cat /etc/docker/daemon.json 2>/dev/null || echo "keine daemon.json"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls
docker volume ls
docker system df
```

Aufzuschreiben: Compose-Hauptversion (Plugin `docker compose`, nicht das alte `docker-compose`), Cgroup-Version (v2 wird für `deploy.resources.limits` erwartet), vorhandene Netzwerke, bereits belegte Ports.

### 1.3 Traefik

Zuerst den Container finden, dann Netzwerke, Kommandozeile, Labels und Mounts auslesen.

```bash
# Traefik-Container ermitteln (Name kann abweichen)
docker ps --format '{{.Names}}\t{{.Image}}' | grep -i traefik
TRAEFIK=$(docker ps --format '{{.Names}}\t{{.Image}}' | grep -i traefik | head -1 | cut -f1)
echo "Traefik-Container: $TRAEFIK"

# Netzwerke, in denen Traefik hängt (Kandidaten für TRAEFIK_NETWORK)
docker inspect "$TRAEFIK" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'

# Kommandozeile (statische Konfiguration als Flags)
docker inspect "$TRAEFIK" --format '{{json .Config.Cmd}}' | tr ',' '\n'
docker inspect "$TRAEFIK" --format '{{json .Config.Entrypoint}}'

# Umgebungsvariablen (statische Konfiguration als TRAEFIK_*), Werte mit Secrets nicht weitergeben
docker inspect "$TRAEFIK" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -i '^TRAEFIK_' | sed 's/=.*//'

# Labels des Traefik-Containers selbst (Dashboard, Redirects, Middlewares)
docker inspect "$TRAEFIK" --format '{{json .Config.Labels}}' | tr ',' '\n'

# Mounts: hier liegen traefik.yml/traefik.toml, dynamische Konfiguration und acme.json
docker inspect "$TRAEFIK" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Type}}){{"\n"}}{{end}}'

# Veröffentlichte Ports (erwartet 80 und 443)
docker inspect "$TRAEFIK" --format '{{json .NetworkSettings.Ports}}'
```

Konfigurationsdateien lesen (Pfade aus der Mounts-Ausgabe einsetzen). Falls Traefik über eine eigene Compose-Datei läuft, liegt sie meist im selben Verzeichnis wie die Konfiguration.

```bash
# Kandidaten für die statische Konfiguration
sudo find / -xdev \( -name 'traefik.yml' -o -name 'traefik.yaml' -o -name 'traefik.toml' \) 2>/dev/null
# Compose-Datei des Traefik-Stacks
docker inspect "$TRAEFIK" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
docker inspect "$TRAEFIK" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'

# Entrypoints und Cert-Resolver aus der statischen Konfiguration (Datei- oder Flag-Form)
sudo grep -nEi 'entryPoints|address:|certificatesResolvers|acme|httpChallenge|tlsChallenge|dnsChallenge|email' <PFAD_traefik.yml>
docker inspect "$TRAEFIK" --format '{{json .Config.Cmd}}' | tr ',' '\n' | grep -Ei 'entrypoints|certificatesresolvers|providers.docker'

# Docker-Provider: exposedByDefault und Standardnetzwerk
sudo grep -nEi 'providers|docker|exposedByDefault|network:|constraints|watch' <PFAD_traefik.yml>
docker inspect "$TRAEFIK" --format '{{json .Config.Cmd}}' | tr ',' '\n' | grep -Ei 'exposedbydefault|providers.docker.network|constraints'

# Dynamische Konfiguration (Middlewares, globale Redirects, TLS-Optionen)
sudo grep -rnEi 'redirectScheme|redirections|middlewares|tls:|options:|minVersion|headers' <VERZEICHNIS_dynamische_Konfiguration> 2>/dev/null

# Zertifikatsspeicher (nur Existenz und Rechte prüfen, Inhalt nicht kopieren)
sudo ls -l <PFAD_acme.json>
```

Prüfung des Netzwerks, das für `TRAEFIK_NETWORK` gewählt wird:

```bash
docker network inspect <NETZWERKNAME> --format '{{.Name}} | Driver {{.Driver}} | Internal {{.Internal}} | Container: {{range .Containers}}{{.Name}} {{end}}'
```

Auswertung:

| Frage | Woran erkennbar | Folge für die Compose-Datei |
|---|---|---|
| Wie heißt das externe Netzwerk? | Netzwerk, in dem Traefik hängt und das nicht das Default-Bridge-Netz ist. Hängt Traefik in mehreren, das Netz nehmen, das andere Anwendungscontainer verwenden oder das in `providers.docker.network` steht. | `TRAEFIK_NETWORK=<Name>` |
| Wie heißt der HTTPS-Entrypoint? | `entryPoints.<name>.address: ":443"` bzw. Flag `--entrypoints.<name>.address=:443`. Häufig `websecure`, aber nicht annehmen. | `TRAEFIK_ENTRYPOINT=<name>` |
| Wie heißt der HTTP-Entrypoint und gibt es einen globalen Redirect auf HTTPS? | `address: ":80"` und darunter `http.redirections.entryPoint.to`. | `TRAEFIK_ENTRYPOINT_INSECURE=<name>`; ohne globalen Redirect wird der optionale Redirect-Router aus Abschnitt 3 aktiviert (`TRAEFIK_REDIRECT_ROUTER=true`). |
| Wie heißt der Cert-Resolver? | Schlüssel unter `certificatesResolvers.<name>.acme` bzw. Flag `--certificatesresolvers.<name>.acme...`. | `TRAEFIK_CERTRESOLVER=<name>` |
| Welche ACME-Challenge? | `httpChallenge`, `tlsChallenge` oder `dnsChallenge`. Bei `httpChallenge` muss Port 80 offen bleiben (UFW). | Keine Variable, aber Voraussetzung für die Firewall-Regel. |
| Ist `exposedByDefault` auf `false`? | `providers.docker.exposedByDefault: false` oder Flag. | Dann ist `traefik.enable=true` am Dienst `web` zwingend. Die Compose-Datei setzt das Label immer, weil es auch bei `true` unschädlich ist. |
| Gibt es `providers.docker.constraints`? | Ausdruck mit Labels, z. B. auf ein eigenes Label. | Dann muss `web` dieses Label zusätzlich tragen; Wert im Ergebnisblatt notieren. |
| Hängt Traefik im Swarm-Modus? | `providers.swarm` oder `providers.docker.swarmMode`. | Erwartet wird kein Swarm. Falls doch, Labels unter `deploy.labels` statt `labels`. |
| Welche Traefik-Hauptversion? | Image-Tag in `docker ps`. | Label-Syntax (Router, Middlewares) gilt für Traefik v2 und v3; bei v1 wäre die Syntax anders und der Entwurf anzupassen. |

### 1.4 Host-Sicherheit (Ist-Zustand, nur lesen)

```bash
sudo sshd -T 2>/dev/null | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|port) '
sudo ufw status verbose
sudo systemctl is-enabled unattended-upgrades; sudo systemctl is-active unattended-upgrades
sudo cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null
sudo ss -tulpen | grep LISTEN
id; sudo -l | head -20
sudo journalctl --disk-usage
```

### 1.5 Ergebnisblatt

Das Blatt wird ausgefüllt und in `docs/betrieb/serverbefund.md` im Repository abgelegt (ohne Geheimnisse). Die rechte Spalte nennt die Zielvariable.

| Messgröße | Befehl | Wert | Zielvariable |
|---|---|---|---|
| Kerne C | `nproc` | | Grundlage für `OCR_PROCESSES`, `WORKER_CPUS` |
| RAM M in GiB | `free -h` | | Grundlage für alle `*_MEM` |
| Swap S | `free -h` | | Hinweis für Abschnitt 5 |
| Platte D frei in GiB (Partition von `/srv`) | `df -h` | | `DISK_RESERVE_GB`, `BACKUP_RETENTION_DAYS` |
| Compose-Version | `docker compose version` | | Prüfung `deploy.resources.limits` |
| Cgroup-Version | `docker info` | | Prüfung CPU-Limits |
| Traefik-Containername | `docker ps` | | nur Doku |
| Traefik-Hauptversion | `docker ps` (Image-Tag) | | nur Doku |
| Externes Netzwerk | `docker inspect` | | `TRAEFIK_NETWORK` |
| HTTPS-Entrypoint | Konfiguration | | `TRAEFIK_ENTRYPOINT` |
| HTTP-Entrypoint | Konfiguration | | `TRAEFIK_ENTRYPOINT_INSECURE` |
| Globaler Redirect vorhanden | Konfiguration | ja/nein | `TRAEFIK_REDIRECT_ROUTER` |
| Cert-Resolver | Konfiguration | | `TRAEFIK_CERTRESOLVER` |
| ACME-Challenge | Konfiguration | | Firewall-Regel Port 80 |
| exposedByDefault | Konfiguration | true/false | Label `traefik.enable` |
| Constraints | Konfiguration | | Zusatzlabel an `web` |
| SSH: Root-Login, Passwort-Login | `sshd -T` | | Abschnitt 6 |
| UFW-Status und Regeln | `ufw status` | | Abschnitt 6 |
| unattended-upgrades | `systemctl` | | Abschnitt 6 |
| Zeitzone | `timedatectl` | | `TZ` |
| Belegte Host-Ports | `ss -tulpen` | | Konfliktprüfung (unsere Dienste binden keine) |

## 2. Verzeichnisse, Volumes und Netzwerke

### 2.1 Verzeichnisstruktur auf dem Host

```text
/srv/objektakte/
  db/           MariaDB-Datenverzeichnis (benanntes Volume, gebunden auf diesen Pfad)
  redis/        Redis-AOF (benanntes Volume, gebunden auf diesen Pfad)
  transit/      Uploads bis zur Übernahme nach Drive (Pipeline E, Abschnitt 1.1)
  work/         Original, Chunks, OCR-Zwischenstand je Dokument; wird nach DONE geräumt
  ocr-cache/    maskierter Seitentext je Dokument-Hash, bleibt erhalten
  models/       Artefakte des lokalen Klassifikators je Version
  backup/       Sicherungen (db/, volumes/, status.json), getrennt von den Nutzdaten
  secrets/      dateibasierte Docker Secrets, 0700 root, Dateien 0600 root
  deploy/       current, previous, tags.log (Image-Tags für Rollback)
```

Anlage (einmalig, als root; die Anwendungscontainer laufen mit einer festen, nicht privilegierten UID, ANNAHME: 10001, im Dockerfile festgelegt und in `.env` als `APP_UID` gespiegelt):

```bash
sudo mkdir -p /srv/objektakte/{db,redis,transit,work,ocr-cache,models,backup/db,backup/volumes,secrets,deploy}
sudo chown -R 10001:10001 /srv/objektakte/{transit,work,ocr-cache,models}
sudo chmod 750 /srv/objektakte/{transit,work,ocr-cache,models}
sudo chmod 700 /srv/objektakte/secrets /srv/objektakte/backup
# db und redis: die Images setzen ihre eigenen Nutzer, deshalb hier keine chown-Vorgabe
```

Warum getrennte Verzeichnisse für `db` und `redis` als benannte Volumes mit `driver_opts`: Compose kennt sie damit als Volumes (Lebenszyklus, `docker volume ls`), die Daten liegen aber sichtbar unter `/srv/objektakte/` und nicht unter `/var/lib/docker/volumes/`. Das erfüllt beide Vorgaben des CR (benannte Volumes oder `/srv/objektakte/`) und hält Sicherung und Plattenüberwachung an einem Ort. Voraussetzung: die Verzeichnisse existieren vor dem ersten `docker compose up`.

### 2.2 Netzwerke

| Netzwerk | Typ | Teilnehmer | Zweck |
|---|---|---|---|
| `data` | Compose-Netz, `internal: true` | `web`, `worker`, `worker-io`, `beat`, `backup`, `db`, `redis`, optional `classifier` | Interner Verkehr zu Datenbank und Queue. `internal: true` verhindert jede ausgehende Verbindung aus diesem Netz; `db` und `redis` hängen nur hier und erreichen das Internet nie. |
| `egress` | Compose-Netz, Bridge | `web`, `worker`, `worker-io`, `beat`, optional `backup` (nur bei Offsite-Kopie) | Ausgehende Verbindungen zu Google Drive, OpenAI, Anthropic, SMTP. Keine Host-Ports. |
| `${TRAEFIK_NETWORK}` | vorhandenes externes Netz | nur `web` | Eingehender Verkehr von Traefik. Name aus dem Serverbefund. |

Docker-DNS funktioniert auch in internen Netzen, die Dienste erreichen sich unter ihren Compose-Namen (`db`, `redis`). Traefik erreicht `web` über das externe Netz; das Label `traefik.docker.network` sagt Traefik, welches der drei Netze von `web` es für die Weiterleitung nutzen soll. Ohne dieses Label wählt Traefik bei mehreren Netzen unter Umständen das falsche.

## 3. docker-compose.yml (vollständiger Entwurf)

Datei `docker-compose.yml` im Repository-Wurzelverzeichnis. Alle serverabhängigen Werte kommen aus `.env` (Abschnitt 4). Kommentare in der Datei sind auf das Nötigste beschränkt; die Begründungen stehen unter der Datei. Stackspezifische Kommandos (`gunicorn`, `celery`) folgen Stack A; wird ein anderer Stack gewählt, ändern sich nur die `command`-Zeilen und die Heartbeat-Skripte, nicht die Struktur.

```yaml
name: objektakte

x-app-env: &app-env
  TZ: ${TZ}
  DB_HOST: db
  DB_PORT: "3306"
  REDIS_URL: redis://redis:6379/0
  DB_PASSWORD_FILE: /run/secrets/db_app_password
  REDIS_PASSWORD_FILE: /run/secrets/redis_password
  APP_SECRET_KEY_FILE: /run/secrets/app_secret_key
  IBAN_KEY_FILE: /run/secrets/iban_key
  IBAN_HMAC_KEY_FILE: /run/secrets/iban_hmac_key
  TOKEN_KEY_FILE: /run/secrets/token_key
  TOTP_KEY_FILE: /run/secrets/totp_key
  GOOGLE_CLIENT_SECRET_FILE: /run/secrets/google_client_secret
  OPENAI_API_KEY_FILE: /run/secrets/openai_api_key
  ANTHROPIC_API_KEY_FILE: /run/secrets/anthropic_api_key
  SMTP_PASSWORD_FILE: /run/secrets/smtp_password
  READYZ_TOKEN_FILE: /run/secrets/readyz_token

x-app-base: &app-base
  image: objektakte/app:${IMAGE_TAG:-dev}
  build:
    context: .
    dockerfile: docker/app.Dockerfile
  env_file: .env
  restart: unless-stopped
  user: "${APP_UID}:${APP_UID}"
  security_opt:
    - no-new-privileges:true
  logging:
    driver: json-file
    options:
      max-size: "${LOG_MAX_SIZE}"
      max-file: "${LOG_MAX_FILE}"
  environment: *app-env
  secrets:
    - db_app_password
    - redis_password
    - app_secret_key
    - iban_key
    - iban_hmac_key
    - token_key
    - totp_key
    - google_client_secret
    - openai_api_key
    - anthropic_api_key
    - smtp_password
    - readyz_token
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy

services:

  web:
    <<: *app-base
    command: >
      gunicorn objektakte.wsgi:application
      --bind 0.0.0.0:8000
      --workers ${GUNICORN_WORKERS}
      --timeout ${GUNICORN_TIMEOUT}
      --access-logfile - --error-logfile -
      --forwarded-allow-ips ${TRUSTED_PROXY_CIDR}
    networks:
      - data
      - egress
      - traefik
    volumes:
      - /srv/objektakte/transit:/data/transit
      - /srv/objektakte/ocr-cache:/data/ocr-cache:ro
      - /srv/objektakte/backup:/data/backup:ro
    labels:
      traefik.enable: "true"
      traefik.docker.network: "${TRAEFIK_NETWORK}"
      traefik.http.routers.objektakte.rule: "Host(`${APP_DOMAIN}`)"
      traefik.http.routers.objektakte.entrypoints: "${TRAEFIK_ENTRYPOINT}"
      traefik.http.routers.objektakte.tls: "true"
      traefik.http.routers.objektakte.tls.certresolver: "${TRAEFIK_CERTRESOLVER}"
      traefik.http.routers.objektakte.middlewares: "objektakte-headers"
      traefik.http.services.objektakte.loadbalancer.server.port: "8000"
      traefik.http.middlewares.objektakte-headers.headers.stsSeconds: "31536000"
      traefik.http.middlewares.objektakte-headers.headers.stsIncludeSubdomains: "false"
      traefik.http.middlewares.objektakte-headers.headers.contentTypeNosniff: "true"
      traefik.http.middlewares.objektakte-headers.headers.customFrameOptionsValue: "SAMEORIGIN"
      traefik.http.middlewares.objektakte-headers.headers.referrerPolicy: "strict-origin-when-cross-origin"
      # Optionaler Redirect-Router, nur wenn Traefik keinen globalen HTTP-zu-HTTPS-Redirect hat
      # (Serverbefund TRAEFIK_REDIRECT_ROUTER=true). Sonst diese vier Zeilen entfernen.
      traefik.http.routers.objektakte-http.rule: "Host(`${APP_DOMAIN}`)"
      traefik.http.routers.objektakte-http.entrypoints: "${TRAEFIK_ENTRYPOINT_INSECURE}"
      traefik.http.routers.objektakte-http.middlewares: "objektakte-redirect"
      traefik.http.middlewares.objektakte-redirect.redirectscheme.scheme: "https"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=5); sys.exit(0 if r.status==200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    cpu_shares: 1024
    deploy:
      resources:
        limits:
          cpus: "${WEB_CPUS}"
          memory: "${WEB_MEM}"

  worker:
    <<: *app-base
    command: >
      celery -A objektakte worker
      -Q cpu -n worker-cpu@%h
      --concurrency ${OCR_PROCESSES}
      --prefetch-multiplier 1
      --max-tasks-per-child ${WORKER_MAX_TASKS_PER_CHILD}
      --max-memory-per-child ${WORKER_MAX_MEMORY_PER_CHILD_KB}
    environment:
      <<: *app-env
      OMP_THREAD_LIMIT: "1"
      HEARTBEAT_FILE: /tmp/heartbeat
    networks:
      - data
      - egress
    volumes:
      - /srv/objektakte/transit:/data/transit
      - /srv/objektakte/work:/data/work
      - /srv/objektakte/ocr-cache:/data/ocr-cache
      - /srv/objektakte/models:/data/models
    stop_grace_period: ${WORKER_STOP_GRACE}
    healthcheck:
      test: ["CMD-SHELL", "test -n \"$$(find /tmp/heartbeat -mmin -2 2>/dev/null)\""]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 60s
    cpu_shares: 256
    deploy:
      resources:
        limits:
          cpus: "${WORKER_CPUS}"
          memory: "${WORKER_MEM}"

  worker-io:
    <<: *app-base
    command: >
      celery -A objektakte worker
      -Q io -n worker-io@%h
      --concurrency ${IO_CONCURRENCY}
      --prefetch-multiplier 1
      --max-tasks-per-child ${WORKER_MAX_TASKS_PER_CHILD}
    environment:
      <<: *app-env
      HEARTBEAT_FILE: /tmp/heartbeat
    networks:
      - data
      - egress
    volumes:
      - /srv/objektakte/transit:/data/transit
      - /srv/objektakte/work:/data/work:ro
      - /srv/objektakte/ocr-cache:/data/ocr-cache
      - /srv/objektakte/models:/data/models:ro
    stop_grace_period: ${WORKER_STOP_GRACE}
    healthcheck:
      test: ["CMD-SHELL", "test -n \"$$(find /tmp/heartbeat -mmin -2 2>/dev/null)\""]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 60s
    cpu_shares: 512
    deploy:
      resources:
        limits:
          cpus: "${WORKER_IO_CPUS}"
          memory: "${WORKER_IO_MEM}"

  beat:
    <<: *app-base
    command: celery -A objektakte beat --schedule /tmp/celerybeat-schedule --pidfile=
    environment:
      <<: *app-env
      HEARTBEAT_FILE: /tmp/heartbeat
    networks:
      - data
      - egress
    healthcheck:
      test: ["CMD-SHELL", "test -n \"$$(find /tmp/heartbeat -mmin -3 2>/dev/null)\""]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 60s
    cpu_shares: 256
    deploy:
      resources:
        limits:
          cpus: "${BEAT_CPUS}"
          memory: "${BEAT_MEM}"

  redis:
    image: redis:${REDIS_TAG}
    command: >
      sh -c 'exec redis-server
      --requirepass "$$(cat /run/secrets/redis_password)"
      --appendonly yes
      --maxmemory ${REDIS_MAXMEMORY}
      --maxmemory-policy noeviction
      --save ""'
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    networks:
      - data
    volumes:
      - redis-data:/data
    secrets:
      - redis_password
    logging:
      driver: json-file
      options:
        max-size: "${LOG_MAX_SIZE}"
        max-file: "${LOG_MAX_FILE}"
    healthcheck:
      test: ["CMD-SHELL", "REDISCLI_AUTH=\"$$(cat /run/secrets/redis_password)\" redis-cli ping | grep -q PONG"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: "${REDIS_CPUS}"
          memory: "${REDIS_MEM}"

  db:
    image: mariadb:${MARIADB_TAG}
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    environment:
      TZ: ${TZ}
      MARIADB_DATABASE: ${DB_NAME}
      MARIADB_USER: ${DB_USER}
      MARIADB_PASSWORD_FILE: /run/secrets/db_app_password
      MARIADB_ROOT_PASSWORD_FILE: /run/secrets/db_root_password
      MARIADB_AUTO_UPGRADE: "1"
    command: >
      --character-set-server=utf8mb4
      --collation-server=utf8mb4_unicode_ci
      --innodb-buffer-pool-size=${DB_INNODB_BUFFER_POOL}
      --max-connections=${DB_MAX_CONNECTIONS}
      --sql-mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION
      --skip-name-resolve
    networks:
      - data
    volumes:
      - db-data:/var/lib/mysql
      - ./docker/db/init:/docker-entrypoint-initdb.d:ro
    secrets:
      - db_app_password
      - db_root_password
      - db_backup_password
    logging:
      driver: json-file
      options:
        max-size: "${LOG_MAX_SIZE}"
        max-file: "${LOG_MAX_FILE}"
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s
    cpu_shares: 768
    deploy:
      resources:
        limits:
          cpus: "${DB_CPUS}"
          memory: "${DB_MEM}"

  backup:
    build:
      context: .
      dockerfile: docker/backup.Dockerfile
    image: objektakte/backup:${IMAGE_TAG:-dev}
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    environment:
      TZ: ${TZ}
      DB_HOST: db
      DB_NAME: ${DB_NAME}
      DB_BACKUP_USER: ${DB_BACKUP_USER}
      DB_BACKUP_PASSWORD_FILE: /run/secrets/db_backup_password
      BACKUP_CRON: ${BACKUP_CRON}
      BACKUP_RETENTION_DAYS: ${BACKUP_RETENTION_DAYS}
      BACKUP_MAX_AGE_HOURS: ${BACKUP_MAX_AGE_HOURS}
      BACKUP_ENCRYPT_RECIPIENT_FILE: /run/secrets/backup_age_recipient
      OFFSITE_ENABLED: ${OFFSITE_ENABLED}
    networks:
      - data
    volumes:
      - /srv/objektakte/transit:/src/transit:ro
      - /srv/objektakte/ocr-cache:/src/ocr-cache:ro
      - /srv/objektakte/models:/src/models:ro
      - /srv/objektakte/backup:/backup
    secrets:
      - db_backup_password
      - backup_age_recipient
    logging:
      driver: json-file
      options:
        max-size: "${LOG_MAX_SIZE}"
        max-file: "${LOG_MAX_FILE}"
    healthcheck:
      test: ["CMD-SHELL", "pgrep crond >/dev/null && test -n \"$$(find /backup/status.json -mmin -$$((BACKUP_MAX_AGE_HOURS*60)) 2>/dev/null)\""]
      interval: 5m
      timeout: 10s
      retries: 3
      start_period: 2m
    depends_on:
      db:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: "${BACKUP_CPUS}"
          memory: "${BACKUP_MEM}"

  classifier:
    <<: *app-base
    profiles: ["classifier"]
    command: python -m objektakte.classifier.server --port 9000
    networks:
      - data
    volumes:
      - /srv/objektakte/models:/data/models:ro
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:9000/healthz', timeout=5); sys.exit(0 if r.status==200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          cpus: "${CLASSIFIER_CPUS}"
          memory: "${CLASSIFIER_MEM}"

networks:
  data:
    driver: bridge
    internal: true
  egress:
    driver: bridge
  traefik:
    external: true
    name: ${TRAEFIK_NETWORK}

volumes:
  db-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /srv/objektakte/db
  redis-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /srv/objektakte/redis

secrets:
  db_root_password:        { file: /srv/objektakte/secrets/db_root_password }
  db_app_password:         { file: /srv/objektakte/secrets/db_app_password }
  db_backup_password:      { file: /srv/objektakte/secrets/db_backup_password }
  redis_password:          { file: /srv/objektakte/secrets/redis_password }
  app_secret_key:          { file: /srv/objektakte/secrets/app_secret_key }
  iban_key:                { file: /srv/objektakte/secrets/iban_key }
  iban_hmac_key:           { file: /srv/objektakte/secrets/iban_hmac_key }
  token_key:               { file: /srv/objektakte/secrets/token_key }
  totp_key:                { file: /srv/objektakte/secrets/totp_key }
  google_client_secret:    { file: /srv/objektakte/secrets/google_client_secret }
  openai_api_key:          { file: /srv/objektakte/secrets/openai_api_key }
  anthropic_api_key:       { file: /srv/objektakte/secrets/anthropic_api_key }
  smtp_password:           { file: /srv/objektakte/secrets/smtp_password }
  readyz_token:            { file: /srv/objektakte/secrets/readyz_token }
  backup_age_recipient:    { file: /srv/objektakte/secrets/backup_age_recipient }
```

Erläuterungen zu den Entscheidungen in der Datei:

| Stelle | Entscheidung | Begründung |
|---|---|---|
| `x-app-base` | Ein Image für `web`, `worker`, `worker-io`, `beat`, `classifier` | Code, Migrationen und Abhängigkeiten sind immer identisch; ein Build-Pfad. Stack A Abschnitt 3.1. |
| `user`, `no-new-privileges` | Anwendungscontainer laufen nicht als root | Begrenzung der Wirkung einer Sicherheitslücke in ocrmypdf, Ghostscript oder der Web-App. Bind-Mounts gehören der gleichen UID (Abschnitt 2.1). |
| `*_FILE`-Variablen | Geheimnisse werden aus `/run/secrets/` gelesen, nie aus Umgebungsvariablen | Umgebungsvariablen erscheinen in `docker inspect`, Prozesslisten und Fehlerberichten. Die Anwendung liest bei Start einmal die Datei. Das MariaDB-Image unterstützt die `_FILE`-Form nativ (zum Umsetzungszeitpunkt am gewählten Tag prüfen). |
| `web` ohne `ports` | Keine Host-Ports; Traefik erreicht Port 8000 über das externe Netz | CR Abschnitt 0.1. |
| `traefik.enable=true` | Immer gesetzt | Zwingend bei `exposedByDefault=false`, unschädlich bei `true`. |
| `traefik.docker.network` | Weist Traefik das Netz zu | `web` hängt in drei Netzen; ohne Label ist die Wahl nicht deterministisch. |
| Header-Middleware | HSTS, `nosniff`, `SAMEORIGIN`, Referrer-Policy | Grundschutz ohne Eingriff in die Traefik-Konfiguration; `SAMEORIGIN` statt `frameDeny`, weil die PDF-Vorschau im Review Center in einem Frame derselben Origin läuft. HSTS ohne Subdomains, weil nur diese Subdomain betroffen ist und andere Dienste unter `muellerhv.de` nicht beeinflusst werden dürfen. |
| Redirect-Router | Nur wenn Traefik keinen globalen Redirect hat | Doppelte Redirects sind unschädlich, aber unnötig; Entscheidung nach Serverbefund. |
| `--forwarded-allow-ips` | Nur Traefik darf `X-Forwarded-*` setzen | Sonst könnte ein Client die eigene IP im Audit fälschen. Wert `TRUSTED_PROXY_CIDR` = Subnetz des Traefik-Netzes aus `docker network inspect`. |
| `$$(` in `command` und `healthcheck` | Doppeltes Dollarzeichen | Compose interpoliert `$`; `$$` übergibt ein literales `$` an die Shell im Container. |
| Worker-Healthcheck über Heartbeat-Datei | Der Worker-Prozess schreibt alle 30 s `/tmp/heartbeat` | Stackneutral, kostet keine Verbindung zur Queue, erkennt hängende Prozesse. Beat gleichermaßen. |
| `stop_grace_period` beim Worker | Konfigurierbar (`WORKER_STOP_GRACE`) | Ein OCR-Chunk soll beim Deployment zu Ende laufen; danach übernimmt der Sweeper aus Pipeline E Abschnitt 10.4 ohnehin unfertige Jobs. |
| `OMP_THREAD_LIMIT=1` | Ein OCR-Prozess belegt genau einen Kern | Pipeline E Abschnitt 1.2, sonst überzeichnet Tesseract die Kerne. |
| `redis` mit Passwort, `noeviction`, AOF | Kein stiller Verlust von Aufträgen | Redis transportiert nur Job-IDs; bei vollem Speicher soll er Fehler melden statt Aufträge zu verwerfen. Passwort als Tiefenschutz auch im internen Netz. Hinweis: Die Lizenzlage von Redis hat sich in den letzten Jahren geändert; zum Umsetzungszeitpunkt prüfen, ob das offizielle Image oder ein kompatibler Fork (z. B. Valkey) verwendet wird. Für die Anwendung ist das transparent. |
| `db` mit `skip-name-resolve`, striktem SQL-Modus, utf8mb4 | Datenmodell D Abschnitt 1 | Umlaute in Ordnernamen (`05_Eigentümerakte`) und Namen verlangen utf8mb4. |
| `MARIADB_AUTO_UPGRADE` | Systemtabellen werden bei Minor-Anhebung angepasst | Vermeidet manuellen `mariadb-upgrade`. Vor Major-Anhebungen trotzdem Dump und Test. |
| `backup` liest Nutzdaten nur lesend (`:ro`) | Sicherungsprozess kann Nutzdaten nicht beschädigen | Einziger Schreibpfad ist `/backup`. |
| `classifier` als Profil | Startet nur mit `docker compose --profile classifier up -d` | CR Abschnitt 7 optional; Pipeline E aktiviert ihn erst bei unzureichender Trefferquote des linearen Modells. |
| `cpu_shares` | `web` 1024, `db` 768, `worker-io` 512, `worker` 256, `beat` 256 | Gewichte wirken nur bei Konkurrenz; die harten Limits stehen in `deploy.resources.limits`. Prüfen, ob die installierte Compose-Version `cpu_shares` neben `deploy` akzeptiert (`docker compose config`). |

Prüfung der Datei vor dem ersten Start (auf dem Server, nach Anlage der `.env`):

```bash
docker compose config --quiet && echo "Compose-Datei gültig"
docker compose config | grep -A3 'limits:' | head -40      # Limits sind aufgelöst
docker network inspect "$TRAEFIK_NETWORK" >/dev/null && echo "externes Netz vorhanden"
```

## 4. Konfiguration: `.env.example` und Secrets

### 4.1 Grundsatz

- `.env.example` liegt im Repository und enthält alle Schlüssel ohne Werte (Ausnahme: unkritische Standardwerte, die den Betrieb erklären).
- `.env` liegt nur auf dem Server unter dem Checkout-Verzeichnis, Rechte 0600, Eigentümer der Deploy-Nutzer. Sie steht in `.gitignore`.
- Geheimnisse liegen nie in `.env`, sondern als Dateien unter `/srv/objektakte/secrets/` und werden über den Compose-Block `secrets:` als `/run/secrets/<name>` in die Container gereicht. Die Anwendung liest `*_FILE`-Variablen.
- Fachliche Konfiguration zur Laufzeit (Ordnernamen, Schwellwerte, Präfix-Mappings, Provider-Auswahl) liegt in `app_settings` (Datenmodell D Abschnitt 4.2), nicht in `.env`. `.env` enthält nur, was vor dem Start feststehen muss.

### 4.2 `.env.example`

```dotenv
# ------------------------------------------------------------------
# Objektakte: Umgebungskonfiguration (Vorlage). Nach .env kopieren,
# Werte eintragen. Keine Geheimnisse hier, siehe Abschnitt 4.3.
# ------------------------------------------------------------------

# --- Deployment ---
IMAGE_TAG=                      # wird von scripts/deploy.sh gesetzt (Git-SHA, kurz)
APP_UID=10001                   # UID der Anwendungscontainer, muss zu chown in /srv/objektakte passen
TZ=Europe/Berlin
APP_DOMAIN=uebernahme.muellerhv.de
APP_ENV=production              # production | staging | development
APP_BASE_URL=https://uebernahme.muellerhv.de

# --- Traefik (Werte aus dem Serverbefund, Abschnitt 1.5) ---
TRAEFIK_NETWORK=                # Name des externen Docker-Netzwerks von Traefik
TRAEFIK_ENTRYPOINT=             # Name des HTTPS-Entrypoints (Port 443)
TRAEFIK_ENTRYPOINT_INSECURE=    # Name des HTTP-Entrypoints (Port 80), nur fuer Redirect-Router
TRAEFIK_CERTRESOLVER=           # Name des Let's-Encrypt-Resolvers
TRAEFIK_REDIRECT_ROUTER=        # true, wenn Traefik keinen globalen Redirect hat
TRUSTED_PROXY_CIDR=             # Subnetz des Traefik-Netzes, aus docker network inspect

# --- Image-Tags (aktuelle LTS bzw. stabile Version zum Umsetzungszeitpunkt pruefen) ---
MARIADB_TAG=
REDIS_TAG=

# --- Datenbank (nicht geheim) ---
DB_NAME=objektakte
DB_USER=app_rw
DB_BACKUP_USER=app_backup
DB_MAX_CONNECTIONS=             # Formel Abschnitt 5: GUNICORN_WORKERS + OCR_PROCESSES + IO_CONCURRENCY + 10
DB_INNODB_BUFFER_POOL=          # Formel Abschnitt 5: 0,5 x DB_MEM, z. B. 1G

# --- Prozessdimensionierung (Formel Abschnitt 5, aus nproc und free -h) ---
GUNICORN_WORKERS=               # ANNAHME 3, verifizieren
GUNICORN_TIMEOUT=120
OCR_PROCESSES=                  # P = max(1, C-1)
IO_CONCURRENCY=                 # ANNAHME 4, Drive-Quota beachten
WORKER_MAX_TASKS_PER_CHILD=50
WORKER_MAX_MEMORY_PER_CHILD_KB= # Formel: m_ocr in KB, z. B. 786432 fuer 0,75 GiB
WORKER_STOP_GRACE=120s

# --- Ressourcenlimits (Formel Abschnitt 5) ---
WEB_CPUS=
WEB_MEM=
WORKER_CPUS=
WORKER_MEM=
WORKER_IO_CPUS=
WORKER_IO_MEM=
BEAT_CPUS=
BEAT_MEM=
DB_CPUS=
DB_MEM=
REDIS_CPUS=
REDIS_MEM=
REDIS_MAXMEMORY=                # knapp unter REDIS_MEM, z. B. 200mb bei 256M
BACKUP_CPUS=
BACKUP_MEM=
CLASSIFIER_CPUS=
CLASSIFIER_MEM=

# --- Logging ---
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_MAX_SIZE=20m
LOG_MAX_FILE=5

# --- Speicherplatz ---
DISK_RESERVE_GB=                # Ingest stoppt, wenn weniger frei ist (Stack A Abschnitt 3.3)

# --- Backup ---
BACKUP_CRON=30 2 * * *          # taeglich 02:30 Serverzeit
BACKUP_RETENTION_DAYS=          # Auftraggeber legt fest, Vorschlag 30
BACKUP_MAX_AGE_HOURS=26         # Healthcheck rot, wenn die letzte Sicherung aelter ist
OFFSITE_ENABLED=false           # true erst nach Entscheidung ueber Zielort (Abschnitt 7.6)

# --- Google Drive und OAuth ---
GOOGLE_CLIENT_ID=
GOOGLE_REDIRECT_URI=https://uebernahme.muellerhv.de/auth/google/callback
GOOGLE_LOGIN_REDIRECT_URI=https://uebernahme.muellerhv.de/auth/google/login/callback
DRIVE_ACCOUNT_EMAIL=ablage@muellerhv.de
DRIVE_SCOPES=https://www.googleapis.com/auth/drive
GOOGLE_LOGIN_ENABLED=false      # optionaler Workspace-Login
GOOGLE_LOGIN_HOSTED_DOMAIN=muellerhv.de

# --- KI-Provider (Endpunkt und Region konfigurierbar, CR 0.1) ---
AI_PRIMARY_PROVIDER=            # openai | anthropic
AI_FALLBACK_PROVIDER=           # anthropic | openai | none
OPENAI_BASE_URL=                # EU-Endpunkt eintragen, sobald vom Anbieter bestaetigt
OPENAI_MODEL=
OPENAI_TIMEOUT_S=60
OPENAI_MONTHLY_BUDGET_EUR=
ANTHROPIC_BASE_URL=             # EU-Endpunkt eintragen, sobald vom Anbieter bestaetigt
ANTHROPIC_MODEL=
ANTHROPIC_TIMEOUT_S=60
ANTHROPIC_MONTHLY_BUDGET_EUR=

# --- E-Mail (Alarmierung optional, Abschnitt 9.5) ---
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_FROM=
ALERT_EMAIL_TO=
ALERTS_ENABLED=false

# --- Sitzungen und Login (Abschnitt 12.4) ---
SESSION_IDLE_MINUTES=480        # ANNAHME 8 h, durch Auftraggeber bestaetigen
SESSION_ABSOLUTE_HOURS=12
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_MINUTES=15
```

### 4.3 Secrets anlegen

Dateien unter `/srv/objektakte/secrets/`, je eine Zeile ohne Zeilenumbruch am Ende (`printf`, nicht `echo`). Die kryptografischen Schlüssel werden zufällig erzeugt; API-Schlüssel und Client-Secret stammen aus den Anbieterkonsolen.

| Datei | Inhalt | Erzeugung |
|---|---|---|
| `db_root_password` | Root-Passwort MariaDB (nur Initialisierung und Wartung) | `openssl rand -base64 32` |
| `db_app_password` | Passwort des Anwendungsnutzers `app_rw` | `openssl rand -base64 32` |
| `db_backup_password` | Passwort des Sicherungsnutzers `app_backup` | `openssl rand -base64 32` |
| `redis_password` | Redis `requirepass` | `openssl rand -base64 32` |
| `app_secret_key` | Framework-Schlüssel für Sitzungen und CSRF | `openssl rand -base64 48` |
| `iban_key` | AES-256-Schlüssel IBAN, Version 1 | `openssl rand -base64 32` |
| `iban_hmac_key` | HMAC-Schlüssel `iban_hash` | `openssl rand -base64 32` |
| `token_key` | AES-256-Schlüssel OAuth-Tokens | `openssl rand -base64 32` |
| `totp_key` | AES-256-Schlüssel TOTP-Geheimnisse | `openssl rand -base64 32` |
| `google_client_secret` | OAuth-Client-Secret aus der Google Cloud Console | Abschnitt 10 |
| `openai_api_key` | API-Schlüssel OpenAI | Anbieterkonsole |
| `anthropic_api_key` | API-Schlüssel Anthropic | Anbieterkonsole |
| `smtp_password` | SMTP-Passwort für Alarmierung (leer, wenn deaktiviert) | Mailanbieter |
| `backup_age_recipient` | Öffentlicher Schlüssel für die Backup-Verschlüsselung (age) | Abschnitt 7.6, privater Schlüssel liegt nicht auf dem Server |
| `readyz_token` | Bearer-Token für `/readyz/` (Abschnitt 9.4, Smoke-Test in `deploy.sh`) | `openssl rand -hex 32` |

```bash
cd /srv/objektakte/secrets
for n in db_root_password db_app_password db_backup_password redis_password iban_key iban_hmac_key token_key totp_key readyz_token; do
  [ -f "$n" ] || printf '%s' "$(openssl rand -base64 32)" | sudo tee "$n" >/dev/null
done
[ -f app_secret_key ] || printf '%s' "$(openssl rand -base64 48)" | sudo tee app_secret_key >/dev/null
sudo chmod 600 /srv/objektakte/secrets/*; sudo chown root:root /srv/objektakte/secrets/*
ls -l /srv/objektakte/secrets
```

Die Datenbank-Initialisierung (`docker/db/init/01_users.sql`) legt den Nutzer `app_backup` mit Leserechten an; das Passwort wird beim ersten Start aus `/run/secrets/db_backup_password` gelesen (kleines Shell-Skript im Init-Verzeichnis, weil das MariaDB-Image nur Root- und App-Passwort nativ aus Dateien liest). Mindestrechte für den Dump: `SELECT`, `SHOW VIEW`, `TRIGGER`, `LOCK TABLES`, `EVENT` (zum Umsetzungszeitpunkt gegen die Dokumentation des Dump-Werkzeugs prüfen). Der Nutzer `app_rw` erhält auf `audit_events` und `iban_access_log` nur `INSERT` und `SELECT` (Datenmodell D Abschnitt 8.3 und 10.3).

Sicherung der Schlüssel außerhalb des Servers: Die vier Verschlüsselungsschlüssel und das Root-Passwort werden zusätzlich im Passwortmanager der Geschäftsführung hinterlegt. Ohne sie sind IBAN-Chiffrate, OAuth-Tokens und TOTP-Geheimnisse in einer wiederhergestellten Sicherung unbrauchbar (Abschnitt 11.5).

## 5. Ressourcenlimits als Formel

Eingangsgrößen aus dem Serverbefund: C = Kerne, M = RAM in GiB, D = freie Platte in GiB. Keine dieser Größen ist bekannt (Befund Abschnitt 2); die Formel wird nach der Messung ausgefüllt und das Ergebnis in `.env` eingetragen.

| Variable | Formel | Herleitung |
|---|---|---|
| `OCR_PROCESSES` (P) | `max(1, C-1)` | CR Abschnitt 7: gemessene Kerne minus 1. Bei C ≤ 4 im Performance-Test zusätzlich P = C-2 vergleichen, weil `web`, `db` und `redis` sonst um einen Kern konkurrieren. |
| `WORKER_CPUS` | `P` | Hartes Limit, damit der Worker nie mehr als P Kerne belegt. |
| `WORKER_MEM` | `P × m_ocr + 0,5 GiB` | ANNAHME: m_ocr = 0,75 GiB je OCR-Prozess (ocrmypdf plus Tesseract auf einer A4-Seite bei 300 dpi). Verifikation: `docker stats` und cgroup `memory.peak` während des Performance-Laufs; der Messwert ersetzt die Annahme. |
| `WORKER_MAX_MEMORY_PER_CHILD_KB` | `m_ocr in KB` | Kindprozess wird nach Überschreiten recycelt, bevor das Container-Limit greift. |
| `WORKER_IO_CPUS`, `WORKER_IO_MEM` | `0,5`, `0,5 GiB` | Netzwerkarbeit, `IO_CONCURRENCY` Threads. ANNAHME, verifizieren. |
| `WEB_CPUS`, `WEB_MEM` | `1,0`, `1 GiB` | ANNAHME: `GUNICORN_WORKERS` = 3 synchrone Worker je 150 bis 250 MiB (Stack A Abschnitt 3.3). Verifikation `docker stats`. |
| `DB_CPUS`, `DB_MEM` | `1,0`, `max(1 GiB, 0,15 × M)` | Buffer-Pool `DB_INNODB_BUFFER_POOL = 0,5 × DB_MEM`. |
| `DB_MAX_CONNECTIONS` | `GUNICORN_WORKERS + P + IO_CONCURRENCY + 10` | Jeder Prozess hält höchstens eine Verbindung; Reserve für Migration, Backup, Shell. |
| `REDIS_CPUS`, `REDIS_MEM`, `REDIS_MAXMEMORY` | `0,5`, `256 MiB`, `REDIS_MEM minus 56 MiB` | Redis transportiert nur IDs. |
| `BEAT_CPUS`, `BEAT_MEM` | `0,25`, `128 MiB` | Zeitgeber ohne Last. |
| `BACKUP_CPUS`, `BACKUP_MEM` | `0,5`, `256 MiB` | Dump und Tar nachts. |
| `CLASSIFIER_CPUS`, `CLASSIFIER_MEM` | `1,0`, `1,5 GiB` | Nur bei aktiviertem Profil; ANNAHME aus Pipeline E Abschnitt 3.3, verifizieren. |
| `DISK_RESERVE_GB` | `max(10, 0,1 × D)` | Ingest bricht ab, bevor die Platte voll ist. |
| `BACKUP_RETENTION_DAYS` | so, dass `Tage × tägliche Sicherungsgröße < 0,3 × D` | Sicherungsgröße nach dem ersten Objekt messen; Wert legt der Auftraggeber fest. |

Randbedingung: Summe aller `*_MEM` (ohne `classifier`) ≤ 0,85 × M. Der Rest bleibt für Host, Traefik und Seitencache. Ist die Bedingung verletzt, wird P schrittweise verringert und der gewählte Wert mit Begründung in `docs/betrieb/serverbefund.md` dokumentiert. Swap: falls der Server kein Swap hat, ist ein kleiner Swap (ANNAHME: 2 GiB) als Puffer gegen den OOM-Killer sinnvoll; das ist eine Host-Einstellung und Teil der Checkliste in Abschnitt 6.

Rechenbeispiel mit frei gewählten Werten, ausdrücklich keine Aussage über den VPS: C = 6, M = 12 GiB. P = 5, `WORKER_MEM` = 5 × 0,75 + 0,5 = 4,25 GiB, `DB_MEM` = max(1; 1,8) = 1,8 GiB, `WEB_MEM` = 1 GiB, `WORKER_IO_MEM` = 0,5 GiB, `REDIS_MEM` + `BEAT_MEM` + `BACKUP_MEM` = 0,625 GiB. Summe 8,175 GiB = 68 % von M, Bedingung erfüllt. `DB_MAX_CONNECTIONS` = 3 + 5 + 4 + 10 = 22.

Verifikation nach dem Performance-Test (CR Abschnitt 14): Seiten pro Minute, RAM-Spitze je Container (`docker stats --no-stream` im Intervall während des Laufs) und p95-Antwortzeit des Review Centers werden protokolliert; Limits werden danach angepasst und die `.env` versioniert in `docs/betrieb/serverbefund.md` dokumentiert (ohne Geheimnisse).

## 6. Host-Härtung (Checkliste mit Befehlen)

Gilt für Ubuntu 24.04 LTS (CR Abschnitt 0.1). Reihenfolge einhalten: erst den Schlüssel-Login prüfen, dann Passwort-Login abschalten, sonst sperrt man sich aus. Traefik und Docker bleiben unangetastet.

| Nr. | Maßnahme | Befehle | Prüfung |
|---|---|---|---|
| 1 | Eigener Deploy-Nutzer mit sudo, kein Arbeiten als root | `sudo adduser deploy && sudo usermod -aG sudo,docker deploy` | `id deploy` |
| 2 | SSH-Schlüssel hinterlegen und testen, bevor Passwort abgeschaltet wird | Auf dem Client: `ssh-copy-id deploy@187.124.23.80`; dann in zweiter Sitzung `ssh deploy@187.124.23.80` | Login ohne Passwortabfrage |
| 3 | SSH: nur Schlüssel, kein Root-Login | Datei `/etc/ssh/sshd_config.d/90-hardening.conf` mit Inhalt `PermitRootLogin no`, `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`, `MaxAuthTries 3`, `X11Forwarding no`; dann `sudo sshd -t && sudo systemctl reload ssh` | `sudo sshd -T \| grep -Ei 'permitrootlogin\|passwordauthentication'` zeigt `no` |
| 4 | UFW: nur 22, 80, 443 | `sudo ufw default deny incoming && sudo ufw default allow outgoing && sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable` | `sudo ufw status verbose` |
| 5 | Docker und UFW | Docker schreibt eigene iptables-Regeln, die UFW umgehen können. Da kein Dienst Host-Ports veröffentlicht, entsteht kein Loch. Kontrolle: `docker ps --format '{{.Names}} {{.Ports}}'` darf außer Traefik (80, 443) keine veröffentlichten Ports zeigen. | `sudo ss -tulpen \| grep LISTEN` zeigt nur 22, 80, 443 und lokale Dienste |
| 6 | Automatische Sicherheitsupdates | `sudo apt install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades`; in `/etc/apt/apt.conf.d/50unattended-upgrades` `Unattended-Upgrade::Automatic-Reboot "false"` belassen (Neustart bewusst planen) | `sudo unattended-upgrades --dry-run --debug` |
| 7 | Docker-Engine-Updates | Docker-Repository ist in unattended-upgrades nicht enthalten; monatlich `sudo apt list --upgradable \| grep docker` und geplantes Update mit anschließendem `docker compose ps` | Update-Protokoll in `docs/betrieb/wartung.md` |
| 8 | Zeitzone und Zeitsynchronisation | `sudo timedatectl set-timezone Europe/Berlin && sudo timedatectl set-ntp true` | `timedatectl` zeigt `System clock synchronized: yes` |
| 9 | Docker-Logrotation für alle Container | `/etc/docker/daemon.json`: `{"log-driver":"json-file","log-opts":{"max-size":"20m","max-file":"5"}}`; danach `sudo systemctl restart docker`. Achtung: der Neustart unterbricht Traefik kurz; mit dem Auftraggeber abstimmen. Unsere eigenen Dienste tragen die Rotation bereits in der Compose-Datei, dieser Schritt ist deshalb optional und betrifft Fremdcontainer. | `docker info --format '{{.LoggingDriver}}'` |
| 10 | Journal begrenzen | `/etc/systemd/journald.conf`: `SystemMaxUse=500M`; `sudo systemctl restart systemd-journald` | `journalctl --disk-usage` |
| 11 | Swap (falls keiner vorhanden) | `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' \| sudo tee -a /etc/fstab` | `free -h` zeigt Swap |
| 12 | Fail2ban für SSH (optional, geringer Aufwand) | `sudo apt install -y fail2ban` mit Standard-Jail `sshd` | `sudo fail2ban-client status sshd` |
| 13 | Dateirechte der Konfiguration | `chmod 600 .env`, `sudo chmod 700 /srv/objektakte/secrets`, `sudo chmod 700 /srv/objektakte/backup` | `ls -la` |
| 14 | Kein Passwort-Login für `deploy` per sudo ohne Passwort | Standard belassen (sudo fragt Passwort), kein `NOPASSWD` | `sudo -l` |
| 15 | Ungenutzte Dienste | `sudo ss -tulpen` prüfen, nicht benötigte Dienste deaktivieren (`systemctl disable --now <dienst>`) | erneutes `ss` |

Nach Abschluss wird der Ist-Zustand erneut mit den Lesebefehlen aus Abschnitt 1.4 erfasst und als Nachweis in `docs/betrieb/serverbefund.md` ergänzt.

## 7. Backup und Wiederherstellung

### 7.1 Umfang und Ablage

| Was | Wie | Ziel | Bemerkung |
|---|---|---|---|
| Datenbank | `mariadb-dump --single-transaction --routines --triggers --events --hex-blob` als Nutzer `app_backup`, gzip | `/srv/objektakte/backup/db/objektakte_<JJJJMMTT_HHMMSS>.sql.gz` | Konsistenter Stand ohne Tabellensperren (InnoDB). `--hex-blob` wegen der `VARBINARY`-Chiffrate. |
| Arbeitsverzeichnisse | `tar --create --gzip` je Verzeichnis `transit`, `ocr-cache`, `models` | `/srv/objektakte/backup/volumes/<name>_<JJJJMMTT_HHMMSS>.tar.gz` | `work/` wird nicht gesichert (Zwischenstand, reproduzierbar aus Drive und OCR). `redis/` wird nicht gesichert: die Queue enthält nur Job-IDs, der Zustand liegt in der DB; nach Wiederherstellung reiht der Sweeper offene Jobs erneut ein. |
| Konfiguration | Kopie von `.env` (ohne Secrets, enthält sie ohnehin nicht) und der Compose-Datei | `/srv/objektakte/backup/config/` | Secrets werden nicht in das Backup kopiert; sie werden getrennt verwahrt (Abschnitt 4.3). |
| Status | `status.json` mit Zeitpunkt, Dauer, Größen, Prüfsummen, Ergebnis | `/srv/objektakte/backup/status.json` | Wird von der Statusseite (Abschnitt 9.3) und vom Healthcheck gelesen. |

Zeitpunkt: `BACKUP_CRON`, Vorschlag 02:30 Serverzeit, weil dann in der Regel keine Objektverarbeitung läuft. Läuft dennoch ein Job, ist der Dump trotzdem konsistent; die Tar-Archive können sich ändernde Dateien in `transit/` enthalten (Tar meldet das, Rückgabewert 1 wird als Warnung protokolliert, nicht als Fehler). `ocr-cache/` ist inhaltsadressiert (Schlüssel SHA-256), Einträge werden nie verändert, nur ergänzt; ein Archiv ist daher immer in sich konsistent.

Aufbewahrung: `BACKUP_RETENTION_DAYS`, Löschung älterer Dateien nach erfolgreicher neuer Sicherung (`find -mtime +N -delete`), nie vorher. Der Wert wird vom Auftraggeber festgelegt (Vorschlag 30 Tage; die Aufbewahrungsfristen der Fachdaten sind davon unabhängig und liegen in `retention_policies`).

### 7.2 Container `backup`

`docker/backup.Dockerfile`: schlankes Basis-Image mit `mariadb-client`, `tar`, `gzip`, `age`, `crond`, `jq` (Paketnamen zum Umsetzungszeitpunkt prüfen). Entrypoint schreibt aus `BACKUP_CRON` eine Crontab und startet `crond` im Vordergrund. Kein Root nötig außer für `crond`; das Skript selbst läuft als unprivilegierter Nutzer mit Leserechten auf `/src/*` und Schreibrechten auf `/backup`.

`docker/backup/backup.sh` (Kern, gekürzt):

```bash
#!/bin/sh
set -eu
TS=$(date +%Y%m%d_%H%M%S); START=$(date +%s)
DBPW=$(cat "$DB_BACKUP_PASSWORD_FILE")
mkdir -p /backup/db /backup/volumes /backup/config
STATUS=ok; WARN=""

# 1. Datenbank
if ! MYSQL_PWD="$DBPW" mariadb-dump -h "$DB_HOST" -u "$DB_BACKUP_USER" \
     --single-transaction --routines --triggers --events --hex-blob "$DB_NAME" \
     | gzip -6 > "/backup/db/${DB_NAME}_${TS}.sql.gz.part"; then STATUS=failed; fi
mv "/backup/db/${DB_NAME}_${TS}.sql.gz.part" "/backup/db/${DB_NAME}_${TS}.sql.gz"
gzip -t "/backup/db/${DB_NAME}_${TS}.sql.gz" || STATUS=failed

# 2. Verzeichnisse
for d in transit ocr-cache models; do
  tar --create --gzip --file "/backup/volumes/${d}_${TS}.tar.gz.part" \
      --warning=no-file-changed -C /src "$d" || { rc=$?; [ "$rc" -eq 1 ] && WARN="$WARN $d:changed" || STATUS=failed; }
  mv "/backup/volumes/${d}_${TS}.tar.gz.part" "/backup/volumes/${d}_${TS}.tar.gz"
done

# 3. Aufraeumen nur nach Erfolg
if [ "$STATUS" = ok ]; then
  find /backup/db /backup/volumes -type f -mtime "+${BACKUP_RETENTION_DAYS}" -delete
fi

# 4. Pruefsummen und Status
( cd /backup && sha256sum db/*_${TS}.sql.gz volumes/*_${TS}.tar.gz ) > "/backup/checksums_${TS}.txt"
jq -n --arg ts "$TS" --arg st "$STATUS" --arg warn "$WARN" \
      --argjson dur $(( $(date +%s) - START )) \
      --argjson size "$(du -sb /backup | cut -f1)" \
      '{last_run:$ts,status:$st,warnings:$warn,duration_s:$dur,total_bytes:$size}' > /backup/status.json.tmp
mv /backup/status.json.tmp /backup/status.json
[ "$STATUS" = ok ]
```

Eigenschaften: Schreiben in `.part`-Dateien und atomares Umbenennen (kein halbes Archiv wird je als gültig gelesen), Integritätsprüfung des Dumps (`gzip -t`), Löschung alter Sicherungen nur nach Erfolg, Prüfsummen je Lauf, Statusdatei für Monitoring. Der Exit-Code landet im Container-Log; die Statusseite zeigt den letzten Status und das Alter.

Manueller Lauf (z. B. vor einer Migration): `docker compose exec backup /usr/local/bin/backup.sh`. Das Deployment-Skript ruft genau dies vor jeder Migration auf (Abschnitt 8.2).

### 7.3 Wiederherstellung auf leerer Instanz (Schritt für Schritt)

Voraussetzungen: Server nach Abschnitt 6 gehärtet, Docker und Traefik vorhanden, Verzeichnisse nach Abschnitt 2.1 angelegt, Secrets nach Abschnitt 4.3 wiederhergestellt (aus dem Passwortmanager, nicht aus dem Backup), `.env` aus `backup/config/` oder neu ausgefüllt, Sicherungsdateien nach `/srv/objektakte/backup/` kopiert (bei Offsite-Kopie zuvor entschlüsselt, Abschnitt 7.6).

```bash
# 1. Checkout der Version, die zum Backup passt (app_version steht in schema_migrations, siehe Dump)
git clone <repo> /opt/objektakte && cd /opt/objektakte
zcat /srv/objektakte/backup/db/objektakte_<TS>.sql.gz | grep -m1 -oE "INSERT INTO \`schema_migrations\`.*" | head -c 400
git checkout <tag_oder_sha_aus_app_version>
cp /srv/objektakte/backup/config/.env .env && chmod 600 .env
export IMAGE_TAG=$(git rev-parse --short HEAD)

# 2. Nur Datenbank und Queue starten, Anwendung noch nicht
docker compose build
docker compose up -d db redis
docker compose ps    # warten, bis db healthy ist

# 3. Dump einspielen (Root-Passwort aus Secret, Datenbank wurde vom Image leer angelegt)
zcat /srv/objektakte/backup/db/objektakte_<TS>.sql.gz | \
  docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" "$MARIADB_DATABASE"'

# 4. Verzeichnisse zurueckspielen (Rechte auf APP_UID)
for d in transit ocr-cache models; do
  sudo tar --extract --gzip --file /srv/objektakte/backup/volumes/${d}_<TS>.tar.gz -C /srv/objektakte
done
sudo chown -R 10001:10001 /srv/objektakte/{transit,ocr-cache,models}

# 5. Schema-Stand pruefen: Code-Version und schema_migrations muessen passen, sonst migrieren
docker compose run --rm --no-deps web app-migrate --check

# 6. Alle Dienste starten
docker compose up -d
docker compose ps      # alle healthy

# 7. Smoke-Test
curl -fsS https://uebernahme.muellerhv.de/healthz/ && echo OK
# Anmeldung im Browser, Objektliste vergleichen, Statusseite: Job-Zaehler, letzte Sicherung, Token-Status
```

Nacharbeiten nach der Wiederherstellung:
- OAuth: Die Tokens im Dump sind mit `TOKEN_KEY` verschlüsselt. Ist der Schlüssel identisch, funktioniert der Zugriff sofort; die Statusseite zeigt den Token-Status. Andernfalls Erstautorisierung nach Abschnitt 10.6 wiederholen.
- Offene Jobs: Der Sweeper (Pipeline E Abschnitt 10.4) setzt Jobs, die zum Dump-Zeitpunkt liefen, zurück und reiht sie neu ein. Doppelverarbeitung ist über `(object_id, sha256)` ausgeschlossen.
- Drive-Abbild: `drive_nodes` enthält Folder-IDs; der nächste Ordnerabgleich (CR Abschnitt 9) prüft sie gegen Drive und ergänzt, was seit dem Dump entstanden ist. Dateien, die nach dem Dump in Drive verschoben wurden, sind dort bereits richtig und werden über den Hash als bekannt erkannt.

### 7.4 Testnachweis

Die Wiederherstellung wird einmal vor Produktivstart (Meilenstein 1, Deployment-Test CR Abschnitt 14) und danach quartalsweise geprobt. Nachweis in `docs/betrieb/restore-protokoll.md` je Probe:

| Feld | Inhalt |
|---|---|
| Datum, Durchführender | |
| Verwendete Sicherung (Dateinamen, Prüfsummen) | |
| Zielumgebung | leere Instanz (zweiter VPS, lokale VM oder derselbe Server nach `docker compose down -v` und Leeren von `/srv/objektakte/db`) |
| Dauer bis Smoke-Test grün | |
| Vergleich | Anzahl Zeilen in `objects`, `units`, `owners`, `documents`, `review_cases` vor und nach; Anzahl Dateien in `ocr-cache`; Stichprobe: ein Dokument öffnen, eine Eigentümerakte anzeigen, ein Review-Fall bearbeiten |
| Abweichungen und Maßnahmen | |

### 7.5 Wiederherstellung einzelner Bestandteile

- Nur Datenbank auf einen Zeitpunkt: Schritte 2, 3, 5, 6. Vorher `docker compose stop web worker worker-io beat`, damit keine Schreibzugriffe laufen.
- Nur ein Verzeichnis (z. B. `models/` nach fehlerhaftem Nachtraining): Schritt 4 für dieses Verzeichnis, danach `docker compose restart worker worker-io`.
- Einzelne Tabelle: aus dem Dump extrahieren (`zcat ... | sed -n '/CREATE TABLE `<name>`/,/UNLOCK TABLES/p'`) und in eine Hilfsdatenbank einspielen, dann gezielt kopieren. Nur durch den Entwickler, mit vorherigem vollständigem Dump.

### 7.6 Offsite-Kopie (Empfehlung)

Ein Backup auf derselben Maschine schützt vor Bedienfehlern und Softwarefehlern, nicht vor Ausfall oder Kompromittierung des VPS. Empfehlung: tägliche Kopie der jeweils jüngsten Sicherung an einen Ort außerhalb des VPS. Anforderungen:

- Verschlüsselung vor dem Transport, weil Dumps Namen, Adressen und Kontaktdaten im Klartext enthalten. Vorgesehen: `age` mit öffentlichem Schlüssel in `backup_age_recipient`; der private Schlüssel liegt ausschließlich beim Auftraggeber (Passwortmanager), nicht auf dem Server. Damit kann der Server verschlüsseln, aber nicht entschlüsseln.
- Zielort: Entscheidung des Auftraggebers (Objektspeicher eines Anbieters mit AVV und EU-Region, zweiter Server, verschlüsselter Netzwerkspeicher). Nicht das Google-Drive-Konto `ablage@muellerhv.de`, weil sonst die Sicherung im selben Vertrauensbereich liegt wie die Nutzdaten und ein kompromittiertes OAuth-Token beides erreicht.
- Umsetzung: `rclone` im Backup-Image, Ziel als Remote in einer Konfigurationsdatei unter `/srv/objektakte/secrets/rclone.conf`, Aufruf am Ende von `backup.sh` bei `OFFSITE_ENABLED=true`; dafür erhält `backup` zusätzlich das Netz `egress`. Aufbewahrung am Zielort getrennt konfigurierbar.
- Nachweis: Die Wiederherstellungsprobe (Abschnitt 7.4) wird mindestens einmal aus der Offsite-Kopie durchgeführt, inklusive Entschlüsselung mit dem privaten Schlüssel.

## 8. Deployment und Rollback

### 8.1 Grundsätze

- Jedes Deployment ist ein Git-Commit auf dem Server, ein Image mit dem Git-SHA als Tag und ein Eintrag in `/srv/objektakte/deploy/tags.log`.
- Migrationen laufen als eigener Schritt vor `up -d`, nie automatisch beim Container-Start (Datenmodell D Abschnitt 13.1). Vor jeder Migration ein Dump.
- Es werden nur rückwärtskompatible Migrationen im laufenden Betrieb eingespielt: Das neue Schema muss vom zuvor laufenden Image fehlerfrei verwendet werden können (Muster Erweitern und Zusammenziehen, Datenmodell D Abschnitt 13.3 Regel 4). Dadurch braucht ein Image-Rollback keinen Schema-Rollback.
- Ausfallzeit: Sekunden während des Containerwechsels. Deployments außerhalb laufender Objektverarbeitung planen; läuft dennoch ein Objekt, beenden die Worker den aktuellen Chunk (`WORKER_STOP_GRACE`) und der Sweeper setzt fort.

### 8.2 `scripts/deploy.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR=/srv/objektakte/deploy
BRANCH=${1:-main}

# 1. Code holen
git fetch --tags origin
git checkout "$BRANCH" && git pull --ff-only origin "$BRANCH"
NEW_TAG=$(git rev-parse --short=12 HEAD)
CUR_TAG=$(cat "$DEPLOY_DIR/current" 2>/dev/null || echo none)
echo "Deployment $CUR_TAG -> $NEW_TAG"

# 2. Bauen (Tag = Git-SHA)
export IMAGE_TAG="$NEW_TAG"
docker compose build --pull

# 3. Compose-Datei validieren
docker compose config --quiet

# 4. Dump vor Migration (Pflicht, Datenmodell D 13.3 Regel 3)
docker compose exec -T backup /usr/local/bin/backup.sh || { echo "Dump fehlgeschlagen, Abbruch"; exit 1; }

# 5. Migrationen mit neuem Image, alte Container laufen weiter (rueckwaertskompatibel)
docker compose run --rm --no-deps web app-migrate

# 6. Container wechseln
docker compose up -d --remove-orphans

# 7. Auf Healthchecks warten (max. 3 min)
for i in $(seq 1 36); do
  UNHEALTHY=$(docker compose ps --format '{{.Name}} {{.Health}}' | grep -vc healthy || true)
  [ "$UNHEALTHY" -eq 0 ] && break; sleep 5
done
docker compose ps

# 8. Smoke-Test ueber Traefik (TLS, Redirect, Anwendung)
curl -fsS -o /dev/null -w 'healthz %{http_code} tls %{ssl_verify_result}\n' "https://${APP_DOMAIN:-uebernahme.muellerhv.de}/healthz/"
curl -fsS "https://${APP_DOMAIN:-uebernahme.muellerhv.de}/readyz/" -H "Authorization: Bearer $(cat /srv/objektakte/secrets/readyz_token 2>/dev/null || echo none)" | head -c 400; echo

# 9. Tags fortschreiben, Symlink previous/current
[ "$CUR_TAG" != none ] && echo "$CUR_TAG" > "$DEPLOY_DIR/previous"
echo "$NEW_TAG" > "$DEPLOY_DIR/current"
docker tag "objektakte/app:$NEW_TAG" objektakte/app:current
[ "$CUR_TAG" != none ] && docker tag "objektakte/app:$CUR_TAG" objektakte/app:previous
printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "$NEW_TAG" "$CUR_TAG" "$(whoami)" >> "$DEPLOY_DIR/tags.log"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$NEW_TAG/" .env

# 10. Alte Images aufraeumen, die letzten N behalten
KEEP=${IMAGE_KEEP:-5}
docker images 'objektakte/app' --format '{{.Tag}} {{.CreatedAt}}' | grep -vE '^(current|previous|dev) ' | sort -k2 -r | tail -n +$((KEEP+1)) | awk '{print $1}' | xargs -r -I{} docker rmi "objektakte/app:{}" || true
echo "Deployment $NEW_TAG abgeschlossen"
```

Hinweise: `app-migrate` ist ein Unterbefehl des Container-Entrypoints, der auf das Migrationswerkzeug des gewählten Stacks zeigt (bei Stack A `python manage.py migrate`, sonst z. B. Alembic). `--check` prüft ohne Änderung. Der Aufruf von `/readyz/` ist optional und nur mit einem Token erreichbar (Abschnitt 9.4). Schritt 9 schreibt `IMAGE_TAG` in `.env`, damit ein späteres `docker compose up -d` ohne Skript denselben Stand verwendet.

### 8.3 Rollback mit einem Befehl

```bash
scripts/rollback.sh
```

Inhalt:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY_DIR=/srv/objektakte/deploy
PREV=$(cat "$DEPLOY_DIR/previous")
CUR=$(cat "$DEPLOY_DIR/current")
echo "Rollback $CUR -> $PREV"
docker image inspect "objektakte/app:$PREV" >/dev/null   # Image muss lokal vorliegen
IMAGE_TAG="$PREV" docker compose up -d --no-build
echo "$CUR" > "$DEPLOY_DIR/previous"; echo "$PREV" > "$DEPLOY_DIR/current"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$PREV/" .env
printf '%s\t%s\t%s\t%s\tROLLBACK\n' "$(date -Is)" "$PREV" "$CUR" "$(whoami)" >> "$DEPLOY_DIR/tags.log"
docker compose ps
echo "Hinweis: Schema bleibt auf dem Stand des zurueckgenommenen Deployments (rueckwaertskompatibel). Schema-Rollback nur nach Abschnitt 8.4."
```

Die Umgebungsvariable `IMAGE_TAG` in der Shell hat Vorrang vor dem Wert in `.env`, deshalb reicht der eine Befehl. Das `previous`-Image bleibt durch Schritt 10 in `deploy.sh` immer erhalten (Tag `previous` ist von der Aufräumung ausgenommen).

### 8.4 Rollback einer Datenbankmigration

Regel: Ein Schema-Rollback ist die Ausnahme und nie Teil des Standard-Rollbacks. Er wird nur nötig, wenn eine Migration selbst fehlerhaft ist (z. B. falscher Index, falsche Default-Werte), nicht wenn der Anwendungscode fehlerhaft ist.

Ablauf:
1. Anwendung anhalten: `docker compose stop web worker worker-io beat`.
2. Dump: `docker compose exec -T backup /usr/local/bin/backup.sh`.
3. Genau eine Version zurück: `IMAGE_TAG=$(cat /srv/objektakte/deploy/current) docker compose run --rm --no-deps web app-migrate --down 1` (bei Stack A: `python manage.py migrate <app> <vorherige_migration>`). Jede Migration hat eine Undo-Datei bzw. eine Rückwärtsfunktion; ohne Undo keine Freigabe (Datenmodell D Abschnitt 13.3 Regel 1).
4. Image zurücksetzen, falls nötig: `scripts/rollback.sh`.
5. Starten: `docker compose up -d`, Smoke-Test.
6. Protokoll in `tags.log` und im Deployment-Protokoll.

Migrationen, die nicht rückrollbar sind (Verlust nach der Migration entstandener Daten), werden im Umsetzungsplan vorab benannt und brauchen eine Vorwärtskorrektur statt eines Rollbacks (Datenmodell D Abschnitt 13.4). In der CI wird jede Migration vorwärts und rückwärts auf leerer Datenbank ausgeführt.

### 8.5 Erstinstallation (Kurzform)

1. Serverbefund (Abschnitt 1), Host-Härtung (Abschnitt 6).
2. Verzeichnisse (Abschnitt 2.1), Secrets (Abschnitt 4.3), `.env` aus `.env.example` mit Werten aus dem Ergebnisblatt.
3. `git clone <repo> /opt/objektakte && cd /opt/objektakte`.
4. `scripts/deploy.sh main` (baut, migriert, startet, testet). Der erste Lauf legt über die Seeds Kategorien, Unterordner, Rollen und `app_settings` an.
5. Ersten Admin anlegen: `docker compose exec web app-create-admin --email <adresse>`; TOTP beim ersten Login einrichten.
6. Google-Verbindung nach Abschnitt 10.6.
7. Deployment-Test nach Abschnitt 14 durchführen und protokollieren.

## 9. Logging, Monitoring, Statusseite, Alarmierung

### 9.1 Strukturierte Logs

- Alle eigenen Dienste schreiben JSON-Zeilen nach stdout, eine Zeile je Ereignis. Pflichtfelder: `ts` (ISO 8601, UTC), `level`, `service` (`web`, `worker`, `worker-io`, `beat`, `backup`), `logger`, `msg`, `request_id` (web) bzw. `task_id` und `job_id` (worker), `object_id` und `document_id` wo vorhanden, `user_id` (nie E-Mail, nie Name), `duration_ms`.
- Ein zentraler Filter maskiert vor der Ausgabe IBAN-, Kontonummern- und BIC-Muster (Muster aus Pipeline E Abschnitt 5.4) sowie Tokens und Passwörter (Schlüsselnamen `token`, `secret`, `password`, `authorization`). Kein Dokumenttext im Log, nur Längen und Hashes.
- Zugriffslog von gunicorn ebenfalls als JSON; die Client-IP stammt aus `X-Forwarded-For` nur, wenn sie von `TRUSTED_PROXY_CIDR` gesetzt wurde.
- `db`, `redis` und `backup` loggen im Format ihrer Images; Rotation greift über den Docker-Treiber.

### 9.2 Docker-Logs und Rotation

- Treiber `json-file` mit `LOG_MAX_SIZE` und `LOG_MAX_FILE` je Dienst (Compose-Datei). Platzbedarf pro Dienst maximal `LOG_MAX_SIZE × LOG_MAX_FILE`, bei den Vorgabewerten 100 MB, für sieben Dienste 700 MB.
- Lesen: `docker compose logs -f --since 1h worker`, gefiltert mit `jq`: `docker compose logs --no-log-prefix worker | jq -c 'select(.level=="ERROR")'`.
- Aufbewahrung über die Rotation hinaus ist nicht vorgesehen; das fachliche Protokoll liegt in `audit_events`, `processing_job_events`, `ai_calls` und `iban_access_log` in der Datenbank und ist damit Teil des Backups.

### 9.3 Statusseite in der Anwendung

Erreichbar für angemeldete Nutzer unter `/status/` (Sachbearbeiter lesend, Admin mit Aktionen). Aktualisierung per Polling (ANNAHME: alle 10 s). Alle Kennzahlen sind Datenbankabfragen oder Dateilesungen, keine Docker-API-Zugriffe aus dem Container.

| Bereich | Kennzahl | Quelle |
|---|---|---|
| Objektverarbeitung | Fortschritt je Objekt: Dokumente gesamt, je Status (Pipeline E Zustandsautomat), Seiten gesamt, Seiten fertig, geschätzte Restzeit | `processing_runs`, `processing_jobs`, `documents`, `document_pages` |
| Durchsatz | Seiten pro Minute (gleitend über 10 min) für den laufenden Lauf, Seiten pro Minute des letzten abgeschlossenen Laufs | `processing_job_events` |
| Review | Offene Review-Fälle gesamt und je Objekt, davon älter als 7 Tage, davon mit Kandidatenliste (mehrere historische Eigentümer) | `review_cases` |
| Qualität | Anteil 06_Sonstiges je Objekt (Zielwert unter 5 %, CR Abschnitt 8), Anteil Stufe-3-Aufrufe je Objekt | `documents`, `document_classifications` (Abfrage Datenmodell D Abschnitt 12.4) |
| KI | Token-Verbrauch und Kosten je Objekt und Monat je Provider, aktiver Provider (primär oder Fallback), letzte Fehler | `ai_calls` |
| Google | Token-Status (`active`, `expired`, `revoked`), letzter erfolgreicher Refresh, Ablauf des Access-Tokens, Fehler in Folge, Tage seit Erstautorisierung (für den 8-Tage-Nachweis) | `oauth_tokens` |
| Backup | Zeitpunkt, Status, Dauer, Größe der letzten Sicherung, Alter in Stunden, Warnung bei Alter über `BACKUP_MAX_AGE_HOURS`, Offsite-Status | `/data/backup/status.json` (lesend in `web` gemountet) |
| Queue | Länge der Queues `cpu` und `io`, Anzahl Jobs in `RUNNING` mit veraltetem Heartbeat, letzter Sweeper-Lauf | Redis `LLEN`, `processing_jobs` |
| Dienste | Heartbeat-Alter von `worker`, `worker-io`, `beat` (schreiben ihren Heartbeat zusätzlich in eine Tabelle `service_heartbeats` oder als Redis-Schlüssel mit TTL) | Redis-Schlüssel `heartbeat:<service>` |
| Speicher | Freier Platz unter `/srv/objektakte` (aus der Sicht des `web`-Containers über `os.statvfs` auf `/data/transit`), Belegung `transit`, `ocr-cache`, `work` | Dateisystem |
| Konfiguration | Aktive Provider, Schwellwerte, Aufbewahrungsfristen mit Hinweis „durch Geschäftsführung und Steuerberater festzulegen", wenn leer | `app_settings`, `retention_policies` |

Aktionen des Admins auf der Statusseite: Sweeper manuell anstoßen, Google-Verbindung erneuern, Backup manuell auslösen (ruft über Redis einen Auftrag an `backup` ab, weil `web` nicht in den Backup-Container greifen soll; ANNAHME: einfacher ist ein Hinweis mit dem Befehl für die Shell, Entscheidung im Umsetzungsplan), Listen neu erzeugen.

### 9.4 Healthcheck-Endpunkte

| Endpunkt | Zweck | Prüfung | Zugriff |
|---|---|---|---|
| `GET /healthz/` | Lebenszeichen für Docker und Traefik | Prozess antwortet, keine Abhängigkeiten | öffentlich, Antwort nur `ok`, keine Details, keine Sitzung |
| `GET /readyz/` | Betriebsbereitschaft | DB erreichbar (einfaches `SELECT 1`), Redis `PING`, freier Platz über `DISK_RESERVE_GB`, Token-Status nicht `revoked`, Schema-Version passt zum Code | nur mit Bearer-Token aus `/srv/objektakte/secrets/readyz_token` oder als Admin; Antwort als JSON je Prüfung |
| Heartbeat-Datei | Docker-Healthcheck der Worker | Datei jünger als 2 min | containerintern |

Traefik nutzt den Docker-Healthcheck nicht direkt; ein unhealthy `web` wird von Docker neu gestartet (`restart: unless-stopped` greift bei Absturz, bei `unhealthy` ist ein Neustart nicht automatisch). Deshalb überwacht `beat` den eigenen `/readyz/`-Status und meldet (Abschnitt 9.5); ein automatischer Neustart bei `unhealthy` ist mit Compose allein nicht vorgesehen und wird nicht angenommen. Im Runbook steht der manuelle Befehl `docker compose restart web`.

### 9.5 Alarmierung per E-Mail (optional)

Nicht Teil des CR, aber mit geringem Aufwand über `beat` umsetzbar, wenn ein SMTP-Zugang vorliegt (`SMTP_*`, `ALERT_EMAIL_TO`, `ALERTS_ENABLED=true`). Prüfintervall 5 min, jede Bedingung wird höchstens einmal je 6 h gemeldet, Entwarnung wird gemeldet.

| Bedingung | Schwelle |
|---|---|
| Letzte Sicherung älter als `BACKUP_MAX_AGE_HOURS` oder Status `failed` | sofort |
| Token-Refresh zweimal in Folge fehlgeschlagen oder Status `revoked` | sofort |
| Heartbeat `worker` oder `worker-io` älter als 10 min bei nicht leerer Queue | sofort |
| Freier Platz unter `DISK_RESERVE_GB` | sofort |
| Fallback-Provider länger als 1 h aktiv | 1 h |
| Monatsbudget eines Providers zu 80 % ausgeschöpft | einmal |
| TLS-Zertifikat der Domain läuft in weniger als 14 Tagen ab (Prüfung per TLS-Verbindung aus `beat` auf `APP_DOMAIN`) | täglich |
| Ordnerabgleich meldet Dateianzahl vorher ungleich nachher | sofort |

## 10. Google OAuth: Schritt-für-Schritt-Anleitung

Durchführung durch den Auftraggeber als Workspace-Administrator. Bezeichnungen der Menüpunkte in der Google Cloud Console und im Admin-Console ändern sich gelegentlich; die Reihenfolge und die Inhalte bleiben. Die Anleitung wird nach Durchführung mit Bildschirmfotos (ohne Secrets) in `docs/betrieb/google-oauth.md` abgelegt.

### 10.1 Projekt anlegen

1. Als Administrator der Workspace-Organisation `muellerhv.de` in der Google Cloud Console anmelden (nicht mit einem privaten Google-Konto, sonst gibt es keinen Nutzertyp „Intern").
2. Neues Projekt anlegen, Name Vorschlag `hvm-objektakte`, Organisation `muellerhv.de` auswählen. Projekt-ID notieren (nur Doku).
3. Abrechnungskonto ist für die Drive-API nicht erforderlich; keines verknüpfen.

### 10.2 Drive API aktivieren

1. APIs und Dienste, Bibliothek, „Google Drive API" suchen, aktivieren.
2. Keine weiteren APIs. Insbesondere keine Gmail- oder Admin-APIs.

### 10.3 OAuth-Zustimmungsbildschirm

1. APIs und Dienste, OAuth-Zustimmungsbildschirm (in neueren Konsolen unter „Google Auth Platform", Branding und Zielgruppe).
2. Nutzertyp **Intern** wählen. Begründung (CR Abschnitt 0.1): Bei externen Apps im Testmodus verfällt der Refresh-Token nach sieben Tagen; interne Apps brauchen keine Verifizierung durch Google und die Tokens verfallen nicht durch den Testmodus.
3. App-Name `Objektakte Hausverwaltung Müller`, Support-E-Mail und Entwicklerkontakt: eine Adresse der Organisation (Entscheidung Auftraggeber, keine private Adresse).
4. Bereiche (Scopes): `https://www.googleapis.com/auth/drive` hinzufügen. Begründung für die Dokumentation: Der eingeschränkte Bereich `drive.file` erlaubt nur den Zugriff auf Dateien, die die App selbst angelegt oder die der Nutzer per Picker gewählt hat. Die Anwendung muss aber bestehende Objektordner finden, lesen, umbenennen (`05_Sonstiges` zu `06_Sonstiges`) und Dateien innerhalb bestehender Ordner verschieben (CR Abschnitt 9). Das geht nur mit dem vollen Drive-Bereich. Der Zugriff bleibt auf das technische Konto `ablage@muellerhv.de` beschränkt; die App sieht nur, was dieses Konto sieht.
5. Speichern. Bei internen Apps ist kein Veröffentlichungsstatus zu setzen.

### 10.4 OAuth-Client anlegen

1. APIs und Dienste, Anmeldedaten, „Anmeldedaten erstellen", „OAuth-Client-ID".
2. Anwendungstyp **Webanwendung**, Name `objektakte-web`.
3. Autorisierte JavaScript-Quellen: keine (der Ablauf läuft serverseitig).
4. Autorisierte Weiterleitungs-URIs (Vorschlag, muss exakt mit `.env` übereinstimmen):
   - `https://uebernahme.muellerhv.de/auth/google/callback` (Drive-Verbindung des technischen Kontos)
   - `https://uebernahme.muellerhv.de/auth/google/login/callback` (optionaler Workspace-Login der Mitarbeiter, nur wenn `GOOGLE_LOGIN_ENABLED=true`)
   Für eine Staging-Umgebung später eigene URIs ergänzen, nie `http://`.
5. Erstellen. Client-ID in `.env` als `GOOGLE_CLIENT_ID`, Client-Secret als Datei `/srv/objektakte/secrets/google_client_secret` (Abschnitt 4.3). Das Secret nicht per E-Mail oder Chat übertragen; auf dem Server direkt eintragen.

Empfehlung: ein Client für beide Zwecke reicht; getrennte Clients sind sinnvoll, wenn der Mitarbeiter-Login später andere Scopes (nur `openid email profile`) und eine andere Zielgruppe erhalten soll. Entscheidung im Umsetzungsplan.

### 10.5 Workspace-Admin-Console prüfen

1. Admin-Console, Sicherheit, Zugriffs- und Datenkontrolle, API-Steuerung (Bezeichnung prüfen).
2. Unter der Verwaltung des Drittanbieter-App-Zugriffs prüfen, ob interne Apps standardmäßig als vertrauenswürdig gelten. Falls der Zugriff auf Google-Dienste für nicht konfigurierte Apps eingeschränkt ist, die App `hvm-objektakte` anhand der Client-ID hinzufügen und als vertrauenswürdig markieren, mindestens für den Drive-Dienst.
3. Prüfen, ob für das Konto `ablage@muellerhv.de` eine Organisationseinheit mit abweichenden Richtlinien gilt (z. B. Drive deaktiviert, Freigabe eingeschränkt). Drive muss für dieses Konto aktiv sein.
4. Zwei-Faktor für `ablage@muellerhv.de` aktivieren (Sicherheitsschlüssel oder Authenticator beim Auftraggeber), Wiederherstellungsdaten hinterlegen. Das Konto ist die Wurzel des gesamten Datenbestands.

### 10.6 Erstautorisierung in der Anwendung

1. Als Admin in der Anwendung anmelden (E-Mail, Passwort, TOTP), Bereich Administration, „Google Drive verbinden".
2. Die Anwendung startet den Autorisierungsablauf mit `access_type=offline`, `prompt=consent`, `include_granted_scopes=false`, Zustandsparameter (`state`) und PKCE. Nur `prompt=consent` garantiert, dass Google einen Refresh-Token ausgibt, auch wenn das Konto der App früher schon zugestimmt hat.
3. Im Google-Dialog mit **`ablage@muellerhv.de`** anmelden, nicht mit dem persönlichen Konto. Die Anwendung prüft nach dem Callback die E-Mail des autorisierenden Kontos gegen `DRIVE_ACCOUNT_EMAIL` und lehnt jedes andere Konto ab (protokolliert in `audit_events`).
4. Die Anwendung speichert Refresh-Token und Access-Token verschlüsselt (`TOKEN_KEY`, Abschnitt 11) in `oauth_tokens` mit `storage_mode = db`, `refresh_obtained_at = jetzt`.
5. Wurzelordner ermitteln: Die Anwendung wandert den Pfad `Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten` ab, zeigt die gefundene Folder-ID mit Ordnername und Anzahl der Unterordner zur Bestätigung an und speichert sie nach Bestätigung in `app_settings` (`drive.root_folder_id`). Mehrdeutige Treffer werden zur Auswahl gestellt, nicht geraten.
6. Erster Lesetest: Liste der Objektordner (nur Namen und IDs) anzeigen, kein Schreiben. Danach ist der Ordnerabgleich im Dry-Run-Modus (CR Abschnitt 9.5) der nächste Schritt.

### 10.7 Refresh, Überwachung, Nachweis über 8 Tage

- Access-Tokens sind kurzlebig; die Google-Bibliothek erneuert sie mit dem Refresh-Token automatisch vor jedem Aufruf. Zusätzlich prüft ein Beat-Task stündlich mit einem günstigen Aufruf (Metadaten des Wurzelordners), ob das Token gültig ist, und schreibt `last_refresh_at`, `last_refresh_status`, `consecutive_failures` in `oauth_tokens`.
- Der Statusbereich zeigt Token-Status, letzten Refresh und Tage seit Erstautorisierung (Abschnitt 9.3). Bei zwei Fehlern in Folge oder Status `revoked` wird alarmiert (Abschnitt 9.5) und der Admin sieht die Aufforderung „Google-Verbindung erneuern".
- Nachweis über mindestens 8 Tage (CR Abschnitt 14): Tag 0 Erstautorisierung; täglich ein automatischer Lesezugriff (Beat) und mindestens ein fachlicher Lauf (Ordnerabgleich Dry-Run) ohne erneute Anmeldung; Tag 8 Export der Zeilen aus `oauth_tokens` (ohne Chiffrate) und der zugehörigen `audit_events` (`drive.token_refresh`, `drive.authorize`) als Tabelle in `docs/betrieb/oauth-nachweis.md`, Bildschirmfoto der Statusseite. Kriterium: kein Ereignis `drive.authorize` nach Tag 0, alle Refreshes `ok`.
- Zu vermeiden: wiederholte Erstautorisierungen (Google begrenzt die Anzahl gültiger Refresh-Tokens je Konto und Client; die ältesten verfallen still), Widerruf des App-Zugriffs in den Kontoeinstellungen von `ablage@muellerhv.de`, Änderung des Nutzertyps auf „Extern". Vor einem Wechsel des Client-Secrets die Auswirkung auf bestehende Refresh-Tokens prüfen und die Erneuerung der Verbindung einplanen.

### 10.8 Optionaler Workspace-Login der Mitarbeiter

- Nur für Nutzer, die der Admin zuvor in `users` angelegt hat; keine Selbstregistrierung. Abgleich über die verifizierte E-Mail und den `sub`-Anspruch (`users.google_subject`), nicht nur über die E-Mail.
- Parameter `hd=muellerhv.de` im Autorisierungsaufruf und serverseitige Prüfung des `hd`-Anspruchs im ID-Token; andere Domains werden abgelehnt.
- Scopes nur `openid email profile`. Kein Drive-Zugriff über Mitarbeiterkonten; die Drive-Verbindung läuft ausschließlich über `ablage@muellerhv.de`.
- TOTP bleibt auch nach Google-Login Pflicht (Abschnitt 12.3), damit die Sicherheit der Anwendung nicht von der 2FA-Richtlinie des Workspace abhängt. Alternative zur Entscheidung: Google-Login gilt als zweiter Faktor, wenn die Workspace-Richtlinie 2FA für alle erzwingt (Offene Frage).

## 11. Verschlüsselung und Schlüsselverwaltung

### 11.1 Was verschlüsselt wird

| Datum | Spalte | Schlüssel | Klartext sichtbar |
|---|---|---|---|
| IBAN von Eigentümern und Mietern | `owners.iban_encrypted`, `tenants.iban_encrypted` | `IBAN_KEY` | nie in der UI, nie in Listen (nur `iban_last4`), nie in Logs oder Prompts |
| IBAN-Abgleichswert | `iban_hash` (HMAC-SHA256) | `IBAN_HMAC_KEY` | kein Klartext, nur Gleichheitsvergleich Dokument gegen Stammdaten |
| Google-Tokens | `oauth_tokens.access_token_encrypted`, `refresh_token_encrypted` | `TOKEN_KEY` | nie; nur der Prozess im Worker und Web entschlüsselt im Speicher |
| TOTP-Geheimnisse | `users.totp_secret_encrypted` | `TOTP_KEY` | nie; Anzeige nur einmal bei Einrichtung als QR-Code |
| Wiederherstellungscodes TOTP | `users.totp_recovery_hashes` | keiner (Hash wie Passwort) | nie |
| Passwörter | Hash mit speicherhartem Verfahren (Argon2id oder bcrypt, Bibliothek prüfen) | keiner | nie |

Warum getrennte Schlüssel je Zweck: Eine Rotation oder ein Verlust betrifft nur einen Datentyp; ein Schlüssel für Tokens kann z. B. rotiert werden, ohne IBAN-Chiffrate anzufassen. Warum HMAC statt Suchen im Klartext: Der Abgleich einer im Dokument erkannten IBAN mit den Stammdaten braucht nur Gleichheit; HMAC mit geheimem Schlüssel verhindert, dass aus dem Hash die IBAN rekonstruiert wird (Raum der gültigen IBANs ist klein genug für Wörterbuchangriffe ohne Schlüssel).

### 11.2 Verfahren

- AES-256-GCM (authentifizierte Verschlüsselung) über die Kryptobibliothek des Stacks (bei Python `cryptography`, aktuelle stabile Version zum Umsetzungszeitpunkt prüfen). Alternative Fernet ist zulässig, bringt aber AES-128-CBC mit HMAC und eine eigene Zeitstempel-Semantik; GCM ist hier die klarere Wahl. Entscheidung: AES-256-GCM.
- Spaltenformat (Datenmodell D Abschnitt 10.4): `nonce (12 Byte) || ciphertext || tag (16 Byte)` als `VARBINARY`; Schlüsselversion in der Nachbarspalte `*_key_version`. Zusätzlich wird der Tabellen- und Spaltenname als zusätzliche authentifizierte Daten (AAD) mitgeführt, damit ein Chiffrat nicht in eine andere Spalte kopiert werden kann.
- Nonce zufällig je Verschlüsselung; nie wiederverwenden.
- Schlüsselmaterial: 32 Byte Zufall, Base64 in der Secret-Datei. Die Anwendung dekodiert beim Start und hält die Schlüssel nur im Speicher.
- Schlüsselversionen: Die Secret-Dateien tragen die Version im Namen (`iban_key` ist Version 1; ab Rotation `iban_key_v2`). Die Anwendung lädt alle vorhandenen Versionen, verschlüsselt immer mit der höchsten (`security.iban_key_version_current` in `app_settings`) und entschlüsselt mit der in der Zeile genannten.

### 11.3 Zugriff und Protokoll

- Entschlüsselung der IBAN nur durch Systemprozesse für definierte Zwecke (`rekey`, künftig ggf. `export` für SEPA-Dateien, falls fachlich gewünscht) und nie zur Anzeige in der Oberfläche. Jeder Entschlüsselungsvorgang schreibt `iban_access_log` (Datenmodell D Abschnitt 10.3). Damit ist `security.iban_decrypt_roles` in `app_settings` leer zu setzen; das Datenmodell D führt hierzu eine offene Frage, dieser Entwurf empfiehlt die Antwort „keine Rolle".
- Token-Entschlüsselung nur im Drive-Adapter. TOTP-Entschlüsselung nur bei der Prüfung des Codes.
- Der Datenbanknutzer `app_rw` hat keinen Zugriff auf die Secret-Dateien; die Datenbank allein (auch als Dump) enthält nur Chiffrate.

### 11.4 Schlüsselrotation (dokumentierter Ablauf)

Anlass: planmäßig (ANNAHME: jährlich, Entscheidung Auftraggeber), bei Verdacht auf Kompromittierung, bei Ausscheiden einer Person mit Serverzugang.

1. Neuen Schlüssel erzeugen: `printf '%s' "$(openssl rand -base64 32)" | sudo tee /srv/objektakte/secrets/iban_key_v2 >/dev/null && sudo chmod 600 ...`. Secret in der Compose-Datei ergänzen (`iban_key_v2`), `docker compose up -d` (Container lesen die neue Datei).
2. `security.iban_key_version_current = 2` setzen (Admin-Bereich, protokolliert). Ab jetzt werden neue Werte mit Version 2 verschlüsselt; alte bleiben lesbar.
3. Rekey-Job starten: `docker compose exec web app-rekey --purpose iban --to-version 2`. Der Job liest zeilenweise, entschlüsselt mit der Zeilenversion, verschlüsselt mit Version 2, schreibt Chiffrat und Version in einer Transaktion je Zeile, protokolliert `iban_access_log` mit `purpose = rekey`. Wiederaufnehmbar, idempotent (Zeilen mit Version 2 werden übersprungen).
4. Prüfen: `SELECT iban_key_version, COUNT(*) FROM owners WHERE iban_encrypted IS NOT NULL GROUP BY 1` liefert nur Version 2 (analog `tenants`).
5. Alten Schlüssel frühestens nach einem erfolgreichen Backup-Zyklus mit ausschließlich Version-2-Chiffraten entfernen (Secret aus Compose löschen, Datei in den Passwortmanager verschieben, dann vom Server löschen). Solange Sicherungen mit Version-1-Chiffraten in der Aufbewahrung sind, muss Version 1 im Passwortmanager verfügbar bleiben.
6. Gleiches Muster für `TOKEN_KEY` und `TOTP_KEY`. Für `IBAN_HMAC_KEY` ist die Rotation aufwendiger: alle `iban_hash`-Werte müssen aus dem entschlüsselten Klartext neu berechnet werden (gleicher Rekey-Job mit `--purpose iban_hmac`); für Eigentümer ohne gespeicherte IBAN (nur `iban_last4`) ist kein Hash vorhanden, das ist zulässig.
7. `APP_SECRET_KEY`-Rotation: alle Sitzungen werden ungültig; Nutzer melden sich erneut an. Ankündigen, außerhalb der Arbeitszeit.

### 11.5 Verlust eines Schlüssels

| Schlüssel verloren | Folge | Wiederherstellung |
|---|---|---|
| `IBAN_KEY` | IBAN-Chiffrate unlesbar; `iban_last4` und alle übrigen Daten intakt | IBAN aus Dokumenten oder Rückfrage neu erfassen; Chiffrate löschen |
| `IBAN_HMAC_KEY` | Abgleich Dokument gegen Stammdaten liefert keine Treffer mehr | Neuer Schlüssel, Rekey aus Klartext (setzt `IBAN_KEY` voraus) |
| `TOKEN_KEY` | Drive-Verbindung unbenutzbar | Erstautorisierung nach Abschnitt 10.6 wiederholen (wenige Minuten) |
| `TOTP_KEY` | Kein Nutzer kann sich anmelden | Admin-Wiederherstellung über Shell-Befehl `app-reset-totp --all`, jeder Nutzer richtet TOTP neu ein; protokolliert |
| `APP_SECRET_KEY` | Sitzungen ungültig | Neuer Schlüssel, erneute Anmeldung |

Deshalb: Alle Schlüssel zusätzlich im Passwortmanager der Geschäftsführung, Zugriff auf mindestens zwei Personen, Prüfung der Hinterlegung als Teil der quartalsweisen Wiederherstellungsprobe (Abschnitt 7.4).

### 11.6 Verschlüsselung ruhender Daten auf Volume-Ebene (nicht vorgesehen)

Eine zusätzliche Verschlüsselung des Datenbank-Volumes (MariaDB-Tablespace-Verschlüsselung oder LUKS auf dem Host) schützt gegen Zugriff auf abgezogene Datenträger, nicht gegen einen Angreifer mit Root-Zugriff auf den laufenden Server. Bei einem VPS liegt der Datenträger beim Anbieter; der Nutzen ist begrenzt, der Aufwand (Schlüsseleingabe beim Neustart oder Schlüssel auf derselben Maschine) widerspricht dem automatischen Hochfahren nach Neustart (CR Abschnitt 14). Nicht vorgesehen; Offsite-Sicherungen werden stattdessen vor dem Transport verschlüsselt (Abschnitt 7.6).

## 12. Rollen, Zugriff, Sitzungen, Audit

### 12.1 Rollen

Zwei Rollen nach CR Abschnitt 15: `admin` und `sachbearbeiter`. Rechte liegen als Berechtigungsschlüssel in `roles.permissions` (Datenmodell D Abschnitt 10.1) und werden in Views und Service-Funktionen geprüft, nicht nur in der Oberfläche. Eine dritte, rein lesende Rolle (z. B. für die Geschäftsführung oder einen Steuerberater) ist als Erweiterung ohne Schemaänderung möglich, aber nicht Teil dieses Entwurfs.

### 12.2 Rechtematrix

| Recht | Schlüssel | Admin | Sachbearbeiter | Bemerkung |
|---|---|---|---|---|
| Konfiguration ändern (Namensmuster, Unterstrukturen, Schwellwerte, Provider, Duplikat-Option) | `settings.write` | ja | nein | Jede Änderung mit Vorher und Nachher in `audit_events`; Sachbearbeiter sehen die Werte lesend |
| Aufbewahrungsfristen setzen und freigeben | `retention.approve` | ja | nein | Freigabe durch Geschäftsführung erforderlich (CR Abschnitt 15); Wert und Freigebender in `retention_policies` |
| Nutzer anlegen, sperren, Rolle ändern, TOTP zurücksetzen | `users.manage` | ja | nein | Kein Nutzer kann die eigene Rolle ändern oder sich selbst sperren |
| Löschläufe starten (nur mit freigegebener Frist) | `deletion.approve` | ja | nein | Vier-Augen-Prinzip als Vorschlag: zweiter Admin bestätigt; bei nur einem Admin Zeitverzögerung von 24 h mit Abbruchmöglichkeit |
| Objekte anlegen, bearbeiten, Ordnerabgleich starten (Dry-Run und Ausführung) | `objects.write` | ja | ja | Ausführung des Abgleichs protokolliert mit Dateianzahl vorher und nachher |
| Dokumente hochladen, Verarbeitung starten | `documents.ingest` | ja | ja | |
| Review Center: Fälle bearbeiten, Zielbereich wählen, Massenbearbeitung | `review.decide` | ja | ja | Jede Entscheidung in `review_decisions` und `audit_events` |
| Nachforderungen erzeugen und als Entwurf ausgeben | `demands.write` | ja | ja | Versand erfolgt nicht durch die Anwendung, nur Entwurf (Organisationsvorgabe) |
| Eigentümerakten sehen (Ordner, Dokumente, Stammdaten, Zuordnungen) | `owner_files.read` | ja | ja | CR Abschnitt 10: nur berechtigte Rollen; beide Rollen sind berechtigt, weil alle Sachbearbeiter alle Objekte bearbeiten. Eine Einschränkung je Objekt ist nicht vorgesehen |
| Mieterakten sehen | `tenant_files.read` | ja | ja | analog |
| Eigentümer- und Mieterstammdaten ändern | `masterdata.write` | ja | ja | Nur über Review-Bestätigung oder Formular mit Quelle; Änderung in `audit_events` und `field_provenance` |
| IBAN im Klartext anzeigen | keiner | **nein** | **nein** | Für niemanden in der Oberfläche. Nur `iban_last4`. Systemzugriffe nach Abschnitt 11.3 |
| Listen (PDF, Excel) erzeugen und herunterladen | `lists.generate` | ja | ja | Enthalten keine vollständige IBAN (CR Abschnitt 12a) |
| Statusseite lesen | `status.read` | ja | ja | |
| Statusseite: Aktionen (Sweeper, Google-Verbindung erneuern, Backup auslösen) | `status.operate` | ja | nein | |
| Audit einsehen (alle Nutzer, alle Aktionen) | `audit.read_all` | ja | nein | |
| Audit einsehen (eigene Aktionen) | `audit.read_own` | ja | ja | Transparenz gegenüber Mitarbeitern |
| KI-Aufrufe und Kosten einsehen | `ai.read` | ja | ja (nur Zähler) | Prompts und Antworten in `ai_calls` nur für Admin |
| Import von Eigentümer- und Mieterlisten hochladen und bestätigen | `imports.write` | ja | ja | Übernahme in Stammdaten nur nach Bestätigung im Review Center |
| Google-Drive-Verbindung herstellen, trennen | `drive.connect` | ja | nein | |

### 12.3 Anmeldung und zweiter Faktor

- Anmeldung mit E-Mail und Passwort. Passwortregeln: Mindestlänge 12 Zeichen (ANNAHME, Entscheidung Auftraggeber), Prüfung gegen eine Liste häufiger Passwörter, keine Wiederverwendung der letzten fünf. Hash mit Argon2id oder bcrypt.
- TOTP verpflichtend für jede Rolle. Nach dem ersten Login wird der Nutzer ohne aktiven zweiten Faktor auf die Einrichtungsseite geleitet und kann nichts anderes aufrufen. Einrichtung: QR-Code plus manueller Schlüssel, Bestätigung durch einen gültigen Code, Ausgabe von zehn Wiederherstellungscodes (einmalig anzeigen, als Hash speichern).
- Zurücksetzen des zweiten Faktors nur durch einen Admin nach Identitätsprüfung außerhalb des Systems (Telefon, persönlich); protokolliert. Der Admin selbst: zweiter Admin oder Shell-Befehl auf dem Server (Root-Zugang als letzter Faktor).
- Optional Google-Workspace-Login nach Abschnitt 10.8, TOTP bleibt Pflicht (Alternative als offene Frage).
- Ratenbegrenzung: `LOGIN_MAX_ATTEMPTS` Fehlversuche je Konto und je IP, dann `LOGIN_LOCKOUT_MINUTES` Sperre; Fehlversuche in `audit_events` (`auth.login_failed`, ohne Passwort).
- Selbstregistrierung deaktiviert. Passwort-Zurücksetzen per E-Mail-Link nur, wenn SMTP konfiguriert ist; sonst über den Admin.

### 12.4 Sitzungssicherheit

- Cookies `Secure`, `HttpOnly`, `SameSite=Lax`; Sitzungs-ID wird beim Login und beim TOTP-Erfolg erneuert.
- Leerlaufzeit `SESSION_IDLE_MINUTES` (ANNAHME 8 h), absolute Höchstdauer `SESSION_ABSOLUTE_HOURS` (12 h). Danach erneute Anmeldung.
- Erneute Eingabe des TOTP-Codes (Step-up) vor sensiblen Aktionen: Nutzerverwaltung, Konfigurationsänderung, Löschlauf, Google-Verbindung, Export der Eigentümerliste als Excel (enthält Kontaktdaten aller Eigentümer). Gültigkeit des Step-up: 15 min.
- CSRF-Schutz für alle Formulare, auch HTMX-Teilanfragen.
- Vertrauen in `X-Forwarded-*` nur von `TRUSTED_PROXY_CIDR`; die Anwendung erzeugt absolute URLs nur aus `APP_BASE_URL`, nie aus dem Host-Header.
- Sitzungen liegen in der Datenbank oder in Redis; ein Admin kann alle Sitzungen eines Nutzers beenden (Sperre wirkt sofort).
- Dateivorschau (PDF) wird von `web` als Stream mit `Content-Disposition: inline` und `X-Content-Type-Options: nosniff` ausgeliefert; nur nach Prüfung von `owner_files.read` bzw. `tenant_files.read`. Kein direkter Drive-Link an den Browser.

### 12.5 Audit

- Jede Aktion im Review Center, jede Stammdatenänderung, jede Konfigurationsänderung, jede Nutzerverwaltungsaktion, jeder Drive-Schreibzugriff, jeder Login und Fehlversuch, jede Token-Erneuerung, jeder Löschlauf, jede Listenerzeugung und jeder Export schreibt eine Zeile in `audit_events` mit `user_id` bzw. `actor_type = system`, Zeitstempel, `action`, `entity_type`, `entity_id`, `object_id`, Vorher und Nachher als JSON, `request_id`, IP-Adresse (CR Abschnitt 15).
- `audit_events` ist append-only; `app_rw` hat nur `INSERT` und `SELECT`. Änderungen am Protokoll sind technisch nur mit dem Root-Datenbanknutzer möglich und damit außerhalb der Anwendung.
- IBAN-Entschlüsselungen stehen zusätzlich in `iban_access_log`.
- Auswertung im Admin-Bereich: Filter nach Nutzer, Zeitraum, Objekt, Aktion; Export als CSV für die Geschäftsführung.
- Aufbewahrung des Audits: kein Löschen im Rahmen der Aufbewahrungsfristen der Fachdaten; Mitarbeiterdaten im Audit (Nutzer-ID, IP) sind personenbezogen, die Aufbewahrungsdauer wird vom Auftraggeber festgelegt (Offene Frage).

## 13. DSGVO und Compliance: Voraussetzungen des Auftraggebers

Die folgenden Punkte kann die Softwareentwicklung nicht ersetzen. Sie sind Freigabekriterium vor der Verarbeitung von Produktivdaten (Punkte 1 bis 3 vor dem ersten externen KI-Aufruf mit echten Dokumenten). Zuständig: Geschäftsführung, bei Bedarf mit Datenschutzberater und Steuerberater. Dieser Abschnitt ist eine Einschätzung aus technischer Sicht und keine Rechtsberatung.

| Nr. | Voraussetzung | Technische Entsprechung im System | Status |
|---|---|---|---|
| 1 | Auftragsverarbeitungsvertrag mit OpenAI und mit Anthropic abschließen (jeweils die Vertragsvariante für API-Nutzung); Kopie in der Verfahrensdokumentation | Provider werden erst nach Freigabe in `app_settings` aktiviert (`ai.provider_<name>.enabled`), Schalter protokolliert | offen |
| 2 | Datenresidenz EU: prüfen, welche EU-Endpunkte und Regionen beide Anbieter zum Umsetzungszeitpunkt anbieten, und diese verbindlich festlegen | `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` und Region je Provider in `.env` konfigurierbar; kein Aufruf an einen nicht konfigurierten Endpunkt | offen |
| 3 | Opt-out aus Training und Datenaufbewahrung beim Anbieter: schriftlich bestätigen lassen, welche Aufbewahrung von API-Eingaben gilt (Zero-Retention oder Frist) | Es werden nur bereinigte Textauszüge mit maskierter IBAN übermittelt (CR Abschnitt 7 und 10), Prompts und Antworten werden lokal in `ai_calls` protokolliert | offen |
| 4 | Google Workspace: Der bestehende Workspace-Vertrag deckt Drive als Auftragsverarbeitung ab; prüfen, dass die Nutzung durch die Anwendung darunter fällt und die Datenverarbeitungsregion des Workspace bekannt ist | Zugriff nur über das Konto `ablage@muellerhv.de` | prüfen |
| 5 | Verzeichnis der Verarbeitungstätigkeiten ergänzen: Zweck (digitale Objektübernahme und Aktenführung), Kategorien Betroffener (Eigentümer, Mieter, Beiräte, Mitarbeiter der Vorverwaltung, eigene Mitarbeiter), Datenkategorien (Kontaktdaten, Bankverbindung maskiert, Vertragsdaten, Zahlungsdaten), Empfänger (Google, OpenAI, Anthropic, Hosting IONOS), Löschfristen | Datenkategorien sind im Datenmodell D benannt | offen |
| 6 | Hosting: AVV mit dem VPS-Anbieter prüfen, Serverstandort dokumentieren | Server 187.124.23.80, Standort aus dem Vertrag | prüfen |
| 7 | Aufbewahrungsfristen je Dokumentkategorie durch Geschäftsführung und Steuerberater festlegen (handels- und steuerrechtliche Fristen, WEG-Unterlagen, Mietunterlagen) | `retention_policies` mit leeren Standardwerten und Hinweistext; keine automatische Löschung ohne gesetzten und freigegebenen Wert (CR Abschnitt 15) | offen |
| 8 | Technische und organisatorische Maßnahmen dokumentieren | Abschnitte 6, 11, 12 dieses Entwurfs sind die Vorlage | nach Umsetzung |
| 9 | Prüfen, ob eine Datenschutz-Folgenabschätzung erforderlich ist (automatisierte Klassifikation personenbezogener Dokumente mit externer KI, umfangreicher Bestand) | Klassifikation trifft keine Entscheidungen über Personen, sondern ordnet Dokumente zu; Review Center als menschliche Kontrolle | Einschätzung durch Berater |
| 10 | Informationspflichten gegenüber Eigentümern und Mietern prüfen (Datenschutzhinweise der Hausverwaltung um die Verarbeitung durch Dienstleister ergänzen) | keine | prüfen |
| 11 | Mitarbeiter über das Audit-Protokoll informieren (Protokollierung aller Aktionen mit Nutzer und Zeitstempel) | Abschnitt 12.5, eigene Aktionen sind für jeden Nutzer einsehbar | vor Produktivstart |
| 12 | Berechtigungskonzept freigeben (Abschnitt 12.2) und Nutzerliste festlegen | `roles`, `users` | vor Produktivstart |
| 13 | Verfahren für Auskunfts- und Löschersuchen Betroffener festlegen | Suche nach Eigentümer und Mieter über alle Objekte (CR Abschnitt 13); Löschlauf nur mit freigegebener Frist; Export der Daten einer Person als Funktion vorsehen | offen |
| 14 | Umgang mit Sicherungen: Aufbewahrungsdauer der Backups und Ort der Offsite-Kopie festlegen; Sicherungen enthalten personenbezogene Daten | `BACKUP_RETENTION_DAYS`, Verschlüsselung der Offsite-Kopie (Abschnitt 7.6) | offen |
| 15 | Zugriff auf Dokumente der Vorverwaltung: sicherstellen, dass die Übergabe der Unterlagen an die Hausverwaltung Müller GmbH die Verarbeitung deckt (Verwaltervertrag, Bestellung) | keine | fachlich |

Regel im System: Kein Wert für Aufbewahrungsfristen wird vorbelegt. Der Admin-Bereich zeigt je Kategorie den Hinweis „durch Geschäftsführung und Steuerberater festzulegen" bis ein Wert gesetzt und mit Nutzer und Zeitpunkt freigegeben ist. Ein Löschlauf ohne freigegebenen Wert ist technisch nicht auslösbar.

## 14. Deployment-Testplan (CR Abschnitt 14)

Alle Tests werden auf dem VPS durchgeführt, Ergebnisse in `docs/betrieb/deployment-test.md` mit Datum, Durchführendem, Befehlsausgaben und Bildschirmfotos.

| Nr. | Test | Schritte | Erwartung | Nachweis |
|---|---|---|---|---|
| T1 | Frischer Checkout | Neues Verzeichnis, `git clone`, `.env` aus Ergebnisblatt, Secrets vorhanden, `scripts/deploy.sh main` | Alle Dienste `healthy` innerhalb von 3 min (`docker compose ps`); `curl -I https://uebernahme.muellerhv.de/` liefert 200 oder 302 zur Anmeldung; Zertifikat gültig (`openssl s_client -connect uebernahme.muellerhv.de:443 -servername uebernahme.muellerhv.de </dev/null 2>/dev/null \| openssl x509 -noout -issuer -dates`, Aussteller Let's Encrypt, Gültigkeit in der Zukunft); HTTP auf Port 80 leitet auf HTTPS um | Ausgabe `docker compose ps`, `curl -I`, `openssl` |
| T2 | Kein Host-Port | `docker ps --format '{{.Names}} {{.Ports}}'`; `sudo ss -tulpen \| grep LISTEN` | Nur Traefik zeigt 80 und 443; kein Dienst des Projekts veröffentlicht Ports; `db` und `redis` sind nur im Netz `data` | Ausgaben |
| T3 | Isolation `data` | `docker compose exec db sh -c 'getent hosts example.org \|\| echo kein_dns'` und ein Verbindungsversuch nach außen aus `db` | Kein Weg ins Internet aus `db` und `redis` | Ausgabe |
| T4 | Serverneustart | Laufenden Verarbeitungslauf starten (Testobjekt mit einigen hundert Seiten), `sudo reboot`, nach 3 min prüfen | Alle Container laufen ohne manuellen Eingriff (`restart: unless-stopped`), Healthchecks grün, der Lauf wird fortgesetzt, keine doppelt verarbeiteten Dokumente (`SELECT sha256, COUNT(*) FROM documents WHERE object_id = ? GROUP BY 1 HAVING COUNT(*) > 1` leer), Sweeper-Lauf im Log | `docker compose ps`, Abfrage, Log-Auszug |
| T5 | Container-Abbruch Worker | `docker kill objektakte-worker-1` während OCR | Container startet neu, Chunk wird wiederholt, Dokument endet in DONE, keine Duplikate | Log, Abfrage |
| T6 | Backup und Wiederherstellung | Manueller Backup-Lauf; auf zweiter leerer Instanz (oder nach `docker compose down` und Leeren von `/srv/objektakte/db`) Wiederherstellung nach Abschnitt 7.3 | Zeilenzahlen der Kerntabellen identisch, Stichproben laut Abschnitt 7.4, Statusseite zeigt Token-Status `active` (bei identischem `TOKEN_KEY`) | Protokoll nach Abschnitt 7.4 |
| T7 | Rollback | Deployment eines Commits mit sichtbarer Änderung (z. B. Versionsanzeige), dann `scripts/rollback.sh` | Vorherige Version läuft innerhalb einer Minute, Healthchecks grün, `tags.log` enthält ROLLBACK-Zeile | Ausgabe, Bildschirmfoto |
| T8 | Migration vorwärts und rückwärts | Auf Kopie der Datenbank: `app-migrate`, dann `app-migrate --down 1`, dann erneut `app-migrate` | Fehlerfrei, `schema_migrations` konsistent | Ausgabe |
| T9 | OAuth 8 Tage | Erstautorisierung Tag 0 nach Abschnitt 10.6; täglicher Beat-Check und täglicher Dry-Run des Ordnerabgleichs | Über mindestens 8 Tage kein Ereignis `drive.authorize`, alle Refreshes `ok`, Statusseite zeigt Tage seit Autorisierung ≥ 8 | Export nach Abschnitt 10.7 |
| T10 | Ressourcenlimits | Während des Performance-Tests (10.000 Seiten): `docker stats --no-stream` alle 60 s in Datei | Kein Container über seinem Limit (kein OOM-Kill in `docker inspect --format '{{.State.OOMKilled}}'`), `web` p95 unter 2 s, Seiten pro Minute protokolliert | Messdatei, Bericht |
| T11 | Logging | `docker compose logs --no-log-prefix web \| head -5 \| jq .`; Suche nach IBAN-Mustern in allen Logs (`docker compose logs \| grep -E 'DE[0-9]{2}[0-9 ]{18,}'`) | Gültiges JSON je Zeile; Suche liefert keinen Treffer | Ausgaben |
| T12 | Sicherheitseinstellungen Host | Lesebefehle aus Abschnitt 1.4 nach Härtung | Root-Login `no`, Passwort-Login `no`, UFW aktiv mit 22, 80, 443, unattended-upgrades aktiv, Zeitzone Europe/Berlin | Ausgaben |
| T13 | Login und Rollen | Neuer Sachbearbeiter ohne TOTP: nach Login nur Einrichtungsseite erreichbar; Sachbearbeiter ruft Konfigurationsseite auf | Einrichtung erzwungen; Zugriff verweigert und `audit_events` enthält `auth.denied` | Bildschirmfoto, Abfrage |
| T14 | Healthcheck-Endpunkte | `curl https://uebernahme.muellerhv.de/healthz/`, `curl https://uebernahme.muellerhv.de/readyz/` ohne Token | `ok` ohne Details; `/readyz/` liefert 401 | Ausgaben |

Definition of Done für den Betriebsteil: T1 bis T14 bestanden und dokumentiert, Serverbefund abgelegt, Runbook `docs/betrieb/runbook.md` vorhanden (Störungsfälle: Token ungültig, Drive-Quota, Platte voll, Worker ohne Fortschritt, Fallback aktiv, Backup fehlgeschlagen, Zertifikat nicht erneuert), Wiederherstellungsprotokoll vorhanden.

## 15. Abweichungen vom CR-Wortlaut (Vorschläge, keine Entscheidungen)

| Nr. | CR-Vorgabe | Vorschlag | Begründung |
|---|---|---|---|
| 1 | Container `web`, `worker`, `queue`, `db`, optional `classifier` (Abschnitt 7) | Zusätzlich `worker-io`, `beat`, `backup`; Dienstname `redis` statt `queue` | Netzwerkarbeit blockiert keine OCR-Slots; periodische Aufgaben brauchen einen Zeitgeber; Backup ist im CR gefordert, aber nicht als Container benannt. Der Name `redis` beschreibt das Image; ein Wechsel zu einem kompatiblen Fork ändert nur `REDIS_TAG`. |
| 2 | „Zugangsdaten und API-Keys nur in `.env` oder Docker Secrets" | Ausschließlich dateibasierte Docker Secrets; `.env` ohne Geheimnisse | Kleinerer Leckpfad (`docker inspect`, `compose config`, Fehlerberichte). |
| 3 | „Persistente Daten in benannten Volumes oder unter `/srv/objektakte/`" | Beides kombiniert: benannte Volumes mit Bindung auf `/srv/objektakte/db` und `/srv/objektakte/redis` | Ein Ort für Backup und Plattenüberwachung. |
| 4 | „Token verschlüsselt in der DB oder als Docker Secret" | In der DB verschlüsselt (`oauth_tokens`), Schlüssel als Docker Secret | Der Refresh schreibt neue Access-Tokens; Docker Secrets sind zur Laufzeit nicht beschreibbar. |
| 5 | Redirect-URI `https://uebernahme.muellerhv.de/...` | `/auth/google/callback` und `/auth/google/login/callback` | Konkreter Vorschlag zur Eintragung in der Cloud Console. |
| 6 | Rollen Admin und Sachbearbeiter | Zusätzlich: IBAN-Klartext für keine Rolle in der UI, Step-up-TOTP vor sensiblen Aktionen, Vier-Augen- oder Wartefrist bei Löschläufen | Schutz der Bankdaten und Schutz vor Fehlbedienung bei kleinem Team. |
| 7 | Backup „in ein separates Verzeichnis" | `/srv/objektakte/backup` auf derselben Platte plus verschlüsselte Offsite-Kopie | Ein Verzeichnis allein schützt nicht vor Ausfall des VPS. |

## 16. Annahmen (Liste, jeweils mit Verifikation im Projekt)

| Nr. | ANNAHME | Verifikation |
|---|---|---|
| A1 | ANNAHME: Die Anwendungscontainer laufen mit UID 10001; die Bind-Mounts gehören dieser UID. | Festlegung im Dockerfile, Prüfung mit `docker compose exec web id`. |
| A2 | ANNAHME: Speicherbedarf je OCR-Prozess m_ocr = 0,75 GiB. | `docker stats` und cgroup `memory.peak` während des Performance-Tests; Messwert ersetzt Annahme in `.env`. |
| A3 | ANNAHME: `web` mit 3 gunicorn-Workern braucht rund 1 GiB. | `docker stats` im Betrieb mit 2 bis 5 Nutzern. |
| A4 | ANNAHME: `worker-io` mit 4 Threads braucht 0,5 GiB und 0,5 CPU. | `docker stats` während Drive-Uploads und KI-Aufrufen. |
| A5 | ANNAHME: `classifier` braucht 1,5 GiB und 1 CPU, falls aktiviert. | Benchmark im Container (Pipeline E). |
| A6 | ANNAHME: Ein Swap von 2 GiB ist als Puffer sinnvoll, falls der Server keinen hat. | `free -h` im Serverbefund; Entscheidung nach Messung. |
| A7 | ANNAHME: Backup-Zeitpunkt 02:30 Serverzeit liegt außerhalb der Objektverarbeitung. | Auslastungsmuster nach den ersten Objekten prüfen; `BACKUP_CRON` anpassen. |
| A8 | ANNAHME: Aufbewahrung der Backups 30 Tage ist ein sinnvoller Startwert. | Entscheidung Auftraggeber; Plattenverbrauch nach dem ersten Objekt messen. |
| A9 | ANNAHME: Statusseite aktualisiert alle 10 s per Polling ohne spürbare Last. | Messung der Antwortzeit während des Performance-Tests. |
| A10 | ANNAHME: Sitzungs-Leerlaufzeit 8 h, Höchstdauer 12 h, Passwortmindestlänge 12 Zeichen. | Entscheidung Auftraggeber vor Produktivstart. |
| A11 | ANNAHME: Planmäßige Schlüsselrotation jährlich. | Entscheidung Auftraggeber. |
| A12 | ANNAHME: Die installierte Compose-Version wertet `deploy.resources.limits` und `cpu_shares` außerhalb von Swarm aus. | `docker compose config` und `docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'` nach dem Start. |
| A13 | ANNAHME: Das MariaDB-Image des gewählten Tags unterstützt `MARIADB_*_FILE` und liefert `healthcheck.sh`. | Dokumentation des Image-Tags zum Umsetzungszeitpunkt. |
| A14 | ANNAHME: Traefik läuft in Version 2 oder 3 mit Docker-Provider ohne Swarm; Label-Syntax passt. | Serverbefund Abschnitt 1.3. |
| A15 | ANNAHME: Die Traefik-Entrypoint-Timeouts erlauben Uploads großer Dateien (mehrere hundert MB) ohne Abbruch. | Upload-Test mit großer Datei; bei Abbruch Chunked-Upload in der Anwendung oder Abstimmung der Traefik-Konfiguration mit dem Auftraggeber. |
| A16 | ANNAHME: Für Backup-Verschlüsselung und Offsite-Kopie sind `age` und `rclone` als Pakete im Backup-Image verfügbar. | Paketquellen zum Umsetzungszeitpunkt prüfen. |
| A17 | ANNAHME: Der Kreis der Nutzer bleibt bei Timo Müller plus 2 bis 5 Mitarbeitern; keine objektbezogenen Rechte nötig. | CR Abschnitt 15; bei Änderung Erweiterung des Rollenmodells. |

## 17. Offene Fragen an den Auftraggeber (gebündelt, nur echte Entscheidungen)

1. Offsite-Kopie der Sicherungen: gewünscht? Wenn ja, welcher Zielort (Objektspeicher eines Anbieters mit AVV in der EU, zweiter Server, Netzwerkspeicher) und wer verwahrt den privaten Entschlüsselungsschlüssel?
2. Aufbewahrungsdauer der Sicherungen in Tagen (Vorschlag 30) und Aufbewahrungsdauer des Audit-Protokolls (Mitarbeiterdaten).
3. Alarmierung per E-Mail: gewünscht? Wenn ja, welcher SMTP-Zugang (Workspace-Konto oder anderer Anbieter) und welche Empfängeradresse?
4. Zwei-Faktor bei Google-Workspace-Login: TOTP der Anwendung bleibt zusätzlich Pflicht (Empfehlung) oder Google-Login gilt als zweiter Faktor, sofern die Workspace-Richtlinie 2FA erzwingt?
5. Löschläufe: Vier-Augen-Prinzip mit zweitem Admin oder Wartefrist von 24 h mit Abbruchmöglichkeit? Wie viele Admin-Konten soll es geben (Empfehlung: zwei)?
6. Passwortmindestlänge, Sitzungsdauer und Rotationsintervall der Schlüssel: Vorschläge aus Abschnitt 16 bestätigen oder ändern.
7. Wer außer dem Entwickler erhält SSH-Zugang zum Server (Deploy-Nutzer, Schlüssel) und wer verwahrt die Kopie der Verschlüsselungsschlüssel im Passwortmanager (mindestens zwei Personen)?
8. Support- und Entwicklerkontakt für den OAuth-Zustimmungsbildschirm: welche Adresse der Organisation?
9. Ein OAuth-Client für Drive-Verbindung und Mitarbeiter-Login oder zwei getrennte Clients?
10. Soll die IBAN überhaupt vollständig (verschlüsselt) gespeichert werden, oder reicht `iban_last4`, solange die Anwendung keinen SEPA-Einzug erzeugt? Ohne vollständige Speicherung entfallen `IBAN_KEY`-Rotation und Zugriffsprotokoll auf Klartext, `iban_hash` für den Abgleich bleibt möglich, solange die IBAN beim Erfassen einmal vorliegt.
11. Zeitfenster für Deployments und den Neustart-Test des Servers (Unterbrechung im Sekunden- bis Minutenbereich).
12. Zeitpunkt für den 8-Tage-OAuth-Nachweis: Er setzt das angelegte Konto `ablage@muellerhv.de`, die OAuth-App und den DNS-Eintrag voraus; ab wann stehen diese bereit?
13. Darf für die Docker-Logrotation die `daemon.json` geändert und der Docker-Dienst einmal neu gestartet werden (kurze Unterbrechung von Traefik), oder bleibt es bei der Rotation nur für die eigenen Dienste?
