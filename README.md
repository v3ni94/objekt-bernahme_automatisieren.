# Objektuebernahme

Arbeitstitel der Anwendung zur digitalen Objektübernahme der Hausverwaltung Müller GmbH. Technischer Kurzname in Code und Compose: `objektakte`. Stand: 10.09.2026.

## Zweck

Die Anwendung unterstützt die Übernahme von Verwaltungsobjekten (WEG und Miete) von Vorverwaltungen. Sie gleicht die Objektordner in Google Drive mit einer verbindlichen Sechs-Ordner-Struktur ab, verarbeitet die übergebenen Unterlagen (OCR, dreistufige Klassifikation, Ablage) und führt Eigentümer- und Mieterstammdaten mit historisch korrekten Zuordnungen. Unsichere Fälle landen in einem Review Center; aus den bestätigten Daten entstehen Eigentümer- und Mieterlisten, eine Vollständigkeitsprüfung der Übernahme und Nachforderungsentwürfe an die Vorverwaltung.

## Status

Umsetzung nach Freigabe FG-1 bis FG-3 vom 10.09.2026 (docs/plan/entscheidungen.md). Stand: M1 (Gerüst, Betrieb, Sicherheit), M2 (Datenmodell, Objektverwaltung) und M3 (Import von Eigentümerlisten aus CSV, Excel und Immoware24) und M4 (Drive-Adapter, OAuth, Ordnerabgleich; Live-Test und 8-Tage-Nachweis warten auf den Zugang) M5 (Pipeline: Upload, Hash, Dubletten, Seitenanalyse, OCR in Blöcken, Maskierung, Seitenbilder, Entitäten, Sweeper, Objektserialität; Messlauf auf dem VPS offen) und M6 (Klassifikation Stufe 1 und 2, Konfidenzmodell, Entscheidungsalgorithmus mit Zusatzprüfungen, Segmente, Review-Fälle nach B-09, Ablage in Drive, Pipeline-Dry-Run, Testkatalog E 8) und M7 (Review Center: Arbeitsliste, Detailansicht, Entscheidungen mit Trainingsdatum und Audit, Massenbearbeitung, Serienmodus) und M8 (Stufe 3: OpenAI und Anthropic hinter einer Schnittstelle, Maskierung vor dem Senden, Fallback, Circuit Breaker, Kostenlimit, Protokoll in ai_calls, Stichprobe; Testlauf gegen die echten Anbieter wartet auf AVV und Schlüssel) und M9 (Import Teil 2: PDF-Listen digital und als Scan mit Zellkonfidenz, Mieterlisten mit Mietverhältnissen, Listenerkennung mit Import nach Bestätigung) und M10 (Requirement Engine mit den 15 Prüfpunkten aus CR 12, Offene Punkte, Nachforderungsentwurf im HVM-CI als PDF und DOCX mit Freigabeschritt; Sichtprüfung des Layouts offen) und M11 (Eigentümer- und Mieterliste als Excel und PDF im HVM-CI, Veröffentlichung über die gespeicherte Drive-File-ID, IBAN-Sperre, Entprellung; Sichtprüfung offen) und M12 (Suche mit Filtern und Volltext, Berichte mit Kennzahlen und CSV, vollständige Statusseite, optionale Alarmierung) im Repository; Fortschritt in docs/plan/status.md. Stack laut Gutachtergremium: Python-Monolith mit Django, HTMX, Celery, Redis und MariaDB (docs/architektur.md 3). Serverseitige Schritte (Serverbefund M0, Verzeichnisse, Secrets, Härtung, Deployment-Tests) warten auf den SSH-Zugang zum VPS (V-01); die GitHub-Anbindung des Servers (Deploy Key, Workflow `Deploy`) ist in docs/betrieb/github-deploy.md beschrieben und wartet auf die Einrichtung der Schlüssel.

Entwicklung lokal: MariaDB und Redis starten, `.venv` mit `requirements-dev.lock.txt`, dann `make test` (Details in docs/betrieb/deployment.md und Makefile). Verbindliche Bezeichner: docs/architektur/bezeichnerregister.md; Datenmodell: docs/architektur/datenmodell.md.

## Dokumente

| Dokument | Inhalt | Für wen |
|---|---|---|
| [docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md](docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md) | Change Request des Auftraggebers, unveränderte Anforderungsquelle. Änderungen nur über einen neuen Change Request. | alle |
| [docs/architektur.md](docs/architektur.md) | Stack-Entscheidung mit Begründung, Container, Datenmodell, Klassifikationspipeline, Drive-Anbindung, Review Center, Sicherheit, Repository-Struktur, Performance-Modell | Entwicklung, Abnahme |
| [docs/umsetzungsplan.md](docs/umsetzungsplan.md) | Meilensteine, Prüfpunkte, Risiken, gebündelte offene Fragen mit Vorschlagswerten, Voraussetzungen des Auftraggebers, Freigabevorbehalt | Geschäftsführung, Entwicklung |
| [docs/betrieb.md](docs/betrieb.md) | Serverbefund, Host-Härtung, Compose und Konfiguration, Secrets, Backup und Wiederherstellung, Deployment und Rollback, Google-OAuth-Anleitung, Runbook, Testplan | Betrieb, Auftraggeber (Vorleistungen) |
| [docs/architektur/bezeichnerregister.md](docs/architektur/bezeichnerregister.md), [docs/architektur/datenmodell.md](docs/architektur/datenmodell.md) | Verbindliche Bezeichner (aus dem Code erzeugt) und Umsetzung des Datenmodells mit Abweichungen zu Fachentwurf D | Entwicklung |
| [docs/entwurf/](docs/entwurf/README.md) | Historische Arbeitspapiere der Entwurfsphase: Befundakte, drei Stack-Vorschläge, drei Gutachten, fünf Fachentwürfe, vier Kritiken. Nur zur Nachvollziehbarkeit; bei Abweichungen gelten Architektur, Betrieb und Umsetzungsplan. | Entwicklung |

Lesereihenfolge für die Freigabe: Umsetzungsplan Abschnitt 1 (Kurzfassung und Freigabevorbehalt), danach Abschnitt 4 (offene Fragen), bei Bedarf die Architektur.

## Verbindliche Ordnerstruktur je Objekt (CR-05, Abschnitt 2, 4 und 8)

Wurzelpfad in Google Drive: `Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten/`. Darunter liegt je Objekt ein Ordner, erkannt ausschließlich über die führende Objektnummer; Namensmuster für Neuanlagen `NNN Ort, Straße Hausnummer`.

```text
623 Düsseldorf, Joachimstraße 49/          Beispielobjekt aus dem CR, nicht im Bestand
├── 01_Legitimationsunterlagen
├── 02_Stammakte
├── 03_Buchhaltung
├── 04_Mieterakte
├── 05_Eigentümerakte
│   └── WE01_Nachname/                      eine Akte je Einheit und Eigentümer
│       ├── 01_Stammdaten
│       ├── 02_Eigentumsnachweise
│       ├── 03_SEPA
│       ├── 04_Hausgeld
│       ├── 05_Abrechnungen
│       ├── 06_Wirtschaftsplaene
│       ├── 07_Beschluesse
│       ├── 08_Korrespondenz
│       ├── 09_Mahnwesen
│       ├── 10_Vollmachten
│       └── 11_Sonstiges
└── 06_Sonstiges
    ├── 01_Unklar
    ├── 02_Manuelle_Pruefung
    ├── 03_Dubletten
    └── 04_Nicht_objektbezogen
```

Regeln: Die Drive-Ordner sind eine Ansicht auf das Datenmodell, nicht dessen Quelle. Bestehende Ordner werden nie umbenannt, nichts wird gelöscht. Der frühere Auffangordner mit dem Präfix 05 entfällt vollständig; seine Altbezeichnung steht ausschließlich als Konfigurationswert im Seed und kommt weder im Anwendungscode noch in der verbindlichen Dokumentation vor (Definition of Done: Grep leer; dokumentierte Ausnahmen sind der CR selbst und die historischen Arbeitspapiere unter docs/entwurf/). Die Schreibweise der Ordnernamen (Umlaut im Hauptordner 05, ASCII in den Unterordnern) ist wortgetreu aus dem CR übernommen und beim Auftraggeber zur Bestätigung angefragt.

## Zielumgebung

| Merkmal | Vorgabe (CR-05, Abschnitt 0.1) |
|---|---|
| Server | IONOS VPS mit Ubuntu 24.04 LTS, Docker mit Docker Compose; Traefik mit Let's Encrypt ist vorhanden und wird nicht verändert |
| Domain | `uebernahme.muellerhv.de`, DNS-Eintrag setzt der Auftraggeber |
| Ablage | Google Drive des technischen Workspace-Kontos `ablage@muellerhv.de`, OAuth 2.0 über eine interne Workspace-App |
| Externe KI | OpenAI API und Anthropic Claude API hinter einer Provider-Abstraktion, Primär und Fallback konfigurierbar; nur maskierte Textauszüge, keine Originaldateien |
| Datenbank und Queue | MariaDB und Redis (oder ein protokollkompatibler Fork) als Container mit persistenten Volumes |
| Persistente Daten | ausschließlich in benannten Volumes oder unter `/srv/objektakte/`, nie im Container |

Kerne, Arbeitsspeicher, Platte, Name des Traefik-Netzes, Entrypoints und Cert-Resolver werden auf dem Server ausgelesen und nicht angenommen; alle serverabhängigen Werte sind Variablen in `.env`. Ausleseanleitung und Ergebnisblatt stehen in docs/betrieb.md.

## Zugangsdaten und Datenschutz

Zugangsdaten gehören nie ins Repository: keine `.env`, keine Secret-Dateien, keine API-Schlüssel, OAuth-Client-Secrets, Tokens, Passwörter oder Verschlüsselungsschlüssel. Im Repository wird nur `.env.example` ohne Werte liegen (Vorlage in docs/betrieb.md, Abschnitt 3.7); `.gitignore` schließt `.env`, `.env.*`, `secrets/`, `*.pem`, `*.key`, `token*.json` und `client_secret*.json` aus. Geheimnisse liegen auf dem Server als dateibasierte Docker Secrets unter `/srv/objektakte/secrets/` (docs/betrieb.md). Ein versehentlich eingechecktes Geheimnis gilt als kompromittiert und wird sofort rotiert; das Entfernen aus der Historie reicht nicht.

Ebenso gehören keine personenbezogenen Daten aus dem Verwaltungsbestand (Namen, Anschriften, Telefonnummern, E-Mail-Adressen, Bankdaten) in Dokumentation, Tests oder Fixtures. Beispiele sind synthetisch (Objekt 623, WE03_Mustermann). Bankverbindungen speichert die Anwendung nur maskiert oder verschlüsselt; sie erscheinen weder in Logs noch in KI-Prompts noch im Volltextindex.

## Konventionen

| Regel | Festlegung |
|---|---|
| Sprache | Dokumentation und Code-Kommentare auf Deutsch, Fachbegriffe deutsch. Technische Bezeichner (Tabellen, Felder, Klassen, Container, Konfigurationsschlüssel) auf Englisch in snake_case. |
| Typografie | Keine Gedankenstriche in deutschen Texten, stattdessen Komma, Punkt oder Umformulierung. Datum TT.MM.JJJJ, Beträge 1.234,56 EUR. |
| Migrationen | Jede Migration ist rückrollbar oder hat einen dokumentierten Rollback. Migrationen laufen als eigener Deployment-Schritt, nie beim Container-Start; vor jeder Migration ein Datenbank-Dump. Kein Datenverlust. |
| Planungsgrößen | Werte, die nicht aus CR oder Befund stammen, tragen das Präfix ANNAHME und nennen die Verifikation im Projekt. Bibliotheken und Images ohne Versionsnummer; aktuelle LTS bzw. stabile Version zum Umsetzungszeitpunkt prüfen und im Lockfile festschreiben. |
| Bezeichner | Das Datenmodell in docs/architektur.md (Abschnitt 5) ist die einzige Quelle für Tabellen, Statuswerte und Konfigurationsschlüssel. Container-, Netz-, Volume- und `.env`-Namen sowie Secret-Dateinamen folgen der Compose-Datei in docs/betrieb.md (Abschnitt 3). Fragen (F), Meilensteine (M, P), Voraussetzungen (V), Beschlüsse (B) und Auflagen (Ü) werden im Umsetzungsplan nummeriert und in allen Dokumenten mit denselben Nummern verwendet. |
| Entscheidungen | Offene Fragen werden gebündelt im Umsetzungsplan gestellt (F-Nummern) und nicht durch stille Annahmen ersetzt; Antworten des Auftraggebers werden mit Nummer und Datum festgehalten (Umsetzungsplan, Abschnitt 7). |
| Arbeitsweise | Kleine, einzeln testbare Schritte; nach jedem Schritt Tests laufen lassen und Ergebnis berichten (CR-05, Abschnitt 0). |

## Installation und Betrieb

Es gibt noch keine Installationsanleitung, weil noch kein Code existiert. Serverbefund, Host-Härtung, Erstinstallation, Deployment, Rollback, Backup und Wiederherstellung sowie die Einrichtung der Google-OAuth-App sind in [docs/betrieb.md](docs/betrieb.md) beschrieben und werden mit den ersten Meilensteinen umgesetzt.
