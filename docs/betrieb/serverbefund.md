# Serverbefund VPS 187.124.23.80

Stand: 10.09.2026, 23:48 UTC, erhoben mit `scripts/measure_server.sh` über die Workflow-Aktion `befund` (nur lesend, Geheimnisse geschwärzt). Ergebnisblatt nach docs/betrieb.md 1.6. Der OCR-Probelauf (M0 Schritt 2) ist noch nicht gelaufen; die Prozesswerte in Abschnitt 6 sind deshalb Vorschlagswerte aus der Formel mit Abschlag für die Mitnutzer des Servers und werden im Performance-Test (M13) durch Messwerte ersetzt.

## 1. Ergebnisblatt

| Feld | Wert | Verwendung |
|---|---|---|
| Betriebssystem, Kernel | Ubuntu 24.04.4 LTS, 6.8.0-139-generic | |
| [C] Kerne | 8 | OCR_PROCESSES |
| [M] Arbeitsspeicher | 31,3 GiB, Auslastung im Leerlauf rund 20 Prozent durch andere Anwendungen | Speicherformel |
| [S] Swap | 0 GiB | Härtung Nr. 11 (2 GiB anlegen) |
| [D] freie Platte | 189,6 GiB von 386 GiB (50,9 Prozent belegt) | DISK_RESERVE_GB = 18 |
| Scheduler | mq-deadline | ionice wirksam |
| Docker, Compose | 29.4.0, Compose v5.1.2, cgroup v2, Storage overlayfs, Log json-file (daemon.json: 10m x 3, Adresspools 172.17.0.0/12 und 192.168.0.0/16 mit /24) | Ressourcenlimits über Compose |
| Zeitzone | Etc/UTC, NTP aktiv | Ziel Europe/Berlin (Härtung Nr. 8); Container laufen über `TZ` in Europe/Berlin |
| Uptime | 2 Tage | |
| Traefik | Container `traefik-traefik-1`, Image traefik:latest (v3.7.0), Compose-Projekt `/docker/traefik`, **Netzmodus host** (keine veröffentlichten Ports, lauscht direkt auf 80 und 443) | TRAEFIK_NETWORK |
| Entrypoints | `web` (:80), `websecure` (:443) | TRAEFIK_ENTRYPOINT, TRAEFIK_ENTRYPOINT_INSECURE |
| Cert-Resolver | `letsencrypt`, httpChallenge über `web`, Speicher `/letsencrypt/acme.json` im Volume `traefik_traefik-letsencrypt` | TRAEFIK_CERTRESOLVER |
| exposedByDefault | false | `traefik.enable=true` bleibt gesetzt |
| Constraints | keine | kein Zusatzlabel |
| Globaler HTTP-Redirect | ja (`entrypoints.web.http.redirections.entrypoint.to=websecure`) | TRAEFIK_REDIRECT_ROUTER=false, Redirect-Labels entfernt |
| Weitere Container | 17 Container anderer Anwendungen (Automatisierung, Dokumentenverwaltung, Zeiterfassung, Wiki, Passwortverwaltung, Datenbanken), 190 GB in Volumes | Server ist geteilt; Limits konservativ (Abschnitt 6) |
| Veröffentlichte Host-Ports fremder Container | 32768 (Wiki), 32771 (MariaDB einer anderen Anwendung) auf 0.0.0.0 | Härtung Nr. 4 und 5: prüfen, ob diese Ports nach außen offen sein müssen |
| UFW | inaktiv (Stand 23:42 UTC), iptables INPUT ACCEPT | Härtung Nr. 4 offen |
| sshd | lauscht auf 22 (IPv4 und IPv6), Passwort-Login noch aktiv (`Permission denied (publickey,password)`) | Härtung Nr. 3 offen |
| unattended-upgrades | aktiviert; 48 ausstehende Updates, 6 Sicherheitsupdates, ein Update nicht automatisch installierbar | Härtung Nr. 6 |
| Deploy-Nutzer | `deploy` (uid 1001), Gruppen sudo, users, docker | angelegt am 10.09.2026 |
| /srv/objektakte | angelegt, Rechte nach 3.3 | |

## 2. Auswirkung des Traefik-Host-Netzes

Traefik ist nicht Mitglied eines Docker-Netzes, sondern nutzt den Netzwerk-Namensraum des Hosts. Der Docker-Provider erreicht Container deshalb über deren Adresse in einem beliebigen Bridge-Netz des Hosts. Die Compose-Datei erwartet ein externes Netz `TRAEFIK_NETWORK`; dieses Netz wird von `scripts/deploy_remote.sh` (Aktionen `env-init`, `first-run`, `deploy`) als `objektakte-proxy` mit festem Subnetz `172.31.250.0/24` angelegt. Das Label `traefik.docker.network` zeigt auf dieses Netz, `TRUSTED_PROXY_CIDR` deckt den Gateway ab, von dem Traefik die Anfragen weiterreicht. Der optionale Redirect-Router aus der Compose-Vorlage entfällt, weil Traefik bereits global umleitet.

## 3. Abweichungen von der Ressourcenformel (docs/betrieb.md 3.10)

Der Server wird mit anderen Anwendungen geteilt, die im Leerlauf rund 6 GiB belegen und unter Last mehr. Die Formel geht von einem exklusiven Host aus. Gewählt (Entscheidung, docs/plan/entscheidungen.md):

| Größe | Formel | Gewählt | Begründung |
|---|---|---|---|
| OCR_PROCESSES | 7 | 4 | vier Kerne bleiben für Web, Datenbank, Redis und die Mitnutzer |
| WORKER_MEM | 4 x 0,75 + 0,5 | 3.584 MiB | m_ocr = 0,75 GiB (ANNAHME AB2, bis zum Probelauf) |
| NLP_CONCURRENCY | 2 | 2 | C größer oder gleich 6 |
| DB_MEM | 4,7 GiB | 3.072 MiB, Buffer Pool 1.536 MiB | Anfangsbestand klein, Erhöhung nach Messung |
| Summe Limits | | rund 10,5 GiB | unter 0,5 x M, damit die Mitnutzer nicht verdrängt werden |

Alle übrigen Werte stehen in `deploy/env.produktion` und nach `env-init` in `/opt/objektakte/.env`.

## 4. Offene Punkte aus dem Befund

- Härtung nach docs/betrieb.md 2.1 steht aus: Passwort-Login und Root-Login abschalten (erst nach geprüftem Schlüssel-Login als deploy), UFW mit 22, 80, 443, Zeitzone Europe/Berlin, Swap 2 GiB, Updates einspielen.
- OCR-Probelauf (`scripts/perf_probe.sh`) für m_ocr, t_ocr und die Prozessentscheidung nach P1.
- Fremde Host-Ports 32768 und 32771 gehören anderen Anwendungen; Entscheidung des Auftraggebers, ob sie nach außen offen bleiben.
- Traefik läuft als `traefik:latest` ohne Versionsbindung; ein Update kann das Verhalten des Docker-Providers ändern (Risiko, Beobachtung im Runbook).
