# GitHub-Anbindung des VPS über SSH

Stand: 10.09.2026. Zwei getrennte Schlüsselpaare, zwei Richtungen. SFTP wird nicht benötigt: Der Server holt den Code per Git, GitHub Actions stößt das Deployment per SSH an. Alle Schritte laufen auf dem VPS als Deploy-Nutzer (docs/betrieb.md 2.1 Nr. 1, V-01). Die Entwicklungsumgebung hat keinen Zugang zum Server und keine Rechte auf die Repository-Secrets; die Schritte 1 bis 4 führt der Auftraggeber oder eine Person mit Serverzugang aus.

## Überblick

| Richtung | Schlüssel | Liegt wo | Zweck |
|---|---|---|---|
| VPS holt Code von GitHub | Deploy Key (nur Lesen) | privater Teil unter `~deploy/.ssh/github_deploy`, öffentlicher Teil im Repository unter Settings, Deploy keys | `git fetch` und `git pull` in `scripts/deploy.sh` |
| GitHub Actions löst Deployment aus | Aktionsschlüssel | privater Teil als Repository-Secret `DEPLOY_SSH_KEY`, öffentlicher Teil in `~deploy/.ssh/authorized_keys` mit `command=`-Einschränkung | Workflow `.github/workflows/deploy.yml` ruft `scripts/deploy_remote.sh` auf |

Der Aktionsschlüssel kann auf dem Server ausschließlich `deploy <branch>`, `rollback` oder `check` auslösen (erzwungenes Kommando, kein Terminal, keine Weiterleitungen). Ein kompromittierter Schlüssel erlaubt damit kein Arbeiten auf dem Server.

## Kurzweg: `scripts/bootstrap_vps.sh`

Die Schritte 1 bis 3 auf dem Server (Deploy-Nutzer, Verzeichnisse nach docs/betrieb.md 3.3, Secrets nach 3.8, beide Schlüsselpaare, Werte für GitHub) erledigt `sudo bash scripts/bootstrap_vps.sh` in einem Lauf; das Skript ist idempotent und überschreibt nichts Vorhandenes. Es schreibt die drei Werte für GitHub nach `/home/deploy/github-werte.txt` (nur für `deploy` lesbar); die Datei wird nach dem Eintragen mit `shred -u` gelöscht. SSH-Härtung und Firewall bleiben bewusst manuelle Schritte (docs/betrieb.md 2.1), damit der Schlüssel-Login vorher geprüft wird. Die Einzelschritte darunter erklären, was das Skript tut.

## Schritt 1: Deploy Key (VPS liest von GitHub)

```bash
# auf dem VPS als deploy
ssh-keygen -t ed25519 -N "" -C "objektakte-vps-deploy-key" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub
cat >> ~/.ssh/config <<'CFG'
Host github.com
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
CFG
chmod 600 ~/.ssh/config
ssh -T git@github.com   # erwartete Antwort: "Hi v3ni94/objekt-bernahme_automatisieren.! You've successfully authenticated"
```

Den Inhalt von `github_deploy.pub` im Repository unter Settings, Deploy keys, Add deploy key eintragen; Schreibrecht nicht setzen. Danach Checkout über SSH (Erstinstallation, docs/betrieb/deployment.md Schritt 4):

```bash
sudo mkdir -p /opt/objektakte && sudo chown deploy:deploy /opt/objektakte
git clone git@github.com:v3ni94/objekt-bernahme_automatisieren..git /opt/objektakte
```

Ist das Repository bereits per HTTPS ausgecheckt: `git -C /opt/objektakte remote set-url origin git@github.com:v3ni94/objekt-bernahme_automatisieren..git`.

## Schritt 2: Aktionsschlüssel (GitHub Actions verbindet sich zum VPS)

```bash
# auf dem VPS als deploy; der private Teil wird gleich in ein Secret kopiert und dann gelöscht
ssh-keygen -t ed25519 -N "" -C "github-actions-deploy" -f /tmp/gh_actions_deploy
echo "command=\"/opt/objektakte/scripts/deploy_remote.sh\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty $(cat /tmp/gh_actions_deploy.pub)" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
cat /tmp/gh_actions_deploy          # Inhalt in das Secret DEPLOY_SSH_KEY übernehmen
ssh-keyscan -t ed25519 187.124.23.80 # Ausgabe in das Secret DEPLOY_KNOWN_HOSTS übernehmen
shred -u /tmp/gh_actions_deploy /tmp/gh_actions_deploy.pub
```

## Schritt 3: Secrets im Repository

Settings, Secrets and variables, Actions, Environment `production` (oder Repository-Secrets):

| Secret | Wert |
|---|---|
| `DEPLOY_HOST` | `187.124.23.80` (oder der DNS-Name, wenn der Host-Key dazu passt) |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | vollständiger privater Schlüssel aus Schritt 2 (mehrzeilig) |
| `DEPLOY_KNOWN_HOSTS` | Zeile aus `ssh-keyscan` (Schritt 2) |

Für die Umgebung `production` empfiehlt sich in GitHub die Freigabepflicht durch eine benannte Person (Required reviewers), damit kein Deployment ohne Vier-Augen-Prinzip startet.

## Schritt 4: Aktionen des Workflows

Actions, Workflow `Deploy`, Run workflow; Eingaben Branch, Aktion und (nur für create-admin) Argument. Auf dem Server führt `scripts/deploy_remote.sh` genau diese Aktionen aus, alles andere weist es ab. Reihenfolge der Erstinstallation: `check`, `befund`, `env-init` (nachdem `deploy/env.produktion` aus dem Befund befüllt wurde), `first-run`, `create-admin`.

| Aktion | Wirkung | Schreibt auf dem Server |
|---|---|---|
| `check` | Hostname, Nutzer, ausgecheckter Branch, ob `.env` vorhanden ist | nichts |
| `pull` | Checkout auf den angegebenen Branch bringen (`git fetch`, `checkout`, `pull --ff-only`) | nur den Checkout |
| `befund` | Serverbefund nach docs/betrieb/m0-anleitung.md ins Log (Kerne, RAM, Platte, Docker, Traefik, Host-Sicherheit; Geheimnisse geschwärzt) | nichts |
| `env-init` | `.env` aus `deploy/env.produktion` anlegen; ist `.env` vorhanden, nur Abweichungen anzeigen; legt das Proxy-Netz an und prüft die Compose-Datei | `.env`, Docker-Netz |
| `ps` | Zustand aller Container | nichts |
| `logs` | letzte 40 Logzeilen; ohne Argument die der Anwendungsdienste, sonst der genannte Dienst | nichts |
| `db-status` | Datenbankkonten und Tabellenzahl anzeigen | nichts |
| `db-reset` | Datenverzeichnis der Datenbank leeren und neu initialisieren; bricht ab, sobald `django_migrations` vorhanden oder nicht prüfbar ist | Datenverzeichnis der Datenbank |
| `first-run` | Erstinstallation `scripts/deploy.sh --first-run <branch>` (Images bauen, db und redis starten, Migration, Seeds, Rechte, alle Dienste, Smoke-Test) | alles |
| `deploy` | Deployment `scripts/deploy.sh <branch>` | alles |
| `rollback` | vorherige Version (`scripts/rollback.sh`) | Container |
| `create-admin` | Admin mit der E-Mail aus dem Argument anlegen; Startpasswort nur in `/home/deploy/admin-startpasswort.txt` auf dem Server, nie im Log | Datenbank, eine Datei |

Die Aktionen kennt nur die Fassung von `deploy_remote.sh`, die auf dem Server ausgecheckt ist. Nach dem ersten Checkout deshalb einmal von Hand `git -C /opt/objektakte pull` als deploy; danach genügt die Aktion `pull`.

## Schritt 5: Probelauf

1. Actions, Workflow `Deploy`, Run workflow, Branch `main`, Aktion `deploy`.
2. Erwartung: Das Log zeigt die Schritte von `scripts/deploy.sh` (Dump, Migration, Containerwechsel, Smoke-Test). Auf dem Server steht der Aufruf in `/srv/objektakte/deploy/remote.log`.
3. Rollback-Probe: Aktion `rollback` (ein Befehl, docs/betrieb.md 4.3).

Der Workflow läuft ausschließlich manuell. Ein automatisches Deployment bei jedem Push auf `main` ist bewusst nicht eingerichtet, weil Deployments außerhalb laufender Objektverarbeitung und im Zeitfenster nach F27 stattfinden sollen. Umstellung später durch einen `push`-Auslöser in `deploy.yml`, sobald F27 beantwortet ist.

## Was nicht eingerichtet wird

- Kein Passwort-Login, kein Root-Login (Härtung Nr. 3), kein SFTP-Konto: Dateien werden nicht per SFTP übertragen, der Code kommt aus Git, Daten liegen in Sicherungen (docs/betrieb.md 5).
- Kein Schlüssel im Repository, keine Zugangsdaten in `.env` (Secrets nur in GitHub und in `~deploy/.ssh`).
- Kein Deploy Key mit Schreibrecht: Der Server muss nie nach GitHub pushen.

## Fehlerbilder

| Fehler | Ursache | Maßnahme |
|---|---|---|
| `Host key verification failed` oder `REMOTE HOST IDENTIFICATION HAS CHANGED` | `DEPLOY_KNOWN_HOSTS` fehlt oder enthält einen anderen Schlüssel als der Server | Auf dem Server `ssh-keyscan -t ed25519 127.0.0.1 2>/dev/null \| sed 's/^127.0.0.1/187.124.23.80/'` ausführen, Zeile als Secret ersetzen; Kontrolle: `ssh-keyscan -t ed25519 127.0.0.1 2>/dev/null \| ssh-keygen -lf -` zeigt denselben SHA256-Fingerabdruck wie die Fehlermeldung im Workflow-Log |
| `Permission denied (publickey)` | öffentlicher Teil nicht in `authorized_keys` oder Datei mit falschen Rechten | Zeile prüfen, `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys` |
| `Nur check, pull, befund, ... erlaubt` | erzwungenes Kommando greift, Eingabe unbekannt | Workflow-Eingaben prüfen; direkter Shell-Zugang mit diesem Schlüssel ist nicht vorgesehen |
| `git fetch` schlägt fehl (`Repository not found`) | Deploy Key nicht eingetragen oder `remote` zeigt auf HTTPS | Schritt 1 wiederholen, `git remote -v` prüfen |
| `Host key verification failed` bei `git fetch` auf dem Server | Der Deploy-Nutzer kennt den Host-Key von github.com nicht; ein Aufruf ohne Terminal kann ihn nicht bestätigen | Die Aktionen `pull`, `deploy` und `first-run` tragen ihn selbst ein, geprüft gegen den von GitHub veröffentlichten Fingerabdruck. Bei einem Checkout mit älterem Skript einmalig: `sudo -u deploy bash -c 'ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts'` |
