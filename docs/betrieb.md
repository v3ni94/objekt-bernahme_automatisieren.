# Betriebs- und Voraussetzungsdokument Objektübernahme (CR-05)

Stand: 10.09.2026. Status: Entwurf zur Freigabe, dauerhaft als Referenz im Repository.

Faktenbasis: CR-05 (`docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md`, Abschnitte 0.1, 7, 10, 14, 15) und die Befundakte. Grundlage der Ausarbeitung sind Fachentwurf G (Betrieb, Sicherheit) und Fachentwurf F (Drive, OAuth), konsolidiert nach den vier Prüfberichten und abgeglichen mit `docs/architektur.md` (Abschnitte 4, 9, 10) und `docs/umsetzungsplan.md` (Anhang B, Anhang D, Abschnitt 5). Wo dieses Dokument von einem Fachentwurf abweicht, gilt dieses Dokument; die Abweichungen sind in Anhang E benannt.

Geltungsbereich: Zielumgebung nach CR 0.1 (IONOS VPS, IP 187.124.23.80, Ubuntu 24.04 LTS, Docker Compose, vorhandener Traefik, Domain `uebernahme.muellerhv.de`, technisches Google-Konto `ablage@muellerhv.de`). Das Dokument beschreibt, was der Auftraggeber bereitstellen muss (Voraussetzungen) und wie der Entwickler den Betrieb aufsetzt (Anleitungen, Skripte, Tests).

Kennzeichnungen in diesem Dokument:

| Kennzeichnung | Bedeutung |
|---|---|
| `[C]`, `[M]`, `[D]`, `[TRAEFIK_NETWORK]` und andere Werte in eckigen Klammern | Werte, die erst auf dem Server ermittelt werden. Der Server war aus der Entwicklungsumgebung nicht erreichbar (Befund Abschnitt 2). Kein Wert in diesem Dokument ist eine Messung. |
| ANNAHME AB1 bis AB29 | Planungsgröße ohne Beleg, mit Angabe, wie sie im Projekt verifiziert wird. Liste in Anhang A. Der Nummernkreis AB gilt nur für dieses Dokument; `docs/architektur.md` zählt seine Annahmen A1 ff. ohne Bindestrich, der Umsetzungsplan A-01 ff. mit Bindestrich. |
| gilt bei Stack A | Zeile hängt am empfohlenen Stack (Python-Monolith: Django, HTMX, Celery, Redis, MariaDB). Bei anderem Stack ändern sich nur diese Zeilen, nicht die Struktur. |
| Versionsnummern | Werden nicht genannt. Für Images und Bibliotheken gilt: aktuelle LTS bzw. stabile Version zum Umsetzungszeitpunkt prüfen und in `.env` eintragen. |
| Beispiele | Immer synthetisch. Keine Namen, Adressen oder Objektnummern aus den Stammdaten. |

Hinweis zur Rechtslage: Abschnitt 8 ist eine Einschätzung aus technischer Sicht und keine Rechtsberatung. Vertrags-, datenschutz- und steuerrechtliche Fragen sind mit Rechtsanwalt, Datenschutzberater und Steuerberater zu klären.

---

## 0. Ergebnis und Empfehlung

| Nr. | Entscheidung | Begründung (kurz) |
|---|---|---|
| 1 | Acht Dienste in einer Compose-Datei: `web`, `worker` (Queue `ocr`), `worker-nlp` (Queue `classify`), `worker-io` (Queues `ai`, `io`, `lists`), `beat`, `redis`, `db`, `backup`; optional `classifier` über ein Compose-Profil. | CR 7 nennt `web`, `worker`, `queue`, `db`, optional `classifier`. Die zusätzlichen Dienste sind Beschluss B-13 des Umsetzungsplans (Frage F2 zur Bestätigung): Netzwerkarbeit belegt keine OCR-Prozesse, Modelle werden nicht in jedem OCR-Prozess geladen, periodische Aufgaben brauchen einen Zeitgeber, Backup ist im CR gefordert. |
| 2 | Zwei Build-Ziele aus einem Dockerfile: `web` ohne OCR-Binärdateien, `worker` mit Tesseract, ocrmypdf, Ghostscript und Modellen. | Beschluss B-44. Kleineres Angriffs- und Update-Profil für den öffentlich erreichbaren Dienst; identische Codebasis und Migrationen. |
| 3 | Drei Netze: `data` (Docker `internal: true`), `egress` (ausgehend), `[TRAEFIK_NETWORK]` (vorhanden, nur `web`). Kein Dienst veröffentlicht Host-Ports. | CR 0.1: Traefik ist der einzige öffentlich erreichbare Dienst. `db` und `redis` erreichen das Internet nie. |
| 4 | Alle persistenten Daten unter `/srv/objektakte/` in 14 Verzeichnissen; `db` und `redis` als benannte Volumes mit Bindung auf diese Pfade. | CR 0.1. Beschluss B-14. Ein Wurzelpfad für Backup, Plattenüberwachung und Wiederherstellung. |
| 5 | Geheimnisse ausschließlich als dateibasierte Docker Secrets unter `/srv/objektakte/secrets/`; `.env` enthält nur Startparameter; alles Fachliche liegt in `app_settings`. | Beschluss B-06 und B-25. Secrets erscheinen nicht in `docker inspect` und `docker compose config`. KI-Modelle, Endpunkte, Timeouts und Budgets stehen nicht in `.env`. |
| 6 | Ressourcenlimits als Variablen mit Formel aus den gemessenen Größen C, M, D. Alle Faktoren sind Annahmen und werden im OCR-Probelauf (Meilenstein M0) ersetzt. | CR 0.1 und 7: messen statt annehmen. |
| 7 | Fünf Datenbankkonten mit abgestuften Rechten (`app_migrate`, `app_rw`, `app_worker`, `app_backup`, `app_ro`); Trigger gegen UPDATE und DELETE auf `audit_events` und `iban_access_log`. | Beschluss B-43. Ein kompromittierter Worker kann keine Stammdaten ändern und kein Protokoll fälschen. |
| 8 | Deployment über `scripts/deploy.sh` (ein Befehl, Schalter `--first-run` für die Erstinstallation), Rollback über `scripts/rollback.sh` (ein Befehl). Migrationen laufen nie beim Container-Start. | CR 0 und 0.1. Prüfberichte K3-15 und K4-20 (Erstinstallation). |
| 9 | Backup täglich als Container `backup`: Dump plus Archive der Fachverzeichnisse, atomares Schreiben, Löschung alter Sicherungen nur nach Erfolg, Statusdatei für die Statusseite. Offsite-Kopie verschlüsselt als Empfehlung. | CR 0.1 und 14. Dumps enthalten Namen und Adressen im Klartext. |
| 10 | OAuth als interne App mit Scope `drive`, Redirect-URIs nach Beschluss B-16, Token AES-256-GCM verschlüsselt in `oauth_tokens`; Nachweis über 8 Tage durch täglichen erzwungenen Refresh plus stündlichen Lesetest. | CR 0.1, 14 und 15. Prüfbericht K12 (F gegen G vereinheitlicht). |
| 11 | Datenschutz-Voraussetzungen als Checkliste des Auftraggebers; Freigabekriterium vor dem ersten externen KI-Aufruf mit Produktivdaten. | CR 0.1 und 10. |

Reihenfolge im Betriebsteil: (1) Serverbefund nach Abschnitt 1 erheben, (2) Host härten nach Abschnitt 2, (3) Verzeichnisse, Secrets und `.env` anlegen nach Abschnitt 3, (4) `scripts/deploy.sh --first-run` und Deployment-Tests nach Abschnitt 9, (5) Google-OAuth nach Abschnitt 7 verbinden und den 8-Tage-Nachweis starten, (6) Wiederherstellung einmal proben nach Abschnitt 5.

Was der Auftraggeber vor Meilenstein M1 entscheiden muss, steht gebündelt in Anhang B. Ohne Antwort werden die dort genannten Vorschlagswerte gebaut.

---

## 1. Server-Ausleseanleitung (noch durchzuführen)

Status: **noch nicht durchgeführt.** Der Zielserver war aus der Entwicklungsumgebung nicht erreichbar. Alle Werte in diesem Abschnitt sind Platzhalter. Die Durchführung ist Arbeitsschritt 1 bis 5 von Meilenstein M0 (`scripts/measure_server.sh`); Voraussetzung ist der SSH-Zugang für einen Deploy-Nutzer (Umsetzungsplan V-01).

Regeln: In diesem Schritt wird nur gelesen, nichts geändert. Befehle mit `sudo` sind gekennzeichnet. Ausgaben, die Geheimnisse enthalten können (Umgebungsvariablen von Traefik, `acme.json`, Passwörter), werden nicht kopiert; es werden nur Namen und Strukturen notiert. Das Ergebnisblatt (Abschnitt 1.6) wird ohne Geheimnisse unter `docs/betrieb/serverbefund.md` abgelegt.

### 1.1 Ressourcen

```bash
# Kerne, Arbeitsspeicher, Platte
nproc
lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Core|Socket'
free -h
df -h
df -h /srv 2>/dev/null || echo "/srv existiert noch nicht, liegt dann auf der Wurzelpartition"
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
# I/O-Scheduler (Wirkung von ionice haengt davon ab)
for b in /sys/block/*/queue/scheduler; do echo "$b: $(cat "$b")"; done
# Betriebssystem und Zeit
lsb_release -a
timedatectl
uptime
```

Ergebnisfelder: `[C]` = Ausgabe von `nproc`; `[M]` = Spalte total der Zeile Mem von `free -h` in GiB; `[D]` = Spalte Avail von `df -h` für die Partition, auf der `/srv` liegt, in GiB; `[S]` = Zeile Swap; `[SCHEDULER]` = Scheduler des Datenträgers von `/srv`.

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

Ergebnisfelder: `[COMPOSE_VERSION]` (Plugin `docker compose`, nicht das alte `docker-compose`), `[CGROUP_VERSION]` (ANNAHME AB15: v2 wird für `deploy.resources.limits` erwartet), vorhandene Netzwerke, bereits belegte Host-Ports, Inhalt der `daemon.json` (Logtreiber, Rotation).

### 1.3 Traefik

Zuerst den Container finden, dann Netzwerke, Kommandozeile, Labels und Mounts auslesen. Der Containername kann abweichen.

```bash
docker ps --format '{{.Names}}\t{{.Image}}' | grep -i traefik
TRAEFIK=$(docker ps --format '{{.Names}}\t{{.Image}}' | grep -i traefik | head -1 | cut -f1)
echo "Traefik-Container: $TRAEFIK"

# Netzwerke, in denen Traefik haengt (Kandidaten fuer TRAEFIK_NETWORK)
docker inspect "$TRAEFIK" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'

# Kommandozeile (statische Konfiguration als Flags)
docker inspect "$TRAEFIK" --format '{{json .Config.Cmd}}' | tr ',' '\n'
docker inspect "$TRAEFIK" --format '{{json .Config.Entrypoint}}'

# Umgebungsvariablen: nur die Namen, Werte koennen Geheimnisse enthalten
docker inspect "$TRAEFIK" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -i '^TRAEFIK_' | sed 's/=.*//'

# Labels des Traefik-Containers (Dashboard, Redirects, Middlewares)
docker inspect "$TRAEFIK" --format '{{json .Config.Labels}}' | tr ',' '\n'

# Mounts: hier liegen traefik.yml oder traefik.toml, dynamische Konfiguration, acme.json
docker inspect "$TRAEFIK" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Type}}){{"\n"}}{{end}}'

# Veroeffentlichte Ports (erwartet 80 und 443)
docker inspect "$TRAEFIK" --format '{{json .NetworkSettings.Ports}}'
```

Konfigurationsdateien lesen (Pfade aus der Mounts-Ausgabe einsetzen):

```bash
sudo find / -xdev \( -name 'traefik.yml' -o -name 'traefik.yaml' -o -name 'traefik.toml' \) 2>/dev/null
docker inspect "$TRAEFIK" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
docker inspect "$TRAEFIK" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'

# Entrypoints und Cert-Resolver (Datei- oder Flag-Form)
sudo grep -nEi 'entryPoints|address:|certificatesResolvers|acme|httpChallenge|tlsChallenge|dnsChallenge|email' [PFAD_traefik.yml]
docker inspect "$TRAEFIK" --format '{{json .Config.Cmd}}' | tr ',' '\n' | grep -Ei 'entrypoints|certificatesresolvers|providers.docker'

# Docker-Provider: exposedByDefault, Standardnetzwerk, Constraints
sudo grep -nEi 'providers|docker|exposedByDefault|network:|constraints|watch' [PFAD_traefik.yml]
docker inspect "$TRAEFIK" --format '{{json .Config.Cmd}}' | tr ',' '\n' | grep -Ei 'exposedbydefault|providers.docker.network|constraints'

# Dynamische Konfiguration (Middlewares, globale Redirects, TLS-Optionen)
sudo grep -rnEi 'redirectScheme|redirections|middlewares|tls:|options:|minVersion|headers' [VERZEICHNIS_dynamische_Konfiguration] 2>/dev/null

# Zertifikatsspeicher: nur Existenz und Rechte, Inhalt nicht kopieren
sudo ls -l [PFAD_acme.json]

# Kandidat fuer TRAEFIK_NETWORK pruefen, Subnetz fuer TRUSTED_PROXY_CIDR ablesen
docker network inspect [NETZWERKNAME] --format '{{.Name}} | Driver {{.Driver}} | Internal {{.Internal}} | Subnetz {{range .IPAM.Config}}{{.Subnet}}{{end}} | Container: {{range .Containers}}{{.Name}} {{end}}'
```

### 1.4 Host-Sicherheit (Ist-Zustand)

```bash
sudo sshd -T 2>/dev/null | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|port) '
sudo ufw status verbose
sudo systemctl is-enabled unattended-upgrades; sudo systemctl is-active unattended-upgrades
sudo cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null
sudo ss -tulpen | grep LISTEN
id; sudo -l | head -20
sudo journalctl --disk-usage
```

### 1.5 Auswertung der Traefik-Werte

| Frage | Woran erkennbar | Folge für die Compose-Datei |
|---|---|---|
| Wie heißt das externe Netzwerk? | Netzwerk, in dem Traefik hängt und das nicht das Default-Bridge-Netz ist. Hängt Traefik in mehreren, das Netz nehmen, das andere Anwendungscontainer verwenden oder das in `providers.docker.network` steht. | `TRAEFIK_NETWORK=[Name]` |
| Wie heißt der HTTPS-Entrypoint? | `entryPoints.<name>.address: ":443"` bzw. Flag `--entrypoints.<name>.address=:443`. Häufig `websecure`, aber nicht annehmen. | `TRAEFIK_ENTRYPOINT=[Name]` |
| Wie heißt der HTTP-Entrypoint, gibt es einen globalen Redirect auf HTTPS? | `address: ":80"` und darunter `http.redirections.entryPoint.to`. | `TRAEFIK_ENTRYPOINT_INSECURE=[Name]`; ohne globalen Redirect wird der optionale Redirect-Router aktiviert (`TRAEFIK_REDIRECT_ROUTER=true`). |
| Wie heißt der Cert-Resolver? | Schlüssel unter `certificatesResolvers.<name>.acme` bzw. Flag `--certificatesresolvers.<name>.acme...`. | `TRAEFIK_CERTRESOLVER=[Name]` |
| Welche ACME-Challenge? | `httpChallenge`, `tlsChallenge` oder `dnsChallenge`. | Bei `httpChallenge` muss Port 80 in UFW offen bleiben. |
| Ist `exposedByDefault` auf `false`? | `providers.docker.exposedByDefault: false` oder Flag. | Dann ist `traefik.enable=true` an `web` zwingend; die Compose-Datei setzt das Label immer. |
| Gibt es `providers.docker.constraints`? | Ausdruck mit Labels. | `web` muss dieses Label zusätzlich tragen; Wert im Ergebnisblatt notieren. |
| Swarm-Modus? | `providers.swarm` oder `providers.docker.swarmMode`. | Erwartet wird kein Swarm. Falls doch, Labels unter `deploy.labels`. |
| Traefik-Hauptversion? | Image-Tag in `docker ps`. | Label-Syntax gilt für Traefik v2 und v3 (ANNAHME AB17); bei v1 wäre der Entwurf anzupassen. |
| Subnetz des Traefik-Netzes? | `docker network inspect`, IPAM-Konfiguration. | `TRUSTED_PROXY_CIDR=[Subnetz]` (nur Traefik darf `X-Forwarded-*` setzen). |

### 1.6 Ergebnisblatt

Wird ausgefüllt und als `docs/betrieb/serverbefund.md` abgelegt (ohne Geheimnisse). Die rechte Spalte nennt die Zielvariable in `.env`.

| Messgröße | Befehl | Wert | Zielvariable oder Verwendung |
|---|---|---|---|
| Kerne C | `nproc` | `[C]` | `OCR_PROCESSES`, `WORKER_CPUS`, `NLP_CONCURRENCY` |
| RAM M in GiB | `free -h` | `[M]` | alle `*_MEM`, Randbedingung Abschnitt 3.10 |
| Swap S | `free -h` | `[S]` | Härtung Nr. 11 |
| Platte D frei in GiB (Partition von `/srv`) | `df -h` | `[D]` | `DISK_RESERVE_GB`, `BACKUP_RETENTION_DAYS` |
| I/O-Scheduler | `/sys/block` | `[SCHEDULER]` | Wirkung von `ionice` (ANNAHME AB25) |
| Compose-Version | `docker compose version` | `[COMPOSE_VERSION]` | Prüfung `deploy.resources.limits` (ANNAHME AB15) |
| Cgroup-Version | `docker info` | `[CGROUP_VERSION]` | Prüfung CPU-Limits |
| Logtreiber und `daemon.json` | `docker info`, `daemon.json` | `[LOG_DRIVER]` | Härtung Nr. 9, Frage F27 |
| Traefik-Containername | `docker ps` | `[TRAEFIK_CONTAINER]` | nur Doku |
| Traefik-Hauptversion | `docker ps` (Image-Tag) | `[TRAEFIK_VERSION]` | nur Doku |
| Externes Netzwerk | `docker inspect` | `[TRAEFIK_NETWORK]` | `TRAEFIK_NETWORK` |
| Subnetz des externen Netzes | `docker network inspect` | `[TRUSTED_PROXY_CIDR]` | `TRUSTED_PROXY_CIDR` |
| HTTPS-Entrypoint | Konfiguration | `[TRAEFIK_ENTRYPOINT]` | `TRAEFIK_ENTRYPOINT` |
| HTTP-Entrypoint | Konfiguration | `[TRAEFIK_ENTRYPOINT_INSECURE]` | `TRAEFIK_ENTRYPOINT_INSECURE` |
| Globaler Redirect vorhanden | Konfiguration | ja/nein | `TRAEFIK_REDIRECT_ROUTER` |
| Cert-Resolver | Konfiguration | `[TRAEFIK_CERTRESOLVER]` | `TRAEFIK_CERTRESOLVER` |
| ACME-Challenge | Konfiguration | `[ACME_CHALLENGE]` | Firewall-Regel Port 80 |
| exposedByDefault | Konfiguration | true/false | Label `traefik.enable` |
| Constraints | Konfiguration | `[TRAEFIK_CONSTRAINT_LABEL]` | Zusatzlabel an `web` |
| SSH: Root-Login, Passwort-Login | `sshd -T` | | Härtung Nr. 3 |
| UFW-Status und Regeln | `ufw status` | | Härtung Nr. 4 |
| unattended-upgrades | `systemctl` | | Härtung Nr. 6 |
| Zeitzone, NTP | `timedatectl` | `[TZ]` | `TZ` (Sollwert Europe/Berlin) |
| Belegte Host-Ports | `ss -tulpen` | | Konfliktprüfung; die eigenen Dienste binden keine |

### 1.7 Eintrag in `.env`

1. `.env.example` (Abschnitt 3.7) nach `.env` kopieren, Rechte `0600`, Eigentümer Deploy-Nutzer.
2. Traefik-Werte aus dem Ergebnisblatt eintragen: `TRAEFIK_NETWORK`, `TRAEFIK_ENTRYPOINT`, `TRAEFIK_ENTRYPOINT_INSECURE`, `TRAEFIK_REDIRECT_ROUTER`, `TRAEFIK_CERTRESOLVER`, `TRUSTED_PROXY_CIDR`, `TZ`.
3. Prozesszahlen und Limits nach der Formel in Abschnitt 3.10 aus `[C]`, `[M]`, `[D]` berechnen und eintragen. Vor dem OCR-Probelauf gelten die Annahmefaktoren, danach die Messwerte aus `docs/betrieb/performance-entscheidung.md`.
4. Prüfen: `docker compose config --quiet` fehlerfrei; `docker network inspect "$TRAEFIK_NETWORK"` findet das Netz.

### 1.8 OCR-Probelauf (Verweis)

Der Serverbefund liefert C, M und D. Die Faktoren m_ocr (Speicher je OCR-Prozess), t_ocr (Sekunden je Seite) und die Skalierungseffizienz liefert der OCR-Probelauf in einem temporären Container (Umsetzungsplan M0, Schritte 6 bis 8; Freigabe V-03). Erst danach werden `OCR_PROCESSES`, `WORKER_MEM` und `WORKER_NLP_MEM` endgültig gesetzt. Zeigt der Probelauf weniger als fünf nutzbare OCR-Prozesse oder mehr als 5 Sekunden je Seite, fällt vor M1 die Entscheidung nach Frage F18 (Tarifwechsel, `tessdata_fast`, Zwei-Phasen-OCR, längere Laufzeit).

---
## 2. Host-Härtung (Checkliste mit Befehlen)

Gilt für Ubuntu 24.04 LTS (CR 0.1). Reihenfolge einhalten: erst den Schlüssel-Login in einer zweiten Sitzung prüfen, dann den Passwort-Login abschalten. Traefik und Docker werden nicht verändert. Die Härtung wird in Meilenstein M1 durchgeführt und mit Test T12 abgenommen (Auflage Ü22 aus Gutachten 3). Wer außer dem Entwickler einen Deploy-Zugang erhält, ist Frage F26 (Anhang B).

### 2.1 Checkliste

| Nr. | Maßnahme | Befehle | Prüfung |
|---|---|---|---|
| 1 | Eigener Deploy-Nutzer mit sudo und Docker-Gruppe, kein Arbeiten als root | `sudo adduser deploy && sudo usermod -aG sudo,docker deploy` | `id deploy` |
| 2 | SSH-Schlüssel hinterlegen und testen, bevor der Passwort-Login abgeschaltet wird | Auf dem Client: `ssh-copy-id deploy@187.124.23.80`; dann in einer zweiten Sitzung `ssh deploy@187.124.23.80` | Login ohne Passwortabfrage |
| 3 | SSH: nur Schlüssel, kein Root-Login | Datei `/etc/ssh/sshd_config.d/90-hardening.conf` mit `PermitRootLogin no`, `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`, `MaxAuthTries 3`, `X11Forwarding no`; danach `sudo sshd -t && sudo systemctl reload ssh` | `sudo sshd -T \| grep -Ei 'permitrootlogin\|passwordauthentication'` zeigt `no` |
| 4 | UFW: nur 22, 80, 443 | `sudo ufw default deny incoming && sudo ufw default allow outgoing && sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable` | `sudo ufw status verbose` |
| 5 | Docker und UFW | Docker schreibt eigene iptables-Regeln, die UFW umgehen können. Da kein Dienst des Projekts Host-Ports veröffentlicht, entsteht keine Lücke in der Firewall. Kontrolle: `docker ps --format '{{.Names}} {{.Ports}}'` zeigt außer Traefik (80, 443) keine veröffentlichten Ports. | `sudo ss -tulpen \| grep LISTEN` zeigt nur 22, 80, 443 und lokale Dienste |
| 6 | Automatische Sicherheitsupdates ohne automatischen Neustart | `sudo apt install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades`; in `/etc/apt/apt.conf.d/50unattended-upgrades` bleibt `Unattended-Upgrade::Automatic-Reboot "false"` (Neustart bewusst planen, Kernel-Updates erfordern ihn) | `sudo unattended-upgrades --dry-run --debug` |
| 7 | Docker-Engine-Updates | Das Docker-Repository ist in unattended-upgrades nicht enthalten. Monatlich `sudo apt list --upgradable \| grep docker`, geplantes Update außerhalb laufender Verarbeitung, danach `docker compose ps` | Eintrag in `docs/betrieb/wartung.md` |
| 8 | Zeitzone und Zeitsynchronisation | `sudo timedatectl set-timezone Europe/Berlin && sudo timedatectl set-ntp true` | `timedatectl` zeigt `System clock synchronized: yes` |
| 9 | Docker-Logrotation für fremde Container (optional, nur nach Freigabe F27) | `/etc/docker/daemon.json` mit `{"log-driver":"json-file","log-opts":{"max-size":"20m","max-file":"5"}}`, danach `sudo systemctl restart docker`. Der Neustart unterbricht Traefik kurz. Die eigenen Dienste tragen die Rotation bereits in der Compose-Datei; Empfehlung: nur eigene Dienste, keine Änderung der `daemon.json`. | `docker info --format '{{.LoggingDriver}}'` |
| 10 | Journal begrenzen | `/etc/systemd/journald.conf`: `SystemMaxUse=500M` (ANNAHME AB22); `sudo systemctl restart systemd-journald` | `journalctl --disk-usage` |
| 11 | Swap, falls `[S]` gleich 0 (ANNAHME AB9: 2 GiB) | `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' \| sudo tee -a /etc/fstab` | `free -h` zeigt Swap |
| 12 | fail2ban für SSH (optional, geringer Aufwand) | `sudo apt install -y fail2ban`, Standard-Jail `sshd` | `sudo fail2ban-client status sshd` |
| 13 | Dateirechte der Konfiguration | `chmod 600 .env`, `sudo chmod 700 /srv/objektakte/secrets`, `sudo chmod 700 /srv/objektakte/backup` | `ls -la` |
| 14 | sudo mit Passwort, kein `NOPASSWD` | Standard belassen | `sudo -l` |
| 15 | Ungenutzte Dienste | `sudo ss -tulpen` prüfen, nicht benötigte Dienste mit `systemctl disable --now [dienst]` abschalten | erneutes `ss` |

### 2.2 Härtung der Container

Gilt für alle eigenen Dienste und ist in der Compose-Datei (Abschnitt 3.5) umgesetzt:

| Maßnahme | Umsetzung |
|---|---|
| Kein Root im Container | `user: "${APP_UID}:${APP_UID}"`; ANNAHME AB1: UID 10001, im Dockerfile festgelegt, Bind-Mounts gehören dieser UID |
| Keine Rechteausweitung | `security_opt: no-new-privileges:true` |
| Read-only Root-Dateisystem | für `web` mit `tmpfs` für `/tmp`; Worker brauchen Schreibrechte auf `/data/work` und werden nicht read-only gefahren |
| Ein OCR-Prozess belegt einen Kern | `OMP_THREAD_LIMIT=1` im `worker`; OCR-Unterprozesse starten mit `nice` und `ionice` (Wirkung hängt vom Scheduler ab, ANNAHME AB25) |
| Keine Host-Ports | kein `ports:`-Block; Traefik erreicht `web` über das externe Netz |
| Geheimnisse nur als Datei | `/run/secrets/<name>`, Variablen mit Suffix `_FILE` |
| Kind-Prozesse werden recycelt | `--max-tasks-per-child`, `--max-memory-per-child` (gilt bei Stack A) |
| Harte Limits | `deploy.resources.limits` je Dienst, Gewichte über `cpu_shares` |

### 2.3 Sicherheits-Header über Traefik-Middleware

Ohne Eingriff in die Traefik-Konfiguration trägt `web` eine Middleware als Labels: HSTS ein Jahr ohne Subdomains (andere Dienste unter `muellerhv.de` bleiben unberührt), `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN` (die PDF-Vorschau im Review Center läuft in einem Frame derselben Origin), `Referrer-Policy: strict-origin-when-cross-origin`. Content-Security-Policy setzt die Anwendung selbst, weil sie von den eingebundenen Skripten abhängt.

### 2.4 Nachweis und Abnahme

Nach Abschluss werden die Lesebefehle aus Abschnitt 1.4 erneut ausgeführt und die Ausgaben als Nachweis in `docs/betrieb/serverbefund.md` ergänzt (Test T12). Die Abnahme der Härtung durch den Auftraggeber ist Teil von Meilenstein M1.

---

## 3. Compose-Entwurf

### 3.1 Dienste und Queues

| Dienst | Aufgabe | Build-Ziel | Queues | Concurrency-Variable | Netze | Healthcheck |
|---|---|---|---|---|---|---|
| `web` | Anwendung über gunicorn (gilt bei Stack A): Review Center, Admin, OAuth-Callback, Statusseite, rechtegeprüfte Auslieferung von Seitenbildern und Dateien | `web` | keine | `GUNICORN_WORKERS`, `GUNICORN_THREADS` | `data`, `egress`, `[TRAEFIK_NETWORK]` | HTTP GET `/healthz/` |
| `worker` | Prozesspool für OCR: discover, hash, Seitenanalyse, Chunking, OCR je Chunk, Merge, Seitenbilder; keine Modelle im Speicher | `worker` | `ocr` | `OCR_PROCESSES` | `data`, `egress` | Heartbeat-Datei jünger als 2 min |
| `worker-nlp` | Kleiner Pool für Klassifikation: Entitätenerkennung, Gazetteer-Abgleich, lokaler Klassifikator, Entscheidungsalgorithmus, Nachtraining; lädt spaCy und Modell einmal je Prozess | `worker` | `classify` | `NLP_CONCURRENCY` | `data` | Heartbeat-Datei |
| `worker-io` | Threads für Netzwerkarbeit: Drive-Schreibzugriffe mit Sperre je Objekt, KI-Aufrufe Stufe 3, Listen, Nachforderung, Import-Parsing, Exporte | `worker` | `ai`, `io`, `lists` | `IO_CONCURRENCY` | `data`, `egress` | Heartbeat-Datei |
| `beat` | Zeitplan: Sweeper jede Minute, Token-Lesetest stündlich, erzwungener Refresh täglich, Vollständigkeitsprüfung und Nachtraining nachts, Bereinigung `work` und `previews`, Alarmierung | `web` | keine | | `data`, `egress` | Heartbeat-Datei |
| `redis` | Broker und Cache-Versionsschlüssel; AOF, `noeviction`, Passwort | offizielles Image (Redis oder kompatibler Fork, Anhang C) | | | `data` | `redis-cli ping` |
| `db` | MariaDB, utf8mb4, InnoDB, strikter SQL-Modus, Konfigurationsfragmente | offizielles Image | | | `data` | `healthcheck.sh` des Images |
| `backup` | Cron: täglicher Dump, Archive der Fachverzeichnisse, Aufbewahrung, optional verschlüsselte Offsite-Kopie | `docker/backup/Dockerfile` | | | `data`, `egress` nur bei Offsite | Alter von `status.json` |
| `classifier` (optional, Profil) | HTTP-Dienst für ein Embedding-Modell, nur bei unzureichender Trefferquote des linearen Modells | `worker` | | | `data` | HTTP GET `/healthz` |

Abweichung vom CR 7: `queue` heißt `redis`; `worker-nlp`, `worker-io`, `beat` und `backup` sind zusätzlich (Beschluss B-13, Bestätigung Frage F2). Die Dienstnamen sind in `docs/architektur.md` 4.1, Umsetzungsplan B-13 und D.1 und diesem Dokument gleichlautend.

### 3.2 Netzwerke

| Netz | Typ | Teilnehmer | Zweck |
|---|---|---|---|
| `data` | Compose-Netz, `internal: true` | alle Dienste | Verkehr zu `db` und `redis`. Aus diesem Netz gibt es keinen Weg ins Internet; `db`, `redis` und `worker-nlp` hängen nur hier. |
| `egress` | Compose-Netz, Bridge | `web`, `worker`, `worker-io`, `beat`; `backup` nur bei `OFFSITE_ENABLED=true` | Ausgehende Verbindungen zu Google Drive, OpenAI, Anthropic, SMTP. |
| `[TRAEFIK_NETWORK]` | vorhandenes externes Netz | nur `web` | Eingehender Verkehr von Traefik. Name aus dem Serverbefund. |

Docker-DNS funktioniert auch in internen Netzen; die Dienste erreichen sich unter ihren Compose-Namen. Das Label `traefik.docker.network` sagt Traefik, welches der drei Netze von `web` es nutzen soll; ohne das Label ist die Wahl nicht deterministisch.

### 3.3 Verzeichnisse auf dem Host

Alle persistenten Daten liegen unter `/srv/objektakte/` (CR 0.1, Beschluss B-14). `db` und `redis` sind benannte Volumes, die per `driver_opts` auf ihre Unterverzeichnisse gebunden sind; die übrigen Verzeichnisse sind Bind-Mounts. Downloads aus Drive gehen einheitlich nach `work/<sha256>/`; `transit/` ist ausschließlich für Uploads reserviert.

| Verzeichnis | Inhalt | Schreibt | Liest | Im Backup | Löschkonzept |
|---|---|---|---|---|---|
| `db/` | MariaDB-Datenverzeichnis | `db` | `db` | als Dump | Fachdaten, Fristen aus `retention_policies` |
| `redis/` | AOF des Brokers | `redis` | `redis` | nein (nur Job-IDs, Zustand liegt in der Datenbank) | Broker |
| `transit/` | Uploads bis zur Übernahme nach Drive | `web`, `worker-io` | `worker` | ja | Löschung nach erfolgreicher Ablage in Drive |
| `work/` | Original nach Download, Chunks, OCR-Zwischenstand je Dokument | `worker` | `worker-io` | nein (reproduzierbar) | Sweeper löscht nach Abschluss oder nach Frist |
| `ocr-cache/` | Maskierter Seitentext und OCR-Ausgabe je Dokument-Hash | `worker` | `worker-nlp`, `worker-io` | ja | folgt dem Dokument (Löschkonzept Abschnitt 8) |
| `previews/` | Seitenbilder je Dokument und Seite (JPEG) | `worker` | `web` (nur nach Rechteprüfung) | nein (reproduzierbar) | `previews.retention_days_after_resolve` (ANNAHME AB29: 90 Tage nach Erledigung), Neuerzeugung auf Anforderung |
| `models/` | Klassifikator-Artefakte je Version, Metriken | `worker-nlp` | `worker-io`, `classifier` | ja | Versionen behalten, aktive Version in `classifier_models` |
| `lists/` | Lokale Kopie der Listen, Rückhalt bei Drive-Fehlern | `worker-io` | `web` | ja | wie Fachdaten |
| `requests/` | Nachforderungsschreiben (DOCX, PDF) je Objekt und Version | `worker-io` | `web` | ja | Fachdaten |
| `imports/` | Importprotokolle je Import | `worker-io` | `web` | ja | Fachdaten |
| `exports/` | Abgleichsprotokolle und sonstige Exporte | `worker-io` | `web` | ja | Fachdaten |
| `backup/` | Dumps, Archive, Konfigurationskopie, `status.json` | `backup` | `web` (nur `status.json`) | ist das Backup | `BACKUP_RETENTION_DAYS` |
| `secrets/` | Dateibasierte Docker Secrets, `0700` root, Dateien `0600` root | Administrator | Docker | nein, Kopie im Passwortmanager | Rotation nach `docs/architektur.md` 9.6 |
| `deploy/` | `current`, `previous`, `tags.log` | `deploy.sh` | `rollback.sh` | ja (Konfiguration) | unbegrenzt |

Anlage (einmalig, als Deploy-Nutzer mit sudo):

```bash
sudo mkdir -p /srv/objektakte/{db,redis,transit,work,ocr-cache,previews,models,lists,requests,imports,exports,backup/db,backup/volumes,backup/config,secrets,deploy}
# Verzeichnisse der Anwendungscontainer gehoeren APP_UID (ANNAHME AB1: 10001)
sudo chown -R 10001:10001 /srv/objektakte/{transit,work,ocr-cache,previews,models,lists,requests,imports,exports}
sudo chmod 750 /srv/objektakte/{transit,work,ocr-cache,previews,models,lists,requests,imports,exports}
sudo chmod 700 /srv/objektakte/secrets /srv/objektakte/backup
sudo chown deploy:deploy /srv/objektakte/deploy && sudo chmod 750 /srv/objektakte/deploy
# db und redis: die Images setzen ihre eigenen Nutzer, deshalb hier keine chown-Vorgabe
ls -la /srv/objektakte
```

Voraussetzung: Die Verzeichnisse existieren vor dem ersten `docker compose up`, sonst legt Docker sie als root an und die Container können nicht schreiben.

### 3.4 Image-Aufbau (zwei Build-Ziele)

Ein Dockerfile `docker/app.Dockerfile` mit zwei Zielen (Beschluss B-44). Paketliste ohne Versionsnummern; die vollständige Liste entsteht in M1 unter `docs/architektur/image.md`.

| Ziel | Inhalt | Verwendet von |
|---|---|---|
| `web` | Python-Laufzeit, Anwendungscode, Abhängigkeiten ohne OCR-Binärdateien; nicht privilegierter Nutzer `APP_UID` | `web`, `beat` |
| `worker` | wie `web` plus `tesseract-ocr`, `tesseract-ocr-deu` (Standard-Sprachdaten; `tessdata_fast` über Build-Argument `TESSDATA_VARIANT`), `ocrmypdf`, `ghostscript`, `qpdf`, `poppler-utils`, `img2pdf`, `pikepdf`, `pypdfium2` oder `PyMuPDF`, `pdfplumber`, spaCy mit deutschem Modell, `scikit-learn`, `rapidfuzz` | `worker`, `worker-nlp`, `worker-io`, `classifier` |

Beide Ziele teilen Codebasis und Lockfile; Migrationen und Fachlogik sind damit immer identisch. Das Backup-Image (`docker/backup/Dockerfile`) enthält `mariadb-client`, `tar`, `gzip`, `jq`, `age`, `rclone` und einen Cron-Dienst (Paketnamen zum Umsetzungszeitpunkt prüfen, ANNAHME AB19).

### 3.5 `docker-compose.yml` (vollständiger Entwurf)

Dateiname im Repository: `docker-compose.yml` (CR 0.1; ebenso Umsetzungsplan 1.5 und `docs/architektur.md` Abschnitt 11). Alle serverabhängigen Werte kommen aus `.env` (Abschnitt 3.7). Kommandozeilen mit `gunicorn`, `celery` und `python -m` gelten bei Stack A; bei anderem Stack ändern sich nur die `command`-Zeilen und die Heartbeat-Skripte.

```yaml
name: objektakte

x-app-env: &app-env
  TZ: ${TZ}
  APP_ENV: ${APP_ENV}
  DB_HOST: db
  DB_PORT: "3306"
  DB_NAME: ${DB_NAME}
  DB_USER: ${DB_USER}                                   # app_rw fuer web und beat
  DB_PASSWORD_FILE: /run/secrets/db_app_password
  REDIS_URL: redis://redis:6379/0
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
  HEARTBEAT_FILE: /tmp/heartbeat

x-worker-env: &worker-env
  <<: *app-env
  DB_USER: ${DB_WORKER_USER}                            # app_worker, Mindestrechte
  DB_PASSWORD_FILE: /run/secrets/db_worker_password

x-common: &common
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
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy

x-worker-secrets: &worker-secrets
  - db_worker_password
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

x-worker-healthcheck: &worker-healthcheck
  test: ["CMD-SHELL", "test -n \"$$(find /tmp/heartbeat -mmin -2 2>/dev/null)\""]
  interval: 60s
  timeout: 10s
  retries: 3
  start_period: 90s

services:

  web:
    <<: *common
    image: objektakte/web:${IMAGE_TAG:-dev}
    build:
      context: .
      dockerfile: docker/app.Dockerfile
      target: web
    command: >
      gunicorn objektakte.wsgi:application
      --bind 0.0.0.0:8000
      --worker-class gthread
      --workers ${GUNICORN_WORKERS}
      --threads ${GUNICORN_THREADS}
      --timeout ${GUNICORN_TIMEOUT}
      --access-logfile - --error-logfile -
      --forwarded-allow-ips ${TRUSTED_PROXY_CIDR}
    environment:
      <<: *app-env
      DB_MIGRATE_USER: ${DB_MIGRATE_USER}               # nur fuer app-migrate (deploy.sh)
      DB_MIGRATE_PASSWORD_FILE: /run/secrets/db_migrate_password
    read_only: true
    tmpfs:
      - /tmp:size=256m
    networks:
      - data
      - egress
      - traefik
    volumes:
      - /srv/objektakte/transit:/data/transit
      - /srv/objektakte/previews:/data/previews:ro
      - /srv/objektakte/requests:/data/requests:ro
      - /srv/objektakte/lists:/data/lists:ro
      - /srv/objektakte/exports:/data/exports:ro
      - /srv/objektakte/backup:/data/backup:ro           # Anwendung liest nur status.json
    secrets:
      - db_app_password
      - db_migrate_password
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
      # Optionaler Redirect-Router, nur wenn der Serverbefund keinen globalen
      # HTTP-zu-HTTPS-Redirect zeigt (TRAEFIK_REDIRECT_ROUTER=true). Sonst diese vier Zeilen entfernen.
      traefik.http.routers.objektakte-http.rule: "Host(`${APP_DOMAIN}`)"
      traefik.http.routers.objektakte-http.entrypoints: "${TRAEFIK_ENTRYPOINT_INSECURE}"
      traefik.http.routers.objektakte-http.middlewares: "objektakte-redirect"
      traefik.http.middlewares.objektakte-redirect.redirectscheme.scheme: "https"
      # Falls der Serverbefund providers.docker.constraints zeigt: Zusatzlabel hier eintragen.
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
    <<: *common
    image: objektakte/worker:${IMAGE_TAG:-dev}
    build:
      context: .
      dockerfile: docker/app.Dockerfile
      target: worker
      args:
        TESSDATA_VARIANT: "${TESSDATA_VARIANT}"
    command: >
      celery -A objektakte worker
      -Q ocr -n worker-ocr@%h
      --concurrency ${OCR_PROCESSES}
      --prefetch-multiplier 1
      --max-tasks-per-child ${WORKER_MAX_TASKS_PER_CHILD}
      --max-memory-per-child ${WORKER_MAX_MEMORY_PER_CHILD_KB}
    environment:
      <<: *worker-env
      OMP_THREAD_LIMIT: "1"
    networks:
      - data
      - egress
    volumes:
      - /srv/objektakte/transit:/data/transit
      - /srv/objektakte/work:/data/work
      - /srv/objektakte/ocr-cache:/data/ocr-cache
      - /srv/objektakte/previews:/data/previews
    secrets: *worker-secrets
    stop_grace_period: ${WORKER_STOP_GRACE}
    healthcheck: *worker-healthcheck
    cpu_shares: 256
    deploy:
      resources:
        limits:
          cpus: "${WORKER_CPUS}"
          memory: "${WORKER_MEM}"

  worker-nlp:
    <<: *common
    image: objektakte/worker:${IMAGE_TAG:-dev}
    build:
      context: .
      dockerfile: docker/app.Dockerfile
      target: worker
      args:
        TESSDATA_VARIANT: "${TESSDATA_VARIANT}"
    command: >
      celery -A objektakte worker
      -Q classify -n worker-nlp@%h
      --concurrency ${NLP_CONCURRENCY}
      --prefetch-multiplier 1
      --max-tasks-per-child ${WORKER_MAX_TASKS_PER_CHILD}
    environment:
      <<: *worker-env
      OMP_THREAD_LIMIT: "1"
    networks:
      - data
    volumes:
      - /srv/objektakte/ocr-cache:/data/ocr-cache:ro
      - /srv/objektakte/models:/data/models
    secrets: *worker-secrets
    stop_grace_period: ${WORKER_STOP_GRACE}
    healthcheck: *worker-healthcheck
    cpu_shares: 512
    deploy:
      resources:
        limits:
          cpus: "${WORKER_NLP_CPUS}"
          memory: "${WORKER_NLP_MEM}"

  worker-io:
    <<: *common
    image: objektakte/worker:${IMAGE_TAG:-dev}
    build:
      context: .
      dockerfile: docker/app.Dockerfile
      target: worker
      args:
        TESSDATA_VARIANT: "${TESSDATA_VARIANT}"
    command: >
      celery -A objektakte worker
      -Q io,ai,lists -n worker-io@%h
      --pool threads
      --concurrency ${IO_CONCURRENCY}
      --prefetch-multiplier 1
    environment: *worker-env
    networks:
      - data
      - egress
    volumes:
      - /srv/objektakte/transit:/data/transit
      - /srv/objektakte/work:/data/work:ro
      - /srv/objektakte/ocr-cache:/data/ocr-cache:ro
      - /srv/objektakte/lists:/data/lists
      - /srv/objektakte/requests:/data/requests
      - /srv/objektakte/imports:/data/imports
      - /srv/objektakte/exports:/data/exports
      - /srv/objektakte/models:/data/models:ro
    secrets: *worker-secrets
    stop_grace_period: ${WORKER_STOP_GRACE}
    healthcheck: *worker-healthcheck
    cpu_shares: 512
    deploy:
      resources:
        limits:
          cpus: "${WORKER_IO_CPUS}"
          memory: "${WORKER_IO_MEM}"

  beat:
    <<: *common
    image: objektakte/web:${IMAGE_TAG:-dev}
    build:
      context: .
      dockerfile: docker/app.Dockerfile
      target: web
    command: celery -A objektakte beat --schedule /tmp/celerybeat-schedule --pidfile=
    environment: *app-env
    read_only: true
    tmpfs:
      - /tmp:size=64m
    networks:
      - data
      - egress
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
    image: ${REDIS_IMAGE}:${REDIS_TAG}
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
      - ./docker/db/conf.d:/etc/mysql/conf.d:ro
      - ./docker/db/init:/docker-entrypoint-initdb.d:ro
    secrets:
      - db_root_password
      - db_app_password
      - db_worker_password
      - db_migrate_password
      - db_backup_password
      - db_ro_password
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
    image: objektakte/backup:${IMAGE_TAG:-dev}
    build:
      context: .
      dockerfile: docker/backup/Dockerfile
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
      OFFSITE_REMOTE: ${OFFSITE_REMOTE}
      RCLONE_CONFIG: /run/secrets/rclone_conf
    networks:
      - data
      # - egress                                        # nur bei OFFSITE_ENABLED=true einkommentieren
    volumes:
      - /srv/objektakte/transit:/src/transit:ro
      - /srv/objektakte/ocr-cache:/src/ocr-cache:ro
      - /srv/objektakte/models:/src/models:ro
      - /srv/objektakte/lists:/src/lists:ro
      - /srv/objektakte/requests:/src/requests:ro
      - /srv/objektakte/imports:/src/imports:ro
      - /srv/objektakte/exports:/src/exports:ro
      - /srv/objektakte/deploy:/src/deploy:ro
      - ./.env:/src/config/.env:ro
      - ./docker-compose.yml:/src/config/docker-compose.yml:ro
      - /srv/objektakte/backup:/backup
    secrets:
      - db_backup_password
      - backup_age_recipient
      - rclone_conf
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
    <<: *common
    profiles: ["classifier"]
    image: objektakte/worker:${IMAGE_TAG:-dev}
    build:
      context: .
      dockerfile: docker/app.Dockerfile
      target: worker
    command: python -m objektakte.classifier.server --port 9000
    environment: *worker-env
    networks:
      - data
    volumes:
      - /srv/objektakte/models:/data/models:ro
    secrets: *worker-secrets
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
  db_worker_password:      { file: /srv/objektakte/secrets/db_worker_password }
  db_migrate_password:     { file: /srv/objektakte/secrets/db_migrate_password }
  db_backup_password:      { file: /srv/objektakte/secrets/db_backup_password }
  db_ro_password:          { file: /srv/objektakte/secrets/db_ro_password }
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
  rclone_conf:             { file: /srv/objektakte/secrets/rclone.conf }
```

### 3.6 Erläuterungen zu den Entscheidungen in der Datei

| Stelle | Entscheidung | Begründung |
|---|---|---|
| Zwei Images `objektakte/web` und `objektakte/worker` aus einem Dockerfile | `web` und `beat` ohne OCR-Binärdateien, die drei Worker und `classifier` mit | Beschluss B-44. Beide Ziele teilen Code und Lockfile; ein Build-Lauf erzeugt beide. |
| `x-worker-env`, `db_worker_password` | Die Worker verbinden sich als `app_worker` | Beschluss B-43: Mindestrechte für die Prozesse, die fremde Dokumente verarbeiten. |
| `db_migrate_password` nur an `web` | Migrationen laufen über `docker compose run web app-migrate` | Nur der Dienst, der die Migration ausführt, sieht das DDL-Konto. |
| `read_only: true` mit `tmpfs` für `web` und `beat` | Root-Dateisystem unveränderlich | Schreibpfade sind nur `/tmp` und die gemounteten Datenverzeichnisse. |
| `*_FILE`-Variablen | Geheimnisse aus `/run/secrets/`, nie aus Umgebungsvariablen | Umgebungsvariablen erscheinen in `docker inspect`, Prozesslisten und Fehlerberichten. Das MariaDB-Image unterstützt `MARIADB_*_FILE` (ANNAHME AB16, zum Umsetzungszeitpunkt prüfen). |
| `web` ohne `ports` | Keine Host-Ports; Traefik erreicht Port 8000 über das externe Netz | CR 0.1. |
| `traefik.enable=true`, `traefik.docker.network` | Immer gesetzt | Zwingend bei `exposedByDefault=false`, unschädlich sonst; das Netz-Label macht die Wahl bei drei Netzen deterministisch. |
| Header-Middleware | HSTS ohne Subdomains, `nosniff`, `SAMEORIGIN`, Referrer-Policy | Abschnitt 2.3. |
| Redirect-Router | Nur ohne globalen Redirect in Traefik | Doppelte Redirects sind unschädlich, aber unnötig; Entscheidung nach Serverbefund. |
| `--forwarded-allow-ips ${TRUSTED_PROXY_CIDR}` | Nur Traefik darf `X-Forwarded-*` setzen | Sonst könnte ein Client die eigene IP im Audit fälschen. |
| `$$(` in `command` und `healthcheck` | Doppeltes Dollarzeichen | Compose interpoliert `$`; `$$` übergibt ein literales `$` an die Shell im Container. |
| Heartbeat-Datei als Healthcheck | Worker und Beat schreiben alle 30 s `/tmp/heartbeat` (ANNAHME AB24) | Stackneutral, keine Verbindung zur Queue nötig, erkennt hängende Prozesse. |
| `stop_grace_period` | `WORKER_STOP_GRACE` (ANNAHME AB23: 120 s) | Ein OCR-Chunk soll beim Deployment zu Ende laufen; unfertige Jobs übernimmt der Sweeper. |
| `OMP_THREAD_LIMIT=1` | Ein OCR-Prozess belegt genau einen Kern | Sonst überzeichnet Tesseract die Kerne. |
| `--pool threads` für `worker-io` | Netzwerkarbeit wartet, rechnet nicht | Gilt bei Stack A; Threads sind für I/O-gebundene Aufgaben ausreichend und sparen Speicher. |
| `worker-nlp` nur im Netz `data` | Klassifikation ist lokal | Umsetzungsplan D.1. Kein Weg ins Internet für den Dienst, der Dokumenttext verarbeitet. |
| `redis` mit Passwort, `noeviction`, AOF | Kein stiller Verlust von Aufträgen | Redis transportiert nur Job-IDs; bei vollem Speicher meldet er Fehler statt Aufträge zu verwerfen. `REDIS_IMAGE` erlaubt einen kompatiblen Fork (Anhang C). |
| `db` mit `skip-name-resolve`, striktem SQL-Modus, utf8mb4 | Umlaute in Ordner- und Personennamen | Datenmodell D Abschnitt 1. |
| `MARIADB_AUTO_UPGRADE` | Systemtabellen werden bei Minor-Anhebung angepasst | Vor Major-Anhebungen trotzdem Dump und Test. |
| `backup` liest Nutzdaten nur lesend (`:ro`) | Einziger Schreibpfad ist `/backup` | Der Sicherungsprozess kann Nutzdaten nicht beschädigen. |
| `/srv/objektakte/backup` in `web` als Verzeichnis gemountet, nicht als einzelne Datei | `status.json` wird atomar ersetzt (Umbenennen) | Ein Bind-Mount einer einzelnen Datei bricht beim Umbenennen; die Anwendung liest nur `status.json`. |
| `classifier` als Profil | Startet nur mit `docker compose --profile classifier up -d` | CR 7 optional; Aktivierung erst bei unzureichender Trefferquote des linearen Modells. |
| Uploads großer Dateien | Keine eigene Begrenzung in Traefik oder gunicorn über `GUNICORN_TIMEOUT` hinaus | ANNAHME AB18: Die Traefik-Entrypoint-Timeouts erlauben Uploads von mehreren hundert MB; Test in M5, sonst Chunked-Upload in der Anwendung. |
| `cpu_shares` | `web` 1024, `db` 768, `worker-io` 512, `worker-nlp` 512, `worker` 256, `beat` 256 | Gewichte wirken nur bei Konkurrenz; harte Limits stehen in `deploy.resources.limits`. Ob die installierte Compose-Version beides außerhalb von Swarm auswertet, prüft `docker compose config` (ANNAHME AB15). |

### 3.7 `.env.example`

Grundsätze: `.env.example` liegt im Repository und enthält alle Schlüssel ohne Werte; Sollwerte und Formeln stehen als Kommentar. `.env` liegt nur auf dem Server im Checkout-Verzeichnis (`0600`, Eigentümer Deploy-Nutzer, in `.gitignore`). Geheimnisse stehen nie in `.env` (Abschnitt 3.8). Fachliche Konfiguration (KI-Modell, Endpunkt, Region, Timeout, Kostenlimit, Reihenfolge Primär und Fallback, Schwellwerte, Stale-Fristen, Aufbewahrung der Vorschaubilder) liegt in `app_settings` und nicht hier (Beschluss B-06; Prüfberichte K02, K2-10, K3-03, K4-05).

```dotenv
# ------------------------------------------------------------------
# Objektakte: Startparameter (Vorlage). Nach .env kopieren, Werte eintragen.
# Keine Geheimnisse in dieser Datei (siehe docs/betrieb.md Abschnitt 3.8).
# Fachliche Konfiguration liegt in app_settings, nicht hier.
# ------------------------------------------------------------------

# --- Deployment ---
IMAGE_TAG=                       # setzt scripts/deploy.sh (Git-SHA, 12 Zeichen)
APP_UID=                         # UID der Anwendungscontainer; ANNAHME AB1: 10001; muss zu chown unter /srv/objektakte passen
APP_ENV=                         # production | staging | development
TZ=                              # Sollwert Europe/Berlin; Ist-Wert aus timedatectl (Ergebnisblatt)
APP_DOMAIN=                      # uebernahme.muellerhv.de (CR 0.1)
APP_BASE_URL=                    # https://uebernahme.muellerhv.de
TESSDATA_VARIANT=                # best | fast; Build-Argument des Ziels worker; Entscheidung nach M0 (F18)

# --- Traefik (Werte aus dem Serverbefund, Abschnitt 1.6) ---
TRAEFIK_NETWORK=                 # Name des externen Docker-Netzes von Traefik
TRAEFIK_ENTRYPOINT=              # Name des HTTPS-Entrypoints (Port 443)
TRAEFIK_ENTRYPOINT_INSECURE=     # Name des HTTP-Entrypoints (Port 80), nur fuer den Redirect-Router
TRAEFIK_CERTRESOLVER=            # Name des Let's-Encrypt-Resolvers
TRAEFIK_REDIRECT_ROUTER=         # true, wenn Traefik keinen globalen Redirect hat; sonst false und Labels entfernen
TRUSTED_PROXY_CIDR=              # Subnetz des Traefik-Netzes aus docker network inspect

# --- Image-Tags (aktuelle LTS bzw. stabile Version zum Umsetzungszeitpunkt pruefen) ---
MARIADB_TAG=
REDIS_IMAGE=                     # redis oder kompatibler Fork (Anhang C)
REDIS_TAG=

# --- Datenbank (nicht geheim; Passwoerter als Secrets) ---
DB_NAME=                         # objektakte
DB_USER=                         # app_rw (web, beat)
DB_WORKER_USER=                  # app_worker (worker, worker-nlp, worker-io, classifier)
DB_MIGRATE_USER=                 # app_migrate (nur deploy.sh, Schritt Migration)
DB_BACKUP_USER=                  # app_backup (nur backup)
DB_MAX_CONNECTIONS=              # Formel 3.10: GUNICORN_WORKERS x GUNICORN_THREADS + OCR_PROCESSES + NLP_CONCURRENCY + IO_CONCURRENCY + 10
DB_INNODB_BUFFER_POOL=           # Formel 3.10: 0,5 x DB_MEM, Schreibweise z. B. 1G

# --- Prozessdimensionierung (Formel 3.10, aus nproc und free -h) ---
GUNICORN_WORKERS=                # ANNAHME AB4: 3
GUNICORN_THREADS=                # ANNAHME AB4: 4 (gthread)
GUNICORN_TIMEOUT=                # ANNAHME AB23: 120 (Sekunden)
OCR_PROCESSES=                   # P = max(1, C minus 1); nur hier, nicht in app_settings
NLP_CONCURRENCY=                 # L = 1; 2 ab C groesser oder gleich 6 (ANNAHME AB3)
IO_CONCURRENCY=                  # ANNAHME AB5: 4 Threads; Drive-Quota beachten
WORKER_MAX_TASKS_PER_CHILD=      # ANNAHME AB23: 50
WORKER_MAX_MEMORY_PER_CHILD_KB=  # m_ocr in KB (Messwert aus M0; ANNAHME AB2: 786432 fuer 0,75 GiB)
WORKER_STOP_GRACE=               # ANNAHME AB23: 120s

# --- Ressourcenlimits (Formel 3.10) ---
WEB_CPUS=
WEB_MEM=
WORKER_CPUS=
WORKER_MEM=
WORKER_NLP_CPUS=
WORKER_NLP_MEM=
WORKER_IO_CPUS=
WORKER_IO_MEM=
BEAT_CPUS=
BEAT_MEM=
DB_CPUS=
DB_MEM=
REDIS_CPUS=
REDIS_MEM=
REDIS_MAXMEMORY=                 # REDIS_MEM minus 56 MiB, Schreibweise z. B. 200mb
BACKUP_CPUS=
BACKUP_MEM=
CLASSIFIER_CPUS=                 # nur bei Profil classifier
CLASSIFIER_MEM=

# --- Logging ---
LOG_LEVEL=                       # INFO
LOG_FORMAT=                      # json
LOG_MAX_SIZE=                    # ANNAHME AB22: 20m
LOG_MAX_FILE=                    # ANNAHME AB22: 5

# --- Speicherplatz ---
DISK_RESERVE_GB=                 # max(10, 0,1 x D); Ingest stoppt, wenn weniger frei ist

# --- Backup ---
BACKUP_CRON=                     # ANNAHME AB10: 30 2 * * * (taeglich 02:30 Serverzeit)
BACKUP_RETENTION_DAYS=           # Entscheidung Auftraggeber (F26), Vorschlag 30
BACKUP_MAX_AGE_HOURS=            # ANNAHME AB10: 26; Healthcheck rot, wenn die letzte Sicherung aelter ist
OFFSITE_ENABLED=                 # false bis zur Entscheidung F26
OFFSITE_REMOTE=                  # rclone-Remote und Pfad, nur bei OFFSITE_ENABLED=true

# --- Google Drive und OAuth (Abschnitt 7) ---
GOOGLE_CLIENT_ID=
GOOGLE_REDIRECT_URI=             # https://uebernahme.muellerhv.de/auth/google/callback
GOOGLE_LOGIN_REDIRECT_URI=       # https://uebernahme.muellerhv.de/auth/google/login/callback
DRIVE_ACCOUNT_EMAIL=             # ablage@muellerhv.de (CR 0.1)
DRIVE_SCOPES=                    # https://www.googleapis.com/auth/drive
GOOGLE_LOGIN_ENABLED=            # false bis F14
GOOGLE_LOGIN_HOSTED_DOMAIN=      # muellerhv.de

# --- KI-Provider: nur Basis-URLs (B-06). Modell, Region, Timeout, Budget in app_settings ---
OPENAI_BASE_URL=                 # EU-Endpunkt, sobald vom Anbieter bestaetigt (V-12)
ANTHROPIC_BASE_URL=              # EU-Endpunkt, sobald vom Anbieter bestaetigt (V-12)

# --- E-Mail (Alarmierung optional, Abschnitt 6.5, F27) ---
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_FROM=
ALERT_EMAIL_TO=
ALERTS_ENABLED=                  # false bis F27

# --- Sitzungen und Login (ANNAHME AB13, Entscheidung F28) ---
SESSION_IDLE_MINUTES=            # Vorschlag 480
SESSION_ABSOLUTE_HOURS=          # Vorschlag 12
LOGIN_MAX_ATTEMPTS=              # Vorschlag 5
LOGIN_LOCKOUT_MINUTES=           # Vorschlag 15
```

Hinweis zur Bezeichnung: Eine vollständige Verbindungs-URL enthielte das Passwort und gehört nicht in `.env`. Die Anwendung setzt die URL aus `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` bzw. `DB_WORKER_USER` und dem Inhalt der `*_FILE`-Variablen zusammen (gleichlautend in Umsetzungsplan D.5).

### 3.8 Secrets anlegen

Dateien unter `/srv/objektakte/secrets/`, je eine Zeile ohne Zeilenumbruch (`printf`, nicht `echo`). Kryptografische Schlüssel werden zufällig erzeugt; Client-Secret und API-Schlüssel stammen aus den Anbieterkonsolen und werden direkt auf dem Server eingetragen, nie per E-Mail oder Chat übertragen (V-07, V-13).

| Datei | Inhalt | Erzeugung | Verwendet von |
|---|---|---|---|
| `db_root_password` | Root-Passwort MariaDB (Initialisierung, Wartung, Wiederherstellung) | `openssl rand -base64 32` | `db` |
| `db_app_password` | Passwort `app_rw` | `openssl rand -base64 32` | `web`, `beat`, `db` |
| `db_worker_password` | Passwort `app_worker` | `openssl rand -base64 32` | Worker, `classifier`, `db` |
| `db_migrate_password` | Passwort `app_migrate` (DDL) | `openssl rand -base64 32` | `web` (nur `app-migrate`), `db` |
| `db_backup_password` | Passwort `app_backup` | `openssl rand -base64 32` | `backup`, `db` |
| `db_ro_password` | Passwort `app_ro` (Auswertungen, Support) | `openssl rand -base64 32` | `db` |
| `redis_password` | Redis `requirepass` | `openssl rand -base64 32` | alle Anwendungsdienste, `redis` |
| `app_secret_key` | Framework-Schlüssel für Sitzungen und CSRF | `openssl rand -base64 48` | `web`, `beat`, Worker |
| `iban_key` | AES-256-Schlüssel IBAN, Version 1 (Verwendung nach F16) | `openssl rand -base64 32` | Anwendungsdienste |
| `iban_hmac_key` | HMAC-Schlüssel `iban_hash` | `openssl rand -base64 32` | Anwendungsdienste |
| `token_key` | AES-256-Schlüssel OAuth-Tokens | `openssl rand -base64 32` | Anwendungsdienste |
| `totp_key` | AES-256-Schlüssel TOTP-Geheimnisse | `openssl rand -base64 32` | `web` |
| `google_client_secret` | OAuth-Client-Secret | Google Cloud Console (Abschnitt 7.5) | `web`, `worker-io`, `beat` |
| `openai_api_key`, `anthropic_api_key` | API-Schlüssel | Anbieterkonsolen (V-13) | `worker-io` |
| `smtp_password` | SMTP-Passwort für Alarmierung (leer, wenn deaktiviert) | Mailanbieter (V-23) | `beat`, `web` |
| `readyz_token` | Bearer-Token für `/readyz/` und Smoke-Test | `openssl rand -hex 32` | `web`, `deploy.sh` |
| `backup_age_recipient` | Öffentlicher age-Schlüssel für die Backup-Verschlüsselung; privater Schlüssel nur beim Auftraggeber | `age-keygen` auf dem Rechner des Auftraggebers, nur der öffentliche Teil auf den Server | `backup` |
| `rclone.conf` | Zugang zum Offsite-Ziel (leer, wenn deaktiviert) | Anbieter (V-22) | `backup` |

```bash
cd /srv/objektakte/secrets
for n in db_root_password db_app_password db_worker_password db_migrate_password db_backup_password db_ro_password \
         redis_password iban_key iban_hmac_key token_key totp_key; do
  [ -f "$n" ] || printf '%s' "$(openssl rand -base64 32)" | sudo tee "$n" >/dev/null
done
[ -f app_secret_key ] || printf '%s' "$(openssl rand -base64 48)" | sudo tee app_secret_key >/dev/null
[ -f readyz_token ]   || printf '%s' "$(openssl rand -hex 32)"    | sudo tee readyz_token >/dev/null
# Platzhalter fuer optionale Secrets, damit Compose die Dateien findet
for n in google_client_secret openai_api_key anthropic_api_key smtp_password backup_age_recipient rclone.conf; do
  [ -f "$n" ] || sudo touch "$n"
done
sudo chmod 600 /srv/objektakte/secrets/*; sudo chown root:root /srv/objektakte/secrets/*
ls -l /srv/objektakte/secrets
```

Verwahrung außerhalb des Servers (V-21): Die Verschlüsselungsschlüssel (`iban_key`, `iban_hmac_key`, `token_key`, `totp_key`), `app_secret_key`, das Root-Passwort und der private age-Schlüssel werden im Passwortmanager der Geschäftsführung bei mindestens zwei Personen hinterlegt. Ohne sie sind IBAN-Chiffrate, OAuth-Tokens und TOTP-Geheimnisse einer wiederhergestellten Sicherung unbrauchbar. Die Hinterlegung wird in jeder Wiederherstellungsprobe (Abschnitt 5.6) geprüft. Planmäßige Rotation (ANNAHME AB14: jährlich, Frage F28) und Ablauf je Schlüssel nach `docs/architektur.md` 9.6. Die Dateinamen sind mit Umsetzungsplan D.5 und `docs/architektur.md` 5.7 gleichlautend.

### 3.9 Datenbankkonten und Initialisierung

Das MariaDB-Image legt beim ersten Start die Datenbank und den Nutzer `MARIADB_USER` (`app_rw`) an. Die übrigen Konten, Rechte und Trigger legt ein Init-Skript unter `docker/db/init/` an, das die Passwörter aus `/run/secrets/` liest (das Image führt Skripte im Init-Verzeichnis nur bei leerem Datenverzeichnis aus). Beschluss B-43; Umsetzungsplan M1 Schritt 5.

| Konto | Rechte | Verwendet von |
|---|---|---|
| `app_migrate` | DDL und DML auf der Datenbank `objektakte` (CREATE, ALTER, DROP, INDEX, REFERENCES, TRIGGER, alle DML) | `deploy.sh` Schritt Migration, Seeds |
| `app_rw` | DML auf allen Fachtabellen; auf `audit_events` und `iban_access_log` nur INSERT und SELECT | `web`, `beat` |
| `app_worker` | SELECT auf Stammdaten und Kataloge; INSERT und UPDATE auf Verarbeitungs-, Dokument-, Review-Fall- und Protokolltabellen; INSERT auf `audit_events`; kein Schreibrecht auf `owners`, `units`, `owner_unit_assignments`, `tenants`, `leases`, `users`, `app_settings` | `worker`, `worker-nlp`, `worker-io`, `classifier` |
| `app_backup` | SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, EVENT (zum Umsetzungszeitpunkt gegen die Dokumentation von `mariadb-dump` prüfen) | `backup` |
| `app_ro` | SELECT auf Fachtabellen ohne Chiffratspalten | Auswertungen, Support |

Pflicht (B-43): Trigger `BEFORE UPDATE` und `BEFORE DELETE` auf `audit_events` und `iban_access_log`, die mit `SIGNAL SQLSTATE '45000'` abbrechen. Damit sind die Protokolle auch für `app_migrate` und `root` nicht ohne vorheriges Entfernen des Triggers änderbar; das Entfernen selbst ist im Binärlog sichtbar. Die genaue Tabellenliste je Konto entsteht mit dem Bezeichnerregister in M2 und wird in `docker/db/init/` gepflegt; ein Test in der CI prüft, dass ein UPDATE auf `audit_events` fehlschlägt (M1 Schritt 8).

Skizze `docker/db/init/01_accounts.sh` (läuft im Entrypoint des Images, Variablen des Images verfügbar):

```bash
#!/bin/sh
set -eu
pw() { cat "/run/secrets/$1"; }
mariadb -uroot -p"$(pw db_root_password)" <<SQL
CREATE USER IF NOT EXISTS 'app_migrate'@'%' IDENTIFIED BY '$(pw db_migrate_password)';
CREATE USER IF NOT EXISTS 'app_worker'@'%'  IDENTIFIED BY '$(pw db_worker_password)';
CREATE USER IF NOT EXISTS 'app_backup'@'%'  IDENTIFIED BY '$(pw db_backup_password)';
CREATE USER IF NOT EXISTS 'app_ro'@'%'      IDENTIFIED BY '$(pw db_ro_password)';
GRANT ALL PRIVILEGES ON \`${MARIADB_DATABASE}\`.* TO 'app_migrate'@'%';
GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, EVENT ON \`${MARIADB_DATABASE}\`.* TO 'app_backup'@'%';
-- Rechte von app_rw, app_worker und app_ro je Tabelle: 02_grants.sql (entsteht mit dem Schema in M2)
FLUSH PRIVILEGES;
SQL
```

### 3.10 Ressourcenformel

Eingangsgrößen aus dem Serverbefund: `[C]` Kerne, `[M]` RAM in GiB, `[D]` freie Platte in GiB. Keine dieser Größen ist bekannt. Alle Faktoren sind Annahmen (Prüfbericht K4-19) und werden im OCR-Probelauf (M0) und im Performance-Test (M13) durch Messwerte ersetzt.

| Variable | Formel | Herleitung und Verifikation |
|---|---|---|
| `OCR_PROCESSES` (P) | `max(1, C minus 1)` | CR 7. Nur in `.env`. Bei C kleiner oder gleich 4 im Performance-Test zusätzlich P = C minus 2 vergleichen, weil `web`, `db` und `redis` sonst um einen Kern konkurrieren. |
| `WORKER_CPUS` | `P` | Hartes Limit; der Worker belegt nie mehr als P Kerne. |
| `WORKER_MEM` | `P mal m_ocr plus 0,5 GiB` | ANNAHME AB2: m_ocr = 0,75 GiB je OCR-Prozess. Verifikation: `docker stats` und cgroup `memory.peak` im Probelauf. |
| `WORKER_MAX_MEMORY_PER_CHILD_KB` | `m_ocr in KB` | Kindprozess wird recycelt, bevor das Container-Limit greift. |
| `NLP_CONCURRENCY` (L) | `1`; `2` bei C größer oder gleich 6 | ANNAHME AB3. |
| `WORKER_NLP_CPUS`, `WORKER_NLP_MEM` | `L`, `L mal m_nlp plus 0,3 GiB` | ANNAHME AB3: m_nlp = 0,8 GiB je Prozess mit spaCy und Klassifikator. Verifikation `docker stats`. |
| `WORKER_IO_CPUS`, `WORKER_IO_MEM`, `IO_CONCURRENCY` | `0,5`, `0,5 GiB`, `4` | ANNAHME AB5. Verifikation während Drive-Uploads und KI-Aufrufen. |
| `WEB_CPUS`, `WEB_MEM`, `GUNICORN_WORKERS`, `GUNICORN_THREADS` | `1,0`, `1 GiB`, `3`, `4` | ANNAHME AB4 (gthread, gilt bei Stack A). Verifikation `docker stats` mit 2 bis 5 Nutzern (Nutzerkreis ANNAHME AB20). |
| `DB_CPUS`, `DB_MEM`, `DB_INNODB_BUFFER_POOL` | `1,0`, `max(1 GiB, 0,15 mal M)`, `0,5 mal DB_MEM` | ANNAHME AB6. |
| `DB_MAX_CONNECTIONS` | `GUNICORN_WORKERS mal GUNICORN_THREADS plus P plus L plus IO_CONCURRENCY plus 10` | Jeder Prozess oder Thread hält höchstens eine Verbindung; Reserve für Migration, Backup, Shell. |
| `REDIS_CPUS`, `REDIS_MEM`, `REDIS_MAXMEMORY` | `0,5`, `256 MiB`, `REDIS_MEM minus 56 MiB` | ANNAHME AB7. Der Broker transportiert nur IDs. |
| `BEAT_CPUS`, `BEAT_MEM` | `0,25`, `128 MiB` | ANNAHME AB7. |
| `BACKUP_CPUS`, `BACKUP_MEM` | `0,5`, `256 MiB` | ANNAHME AB7. |
| `CLASSIFIER_CPUS`, `CLASSIFIER_MEM` | `1,0`, `1,5 GiB` | ANNAHME AB7, nur bei aktiviertem Profil. |
| `DISK_RESERVE_GB` | `max(10, 0,1 mal D)` | ANNAHME AB8. Ingest bricht ab, bevor die Platte voll ist; bezieht den Vorschauspeicher ein (ANNAHME AB29: rund 1,5 GB je 10.000 Seiten). |
| `BACKUP_RETENTION_DAYS` | so, dass `Tage mal tägliche Sicherungsgröße kleiner als 0,3 mal D` | ANNAHME AB28. Sicherungsgröße nach dem ersten Objekt messen; Wert legt der Auftraggeber fest (F26). |

Randbedingung: Summe aller `*_MEM` ohne `classifier` kleiner oder gleich `0,85 mal M` (ANNAHME AB8). Der Rest bleibt für Host, Docker, Traefik und Seitencache. Ist die Bedingung verletzt, sinkt zuerst L auf 1, dann P schrittweise; der gewählte Wert wird mit Begründung in `docs/betrieb/serverbefund.md` dokumentiert. Bei knappem Arbeitsspeicher sinkt P unter C minus 1 und der Durchsatz mit; deshalb steht die Messung vor jeder Bauentscheidung (Frage F18).

Rechenweg mit frei gewählten Werten, ausdrücklich keine Aussage über den VPS: C = 6, M = 12 GiB. P = 5, L = 2. `WORKER_MEM` = 5 mal 0,75 plus 0,5 = 4,25 GiB; `WORKER_NLP_MEM` = 2 mal 0,8 plus 0,3 = 1,9 GiB; `DB_MEM` = max(1; 1,8) = 1,8 GiB; `WEB_MEM` = 1 GiB; `WORKER_IO_MEM` = 0,5 GiB; `REDIS_MEM` plus `BEAT_MEM` plus `BACKUP_MEM` = 0,625 GiB. Summe 10,075 GiB = 84 Prozent von M, Bedingung knapp erfüllt; bei L = 1 wären es 9,275 GiB = 77 Prozent. `DB_MAX_CONNECTIONS` = 3 mal 4 plus 5 plus 2 plus 4 plus 10 = 33.

### 3.11 Prüfung vor dem ersten Start

```bash
cd /opt/objektakte
docker compose config --quiet && echo "Compose-Datei gueltig"
docker compose config | grep -A3 'limits:' | head -60          # Limits sind aufgeloest, keine leeren Werte
docker network inspect "$(grep -E '^TRAEFIK_NETWORK=' .env | cut -d= -f2 | awk '{print $1}')" >/dev/null && echo "externes Netz vorhanden"
ls -l /srv/objektakte/secrets | awk '$5==0 {print "leer:", $9}'  # leere Secret-Dateien nur bei optionalen Diensten
```

---

## 4. Deployment und Rollback

### 4.1 Grundsätze

- Jedes Deployment ist ein Git-Commit auf dem Server, zwei Images (`objektakte/web`, `objektakte/worker`) mit dem Git-SHA als Tag und eine Zeile in `/srv/objektakte/deploy/tags.log`.
- Migrationen laufen als eigener Schritt vor `up -d`, nie beim Container-Start (Datenmodell D Abschnitt 13). Ein Container, der beim Start migriert, geriete bei einer fehlgeschlagenen Migration in eine Neustartschleife. Vor jeder Migration ein Dump.
- Im laufenden Betrieb werden nur rückwärtskompatible Migrationen eingespielt (Muster Erweitern und Zusammenziehen): Das neue Schema muss vom zuvor laufenden Image fehlerfrei verwendet werden können. Ein Image-Rollback braucht deshalb keinen Schema-Rollback.
- Ausfallzeit: Sekunden während des Containerwechsels. Deployments außerhalb laufender Objektverarbeitung planen (Zeitfenster Frage F27). Läuft dennoch ein Objekt, beenden die Worker den aktuellen Chunk (`WORKER_STOP_GRACE`), der Sweeper setzt fort.
- Images werden auf dem VPS gebaut, außerhalb laufender Verarbeitung (Standard nach F27; Alternative GitHub Actions mit Registry).
- `app-migrate`, `app-seed`, `app-create-admin` sind Unterbefehle des Container-Entrypoints und zeigen auf die Werkzeuge des Stacks (gilt bei Stack A: `python manage.py migrate`, Seed-Kommando, `createsuperuser` mit Rollenzuordnung). `app-migrate` verbindet sich als `DB_MIGRATE_USER`.

### 4.2 `scripts/deploy.sh`

Aufruf: `scripts/deploy.sh [branch]` für jedes Deployment, `scripts/deploy.sh --first-run [branch]` für die Erstinstallation (Beschluss B-46; Prüfberichte K3-15, K4-20). Der Erstinstallationspfad startet `db`, `redis` und `backup` zuerst, wartet auf `healthy`, überspringt den Dump bei leerer Datenbank, führt Migration und Seeds aus und startet dann alle Dienste.

```bash
#!/usr/bin/env bash
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

wait_healthy() {   # wait_healthy <sekunden> [dienst ...]
  local end=$((SECONDS + $1)); shift
  while [ "$SECONDS" -lt "$end" ]; do
    if ! docker compose ps --format '{{.Name}} {{.Health}}' "$@" | grep -vqE ' healthy$'; then return 0; fi
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
if $FIRST_RUN; then docker compose run --rm --no-deps web app-seed; fi

# 6. Container wechseln
docker compose up -d --remove-orphans

# 7. Auf Healthchecks warten (ANNAHME AB27: hoechstens 3 min)
wait_healthy 180

# 8. Smoke-Test ueber Traefik (TLS, Anwendung); beim ersten Lauf kann die Zertifikatsausstellung Sekunden dauern
curl -fsS --retry 6 --retry-delay 10 -o /dev/null -w 'healthz %{http_code} tls %{ssl_verify_result}\n' "https://${APP_DOMAIN}/healthz/"
curl -fsS "https://${APP_DOMAIN}/readyz/" \
  -H "Authorization: Bearer $(sudo cat /srv/objektakte/secrets/readyz_token 2>/dev/null || echo none)" | head -c 400; echo

# 9. Tags fortschreiben
[ "$CUR_TAG" != none ] && echo "$CUR_TAG" > "$DEPLOY_DIR/previous"
echo "$NEW_TAG" > "$DEPLOY_DIR/current"
for img in web worker backup; do
  docker tag "objektakte/$img:$NEW_TAG" "objektakte/$img:current"
  [ "$CUR_TAG" != none ] && docker tag "objektakte/$img:$CUR_TAG" "objektakte/$img:previous"
done
printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "$NEW_TAG" "$CUR_TAG" "$(whoami)" "$($FIRST_RUN && echo FIRST-RUN || echo DEPLOY)" >> "$DEPLOY_DIR/tags.log"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$NEW_TAG/" .env

# 10. Alte Images aufraeumen, die letzten N behalten (current, previous, dev bleiben)
KEEP=${IMAGE_KEEP:-5}
for img in web worker backup; do
  docker images "objektakte/$img" --format '{{.Tag}} {{.CreatedAt}}' \
    | grep -vE '^(current|previous|dev) ' | sort -k2 -r | tail -n +$((KEEP + 1)) | awk '{print $1}' \
    | xargs -r -I{} docker rmi "objektakte/$img:{}" || true
done
echo "Deployment $NEW_TAG abgeschlossen"
```

Hinweise: Schritt 9 schreibt `IMAGE_TAG` in `.env`, damit ein späteres `docker compose up -d` ohne Skript denselben Stand verwendet. Der Aufruf von `/readyz/` liefert eine JSON-Übersicht (Datenbank, Redis, Platte, Token-Status, Schema-Version) und ist nur mit Token erreichbar. Beim ersten Lauf legt `app-seed` Kataloge, Unterordner, Rollen und die in M1 benötigten `app_settings` an; anschließend wird der erste Admin angelegt (Abschnitt 4.5).

### 4.3 `scripts/rollback.sh` (ein Befehl)

```bash
#!/usr/bin/env bash
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
```

Die Umgebungsvariable `IMAGE_TAG` in der Shell hat Vorrang vor dem Wert in `.env`, deshalb reicht der eine Befehl. Die Images mit Tag `previous` sind von der Aufräumung in `deploy.sh` ausgenommen.

### 4.4 Rollback einer Datenbankmigration (Ausnahme)

Nur nötig, wenn eine Migration selbst fehlerhaft ist (falscher Index, falsche Default-Werte), nicht wenn der Anwendungscode fehlerhaft ist.

1. Anwendung anhalten: `docker compose stop web worker worker-nlp worker-io beat`.
2. Dump: `docker compose exec -T backup /usr/local/bin/backup.sh`.
3. Genau eine Version zurück: `IMAGE_TAG=$(cat /srv/objektakte/deploy/current) docker compose run --rm --no-deps web app-migrate --down 1` (gilt bei Stack A: `python manage.py migrate <app> <vorherige_migration>`). Jede Migration hat eine Rückwärtsfunktion; ohne Rückwärtsfunktion keine Freigabe.
4. Image zurücksetzen, falls nötig: `scripts/rollback.sh`.
5. Starten: `docker compose up -d`, Smoke-Test.
6. Eintrag in `tags.log` und im Deployment-Protokoll.

Migrationen, die nicht rückrollbar sind (Verlust nach der Migration entstandener Daten), werden im Umsetzungsplan je Meilenstein vorab benannt und brauchen eine Vorwärtskorrektur. In der CI wird jede Migration vorwärts und rückwärts auf leerer Datenbank ausgeführt (Test T8).

### 4.5 Erstinstallation (Kurzform)

1. Serverbefund (Abschnitt 1), Host-Härtung (Abschnitt 2), DNS-A-Record vorhanden (V-04).
2. Verzeichnisse (Abschnitt 3.3), Secrets (Abschnitt 3.8), `.env` aus `.env.example` mit den Werten des Ergebnisblatts (Abschnitt 1.6).
3. `sudo mkdir -p /opt/objektakte && sudo chown deploy:deploy /opt/objektakte && git clone [REPO_URL] /opt/objektakte && cd /opt/objektakte`.
4. `scripts/deploy.sh --first-run main`.
5. Ersten Admin anlegen: `docker compose exec web app-create-admin --email [ADMIN_ADRESSE]`; TOTP beim ersten Login einrichten. Zweiten Admin anlegen (Empfehlung F28).
6. Deployment-Tests T1, T2, T3, T7, T8, T11, T12, T13, T14 durchführen und protokollieren (Abschnitt 9).
7. Google-Verbindung nach Abschnitt 7.7 herstellen, sobald die OAuth-App vorliegt (V-06); 8-Tage-Nachweis starten.
8. Wiederherstellung einmal in Grundform proben (Abschnitt 5.4, ohne Fachdaten).

### 4.6 Auslegung des Deployment-Tests T1

CR 14 verlangt „`docker compose up -d` auf frischem Checkout". Ein reines `up -d` würde Container ohne Schema starten, weil Migrationen bewusst nicht beim Container-Start laufen. Dieses Dokument legt T1 als `scripts/deploy.sh --first-run` aus (Frage F20 des Umsetzungsplans, Vorschlag mit Begründung); nach dem ersten Durchlauf funktioniert ein reines `docker compose up -d` (Neustart, Test T4) ohne Skript, weil `IMAGE_TAG` in `.env` steht und das Schema vorliegt.

---

## 5. Backup und Wiederherstellung

### 5.1 Umfang und Ablage

| Was | Wie | Ziel | Bemerkung |
|---|---|---|---|
| Datenbank | `mariadb-dump --single-transaction --routines --triggers --events --hex-blob` als `app_backup`, gzip | `/srv/objektakte/backup/db/objektakte_[JJJJMMTT_HHMMSS].sql.gz` | Konsistenter Stand ohne Tabellensperren (InnoDB). `--hex-blob` wegen der `VARBINARY`-Chiffrate. |
| Fachverzeichnisse | `tar --create --gzip` je Verzeichnis `transit`, `ocr-cache`, `models`, `lists`, `requests`, `imports`, `exports` | `/srv/objektakte/backup/volumes/[name]_[JJJJMMTT_HHMMSS].tar.gz` | Beschluss B-14. |
| Nicht gesichert | `work/` (Zwischenstand, reproduzierbar aus Drive und OCR), `previews/` (reproduzierbar aus dem Dokument), `redis/` (nur Job-IDs; nach Wiederherstellung reiht der Sweeper offene Jobs neu ein) | | Begründung in Abschnitt 3.3. |
| Konfiguration | Kopie von `.env`, `docker-compose.yml` und `deploy/` (Tags) | `/srv/objektakte/backup/config/` | Secrets werden nicht in das Backup kopiert; sie liegen im Passwortmanager (V-21). |
| Status | `status.json` mit Zeitpunkt, Dauer, Größen, Prüfsummen, Ergebnis | `/srv/objektakte/backup/status.json` | Gelesen von Statusseite und Healthcheck. |

Zeitpunkt: `BACKUP_CRON` (ANNAHME AB10: 02:30 Serverzeit, in der Regel außerhalb der Objektverarbeitung). Läuft dennoch ein Job, bleibt der Dump konsistent; Archive können sich ändernde Dateien in `transit/` enthalten (Tar meldet Rückgabewert 1, wird als Warnung protokolliert). `ocr-cache/` ist inhaltsadressiert und wird nur ergänzt, ein Archiv ist daher immer in sich konsistent.

Aufbewahrung: `BACKUP_RETENTION_DAYS` (Vorschlag 30, Entscheidung F26). Ältere Dateien werden erst nach erfolgreicher neuer Sicherung gelöscht, nie vorher. Die Aufbewahrungsfristen der Fachdaten (`retention_policies`) sind davon unabhängig.

### 5.2 Container `backup`

`docker/backup/Dockerfile`: schlankes Basis-Image mit `mariadb-client`, `tar`, `gzip`, `jq`, `age`, `rclone`, Cron-Dienst (Paketnamen zum Umsetzungszeitpunkt prüfen, ANNAHME AB19). Der Entrypoint schreibt aus `BACKUP_CRON` eine Crontab, legt beim ersten Start eine `status.json` mit `status: started` an (sonst wäre der Healthcheck bis zum ersten Lauf rot) und startet `crond` im Vordergrund. Das Skript läuft mit Leserechten auf `/src/*` und Schreibrechten auf `/backup`.

`docker/backup/backup.sh` (Kern):

```bash
#!/bin/sh
set -eu
TS=$(date +%Y%m%d_%H%M%S); START=$(date +%s)
DBPW=$(cat "$DB_BACKUP_PASSWORD_FILE")
mkdir -p /backup/db /backup/volumes /backup/config
STATUS=ok; WARN=""
DUMP="/backup/db/${DB_NAME}_${TS}.sql.gz"

# 1. Datenbank: in .part schreiben, pruefen, atomar umbenennen
if MYSQL_PWD="$DBPW" mariadb-dump -h "$DB_HOST" -u "$DB_BACKUP_USER" \
     --single-transaction --routines --triggers --events --hex-blob "$DB_NAME" \
     | gzip -6 > "${DUMP}.part" && gzip -t "${DUMP}.part"; then
  mv "${DUMP}.part" "$DUMP"
else
  STATUS=failed; rm -f "${DUMP}.part"
fi

# 2. Fachverzeichnisse (B-14)
for d in transit ocr-cache models lists requests imports exports; do
  ARCH="/backup/volumes/${d}_${TS}.tar.gz"
  if tar --create --gzip --file "${ARCH}.part" --warning=no-file-changed -C /src "$d"; then
    mv "${ARCH}.part" "$ARCH"
  else
    rc=$?
    if [ "$rc" -eq 1 ]; then WARN="$WARN $d:changed"; mv "${ARCH}.part" "$ARCH"; else STATUS=failed; rm -f "${ARCH}.part"; fi
  fi
done

# 3. Konfiguration (ohne Geheimnisse)
cp /src/config/.env "/backup/config/env_${TS}" && cp /src/config/docker-compose.yml "/backup/config/docker-compose_${TS}.yml"
cp -r /src/deploy "/backup/config/deploy_${TS}"

# 4. Pruefsummen
( cd /backup && sha256sum db/*_${TS}.sql.gz volumes/*_${TS}.tar.gz 2>/dev/null ) > "/backup/checksums_${TS}.txt"

# 5. Offsite-Kopie (optional): verschluesseln, uebertragen; privater Schluessel liegt nicht auf dem Server
OFFSITE=skipped
if [ "${OFFSITE_ENABLED:-false}" = true ] && [ "$STATUS" = ok ]; then
  OFFSITE=ok
  for f in "$DUMP" /backup/volumes/*_${TS}.tar.gz "/backup/checksums_${TS}.txt"; do
    age -r "$(cat "$BACKUP_ENCRYPT_RECIPIENT_FILE")" -o "${f}.age" "$f" \
      && rclone copy "${f}.age" "$OFFSITE_REMOTE/$(date +%Y/%m)/" && rm -f "${f}.age" || OFFSITE=failed
  done
fi

# 6. Aufraeumen nur nach Erfolg
if [ "$STATUS" = ok ]; then
  find /backup/db /backup/volumes /backup/config -mindepth 1 -mtime "+${BACKUP_RETENTION_DAYS}" -exec rm -rf {} +
  find /backup -maxdepth 1 -name 'checksums_*' -mtime "+${BACKUP_RETENTION_DAYS}" -delete
fi

# 7. Status
jq -n --arg ts "$TS" --arg st "$STATUS" --arg warn "$WARN" --arg off "$OFFSITE" \
      --argjson dur $(( $(date +%s) - START )) \
      --argjson size "$(du -sb /backup | cut -f1)" \
      '{last_run:$ts,status:$st,warnings:$warn,offsite:$off,duration_s:$dur,total_bytes:$size}' > /backup/status.json.tmp
mv /backup/status.json.tmp /backup/status.json
[ "$STATUS" = ok ]
```

Eigenschaften: `.part`-Dateien mit atomarem Umbenennen (ein unvollständiges Archiv gilt zu keinem Zeitpunkt als gültig), Integritätsprüfung des Dumps, Löschung alter Sicherungen nur nach Erfolg, Prüfsummen je Lauf, Statusdatei für Monitoring, Offsite-Kopie nur verschlüsselt. Der Exit-Code steht im Container-Log; die Statusseite zeigt Status und Alter.

### 5.3 Manueller Lauf

`docker compose exec -T backup /usr/local/bin/backup.sh`. Das Deployment-Skript ruft genau dies vor jeder Migration auf. Ein Admin kann den Lauf nicht aus der Oberfläche starten; die Statusseite zeigt stattdessen diesen Befehl an (Festlegung für M1; ein Auftrag über Redis an den Backup-Container ist als spätere Erweiterung möglich).

### 5.4 Wiederherstellung auf leerer Instanz (Schritt für Schritt)

Voraussetzungen: Server nach Abschnitt 2 gehärtet, Docker und Traefik vorhanden, Verzeichnisse nach Abschnitt 3.3 angelegt, Secrets nach Abschnitt 3.8 aus dem Passwortmanager wiederhergestellt (nicht aus dem Backup), Sicherungsdateien nach `/srv/objektakte/backup/` kopiert (bei Offsite-Kopie zuvor mit dem privaten age-Schlüssel entschlüsselt: `age -d -i [PRIVATER_SCHLUESSEL] -o [DATEI] [DATEI].age`).

```bash
# 1. Checkout der Version, die zum Backup passt (Tag steht in backup/config/deploy_[TS]/current)
sudo mkdir -p /opt/objektakte && sudo chown deploy:deploy /opt/objektakte
git clone [REPO_URL] /opt/objektakte && cd /opt/objektakte
git checkout "$(cat /srv/objektakte/backup/config/deploy_[TS]/current)"
cp /srv/objektakte/backup/config/env_[TS] .env && chmod 600 .env
export IMAGE_TAG=$(git rev-parse --short=12 HEAD)

# 2. Nur Datenbank und Queue starten, Anwendung noch nicht
docker compose build
docker compose up -d db redis
docker compose ps                     # warten, bis db healthy ist

# 3. Dump einspielen (Datenbank wurde vom Image leer angelegt, Konten und Trigger durch das Init-Skript)
zcat /srv/objektakte/backup/db/objektakte_[TS].sql.gz | \
  docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" "$MARIADB_DATABASE"'

# 4. Fachverzeichnisse zurueckspielen, Rechte auf APP_UID
for d in transit ocr-cache models lists requests imports exports; do
  sudo tar --extract --gzip --file /srv/objektakte/backup/volumes/${d}_[TS].tar.gz -C /srv/objektakte
done
sudo chown -R 10001:10001 /srv/objektakte/{transit,ocr-cache,models,lists,requests,imports,exports}

# 5. Schema-Stand pruefen: Code-Version und Migrationstabelle muessen passen, sonst migrieren
docker compose run --rm --no-deps web app-migrate --check

# 6. Alle Dienste starten
docker compose up -d
docker compose ps                     # alle healthy
echo "$IMAGE_TAG" > /srv/objektakte/deploy/current

# 7. Smoke-Test
curl -fsS https://uebernahme.muellerhv.de/healthz/ && echo OK
# Anmeldung im Browser, Objektliste vergleichen, Statusseite: Job-Zaehler, letzte Sicherung, Token-Status
```

### 5.5 Nacharbeiten nach der Wiederherstellung

- OAuth: Die Tokens im Dump sind mit `TOKEN_KEY` verschlüsselt. Bei identischem Schlüssel funktioniert der Zugriff sofort (Statusseite zeigt `active`). Sonst Erstautorisierung nach Abschnitt 7.7 wiederholen.
- Offene Jobs: Der Sweeper setzt Jobs, die zum Dump-Zeitpunkt liefen, zurück und reiht sie neu ein. Doppelverarbeitung ist über `(object_id, sha256)` ausgeschlossen.
- Drive-Abbild: `drive_nodes` enthält Folder-IDs; der nächste Ordnerabgleich prüft sie gegen Drive und ergänzt, was seit dem Dump entstanden ist. Dateien, die nach dem Dump in Drive verschoben wurden, werden über den Hash als bekannt erkannt.
- Vorschaubilder fehlen bis zur Neuerzeugung (auf Anforderung beim Öffnen eines Falls).

### 5.6 Testnachweis

Die Wiederherstellung wird einmal in Grundform in M1 (ohne Fachdaten), vollständig vor Produktivstart (Test T6) und danach quartalsweise geprobt. Nachweis in `docs/betrieb/restore-protokoll.md`:

| Feld | Inhalt |
|---|---|
| Datum, Durchführender | |
| Verwendete Sicherung (Dateinamen, Prüfsummen aus `checksums_[TS].txt`) | |
| Zielumgebung | leere Instanz: zweiter VPS, lokale VM oder derselbe Server nach `docker compose down -v` und Leeren von `/srv/objektakte/db` |
| Quelle | lokal oder Offsite-Kopie (mindestens einmal aus der Offsite-Kopie, mit Entschlüsselung) |
| Dauer bis Smoke-Test grün | |
| Vergleich | Zeilen in `objects`, `units`, `owners`, `documents`, `review_cases` vor und nach; Anzahl Dateien in `ocr-cache`; Stichprobe: ein Dokument öffnen, eine Eigentümerakte anzeigen (synthetisches Testobjekt), ein Review-Fall bearbeiten |
| Schlüsselverwahrung geprüft | Zugriff auf den Passwortmanager durch zwei Personen bestätigt (V-21) |
| Abweichungen und Maßnahmen | |

### 5.7 Wiederherstellung einzelner Bestandteile

- Nur Datenbank auf einen Zeitpunkt: Schritte 2, 3, 5, 6. Vorher `docker compose stop web worker worker-nlp worker-io beat`, damit keine Schreibzugriffe laufen.
- Nur ein Verzeichnis (etwa `models/` nach fehlerhaftem Nachtraining): Schritt 4 für dieses Verzeichnis, danach `docker compose restart worker-nlp worker-io`.
- Einzelne Tabelle: aus dem Dump extrahieren (`zcat ... | sed -n '/CREATE TABLE \`[name]\`/,/UNLOCK TABLES/p'`) und in eine Hilfsdatenbank einspielen, dann gezielt kopieren. Nur durch den Entwickler, mit vorherigem vollständigem Dump.

### 5.8 Offsite-Kopie (Empfehlung)

Ein Backup auf derselben Maschine schützt vor Bedien- und Softwarefehlern, nicht vor Ausfall oder Kompromittierung des VPS. Empfehlung: tägliche Kopie der jüngsten Sicherung an einen Ort außerhalb des VPS.

- Verschlüsselung vor dem Transport mit `age`; der öffentliche Schlüssel liegt als `backup_age_recipient` auf dem Server, der private ausschließlich beim Auftraggeber im Passwortmanager. Der Server kann verschlüsseln, aber nicht entschlüsseln.
- Zielort: Entscheidung des Auftraggebers (Frage F26, V-22): Objektspeicher eines Anbieters mit AVV und EU-Standort, zweiter Server oder verschlüsselter Netzwerkspeicher. Nicht das Google-Drive-Konto `ablage@muellerhv.de`, weil sonst ein kompromittiertes OAuth-Token Nutzdaten und Sicherung zugleich erreicht.
- Umsetzung: `rclone` im Backup-Image, Zugang als Secret `rclone.conf`, Aufruf am Ende von `backup.sh` bei `OFFSITE_ENABLED=true`; `backup` erhält dann zusätzlich das Netz `egress` (Zeile in `docker-compose.yml` einkommentieren). Aufbewahrung am Zielort getrennt konfigurieren.
- Nachweis: mindestens eine Wiederherstellungsprobe aus der Offsite-Kopie mit Entschlüsselung (Abschnitt 5.6).

---

## 6. Monitoring, Logs, Statusseite

### 6.1 Strukturierte Logs

- Alle eigenen Dienste schreiben JSON-Zeilen nach stdout, eine Zeile je Ereignis. Pflichtfelder: `ts` (ISO 8601, UTC), `level`, `service` (`web`, `worker`, `worker-nlp`, `worker-io`, `beat`, `backup`), `logger`, `msg`, `request_id` (web) bzw. `task_id` und `job_id` (Worker), `object_id` und `document_id` wo vorhanden, `user_id` (nie E-Mail, nie Name), `duration_ms`.
- Ein zentraler Filter maskiert vor der Ausgabe IBAN-, Kontonummern-, BLZ-, BIC- und Ausweisnummernmuster (Muster aus Fachentwurf E, Beschluss B-11, einschließlich OCR-Varianten wie O statt 0) sowie Werte zu den Schlüsseln `token`, `secret`, `password`, `authorization`. Kein Dokumenttext im Log, nur Längen und Hashes.
- Zugriffslog von gunicorn (gilt bei Stack A) ebenfalls als JSON; die Client-IP stammt aus `X-Forwarded-For` nur, wenn sie aus `TRUSTED_PROXY_CIDR` gesetzt wurde.
- `db`, `redis` und `backup` loggen im Format ihrer Images.

### 6.2 Docker-Logs und Rotation

- Treiber `json-file` mit `LOG_MAX_SIZE` und `LOG_MAX_FILE` je Dienst in der Compose-Datei (ANNAHME AB22: 20 MB mal 5). Platzbedarf je Dienst höchstens `LOG_MAX_SIZE mal LOG_MAX_FILE`, bei den Vorschlagswerten 100 MB, für acht Dienste 800 MB.
- Lesen: `docker compose logs -f --since 1h worker`; gefiltert: `docker compose logs --no-log-prefix worker | jq -c 'select(.level=="ERROR")'`.
- Aufbewahrung über die Rotation hinaus ist nicht vorgesehen; das fachliche Protokoll liegt in `audit_events`, `processing_job_events`, `ai_calls` und `iban_access_log` in der Datenbank und ist Teil des Backups. Eine Rotation für fremde Container über `daemon.json` nur nach Freigabe (Härtung Nr. 9, F27).

### 6.3 Healthcheck-Endpunkte

| Endpunkt | Zweck | Prüfung | Zugriff |
|---|---|---|---|
| `GET /healthz/` | Lebenszeichen für Docker | Prozess antwortet, keine Abhängigkeiten | öffentlich, Antwort nur `ok`, keine Details, keine Sitzung |
| `GET /readyz/` | Betriebsbereitschaft | Datenbank (`SELECT 1`), Redis `PING`, freier Platz über `DISK_RESERVE_GB`, Token-Status nicht `revoked`, Schema-Version passt zum Code | nur mit Bearer-Token aus `readyz_token` oder als angemeldeter Admin; JSON je Prüfung; ohne Token 401 |
| Heartbeat-Datei | Docker-Healthcheck der Worker und von `beat` | Datei jünger als 2 min (Worker) bzw. 3 min (`beat`) | containerintern |

Docker startet einen Container bei Absturz neu (`restart: unless-stopped`), bei Zustand `unhealthy` jedoch nicht automatisch. Deshalb prüft `beat` den eigenen `/readyz/`-Status und meldet (Abschnitt 6.5). Im Runbook steht der manuelle Befehl `docker compose restart [dienst]`.

### 6.4 Statusseite in der Anwendung

Erreichbar für angemeldete Nutzer unter `/status/` (Sachbearbeiter lesend, Admin mit Aktionen). Aktualisierung per Polling (ANNAHME AB11: alle 10 s). Alle Kennzahlen sind Datenbankabfragen, Redis-Abfragen oder Dateilesungen; kein Zugriff auf die Docker-API aus dem Container. Grundform in M1 (Heartbeats, letzte Sicherung, Token-Platzhalter), Vollausbau in M12.

| Bereich | Kennzahl | Quelle |
|---|---|---|
| Objektverarbeitung | Fortschritt je Objekt: Dokumente gesamt und je Status, Seiten gesamt und fertig, Seiten pro Minute gleitend über 10 min, geschätzte Restzeit, Warteposition (Objekte laufen seriell, `processing.max_parallel_objects`, ANNAHME AB26) | `object_progress`, `processing_runs`, `processing_jobs` |
| Review | Offene Fälle gesamt und je Objekt, davon älter als der Zielwert, davon mit Kandidatenliste | `review_cases` |
| Qualität | Anteil 06_Sonstiges je Objekt (Zielwert unter 5 Prozent, CR 8) nach CR-Definition und bereinigt, Anteil Stufe-3-Aufrufe je Objekt, Kaltstartstatus des Klassifikators | `documents`, `document_classifications`, `classifier_models` |
| KI | Token-Verbrauch und Kosten je Objekt und Monat je Provider in EUR, aktiver Provider (primär oder Fallback), letzte Fehler | `ai_calls` |
| Google | Token-Status (`active`, `expired`, `revoked`), letzter erfolgreicher Refresh, Ablauf des Access-Tokens, Fehler in Folge, Tage seit Erstautorisierung ohne Neuanmeldung (8-Tage-Nachweis), Zustand der Drive-Warteschlange (`paused_auth`) | `oauth_tokens`, Queue-Zähler |
| Backup | Zeitpunkt, Status, Dauer, Größe und Alter der letzten Sicherung, Warnung bei Alter über `BACKUP_MAX_AGE_HOURS`, Offsite-Status, Befehl für den manuellen Lauf | `/data/backup/status.json` |
| Queues | Länge der Queues `ocr`, `classify`, `ai`, `io`, `lists`; Jobs in `running` mit veraltetem Heartbeat; letzter Sweeper-Lauf | Redis `LLEN`, `processing_jobs` |
| Dienste | Heartbeat-Alter von `worker`, `worker-nlp`, `worker-io`, `beat` | Redis-Schlüssel `heartbeat:[service]` mit TTL |
| Speicher | Freier Platz unter `/srv/objektakte` (aus Sicht von `web` über `statvfs` auf `/data/transit`), Belegung `transit`, `ocr-cache`, `work`, `previews` | Dateisystem |
| Konfiguration | Aktive Provider, Schwellwerte, Aufbewahrungsfristen mit Hinweis „durch Geschäftsführung und Steuerberater festzulegen", wenn leer | `app_settings`, `retention_policies` |

Aktionen des Admins (`status.operate`): Sweeper anstoßen, Google-Verbindung erneuern, Listen neu erzeugen, Nachklassifikationslauf nach KI-Ausfall manuell starten. Backup manuell: Anzeige des Shell-Befehls (Abschnitt 5.3).

### 6.5 Alarmierung per E-Mail (optional, Frage F27)

Nicht Teil des CR, mit geringem Aufwand über `beat` umsetzbar, wenn ein SMTP-Zugang vorliegt (`SMTP_*`, `ALERT_EMAIL_TO`, `ALERTS_ENABLED=true`, V-23). Prüfintervall 5 min, jede Bedingung höchstens einmal je 6 h, Entwarnung wird gemeldet. Alle Schwellen sind ANNAHME AB12 und in `app_settings` änderbar.

| Bedingung | Schwelle |
|---|---|
| Letzte Sicherung älter als `BACKUP_MAX_AGE_HOURS` oder Status `failed` | sofort |
| Token-Refresh zweimal in Folge fehlgeschlagen oder Status `revoked` | sofort |
| Heartbeat eines Workers älter als 10 min bei nicht leerer Queue | sofort |
| Freier Platz unter `DISK_RESERVE_GB` | sofort |
| Fallback-Provider länger als 1 h aktiv | 1 h |
| Monatsbudget eines Providers zu 80 Prozent ausgeschöpft | einmal |
| TLS-Zertifikat der Domain läuft in weniger als 14 Tagen ab (Prüfung per TLS-Verbindung aus `beat` auf `APP_DOMAIN`) | täglich |
| Ordnerabgleich meldet Dateianzahl vorher ungleich nachher | sofort |

Ohne SMTP zeigen Statusseite und `/readyz/` dieselben Zustände; der Admin prüft sie dann aktiv (Empfehlung: tägliche Kontrolle der Statusseite während der Übernahmephase).

### 6.6 Runbook: Störungsfälle (Kurzfassung)

Die ausführliche Fassung entsteht in M1 unter `docs/betrieb/runbook.md`.

| Störung | Symptom | Prüfung | Maßnahme |
|---|---|---|---|
| Token ungültig oder widerrufen | Statusseite rot, Drive-Queue `paused_auth`, Alarm | `oauth_tokens.status`, `last_refresh_error`; Admin-Console: App noch vertrauenswürdig, Konto aktiv | Admin: „Google-Konto neu autorisieren" mit `ablage@muellerhv.de` (Abschnitt 7.7); Schreibjobs laufen danach automatisch weiter |
| Drive-Quota oder Ratenbegrenzung | Fehler 403/429 im Log von `worker-io`, Jobs in Wiederholung | Fehlerzähler in `processing_jobs`, Zeitfenster | Warten; `IO_CONCURRENCY` senken; Abläufe außerhalb der Arbeitszeit planen |
| Platte voll oder unter Reserve | Ingest gestoppt, `/readyz/` rot | `df -h /srv`, Belegung je Verzeichnis auf der Statusseite | `work/` per Sweeper räumen, alte Sicherungen prüfen, `previews/` bereinigen; danach Tarif oder Platte erweitern |
| Worker ohne Fortschritt | Heartbeat alt, Queue nicht leer | `docker compose ps`, `docker compose logs worker --since 30m` | `docker compose restart worker`; Sweeper setzt hängende Jobs zurück |
| Fallback-Provider aktiv | Statusseite zeigt Fallback, Alarm nach 1 h | `ai_calls` letzte Fehler, Anbieterstatus | Beobachten; Primärprovider in `app_settings` prüfen; nach Rückkehr Nachklassifikationslauf manuell |
| Backup fehlgeschlagen | `status.json` mit `failed`, Healthcheck rot | `docker compose logs backup` | Ursache beheben (Platz, Passwort, Rechte), manuellen Lauf starten, Ergebnis prüfen |
| Zertifikat nicht erneuert | Browserwarnung, Alarm 14 Tage vorher | Traefik-Log (nur lesen), DNS, Port 80 offen bei `httpChallenge` | Mit dem Betreiber von Traefik klären; keine eigene Änderung an Traefik |
| `web` unhealthy | `docker compose ps` zeigt `unhealthy`, Traefik liefert 502 | `docker compose logs web --since 10m` | `docker compose restart web`; bei Wiederholung `scripts/rollback.sh` |
| Doppelte Objektnummer im Drive | Review-Fall `duplicate_object_number` | Fall im Review Center | Fachliche Entscheidung durch Sachbearbeiter, kein Automatismus |
| Listen veraltet | Datum der Liste älter als letzte Stammdatenänderung | `list_generations` | Admin-Aktion „Listen neu erzeugen"; Drive-Fehler im Log prüfen |
| Datenbank startet nicht | `db` nicht `healthy` | `docker compose logs db` | Nicht in `/srv/objektakte/db` eingreifen; Wiederherstellung nach Abschnitt 5.4 nur nach Rücksprache mit dem Entwickler |

---

## 7. Google Cloud Console: OAuth-App (intern)

Durchführung durch den Auftraggeber als Administrator der Workspace-Organisation `muellerhv.de` (CR 15; Umsetzungsplan V-05, V-06, V-09). Bezeichnungen der Menüpunkte in der Cloud Console und der Admin-Console ändern sich gelegentlich; Reihenfolge und Inhalt bleiben. Nach Durchführung wird die Anleitung mit Bildschirmfotos (ohne Secrets) als `docs/betrieb/google-oauth.md` abgelegt (M0, Schritt 10). Fachliche Details des Adapters stehen in Fachentwurf F und `docs/architektur.md` Abschnitt 7.

### 7.1 Parameter auf einen Blick (Beschluss B-16)

| Parameter | Wert | Quelle |
|---|---|---|
| Nutzertyp | Intern | CR 0.1 |
| Scope | `https://www.googleapis.com/auth/drive` | CR 0.1; Begründung Abschnitt 7.4 |
| Anwendungstyp | Webanwendung | serverseitiger Ablauf |
| Redirect-URI Drive-Verbindung | `https://uebernahme.muellerhv.de/auth/google/callback` | Vorschlag, Frage F13 |
| Redirect-URI Mitarbeiter-Login (optional) | `https://uebernahme.muellerhv.de/auth/google/login/callback` | Vorschlag, Frage F13 und F14 |
| Autorisierungsparameter | `access_type=offline`, `prompt=consent`, `include_granted_scopes=false`, `state`, PKCE | B-16 |
| Erlaubtes Konto | nur `ablage@muellerhv.de` (Prüfung gegen `DRIVE_ACCOUNT_EMAIL` nach dem Callback) | CR 0.1 |
| Token-Ablage | `oauth_tokens`, AES-256-GCM mit `TOKEN_KEY`, `storage_mode = db` | Abweichung vom CR-Wortlaut, Anhang D Nr. 4 |
| Audit-Aktionen | `drive.authorize`, `drive.token_refresh` | Umsetzungsplan D.6 |
| Nachweis | täglicher erzwungener Refresh plus stündlicher Lesetest über mindestens 8 Tage | B-16, CR 14 |
| Client-Secret | Datei `/srv/objektakte/secrets/google_client_secret`, nie in `.env` | V-07 |

### 7.2 Projekt anlegen

1. Als Administrator der Workspace-Organisation in der Google Cloud Console anmelden. Nicht mit einem privaten Google-Konto: dann gibt es keinen Nutzertyp „Intern".
2. Neues Projekt anlegen, Name Vorschlag `hvm-objektakte`, Organisation `muellerhv.de` auswählen. Projekt-ID notieren (nur Doku).
3. Kein Abrechnungskonto verknüpfen; die Drive API braucht keines.

### 7.3 Drive API aktivieren

1. APIs und Dienste, Bibliothek, „Google Drive API" suchen und aktivieren.
2. Keine weiteren APIs, insbesondere keine Gmail- oder Admin-APIs.

### 7.4 OAuth-Zustimmungsbildschirm

1. APIs und Dienste, OAuth-Zustimmungsbildschirm (in neueren Konsolen „Google Auth Platform", Branding und Zielgruppe).
2. Nutzertyp **Intern**. Begründung (CR 0.1): Interne Apps brauchen keine Verifizierung durch Google, und ihre Refresh-Tokens unterliegen nicht dem Verfall des Testmodus externer Apps.
3. App-Name `Objektakte Hausverwaltung Müller`; Support-E-Mail und Entwicklerkontakt: eine Adresse der Organisation (Entscheidung F13, keine private Adresse).
4. Bereiche: `https://www.googleapis.com/auth/drive` hinzufügen. Begründung für die Dokumentation: Der eingeschränkte Bereich `drive.file` erlaubt nur den Zugriff auf Dateien, die die App selbst angelegt hat. Die Anwendung muss bestehende Objektordner finden, lesen, umbenennen (Altordner des Auffangbereichs zu `06_Sonstiges`, CR 9.3) und Dateien innerhalb bestehender Ordner verschieben (CR 9). Das geht nur mit dem vollen Drive-Bereich. Die App sieht nur, was `ablage@muellerhv.de` sieht.
5. Speichern. Bei internen Apps ist kein Veröffentlichungsstatus zu setzen.

### 7.5 OAuth-Client anlegen

1. APIs und Dienste, Anmeldedaten, „Anmeldedaten erstellen", „OAuth-Client-ID".
2. Anwendungstyp **Webanwendung**, Name `objektakte-web`.
3. Autorisierte JavaScript-Quellen: keine.
4. Autorisierte Weiterleitungs-URIs exakt wie in `.env` (`GOOGLE_REDIRECT_URI`, `GOOGLE_LOGIN_REDIRECT_URI`): die beiden URIs aus Abschnitt 7.1. Nie `http://`. Für eine spätere Staging-Umgebung eigene URIs ergänzen.
5. Erstellen. Client-ID in `.env` als `GOOGLE_CLIENT_ID`; Client-Secret direkt auf dem Server als Datei `/srv/objektakte/secrets/google_client_secret` eintragen (`printf '%s' '[SECRET]' | sudo tee ...`, danach Shell-History bereinigen), nicht per E-Mail oder Chat (V-07).

Empfehlung: ein Client für Drive-Verbindung und Mitarbeiter-Login (F13). Getrennte Clients sind sinnvoll, wenn der Mitarbeiter-Login später eine andere Zielgruppe erhalten soll.

### 7.6 Workspace-Admin-Console prüfen

1. Admin-Console, Sicherheit, Zugriffs- und Datenkontrolle, API-Steuerung (Bezeichnung prüfen).
2. Drittanbieter-App-Zugriff: Prüfen, ob interne Apps standardmäßig als vertrauenswürdig gelten. Falls der Zugriff für nicht konfigurierte Apps eingeschränkt ist, die App anhand der Client-ID hinzufügen und mindestens für den Drive-Dienst als vertrauenswürdig markieren (V-09).
3. Organisationseinheit von `ablage@muellerhv.de`: Drive muss aktiv sein, Freigaberichtlinien dürfen die App nicht blockieren.
4. Zwei-Faktor für `ablage@muellerhv.de` aktivieren (Sicherheitsschlüssel oder Authenticator beim Auftraggeber), Wiederherstellungsdaten hinterlegen (V-05). Das Konto ist die Wurzel des gesamten Datenbestands.
5. Test-Wurzelverzeichnis außerhalb von `01_Daten` anlegen und Folder-ID übergeben (V-08), damit Live-Test und Performance-Test nicht im Produktivbestand laufen.

### 7.7 Erstautorisierung in der Anwendung

1. Als Admin anmelden (E-Mail, Passwort, TOTP), Bereich Administration, „Google Drive verbinden" (Recht `drive.connect`, Step-up-TOTP).
2. Die Anwendung startet den Ablauf mit `access_type=offline`, `prompt=consent`, `include_granted_scopes=false`, `state` und PKCE. Nur `prompt=consent` stellt sicher, dass Google einen Refresh-Token ausgibt, auch wenn das Konto der App früher schon zugestimmt hat.
3. Im Google-Dialog mit **`ablage@muellerhv.de`** anmelden, nicht mit dem persönlichen Konto. Nach dem Callback prüft die Anwendung die E-Mail des autorisierenden Kontos gegen `DRIVE_ACCOUNT_EMAIL` und lehnt jedes andere Konto ab (Audit `drive.authorize` mit Ergebnis).
4. Refresh- und Access-Token werden mit `TOKEN_KEY` verschlüsselt in `oauth_tokens` gespeichert (`storage_mode = db`, `refresh_obtained_at = jetzt`).
5. Wurzelordner: Die Anwendung wandert den Pfad `Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten` ab, zeigt Folder-ID, Ordnername und Anzahl der Unterordner zur Bestätigung und speichert die ID nach Bestätigung in `app_settings` (`drive.root_folder_id`). Mehrdeutige Treffer werden zur Auswahl gestellt, nicht geraten. Für Tests wird stattdessen die ID des Test-Wurzelverzeichnisses eingetragen.
6. Erster Lesetest: Liste der Objektordner (Namen und IDs), kein Schreiben. Danach folgt der Ordnerabgleich im Dry-Run-Modus (CR 9.5).

### 7.8 Refresh und Überwachung

- Access-Tokens sind kurzlebig; die Google-Bibliothek erneuert sie mit dem Refresh-Token vor dem nächsten Aufruf. Mehrere Prozesse serialisieren den Refresh über einen Redis-Lock (`drive:token:refresh`); der Adapter schreibt nach jedem Refresh `access_expires_at`, `last_refresh_at`, `last_refresh_status`, `consecutive_failures`.
- `beat` prüft stündlich mit einem günstigen Aufruf (Metadaten des Wurzelordners), ob das Token gültig ist, und erzwingt einmal täglich einen Refresh, auch wenn kein fachlicher Aufruf stattfand. Beides schreibt `drive.token_refresh` in `audit_events`.
- Bei Fehler: `consecutive_failures` plus 1, Statusanzeige gelb. Bei `invalid_grant` (Token widerrufen oder ungültig) sofort `status = revoked`, Statusanzeige rot, Alarm (Abschnitt 6.5).
- Die Statusseite zeigt Token-Status, letzten Refresh und „Tage seit Autorisierung ohne Neuanmeldung".

### 7.9 Nachweis über mindestens 8 Tage (CR 14, Test T9)

| Tag | Schritt | Nachweis |
|---|---|---|
| 0 | Erstautorisierung nach Abschnitt 7.7; Eintrag `drive.authorize` | Bildschirmfoto Statusseite |
| 1 bis 8 | Automatisch: stündlicher Lesetest, täglicher erzwungener Refresh (`beat`). Fachlich: mindestens ein Ordnerabgleich im Dry-Run auf dem Test-Wurzelverzeichnis je Tag, ohne erneute Anmeldung | `audit_events` (`drive.token_refresh`) |
| 8 | Export der Zeilen aus `oauth_tokens` (ohne Chiffrate) und der zugehörigen `audit_events` als Tabelle nach `docs/betrieb/oauth-nachweis.md`, Bildschirmfoto der Statusseite | Kriterium: kein `drive.authorize` nach Tag 0, alle Refreshes `ok`, Anzeige „Tage seit Autorisierung" größer oder gleich 8 |

Voraussetzungen für den Start: Konto `ablage@muellerhv.de` mit 2FA (V-05), OAuth-App (V-06), DNS-Eintrag (V-04), Freigabe in der Admin-Console (V-09). Der Zeitpunkt hängt von diesen Bereitstellungen ab (Frage F13).

### 7.10 Verhalten bei widerrufenem oder ungültigem Token

1. Drive-Schreibjobs werden nicht verworfen; die Drive-Warteschlange wechselt in den Zustand `paused_auth`. OCR und Klassifikation laufen weiter, weil sie auf bereits heruntergeladenen Dateien arbeiten.
2. Statusseite rot mit Schaltfläche „Google-Konto neu autorisieren" (nur Admin); optional E-Mail (Abschnitt 6.5).
3. Nach erfolgreicher Neuautorisierung läuft die Warteschlange automatisch weiter; `drive.authorize` wird protokolliert und unterbricht die Nachweiskette des 8-Tage-Tests.
4. Google kann Refresh-Tokens aus eigenen Gründen ungültig machen (Widerruf durch den Administrator, Änderung der App-Konfiguration, längere Nichtnutzung). Die genauen Regeln stehen in der Google-Dokumentation zum Umsetzungszeitpunkt; der tägliche erzwungene Refresh deckt den Fall Nichtnutzung ab.

### 7.11 Zu vermeiden

- Wiederholte Erstautorisierungen: Sie unterbrechen die Nachweiskette. ANNAHME AB21 (Anbieteraussage, vor M4 gegen die Google-Dokumentation zu prüfen): Google begrenzt die Anzahl gleichzeitig gültiger Refresh-Tokens je Konto und Client, ältere verfallen still.
- Widerruf des App-Zugriffs in den Kontoeinstellungen von `ablage@muellerhv.de`; Änderung des Nutzertyps auf „Extern"; Löschen oder Neuanlegen des OAuth-Clients.
- Wechsel des Client-Secrets ohne Planung: Vorher die Auswirkung auf bestehende Refresh-Tokens prüfen und die Erneuerung der Verbindung einplanen.

### 7.12 Optionaler Workspace-Login der Mitarbeiter (Frage F14)

- Deaktiviert bis zur Entscheidung (`GOOGLE_LOGIN_ENABLED=false`). Nur für Nutzer, die der Admin zuvor angelegt hat; keine Selbstregistrierung. Abgleich über verifizierte E-Mail und `sub`-Anspruch (`users.google_subject`), nicht nur über die E-Mail.
- Parameter `hd=muellerhv.de` im Autorisierungsaufruf und serverseitige Prüfung des `hd`-Anspruchs im ID-Token; andere Domains werden abgelehnt.
- Scopes nur `openid email profile`. Kein Drive-Zugriff über Mitarbeiterkonten.
- TOTP der Anwendung bleibt auch nach Google-Login Pflicht (Empfehlung), damit die Sicherheit der Anwendung nicht von der 2FA-Richtlinie des Workspace abhängt. Alternative zur Entscheidung: Google-Login gilt als zweiter Faktor, wenn die Workspace-Richtlinie 2FA für alle erzwingt.

---

## 8. Datenschutz-Voraussetzungen (Checkliste für den Auftraggeber)

Die folgenden Punkte kann die Softwareentwicklung nicht ersetzen. Sie sind Freigabekriterium vor der Verarbeitung von Produktivdaten; Punkte 1 bis 3 vor dem ersten externen KI-Aufruf mit echten Dokumenten (Meilenstein M8). Zuständig: Geschäftsführung, bei Bedarf mit Datenschutzberater, Rechtsanwalt und Steuerberater. Dieser Abschnitt ist eine Einschätzung aus technischer Sicht und keine Rechtsberatung. Nachweise gehören in die Verfahrensdokumentation (Umsetzungsplan V-10 bis V-12, V-17, V-25 bis V-27).

| Nr. | Voraussetzung | Technische Entsprechung im System | Zuständig, spätestens | Status |
|---|---|---|---|---|
| 1 | Auftragsverarbeitungsvertrag mit OpenAI und mit Anthropic (jeweils Vertragsvariante für API-Nutzung); Kopie in der Verfahrensdokumentation | Provider werden erst nach Freigabe in `app_settings` aktiviert (`ai.providers.<p>.enabled`), Schalter protokolliert | Geschäftsführung, ggf. Rechtsanwalt; M8 (V-10, V-11) | offen |
| 2 | Datenresidenz EU: prüfen, welche EU-Endpunkte und Regionen beide Anbieter zum Umsetzungszeitpunkt anbieten, und verbindlich festlegen | Basis-URL je Anbieter in `.env` (`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`), Region, Modell und Endpunkt in `app_settings`; kein Aufruf an einen nicht konfigurierten Endpunkt | Geschäftsführung; M8 (V-12) | offen |
| 3 | Opt-out aus Training und Datenaufbewahrung beim Anbieter: schriftlich bestätigen lassen, welche Aufbewahrung von API-Eingaben gilt (Zero-Retention oder Frist) | Es werden nur bereinigte Textauszüge mit maskierten Bank- und Ausweisdaten übermittelt (CR 7 und 10, B-11). `ai_calls` speichert keine Prompts und keine Antworttexte, sondern `prompt_hash`, `masked_entities_count`, `response_summary` (strukturierte Entscheidung), Token und Kosten (Prüfberichte K07, K2-14, K4-06; Frage F17) | Geschäftsführung; M8 (V-12) | offen |
| 4 | Google Workspace: Der bestehende Workspace-Vertrag deckt Drive als Auftragsverarbeitung ab; prüfen, dass die Nutzung durch die Anwendung darunter fällt und die Datenverarbeitungsregion bekannt ist | Zugriff nur über das Konto `ablage@muellerhv.de`, Scope `drive` | Geschäftsführung; M4 | prüfen |
| 5 | Verzeichnis der Verarbeitungstätigkeiten ergänzen: Zweck (digitale Objektübernahme und Aktenführung), Kategorien Betroffener (Eigentümer, Mieter, Beiräte, Mitarbeiter der Vorverwaltung, eigene Mitarbeiter), Datenkategorien (Kontaktdaten, Bankverbindung maskiert, Vertrags- und Zahlungsdaten), Empfänger (Google, OpenAI, Anthropic, Hosting), Löschfristen | Datenkategorien im Datenmodell D benannt | Geschäftsführung mit Datenschutzberater; vor Produktivstart (V-25) | offen |
| 6 | Hosting: AVV mit IONOS prüfen, Serverstandort dokumentieren | Server 187.124.23.80, Standort aus dem Vertrag | Geschäftsführung; vor Produktivstart (V-27) | prüfen |
| 7 | Aufbewahrungsfristen je Dokumentkategorie durch Geschäftsführung und Steuerberater festlegen (handels- und steuerrechtliche Fristen, WEG-Unterlagen, Mietunterlagen) | `retention_policies` mit leeren Standardwerten und Hinweistext; keine automatische Löschung ohne gesetzten und freigegebenen Wert (CR 15) | Geschäftsführung mit Steuerberater; vor Produktivstart, ohne Wirkung auf den Bau (V-17, F31) | offen |
| 8 | Technische und organisatorische Maßnahmen dokumentieren | Abschnitte 2, 3.8, 3.9 dieses Dokuments und `docs/architektur.md` Abschnitt 9 sind die Vorlage | Geschäftsführung; nach Umsetzung (V-25) | nach Umsetzung |
| 9 | Prüfen, ob eine Datenschutz-Folgenabschätzung erforderlich ist (automatisierte Klassifikation personenbezogener Dokumente mit externer KI, umfangreicher Bestand) | Klassifikation trifft keine Entscheidungen über Personen, sondern ordnet Dokumente zu; Review Center als menschliche Kontrolle | Datenschutzberater; vor Produktivstart (V-25) | Einschätzung durch Berater |
| 10 | Informationspflichten gegenüber Eigentümern und Mietern prüfen (Datenschutzhinweise um die Verarbeitung durch Dienstleister ergänzen) | keine | Geschäftsführung; vor Produktivstart (V-25) | prüfen |
| 11 | Mitarbeiter über das Audit-Protokoll informieren (Protokollierung aller Aktionen und, nach F15, jeder Dokumentansicht mit Nutzer und Zeitstempel) | eigene Aktionen sind für jeden Nutzer einsehbar (`audit.read_own`) | Geschäftsführung; vor Produktivstart (V-25) | vor Produktivstart |
| 12 | Berechtigungskonzept freigeben (Rollen `admin`, `sachbearbeiter`, Rechtematrix in `docs/architektur.md` 9.1) und Nutzerliste festlegen, mindestens zwei Admin-Konten | `roles`, `users` | Auftraggeber; M15 (V-20, F15) | vor Produktivstart |
| 13 | Verfahren für Auskunfts- und Löschersuchen Betroffener festlegen | Suche nach Eigentümer und Mieter über alle Objekte (CR 13); Löschlauf nur mit freigegebener Frist; Export der Daten einer Person als Funktion | Geschäftsführung mit Datenschutzberater; vor Produktivstart | offen |
| 14 | Umgang mit Sicherungen: Aufbewahrungsdauer der Backups und Ort der Offsite-Kopie festlegen; Sicherungen enthalten personenbezogene Daten | `BACKUP_RETENTION_DAYS`, Verschlüsselung der Offsite-Kopie (Abschnitt 5.8) | Geschäftsführung; M13 (F26, V-22) | offen |
| 15 | Zugriff auf Dokumente der Vorverwaltung: sicherstellen, dass Verwaltervertrag und Bestellung die Verarbeitung der übergebenen Unterlagen decken | keine | Geschäftsführung, ggf. Rechtsanwalt; vor Produktivstart (V-26) | fachlich |

Löschkonzept auch für Dateisystemartefakte (Frage F31): Aufbewahrungsfristen wirken nicht nur auf die Datenbank und Drive, sondern auch auf `transit/` (Löschung nach Ablage), `work/` (Sweeper), `ocr-cache/` (folgt dem Dokument), `previews/` (`previews.retention_days_after_resolve`, ANNAHME AB29: 90 Tage nach Erledigung) und auf die Sicherungen (`BACKUP_RETENTION_DAYS`). Ein Löschlauf entfernt ein Dokument in allen Orten; in Sicherungen bleibt es bis zum Ablauf der Sicherungsaufbewahrung, was in der Verfahrensdokumentation zu vermerken ist.

Regel im System: Kein Wert für Aufbewahrungsfristen wird vorbelegt. Der Admin-Bereich zeigt je Kategorie den Hinweis „durch Geschäftsführung und Steuerberater festzulegen", bis ein Wert gesetzt und mit Nutzer und Zeitpunkt freigegeben ist (`retention.approve`). Ein Löschlauf ohne freigegebenen Wert ist technisch nicht auslösbar; Löschläufe laufen mit Vier-Augen-Prinzip oder Wartefrist (Frage F28).

---

## 9. Deployment-Testplan (CR 14)

Alle Tests laufen auf dem VPS. Ergebnisse in `docs/betrieb/deployment-test.md` mit Datum, Durchführendem, Befehlsausgaben und Bildschirmfotos. Spalte Meilenstein: erstmalige Durchführung; Wiederholung nach jeder Änderung an Compose, Dockerfile oder Skripten.

| Nr. | Test | Schritte | Erwartung | Nachweis | Meilenstein |
|---|---|---|---|---|---|
| T1 | Frischer Checkout | Neues Verzeichnis, `git clone`, `.env` aus Ergebnisblatt, Secrets vorhanden, `scripts/deploy.sh --first-run main` (Auslegung Abschnitt 4.6, F20) | Alle Dienste `healthy` innerhalb von 3 min (`docker compose ps`); `curl -I https://uebernahme.muellerhv.de/` liefert 200 oder 302 zur Anmeldung; Zertifikat gültig (`openssl s_client -connect uebernahme.muellerhv.de:443 -servername uebernahme.muellerhv.de </dev/null 2>/dev/null \| openssl x509 -noout -issuer -dates`); HTTP auf Port 80 leitet auf HTTPS | Ausgaben `docker compose ps`, `curl -I`, `openssl` | M1 |
| T2 | Kein Host-Port | `docker ps --format '{{.Names}} {{.Ports}}'`; `sudo ss -tulpen \| grep LISTEN` | Nur Traefik zeigt 80 und 443; kein Dienst des Projekts veröffentlicht Ports | Ausgaben | M1 |
| T3 | Isolation `data` | `docker compose exec db sh -c 'getent hosts example.org \|\| echo kein_dns'`; Verbindungsversuch nach außen aus `db`, `redis` und `worker-nlp` | Kein Weg ins Internet | Ausgaben | M1 |
| T4 | Serverneustart | Verarbeitungslauf auf synthetischem Testobjekt mit einigen hundert Seiten starten, `sudo reboot`, nach 3 min prüfen (Zeitfenster F27) | Alle Container laufen ohne Eingriff, Healthchecks grün, Lauf wird fortgesetzt, keine doppelt verarbeiteten Dokumente (`SELECT sha256, COUNT(*) FROM documents WHERE object_id = [ID] GROUP BY 1 HAVING COUNT(*) > 1` leer), Sweeper-Lauf im Log | `docker compose ps`, Abfrage, Log-Auszug | nach M5, spätestens M13 |
| T5 | Container-Abbruch Worker | `docker kill objektakte-worker-1` während OCR | Container startet neu, Chunk wird wiederholt, Dokument endet in `done`, keine Duplikate | Log, Abfrage | nach M5, spätestens M13 |
| T6 | Backup und Wiederherstellung | Manueller Backup-Lauf; auf leerer Instanz Wiederherstellung nach Abschnitt 5.4 | Zeilenzahlen der Kerntabellen identisch, Stichproben nach Abschnitt 5.6, Token-Status `active` bei identischem `TOKEN_KEY` | Restore-Protokoll | Grundform M1, vollständig vor Produktivstart, danach quartalsweise |
| T7 | Rollback | Deployment eines Commits mit sichtbarer Änderung (Versionsanzeige), dann `scripts/rollback.sh` | Vorherige Version läuft innerhalb einer Minute, Healthchecks grün, `tags.log` enthält ROLLBACK-Zeile | Ausgabe, Bildschirmfoto | M1 |
| T8 | Migration vorwärts und rückwärts | Auf Kopie der Datenbank: `app-migrate`, `app-migrate --down 1`, erneut `app-migrate` | Fehlerfrei, Migrationstabelle konsistent | Ausgabe | M1, danach in der CI je Migration |
| T9 | OAuth 8 Tage | Ablaufplan Abschnitt 7.9 | Kein `drive.authorize` nach Tag 0, alle Refreshes `ok`, Anzeige größer oder gleich 8 Tage | `docs/betrieb/oauth-nachweis.md` | M4 |
| T10 | Ressourcenlimits | Während des Performance-Tests (10.000 Seiten, synthetischer Korpus): `docker stats --no-stream` alle 60 s in Datei | Kein OOM-Kill (`docker inspect --format '{{.State.OOMKilled}}'` false für alle Container), `web` p95 unter 2 s, Seiten pro Minute protokolliert | Messdatei, `docs/betrieb/performance-bericht.md` | M13 |
| T11 | Logging | `docker compose logs --no-log-prefix web \| head -5 \| jq .`; Suche nach IBAN-Mustern in allen Logs (`docker compose logs \| grep -E 'DE[0-9]{2}[0-9 ]{18,}'`) nach einem Lauf mit synthetischen Dokumenten, die IBAN-Muster enthalten | Gültiges JSON je Zeile; Suche ohne Treffer | Ausgaben | M1, Wiederholung M5 |
| T12 | Host-Härtung | Lesebefehle aus Abschnitt 1.4 nach Härtung | Root-Login `no`, Passwort-Login `no`, UFW aktiv mit 22, 80, 443, unattended-upgrades aktiv, Zeitzone Europe/Berlin, NTP synchron | Ausgaben in `docs/betrieb/serverbefund.md` | M1 |
| T13 | Login und Rollen | Neuer Sachbearbeiter ohne TOTP: nach Login nur Einrichtungsseite erreichbar; Sachbearbeiter ruft Konfigurationsseite auf; Sachbearbeiter versucht `review.dismiss_object_case` | Einrichtung erzwungen; Zugriff verweigert, `audit_events` enthält `auth.denied` | Bildschirmfoto, Abfrage | M1, Erweiterung M7 |
| T14 | Healthcheck-Endpunkte | `curl https://uebernahme.muellerhv.de/healthz/`; `curl https://uebernahme.muellerhv.de/readyz/` ohne Token; mit Token | `ok` ohne Details; 401 ohne Token; JSON je Prüfung mit Token | Ausgaben | M1 |

Protokollvorlage je Test in `docs/betrieb/deployment-test.md`: Nummer, Datum (TT.MM.JJJJ), Durchführender, Image-Tag, Schritte wie durchgeführt, Ausgaben (gekürzt, ohne Geheimnisse), Ergebnis bestanden oder nicht bestanden, Abweichungen und Maßnahmen.

Definition of Done für den Betriebsteil: T1 bis T14 bestanden und dokumentiert; `docs/betrieb/serverbefund.md`, `docs/betrieb/deployment.md`, `docs/betrieb/runbook.md`, `docs/betrieb/restore-protokoll.md` und `docs/betrieb/oauth-nachweis.md` vorhanden; Härtung abgenommen; Schlüsselverwahrung bestätigt (V-21).

---

## Anhang A: Annahmen (mit Verifikation im Projekt)

| Nr. | ANNAHME | Verifikation |
|---|---|---|
| AB1 | ANNAHME: Die Anwendungscontainer laufen mit UID 10001; die Bind-Mounts gehören dieser UID. | Festlegung im Dockerfile, Prüfung mit `docker compose exec web id` (M1). |
| AB2 | ANNAHME: Speicherbedarf je OCR-Prozess m_ocr = 0,75 GiB. | `docker stats` und cgroup `memory.peak` im OCR-Probelauf (M0); Messwert ersetzt die Annahme in `.env`. |
| AB3 | ANNAHME: `worker-nlp` mit L = 1 Prozess (2 ab C größer oder gleich 6) braucht m_nlp = 0,8 GiB je Prozess. | `docker stats` in M6 mit geladenem spaCy-Modell und Klassifikator. |
| AB4 | ANNAHME: `web` mit 3 gunicorn-Workern und 4 Threads (gthread) braucht 1 GiB und 1 CPU (gilt bei Stack A). | `docker stats` im Betrieb mit 2 bis 5 Nutzern (M7). |
| AB5 | ANNAHME: `worker-io` mit 4 Threads braucht 0,5 GiB und 0,5 CPU. | `docker stats` während Drive-Uploads und KI-Aufrufen (M4, M8). |
| AB6 | ANNAHME: `db` mit 1 CPU, `max(1 GiB, 0,15 mal M)` und Buffer-Pool 0,5 mal DB_MEM reicht für den Bestand. | Performance-Test M13 (Antwortzeiten, Buffer-Pool-Trefferrate). |
| AB7 | ANNAHME: `redis` 0,5 CPU und 256 MiB, `beat` 0,25 CPU und 128 MiB, `backup` 0,5 CPU und 256 MiB, `classifier` 1 CPU und 1,5 GiB. | `docker stats` in M13; `classifier` nur bei Aktivierung. |
| AB8 | ANNAHME: `DISK_RESERVE_GB = max(10, 0,1 mal D)` und Speichersumme höchstens 0,85 mal M lassen Host, Docker, Traefik und Seitencache genug Reserve. | Beobachtung `free -h` und `df -h` während M13; Anpassung in `.env`. |
| AB9 | ANNAHME: Ein Swap von 2 GiB ist als Puffer gegen den OOM-Killer sinnvoll, falls der Server keinen hat. | `free -h` im Serverbefund; Entscheidung nach Messung. |
| AB10 | ANNAHME: Backup um 02:30 Serverzeit liegt außerhalb der Objektverarbeitung; `BACKUP_MAX_AGE_HOURS` 26 ist die richtige Warnschwelle. | Auslastungsmuster nach den ersten Objekten; `BACKUP_CRON` anpassen. |
| AB11 | ANNAHME: Statusseite mit Polling alle 10 s erzeugt keine spürbare Last. | Antwortzeit während des Performance-Tests M13. |
| AB12 | ANNAHME: Alarm-Schwellen (Heartbeat 10 min, Fallback 1 h, Budget 80 Prozent, Zertifikat 14 Tage, Prüfintervall 5 min, Wiederholsperre 6 h) passen zum Betrieb. | Anpassung in `app_settings` nach den ersten Wochen (M12). |
| AB13 | ANNAHME: Sitzungs-Leerlauf 8 h, Höchstdauer 12 h, Passwortmindestlänge 12 Zeichen, 5 Fehlversuche, 15 min Sperre. | Entscheidung Auftraggeber F28 vor Produktivstart. |
| AB14 | ANNAHME: Planmäßige Schlüsselrotation jährlich. | Entscheidung Auftraggeber F28. |
| AB15 | ANNAHME: Die installierte Compose-Version wertet `deploy.resources.limits` und `cpu_shares` außerhalb von Swarm aus; der Host läuft mit Cgroup v2. | `docker compose config` und `docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.CpuShares}}'` nach dem Start (M1). |
| AB16 | ANNAHME: Das MariaDB-Image des gewählten Tags unterstützt `MARIADB_*_FILE`, `MARIADB_AUTO_UPGRADE` und liefert `healthcheck.sh`. | Dokumentation des Image-Tags zum Umsetzungszeitpunkt (M1). |
| AB17 | ANNAHME: Traefik läuft in Version 2 oder 3 mit Docker-Provider ohne Swarm; die Label-Syntax passt. | Serverbefund Abschnitt 1.3 (M0). |
| AB18 | ANNAHME: Die Traefik-Entrypoint-Timeouts erlauben Uploads großer Dateien (mehrere hundert MB) ohne Abbruch. | Upload-Test mit großer Datei (M5); bei Abbruch Chunked-Upload in der Anwendung oder Abstimmung mit dem Betreiber von Traefik. |
| AB19 | ANNAHME: `age`, `rclone`, `mariadb-client`, `jq` und ein Cron-Dienst sind als Pakete für das Backup-Image verfügbar. | Paketquellen zum Umsetzungszeitpunkt (M1). |
| AB20 | ANNAHME: Der Nutzerkreis bleibt bei Geschäftsführung plus 2 bis 5 Mitarbeitern; keine objektbezogenen Rechte nötig. | CR 15; Frage F15; bei Änderung Erweiterung des Rollenmodells. |
| AB21 | ANNAHME (Anbieteraussage ohne Beleg in den Unterlagen): Google begrenzt die Anzahl gleichzeitig gültiger Refresh-Tokens je Konto und Client; ältere verfallen still. | Google-Dokumentation zum Umsetzungszeitpunkt vor M4 prüfen; unabhängig davon gilt: keine wiederholten Erstautorisierungen. |
| AB22 | ANNAHME: Log-Rotation 20 MB mal 5 Dateien je Dienst reicht für die Fehlersuche; Journal-Grenze 500 MB auf dem Host. | Prüfung nach dem ersten Objekt (M5). |
| AB23 | ANNAHME: `WORKER_STOP_GRACE` 120 s, `GUNICORN_TIMEOUT` 120 s, `WORKER_MAX_TASKS_PER_CHILD` 50 passen zu Chunk-Dauer und Upload-Größe. | Chunk-Dauer aus M0, Upload-Test M5. |
| AB24 | ANNAHME: Heartbeat alle 30 s, Healthcheck-Schwelle 2 min (Worker) bzw. 3 min (`beat`) erkennt hängende Prozesse ohne Fehlalarme. | Beobachtung in M5 und M13. |
| AB25 | ANNAHME: Der I/O-Scheduler des VPS lässt `ionice` wirken. | `/sys/block/*/queue/scheduler` im Serverbefund; sonst nur `nice`. |
| AB26 | ANNAHME: `processing.max_parallel_objects = 1` (strikt serielle Objekte) ist Grundlage der Laufzeitzusage. | Frage F18; Performance-Test M13. |
| AB27 | ANNAHME: Alle Dienste sind nach `up -d` innerhalb von 3 min `healthy`. | Messung in T1; Wartezeit in `deploy.sh` anpassen. |
| AB28 | ANNAHME: `BACKUP_RETENTION_DAYS` so, dass Tage mal tägliche Sicherungsgröße kleiner als 0,3 mal D bleibt. | Sicherungsgröße nach dem ersten Objekt messen. |
| AB29 | ANNAHME: Vorschaubilder brauchen rund 1,5 GB je 10.000 Seiten und werden 90 Tage nach Erledigung gelöscht. | Messung Bytes je Seitenbild in M0; Frist Frage F26. |

## Anhang B: Offene Fragen an den Auftraggeber (gebündelt)

Nur echte Entscheidungen; technische Festlegungen trifft der Entwickler. Nummern verweisen auf Abschnitt 4 des Umsetzungsplans, damit jede Frage nur einmal beantwortet wird. Ohne Antwort gilt der genannte Vorschlagswert.

| Nr. | Frage | Vorschlag ohne Antwort | Bis |
|---|---|---|---|
| F2 | Erweiterter Container-Katalog (`worker-nlp`, `worker-io`, `beat`, `backup`; `queue` heißt `redis`) bestätigen? | ja | M1 |
| F13 | OAuth-App: Redirect-URIs aus Abschnitt 7.1 und ein Client für beide Zwecke bestätigen; Support- und Entwicklerkontakt (Adresse der Organisation) benennen; Test-Wurzelverzeichnis anlegen; Zeitpunkt, ab dem Konto `ablage@muellerhv.de` mit 2FA, OAuth-App und DNS-Eintrag bereitstehen (Start des 8-Tage-Nachweises) | URIs und ein Client wie vorgeschlagen; Nachweis startet, sobald V-04 bis V-06 und V-09 vorliegen | M4 |
| F14 | Google-Workspace-Login der Mitarbeiter: zum Produktivstart nötig oder nachgelagert; TOTP der Anwendung bleibt zusätzlich Pflicht oder Google-Login gilt als zweiter Faktor bei erzwungener Workspace-2FA; nur Domain `muellerhv.de`; keine automatische Kontoanlage | nachgelagert, TOTP bleibt Pflicht, nur eigene Domain, keine automatische Anlage | vor Produktivstart |
| F15 | Rollen und Protokoll: alle Sachbearbeiter sehen alle Objekte; jede Dokumentansicht und jeder Download werden protokolliert; Aufbewahrungsdauer des Audit-Protokolls (enthält Mitarbeiterdaten) | alle sehen alles, Ansichten werden protokolliert, keine Löschung des Audits | M7 |
| F16 | IBAN vollständig verschlüsselt speichern oder nur `iban_last4` und `iban_hash`? Ohne vollständige Speicherung entfällt die Rotation von `iban_key` im Regelbetrieb | nur `iban_last4` und `iban_hash` | M3 |
| F18 | Falls M0 weniger als fünf nutzbare OCR-Prozesse oder mehr als 5 Sekunden je Seite ergibt: größerer Tarif, `tessdata_fast`, Zwei-Phasen-OCR oder längere Laufzeit? Objekte strikt seriell? Testkorpus synthetisch oder anonymisierte Realdokumente? | Tarifempfehlung wird vorgelegt, seriell ja, synthetisch | P0 |
| F20 | Deployment-Test T1 als `scripts/deploy.sh --first-run` lesen (Abschnitt 4.6)? | ja | M15 |
| F26 | Sicherung: Offsite-Kopie gewünscht, Zielort (Objektspeicher mit AVV in der EU, zweiter Server, Netzwerkspeicher; nicht das Drive-Konto), Aufbewahrung am Zielort; lokale Aufbewahrung in Tagen; wer verwahrt Verschlüsselungsschlüssel, Root-Passwort und privaten age-Schlüssel (mindestens zwei Personen); wer außer dem Entwickler erhält SSH-Zugang; Aufbewahrung von Vorschaubildern und OCR-Cache auf dem Server | keine Offsite-Kopie, 30 Tage, Vorschauen 90 Tage, OCR-Cache unbefristet; Schlüsselverwahrung muss vor M1 geregelt sein (V-21) | M1 (Schlüssel), M13 (übrige) |
| F27 | Betrieb: Alarmierung per E-Mail gewünscht, SMTP-Zugang und Empfängeradresse; Zeitfenster für Deployments und für den Neustart-Test; Änderung der Docker-`daemon.json` mit einmaligem Neustart des Docker-Dienstes (kurze Unterbrechung von Traefik) erlaubt; Image-Build auf dem VPS oder in GitHub Actions mit Registry | keine Alarmierung, Deployments nach Absprache, keine Änderung der `daemon.json`, Build auf dem VPS | M1 (Zeitfenster), M12 (übrige) |
| F28 | Sicherheitsparameter: Passwortmindestlänge, Sitzungsdauer, Rotationsintervall der Schlüssel, Löschläufe mit Vier-Augen-Prinzip oder 24 h Wartefrist, Anzahl Admin-Konten | 12 Zeichen, 8 h Leerlauf und 12 h absolut, jährlich, Wartefrist, zwei Admin-Konten | vor Produktivstart |
| F29 | Weitergabe der Software an andere Gesellschaften oder Dritte denkbar? Dann Lizenzprüfung (Anhang C) durch einen Rechtsanwalt vorab | interner Betrieb | jederzeit |
| F31 | Aufbewahrungsfristen je Dokumentkategorie (Geschäftsführung mit Steuerberater); Wirkung auf Datenbank, Drive, Dateisystemartefakte und Sicherungen | keine Löschung bis zur Freigabe | vor Produktivstart |
| V-10 bis V-12, V-25 bis V-27 | Datenschutz-Checkliste Abschnitt 8: AVV OpenAI und Anthropic, EU-Endpunkte und Opt-out bestätigt, Workspace- und Hosting-AVV geprüft, Verzeichnis der Verarbeitungstätigkeiten, Einschätzung zur Folgenabschätzung, Informationspflichten, Mitarbeiterinformation, Verwaltervertrag deckt Verarbeitung | Stufe 3 bleibt deaktiviert, bis Punkte 1 bis 3 erledigt sind | M8 bzw. vor Produktivstart |

## Anhang C: Lizenzhinweise

Einschätzung aus technischer Sicht; verbindliche Prüfung nur bei Weitergabe der Software (Frage F29) durch einen Rechtsanwalt.

| Komponente | Lizenzlage nach Kenntnisstand | Bedeutung für den internen Betrieb |
|---|---|---|
| Ghostscript (Abhängigkeit von ocrmypdf) | AGPL | Unkritisch beim internen Betrieb ohne Weitergabe; bei Weitergabe prüfungspflichtig. |
| Datenbanktreiber (gilt bei Stack A: `mysqlclient`) | nach Kenntnisstand GPL, zu verifizieren; Alternative `PyMySQL` (MIT) | Entscheidung in M1 nach Prüfung der aktuellen Lizenzangabe. |
| Redis | Lizenzwechsel in den letzten Jahren; kompatibler Fork Valkey (BSD) verfügbar | Für die Anwendung transparent; `REDIS_IMAGE` und `REDIS_TAG` in `.env`. Entscheidung in M1. |
| Tesseract, ocrmypdf, spaCy, scikit-learn, rapidfuzz, Django, Celery, gunicorn, MariaDB | freie Lizenzen (Apache, MIT, MPL, BSD, GPL für MariaDB als Serverprogramm) | Keine Einschränkung für den internen Betrieb. |

## Anhang D: Abweichungen vom CR-Wortlaut (Vorschläge, keine Entscheidungen)

| Nr. | CR-Vorgabe | Vorschlag | Begründung |
|---|---|---|---|
| 1 | Container `web`, `worker`, `queue`, `db`, optional `classifier` (CR 7) | Zusätzlich `worker-nlp`, `worker-io`, `beat`, `backup`; Dienstname `redis` statt `queue` | Netzwerkarbeit blockiert keine OCR-Prozesse; Modelle werden einmal geladen; periodische Aufgaben brauchen einen Zeitgeber; Backup ist gefordert, aber nicht als Container benannt (Frage F2). |
| 2 | „Zugangsdaten und API-Keys nur in `.env` oder Docker Secrets" (CR 0.1) | Ausschließlich dateibasierte Docker Secrets; `.env` ohne Geheimnisse | Kleinerer Leckpfad (`docker inspect`, `compose config`, Fehlerberichte). |
| 3 | „Persistente Daten in benannten Volumes oder unter `/srv/objektakte/`" (CR 0.1) | Beides kombiniert: benannte Volumes mit Bindung auf `/srv/objektakte/db` und `/srv/objektakte/redis`, übrige Verzeichnisse als Bind-Mounts | Ein Ort für Backup und Plattenüberwachung. |
| 4 | „Token verschlüsselt in der DB oder als Docker Secret" (CR 0.1) | In der Datenbank verschlüsselt (`oauth_tokens`), Schlüssel als Docker Secret | Der Refresh schreibt neue Access-Tokens; Docker Secrets sind zur Laufzeit nicht beschreibbar. |
| 5 | Redirect-URI `https://uebernahme.muellerhv.de/...` (CR 0.1) | `/auth/google/callback` und `/auth/google/login/callback` | Konkreter Vorschlag zur Eintragung in der Cloud Console (Frage F13). |
| 6 | Rollen Admin und Sachbearbeiter (CR 15) | Zusätzlich: IBAN-Klartext für keine Rolle, Step-up-TOTP vor sensiblen Aktionen, Vier-Augen-Prinzip oder Wartefrist bei Löschläufen, `review.dismiss_object_case` nur Admin | Schutz der Bankdaten und vor Fehlbedienung im kleinen Team (Beschlüsse B-17, B-18). |
| 7 | Backup „in ein separates Verzeichnis" (CR 0.1) | `/srv/objektakte/backup` auf derselben Platte plus verschlüsselte Offsite-Kopie als Empfehlung | Ein Verzeichnis allein schützt nicht vor Ausfall des VPS. |
| 8 | „`docker compose up -d` auf frischem Checkout" (CR 14) | `scripts/deploy.sh --first-run` | Migrationen laufen nicht beim Container-Start (Frage F20). |
| 9 | Nachweis Token „8 Tage ohne Neuanmeldung" (CR 14) | Täglicher erzwungener Refresh plus stündlicher Lesetest, Export der Kette | Der Nachweis wird auch dann erbracht, wenn kein fachlicher Aufruf stattfindet. |

## Anhang E: Bezeichner und Abgleich mit den Nachbardokumenten

| Thema | Dieses Dokument | Abweichende Quelle | Verbindlich |
|---|---|---|---|
| Klassifikations-Worker | `worker-nlp`, Variablen `NLP_CONCURRENCY`, `WORKER_NLP_CPUS`, `WORKER_NLP_MEM` | Fachentwurf E: `worker-classify`, `CLASSIFY_PROCESSES` | Umsetzungsplan B-13 und D.1; `docs/architektur.md` 4.1 und 4.4 verwenden dieselben Namen |
| Queue-Namen | `ocr`, `classify`, `ai`, `io`, `lists` | Fachentwurf G: `cpu`, `io` | Umsetzungsplan B-13 |
| Compose-Dateiname | `docker-compose.yml` | frühere Planfassung: `compose.yaml` | CR 0.1 Wortlaut; Umsetzungsplan 1.5 und `docs/architektur.md` 11 gleichlautend |
| Secret-Namen | `db_root_password`, `db_app_password`, `db_worker_password`, `db_migrate_password`, `db_backup_password`, `db_ro_password`, `redis_password`, `app_secret_key`, `iban_key`, `iban_hmac_key`, `token_key`, `totp_key`, `google_client_secret`, `openai_api_key`, `anthropic_api_key`, `smtp_password`, `readyz_token`, `backup_age_recipient`, `rclone.conf` | frühere Planfassung D.5: `db_app_rw`, `db_app_worker`, `db_app_migrate`, `db_app_backup`, `django_secret_key`, `backup_public_key` | dieses Dokument (Abschnitt 3.8); Umsetzungsplan D.5 und `docs/architektur.md` 5.7 führen denselben Satz |
| Datenbankverbindung in `.env` | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_WORKER_USER`, `DB_MIGRATE_USER`, Passwörter aus `*_FILE` | frühere Planfassung D.5: `DATABASE_URL_WEB`, `DATABASE_URL_WORKER` | dieses Dokument; eine URL mit Passwort gehört nicht in `.env`, die Anwendung setzt die URL zusammen; Umsetzungsplan D.5 gleichlautend |
| Verzeichnisse | 14 Verzeichnisse nach Abschnitt 3.3 | Fachentwurf G 2.1: 9 Verzeichnisse | Umsetzungsplan B-14 (Prüfberichte K03, K2-12, K3-05, K4-10) |
| KI-Konfiguration in `.env` | nur `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` | Fachentwurf G 4.2: Modell, Timeout, Budget in `.env` | Umsetzungsplan B-06 |
| Prompt-Speicherung | `ai_calls` ohne Prompts und Antworttexte | Fachentwurf G 12.2 und 13 | Umsetzungsplan (Prüfberichte K07, K2-14, K4-06), Frage F17 |
| OAuth-Nachweis | täglicher erzwungener Refresh plus stündlicher Lesetest; Audit `drive.authorize`, `drive.token_refresh` | Fachentwurf F: `oauth.authorized`, `oauth.refresh` | Umsetzungsplan B-16 und D.6 |
| Rollencodes | `admin`, `sachbearbeiter` | Fachentwurf G 12: Admin, Sachbearbeiter (Prosa) | Umsetzungsplan B-17 |
| Offene Fragen | Nummern F2 bis F31 aus `docs/umsetzungsplan.md` Abschnitt 4 | frühere Fassung von `docs/architektur.md` 13.2 mit eigener Zählung von 56 Einzelfragen | Umsetzungsplan Abschnitt 4; `docs/architektur.md` 13.2 verwendet dieselben Nummern, Teilfragen tragen dieselbe Nummer |

Ende des Dokuments. Stand 10.09.2026, Entwurf zur Freigabe.
