# Stand der Umsetzung

Fortschreibung nach jedem Arbeitsschritt (Umsetzungsplan, Abschnitt 7). Neueste Einträge oben.

| Datum | Meilenstein | Schritt | Ergebnis | Tests |
|---|---|---|---|---|
| 10.09.2026 | M1 | Schritte 1 bis 11, 13, 14 | Repository-Gerüst (pyproject, Lockfiles je Ziel, ruff, pytest, CI), Dockerfile mit Zielen web und worker, Compose mit Variablen, Datenbank-Init und Rechte-SQL aus dem Modellregister, Django-Projekt mit Secrets aus Dateien, JSON-Logs mit Maskierung, Request-ID, Healthchecks, Login mit TOTP-Pflicht, Rollen Admin und Sachbearbeiter, Sperre nach Fehlversuchen, Nutzerverwaltung mit Step-up, Audit append-only mit Triggern, Konfigurationsregister mit 97 Schlüsseln und Admin-Formularen, Backup-Container, deploy.sh und rollback.sh, Statusseite in Grundform, Runbook-Gerüst | 48 Tests grün gegen MariaDB, Migrationen vorwärts und rückwärts (T8), Grep-Prüfung leer, Compose-Datei gültig |
| 10.09.2026 | M1 | offen | Schritt 4 (Verzeichnisse und Secrets auf dem Server), Schritt 12 (Host-Härtung) und die Deployment-Tests T1 bis T3, T7, T11 bis T14 brauchen den VPS; Image-Build braucht einen Docker-Daemon (CI-Workflow angelegt, erster Lauf nach Push) | |
| 10.09.2026 | M1 | Start | Freigabe FG-1 bis FG-3 erhalten, Entscheidungen protokolliert, Gerüst wird aufgebaut | |
| 10.09.2026 | M0 | Vorbereitung | Werkzeuge für Serverbefund und OCR-Probelauf liegen vor (docs/betrieb/m0-anleitung.md); Durchführung wartet auf SSH-Zugang (V-01) und Freigabe des Messcontainers (V-03) | Unit-Tests Rechenmodell 10 grün, Funktionstest in der Entwicklungsumgebung |
| 10.09.2026 | Entwurf | Abschluss | Architektur, Umsetzungsplan, Betriebshandbuch und Arbeitspapiere im Repository | Stil-, Fakten- und Konsistenzprüfung |

## Blockiert

- M0 auf dem VPS: SSH-Zugang für Deploy-Nutzer (V-01), Freigabe Messcontainer (V-03)
- M4 gegen das echte Drive: technisches Konto (V-05), OAuth-App (V-06), Test-Wurzelverzeichnis (V-08), Freigabe in der Admin-Console (V-09)
- M8: AVV mit OpenAI und Anthropic, EU-Region, Opt-out (V-10 bis V-12)
