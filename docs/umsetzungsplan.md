# Umsetzungsplan CR-05: Eigentümerakte und 06_Sonstiges

Stand: 10.09.2026. Status: Entwurf zur Freigabe durch den Auftraggeber. Die Umsetzung beginnt erst nach schriftlicher Freigabe (Abschnitt 1.3).

Faktenbasis: CR-05 (`docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md`) und Befundakte vom 10.09.2026. Eingearbeitet sind die drei Stack-Vorschläge, die drei Gutachten, die fünf Fachentwürfe D (Datenmodell), E (Pipeline), F (Drive), G (Betrieb und Sicherheit), H (Review Center, Listen, Import) sowie die vier Kritiken. Was nicht aus CR oder Befund stammt, ist als Vorschlag oder als ANNAHME gekennzeichnet. Versionsnummern von Bibliotheken werden nicht genannt; es gilt durchgängig: aktuelle LTS bzw. stabile Version zum Umsetzungszeitpunkt prüfen und im Lockfile festschreiben. Alle Beispiele sind synthetisch. Die Fachentwürfe D bis H, die Stack-Vorschläge, die Gutachten und die Kritiken liegen als historische Arbeitspapiere unter `docs/entwurf/` (Lesehinweise dort in der README); ihre verbindliche Konsolidierung sind `docs/architektur.md` (Bezeichner in Abschnitt 5.7) und `docs/betrieb.md` (Compose, `.env`, Secrets). Verweise der Form "D 12.4" oder "G Abschnitt 10" bezeichnen die Herkunft; bei Abweichungen gelten `docs/architektur.md` und `docs/betrieb.md`.

Konventionen in diesem Dokument: Meilensteine heißen M0 bis M15, Prüfpunkte P0 bis P2, offene Fragen F1 bis F31 (dieselben Nummern in `docs/architektur.md` 13.2 und `docs/betrieb.md` Anhang B), Fallarten des Review Centers FA1 bis FA8 (`docs/architektur.md` 8.1), Auflagen aus den Gutachten Ü1 bis Ü22 (Anhang C, gleich in `docs/architektur.md` 3.3), Voraussetzungen V-01 bis V-28 (Abschnitt 5), Konsolidierungsbeschlüsse B-01 bis B-47 (Anhang B), Annahmen A-01 bis A-43 (Anhang A; die Architektur zählt ihre Annahmen A1 ff. ohne Bindestrich, das Betriebshandbuch AB1 ff.). Der Auftraggeber kann per Nummer freigeben, ablehnen oder antworten.

---

## 1. Zweck, Kurzfassung und Freigabevorbehalt

### 1.1 Zweck

Dieser Plan legt fest, in welcher Reihenfolge, mit welchen Modulen, Tests und Abnahmekriterien CR-05 umgesetzt wird, welche Risiken bestehen, welche Entscheidungen der Auftraggeber treffen muss und welche Voraussetzungen er beistellt. Er ist die dauerhafte Referenz im Repository (`docs/umsetzungsplan.md`); Fortschritt und Entscheidungen werden in `docs/plan/status.md` fortgeschrieben, der Plan selbst wird nur über eine neue Fassung geändert.

### 1.2 Kurzfassung

1. Stack: Python-Monolith mit Django (LTS-Linie), serverseitigen Templates plus HTMX, Celery mit Redis als Broker, MariaDB, Tesseract `deu` plus ocrmypdf im Worker, TF-IDF-Klassifikator (scikit-learn), ReportLab und openpyxl, google-api-python-client, offizielle SDKs von OpenAI und Anthropic hinter einer eigenen Schnittstelle. Das Gremium hat diesen Stack (Vorschlag A) in allen drei Gutachten auf Platz eins gereiht (Summe 247,0 Punkte gegenüber 198,5 für B und 190,5 für C, `docs/architektur.md` Abschnitt 3.2). Er wird nicht in der Rohfassung gebaut, sondern mit 22 Auflagen aus den Vorschlägen B und C (Anhang C): OCR-Probelauf in M0, Chunking großer Dokumente, `OMP_THREAD_LIMIT=1`, getrennte OCR- und Klassifikationspools, zwei Build-Targets, Netztrennung, zwei Datenbankkonten, Audit-Trigger, Seitenbilder statt PDF-Streaming, Vorschau-Endpunkt für die Massenbearbeitung, Maskierung von Ausweisnummern, Drive-Schreibidempotenz, Host-Härtung mit Abnahme.
2. Datenmodell: Fachentwurf D ist die einzige Bezeichnerquelle. Der Pipeline-Entwurf E wird auf die Bezeichner von D umgeschrieben; D erhält eine nummerierte Ergänzungsmigration (Anhang D). Das war das gewichtigste Finding aller vier Kritiken.
3. Reihenfolge: 16 Meilensteine. Anders als in Vorschlag A wird der Import der Eigentümerlisten (Excel, CSV) direkt nach dem Datenmodell gebaut, damit Stufe 1 der Klassifikation und das Review Center von Anfang an auf befüllten Stammtabellen stehen. Die Massenbearbeitung mit Vorschau gehört zum Review-Center-Meilenstein. Performance-Test und Ordnerabgleich auf allen Bestandsordnern sind eigene Meilensteine mit Freigabe des Auftraggebers dazwischen.
4. Aufwand: Schätzung 71 bis 105 Personentage (PT) für einen Entwickler ohne Wartezeiten auf Zulieferungen; Neuschätzung an den Prüfpunkten P1 (nach M5) und P2 (nach M7). Erster produktiver Nutzen (Objekt anlegen, Abgleich, Import, Verarbeitung, Review) nach M7, Schätzung 47 bis 69 PT.
5. Größtes Risiko: Ob 10.000 Seiten in unter 3 Stunden verarbeitet werden, entscheidet die gemessene Kernzahl des VPS, nicht der Stack. Alle drei Rechenwege der Vorschläge zeigen: mit drei OCR-Prozessen keine Reserve, ab fünf Prozessen sicher. Deshalb steht der 100-Seiten-OCR-Probelauf auf dem VPS in M0, vor jedem Bau, und die Tarifentscheidung fällt am Prüfpunkt P0.
6. Offene Fragen: 31 gebündelte Fragen (Abschnitt 4), davon 14 blockierend für einzelne Meilensteine. Die Pflichtfragen aus der Befundakte (Objektnummer, Basis 01 bis 04, Umlaute, Präfix, Shared Drive, Immoware24-Export) sind enthalten.

### 1.3 Freigabevorbehalt

Vor Beginn von M1 ist freizugeben:

| Nr. | Freigabegegenstand | Fundstelle | Wirkung ohne Freigabe |
|---|---|---|---|
| FG-1 | Stack A mit den Auflagen aus Anhang C, einschließlich Entwicklerprofil (F1) und erweitertem Container-Katalog (F2) | Abschnitt 1.2, Anhang C | Kein Repository-Gerüst, kein Compose, kein M1 |
| FG-2 | Datenmodell D als verbindliche Bezeichnerquelle einschließlich der Ergänzungsmigration und der Abweichungen vom CR-Wortlaut (D Abschnitt 14, Anhang D dieses Plans) | Anhang B, Anhang D | Kein M2; jede spätere Änderung der Kernentitäten kostet Migrationen |
| FG-3 | Meilensteinreihenfolge und Planänderungen gegenüber Vorschlag A (Import vor Review Center, Massenbearbeitung in M7, Prüfpunkt P1 nach M5, eigene Meilensteine M13 und M14) | Abschnitt 2 | Plan bleibt in der Fassung von Vorschlag A |
| FG-4 | Antworten auf die blockierenden Fragen F1, F2, F4, F5, F13 (vor M1 bzw. M2) | Abschnitt 4 | Seeds und Compose werden mit den in Abschnitt 4 genannten Vorschlagswerten gebaut und später umgestellt |
| FG-5 | Voraussetzungen mit Zeitpunkt vor M0 und M1 (SSH-Zugang, DNS, technisches Konto, OAuth-App) | Abschnitt 5 | M0 und M4 verschieben sich |

M0 (Serverbefund, OCR-Probelauf) kann vor der Freigabe FG-1 bis FG-3 laufen, weil er nur misst und nichts baut; er braucht ausschließlich FG-5.

Der Plan ist eine Entscheidungsvorlage. Fristgebundene, haftungsrelevante oder rechtlich zu prüfende Punkte (Auftragsverarbeitungsverträge, Datenschutz-Folgenabschätzung, Aufbewahrungsfristen, Lizenzfragen bei Weitergabe) sind in Abschnitt 4 und 5 gekennzeichnet und durch die Geschäftsführung mit Rechtsanwalt bzw. Steuerberater zu klären; der Plan gibt dazu keine Rechtsauskunft.

### 1.4 Verbindliche Festlegungen aus der Konsolidierung (Kurzfassung)

Die vier Kritiken haben rund 80 Findings gemeldet (7 hoch, 40 mittel, übrige niedrig). Jedes Finding ist in Anhang B entweder als Festlegung geschlossen oder als offene Frage in Abschnitt 4 geführt. Die tragenden Festlegungen:

| Nr. | Festlegung | Betrifft |
|---|---|---|
| B-01 | Datenmodell D ist einzige Bezeichnerquelle; E wird umgeschrieben; Register `docs/architektur/bezeichnerregister.md` (Kurzfassung heute `docs/architektur.md` 5.7 und Anhang D) entsteht in M2 und ist danach für alle Entwürfe und den Code bindend | alle Module |
| B-06 | Eine Konfigurationsquelle für die KI: nur API-Schlüssel (Secrets) und Basis-URL in `.env`; Modell, Endpunkt, Region, Timeout, Kostenlimit je Provider und Objekt, Reihenfolge Primär und Fallback in `app_settings` | `ai`, `config` |
| B-08 | Ein Schlüsselsatz für Schwellwerte in `app_settings` (`classification.*`), Ablage- und Verschiebeschwelle sind derselbe Wert | `classification`, `drive`, `review` |
| B-09 | Fallarten-Entscheidungstabelle: der fachliche Grund bestimmt `case_type` (`owner_candidates`, `missing_metadata`, `move_proposal`, `drive_structure`, `import_row_uncertain`, `import_candidate`), `unclear` nur ohne fachlichen Grund; Ablage in 06_Sonstiges ist Attribut `misc_subfolder_id` | `pipeline`, `review`, `documents` |
| B-10 | Bestandsdateien unter Schwelle werden nie verschoben (CR 9.4), sondern erhalten `move_proposal`; Ablage in 06_Sonstiges nur für Uploads und für sichere 06-Entscheidungen (Dublette, Fremdobjekt); zweistufiger Dry-Run (Abgleich, Pipeline) | `pipeline`, `drive` |
| B-11 | Maskierung erfasst Bank- und Ausweisdaten, liefert strukturierte Treffer (IBAN-HMAC, letzte vier Stellen) und ist Teil des Provider-Interfaces; ein Dokument mit erkannter Ausweiskopie geht nicht an Stufe 3 | `pipeline`, `ai` |
| B-13 | Queues `ocr`, `classify`, `ai`, `io`, `lists`; Container `worker` (ocr), `worker-nlp` (classify), `worker-io` (ai, io, lists); Drive-Schreibzugriffe mit Sperre je Objekt | Compose, `pipeline` |
| B-14 | Verzeichnisse unter `/srv/objektakte/`: `db`, `redis`, `transit`, `work`, `ocr-cache`, `previews`, `models`, `lists`, `imports`, `requests`, `exports`, `backup`, `secrets`, `deploy`; Downloads nach `work/`; Backup umfasst `transit`, `ocr-cache`, `models`, `lists`, `imports`, `requests`, `exports`, nicht `work`, `previews`, `redis` | Compose, `backup` |
| B-16 | OAuth: Redirect-URIs, Parameter (`include_granted_scopes=false`, PKCE), Secret-Namen und Audit-Aktionen aus G; Nachweis über 8 Tage durch täglichen erzwungenen Refresh (F) plus stündlichen Lesetest (G) | `drive`, Betrieb |
| B-17 | Rollencodes `admin`, `sachbearbeiter`; eine Rechtematrix in G; Verwerfen von Objektzuordnungsfällen nur Admin | `accounts` |
| B-18 | IBAN restriktiv: Klartext für keine Rolle in der Oberfläche, `security.iban_decrypt_roles` leer; vollständige Speicherung nur nach F16 | `parties`, Sicherheit |
| B-43 | Zwei Anwendungskonten in der Datenbank (`app_rw` für web, `app_worker` mit Mindestrechten für die Worker), Trigger gegen UPDATE und DELETE auf `audit_events` als Pflicht | `db`, Sicherheit |
| B-44 | Zwei Build-Targets aus einem Dockerfile: `web` ohne OCR-Binärdateien, `worker` mit | Compose |

### 1.5 Repository-Struktur (Zielbild)

Die Struktur folgt Vorschlag A, ergänzt um die Verzeichnisse für Seeds, Verträge zwischen Entwürfen und Betriebsdokumentation. Sie ist die Referenz für die Spalte "Module" in Abschnitt 2.

```text
<repo-wurzel>/
├── docker-compose.yml                  Produktion, alle Serverwerte als Variablen aus .env
├── docker-compose.dev.yml              lokale Entwicklung
├── docker-compose.test.yml             Testprofil (MariaDB, Redis, Worker, Test-Runner)
├── .env.example                  alle Variablen mit Kommentar, ohne Werte
├── pyproject.toml                Abhängigkeiten, Lockfile-Quelle, ruff, pytest, mypy
├── manage.py
├── docker/
│   ├── app.Dockerfile            mehrstufig, Targets web (ohne OCR) und worker (mit Tesseract deu, ocrmypdf)
│   ├── backup/                   Dockerfile, crontab, backup.sh, restore.sh
│   └── db/                       conf.d (utf8mb4, Buffer-Pool, Volltextparameter), init (Konten, Trigger)
├── db/seeds/                     Seed-Dateien (Kataloge, app_settings, Alt-Alias), von einer Datenmigration geladen
├── docs/
│   ├── anforderungen/            CR-05 unverändert
│   ├── architektur.md            Architekturdokument, verbindliche Referenz (Bezeichnerregister Abschnitt 5.7)
│   ├── architektur/              datenmodell.md, bezeichnerregister.md, image.md, lizenzen.md, ADRs
│   ├── umsetzungsplan.md         dieser Plan
│   ├── betrieb.md                Betriebshandbuch (Ausleseanleitung, Härtung, Compose, Secrets, Backup, Deployment, OAuth-Anleitung, Runbook, Testplan)
│   ├── betrieb/                  serverbefund.md, performance-entscheidung.md, performance-bericht.md, deployment-test.md, restore-protokoll.md, oauth-nachweis.md, abgleich-protokoll.md, wartung.md; ausführliche Fassungen deployment.md, runbook.md, google-oauth.md
│   ├── anleitungen/              bedienung-review-center.md, bedienung-objektanlage.md, admin-konfiguration.md
│   └── plan/                     status.md, entscheidungen.md, berichte/
├── src/
│   ├── objektakte/               Django-Projekt: settings, urls, wsgi, celery
│   ├── hvm_ci/                   portierte CI-Bausteine (ReportLab), Assets
│   └── apps/
│       ├── accounts/  audit/  config/  objects/  parties/  documents/
│       ├── pipeline/  classification/  ai/  drive/  review/
│       ├── requirements/  lists/  imports/  search/  reporting/  ui/
├── tests/
│   ├── unit/  integration/  performance/  fixtures/
└── scripts/
    ├── measure_server.sh  perf_probe.sh  deploy.sh  rollback.sh  check_no_legacy_names.sh  check_no_pii.sh
```

---

## 2. Meilensteine

### 2.0 Übersicht

Aufwände sind grobe Schätzungen in PT für einen Entwickler, ohne Wartezeiten auf Zulieferungen des Auftraggebers, ohne Einarbeitung in ein fremdes Team. Sie sind keine Messgröße. Grundlage sind die Schätzungen aus Vorschlag A (57 bis 87 PT), der Mehraufwand der Auflagen aus B und C (ANNAHME A-01: 4 bis 7 PT) und der Konsolidierungsaufwand aus den Kritiken (ANNAHME A-02: 6 bis 10 PT). Die Spanne ist breit, weil die Basis 01 bis 04 nicht spezifiziert ist (F10) und die Formatvielfalt der Importe erst an realen Unterlagen sichtbar wird.

| Nr. | Meilenstein | Ergebnis in einem Satz | Voraussetzungen des Auftraggebers | PT |
|---|---|---|---|---|
| M0 | Serverbefund und OCR-Probelauf | Gemessene Kerne, RAM, Platte, Traefik-Werte, OCR-Seitenzeit und Skalierung liegen vor; Tarif- und Prozessentscheidung dokumentiert | SSH-Zugang, Deploy-Nutzer, Freigabe für temporären Messcontainer | 2 bis 3 |
| P0 | Prüfpunkt Freigabe | Stack, Datenmodell, Planänderungen, blockierende Fragen, Serverdimensionierung freigegeben | Entscheidung Auftraggeber | 0 |
| M1 | Gerüst, Betrieb, Sicherheit | Alle Container laufen hinter Traefik mit TLS, Login mit TOTP, Rollen, Audit, Konfiguration, Backup, Deployment und Rollback; Host gehärtet | DNS-Eintrag, Deployment-Zeitfenster, F2 | 7 bis 10 |
| M2 | Datenmodell und Objektverwaltung | Alle Tabellen aus D plus Ergänzungsmigration migriert, Seeds geladen, Bezeichnerregister verbindlich, Benennungsfunktion und Objektnummer-Erkennung getestet | F4, F5, F6 (Vorschlagswerte sonst) | 5 bis 7 |
| M3 | Import Eigentümerlisten Teil 1 | Excel, CSV und Immoware24-Profil werden geparst, Vorschläge in einer Import-Ansicht bestätigt und in Stammtabellen übernommen | Beispieldateien (synthetisch), F11 | 4 bis 6 |
| M4 | Drive-Adapter und Ordnerabgleich | OAuth-Verbindung steht, Ordnerabgleich mit Dry-Run läuft gegen Fake und Testverzeichnis, 8-Tage-Nachweis startet | OAuth-App, technisches Konto, F13, Test-Wurzelverzeichnis | 6 bis 9 |
| M5 | Pipeline OCR und Extraktion | Ingest, Hash, Chunking, OCR, Maskierung, Seitenbilder, Jobs, Sweeper laufen wiederaufnehmbar; Messlauf 1.000 Seiten auf dem VPS | keine neuen | 7 bis 10 |
| P1 | Prüfpunkt Performance | Rechenweg mit Messwerten neu gerechnet; Entscheidung über P, Stellhebel oder Tarif | F18 | 0 |
| M6 | Klassifikation Stufe 1 und 2, Ablage | Regeln, NER, Klassifikator, Abgrenzungsregel, Zusatzprüfungen, Konfidenzmodell, Segmentierung, Ablage und Review-Fälle nach Entscheidungstabelle | F8, F9 | 8 bis 12 |
| M7 | Review Center | Liste, Detail mit Seitenbildern, Pflichtfelder, Kandidatenliste, Aktionen, Massenbearbeitung mit Vorschau, Serienmodus, Trainingsdaten | F15 | 8 bis 12 |
| P2 | Prüfpunkt erster produktiver Nutzen | Entscheidung, ob ein erstes reales Objekt vor M8 bis M12 läuft | F30 | 0 |
| M8 | Stufe 3 KI-Provider | OpenAI und Anthropic hinter einer Schnittstelle, Maskierung, Fallback, Kostenlimit, Protokoll, Provider-Wechsel-Test | AVV, EU-Region, Opt-out, API-Schlüssel, F17, F19 | 3 bis 5 |
| M9 | Import Teil 2 | PDF digital und Scan, Mieterlisten, weitere Profile, Importprotokoll | Domus-Beispieldatei (falls vorhanden) | 3 bis 4 |
| M10 | Requirement Engine und Nachforderung | 15 Prüfpunkte als Katalog, Bewertung je Objekt, Nachforderungsentwurf DOCX und PDF im HVM-CI mit Freigabe | HVM-CI-Assets, F10, F22, F23 | 4 bis 6 |
| M11 | Listen | Eigentümer- und Mieterliste als Excel und PDF, Drive-Versionierung, Entprellung, IBAN-Prüfung | F21 | 4 bis 6 |
| M12 | Suche, Reporting, Statusseite | Suche über alle Objekte, KPI, vollständige Statusseite, optionale Alarmierung | F27 | 3 bis 4 |
| M13 | Performance-Test | 10.000 Seiten auf dem VPS in unter 3 Stunden, Review Center p95 unter 2 Sekunden, Bericht mit Messwerten; Deployment-Tests vollständig | Testkorpus-Entscheidung F18 | 3 bis 5 |
| M14 | Ordnerabgleich Bestand | Dry-Run über alle Objektordner, Sichtung und Freigabe, Ausführung, zweiter Lauf, Protokoll | Sichtung und Freigabe des Dry-Run-Protokolls | 1 bis 2 |
| M15 | Dokumentation, Abnahme, Produktivstart | Bedienungsanleitung, Admin-Konfigurationsdoku, Runbook, Grep-Nachweis, Restore-Probe, Einweisung, Definition of Done | Nutzerliste, Einweisungstermine | 3 bis 4 |
| | Summe | | | 71 bis 105 |

Reihenfolgebegründung: M0 liefert die Messwerte für alle Formeln. M1 vor M2, weil Migrationen ein laufendes Gerüst brauchen. M3 vor M4, damit der Abgleich und später die Klassifikation Stammdaten vorfinden; M3 braucht kein Drive. M4 vor M5, weil die Pipeline Dateien aus Drive braucht. M5 vor M6, damit die Klassifikation gegen echte Seitentexte gebaut wird und P1 mit Messwerten stattfindet. M6 vor M7, damit das Review Center gegen echte Fälle entsteht. M8 nach M6, weil Stufe 3 nur Fälle unter dem Schwellwert erhält. M13 vor M14, damit der Bestand nicht mit einer unvermessenen Pipeline bearbeitet wird. M15 zuletzt, weil Anleitung und Konfigurationsdoku den Endstand beschreiben.

### 2.1 M0: Serverbefund und OCR-Probelauf

Ziel: Alle serverabhängigen Größen sind gemessen, nicht angenommen (CR 0.1). Der OCR-Probelauf liefert die Seitenzeit und die Skalierung, damit die Prozesszahl P und ein eventueller Tarifwechsel vor dem Bau entschieden werden (Auflage aller drei Gutachter).

Module: `scripts/measure_server.sh`, `scripts/perf_probe.sh`, `tests/performance/corpus_generator.py` (Vorabfassung), `docs/betrieb/serverbefund.md`, `docs/betrieb/performance-entscheidung.md`, `.env.example` (Entwurf).

Arbeitsschritte:

1. Deploy-Nutzer und SSH-Schlüssel prüfen (nur lesen, keine Änderung). Ergebnis: Zugang funktioniert ohne Passwort.
2. Ressourcen auslesen: `nproc`, `lscpu`, `free -h`, `df -h`, `lsblk`, `timedatectl`, Swap. Werte C, M, D, S in das Ergebnisblatt (G Abschnitt 1.5) eintragen.
3. Docker und Compose auslesen: Versionen, Cgroup-Version, vorhandene Netzwerke, Volumes, belegte Host-Ports.
4. Traefik auslesen (G Abschnitt 1.3): Container, Netzwerke, Kommandozeile, Umgebungsvariablen (nur Namen), Labels, Mounts, statische und dynamische Konfiguration. Ableiten: `TRAEFIK_NETWORK`, `TRAEFIK_ENTRYPOINT`, `TRAEFIK_ENTRYPOINT_INSECURE`, globaler Redirect ja oder nein, `TRAEFIK_CERTRESOLVER`, ACME-Challenge, `exposedByDefault`, Constraints, Traefik-Hauptversion, Swarm nein. Nichts an Traefik ändern.
5. Host-Sicherheit Ist-Zustand (nur lesen): `sshd -T`, `ufw status`, unattended-upgrades, offene Ports, I/O-Scheduler unter `/sys/block` (für die Wirkung von `ionice`, ANNAHME A-25).
6. Korpusgenerator in Vorabfassung: 100 synthetische Seiten (50 gerastert ohne Textebene bei 300 dpi, 50 mit Textebene), Text aus dem Testkatalog E Abschnitt 8, keine realen Daten. Optional zusätzlich 50 echte, anonymisierte Scanseiten des Auftraggebers (F18).
7. OCR-Probelauf auf dem VPS in einem temporären Container (Python-Slim-Image plus tesseract-ocr, tesseract-ocr-deu, ocrmypdf, ghostscript; keine Host-Ports, kein Traefik-Netz, nach dem Lauf entfernt): `OMP_THREAD_LIMIT=1`, `ocrmypdf --skip-text --jobs 1 -l deu`, zuerst ein Prozess, dann 2 und C minus 1 parallele Prozesse. Messen: t_ocr (Standard-Sprachdaten und, falls verfügbar, `tessdata_fast`), t_txt je Digitalseite, Skalierungseffizienz e, RAM-Spitze je Prozess (m_ocr über `docker stats` und cgroup `memory.peak`), Plattenfaktor OCR-Ausgabe gegen Original, Zeit und Bytes je Seitenbild bei 1.200 Pixel langer Kante, Zeichenfehlerrate gegen den bekannten Quelltext.
8. Rechenweg neu rechnen (Abschnitt 2.8, Formel mit Effizienzfaktor) mit den Messwerten; Ergebnis, empfohlenes P, `WORKER_MEM`, `WORKER_NLP_MEM` und eine Tarifempfehlung in `docs/betrieb/performance-entscheidung.md`.
9. `.env.example` mit allen Traefik-Platzhaltern und Formeln aus G Abschnitt 5 befüllen (Werte, keine Geheimnisse); Ergebnisblatt in `docs/betrieb/serverbefund.md` ohne Geheimnisse ablegen.
10. Google-OAuth-Anleitung (G Abschnitt 10) als `docs/betrieb/google-oauth.md` übergeben, damit der Auftraggeber die interne App parallel zu M1 bis M3 anlegt (Redirect-URIs nach F13).

Tests, die grün sein müssen: keine Anwendungstests; Prüfung: `docker compose config --quiet` gegen die `.env.example` mit den Messwerten ist fehlerfrei; der temporäre Messcontainer ist entfernt (`docker ps -a` zeigt ihn nicht mehr).

Abnahmekriterium: `docs/betrieb/serverbefund.md` mit allen Feldern des Ergebnisblatts ausgefüllt; `docs/betrieb/performance-entscheidung.md` mit t_ocr, t_txt, e, m_ocr, Plattenfaktor, Seitenbildkosten, Szenariotabelle und Empfehlung für P und Tarif; Traefik-Werte als Variablen belegt; keine Änderung am Server außer dem entfernten Messcontainer.

Abhängigkeiten: SSH-Zugang und Deploy-Nutzer (Abschnitt 5, V-01), Freigabe für den temporären Messcontainer (V-03), optional anonymisierte Scanseiten (V-16).

Aufwand: 2 bis 3 PT (Schätzung).

### 2.2 Prüfpunkt P0: Freigabe

Nach M0 legt der Entwickler eine Entscheidungsvorlage vor: Messwerte, Szenariotabelle, Empfehlung zu P und Tarif, Stand der blockierenden Fragen. Der Auftraggeber gibt FG-1 bis FG-5 frei oder benennt Änderungen. Zeigt M0 weniger als fünf nutzbare OCR-Prozesse oder eine Seitenzeit über 5 Sekunden, fällt hier die Entscheidung nach F18 (Tarif, Zwei-Phasen-OCR, längere Laufzeit), nicht erst nach M5.

### 2.3 M1: Gerüst, Betrieb, Sicherheit

Ziel: Die Anwendung läuft mit allen Diensten hinter Traefik unter `https://uebernahme.muellerhv.de`, mit Login, Rollen, Audit, Konfigurations-App, Backup, Deployment und Rollback. Der Host ist gehärtet und die Härtung abgenommen (Auflage Ü22 aus Gutachten 3).

Module: `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.test.yml`, `docker/app.Dockerfile` (Targets `web`, `worker`), `docker/backup/`, `docker/db/`, `src/objektakte/`, `src/apps/accounts/`, `src/apps/audit/`, `src/apps/config/`, `src/apps/ui/`, `scripts/deploy.sh`, `scripts/rollback.sh`, `scripts/check_no_legacy_names.sh`, `scripts/check_no_pii.sh`, `docs/betrieb/deployment.md`, `docs/betrieb/runbook.md` (Gerüst), `.github/workflows/`.

Arbeitsschritte:

1. Repository-Gerüst nach Abschnitt 1.5, `pyproject.toml` mit Lockfile, ruff, pytest, mypy für Kernmodule; CI-Workflow mit Lint, Unit-Tests, Image-Build, Grep-Prüfung Altbezeichnung, PII-Prüfung der Doku (nur Trefferzähler gegen die Stammdaten-CSVs, keine Ausgabe von Namen).
2. Dockerfile mit zwei Targets aus einem Build (B-44): `web` ohne Tesseract und Ghostscript, `worker` mit tesseract-ocr, tesseract-ocr-deu (Standard; `tessdata_fast` als Build-Argument), ocrmypdf, ghostscript, qpdf, poppler-utils, img2pdf, pikepdf, pypdfium2 oder PyMuPDF, spaCy-Modell, scikit-learn, rapidfuzz. Nicht privilegierter Nutzer (ANNAHME A-26: UID 10001), `no-new-privileges`, Read-only-Root für `web` mit tmpfs. Paketliste ohne Versionsnummern in `docs/architektur/image.md`.
3. Compose nach G Abschnitt 3 mit den Änderungen aus B-13 und B-14: Dienste `web`, `worker` (Queue `ocr`), `worker-nlp` (Queue `classify`), `worker-io` (Queues `io`, `ai`, `lists`), `beat`, `redis`, `db`, `backup`, Profil `classifier`; Netze `data` (internal), `egress`, `${TRAEFIK_NETWORK}`; Volumes und Bind-Mounts für alle 14 Verzeichnisse mit Rechten je Dienst; dateibasierte Secrets; Ressourcenlimits als Variablen; `OMP_THREAD_LIMIT=1` im Worker; Sicherheits-Header als Traefik-Middleware-Labels; Redirect-Router nur, wenn der Serverbefund keinen globalen Redirect zeigt.
4. Verzeichnisse und Secrets auf dem Server anlegen (G Abschnitt 2.1 und 4.3, ergänzt um `previews`, `lists`, `imports`, `requests`, `exports`); `.env` aus dem Ergebnisblatt.
5. Datenbank-Initialisierung: Konten `app_migrate` (DDL), `app_rw` (web), `app_worker` (Mindestrechte: SELECT auf Stammdaten und Kataloge, INSERT und UPDATE auf Verarbeitungs-, Dokument-, Review-Fall- und Protokolltabellen, INSERT auf `audit_events`; kein Schreibrecht auf `owners`, `units`, `owner_unit_assignments`, `tenants`, `leases`, `users`, `app_settings`), `app_backup` (Dump), `app_ro`; Trigger gegen UPDATE und DELETE auf `audit_events` und `iban_access_log` (B-43); Volltextparameter (Stoppwortliste aus, Mindesttokenlänge 2); utf8mb4, strikter SQL-Modus.
6. Django-Projekt mit zwei Datenbankverbindungen (web: `app_rw`, Worker-Container: `app_worker` über Umgebungsvariable), Migrationen als dokumentierter Deployment-Schritt (nie beim Container-Start), Startprüfung der Schema-Version, Healthcheck `/healthz/`, `/readyz/` mit Token, JSON-Logging mit Request- und Task-ID, Log-Filter für IBAN-, BIC-, Kontonummern- und Ausweisnummernmuster (B-11).
7. `accounts`: Login mit E-Mail und Passwort, TOTP mit Wiederherstellungscodes (django-allauth mit MFA-Modul; Funktionsumfang zum Umsetzungszeitpunkt prüfen, Rückfallweg django-two-factor-auth dokumentieren), Erzwingung des zweiten Faktors per Middleware, Ratenbegrenzung, Sitzungsparameter aus `.env` (ANNAHME A-27), Step-up-TOTP für sensible Aktionen, Rollen `admin` und `sachbearbeiter` als Gruppen mit der Rechtematrix aus G 12.2 plus `review.dismiss_object_case` (B-17). Google-Workspace-Login vorbereitet, aber deaktiviert bis F14.
8. `audit`: Tabelle `audit_events` (D 8.3), Schreibfunktion in der Service-Schicht, Platzhalterregel für IBAN und Geheimnisse, Ansicht mit Filter und CSV-Export, Test, der ein UPDATE auf `audit_events` erwartet fehlschlagen lässt.
9. `config`: Tabelle `app_settings` mit Historie, typisierter Zugriff mit Cache und Redis-Versionsschlüssel, Admin-Formulare aus einem JSON-Schema-Katalog (`src/apps/config/catalog.json`), Seed-Kommando aus `db/seeds/`; erster Seed nur mit Schlüsseln, die M1 braucht (Sicherheit, Logging).
10. `backup`: Container mit Cron, `backup.sh` und `restore.sh` nach G Abschnitt 7 mit erweitertem Umfang (B-14), `status.json`, Healthcheck; `OFFSITE_ENABLED=false` bis F26.
11. `scripts/deploy.sh` mit Erstinstallationspfad (`--first-run`: `up -d db redis backup`, auf `healthy` warten, Dump nur wenn `schema_migrations` existiert, dann Migration, dann `up -d`), `scripts/rollback.sh` (ein Befehl, Image-Tag `previous`), Tag-Protokoll unter `/srv/objektakte/deploy/`.
12. Host-Härtung nach G Abschnitt 6 (Deploy-Nutzer, SSH nur Schlüssel, kein Root-Login, UFW 22, 80, 443, unattended-upgrades, Zeitzone, Journal-Grenze, Swap falls nötig); Docker-Logrotation in `daemon.json` nur nach F27.
13. Statusseite in Grundform (`/status/`): Dienste-Heartbeats, letzte Sicherung, Token-Status (Platzhalter bis M4).
14. Runbook-Gerüst mit den Störungsfällen aus G Abschnitt 14 (Definition of Done Betriebsteil).

Tests, die grün sein müssen (CR 14 Deployment-Test): T1 frischer Checkout mit `scripts/deploy.sh --first-run` (Auslegung nach F20), alle Healthchecks grün, TLS gültig, HTTP leitet auf HTTPS; T2 keine Host-Ports; T3 Isolation des Netzes `data`; T7 Rollback mit einem Befehl; T8 Migration vorwärts und rückwärts auf leerer Datenbank; T11 JSON-Logs ohne IBAN-Muster; T12 Host-Härtung; T13 Login und Rollen (TOTP erzwungen, Konfigurationsseite für Sachbearbeiter verweigert, `auth.denied` im Audit); T14 Healthcheck-Endpunkte. Unit-Tests: Audit-Schreibfunktion, Konfigurationszugriff mit Cache, Log-Filter (IBAN mit und ohne Leerzeichen, OCR-Fehler O statt 0, BIC, BLZ, Kontonummer, Ausweisnummer mit Kontextwort, Negativfälle). Grep-Prüfung leer, PII-Prüfung leer.

Abnahmekriterium: Deployment-Tests T1, T2, T3, T7, T8, T11, T12, T13, T14 bestanden und in `docs/betrieb/deployment-test.md` protokolliert; Anmeldung mit TOTP unter der Domain möglich; Backup-Lauf erzeugt Dump und Archive mit `status.json`; Wiederherstellung auf leerer Instanz einmal geprobt (T6 in Grundform, ohne Fachdaten); `docs/betrieb/deployment.md` und `runbook.md` vorhanden.

Abhängigkeiten: DNS-A-Record (V-04), Freigabe FG-1 und F2 (Container-Katalog), Deployment-Zeitfenster (F27), Entscheidung Sitzungsparameter (F28, Vorschlagswerte sonst).

Aufwand: 7 bis 10 PT (Schätzung).

### 2.4 M2: Datenmodell und Objektverwaltung

Ziel: Das vollständige Schema aus Fachentwurf D einschließlich der Ergänzungsmigration (Anhang D) ist migriert und rückrollbar, die Seeds tragen die CR-Strukturen wortgetreu, das Bezeichnerregister ist verbindlich, und die zentralen Fachfunktionen (Benennungsregel, Objektnummer-Erkennung, historische Zuordnung) sind getestet.

Module: `src/apps/objects/`, `src/apps/parties/`, `src/apps/documents/` (Modelle), `src/apps/drive/` (Modelle `drive_nodes`, `drive_sync_*`, Benennungsfunktion), `src/apps/pipeline/` (Modelle `processing_*`), `src/apps/review/` (Modelle), `src/apps/imports/` (Modelle), `src/apps/requirements/` (Modelle), `src/apps/lists/` (Modelle), `src/apps/ai/` (Modell `ai_calls`), `db/seeds/`, `docs/architektur/bezeichnerregister.md`, `docs/architektur/datenmodell.md` (D plus Ergänzung), `tests/unit/`.

Arbeitsschritte:

1. Bezeichnerregister anlegen (Anhang D als Ausgangspunkt): Tabellen, Statuswerte, Jobtypen, Fallarten, Queues, Verzeichnisse, Konfigurationsschlüssel mit Seed und Kategorie, Audit-Aktionen, `.env`-Variablen. Fachentwurf E wird im selben Schritt auf diese Bezeichner umgeschrieben (Anhang zu E "Bezeichner E zu D"); F, G, H erhalten Verweise (B-01).
2. Migrationen in der Reihenfolge aus D Abschnitt 13.1 (Katalog, Sicherheit, Stammdaten, Akten, Drive, Dokumente, Verarbeitung, KI, Review, Import, Vollständigkeit, Fremdschlüssel-Nachtrag, Seeds), jede mit Rückwärtsoperation, idempotent formuliert; CI führt jede Migration vorwärts und rückwärts aus.
3. Ergänzungsmigration nach Anhang D: `classification_rules`, `classifier_models`, `training_samples`, `completeness_checks`, `document_requests`, `request_text_blocks`, `review_saved_filters`, `import_column_profiles`, `object_progress`; Spalten `documents.drive_md5`, `documents.sha256` nullable, `documents.target_drive_node_id`, `documents.classifier_version`, `owner_files.owner_id`, `review_cases.snoozed_until`, `drive_sync_actions.id_hash_before` und `id_hash_after`, `completeness_findings.manual_*`, `objects.expected_unit_count`, `objects.sepa_used`, `objects.special_levies_in_period`, `objects.previous_manager_*`, `objects.is_test`, `owner_unit_assignments.balance_at_takeover`, `units.se_managed`, `units.vacancy_confirmed`; erweiterte CHECK-Wertevorräte (`documents.status` um `hashed`, `moved_out`; `processing_jobs.job_type` um `analyze_pages`, `ocr_chunk`, `merge_pages`, `render_previews`, `decide`, `classify_ai`, `link_segments`, `generate_lists`, `evaluate_completeness`, `reconcile_drive`, `train_classifier`, `sweep`; `review_cases.case_type` um `missing_metadata`, `drive_structure`, `import_candidate`; `ai_calls.status` um `schema_error`, `provider_error`, `blocked_by_mask_check`; `drive_sync_actions.action_type` um `inventory`; `list_generations.trigger_kind` um `import_commit`, `masterdata_change`, `scheduled`); Unique `(object_id, drive_file_id)` auf `documents`; `roles.code` `admin`, `sachbearbeiter`.
4. Seeds unter `db/seeds/` (B-22): sechs Hauptordner wortgetreu (`05_Eigentümerakte` mit Umlaut, sonst ASCII, bis F5), elf Unterordner der Eigentümerakte, vier Unterordner von 06_Sonstiges, vollständiger Katalog `document_types` aus CR 5 mit stabilen Codes, Unterordner, `requires_period`, `requires_owner` (B-21) plus die Objekt- und Buchhaltungstypen aus CR 6 und der Vorschlag `sonderumlage_einzel`, sechs Zeilen `retention_policies` ohne Werte, zwei Rollen, alle `app_settings`-Schlüssel des Registers mit Seed (darunter `drive.legacy_folder_aliases` als einzige Fundstelle des Altnamens, `owner_file.unit_prefix_mode = always_we` bis F6, `duplicate_owner_documents_in_drive = false`, `security.iban_decrypt_roles = []`, `security.store_full_iban = false`, Schwellwertsatz aus B-08, KI-Schlüssel aus B-06 ohne Werte).
5. Objektverwaltung: Anlage und Bearbeitung von Objekten (Nummer, Bezeichnung, Adresse, Verwaltungsart mit Codes `weg`, `rental`, `weg_with_se`, Übernahmezeitraum, Wirtschaftsjahr, Vorverwaltung, Sollzahl Einheiten, SEPA-Nutzung, Sonderumlagen-Kennzeichen), Objektnummer als führende Ziffernfolge mit konfigurierbarer Stellenzahl 2 bis 6 (Befund 4), Eindeutigkeit über den Zahlenwert.
6. Stammdatenverwaltung in Grundform: Einheiten (Präfix-Mapping aus `units.type_prefix_mapping` einschließlich KE und KELLER zu `cellar`, HAUS, CONTAINER, LAGERHALLE, MVW zu `other`), Eigentümer, Zuordnungen mit Zeiträumen, Überlappungsprüfung in der Transaktion, nächtlicher Konsistenzlauf als Beat-Task (meldet Verstöße als Review-Fälle).
7. Zentrale Benennungsfunktion `build_owner_folder_name` nach F Abschnitt 6.1 mit den Konfigurationsschlüsseln aus F 6.1 im Register; `short_name` statt `company_short_name` (B-20).
8. Abfrage "Wer war am Stichtag Eigentümer" (D 12.1) als Service-Funktion, Grundlage der historischen Zuordnung.
9. Datensatzstatus nach H 5.5 (Lücke vor Vorschlag) in `field_provenance` (B-31).

Tests, die grün sein müssen (CR 14 Unit-Tests): Ordnerbenennung mit allen 32 Fällen aus F 6.4 in beiden Modi `always_we` und `by_type` plus Eigenschaftstest (Determinismus, Länge, keine verbotenen Zeichen); historische Zuordnung (Dokument 03/2025 bei Wechsel 01.07.2026 gehört zum Alteigentümer; synthetische Namen Altmuster und Neumuster); Objektnummer-Erkennung (Ordner mit 2 bis 6 Stellen, `6230 Musterstadt` ist nicht Objekt 623, `0631 Musterstadt` und `631 Musterstadt` sind dasselbe Objekt); Einheitennormalisierung (`WE 14` und `WE14` gleich, alle Muster aus Befund 4); Migrationen vorwärts und rückwärts; Seeds idempotent (zweiter Lauf ohne Änderung); Grep-Prüfung: Altname nur unter `db/seeds/` und `docs/anforderungen/`.

Abnahmekriterium: alle Tabellen aus D plus Anhang D vorhanden, `schema_migrations` konsistent, Rückwärtslauf aller Migrationen in der CI grün; Bezeichnerregister im Repository und von D, E, F, G, H referenziert; Seed enthält die CR-Strukturen wortgetreu; Unit-Tests grün; Objekt 623 (synthetisch) kann angelegt werden, Einheiten und Eigentümer erfasst, Zuordnung mit Zeitraum gespeichert.

Abhängigkeiten: Freigabe FG-2; F4, F5, F6 (sonst Vorschlagswerte, spätere Umstellung per Konfiguration); F10 für die Unterstrukturen 01 bis 04 (sonst leer).

Aufwand: 5 bis 7 PT (Schätzung).

### 2.5 M3: Import Eigentümerlisten Teil 1 (Excel, CSV, Immoware24)

Ziel: Die Stammtabellen können aus Listen der Vorverwaltung befüllt werden, bevor Pipeline und Review Center entstehen (Planänderung aus Gutachten 2). Nichts wird stillschweigend übernommen oder verworfen (CR 15).

Module: `src/apps/imports/` (Formatprofile `csv_generic`, `xlsx_generic`, `immoware24_export`, `generic_table`; Spaltenzuordnung; Feldnormalisierung; Namens-Splitter; Einheitennormalisierung; unscharfer Abgleich; Übernahme), `src/apps/ui/` (gemeinsame Tabellenkomponente mit Zeilenauswahl, Zellstatus, Markierung unsicherer Zeilen; sie wird in M7 für das Review Center wiederverwendet), `src/apps/review/` (Fallart FA6 `import_row_uncertain` in Grundform), `tests/fixtures/imports/` (synthetische Dateien in Originalspaltenstruktur).

Arbeitsschritte:

1. Annahme einer Datei in der Objektansicht: SHA-256, `import_batches`, Quelldatei als `documents`-Zeile mit `source = import` (Ablage in Drive erst ab M4, bis dahin Transit).
2. Formaterkennung und Profilwahl mit Score; Trennzeichen- und Zeichensatzerkennung für CSV; Blattwahl und verbundene Zellen für Excel.
3. Spaltenzuordnung mit Vorschlag aus Synonymwörterbuch (`import.column_synonyms`) und Inhaltsmuster; Oberfläche zur Korrektur; Speichern als Profil (`import_column_profiles`).
4. Feldnormalisierung (Adresse mit Hausnummer, Telefon oder Mobil, E-Mail, Datum, Betrag, PLZ als Text, Land als ISO-2); IBAN wird sofort maskiert, `iban_last4` und `iban_hash` gebildet, `iban_encrypted` nur bei `security.store_full_iban = true`.
5. Namens-Splitter nach H 6.4 mit allen elf Testfällen, Konfidenzbänder als Konfiguration `import.name_split_thresholds` (ANNAHME A-20).
6. Einheitennormalisierung nach H 6.5 mit allen Mustern aus Befund 4; führende Nullen im Vergleichsschlüssel entfernen (abschaltbar).
7. Unscharfer Abgleich gegen `owners` (rapidfuzz, Signale nach H 6.6, Schwellen ANNAHME A-19); Nachnamentreffer allein reicht nie; Konflikt mit bestehender Zuordnung ergibt FA6 mit drei Optionen (Wechsel, Mehrfacheigentum, Dublette).
8. Import-Ansicht: Kopf mit Zählern, Zeilen mit Rohzeile, erkannten Feldern, Vorschlag, Konfidenz; sichere Zeilen gesammelt bestätigen (Vorschau vor Ausführung), unsichere einzeln; Übernahme je Zeile in einer Transaktion mit `field_provenance` und `audit_events`; `rows_total` gleich Summe der Statuswerte.
9. Profil Immoware24 nach H 6.2.1 (Spalten aus Befund 4): Filter auf das gewählte Objekt über den Zahlenwert, Statuswerte `abrechnung`, `archiv`, `technisch` mit Vorschlag "nicht übernehmen" bis F11, VE-Nr als `external_ref`, Miete-Spalte in `leases.notes` bis F11.
10. Importprotokoll als Excel unter `/srv/objektakte/imports/<batch_id>/`.

Tests, die grün sein müssen: Namens-Splitter alle Fälle aus H 6.4; Einheitennormalisierung alle Muster aus Befund 4; Immoware24-Profil mit synthetischer Datei in Originalspaltenstruktur (alle Spalten ohne manuelle Korrektur zugeordnet, Zeilen anderer Objekte und inaktiver Status protokolliert, `rows_total` gleich Summe); Konflikt mit bestehender Zuordnung (keine Überschreibung, drei Optionen erzeugen erwartete Zeilen); Übernahme 1.000 synthetischer Zeilen als Hintergrundjob in unter zwei Minuten (ANNAHME A-21); keine vollständige IBAN in `import_rows.raw_data`, `parsed_fields` oder Protokoll; Import-Ansicht bedienbar (Ende-zu-Ende-Test mit Playwright oder gleichwertig: 40 Zeilen bestätigen).

Abnahmekriterium: Eine synthetische Eigentümerliste in Excel, eine in CSV und eine im Immoware24-Format werden geparst, im Review bestätigt und erzeugen `owners`, `units`, `owner_unit_assignments` mit `data_status = confirmed`, `field_provenance` je Feld und Audit-Zeilen; unsichere Zeilen bleiben als FA6 offen; Stammtabellen bleiben bis zur Bestätigung leer.

Abhängigkeiten: synthetische oder anonymisierte Beispieldateien (V-15), F11 für die Immoware24-Regeln (sonst Vorschlagswerte).

Aufwand: 4 bis 6 PT (Schätzung).

### 2.6 M4: Drive-Adapter und Ordnerabgleich

Ziel: Die Anwendung ist mit dem Konto `ablage@muellerhv.de` verbunden, der Ordnerabgleich nach CR 9 läuft mit Dry-Run gegen den Fake und gegen ein Testverzeichnis im echten Drive, der 8-Tage-Nachweis startet.

Module: `src/apps/drive/` (OAuth-Ablauf, `oauth_tokens`, `GoogleDriveAdapter`, `InMemoryDriveAdapter`, `RecordingDriveAdapter`, Backoff, Ratenbegrenzung, `drive_nodes`, Abgleich mit Planungs- und Ausführungsphase, Protokollexport), `src/apps/review/` (Fallart `drive_structure` mit Untertypen aus F 4.4), `docs/betrieb/google-oauth.md` (finalisiert), `tests/integration/drive/`.

Arbeitsschritte:

1. OAuth nach G Abschnitt 10 und B-16: Redirect-URIs `/auth/google/callback` und `/auth/google/login/callback`, `access_type=offline`, `prompt=consent`, `include_granted_scopes=false`, `state`, PKCE; Prüfung des autorisierenden Kontos gegen `DRIVE_ACCOUNT_EMAIL`; Token mit `TOKEN_KEY` verschlüsselt in `oauth_tokens`; Refresh mit Redis-Sperre; Audit `drive.authorize`, `drive.token_refresh`.
2. Ablaufüberwachung: täglicher erzwungener Refresh (Nachweis) und stündlicher Lesetest auf den Wurzelordner (Überwachung); Statusseite zeigt Konto, Scope, Status, letzten Refresh, Tage seit Autorisierung; bei `invalid_grant` Status `revoked`, Drive-Queue `paused_auth`, OCR läuft weiter.
3. Adapter-Schnittstelle nach F 2.8 mit `supportsAllDrives`, Felderauswahl, Escaping in `q`, Paginierung bis leerer Token (`pageSize` auf den API-Höchstwert, ANNAHME A-24), Backoff (ANNAHME A-22), Ratenbegrenzung (ANNAHME A-23), Metrikzähler.
4. Drive-Schreibidempotenz (B-45): SHA-256 als `appProperties` an jeder hochgeladenen Datei, Prüfung im Zielordner vor jedem Upload, `drive_file_id` unmittelbar nach Abschluss persistieren, Session-URI eines Resumable Uploads in `processing_jobs.payload` (Gültigkeit laut Anbieterdokumentation zum Umsetzungszeitpunkt prüfen); Prüfung, ob die Drive-API das Feld `sha256Checksum` liefert (ANNAHME A-31), sonst `drive_md5` als Rückfall.
5. Wurzelauflösung mit Bestätigung durch den Admin (`drive.root_folder_id`), Objektordner-Erkennung nach F 3.3 mit allen Fällen (ein Treffer, kein Treffer, Papierkorb-Treffer, mehrere Treffer, Verknüpfung), Anlage nach Namensmuster nur mit vollständigen Stammdaten.
6. Abgleich nach F 4.2: Planungsphase (lesen), Ausführungsphase in `seq_no`-Reihenfolge, Umbenennung des Altordners mit Zählung und ID-Hash vorher und nachher, danach Anlage `05_Eigentümerakte`, Sonderfälle A bis H als `drive_structure`-Fälle, Inventur der Bestandsdateien mit `documents.status = registered`, `drive_md5`, Jobs `discover`; Dry-Run schreibt nur `drive_sync_runs` und `drive_sync_actions`, keine Review-Fälle, keine Dokumente.
7. Protokoll und Export (F Abschnitt 10) als JSON und Excel unter `/srv/objektakte/exports/drive-sync/`; Admin-Befehl `drive:undo-rename <action_id>`.
8. Eigentümerakten-Ordner: `ensure_owner_folder` mit verzögerter Anlage (`owner_file.create_folders_eagerly = false` bis F24), zwölf `drive_nodes`-Zeilen je Akte, Prozess-Cache in Redis (ANNAHME A-28).
9. Live-Testmodus gegen das Test-Wurzelverzeichnis des Auftraggebers (F13) mit Laufordner `TESTLAUF_<Zeitstempel>`, Abbruch bei Verwechslung mit der Produktivwurzel, Aufräumen nur in den Papierkorb.
10. Start des 8-Tage-Nachweises auf dem VPS (T9): Tag 0 Erstautorisierung, täglich Refresh und Dry-Run-Abgleich eines Testobjekts.

Tests, die grün sein müssen (CR 14 Integrationstests): Ordnerabgleich mit den drei Testobjekten (ohne Unterordner; mit Altordner des Auffangbereichs (Altbezeichnung laut Konfiguration `drive.legacy_folder_aliases`) und sieben Dateien in zwei Unterordnern; vollständige Struktur mit drei losen Dateien), jeweils Dry-Run, Ausführung, zweiter Lauf: Dateianzahl vorher gleich nachher, ID-Hash gleich, zweiter Lauf `no_changes = 1`, `RecordingDriveAdapter` zeigt nur Lesezugriffe; Objektordner-Erkennung über die Nummer, Anlage bei Fehlen, Review-Eintrag bei doppelter Nummer (Fixture mit `0631 Ort A` und `631 Ort A`, `6230 Ort C`, `Archiv`, `623 Ort D` im Papierkorb, Objekt 700 wird angelegt); Sonderfälle A bis H; Backoff-Sequenzen 403, 403, 200 mit gefälschter Uhr; Abbruch eines Resumable Uploads nach zwei Chunks und Wiederaufnahme ohne Duplikat; Umbenennung mit abweichender Nachzählung erzeugt `rename_count_mismatch` und stoppt weitere Schreibaktionen. Live-Test gegen das Testverzeichnis einmal grün.

Abnahmekriterium: OAuth-Verbindung steht, Statusseite zeigt Token `active`; Integrationstests grün; Live-Test grün; Dry-Run-Plan eines Testobjekts als Tabelle in der Objektansicht sichtbar; Nachweiskette T9 läuft (wird in M13 abgeschlossen).

Abhängigkeiten: technisches Konto mit 2FA (V-05), interne OAuth-App mit den Redirect-URIs aus F13 (V-06), Client-ID und Client-Secret direkt auf dem Server (V-07), Test-Wurzelverzeichnis (V-08), Freigabe der App in der Workspace-Admin-Console (V-09).

Aufwand: 6 bis 9 PT (Schätzung).

### 2.7 M5: Pipeline OCR und Extraktion

Ziel: Jede Datei wird in wiederaufnehmbaren Schritten eingelesen, gehasht, auf Dubletten geprüft, in Seitenblöcke geteilt, erkannt, maskiert und als Seitentext mit Seitenbildern abgelegt; Abbruch und Neustart erzeugen keine Doppelverarbeitung (CR 7). Am Ende steht ein Messlauf auf dem VPS als Grundlage für P1.

Module: `src/apps/pipeline/` (Celery-App, Queues `ocr`, `classify`, `ai`, `io`, `lists`; Tasks `discover`, `hash`, `analyze_pages`, `ocr_chunk` je Chunk, `merge_pages`, `render_previews`, `extract_entities`, `sweep` (Jobtypen nach `docs/architektur.md` 5.4); Objektsperre; Statistiktabelle `object_progress`), `src/apps/documents/` (Seitentexte, Entitäten, Maskierung mit strukturierten Treffern), `src/apps/classification/masking.py`, `tests/integration/pipeline/`, `tests/performance/` (Generator, `perf_run`, `perf_probe.sh`).

Arbeitsschritte:

1. Celery-Konfiguration mit den Pflichtwerten aus E 10.3: `acks_late`, `reject_on_worker_lost`, `prefetch_multiplier = 1`, `visibility_timeout` deutlich über der längsten Chunk-Laufzeit (ANNAHME A-12), Zeitlimits je Task, `max_tasks_per_child`, `max_memory_per_child`, kein Ergebnis-Backend; Tasks tragen nur IDs. Queue-Tabelle aus B-13 in der Compose-Datei und im Register.
2. Zustandsautomat nach D: `documents.status` (`registered`, `hashed`, `ocr_done`, `classified`, `filed`, `review`, `duplicate`, `moved_out`, `error`) und `processing_jobs` (`pending`, `running`, `done`, `failed`, `skipped`); Reservierung über `UPDATE ... WHERE status = 'pending'` mit Prüfung der betroffenen Zeilenzahl; Heartbeat je Seite bzw. alle 30 Sekunden (ANNAHME A-13); `processing_job_events` je Übergang.
3. Idempotenzschlüssel je Jobtyp (B-03): `discover:<object_id>:<drive_file_id>:<md5>`, `hash:<object_id>:<drive_file_id>`, `ocr:<object_id>:<sha256>:<chunk_no>`, `extract_entities:<object_id>:<sha256>`, `classify:<object_id>:<sha256>`, `file_to_drive:<object_id>:<sha256>`.
4. Download nach `/srv/objektakte/work/<sha256>/` (B-14), Größenlimit (ANNAHME A-10) mit Review-Fall statt Download, Dublettenprüfung über `(object_id, sha256)`; Treffer erhält `duplicate_of_document_id` und Fall `unclear` mit `misc_subfolder_id` 03_Dubletten; gleicher Hash in anderem Objekt nur als Hinweis.
5. Formatweiche (PDF, Bildformate über img2pdf, Office-Dateien ohne OCR; erkannte Eigentümer- oder Mieterlisten erzeugen Fall `import_candidate` mit vorbelegtem Profil statt automatischem Import, B-34), Seitenanalyse mit Textebenen-Vorprüfung je Seite (Kriterium E 1.3, ANNAHME A-11), sodass ocrmypdf für rein digitale Dateien nicht startet.
6. Chunking als Konstruktion (B-04): OCR-pflichtige Seiten in Blöcke von K Seiten (ANNAHME A-09: 20), je Block ein `processing_jobs`-Eintrag mit `chunk_no` im `payload` und im Schlüssel; Übergang nach `ocr_done` unter Zeilensperre auf dem Dokument, wenn kein Block mehr offen ist; Merge der Sidecar-Texte je Originalseite.
7. OCR je Chunk: `ocrmypdf --skip-text --jobs 1 --sidecar -l deu`, `OMP_THREAD_LIMIT=1`, Aufruf mit `nice` und `ionice` (Wirkung hostabhängig, ANNAHME A-25), Temp auf Platte unter `work/`, `output-type pdf`, Vorverarbeitungen aus; Sprachdatenvariante als Konfiguration `ocr.tessdata_variant` (Standard bis F18).
8. Maskierung nach B-11 als erster Schritt nach der Texterkennung und vor jeder Persistierung: Muster für DE-IBAN, generische IBAN, BIC, BLZ, Kontonummer und Ausweisnummer mit Kontextwörtern; Rückgabe je Treffer (Seite, Position, letzte vier Stellen, HMAC mit `IBAN_HMAC_KEY`, MOD-97-Ergebnis) und Schreiben nach `document_entities` in derselben Transaktion; Klartext wird danach verworfen; `document_pages.text_content` und `ocr-cache/<sha256>/pages/` enthalten nur maskierten Text.
9. Seitenbilder (`render_previews`, Queue `ocr`): JPEG mit 1.200 Pixel langer Kante (ANNAHME A-14) unter `/srv/objektakte/previews/<document_id>/`, Aufbewahrung `previews.retention_days_after_resolve` (Vorschlag 90 Tage nach Erledigung, ANNAHME A-37, Entscheidung F26), Neuerzeugung auf Anforderung.
10. Sweeper (`sweep`, Beat jede Minute und beim Start jedes Worker-Containers): Jobs mit veraltetem Heartbeat je Jobtyp (`jobs.stale_minutes.<job_type>`, ANNAHME A-12) zurücksetzen, `max_attempts` (ANNAHME A-12) prüfen, verwaiste `work/`-Verzeichnisse nach Frist räumen; Objektsperre: höchstens ein `processing_runs` mit `run_type` `full` oder `incremental` im Status `running` (Redis-Lock plus DB-Prüfung, `processing.max_parallel_objects`, ANNAHME A-15), weitere Objekte `pending` mit Warteposition (B-26).
11. Fortschrittszähler in `object_progress` je Objekt (Dokumente und Seiten je Status, Seiten je Minute gleitend), gepflegt per UPDATE aus den Tasks; die Statusseite liest eine Zeile (Auflage Ü18).
12. Korpusgenerator vollständig (`tests/performance/corpus_generator.py`: konfigurierbarer Scan-Anteil, Seitenzahl je Dokument, Gesamtabrechnungen mit eingebetteten Einzelabrechnungen, keine realen Daten) und Kommando `perf_run` mit Messprotokoll (Seiten je Minute je Schritt, RAM-Spitze je Container, Plattenverbrauch `work/`, `ocr-cache/`, `previews/`).
13. Messlauf auf dem VPS mit 1.000 Seiten (60 Prozent Scan, 40 Prozent Digital, ANNAHME A-04) über die vollständige OCR-Kette mit Seitenbildern; Ergebnis in `docs/betrieb/performance-bericht.md` (Zwischenstand).

Tests, die grün sein müssen (CR 14): Integrationstest Pipeline-Abbruch und Wiederaufnahme im Test-Compose (Worker wird während eines OCR-Chunks hart beendet, Neustart, Dokument endet in `ocr_done`, `SELECT sha256, COUNT(*) ... HAVING COUNT(*) > 1` leer, keine doppelten `document_pages`); Unit-Test IBAN-Maskierung (DE-IBAN mit und ohne Leerzeichen, OCR-Fehler O statt 0, österreichische und niederländische IBAN, BLZ, Kontonummer mit `Kto.-Nr.`, Ausweisnummer mit Kontextwort, Negativfälle Datum, Betrag, Telefonnummer; nach der Maskierung findet der Erkenner keinen Treffer; Dokument-IBAN gleich Stammdaten-IBAN ergibt gleichen `iban_hash` ohne Klartext in `document_pages` oder `document_entities`); Chunk-Merge liefert Seitentexte in Originalreihenfolge; Textebenen-Vorprüfung klassifiziert Deckblatt ohne Text, Seite mit Alt-OCR-Zeichensalat und Digitalseite richtig; Dublette wird vor der OCR erkannt (T29 aus E 8); Objektsperre: zweites Objekt wartet in `pending`; Statistikzeile stimmt mit Zählabfrage überein. Deployment-Test T5 (Container-Abbruch Worker) grün.

Abnahmekriterium: Testobjekt mit rund 500 Seiten läuft im Test-Compose durch; Abbruchtest grün; Messlauf 1.000 Seiten auf dem VPS mit Messprotokoll (t_ocr, e, m_ocr, m_nlp noch als Platzhalter, Plattenfaktor, Seitenbildkosten, Wartezeit der Vorschaujobs); kein Klartext einer IBAN oder Ausweisnummer in `document_pages`, `document_entities`, OCR-Cache oder Logs (Prüfabfrage und Log-Grep).

Abhängigkeiten: keine neuen; F25 (OCR-Rückschreiben) mit Vorschlagswert "Original unverändert".

Aufwand: 7 bis 10 PT (Schätzung).

### 2.8 Prüfpunkt P1: Performance-Entscheidung

Verbindlicher Halt nach M5 (Planänderung aus Gutachten 2). Der Entwickler rechnet den Rechenweg mit den Messwerten aus M0 und M5 neu und legt eine Entscheidungsvorlage vor. Die Formel (Übernahme aus C und B, Anhang C):

```text
T_ocr   = S × (1 − d) × t_ocr / (P × e)  +  S × d × t_txt / P
T_ges   = T_ocr + Nachlauf (letzte Klassifikation, KI-Aufrufe, Drive-Ablage, Listen)
P_min   = S × (1 − d) × t_ocr / (e × 7.200)     bei OCR-Budget 2 Stunden und 1 Stunde Reserve

S   Seiten je Objekt (10.000, CR 7)
d   Anteil Seiten mit Textebene           ANNAHME A-04: 0,40 (Spanne 0,30 bis 0,70), Messung je Objekt
t_ocr  Sekunden je Scan-Seite je Prozess  ANNAHME A-03: 3,5 s Standard (Spanne 2 bis 5 s), rund 1,75 s mit tessdata_fast (Faktor 2); Messwert aus M0
t_txt  Sekunden je Digitalseite           ANNAHME A-05: 0,1 s; Messwert aus M0
e   Skalierungseffizienz                   ANNAHME A-06: 0,85; Messwert aus M0 (P = 1, 2, C minus 1)
P   OCR-Prozesse = max(1, C − 1)           C aus nproc
```

Szenariotabelle mit den Annahmen (Richtung, keine Messwerte; die Zeilen werden in P1 durch Messwerte ersetzt):

| Szenario | P = 3 | P = 5 | P = 7 |
|---|---|---|---|
| Basis: d 0,40, t_ocr 3,5 s, e 0,85 | 8.369 s, rund 139 min | 5.021 s, rund 84 min | 3.587 s, rund 60 min |
| Ungünstig: d 0, t_ocr 3,5 s, e 0,85 | 13.725 s, rund 229 min, verfehlt | 8.235 s, rund 137 min | 5.882 s, rund 98 min |
| Basis mit tessdata_fast: t_ocr 1,75 s | rund 71 min | rund 43 min | rund 30 min |
| Nachlauf (ANNAHME A-07) | 10 bis 15 min | 10 bis 15 min | 10 bis 15 min |

Lesart: Mit den Annahmen hält die Vorgabe bei P = 3 nur im Basisfall und ohne Reserve; ab P = 5 auch im ungünstigen Fall. P_min im Basisfall: 21.000 / (0,85 × 7.200) = 3,4, also mindestens 4 Prozesse und 5 Kerne; im ungünstigen Fall 35.000 / 6.120 = 5,7, also 6 Prozesse und 7 Kerne. Die Drive-Ablage läuft mit Nebenläufigkeit 1 je Objekt (B-13): 1.250 Dokumente × 1 s (ANNAHME A-08) sind rund 21 Minuten, überlappend mit der OCR.

Entscheidungsvorlage an den Auftraggeber (F18): (a) Messwerte und Szenarien, (b) Empfehlung zu P, `WORKER_MEM`, `WORKER_NLP_MEM`, (c) falls die Vorgabe nicht mit Reserve hält: Stellhebel in Reihenfolge des Aufwands (Sprachdaten `tessdata_fast` nach gemessener Fehlerrate, Vorverarbeitung aus, Digitalseiten ohne ocrmypdf, Zwei-Phasen-OCR mit geänderter Bedeutung von "verarbeitet", Tarifwechsel, zweiter Worker-Host). Ohne Entscheidung wird M6 mit den gemessenen Werten und P = C minus 1 gebaut; die Stellhebel bleiben Konfiguration.

### 2.9 M6: Klassifikation Stufe 1 und 2, Ablage

Ziel: Dokumente werden nach der Entscheidungsregel aus CR 6, den sechs Zusatzprüfungen aus CR 7 und dem Konfidenzmodell klassifiziert, Gesamtdokumente werden per Seitenbereich relational zugeordnet, Ablage und Review-Fälle folgen der Entscheidungstabelle B-09, Bestandsdateien der Quellenweiche B-10.

Module: `src/apps/classification/` (Regeln als YAML unter `db/seeds/rules/`, Tabelle `classification_rules`, NER mit Mustern und Gazetteer, spaCy-Ergänzung, TF-IDF-Klassifikator mit Kalibrierung, Kaltstart, Nachtraining, `classifier_models`, Konfidenzmodell, Entscheidungsalgorithmus, Segmentierung), `src/apps/pipeline/` (Tasks `classify`, `link_segments`, `file_to_drive`, Pipeline-Dry-Run), `src/apps/drive/` (Verschiebung, `ensure_folder_path`), `src/apps/review/` (Fallerzeugung nach B-09), `tests/fixtures/documents/` (Testkatalog E 8 als YAML, generiert digital und als 300-dpi-Rasterung), `tests/integration/classification/`.

Arbeitsschritte:

1. Regelwerk (E 2.2) mit Codes statt Ordnernamen und Anzeigewerten (B-12): `category_code`, `subfolder_code`, `document_type_code`, `management_types` als `weg`, `rental`, `weg_with_se`; Versionierung in `classification_rules`, Unit-Test je Regel aus ihren Beispielen; Startkatalog nach E 2.3; Verwaltungsart als Steuergröße (E 2.4, Mieterdokumente in reiner WEG bis F9 nach 06/02_Manuelle_Pruefung).
2. NER (E 3.1): Einheiten-Variantengenerator aus `units.unit_label`, Personen und Firmen über rapidfuzz gegen `owners.search_name` und `tenants.search_name` (Schwellen ANNAHME A-19), Beträge, Zeitbezug mit Präzedenz (Abrechnungs- oder Wirtschaftsjahr vor Zeitraum vor Forderungszeitraum vor Dokumentdatum), Objektmarker eigen und fremd; Ergebnis in `document_entities` mit `matched_*`.
3. Klassifikator (E 3.3): TF-IDF auf Zeichen- und Wort-n-Grammen plus lineares Modell mit Kalibrierung, Modell A Hauptkategorie, Modell B Unterordner plus Unterart innerhalb 05; Eingabe erste M Seiten plus letzte Seite (ANNAHME A-16), Kontexttoken; läuft ausschließlich im Container `worker-nlp` (Queue `classify`, `NLP_CONCURRENCY`, ANNAHME A-17); Interface `LocalClassifier.predict`, Embedding-Variante als optionaler `classifier`-Container vorgesehen, nicht gebaut.
4. Kaltstart (E 3.4) mit `training_samples` (Regel-Labels, synthetische Beispiele, KI-Labels, Gewichte ANNAHME A-18) und `review_decisions` (Gewicht 1,0); Scharfschaltung ab `classification.stage2_min_samples_per_class` (ANNAHME A-18); Kaltstartphase auf der Statusseite sichtbar (B-27); Nachtraining als Beat-Task nachts oder manuell ausgelöst, nie während eines Objektlaufs, Freigabe nur bei nicht schlechterer Makro-F1, Rollback über `classifier_models.active`.
5. Konfidenzmodell (E 7.2) mit dem Schlüsselsatz aus B-08 (`classification.threshold_auto_file`, `threshold_stage3_call`, `threshold_stage3_override`, `stage2_conflict_p`, `bonus_agree`, `malus_disagree`, `gap_factor`, `ai_sample_pct`, `stage3_max_tokens`, `stage2_min_samples_per_class`; Startwerte ANNAHME A-18).
6. Entscheidungsalgorithmus (E 6.1) mit den Einschüben 0, 0b, A, 3 als Vorschlag (F9) und Konfliktregel Gesamtdokument vor Einzelbezug; sechs Zusatzprüfungen (E 6.2) mit den Fällen a bis g; Sonderregel Eigentumsnachweise nach Parteien statt Datum; Kandidatenbildung über die Stichtagsabfrage aus M2.
7. Fallerzeugung nach der Entscheidungstabelle B-09: mehrdeutiger Eigentümer ergibt `owner_candidates`; fehlendes Pflichtmetadatum ergibt `missing_metadata`; Kategorie unklar ergibt `unclear`; physischer Ort nach F8 (Standard: Upload mit sicherer Kategorie 05 und bekanntem Eigentümer ohne Einheit nach `Unbekannte_WE_Nachname`, weder Eigentümer noch Einheit nach `Unzugeordnet`, Kandidatenliste und fehlendes Pflichtmetadatum nach 06_Sonstiges/02_Manuelle_Pruefung); ein offener Fall je Seitenbereich, weitere Gründe in `context.reasons[]`; jede 06-Ablage erzeugt den Fall in derselben Transaktion (CR 8).
8. Quellenweiche (B-10): `source = drive_existing` unter Schwelle ergibt keinen Drive-Schreibzugriff, sondern `move_proposal` mit Zielvorschlag; `source = upload` ergibt Ablage und Fall; sichere 06-Entscheidungen (Dublette, Fremdobjekt) werden auch für Bestandsdateien ausgeführt.
9. Segmentierung (E 6.3) in `document_owner_links` mit `link_kind = page_range` plus `document_classifications` mit Seitenbereich; `batch_key` je Gruppe gleichartiger Segmente für die Massenbearbeitung; physische Zweitablage nur bei `duplicate_owner_documents_in_drive = true` (Backfill-Befehl mit Dry-Run).
10. Ablage in Drive (`file_to_drive`, Queue `io`, Sperre je Objekt): Zielordner über `drive_nodes` und Benennungsfunktion, `documents.target_drive_node_id` als Zustandsmarke, Verschiebung über Elternwechsel, Upload über Resumable Upload mit Idempotenzprüfung, Rücklesen des Elternordners, `status = filed`; bei Fehlern Backoff ohne Zurückrollen der Klassifikation.
11. Pipeline-Dry-Run (B-10): `processing_runs.dry_run = 1` klassifiziert alle Bestandsdateien eines Objekts ohne Drive-Schreibzugriff und liefert den Verschiebungsplan (Quelle, Ziel, Konfidenz, Review-Bedarf) in der Objektansicht; die Ausführung übernimmt die Entscheidungen aus dem OCR-Cache ohne erneute OCR.
12. Aktion "In anderes Objekt übernehmen" in Grundform (B-29): neue `documents`-Zeile im Zielobjekt, alte Zeile `moved_out`, Drive-Move über Objektgrenzen, Neuklassifikation aus dem OCR-Cache, Audit beidseitig.
13. KPI Anteil 06_Sonstiges nach D 12.4 (CR-Definition) in `processing_runs` und zusätzlich bereinigt ohne 03_Dubletten und 04_Nicht_objektbezogen (B-31).

Tests, die grün sein müssen (CR 14): Unit-Test Abgrenzungsregel mit dem Testkatalog E 8 (34 Dokumente, mindestens 20 für die Abgrenzung, synthetische Namen Mustermann, Beispiel, Altmuster, Neumuster, Objekte 623, 624, 625, 631); Unit-Test historische Zuordnung (T20: Zahlungserinnerung 03/2025 bei Wechsel 01.07.2026 gehört zum Alteigentümer); Integrationstest Gesamtabrechnung mit 12 Einzelabrechnungen (eine Masterdatei in 03_Buchhaltung, 12 Zeilen `document_owner_links` mit disjunkten Seitenbereichen, keine Drive-Duplikate bei `false`, zwölf Teilkopien bei `true`); zwei Bestandsdatei-Fälle (sortierte Bestandsdatei knapp unter Schwelle bleibt liegen und erhält `move_proposal`; Bestandsdatei mit sicherer Dublettenentscheidung wird nach 03_Dubletten verschoben); Entscheidungstabelle je Auslöser (Fallart, Ort, KPI-Zählung); Kaltstart: ohne Trainingsdaten liefert Stufe 2 keine Konfidenz, Regeln und Stufe 3 tragen; Pipeline-Dry-Run schreibt nichts in Drive (`RecordingDriveAdapter` nur Lesezugriffe); Regeln greifen nur mit Codes (Test mit geändertem Ordnernamen im Seed).

Abnahmekriterium: Testkatalog vollständig grün; Gesamtabrechnungstest grün; ein synthetisches Objekt mit gemischten Dokumenten läuft im Test-Compose bis `filed` bzw. `review` durch; KPI Anteil 06_Sonstiges wird je Lauf berechnet; jede Ablage in 06 hat einen Review-Fall (Prüfabfrage leer).

Abhängigkeiten: F8 (Physik der Ablage, sonst Standard aus Schritt 7), F9 (Regelauslegungen, sonst Vorschlagswerte), F7 (Fall `WE03_Unbekannt`, sonst Vorschlag).

Aufwand: 8 bis 12 PT (Schätzung).

### 2.10 M7: Review Center

Ziel: Sachbearbeiter bearbeiten alle Fallarten in einer Arbeitsliste mit Detailansicht, Pflichtfeldern für 05_Eigentümerakte, Kandidatenliste, Massenbearbeitung mit Vorschau und Serienmodus; jede Entscheidung ist Trainingsdatum und Audit-Zeile (CR 11, 15). Die Antwortzeit hält unter 2 Sekunden auch während der Verarbeitung.

Module: `src/apps/review/` (Liste, Detail, Aktionen, Massenbearbeitung, Vorschau-Endpunkt, Entwurfsspeicher, gespeicherte Sichten), `src/apps/ui/` (Tabellenkomponente aus M3, HTMX-Partials, Tastaturbelegung), `src/apps/documents/` (rechtegeprüfte Auslieferung der Seitenbilder), `tests/e2e/`, `tests/performance/latency_probe.py`.

Arbeitsschritte:

1. Listenansicht nach H 2.2 über `review_cases` mit den Indizes aus D, Filter, gespeicherte Sichten (`review_saved_filters`), Keyset-Paginierung (ANNAHME A-29: 50 Zeilen), Zähler aus einer gruppierten Abfrage.
2. Detailansicht nach H 2.3 in drei Spalten; Seitenbilder über eine berechtigte View mit Sendfile-Mechanismus, `Cache-Control: private, no-store`, kein erratbarer Pfad, Zugriff auf Vorschauen der Kategorie 05 gilt als Aktenzugriff (B-15, B-19); Original-PDF nur auf ausdrücklichen Wunsch als Stream.
3. Entscheidungsformular: Zielbereich 01 bis 06 (Tasten 1 bis 6), je Zielbereich die Felder aus H 2.3; für 05 Eigentümer (Suche mit Vorschlägen, objektbezogen zuerst), Einheit, Zuordnung, Unterordner, Unterart, Jahr bzw. Zeitraum (Pflicht nach F9); ausdrückliche Auswahl "Einheit unbekannt" und "Eigentümer unbekannt" mit resultierender Akte (B-30); Warnung bei Widerspruch zur Stichtagsabfrage; "Neue Zuordnung anlegen".
4. Aktionen nach H 2.4 (Bestätigen, Umklassifizieren, Dublette, Aufteilen, Zurückstellen mit `snoozed_until`, Verwerfen, Zuweisen, Wiedereröffnen, In anderes Objekt übernehmen), jede in einer Transaktion mit `review_decisions`, `review_cases`, `audit_events`; Drive-Schreibvorgänge nie in der Anfrage.
5. Vorschau-Endpunkt ohne Schreibwirkung für die Massenbearbeitung (Auflage aus B): Eingabe Fall-IDs mit Zielwerten, Ausgabe je Zeile Zielpfad aus der Benennungsfunktion und dem Folder-ID-Cache, Ordner vorhanden ja oder nein, Warnungen; als HTMX-Partial.
6. Massenbearbeitung nach H 2.5 über `batch_key` und freie Auswahl, Vorschautabelle, Konfliktzeilen ausgenommen, Ausführung als Hintergrundjob (ANNAHME A-36: höchstens 500 Fälle) mit `is_bulk` und `bulk_key`, eine Listenerzeugung je Gruppe; serverseitiger Entwurfsspeicher für halbfertige Bearbeitungen (Übernahme-Entscheidung aus Gutachten 2).
7. Serienmodus und Tastaturbelegung nach H 2.6; alle Aktionen auch per Schaltfläche.
8. Trainingsdaten: `review_decisions` mit `features_snapshot`, `text_hashes`, Label-Spalten; Bestätigungen ohne Änderung als positives Beispiel.
9. Rechte: `review.decide` für beide Rollen, `review.dismiss_object_case` nur Admin (B-17); Sachbearbeiter sehen alle Objekte bis F15.
10. Latenzsonde: Locust oder k6 mit fünf gleichzeitigen Nutzern gegen Liste, Detail, Vorschaubild, Eigentümersuche, Entscheidung; Messgröße p95 unter 2 Sekunden, p99 unter 4 Sekunden als Warnschwelle (B-35).

Tests, die grün sein müssen: Ende-zu-Ende-Test (Playwright oder gleichwertig) des 40-Dokumente-Falls (Gesamtabrechnung mit 40 Segmenten, 37 eindeutig, 2 mit Kandidaten, 1 mit unbekannter Einheit; 39 Entscheidungen in einer Aktion, Vorschaupfade stimmen mit der Benennungsfunktion überein, genau eine Listenerzeugung, Audit vollständig); jede Aktion gegen einen synthetischen Fall mit Prüfung der Transaktion; historische Zuordnung im Formular (Auswahl des Neueigentümers für ein Dokument aus 03/2025 erzeugt Warnung und wird protokolliert); Seitenbild ohne Sitzung liefert 401 oder 403; Lastmessung mit fünf Nutzern bei laufender Pipeline im Test-Compose p95 unter 2 Sekunden (ANNAHME A-29).

Abnahmekriterium: Ende-zu-Ende-Test grün; Lastmessung grün; alle Fallarten aus B-09 bedienbar; jede Entscheidung erzeugt `review_decisions` und `audit_events`; kein Drive-Aufruf in einer Anfrage (Recording-Adapter im Test).

Abhängigkeiten: F15 (Rollenmodell, sonst Standard alle sehen alles), F3 (Stellenwert des Review Centers, Entscheidung über eine spätere JavaScript-Insel nach drei realen Objekten).

Aufwand: 8 bis 12 PT (Schätzung).

### 2.11 Prüfpunkt P2: Erster produktiver Nutzen

Nach M7 kann ein reales Objekt angelegt, abgeglichen, importiert, verarbeitet und im Review Center bearbeitet werden, ohne Stufe 3, ohne Listen, ohne Nachforderung. Der Auftraggeber entscheidet nach F30, ob ein erstes Objekt jetzt läuft (mit Kaltstart des Klassifikators und höherem Review-Anteil) oder ob M8 bis M12 zuerst fertig werden. Ein früher Realbetrieb liefert Trainingsdaten und Messwerte für die Kalibrierung; er setzt voraus, dass die Basisverzeichnisse 01 bis 04 wie in F10 entschieden sind und der Ordnerabgleich für dieses eine Objekt im Dry-Run gesichtet wurde.

### 2.12 M8: Stufe 3 KI-Provider

Ziel: Dokumente unter dem Schwellwert gehen als maskierter, gekürzter Textauszug an den Primäranbieter, bei Ausfall an den Fallback, mit identischem Ergebnisformat, Kostenlimit je Provider und Objekt und vollständigem Protokoll (CR 7, 10, 0.1).

Module: `src/apps/ai/` (Protokollklasse, `OpenAIProvider`, `AnthropicProvider`, `FakeClassificationProvider`, Router mit Fallback und Circuit Breaker, Maskierung im Interface, Kostenrechner mit versionierter Preisliste, `ai_calls`), `src/apps/pipeline/` (Task `classify_ai`, Queue `ai`), `src/apps/config/` (Admin-Formulare `ai.*`), `tests/integration/ai/`.

Arbeitsschritte:

1. Konfiguration ausschließlich in `app_settings` (B-06): `ai.provider_order`, `ai.providers.<p>.enabled`, `.model`, `.endpoint`, `.region`, `.timeout_s` (ANNAHME A-32), `.max_attempts`, `.cost_limit_eur_per_object`, `ai.monthly_budget_eur.<p>` (optional, Vorschlag aus G), `ai.max_input_tokens` (ANNAHME A-32), `ai.wall_budget_s`, `ai.store_masked_prompts` (Standard `false`); Secrets nur `openai_api_key`, `anthropic_api_key`; Basis-URL in `.env`; ein Provider ist erst nach gesetztem `enabled` und dokumentierter AVV-Freigabe aktiv.
2. Request-Schema nach E 4.1 und Datenminimierung (Auflage Ü15): Kontextfelder ohne Personennamen, nur Präfixmuster der Einheiten, Taxonomie mit Codes; Antwortschema strikt mit Codes (B-12), Validierung mit pydantic, Reparaturversuch bei Schemaverletzung.
3. Maskierung im Interface (B-11): zweite Prüfung unmittelbar vor dem Senden; Treffer bricht den Aufruf mit Status `blocked_by_mask_check` ab; Dokumente mit erkannter Ausweiskopie oder Kategorie 01_Legitimationsunterlagen gehen nach F19 nicht an Stufe 3.
4. Router: Primär bis `max_attempts`, dann Fallback mit identischem Request; Circuit Breaker je Provider (Auflage aus C); Gesamtbudget je Dokument; bei Ausfall beider Anbieter Status `provider_error`, Ablage 06_Sonstiges/01_Unklar mit Fall; Nachklassifikationslauf manuell, zeitgesteuert nach F17.
5. Kostenlimit je Provider und Objekt gegen `SUM(cost_eur)`; bei Erreichen Status `budget_blocked`, Stufe 3 für das Objekt deaktiviert, Fall mit Grund, Admin kann Limit erhöhen (Verhalten nach F17).
6. `ai_calls` nach D 11.1 mit vereinigter Statusliste; kein Prompttext, nur `prompt_hash`, `masked_entities_count`, `response_summary`; Prompts nur bei `ai.store_masked_prompts = true` mit Aufbewahrung über `retention_policies` (B-07).
7. Admin-Formulare aus dem JSON-Schema-Katalog; Statusseite zeigt Kosten je Objekt und Monat, aktiven Provider, letzte Fehler.
8. Stichprobe für KI-Entscheidungen (`classification.ai_sample_pct`) erzeugt Review-Fälle zur Prüfung (Umfang und Prüfer nach F17).

Tests, die grün sein müssen (CR 14): Unit-Test IBAN-Maskierung vor dem KI-Aufruf (kein IBAN-, Kontonummern- oder Ausweisnummernmuster im Request); Integrationstest Provider-Wechsel mit `FakeClassificationProvider` in vier Szenarien (Timeout des Primäranbieters, 5xx, Schemaverletzung mit Reparatur, Ausfall beider Anbieter) mit Prüfaussagen `ai_calls.fallback_used = 1`, `fallback_of_call_id` gesetzt, `document_classifications.provider` gleich Fallback, Antwort gegen dasselbe Schema validiert, bei Ausfall beider Anbieter Ablage 01_Unklar mit Fall (B-28); Kostenlimit stoppt Aufrufe und erzeugt Fall; Datenminimierung: kein Name aus `owners` im Request; Circuit Breaker öffnet nach konfigurierten Fehlern und schließt nach Frist.

Abnahmekriterium: Provider-Wechsel-Test grün; Maskierungstest grün; ein Testlauf gegen beide echten Anbieter mit synthetischen Dokumenten (nach AVV-Freigabe) liefert Ergebnisse im Schema, Kosten werden je Objekt protokolliert; Admin-Konfiguration je Provider dokumentiert.

Abhängigkeiten: AVV mit beiden Anbietern, bestätigte EU-Region, Trainings-Opt-out (V-10 bis V-12), API-Schlüssel (V-13), F17 (Startwerte), F19 (Ausweisdaten).

Aufwand: 3 bis 5 PT (Schätzung).

### 2.13 M9: Import Eigentümerlisten Teil 2 (PDF, Mieterlisten, weitere Profile)

Ziel: Alle im CR genannten Formate werden angenommen (CR 15): PDF digital und Scan über die Pipeline, Mieterlisten mit `leases`, weitere Profile sobald Beispieldateien vorliegen.

Module: `src/apps/imports/` (Profile `pdf_digital_table`, `pdf_scan_ocr`, `domus_export`, Mieterlisten mit `import_kind = tenant_list` und `mixed`), `src/apps/pipeline/` (Übergabe OCR-Ergebnis mit Wortkoordinaten und Wortkonfidenz), `tests/fixtures/imports/`.

Arbeitsschritte:

1. `pdf_digital_table`: Tabellenerkennung über Linien und Textausrichtung, Rückfall Wörter mit Koordinaten; mehrseitige Tabellen zusammenführen.
2. `pdf_scan_ocr`: OCR-Ergebnis der Pipeline mit Wortkonfidenzen, Zellen unter Schwelle (ANNAHME A-33: 70) als unsicher; externe KI zur Strukturierung nur nach F19.
3. Mieterlisten nach H 6.9: `tenants`, `leases`, `tenant_unit_assignments`, Rollen `tenant` und `co_tenant`, Kaution als Betrag und Anlageform.
4. Profil `domus_export`, sobald eine Beispieldatei vorliegt (F11), sonst Rückfall `generic_table`.
5. Auslösemodell aus der Pipeline (B-34): erkannte Liste in 02_Stammakte erzeugt Fall `import_candidate` mit vorbelegtem Profil; Import läuft nach Bestätigung über die Kette aus M3.
6. Nach Übernahme: Listen und Vollständigkeit des Objekts neu bewerten (Auslöser `import_commit`).

Tests, die grün sein müssen: digitale PDF-Tabelle mit wiederholter Kopfzeile über drei Seiten wird zu einer Tabelle; Scan-Liste mit niedriger OCR-Konfidenz markiert die betroffenen Zellen, Zeilen werden FA6, nichts wird verworfen; Mieterliste erzeugt `leases` mit Beträgen als Zahl und Datumsfeldern; `import_candidate` aus der Pipeline führt nach Bestätigung zum Import.

Abnahmekriterium: je Format eine synthetische Testdatei durchläuft Parser, Review und Übernahme; Importprotokoll je Lauf vorhanden; `rows_total` gleich Summe der Statuswerte.

Abhängigkeiten: F11 (Domus-Beispieldatei, Miete-Spalte), F19 (KI im Import).

Aufwand: 3 bis 4 PT (Schätzung).

### 2.14 M10: Requirement Engine und Nachforderung

Ziel: Die 15 Prüfpunkte aus CR 12 werden datengetrieben bewertet, offene Punkte fließen in einen Nachforderungsentwurf an die Vorverwaltung im HVM-CI mit Freigabeschritt; die Anwendung versendet nichts.

Module: `src/apps/requirements/` (Katalog `completeness_checks`, Bewerter, Zeitraumlogik, `completeness_findings`, manuelle Übersteuerung, `document_requests`, `request_text_blocks`, DOCX und PDF), `src/hvm_ci/` (portierte Bausteine aus `scripts/hvm_briefkopf.py`, Logo, Farben, A4 hoch und quer), `db/seeds/completeness_checks.yaml`, `db/seeds/request_text_blocks.yaml`, `tests/integration/requirements/`.

Arbeitsschritte:

1. Katalog `completeness_checks` mit den 15 WEG-Prüfpunkten wortgetreu aus CR 12 (H 3.2), Mietkatalog nach H 3.6 als Vorschlag (F22), Kandidaten-Prüfpunkte für 01 bis 04 nur nach F10.
2. Bewerter je `evaluator` als reine Funktionen; Lauf `evaluate_object` mit Upsert auf `position_key`; Zeitraumlogik nach H 3.3 mit den Schlüsseln `completeness.default_period_years`, `completeness.statement_expected_after`, `completeness.mostly_complete_pct` (ANNAHME A-34, Werte nach F22); Negativnachweis-Muster für SEPA, Sonderumlagen, Zahlungsvereinbarungen, Mahnverfahren mit manueller Übersteuerung (Rolle nach F22).
3. Auslöser: nach Verarbeitungslauf, nach bestätigender Review-Entscheidung (entprellt), nach Import-Übernahme, nach Stammdatenänderung, manuell, nächtlich für Objekte im Status `takeover`; idempotent.
4. Ausgabe "Offene Punkte" nach H 3.4 als Grundlage für Blatt 3 der Listen (M11) und für die Nachforderung.
5. HVM-CI-Bausteine nach `src/hvm_ci/` portieren (Kennlinie, Logo rechts oben, Anschriftfeld, Infoblock, Betreff, Unterschriftsblock, Fußzeile mit Pflichtangaben, Folgeseite), Seitengröße als Parameter; Farben und Schrift ausschließlich aus der CI (Befund 5), keine erfundenen Kontaktdaten.
6. Nachforderungsgenerator nach H 4: `document_requests` mit Status `draft`, `reviewed`, `approved`, `marked_sent`, `withdrawn`; Textbausteine versioniert; Frist als Platzhalter, Wasserzeichen ENTWURF bis zur Freigabe; Anschrift der Vorverwaltung aus `objects.previous_manager_*`; PDF mit ReportLab, DOCX mit python-docx aus einer Vorlage im HVM-CI (Vorlage neu, Freigabe nach F23); Unterschriftsbild nur in der freigegebenen Fassung (F23); Ablage unter `/srv/objektakte/requests/<object_id>/<version>/`, Drive-Upload nur nach F23.
7. Freigabe nach H 4.5 mit Step-up-TOTP; jede Statusänderung in `audit_events`; Hinweis in der Oberfläche, dass die Freigabe durch die Geschäftsführung erfolgt und der Versand außerhalb der Anwendung stattfindet.

Tests, die grün sein müssen: je Prüfpunkt Fixtures für `missing`, `partial`, `fulfilled`, `not_applicable`; Zeitraumlogik (Stichtag 01.07.2026, Kalenderjahr, Zeitraum drei Jahre ergibt 2023 bis 2026; Abrechnung 2025 vor dem 30.06.2026 "noch nicht fällig"); Idempotenz des Bewertungslaufs; Erzeugung ohne Frist trägt Wasserzeichen und kann nicht auf `reviewed` wechseln; Freigabe erzeugt PDF ohne Wasserzeichen und DOCX mit identischem Positionsbestand, Audit mit Freigebendem; Textbausteinänderung ändert bestehende Schreiben nicht; Bewertungslauf unter 5 Sekunden bei 100 Einheiten und vier Jahren (ANNAHME A-34); PDF trägt Kennlinie, Logo, Fußzeile mit Pflichtangaben (Sichtprüfung durch den Auftraggeber).

Abnahmekriterium: alle 15 Prüfpunkte bewerten ein synthetisches Objekt richtig; Nachforderungsentwurf als PDF und DOCX erzeugt, Freigabeablauf funktioniert, Sichtprüfung des Layouts durch den Auftraggeber erfolgt.

Abhängigkeiten: HVM-CI-Assets im Repository (V-14), F10 (Basis 01 bis 04), F22 (Parameter), F23 (Vorlage, Freigaberolle, Ablageort).

Aufwand: 4 bis 6 PT (Schätzung).

### 2.15 M11: Listen

Ziel: Je Objekt genau eine Eigentümer- und eine Mieterliste als Excel und PDF im HVM-CI, aus den Stammtabellen erzeugt, über die gespeicherte Drive-File-ID versioniert, ohne vollständige Bankdaten (CR 12a).

Module: `src/apps/lists/` (Datensatzbildung, Excel mit openpyxl, PDF A4 quer mit ReportLab, `publish_list`, Entprellung, `list_generations`), `src/hvm_ci/` (A4 quer), `src/apps/pipeline/` (Queue `lists`), `tests/integration/lists/`.

Arbeitsschritte:

1. Datensatzbildung nach H 5.1 (eine Zeile je Zuordnung, Blätter Aktuell und Historie, Einheiten ohne Zuordnung mit Status unvollständig); Spalten wortgetreu nach CR 12a aus `lists.owner_columns` und `lists.tenant_columns`, erweiterbar, Mindestumfang nicht entfernbar; Kaution als zwei Spalten nach F21.
2. Status und Quelle je Datensatz nach H 5.5 und D 3.10 (Vorrang Lücke vor Vorschlag); optionales Blatt Herkunft nach F21.
3. Excel nach H 5.3 (Tabellenobjekt mit Filter, Kopfzeile fixiert, Beträge zwei Nachkommastellen, Daten als Datum, PLZ als Text, keine Platzhalter).
4. PDF A4 quer nach H 5.4 mit Kopf (Objektnummer, Bezeichnung, Verwaltungsart, Stand mit Uhrzeit, Seite X von Y) und Tabellenschrift nach F21 (Vorschlag 8 pt, ANNAHME A-35).
5. Blatt "Offene Punkte" identisch mit der Ausgabe aus M10.
6. IBAN-Prüfung über alle Zellwerte und den PDF-Text vor der Veröffentlichung; Treffer bricht ab und erzeugt Fall.
7. `publish_list` nach F 7.2: gespeicherte File-ID, neue Version, Sollname `00_Eigentuemerliste_NNN` bzw. `00_Mieterliste_NNN` mit Objektnummer in der Schreibweise nach F4, Rückführung manuell verschobener Dateien nach F24, `skipped_unchanged` bei gleichem `content_hash`.
8. Auslöser nach CR 12a und H 5.6 (Lauf, Review-Bestätigung entprellt mit `lists.debounce_seconds`, ANNAHME A-36, Import-Übernahme, Stammdatenänderung, manuell); Sperre je Objekt; lokale Ablage unter `/srv/objektakte/lists/` bei Drive-Fehler.

Tests, die grün sein müssen (CR 14): zwei Läufe ergeben je Objekt genau eine Datei je Liste und Format in Drive (Fake mit Revisionsliste, zweite Generation `done` mit gleicher `drive_file_id` oder `skipped_unchanged`); Historie-Blatt enthält den Alteigentümer nach Wechsel; keine vollständige IBAN in Excel oder PDF (Test mit absichtlich vollständiger IBAN in `notes` bricht ab); Formatierung (Tabellenobjekt, `freeze_panes`, Zahlen- und Datumsformate); Szenarien gelöscht, Papierkorb, verschoben, umbenannt; Rendering-Test mit den längsten Werten passt in ein bis zwei Textzeilen.

Abnahmekriterium: Integrationstest grün; Sichtprüfung von Excel und PDF durch den Auftraggeber; Listen erscheinen nach einer Review-Bestätigung innerhalb der Entprellzeit neu in Drive.

Abhängigkeiten: F21 (Layout), F4 (Schreibweise NNN im Dateinamen).

Aufwand: 4 bis 6 PT (Schätzung).

### 2.16 M12: Suche, Reporting, Statusseite

Ziel: Suche nach Eigentümer, Einheit, Zeitraum, Dokumentunterart über alle Objekte; Reporting je Objekt mit Vollständigkeitsstatus, offenen Review-Fällen und Anteil 06_Sonstiges; vollständige Statusseite; optionale Alarmierung (CR 13, 0.1).

Module: `src/apps/search/` (Volltext-Lookup, Suchansicht), `src/apps/reporting/` (KPI, Objektübersicht, CSV-Export), `src/apps/ui/` (Statusseite), `src/apps/pipeline/` (Alarm-Task in `beat`).

Arbeitsschritte:

1. Suche nach H 7.1: relationale Filter zuerst, Volltext auf `document_pages.text_content` im booleschen Modus mit den in M1 gesetzten Parametern (Stoppwortliste aus, Mindesttokenlänge 2), Umlautvarianten ODER-verknüpft, Ergebnisse je Dokument aggregiert, Rechteprüfung serverseitig; Schnittstelle `SearchIndex.query` für einen späteren Suchdienst; Suchanfragen mit Personenbezug nicht in Logs.
2. KPI nach H 7.3 mit D 12.4 als Referenzdefinition; Alterswarnung `reports.review_age_warning_days` (ANNAHME A-30: 10 Arbeitstage, Wert nach F22); Trefferquote System; KI-Kosten je Objekt; Importstand.
3. Statusseite vollständig nach G 9.3 mit den fünf Queues, Fortschritt aus `object_progress`, Token-Status, Backup, Speicher, Konfiguration mit Hinweistext für leere Aufbewahrungsfristen; Admin-Aktionen Sweeper, Google-Verbindung erneuern, Listen erzeugen; Backup manuell nur als Shell-Hinweis (Vorschlag aus G).
4. Alarmierung per E-Mail nach G 9.5, nur bei `ALERTS_ENABLED=true` und SMTP-Zugang (F27); Schwellen als Konfiguration (ANNAHME A-30).

Tests, die grün sein müssen: Suche `WE 14` und `WE14` liefert dieselbe Einheit; Umlautvarianten; Volltexttreffer mit Seitenangabe; keine IBAN im Index (Prüfabfrage); KPI brutto und bereinigt nachrechenbar; Sachbearbeiter sehen in der Suche nur Ergebnisse, für die `owner_files.read` gilt; Statusseite antwortet unter 2 Sekunden bei laufender Pipeline im Test-Compose.

Abnahmekriterium: Suchszenarien aus CR 13 grün; Objektübersicht mit KPI je Objekt; Statusseite vollständig; Alarmtest (Backup älter als Schwelle) sendet eine Nachricht, falls aktiviert.

Abhängigkeiten: F27 (Alarmierung), F22 (Zielwerte).

Aufwand: 3 bis 4 PT (Schätzung).

### 2.17 M13: Performance-Test

Ziel: Nachweis auf dem VPS, dass ein Objekt mit 10.000 Seiten (gemischt) in unter 3 Stunden vollständig verarbeitet wird und das Review Center währenddessen unter 2 Sekunden antwortet, mit Messwerten im Bericht (CR 14). Abschluss aller Deployment-Tests.

Module: `tests/performance/` (Korpusgenerator, `perf_run`, `perf_probe.sh`, Latenzsonde), `docs/betrieb/performance-bericht.md`, `docs/betrieb/deployment-test.md`.

Arbeitsschritte:

1. Testkorpus nach F18: synthetisch mit 60 Prozent Scan und 40 Prozent Digital (ANNAHME A-04), 8 Seiten je Dokument im Mittel (ANNAHME A-04), darunter Gesamtabrechnungen mit eingebetteten Einzelabrechnungen und einige Dokumente mit mehreren hundert Seiten; zusätzlich ein zweiter Lauf mit 100 Prozent Scan als ungünstigster Fall (B-35). Falls der Auftraggeber anonymisierte Realdokumente bereitstellt, dritter Lauf damit.
2. Lauf 1 (Basis) in ein Testobjekt im Live-Testverzeichnis oder gegen den Fake (Drive-Anteil wird je Operation zusätzlich im Live-Test gemessen); parallel `docker stats` alle 60 Sekunden, `iostat`, Latenzsonde mit fünf Nutzern gegen Objektansicht, Review-Liste, Detail, Vorschaubild, Suche, Entscheidung.
3. Lauf 2 (100 Prozent Scan); Lauf 3 optional.
4. Auswertung: Seiten je Minute je Schritt, RAM-Spitze je Container, Plattenverbrauch `work/`, `ocr-cache/`, `previews/`, Anteil Stufe 3 und Kosten, Wartezeit der Vorschaujobs, p95 und p99 je Endpunkt, OOM-Kills, Ratenlimit-Antworten von Drive, Skalierungseffizienz gemessen; Vergleich mit dem Rechenweg aus P1; alle Annahmen in Anhang A durch Messwerte ersetzen.
5. Feinabstimmung der Stellhebel aus P1 in der Reihenfolge des Aufwands; Wiederholung des betroffenen Laufs.
6. Deployment-Tests abschließen: T4 Serverneustart während eines Laufs (Fortsetzung ohne Doppelverarbeitung), T6 Backup und Wiederherstellung auf leerer Instanz mit Fachdaten, T9 OAuth-Nachweis über mindestens 8 Tage (Export aus `oauth_tokens` und `audit_events`), T10 Ressourcenlimits.

Tests, die grün sein müssen: Performance-Test nach CR 14 (unter 3 Stunden, p95 unter 2 Sekunden); Deployment-Tests T4, T6, T9, T10; Prüfabfragen nach Doppelverarbeitung leer; keine IBAN in Logs.

Abnahmekriterium: `docs/betrieb/performance-bericht.md` mit allen Messwerten (Seiten pro Minute, RAM-Spitze, Speicherverbrauch, p95, p99, Skalierungseffizienz, Kosten), Konfiguration nach Feinabstimmung dokumentiert und in `.env` und `app_settings` eingetragen; alle 14 Deployment-Tests bestanden und protokolliert.

Abhängigkeiten: F18 (Korpus), Testobjekt im Drive-Testverzeichnis (V-08), Zeitfenster für den Neustart-Test (F27).

Aufwand: 3 bis 5 PT (Schätzung).

### 2.18 M14: Ordnerabgleich auf allen bestehenden Objektordnern

Ziel: Definition of Done "Ordnerabgleich auf allen bestehenden Objektordnern durchgeführt, Protokoll liegt vor" (CR 14), in der Reihenfolge Dry-Run, Sichtung, Freigabe, Ausführung, zweiter Lauf.

Module: `src/apps/drive/` (Sammellauf `drive:reconcile --all`), `src/apps/drive/export.py`, `docs/betrieb/abgleich-protokoll.md`, `/srv/objektakte/exports/drive-sync/`.

Arbeitsschritte:

1. Objekte aus dem Bestand anlegen (Nummer, Bezeichnung, Adresse, Verwaltungsart); Quelle nach F11 der Immoware24-Export über den Import oder manuelle Erfassung; keine Neuanlage von Objektordnern in diesem Schritt.
2. Sammellauf Dry-Run über alle Objekte; Export der Planliste als Excel (Blätter Objekte, Aktionen, Hinweise, Review-Fälle) und Übergabe an den Auftraggeber.
3. Sichtung durch den Auftraggeber: Anlagen, Umbenennungen des Altordners (Altbezeichnung laut Konfiguration) zu `06_Sonstiges` mit Dateianzahl, ignorierte Wurzelordner, zusätzliche Ordner, abweichende Schreibweisen, Verknüpfungen, geplante Review-Fälle. Schriftliche Freigabe je Objekt oder für alle.
4. Ausführung des Sammellaufs; je Umbenennung Zählung und ID-Hash vorher gleich nachher; bei Abweichung Abbruch weiterer Schreibaktionen und Meldung.
5. Zweiter Sammellauf als Idempotenznachweis (`no_changes = 1`, nur Lesezugriffe).
6. Review-Fälle `drive_structure` und `duplicate_object_number` durch Sachbearbeiter entscheiden; betroffene Objekte erneut abgleichen.
7. Inventur der Bestandsdateien läuft mit; die Klassifikation der Bestandsdateien (Pipeline-Dry-Run je Objekt, dann Ausführung) ist nicht Teil dieses Meilensteins, sondern Regelbetrieb je Objekt nach Freigabe des jeweiligen Verschiebungsplans.
8. Protokoll: Sammelfassung mit Objektordner-ID, Dateien vorher und nachher, Umbenennungen, Anlagen, offene Fälle, `no_changes` des zweiten Laufs; Grep-Nachweis des Altnamens beigefügt.

Tests, die grün sein müssen: Prüfabfragen nach dem Lauf: je Objekt genau ein aktiver `drive_nodes`-Eintrag je Hauptordner ohne offenen Fall; `file_count_before = file_count_after` für alle Läufe; zweiter Lauf ohne Schreibaktion; keine Datei gelöscht (Zählung rekursiv je Objektordner vorher gleich nachher).

Abnahmekriterium: Protokoll unter `docs/betrieb/abgleich-protokoll.md` und Export unter `exports/drive-sync/`; alle Objektordner registriert oder mit offenem Fall gekennzeichnet; Dateianzahl über den gesamten Wurzelpfad vorher gleich nachher.

Abhängigkeiten: Sichtung und Freigabe des Dry-Run-Protokolls durch den Auftraggeber (V-19); F24 (Sonderfälle).

Aufwand: 1 bis 2 PT plus Wartezeit auf die Freigabe (Schätzung).

### 2.19 M15: Dokumentation, Abnahme, Produktivstart

Ziel: Definition of Done vollständig (CR 14): Anwendung produktiv unter der Domain, Grep leer, Abgleichprotokoll, alle Tests grün, Admin-Konfiguration dokumentiert, Bedienungsanleitung vorhanden.

Module: `docs/anleitungen/bedienung-review-center.md`, `docs/anleitungen/bedienung-objektanlage.md`, `docs/anleitungen/admin-konfiguration.md`, `docs/betrieb/runbook.md` (vollständig), `docs/architektur/lizenzen.md`, `docs/plan/status.md`.

Arbeitsschritte:

1. Bedienungsanleitung nach der Gliederung H 8 (Anmeldung, Objekt anlegen, Ordnerabgleich, Import, Verarbeitung, Review Center mit Tastenübersicht, Listen, Vollständigkeit, Nachforderung, Suche, häufige Fragen), kurz und aufgabenorientiert, mit Bildschirmfotos ohne reale Daten.
2. Admin-Konfigurationsdokumentation: alle `app_settings`-Schlüssel aus dem Register mit Bedeutung, Seed, Wertebereich und Wirkung, gruppiert nach Namensmuster Objektordner, Unterstruktur, Schwellwerte, Duplikat-Option, KI-Provider (Pflicht laut Definition of Done), Aufbewahrungsfristen, Drive, Import, Listen, Sicherheit; `.env`-Variablen mit Formeln; Hinweis, welche Werte einen Neustart brauchen.
3. Runbook vollständig (Token ungültig, Drive-Quota, Platte voll, Worker ohne Fortschritt, Fallback aktiv, Backup fehlgeschlagen, Zertifikat nicht erneuert, Kostenlimit erreicht, Dateianzahl ungleich nach Umbenennung).
4. Lizenzabschnitt (Ghostscript AGPL als Abhängigkeit von ocrmypdf, Redis-Lizenzlage mit Valkey als Option, Datenbanktreiber mit Alternativen; für internen Betrieb unkritisch, bei Weitergabe nach F29 prüfen lassen).
5. Grep-Nachweis (`scripts/check_no_legacy_names.sh`) und PII-Prüfung der Dokumentation, Ergebnis im Abnahmeprotokoll.
6. Restore-Probe aus der jüngsten Sicherung (und aus der Offsite-Kopie, falls F26 aktiviert) mit Protokoll nach G 7.4.
7. Einweisung der Anwender (ANNAHME A-39: 2 Stunden je Anwender), Anlage der Nutzer nach Nutzerliste, TOTP-Einrichtung.
8. Abnahmeprotokoll gegen die Definition of Done, Abschluss von `docs/plan/status.md`, Übergabe des Wartungsplans (quartalsweise Abhängigkeiten, Restore-Probe, Schlüsselrotation nach F28).

Tests, die grün sein müssen: vollständige Testsuite (Unit, Integration) in der CI; Grep leer; PII-Prüfung leer; Deployment-Tests T1 bis T14 protokolliert; Performance-Test bestanden.

Abnahmekriterium: alle sechs Punkte der Definition of Done aus CR 14 mit Nachweis; Abnahme durch den Auftraggeber schriftlich.

Abhängigkeiten: Nutzerliste (V-20), Einweisungstermine, offene Fragen mit Wirkung auf die Anleitung beantwortet (F4, F5, F6, F8).

Aufwand: 3 bis 4 PT (Schätzung).

---

## 3. Risiken

Eintrittswahrscheinlichkeit (W) als niedrig, mittel, hoch; Einschätzung aus CR, Befund, Vorschlägen und Gutachten, keine Messgröße. Frühwarnindikatoren sind so gewählt, dass sie aus Statusseite, Tests oder Protokollen ablesbar sind.

| Nr. | Risiko | W | Auswirkung | Gegenmaßnahme | Frühwarnindikator |
|---|---|---|---|---|---|
| R-01 | OCR-Durchsatz verfehlt: 10.000 Seiten nicht in unter 3 Stunden | mittel | CR-Vorgabe verfehlt, Übernahmen dauern länger, Nachtläufe nötig | OCR-Probelauf in M0 statt M4; Entscheidung an P0 und P1; Chunking; Textebenen-Vorprüfung; `tessdata_fast` nach gemessener Fehlerrate; Zwei-Phasen-OCR oder Tarifwechsel nach F18 | M0: t_ocr über 3,5 s, Effizienz e unter 0,8, C kleiner oder gleich 4; M5-Messlauf unter 56 Seiten je Minute |
| R-02 | Serverressourcen zu klein (RAM, Platte) | mittel | OOM-Kills im Worker, Ingest stoppt bei Plattenreserve, `web` verdrängt | Speicherformel mit Randbedingung Summe unter 0,85 M; P schrittweise senken; `DISK_RESERVE_GB`; Plattenfaktor und Seitenbildkosten in M0 messen; Tarif nach F18 | `docker stats` über 85 Prozent des Limits, `OOMKilled` in `docker inspect`, freier Platz unter Reserve |
| R-03 | Drive-Quota und Ratenlimits | mittel | Ablage verlangsamt, Läufe nicht reproduzierbar, Backoff-Erschöpfung | Clientseitige Ratenbegrenzung, Backoff mit Zufallsanteil, serielle Schreibkette je Objekt, verzögerte Anlage der Aktenordner, Quota in der Cloud Console ablesen und dokumentieren | Zähler 403 `rateLimitExceeded` oder 429 über null im Statusbereich, fehlgeschlagene Aufrufe nach Backoff |
| R-04 | OAuth-Token verfällt oder wird widerrufen | mittel | Drive-Schreibjobs pausieren, kein Datenverlust, Verarbeitung ohne Ablage | Interne Workspace-App, `prompt=consent`, täglicher erzwungener Refresh, Zustand `paused_auth` mit Fortsetzung, Alarm, Neuautorisierung nur durch Admin, keine wiederholten Erstautorisierungen | `consecutive_failures` ab 1, Status `expired` oder `revoked`, Nachweiskette unterbrochen |
| R-05 | Fehlklassifikation historischer Eigentümer: Dokument landet in der Akte des aktuellen statt des damaligen Eigentümers | mittel | Dokument in fremder Akte sichtbar (Datenschutz), falsche Listen, falsche Vollständigkeitsbewertung | Stichtagsabfrage gegen `owner_unit_assignments`, Zeitbezug-Präzedenz, Sonderregel Eigentumsnachweise, Kandidatenliste statt Raten (CR 7), Warnung im Review-Formular, Pflichttest T20 | Anteil `owner_candidates` je Objekt, Anteil `assign_owner`-Korrekturen in `review_decisions`, Trefferquote-KPI |
| R-06 | Datenverlust beim Umbenennen des Altordners (Präfix 05) zu `06_Sonstiges` | niedrig | Dateien nicht mehr am erwarteten Ort | Umbenennung nur über Namensänderung derselben Ordner-ID, Zählung plus ID-Hash vorher und nachher, Abbruch bei Abweichung, `drive:undo-rename`, Dry-Run zuerst, nichts löschen | `rename_count_mismatch`-Fall, `file_count_before` ungleich `file_count_after` |
| R-07 | Unklare Basis-Spezifikation der Ordner 01 bis 04, Requirement Engine und Nachforderungsgenerator (Befund 6.2, 6.3) | hoch | Aufwand in M2, M6, M10 unsicher, Nacharbeit an Seeds, Regeln und Prüfpunkten | F10 vor P0 stellen; Unterstrukturen leer und konfigurierbar; Prüfpunkte als Datenkatalog; Regeln datengetrieben; Kandidaten aus H 3.7 nur nach Freigabe | F10 bei P0 unbeantwortet; Zahl der Regel- und Katalognachträge je reales Objekt |
| R-08 | AVV, EU-Region oder Trainings-Opt-out nicht verfügbar oder verzögert | mittel | Stufe 3 nicht nutzbar, mehr Review-Fälle, Kaltstart verlängert, KPI 06 höher | Provider erst nach dokumentierter Freigabe aktiv; Pipeline ohne Stufe 3 lauffähig; Checkliste V-10 bis V-12 sofort anstoßen; Region und Endpunkt konfigurierbar | V-10 bis V-12 bei Ende M6 offen |
| R-09 | Umlaute in Ordnernamen (`05_Eigentümerakte` neben ASCII-Unterordnern, Personennamen, NFC gegen NFD, Desktop-Clients) | mittel | Doppelanlage, nicht erkannte Ordner, brechende Regeln | Codes statt Ordnernamen in Regeln und Schema (B-12), NFC-Normalisierung, dreistufiger Namensvergleich mit Review bei loser Ähnlichkeit, Ordnernamen als Seed, F5 vor M2 | Häufung `similar_folder_name`, Registrierungen mit Hinweis "Schreibweise abweichend" |
| R-10 | Doppelte Objektnummern oder abweichende Stellenzahl im Wurzelpfad (Befund 4) | mittel | Zuordnung zum falschen Ordner, blockierte Objekte | Erkennung über Zahlenwert, nie raten, `duplicate_object_number`-Fall, Papierkorb-Fall, Protokoll ignorierter Wurzelordner, F4 | `root_matches` über 1, ignorierte Ordner mit Zahlpräfix im Protokoll |
| R-11 | Celery-Konfiguration führt zu Doppelverarbeitung oder hängenden Jobs | mittel | Doppelte OCR, doppelte KI-Kosten, Duplikate in Drive | Zustandsautomat in der Datenbank, Statusprüfung zu Beginn jedes Tasks, kleine Tasks durch Chunking, Sweeper, Abbruchtest T5 bei jeder Celery-Anhebung | wiederholte `running`-Übergänge ohne Fehler in `processing_job_events`, Duplikat-Prüfabfrage nicht leer |
| R-12 | Bedienkomfort des Review Centers mit serverseitigem Rendering | mittel | Nachrüstaufwand für eine JavaScript-Insel | Seitenbilder, Vorschau-Endpunkt, Tastaturbedienung, Serienmodus, Entwurfsspeicher; Entscheidung nach drei realen Objekten (F3) | Rückmeldung der Sachbearbeiter, Zeit je Entscheidung, Anteil Gruppen ohne Einzelkorrektur unter 80 Prozent |
| R-13 | Kaltstart des Klassifikators: Anteil 06_Sonstiges anfangs über 5 Prozent, hohe Stufe-3-Quote und Kosten | hoch in den ersten Wochen | Review-Aufwand, KI-Kosten, Zielwert erst nach Einlernphase | Kaltstart-Labels aus Regeln und synthetischen Beispielen, Kostenlimit, Kaltstartphase auf der Statusseite, Erwartung mit dem Auftraggeber abgestimmt (F17, F30) | Stufe-3-Anteil über 20 Prozent, `misc_share_pct` über 5 nach dem zweiten Objekt |
| R-14 | Drive-Schreibidempotenz: Abbruch zwischen Upload und Persistierung erzeugt Duplikat | niedrig | Doppelte Datei in Drive | SHA-256 als `appProperties`, Prüfung vor Upload, sofortige Persistierung der `drive_file_id`, Session-URI im Job, Integrationstest mit Abbruch | Zählung gleichnamiger Dateien im Zielordner über 1 |
| R-15 | Bank- oder Ausweisdaten gelangen an externe KI | niedrig nach B-11 | Compliance-Verstoß | Maskierung im Provider-Interface, zweite Prüfung vor dem Senden, `blocked_by_mask_check`, Kategorie 01 ausgenommen nach F19, Unit-Tests | Zähler `blocked_by_mask_check` (zeigt Treffer der letzten Sperre), Stichprobenprüfung |
| R-16 | Bezeichnerdrift zwischen Fachentwürfen und Code | hoch vor, niedrig nach B-01 | Inkompatible Implementierungen, doppelte Schemata | Bezeichnerregister in M2, E umgeschrieben, CI-Prüfung Schema gegen Register, Entwürfe verweisen auf F-Nummern | Abweichungen im Review jeder Migration |
| R-17 | Aufwandsunterschätzung, Untergrenze optimistisch | mittel | Verzug, Budgetüberschreitung | Neuschätzung an P1 und P2, Ist-Zeiten je Meilenstein, Prüfpunkte als Haltepunkte | Ist über Plan je Meilenstein um mehr als 30 Prozent |
| R-18 | Ausfall oder Wechsel des einzigen Entwicklers | niedrig bis mittel | Stillstand, Einarbeitung | Standard-Stack mit großem Personalmarkt, Register, Runbook, Entscheidungsprotokoll, Vertretungsfrage in F1 | keine Vertretung benannt |
| R-19 | Reife des MFA-Moduls von django-allauth, Anpassungsbedarf bei Hauptversionen | niedrig | Anpassungsaufwand, Rückfallweg mit zwei Paketen | Prüfung zum Umsetzungszeitpunkt in M1, Lockfile, quartalsweise Anhebung mit Testsuite, Login und TOTP als Ende-zu-Ende-Test | CI rot nach Anhebung |
| R-20 | Verlust eines Verschlüsselungsschlüssels oder des Backup-Schlüssels | niedrig | Chiffrate unlesbar, Wiederherstellung unmöglich | Schlüssel im Passwortmanager bei zwei Personen (V-21), Verlusttabelle G 11.5, quartalsweise Restore-Probe einschließlich Schlüsselprüfung | Restore-Probe schlägt fehl |
| R-21 | Deutsche Volltextsuche in MariaDB ohne Stammformen | mittel | Erwartung der Anwender an Recherche verfehlt | Suche primär über Metadaten (CR 13), Schnittstelle `SearchIndex` für späteren Suchdienst, Erwartung abstimmen | Rückmeldung, p95 der Suche über 2 Sekunden |
| R-22 | Nachforderung mit fehlerhaftem Inhalt oder ohne Freigabe nach außen | niedrig | Außenwirkung gegenüber Vorverwaltung | Entwurf mit Wasserzeichen, Freigabe nur durch Admin mit Step-up, Anwendung versendet nie, Textbausteine freigegeben (V-28) | Status `marked_sent` ohne `approved` ist technisch ausgeschlossen; Prüfabfrage |
| R-23 | Personenbezogene Daten aus dem Bestand geraten in Tests oder Dokumentation | niedrig | Datenschutz | PII-Prüfskript in der CI (nur Trefferzähler), ausschließlich synthetische Fixtures (Anhang B, B-39) | Trefferzähler über null |
| R-24 | Traefik-Konfiguration passt nicht zu den Annahmen (Hauptversion, Swarm, Constraints, Timeouts bei großen Uploads) | niedrig | Compose-Anpassung, Upload-Abbrüche | M0 liest alles aus, alle Werte Variablen, Upload-Test großer Datei in M5 | Serverbefund, Upload-Test |

---

## 4. Offene Fragen an den Auftraggeber

Nur echte Entscheidungen des Auftraggebers. Technische Festlegungen, die der Entwickler selbst treffen kann, stehen in Anhang B. Jede Frage nennt den Kontext, die Optionen mit Empfehlung und die Auswirkung ohne Antwort; "blockiert" heißt, der genannte Meilenstein wird sonst mit dem Vorschlagswert gebaut und später umgestellt, "parallel" heißt, die Antwort wird bis zum genannten Zeitpunkt gebraucht.

### 4.1 Blockierende Fragen

**F1 Stack und Entwicklerprofil** (blockiert M1)
Kontext: Das Gremium empfiehlt Stack A. Die Empfehlung setzt voraus, dass der umsetzende Entwickler Python und Django sicher beherrscht (Gutachten 1 bis 3).
Optionen: (a) Stack A wie in Anhang C, Empfehlung; (b) Stack B, nur bei sicherem TypeScript mit SPA-Framework und Review Center als täglichem Hauptarbeitsplatz, Mehraufwand rund 30 bis 40 PT laut Vorschlag B; (c) Stack C, nur bei deutlichem Laravel-Schwerpunkt. Zusätzlich: Gibt es eine Vertretung, und welche Sprachen beherrscht sie?
Ohne Antwort: M1 startet nicht.

**F2 Erweiterter Container-Katalog** (blockiert M1)
Kontext: CR 7 nennt `web`, `worker`, `queue`, `db`, optional `classifier`. Der Plan sieht zusätzlich `worker-io` (Drive, KI, Listen), `worker-nlp` (Klassifikation mit Modellen im Speicher), `beat` (Zeitplan) und `backup` vor; der Queue-Dienst heißt `redis`.
Optionen: (a) erweiterter Katalog, Empfehlung, weil Netzwerkarbeit keine OCR-Prozesse belegen darf und Modelle nicht in jedem OCR-Prozess geladen werden sollen; (b) nur `worker` mit allen Queues in einem Pool, einfacher, aber Uploads warten hinter OCR und jeder Prozess lädt die Modelle.
Ohne Antwort: (a) wird gebaut.

**F4 Objektnummer** (blockiert M2, wirkt in M4, M11, M14)
Kontext: CR 2 spricht von dreistelligen Nummern, der Bestand enthält 2- bis 5-stellige aktive Nummern (Befund 4). Die Erkennung bestehender Ordner läuft über die führende Ziffernfolge mit konfigurierbarer Stellenzahl 2 bis 6 und Vergleich über den Zahlenwert (Vorschlag, Abweichung vom CR-Wortlaut).
Optionen bei Neuanlage von Objektordnern und in den Listen-Dateinamen: (a) Ist-Nummer ohne Auffüllung, Empfehlung, weil der Bestand so benannt ist; (b) Nullauffüllung auf drei Stellen. Zusätzlich: Ist die Spanne 2 bis 6 Stellen richtig?
Ohne Antwort: (a).

**F5 Schreibweise der Ordnernamen** (blockiert M2)
Kontext: CR 2 und 4 nennen `05_Eigentümerakte` mit Umlaut, die Unterordner in ASCII (`06_Wirtschaftsplaene`, `07_Beschluesse`), die Unterordner von 06_Sonstiges ebenfalls ASCII (Befund 6.5).
Optionen: (a) wortgetreu übernehmen, Personennamen in Aktenordnern mit Umlaut, Empfehlung; (b) durchgängig ASCII (`05_Eigentuemerakte`, Namen transliteriert). Die Werte sind Konfiguration, ab der ersten Drive-Anlage in M4 aber faktisch fest.
Ohne Antwort: (a).

**F6 Präfix der Eigentümerakten-Ordner bei Nichtwohnungen** (blockiert die erste Aktenanlage in M6)
Kontext: CR 4 nennt `WE01_Nachname`. Bei Stellplatz, Garage, Gewerbe ergäbe `WE` für Garage 3 und Wohnung 3 denselben Ordnernamen (F 6.2).
Optionen: (a) typabhängig `GE01_`, `ST03_`, `GA03_`, `TG07_`, `KE02_`, `VE01_`, Empfehlung; (b) immer `WE` (CR-Wortlaut). Seed bis zur Antwort: (b), Tests decken beide Modi ab.
Ohne Antwort: (b), Umstellung später nur für neu angelegte Akten.

**F8 Physischer Ablageort bei mehrdeutiger Zuordnung oder fehlendem Pflichtmetadatum** (blockiert M6)
Kontext: CR 4 definiert `Unbekannte_WE_Nachname` und `Unzugeordnet`, CR 7 verlangt bei mehreren historischen Eigentümern eine Kandidatenliste, CR 5 macht das Jahr bei Abrechnungen und Wirtschaftsplänen zur Pflicht, CR 8 sieht `02_Manuelle_Pruefung` vor. Die Entwürfe E und H wählen unterschiedliche Orte; der Ort verändert den KPI Anteil 06_Sonstiges.
Optionen für Uploads mit sicherer Kategorie 05: (a) Eigentümer bekannt und Einheit unbekannt nach `Unbekannte_WE_Nachname`, weder Eigentümer noch Einheit nach `Unzugeordnet` (CR 4), Kandidatenliste bei mehreren historischen Eigentümern und fehlendes Pflichtmetadatum nach `06_Sonstiges/02_Manuelle_Pruefung` bis zur Entscheidung; KPI zählt nach CR-Definition, ein bereinigter Wert wird zusätzlich ausgewiesen; Empfehlung; (b) Dokument bleibt im Transit bzw. am Fundort, bis der Sachbearbeiter entscheidet, KPI unbelastet, aber keine Sichtbarkeit in Drive. Für Bestandsdateien gilt unabhängig davon CR 9.4: keine Verschiebung unter Schwelle, Vorschlag im Review Center.
Ohne Antwort: (a).

**F10 Basis für die Hauptordner 01 bis 04, Requirement Engine und Nachforderungsgenerator** (blockiert M10, wirkt in M2 und M6)
Kontext: CR 12 verlangt, einen bestehenden Nachforderungsgenerator und eine Requirement Engine zu erweitern; es gibt keinen Code (Befund 6.2). Unterstrukturen und Dokumenttypen von 01 bis 04 sind nicht spezifiziert (Befund 6.3).
Optionen: (a) Es existieren CR-01 bis CR-04, ein Lastenheft oder Vorlagen (Word, Excel, anderes System) außerhalb des Repositories; bitte übergeben; (b) Freigabe, beides minimal neu zu bauen: Unterstrukturen 01 bis 04 leer und konfigurierbar, Kandidaten-Prüfpunkte aus H 3.7 als erste Fassung, Nachforderungsgenerator neu; Empfehlung, falls (a) nicht zutrifft.
Ohne Antwort: (b) mit leeren Unterstrukturen; Nacharbeit, sobald Vorlagen vorliegen.

**F13 Google-OAuth-App** (blockiert M4)
Kontext: Der Auftraggeber legt die interne App an (CR 15). Redirect-URIs und Kontakte müssen vorab feststehen.
Optionen: (a) Redirect-URIs `https://uebernahme.muellerhv.de/auth/google/callback` und `https://uebernahme.muellerhv.de/auth/google/login/callback`, ein OAuth-Client für Drive-Verbindung und Mitarbeiter-Login, Empfehlung; (b) andere Pfade oder zwei getrennte Clients. Zusätzlich: Support- und Entwicklerkontakt für den Zustimmungsbildschirm (eine Adresse der Organisation) und Anlage eines Test-Wurzelverzeichnisses außerhalb von `01_Daten` für den Live-Test (Empfehlung ja); ferner, ob Abgleichsprotokolle zusätzlich in einen Drive-Protokollordner geschrieben werden (`drive.protocol_folder_id`, Empfehlung nein, Protokolle nur unter `exports/`).
Ohne Antwort: M4 kann nicht gegen das echte Drive getestet werden; die Anleitung `docs/betrieb/google-oauth.md` nennt (a).

**F17 Startwerte für Stufe 3 (externe KI)** (blockiert M8)
Kontext: Modell, Endpunkt, Region, Timeout und Kostenlimit sind je Provider konfigurierbar (CR 0.1); Startwerte werden gebraucht.
Entscheidungen: Primär- und Fallback-Anbieter (OpenAI oder Anthropic); Kostenlimit je Provider und Objekt in EUR; optional ein Monatsdeckel je Provider (Vorschlag aus G, nicht im CR); Verhalten bei Erreichen (nur Review, Empfehlung, oder Freigabe eines Zusatzbudgets durch Admin); maximale Tokenzahl des Textauszugs (Vorschlag 3.000); Timeout (Vorschlag 30 Sekunden); maskierte Prompts speichern (Empfehlung nein, nur Hash und Trefferzahl); Stichprobenprüfung von KI-Entscheidungen (Vorschlag 10 Prozent) und durch wen; Nachklassifikationslauf nach KI-Ausfall automatisch zeitgesteuert oder nur manuell (Empfehlung manuell); Zielwert für den Anteil später korrigierter automatischer Ablagen zur Kalibrierung (Vorschlag 2 Prozent).
Ohne Antwort: Stufe 3 bleibt deaktiviert; die Pipeline läuft mit Stufe 1 und 2 und mehr Review-Fällen.

**F18 Performance-Vorentscheidung und Testkorpus** (blockiert P0 und P1)
Kontext: Alle Rechenwege zeigen, dass die 3-Stunden-Vorgabe mit drei OCR-Prozessen keine Reserve hat. M0 misst; die Entscheidung soll vorab feststehen.
Entscheidungen, falls M0 weniger als fünf nutzbare OCR-Prozesse oder mehr als 5 Sekunden je Seite ergibt: (a) größerer VPS-Tarif vor M1, Empfehlung, weil OCR nahezu linear mit den Kernen skaliert; (b) `tessdata_fast` als Standard, falls die gemessene Fehlerrate akzeptabel ist; (c) Zwei-Phasen-OCR (Klassifikation aus den ersten drei Seiten, Volltext nachgelagert), ändert die Bedeutung von "vollständig verarbeitet"; (d) längere Laufzeit je Objekt akzeptiert (Nachtlauf). Zusätzlich: Sollen Objekte technisch strikt seriell laufen (Empfehlung ja, Grundlage der Zusage)? Testkorpus für M0 und M13: synthetisch (Empfehlung) oder anonymisierte Realdokumente; falls möglich, 50 anonymisierte echte Scanseiten für M0.
Ohne Antwort: (a) wird als Empfehlung vorgelegt, gebaut wird mit P = C minus 1.

**F19 Ausweisdaten und externe KI** (blockiert M8)
Kontext: CR 10 verbietet Bank- und Ausweisdaten in KI-Prompts. Ausweisnummern stehen in Kaufverträgen, Vollmachten und Legitimationsunterlagen.
Optionen: (a) Dokumente der Kategorie 01_Legitimationsunterlagen und alle Dokumente mit erkannter Ausweiskopie oder Ausweisnummer gehen grundsätzlich nicht an Stufe 3, auch wenn dadurch mehr Fälle im Review landen, Empfehlung; (b) Übergabe nach Maskierung der Ausweisnummer. Zusätzlich: Darf Stufe 3 zur Strukturierung gescannter Eigentümerlisten (Namen und Anschriften) eingesetzt werden? Empfehlung: zunächst nein.
Ohne Antwort: (a) und nein.

**F21 Layout der PDF-Listen** (blockiert M11)
Kontext: 23 Spalten der Eigentümerliste passen auf A4 quer nicht in der CI-Schriftgröße von 10 bis 11 pt (Befund 5).
Optionen: (a) Tabellenschrift 8 pt mit Zeilenumbruch, alle Spalten erhalten, Empfehlung; (b) zwei Teiltabellen je Abschnitt mit Schlüsselspalten Einheit und Nachname; (c) mehrzeilige Datensätze. Zusätzlich: Kaution als zwei Spalten (Betrag als Zahl, Anlageform als Text)? Empfehlung ja. Ausgeblendetes Blatt Herkunft je Feld in der Excel-Datei? Empfehlung ja.
Ohne Antwort: (a), zwei Kautionsspalten, Blatt Herkunft aus.

**F22 Parameter der Vollständigkeitsprüfung und des Reportings** (blockiert M10)
Kontext: CR 12 nennt die Prüfpunkte, nicht die Zeiträume und Schwellen.
Entscheidungen: Übernahmezeitraum ohne Angabe (Vorschlag drei abgeschlossene Wirtschaftsjahre plus laufendes Jahr); Fälligkeit einer Einzelabrechnung (Vorschlag ab dem 30.06. des Folgejahres); Wirtschaftsplan für das Folgejahr erwartet, wenn der Stichtag im letzten Quartal liegt (Vorschlag ja); Kommunikationsdaten erfüllt bei einem Kanal (Vorschlag ja); Objektfindings "Aufstellung oder Negativerklärung" für SEPA, Sonderumlagen, Zahlungsvereinbarungen, Mahnverfahren standardmäßig in die Nachforderung (Vorschlag ja) und wer sie manuell schließen darf (Vorschlag Sachbearbeiter mit Begründung); Sonderumlagen über manuelles Kennzeichen am Objekt und neue Unterart `sonderumlage_einzel` unter 04_Hausgeld (Vorschlag ja); Mietkatalog nach H 3.6 als Minimalumfang (Vorschlag ja); Kennzeichen Sondereigentumsverwaltung je Einheit manuell (Vorschlag ja); Zielwert Alter offener Review-Fälle (Vorschlag 10 Arbeitstage); bereinigter Anteil 06_Sonstiges zusätzlich ausweisen (Vorschlag ja). Fälligkeiten sind fachliche Vorgaben der Geschäftsführung, gegebenenfalls mit dem Steuerberater; die Anwendung gibt keinen Rechtsrat.
Ohne Antwort: Vorschlagswerte als Konfiguration.

**F23 Nachforderungsschreiben** (blockiert M10)
Kontext: Der Generator wird neu gebaut (F10). Das Schreiben ist ein Entwurf im HVM-CI; die Anwendung versendet nichts.
Entscheidungen: Existiert eine Word-Vorlage im HVM-CI, oder wird sie aus den CI-Werten neu erstellt und vom Auftraggeber freigegeben (Empfehlung neu, Freigabe durch den Auftraggeber)? Freigaberolle Admin gleich Geschäftsführung (Empfehlung ja)? Entwurf ohne Unterschriftsbild, freigegebene Fassung mit Unterschriftsbild (Empfehlung ja)? Ablage der freigegebenen Fassung in Drive unter `02_Stammakte` in einem Unterordner `Uebernahme_Korrespondenz` oder nur in der Anwendung (Empfehlung Drive, sobald die Struktur von 02 nach F10 feststeht)? Seed-Textbausteine vor Produktivstart durch den Auftraggeber prüfen (Empfehlung ja)?
Ohne Antwort: neu erstellte Vorlage, Freigabe Admin, Ablage nur in der Anwendung.

### 4.2 Parallel zu klärende Fragen

**F3 Stellenwert des Review Centers** (bis nach den ersten drei realen Objekten)
Kontext: Serverseitiges Rendering mit HTMX ist tragfähig, aber ohne Ziehen von Seitenbereichen und ohne Server-Push. Wird das Review Center das ganztägige Hauptwerkzeug der Sachbearbeiter, kann eine JavaScript-Insel für das Raster nötig werden.
Optionen: (a) Bewertung nach drei realen Objekten anhand der Bedienrückmeldungen, Empfehlung; (b) JavaScript-Insel von Anfang an einplanen (Mehraufwand).
Ohne Antwort: (a).

**F7 Einheit bekannt, Eigentümer unbekannt** (bis M6)
Kontext: CR 4 regelt diesen Fall nicht.
Optionen: (a) Ordnername `WE03_Unbekannt` und Vollständigkeitsbefund, Empfehlung; (b) andere Bezeichnung; (c) keine Akte, Dokument wartet im Review.
Ohne Antwort: (a).

**F9 Auslegungen der Klassifikationsregeln** (bis M6)
Entscheidungen: (a) Mieterdokumente in reiner WEG-Verwaltung: in die Eigentümerakte des Sondereigentümers unter 08_Korrespondenz mit Review (Vorschlag) oder immer 06_Sonstiges/02_Manuelle_Pruefung; (b) Jahr bzw. Zeitraum im Review-Formular Pflicht für alle Dokumente in 05 (CR 11 Wortlaut) oder nur für Abrechnungen und Wirtschaftspläne (CR 5, Vorschlag); (c) die Einschübe 0, 0b, A und 3 in der Entscheidungsreihenfolge (Dubletten, Fremdobjekt, Legitimation, Mieterbezug) vor bzw. zwischen den drei CR-Schritten bestätigen; (d) Kaltstartphase des Klassifikators (Stufe 2 trägt anfangs nur Erkennung und Abgleich) als bewusste Abweichung von "Stufe 2 immer" akzeptieren.
Ohne Antwort: Vorschlagswerte.

**F11 Immoware24-Export und Importregeln** (bis M3, spätestens M14)
Kontext: CR 15 spricht von keinen Bestandsdaten; der Export vom 01.07.2026 existiert (Befund 4) und könnte die Objekte und Einheiten des Bestands über das Review Center anlegen.
Entscheidungen: Erstimport ja oder nein (Empfehlung ja, über das Profil `immoware24_export` mit Bestätigung je Zeile); Bedeutung der Spalte `Miete_EUR_mtl` (Kaltmiete oder Gesamtmiete); Umgang mit Zeilen im Status `abrechnung`, `archiv`, `technisch` (Empfehlung nicht übernehmen); präfixlose Einheitennummern in WEG-Objekten als Wohnung behandeln (Empfehlung ja, mit mittlerer Konfidenz); liegt eine Beispieldatei eines Domus-Exports vor?
Ohne Antwort: Profil wird gebaut, Import erst nach Freigabe; Objekte für M14 werden manuell erfasst.

**F12 Umzug in eine geteilte Ablage** (jederzeit)
Kontext: Der Wurzelpfad liegt in "Meine Ablage" des technischen Kontos (Befund 6.6). Der Adapter unterstützt beides.
Frage: Ist ein Umzug in ein Shared Drive geplant, und wann? Nach einem Umzug ist ein vollständiger Abgleich aller Objekte nötig.
Ohne Antwort: kein Handlungsbedarf.

**F14 Google-Workspace-Login der Mitarbeiter** (bis M1, spätestens vor Produktivstart)
Entscheidungen: zum Produktivstart nötig oder nachgelagert (Empfehlung nachgelagert, E-Mail plus TOTP genügt); TOTP der Anwendung bleibt auch nach Google-Login Pflicht (Empfehlung ja) oder Google-Login gilt als zweiter Faktor, wenn die Workspace-Richtlinie 2FA erzwingt; nur Domain `muellerhv.de` (Empfehlung ja); automatische Kontoanlage beim ersten Login (Empfehlung nein, nur vom Admin angelegte Konten).
Ohne Antwort: deaktiviert, Vorschlagswerte vorbereitet.

**F15 Rollenmodell und Protokollumfang** (bis M7)
Kontext: CR 10 verlangt, dass Eigentümerakten nur für berechtigte Rollen sichtbar sind, und ein dokumentiertes Rollenmodell.
Entscheidungen: Sachbearbeiter sehen alle Objekte und alle Eigentümerakten (Empfehlung ja bei 2 bis 5 Mitarbeitern) oder Zuweisung je Objekt (Erweiterung); IBAN im Klartext für keine Rolle in der Oberfläche (Empfehlung); jede Anzeige und jeder Download eines Dokuments aus 05 wird protokolliert (Empfehlung ja, als `document.view` und `document.download` in `audit_events`, geht über den CR hinaus); Aufbewahrungsdauer des Audit-Protokolls (enthält Mitarbeiterdaten); Mitarbeiter über die Protokollierung informieren (Pflicht vor Produktivstart).
Ohne Antwort: alle sehen alles, kein Klartext, Dokumentansichten werden protokolliert, keine Löschung des Audits.

**F16 Speicherung der vollständigen IBAN** (bis M3)
Kontext: CR 3 erlaubt "verschlüsselt oder nicht gespeichert". Der Abgleich Dokument gegen Stammdaten braucht nur den HMAC, der beim Erfassen einmal gebildet wird.
Optionen: (a) nur `iban_last4` und `iban_hash` speichern, Empfehlung, solange die Anwendung keinen SEPA-Einzug erzeugt; entfällt Schlüsselrotation für `IBAN_KEY` im Regelbetrieb; (b) zusätzlich verschlüsselt speichern (`security.store_full_iban = true`), falls ein Prozess die IBAN braucht.
Ohne Antwort: (a).

**F20 Auslegung zweier Punkte der Definition of Done** (bis M15)
Entscheidungen: (a) Grep-Prüfung "kein Vorkommen der Altbezeichnung des Auffangordners im Code" (CR 14, Definition of Done) umfasst `src/`, `tests/`, `docs/` mit dokumentierten Ausnahmen `db/seeds/` (Alt-Alias als Konfigurationswert, nötig für CR 9.3), `docs/anforderungen/` (der CR selbst) und `docs/entwurf/` (historische Arbeitspapiere der Entwurfsphase, keine verbindliche Dokumentation); (b) Deployment-Test "`docker compose up -d` auf frischem Checkout" wird als `scripts/deploy.sh --first-run` gelesen, weil Migrationen bewusst nicht beim Container-Start laufen (kein Neustart in Schleife bei fehlgeschlagener Migration).
Ohne Antwort: beide Auslegungen gelten.

**F24 Sonderfälle des Ordnerabgleichs** (bis M14)
Entscheidungen: Treffer nur im Papierkorb: Review-Fall ohne automatische Anlage (Empfehlung) oder Neuanlage mit Hinweis; gleichzeitiges Vorkommen von Altordner (Präfix 05) und `06_Sonstiges`: Review-Fall mit den Optionen aus F 4.4 Fall A (Empfehlung) oder feste Regel; Eigentümerakten-Ordner erst bei erster Ablage anlegen (Empfehlung) oder sofort für alle Einheiten mit voller Unterstruktur; manuell verschobene oder umbenannte Listen-Dateien automatisch zurückführen mit Protokoll (Empfehlung) oder Review-Fall.
Ohne Antwort: Empfehlungen.

**F25 OCR-Ergebnis in Drive** (bis M5)
Optionen: (a) Original in Drive bleibt unverändert, Textebene nur im OCR-Cache und in der Datenbank, Empfehlung; (b) durchsuchbare PDF als neue Version derselben Datei (Versionshistorie bleibt); (c) zusätzliche Datei (widerspricht "keine Duplikate").
Ohne Antwort: (a).

**F26 Sicherung, Schlüsselverwahrung, Aufbewahrung von Artefakten** (bis M13)
Entscheidungen: Offsite-Kopie der Sicherungen gewünscht, Zielort (Objektspeicher eines Anbieters mit AVV in der EU, zweiter Server, Netzwerkspeicher; nicht das Drive-Konto der Nutzdaten), Aufbewahrung am Zielort; Aufbewahrung der lokalen Sicherungen in Tagen (Vorschlag 30); wer verwahrt die Verschlüsselungsschlüssel und den privaten Backup-Schlüssel im Passwortmanager (mindestens zwei Personen); wer außer dem Entwickler erhält SSH-Zugang; Aufbewahrung von Vorschaubildern und OCR-Ausgaben auf dem Server nach Ablage in Drive (Vorschlag Vorschaubilder 90 Tage nach Erledigung, OCR-Cache unbefristet als Wiederaufnahmecache).
Ohne Antwort: keine Offsite-Kopie, 30 Tage, Vorschlagswerte; Schlüsselverwahrung muss vor M1 geregelt sein (V-21).

**F27 Betriebsmeldungen und Deployment** (bis M12)
Entscheidungen: Alarmierung per E-Mail bei Störungen (fehlgeschlagenes Backup, ablaufendes Token, hängende Worker, Platte, Fallback aktiv, Zertifikat) gewünscht, SMTP-Zugang (Workspace-Konto oder anderer Anbieter) und Empfängeradresse; Zeitfenster für Deployments und für den Neustart-Test des Servers (Unterbrechung im Sekunden- bis Minutenbereich); Änderung der Docker-`daemon.json` für die Logrotation aller Container mit einmaligem Neustart des Docker-Dienstes (kurze Unterbrechung von Traefik) erlaubt, oder Rotation nur für die eigenen Dienste (Empfehlung: nur eigene Dienste); Image-Build auf dem VPS außerhalb laufender Verarbeitung (Empfehlung) oder in GitHub Actions mit Registry.
Ohne Antwort: keine Alarmierung, Deployments nach Absprache, Rotation nur eigene Dienste, Build auf dem VPS.

**F28 Sicherheitsparameter und Löschläufe** (vor Produktivstart)
Entscheidungen: Passwortmindestlänge (Vorschlag 12 Zeichen), Sitzungsdauer (Vorschlag 8 Stunden Leerlauf, 12 Stunden absolut), planmäßige Schlüsselrotation (Vorschlag jährlich), Löschläufe mit Vier-Augen-Prinzip durch zweiten Admin oder mit 24 Stunden Wartefrist und Abbruchmöglichkeit, Anzahl Admin-Konten (Empfehlung zwei).
Ohne Antwort: Vorschlagswerte, Löschläufe mit Wartefrist.

**F29 Weitergabe der Software** (jederzeit)
Frage: Ist eine spätere Nutzung außerhalb der Hausverwaltung Müller GmbH denkbar (andere Gesellschaften der Gruppe, Dritte)? Dann sind Komponenten unter AGPL bzw. GPL (Ghostscript als Abhängigkeit von ocrmypdf, gegebenenfalls Datenbanktreiber) vorab durch einen Rechtsanwalt zu prüfen; für den internen Betrieb ist die Lizenzlage nach Einschätzung der Gutachter unkritisch.
Ohne Antwort: interner Betrieb angenommen, Lizenzabschnitt in der Architekturdoku.

**F30 Definition des ersten produktiven Einsatzes** (bis P2)
Optionen: (a) nach M7 ein erstes reales Objekt ohne Stufe 3, Listen und Nachforderung (liefert Trainingsdaten und Messwerte, mehr Review-Aufwand); (b) Produktivstart erst nach M15 mit vollem Umfang. Empfehlung: (a) für ein einzelnes, überschaubares Objekt, sofern F10 beantwortet ist.
Ohne Antwort: (b).

**F31 Aufbewahrungsfristen je Dokumentkategorie** (vor Produktivstart, ohne Wirkung auf den Bau)
Kontext: CR 15: Standardwerte leer, Hinweis "durch Geschäftsführung und Steuerberater festzulegen", keine automatische Löschung ohne gesetzten und freigegebenen Wert.
Frage: Wann liegen die Werte je Kategorie vor? Bis dahin zeigt der Admin-Bereich den Hinweis, ein Löschlauf ist technisch nicht auslösbar. Das Löschkonzept umfasst auch Transitdateien, OCR-Cache, Seitenbilder und Sicherungen.
Ohne Antwort: keine Löschung, kein Handlungsbedarf für den Bau.

---

## 5. Voraussetzungen durch den Auftraggeber

Checkliste mit Zuständigkeit und spätestem Zeitpunkt. Ohne die Position startet der genannte Meilenstein nicht oder bleibt unvollständig.

| Nr. | Voraussetzung | Zuständig | Spätestens vor | Nachweis |
|---|---|---|---|---|
| V-01 | SSH-Zugang zum VPS 187.124.23.80 für einen Deploy-Nutzer mit Schlüssel, sudo und Docker-Gruppe; kein Arbeiten als root | Auftraggeber (IONOS-Zugang) | M0 | Login ohne Passwort funktioniert |
| V-02 | Serverbefund gemeinsam erheben: `nproc`, `free -h`, `df -h`, Docker, Traefik-Konfiguration nur lesen | Entwickler mit Zugang aus V-01 | M0 | `docs/betrieb/serverbefund.md` |
| V-03 | Freigabe für einen temporären Messcontainer auf dem VPS (OCR-Probelauf, keine Host-Ports, kein Traefik-Netz, danach entfernt) | Auftraggeber | M0 | schriftlich |
| V-04 | DNS-A-Record `uebernahme.muellerhv.de` auf 187.124.23.80 | Auftraggeber (DNS-Verwaltung) | M1 | `dig` liefert die IP |
| V-05 | Technisches Konto `ablage@muellerhv.de` angelegt, 2FA aktiviert, Drive aktiv, Zugriff auf den Wurzelpfad `Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten` | Auftraggeber (Workspace-Admin) | M4 | Anmeldung möglich, Ordner sichtbar |
| V-06 | Google-Cloud-Projekt in der Organisation, Drive API aktiviert, Zustimmungsbildschirm Nutzertyp Intern, OAuth-Client Webanwendung mit den Redirect-URIs aus F13 (Anleitung `docs/betrieb/google-oauth.md` aus M0) | Auftraggeber (Workspace-Admin) | M4 | Client-ID vorhanden |
| V-07 | Client-ID in `.env`, Client-Secret als Secret-Datei direkt auf dem Server (nicht per E-Mail oder Chat) | Auftraggeber oder Entwickler in Sitzung | M4 | Datei unter `/srv/objektakte/secrets/` |
| V-08 | Test-Wurzelverzeichnis im Drive des technischen Kontos außerhalb von `01_Daten` (Live-Test, Performance-Test) | Auftraggeber | M4 | Folder-ID übergeben |
| V-09 | Freigabe der App in der Workspace-Admin-Console (Drittanbieter-App-Zugriff, Drive für das Konto aktiv) | Auftraggeber (Workspace-Admin) | M4 | Erstautorisierung gelingt |
| V-10 | Auftragsverarbeitungsvertrag mit OpenAI für die API-Nutzung, Kopie in der Verfahrensdokumentation | Geschäftsführung, ggf. Rechtsanwalt | M8 (vor dem ersten Aufruf mit Produktivdaten) | Vertragskopie |
| V-11 | Auftragsverarbeitungsvertrag mit Anthropic für die API-Nutzung | Geschäftsführung, ggf. Rechtsanwalt | M8 | Vertragskopie |
| V-12 | EU-Region bzw. EU-Endpunkt je Anbieter verbindlich festgelegt; Trainings-Opt-out und Aufbewahrung der API-Eingaben (Zero-Retention oder Frist) schriftlich bestätigt | Geschäftsführung | M8 | Bestätigung, Werte in `app_settings` und `.env` |
| V-13 | API-Schlüssel beider Anbieter als Secret-Dateien auf dem Server | Auftraggeber | M8 | Dateien vorhanden |
| V-14 | HVM-CI-Assets in das Repository: `Logo_HVM.jpg`, `scripts/hvm_briefkopf.py`, Farb- und Schriftwerte; Freigabe der Portierung nach `src/hvm_ci/` | Auftraggeber | M10 | Dateien im Repository |
| V-15 | Beispieldateien für die Importprofile (synthetisch oder anonymisiert): Excel, CSV, Immoware24-Struktur (nach F11), Domus falls vorhanden, digitale und gescannte PDF-Listen | Auftraggeber | M3 (Excel, CSV, Immoware24), M9 (PDF, Domus) | Dateien unter `tests/fixtures/imports/` ohne Personenbezug |
| V-16 | Optional 50 anonymisierte echte Scanseiten für den OCR-Probelauf; Entscheidung zum Testkorpus (F18) | Auftraggeber | M0 bzw. M13 | Dateien oder Entscheidung |
| V-17 | Aufbewahrungsfristen je Dokumentkategorie (F31) | Geschäftsführung mit Steuerberater | vor Produktivstart, ohne Wirkung auf den Bau | Werte im Admin-Bereich freigegeben |
| V-18 | Freigabe FG-1 bis FG-3 (Stack, Datenmodell, Plan) | Auftraggeber | M1 | schriftlich, Verweis auf Nummern |
| V-19 | Sichtung und Freigabe des Dry-Run-Protokolls des Bestandsabgleichs (Anlagen, Umbenennungen, Hinweise) | Auftraggeber | M14 Ausführung | schriftlich je Objekt oder gesamt |
| V-20 | Nutzerliste mit Rollen, mindestens zwei Admin-Konten, Einweisungstermine | Auftraggeber | M15 | Liste |
| V-21 | Hinterlegung aller Verschlüsselungsschlüssel und des Root-Passworts im Passwortmanager bei mindestens zwei Personen | Geschäftsführung | M1 (unmittelbar nach Erzeugung der Secrets) | Bestätigung, Prüfung in der Restore-Probe |
| V-22 | Zielort der Offsite-Kopie mit AVV und EU-Standort (falls F26 ja) | Geschäftsführung | M13 | Zugangsdaten als Secret |
| V-23 | SMTP-Zugang und Empfängeradresse für Alarmierung (falls F27 ja) | Auftraggeber | M12 | Zugangsdaten als Secret |
| V-24 | Zeitfenster für Deployments und für den Neustart-Test des Servers | Auftraggeber | M1, M13 | Termine |
| V-25 | Datenschutz: Verzeichnis der Verarbeitungstätigkeiten ergänzt, Einschätzung zur Datenschutz-Folgenabschätzung, Informationspflichten gegenüber Eigentümern und Mietern, technische und organisatorische Maßnahmen dokumentiert, Mitarbeiter über das Audit-Protokoll informiert (G Abschnitt 13) | Geschäftsführung mit Datenschutzberater | vor Produktivstart | Dokumente in der Verfahrensdokumentation |
| V-26 | Fachliche Bestätigung, dass Verwaltervertrag und Bestellung die Verarbeitung der Unterlagen der Vorverwaltung decken | Geschäftsführung, ggf. Rechtsanwalt | vor Produktivstart | Vermerk |
| V-27 | Hosting: AVV mit IONOS geprüft, Serverstandort dokumentiert | Geschäftsführung | vor Produktivstart | Vermerk |
| V-28 | Freigabe der Seed-Textbausteine und der Vorlage des Nachforderungsschreibens (F23) | Geschäftsführung | M10 Abnahme | Freigabevermerk |
## 6. Rollback- und Datensicherheitsregeln je Meilenstein

### 6.1 Regeln für alle Meilensteine

1. Die Anwendung löscht in Drive nichts, in keinem Meilenstein. Dubletten und Fremddokumente werden verschoben, der Papierkorb wird nicht geleert, Umbenennungen erhalten die Datei-IDs.
2. Jede Schreibaktion auf bestehende Drive-Strukturen läuft zuerst als Dry-Run mit Export, dann nach schriftlicher Freigabe. Das gilt für den Ordnerabgleich (M4, M14) und für die Verschiebung von Bestandsdateien (M6). Uploads von Sachbearbeitern und Ablagen neuer Dokumente sind Regelbetrieb und brauchen keine Einzelfreigabe, laufen aber über dieselbe Benennungsfunktion und dieselbe Idempotenzprüfung (B-45).
3. Vor jedem Deployment mit Migration erzeugt `scripts/deploy.sh` einen Dump. Jede Migration hat eine Rückwärtsrichtung; wo eine Rückwärtsmigration Daten verlieren würde, ist der Rückweg der Restore aus dem Dump, und das steht in der Migrationsdatei als Kommentar.
4. Image-Rollback ist ein Befehl: `scripts/rollback.sh <tag>` setzt die Compose-Dienste auf den vorherigen Image-Tag. Der Tag des zuletzt laufenden Images steht in `/srv/objektakte/deploy/current`.
5. Jede Umbenennung und Verschiebung in Drive wird mit Elternordner, Dateizahl und ID-Hash vorher und nachher in `drive_sync_actions` protokolliert. Bei Abweichung bricht der Lauf ab, weitere Schreibaktionen sind gesperrt, bis ein Admin den Fall schließt. Die Rückführung (`drive:undo-rename <action_id>` für Umbenennungen, `drive:undo-move <action_id>` für Verschiebungen) nutzt dieselben Datensätze.
6. Vor M14 schreibt die Anwendung ausschließlich in das Test-Wurzelverzeichnis (V-08). Der produktive Wurzelpfad wird bis dahin nur gelesen.
7. Secrets liegen nur unter `/srv/objektakte/secrets/` und werden weder in das Repository noch in Logs, Protokolle oder Berichte übernommen. Der Log-Filter aus M1 maskiert Bank- und Ausweisnummernmuster in allen Diensten.
8. Sicherungen laufen täglich verschlüsselt. Vor jedem Sammellauf (M13, M14) und vor jedem Prüfpunkt wird zusätzlich eine Sicherung erzeugt und eine Restore-Probe protokolliert.
9. Tests, Fixtures, Bildschirmfotos und Dokumentation enthalten ausschließlich synthetische Daten. Das PII-Prüfskript läuft in der CI und blockiert bei Treffern.
10. Irreversible Läufe (Löschläufe nach F28, Sammellauf M14) laufen nur mit dokumentierter Freigabe und mit Wartefrist oder zweitem Admin.

### 6.2 Regeln je Meilenstein

| Meilenstein | Änderung mit Risiko | Sicherung vorher | Rückweg | Datensicherheit |
|---|---|---|---|---|
| M0 | Temporärer Messcontainer auf dem VPS | Keine, der Container hat keine Volumes außer dem Messverzeichnis | `docker rm`, Messverzeichnis entfernen; keine Änderung an Host, Traefik oder Firewall | Nur synthetischer Korpus; anonymisierte Seiten (V-16) nach der Messung gelöscht, Löschung im Messprotokoll vermerkt |
| M1 | Host-Härtung (Firewall, SSH), Compose-Stack, DB-Konten, Traefik-Router | Kopie der bestehenden Traefik- und SSH-Konfiguration nach `/srv/objektakte/deploy/host-before/`; Snapshot beim Hoster, falls im Tarif enthalten (V-24) | Firewall-Regeln einzeln rücknehmbar, zweite SSH-Sitzung bleibt während der Umstellung offen; `docker compose down` entfernt nichts unter `/srv/objektakte/` | Noch keine Nutzdaten; Secrets mit Dateirechten nur für den Deploy-Nutzer; Root-Passwort und Schlüssel im Passwortmanager (V-21) |
| M2 | Schema-Migrationen, Seeds | Dump durch `deploy.sh` | Rückwärtsmigration; Seeds sind Upserts nach Code und mehrfach ausführbar | Nur synthetische Daten |
| M3 | Import in `owners`, `units`, `owner_unit_assignments` | Dump; Importdatei bleibt unter `imports/<batch_id>/` | Rücknahme je `import_batches`-Zeile: entfernt nur Zeilen mit Herkunft `import:<batch_id>` in `field_provenance`; gesperrt, sobald Review-Entscheidungen oder Ablagen darauf verweisen | IBAN nur als `iban_last4` und `iban_hash` (B-18); Importdateien im Backup und mit Aufbewahrung nach F26; reale Importdateien erst nach F11 |
| M4 | OAuth-Verbindung, Ordneranlage und Umbenennung im Test-Wurzelverzeichnis | Inventar des Test-Wurzelverzeichnisses (Dateizahl, ID-Hash) | `drive:undo-rename` je Aktion; Token widerrufen über das Google-Konto | Token verschlüsselt mit `TOKEN_KEY`, lesbar nur für `worker-io`; Audit `drive.authorize`, `drive.token_refresh` |
| M5 | Downloads nach `work/`, OCR-Cache, Seitenbilder | Keine Drive-Schreibaktion in diesem Meilenstein | `work/` und `previews/` sind verlustfrei löschbar, Quelle bleibt Drive; Jobs zurücksetzen über den Sweeper | Maskierung vor jeder Persistierung (B-11); Seitenbilder nur rechtegeprüft (B-15); OCR-Cache im Backup |
| M6 | Ablage neuer Dokumente, Verschiebung von Bestandsdateien | Pipeline-Dry-Run je Objekt mit Verschiebungsplan, Freigabe je Objekt; Dump | Jede Bewegung in `drive_sync_actions` mit altem Elternordner, `drive:undo-move` verschiebt zurück; Bestandsdateien unter Schwelle werden nie bewegt (B-10) | Modelle unter `models/` versioniert, Training nur aus `training_samples` und `review_decisions`; keine Personennamen in Regeln |
| M7 | Review-Entscheidungen, Massenbearbeitung | Vorschau ohne Schreibwirkung vor jedem Massenvorgang; Obergrenze je Vorgang (A-36) | `review_decisions` ist anfügend, Korrektur erzeugt eine neue Entscheidung; Rückgängig je `bulk_key`, solange die Datei danach nicht erneut bewegt wurde | Rechteprüfung je Anzeige und Download (B-19); Audit vollständig; keine Klartext-IBAN in der Oberfläche |
| M8 | Aufrufe externer KI | Provider bleiben deaktiviert, bis F17 und F19 beantwortet und V-10 bis V-13 erfüllt sind | `ai.providers.<p>.enabled = false` wirkt sofort ohne Deployment; Kostenlimit stoppt Aufrufe | Maskierung und Datenminimierung im Interface (B-11); Prompts nicht gespeichert (B-07); Aufrufe mit `blocked_by_mask_check` protokolliert |
| M9 | Import aus PDF-Scans und Mieterlisten | Wie M3; Importvorschläge nur mit Bestätigung je Zeile | Wie M3 | Wie M3; externe KI zur Strukturierung nur nach F19 |
| M10 | Nachforderungsschreiben | Entwurfsstatus, kein Versand durch die Anwendung; Freigabe nur durch Admin | Zurückziehen mit Status `withdrawn` und Audit; Prüfpunktänderungen ändern bestehende Schreiben nicht (Version je Schreiben) | Empfängerdaten minimal; PDF nur mit Pflichtangaben der Hausverwaltung Müller GmbH aus dem Skill `hvm-ci`; keine Bankverbindung im Schreiben, sofern nicht fachlich nötig |
| M11 | Listen-Dateien im Objektordner | Vorversion lokal unter `lists/<object>/<timestamp>/`, Aufbewahrung nach F26 | Erneute Erzeugung aus der Datenbank jederzeit; Rückführung verschobener Listen nach F24 | Listen enthalten Personendaten: Zugriff über die Drive-Rechte des Objektordners, IBAN maskiert, Kautionsdaten nur in der Mieterliste |
| M12 | Suche, Reporting, Statusseite | Keine Schreibwirkung auf Fachdaten | Volltextindex jederzeit neu aufbaubar (`search:rebuild`); Funktionen per Konfiguration abschaltbar | Suche liefert nur rechtegeprüfte Treffer; Exporte protokolliert |
| M13 | Performance-Test mit 10.000 Seiten im Test-Wurzelverzeichnis | Sicherung vorher; Testobjekt gekennzeichnet (`objects.is_test = 1`) | Testobjekt und Jobs löschen (Admin-Kommando `objects:purge-test`), Test-Wurzelverzeichnis leert der Auftraggeber | Synthetischer Korpus; anonymisierte Seiten nach F18 mit protokollierter Löschung; Messdaten ohne Personendaten |
| M14 | Sammellauf auf allen Objektordnern des Bestands | Dry-Run-Export, Freigabe V-19, Dump, Inventar je Objektordner (Dateizahl, ID-Hash) | Rückbenennung aus `drive_sync_actions`, Abbruch bei Abweichung, nichts gelöscht; zweiter Lauf als Nachweis | Export enthält Ordnernamen mit Eigentümernachnamen: Ablage unter `exports/drive-sync/` im Backup, Weitergabe nur an die Geschäftsführung |
| M15 | Produktivstart, Nutzeranlage | Restore-Probe aus der jüngsten Sicherung | `rollback.sh` auf das letzte abgenommene Image | Bildschirmfotos ohne Realdaten; Mitarbeiter über die Protokollierung informiert (V-25); Nutzer nur aus der Nutzerliste (V-20) |

### 6.3 Nachweise

Jeder Rückweg aus 6.2 wird einmal geübt, bevor der Meilenstein abgenommen wird: Rückwärtsmigration in M2, Import-Rücknahme in M3, `drive:undo-rename` in M4, `drive:undo-move` in M6, `rollback.sh` in M1 und erneut in M15, Restore in M1, P1, M13 und M15. Das Ergebnis steht im Meilensteinbericht.

---

## 7. Berichtsrhythmus

| Bericht | Wann | Inhalt | Form und Ablage |
|---|---|---|---|
| Schrittbericht | Nach jedem Arbeitsschritt aus Abschnitt 2 | Schrittnummer (zum Beispiel M5.6), was gebaut wurde, ausgeführter Testlauf mit Kommando und Ergebnis (Anzahl Tests, grün oder rot), Abweichungen, nächster Schritt; höchstens zehn Zeilen | Commit-Nachricht und Eintrag in `docs/plan/status.md` |
| Meilensteinbericht | Bei Abschluss jedes Meilensteins | Abnahmekriterien mit Nachweis, Messwerte, Aufwand Plan gegen Ist in PT, geprüfte oder ersetzte Annahmen (A-Nummern), neue oder erledigte Fragen (F-Nummern), geänderte Risiken (R-Nummern), Rollback-Nachweis nach 6.3 | `docs/plan/berichte/M<nr>.md`, Abnahmegespräch 30 bis 60 Minuten, Freigabe durch den Auftraggeber per Nummer |
| Prüfpunktbericht | P0, P1, P2 | Entscheidungsvorlage: Messwerte, Rechenweg, Optionen mit Aufwand und Kosten, Empfehlung, benötigte Antwort mit Datum | `docs/plan/berichte/P<nr>.md`, Termin mit der Geschäftsführung |
| Wochennotiz | Jeden Freitag | Ampel je aktivem Meilenstein, PT verbraucht gegen Plan, blockierende Fragen mit Alter in Tagen, Plan der nächsten Woche; höchstens eine Seite | E-Mail an die Geschäftsführung, Kopie in `docs/plan/status.md` |
| Sofortmeldung | Ohne Verzug | Abweichung der Dateizahl nach einer Drive-Aktion, Sicherheitsvorfall, erreichtes Kostenlimit, Tokenverlust, Verzögerung eines Meilensteins über 20 Prozent der Schätzung | E-Mail oder Anruf, Nachtrag in `docs/plan/status.md` |

Antworten des Auftraggebers werden mit Nummer erwartet, zum Beispiel "F4: (a)", "M2: freigegeben", "B-23: abgelehnt, stattdessen typabhängig ab sofort". Jede Antwort wird in `docs/plan/entscheidungen.md` mit Datum festgehalten und ist danach für den Bau bindend.

---

## Anhang A: Annahmen

Alle Planungsgrößen dieses Plans, die nicht aus CR oder Befund stammen. Jede Annahme wird durch den genannten Messwert oder die genannte Antwort ersetzt; der Meilensteinbericht dokumentiert die Ersetzung.

Hinweis zur Abstimmung mit `docs/architektur.md`: Einige Startwerte in dieser Tabelle weichen von den Annahmen A1 bis A41 in `docs/architektur.md` Abschnitt 13.1 ab (unter anderem A-03 gegen A1, A-10 gegen A14, A-12 gegen A19, A-17 gegen A9, A-18 gegen A11, A-19 gegen A12, A-22 gegen A15, A-30 gegen A35, A-34 gegen A27, A-36 gegen A26, A-41 gegen A8 und A9). Für Seeds und `.env` gilt das Konfigurationsregister in `docs/architektur.md` (Abschnitt 5.5 und 13.1, laut README einzige Quelle für Konfigurationsschlüssel); die Werte hier dienen dem Rechenweg und der Aufwandsschätzung. Beide Sätze werden in M2 mit dem Bezeichnerregister auf einen Satz zusammengeführt. Bis dahin sind alle genannten Zahlen Annahmen ohne Messgrundlage.

| Nr. | Annahme | Startwert | Verifikation | Verwendet in |
|---|---|---|---|---|
| A-01 | Mehraufwand der Auflagen aus B und C gegenüber Vorschlag A | 4 bis 7 PT | Ist-Zeiten nach M1 und M5 gegen die Planspannen | 1.2, 2.0 |
| A-02 | Konsolidierungsaufwand aus den vier Kritiken (Bezeichnerregister, Ergänzungsmigration, Umschreiben von E) | 6 bis 10 PT | Ist-Zeiten nach M2 | 1.2, 2.0 |
| A-03 | OCR-Zeit je Scan-Seite je Prozess (`t_ocr`) | 3,5 s mit Standarddaten (Spanne 2 bis 5 s), `tessdata_fast` etwa Faktor 2 schneller (rund 1,75 s) | Messung in M0, Kontrolle in M13 | 2.1, 2.8 |
| A-04 | Zusammensetzung des Testkorpus | 40 Prozent Seiten mit Textebene (Spanne 30 bis 70), 8 Seiten je Dokument im Mittel, 1.250 Dokumente je 10.000 Seiten | Inventur je Objekt nach der Textebenen-Vorprüfung (`object_progress`) | 2.7, 2.8, 2.17 |
| A-05 | Zeit je Digitalseite (`t_txt`) | 0,1 s | Messung in M0 | 2.8 |
| A-06 | Skalierungseffizienz (`e`) bei P parallelen OCR-Prozessen | 0,85 | Messung in M0 mit P = 1, 2 und C minus 1 | 2.8 |
| A-07 | Nachlauf nach der letzten OCR-Seite (Klassifikation, Ablage, Listen) | 10 bis 15 min | Messung in M13 | 2.8 |
| A-08 | Zeit je Drive-Schreibaktion (Upload oder Verschiebung) | 1 s | Messung in M4 und M13 | 2.8 |
| A-09 | Chunk-Größe der OCR-Blöcke (`ocr.chunk_pages`) | 20 Seiten | Messung in M0 und M13: Laufzeit je Chunk gegen `visibility_timeout` | 2.7 |
| A-10 | Größenlimit je Datei für den Download (`documents.max_download_bytes`) | 500 MB | Größte Datei aus der Inventur in M14; Anpassung als Konfiguration | 2.7 |
| A-11 | Kriterium "Seite hat Textebene" (E 1.3) | mindestens 50 extrahierbare Zeichen je Seite, Anteil alphanumerischer Zeichen über 60 Prozent, Seite besteht nicht nur aus einem seitenfüllenden Bild | Stichprobe von 50 Seiten in M5 gegen manuelle Bewertung | 2.7 |
| A-12 | Celery-Fristen: `visibility_timeout`, `jobs.stale_minutes.<job_type>`, `max_attempts` | 3.600 s; ocr_chunk 10 min, classify 3, classify_ai 5, file_to_drive 5, übrige Jobtypen 15; 3 Versuche | Längste gemessene Chunk-Laufzeit in M0 und M13 mal 3 muss unter `visibility_timeout` liegen | 2.7 |
| A-13 | Heartbeat der Jobs | je Seite bzw. alle 30 s | Sweeper-Test in M5 | 2.7 |
| A-14 | Seitenbilder | JPEG, 1.200 Pixel lange Kante, rund 150 KB je Seite | Messung Speicherbedarf und Renderzeit in M5 | 2.7, 2.10 |
| A-15 | Parallel verarbeitete Objekte (`processing.max_parallel_objects`) | 1 | Entscheidung F18; Messung in M13 | 2.7 |
| A-16 | Eingabe des lokalen Klassifikators | erste 3 Seiten plus letzte Seite | Trefferquote auf dem Testkatalog E 8 in M6 mit M = 2, 3, 5 | 2.9 |
| A-17 | Prozesse im Container `worker-nlp` (`NLP_CONCURRENCY`) | 1; 2 ab C größer oder gleich 6 | RAM-Messung je Prozess in M6 | 2.9 |
| A-18 | Startwerte Klassifikation: Gewichte `training_samples` (Regel 0,6, synthetisch 0,3, KI 0,5, Review 1,0), `stage2_min_samples_per_class` 15, `threshold_auto_file` 0,90, `threshold_stage3_call` 0,90, `threshold_stage3_override` 0,90, `stage2_conflict_p` 0,90, `bonus_agree` 0,05, `malus_disagree` 0,25, `gap_factor` 0,50, `ai_sample_pct` 10, `stage3_max_tokens` 3.000 | wie genannt | Kalibrierung nach den ersten drei realen Objekten gegen den Zielwert aus F17 (Anteil später korrigierter automatischer Ablagen) | 2.9, 2.12 |
| A-19 | Schwellen des unscharfen Namensabgleichs (rapidfuzz) | sicher ab 90, Kandidat 78 bis 89, darunter kein Treffer | Testfälle H 6.6 und Stichprobe realer Importe in M3 und M9 | 2.5, 2.9 |
| A-20 | Konfidenzbänder des Namenssplitters (`import.name_split_thresholds`) | hoch ab 0,90, mittel 0,60 bis 0,89, niedrig darunter | elf Testfälle H 6.4 und Stichprobe in M3 | 2.5 |
| A-21 | Laufzeit Übernahme von 1.000 Importzeilen | unter 2 min | Messung in M3 | 2.5 |
| A-22 | Backoff bei Drive-Fehlern | exponentiell ab 1 s, Faktor 2, Obergrenze 64 s, mit Zufallsanteil, 8 Versuche | Fehlerzähler in M4 und M13 | 2.6 |
| A-23 | Ratenbegrenzung Drive-Schreibzugriffe je Prozess | 5 Anfragen je Sekunde | Quota-Anzeige im Google-Cloud-Projekt, Zähler für Statuscode 429 in M13 | 2.6 |
| A-24 | `pageSize` der Drive-Listenabfrage | API-Höchstwert, laut externer Dokumentation derzeit 1.000, zum Umsetzungszeitpunkt prüfen | API-Referenz und Testabfrage in M4 | 2.6 |
| A-25 | Wirkung von `nice` und `ionice` auf dem VPS | wirkt nur, wenn der I/O-Scheduler Prioritäten unterstützt | Auslesen von `/sys/block/*/queue/scheduler` in M0 | 2.1, 2.7 |
| A-26 | Nutzer-ID des nicht privilegierten Container-Nutzers | 10001 | Prüfung auf Kollision mit Host-Nutzern in M1 | 2.3 |
| A-27 | Sitzungs- und Passwortparameter | 8 h Leerlauf, 12 h absolut, Passwortmindestlänge 12 Zeichen | Antwort F28 | 2.3 |
| A-28 | Prozess-Cache der Ordner-IDs in Redis | Ablauf 10 min, Ungültigmachung bei jeder eigenen Schreibaktion | Fehlerzähler "Ordner nicht gefunden" in M4 | 2.6 |
| A-29 | Lastprofil Review Center | 50 Zeilen je Seite, 5 gleichzeitige Nutzer, p95 unter 2 s | Locust oder k6 in M7 und M13 | 2.10 |
| A-30 | Reporting- und Alarmschwellen | Alterswarnung offener Fälle 10 Arbeitstage; Alarmschwellen nach `docs/betrieb.md` 6.5: Worker-Heartbeat älter als 10 min bei nicht leerer Queue, freier Platz unter `DISK_RESERVE_GB`, Token-Refresh zweimal fehlgeschlagen, Fallback länger als 1 h, Monatsbudget 80 Prozent, Zertifikat in weniger als 14 Tagen | Antwort F22 und F27; Betrieb nach P2 | 2.16 |
| A-31 | Drive-API liefert `sha256Checksum` in den Dateimetadaten | ja, zum Umsetzungszeitpunkt prüfen | Testabfrage in M4; sonst `drive_md5` als Rückfall | 2.6 |
| A-32 | KI-Zeitlimit und Eingabegröße | `timeout_s` 30, `ai.max_input_tokens` 3.000 | Antwort F17; Messung Antwortzeiten in M8 | 2.12 |
| A-33 | Schwelle der OCR-Wortkonfidenz für unsichere Zellen im PDF-Import | 70 | Stichprobe in M9 | 2.13 |
| A-34 | Parameter der Vollständigkeitsprüfung | 3 abgeschlossene Jahre plus laufendes, Einzelabrechnung fällig ab 30.06. des Folgejahres, `mostly_complete_pct` 90; Bewertungslauf unter 5 s bei 100 Einheiten | Antwort F22; Messung in M10 | 2.14 |
| A-35 | Tabellenschrift der PDF-Listen | 8 pt | Antwort F21; Sichtprüfung des Layouts in M11 | 2.15 |
| A-36 | Massen- und Entprellgrößen | höchstens 500 Fälle je Massenvorgang; `lists.debounce_seconds` 60 | Messung in M7 und M11 | 2.10, 2.15 |
| A-37 | Speicherbedarf und Aufbewahrung der Seitenbilder | rund 1,5 GB je 10.000 Seiten; `previews.retention_days_after_resolve` 90 nach Erledigung | Messung in M5 und M13; Antwort F26 | 2.7, Abschnitt 3 (R-02) |
| A-38 | Speicherbedarf des OCR-Cache | rund 2 GB je 10.000 Seiten | Messung in M13 | Abschnitt 3 (R-02) |
| A-39 | Einweisungsaufwand | 2 Stunden je Anwender | Ist-Zeit in M15 | 2.19 |
| A-40 | Nutzerzahl | 2 bis 5 Sachbearbeiter, 2 Admin-Konten | Nutzerliste V-20 | 2.10, F15 |
| A-41 | RAM-Planungsgrößen | 0,75 GiB je OCR-Prozess plus 0,5 GiB Sockel, 0,8 GiB je NLP-Prozess plus 0,3 GiB Sockel, 1 GiB für `web`, 0,5 GiB für `worker-io`, 0,625 GiB für `redis`, `beat` und `backup`, zuzüglich Datenbankpuffer (Formel in `docs/architektur.md` 4.4) | Messung `m_ocr` in M0, `m_nlp` in M6, Gesamtlast in M13 | 2.1, 2.8, Abschnitt 3 (R-02) |
| A-42 | Datenvolumen und Netz | rund 0,5 MB je Seite, Download von 10.000 Seiten in unter 20 min | Lesetest in M0, Messung in M13 | 2.8 |
| A-43 | Traefik-Umgebung | ein Traefik-Container mit Docker-Provider, Konfiguration über Labels, ein externes Docker-Netz | Auslesen in M0 (Name, Hauptversion, Netze, Entrypoints, Zertifikatsresolver), nur lesend | 2.1, Abschnitt 3 (R-24) |

---

## Anhang B: Konsolidierungsbeschlüsse

Jedes Finding der vier Kritiken (K01 bis K27, K2-01 bis K2-26, K3-01 bis K3-21, K4-01 bis K4-29) ist genau einer Zeile zugeordnet. "Festlegung" ist eine Entscheidung, die der Entwickler im Rahmen des CR treffen kann; wo eine Frage genannt ist, entscheidet der Auftraggeber, und die Zeile nennt den Zustand bis zur Antwort.

| Nr. | Festlegung | Findings | Umsetzung |
|---|---|---|---|
| B-01 | Fachentwurf D ist die einzige Bezeichnerquelle für Tabellen, Spalten, Statuswerte und Konfigurationsschlüssel. E wird auf D umgeschrieben (Anhang "Bezeichner E zu D"), F, G und H erhalten Verweise. Das Register `docs/architektur/bezeichnerregister.md` ist ab M2 für alle Entwürfe und den Code bindend; die CI prüft neue Bezeichner gegen das Register. | K01, K2-01, K3-01, K4-02 | M2.1, M2.2 |
| B-02 | Was E, F und H über D hinaus brauchen, wird als nummerierte Ergänzungsmigration zu D aufgenommen (Anhang D), nicht als paralleles Schema: Tabellen `classification_rules`, `classifier_models`, `training_samples`, `completeness_checks`, `document_requests`, `request_text_blocks`, `review_saved_filters`, `import_column_profiles`, `object_progress`; die Spalten- und CHECK-Erweiterungen aus Anhang D; alle Konfigurationsschlüssel aus E, F, H im Register D 4.2. | K01, K2-01, K2-23, K3-10, K4-02, K4-17, K4-22 | M2.3 |
| B-03 | Ein Idempotenzschlüssel je Jobtyp mit fester Form: `discover:<object_id>:<drive_file_id>:<md5>`, `hash:<object_id>:<drive_file_id>`, `ocr:<object_id>:<sha256>:<chunk_no>`, `extract_entities:<object_id>:<sha256>`, `classify:<object_id>:<sha256>`, `file_to_drive:<object_id>:<sha256>`. Der Schlüssel ist eindeutig in `processing_jobs`; ein zweiter Lauf erzeugt keinen zweiten Job. | K4-03 | M5.3 |
| B-04 | Chunking ist eine Konstruktionsentscheidung: OCR-pflichtige Seiten werden in Blöcke von K Seiten (A-09) geteilt, je Block ein `processing_jobs`-Eintrag mit `chunk_no` im `payload`; keine eigene Tabelle `ocr_chunks`. Der Merge schreibt Seitentexte je Originalseite in `document_pages`. | K2-01, K4-03 | M5.6 |
| B-05 | Stale-Fristen je Jobtyp in `app_settings` unter `jobs.stale_minutes.<job_type>` (A-12); der Einheitswert `jobs.stale_running_minutes` aus D entfällt. | K16, K2-17 | M5.10 |
| B-06 | Eine Konfigurationsquelle für die KI: `.env` und Secrets enthalten nur API-Schlüssel und Basis-URL. Modell, Endpunkt, Region, Timeout, Kostenlimit je Provider und Objekt, Reihenfolge Primär und Fallback liegen in `app_settings` (`ai.provider_order`, `ai.providers.<p>.*`, `ai.max_input_tokens`, `ai.wall_budget_s`, `ai.store_masked_prompts`, optional `ai.monthly_budget_eur.<p>`). Der Monatsdeckel ist ein Vorschlag aus G, nicht im CR. | K02, K2-10, K3-03, K4-05 | M2.4 (Seeds), M8.1 |
| B-07 | `ai_calls` nach D 11.1 mit den Spalten aus D; Prompts werden nicht gespeichert, nur `prompt_hash` und `masked_entities_count`. `ai.store_masked_prompts = true` ist eine Diagnoseoption mit Aufbewahrung über `retention_policies`, Standard `false`. Zusätzliche Statuswerte `schema_error`, `provider_error`, `blocked_by_mask_check` (Anhang D). | K07, K2-14, K4-06 | M8.6 |
| B-08 | Ein Schlüsselsatz für Schwellwerte unter `classification.*` (Startwerte A-18). Ablageschwelle und Verschiebeschwelle sind derselbe Wert `threshold_auto_file`; die D-Schlüssel `stage3_threshold` und `auto_file_threshold` werden in `threshold_stage3_call` und `threshold_auto_file` umbenannt. Alle Werte sind zur Laufzeit änderbar und in der Admin-Doku beschrieben. | K2-03, K3-02, K4-01 | M2.4, M6.5 |
| B-09 | Entscheidungstabelle für Fallarten: der fachliche Grund bestimmt `review_cases.case_type`: mehrdeutiger Eigentümer `owner_candidates`; fehlendes Pflichtmetadatum `missing_metadata`; Bestandsdatei unter Schwelle `move_proposal`; Ordnerstruktur `drive_structure` mit Untertyp in `case_subtype` (`candidate_in_trash`, `legacy_and_target_both_exist`, `multiple_legacy_folders`, `multiple_folders_same_name`); Importzeile `import_row_uncertain`; erkannte Liste `import_candidate`; `unclear` nur ohne fachlichen Grund. Ablage in 06_Sonstiges ist ein Attribut (`misc_subfolder_id`), keine Fallart. Der physische Ort bei `owner_candidates` und `missing_metadata` folgt F8. | K05, K11, K2-07, K3-12, K4-13 | M6.7, M7.4 |
| B-10 | Bestandsdateien (`source = drive_existing`) unter Schwelle werden nie verschoben (CR 9.4), sondern erhalten `move_proposal`; Ablage in 06_Sonstiges erfolgt nur für Uploads und für sichere 06-Entscheidungen (Dublette, Fremdobjekt). Der Dry-Run ist zweistufig: Ordnerabgleich (Anlagen, Umbenennungen) und Pipeline-Dry-Run (geplante Verschiebungen je Datei), beide mit Export. Der Sonderfall "sichere Kategorie 05, unklare Zuordnung" ist Frage F8. | K2-04, K2-05, K2-08, K4-04 | M6.8, M6.11 |
| B-11 | Maskierung erfasst Bankdaten (IBAN, BIC, Kontonummer) und Ausweisdaten (Personalausweis- und Reisepassnummern, Ausweiskopien) und liefert strukturierte Treffer (IBAN-HMAC, letzte vier Stellen, Position) für den Stammdatenabgleich. Sie läuft als erster Schritt nach der Texterkennung, vor jeder Persistierung, und zusätzlich im Provider-Interface unmittelbar vor dem Senden. Ein Dokument mit erkannter Ausweiskopie geht nicht an Stufe 3 (Ausgestaltung F19). | K06, K2-02, K2-09 | M5.8, M8.3 |
| B-12 | Regeln, Klassifikator und KI-Antwortschema arbeiten mit Codes (`category_code`, `subfolder_code`, `document_type_code`, `management_type` als Code), nie mit Ordnernamen oder Anzeigetexten. Ordnernamen kommen ausschließlich aus den Seeds. | K19, K2-19, K4-11, K4-12 | M6.1, M8.2 |
| B-13 | Queues `ocr`, `classify`, `ai`, `io`, `lists`; Container `worker` (ocr), `worker-nlp` (classify), `worker-io` (ai, io, lists); Nebenläufigkeit `ai` 3, `io` mit Sperre je Objekt (Nebenläufigkeit 1 je Objekt), `lists` 1. Die Queue-Tabelle steht in der Compose-Datei und im Register. Container-Katalog ist Frage F2. | K2-06, K3-06, K4-07 | M1.3, M5.1 |
| B-14 | Verzeichnisse unter `/srv/objektakte/`: `db`, `redis`, `transit`, `work`, `ocr-cache`, `previews`, `models`, `lists`, `imports`, `requests`, `exports`, `backup`, `secrets`, `deploy`. Backup umfasst `db` (Dump), `transit`, `ocr-cache`, `models`, `lists`, `imports`, `requests`, `exports`; nicht `work`, `previews`, `redis`. Downloads liegen unter `work/`, Uploads unter `transit/`. | K03, K2-12, K3-05, K4-10 | M1.4, M1.10 |
| B-15 | Seitenbilder werden nicht als statische Datei ausgeliefert, sondern über eine rechtegeprüfte Route (`owner_files.read` bzw. `tenant_files.read`), `Cache-Control: private, no-store`, kein erratbarer Pfad. Der Zugriff auf Vorschauen der Kategorie 05 gilt als Aktenzugriff. | K2-13, K3-17 | M7.2 |
| B-16 | OAuth nach G: Redirect-URIs `/auth/google/callback` und `/auth/google/login/callback`, `include_granted_scopes=false`, PKCE, Secret-Namen und Audit-Aktionen `drive.authorize`, `drive.token_refresh` aus G. Nachweis über 8 Tage durch täglichen erzwungenen Refresh (F) plus stündlichen Lesetest (G). Die App legt der Auftraggeber an (F13). | K12, K2-11, K3-08, K4-08 | M4.1 |
| B-17 | Rollencodes `admin` und `sachbearbeiter` (D-Kommentar `clerk` entfällt). Eine Rechtematrix in G 12.2, ergänzt um `review.dismiss_object_case` nur für Admin. Umfang der Sichtbarkeit je Objekt ist Frage F15. | K13, K3-13, K4-09 | M1.7, M7.9 |
| B-18 | IBAN restriktiv: gespeichert werden `iban_last4` und `iban_hash`; `security.store_full_iban = false`, `security.iban_decrypt_roles = []`. Klartext für keine Rolle in der Oberfläche. Vollständige verschlüsselte Speicherung nur nach F16. | K06, K2-09 | M2.4, M3.4 |
| B-19 | Kein eigenes `access_log`: Anzeigen und Downloads von Dokumenten der Kategorie 05 werden als `document.view` und `document.download` in `audit_events` protokolliert (geht über den CR hinaus, Bestätigung F15). | K2-20, K4-15 | M7.2 |
| B-20 | `owners.short_name` ist der Firmenkurzname; F verwendet `short_name` statt `company_short_name`. | K14 | M2.7 |
| B-21 | `document_types` wird vollständig aus CR 5 geseedet (rund 45 Typen, stabile Codes, Unterordner, `requires_period`, `requires_owner`), ergänzt um die Objekt- und Buchhaltungstypen aus CR 6 und die Vorschlagsliste für 01 bis 04 (F10). | K09 | M2.4 |
| B-22 | Seeds liegen unter `db/seeds/` als eigene Dateien, die die Migration einliest; `db/migrations/` enthält nur Schema. Der Alt-Alias `drive.legacy_folder_aliases` ist die einzige Fundstelle der Altbezeichnung des Auffangordners in Code, Seeds und verbindlicher Dokumentation. `scripts/check_no_legacy_names.sh` prüft `src/`, `tests/`, `docs/` mit den dokumentierten Ausnahmen `db/seeds/`, `docs/anforderungen/` (der CR selbst) und `docs/entwurf/` (historische Arbeitspapiere) (Auslegung F20). | K10, K3-15, K4-25 | M2.4, M15.5 |
| B-23 | Seed `owner_file.unit_prefix_mode = always_we` (CR-Wortlaut) bis zur Antwort F6; die Unit-Tests der Benennungsfunktion decken beide Modi ab, F Testfall 18 wird je Modus geführt. | K04, K3-09, K4-16 | M2.7 |
| B-24 | `owner_files.owner_id` wird als optionale Spalte ergänzt, damit Akten vom Typ `unknown_unit` (`Unbekannte_WE_Nachname`) auf den Eigentümer verweisen; `unassigned` bleibt ohne Verweis. | K08 | M2.3 |
| B-25 | Die Anzahl der OCR-Prozesse ist ausschließlich `.env`-Startparameter (`OCR_PROCESSES`, Formel C minus 1); der Laufzeitschlüssel `ocr.worker_processes` entfällt, weil Celery die Poolgröße beim Start liest. | K15, K2-15 | M1.3, M5.1 |
| B-26 | Objekte laufen technisch seriell: Redis-Sperre plus Datenbankprüfung erlauben höchstens `processing.max_parallel_objects` (A-15) Läufe im Status `running`; weitere Objekte warten `pending` mit Warteposition. Bestätigung in F18. | K2-25 | M5.10 |
| B-27 | Die Kaltstartphase des Klassifikators (Stufe 2 trägt anfangs nur Erkennung und Abgleich) ist auf der Statusseite sichtbar und als Abweichung von "Stufe 2 immer" dem Auftraggeber in F9 (d) vorgelegt. Trainingsgewichte nach A-18; Nachtraining nachts oder manuell ausgelöst. | K2-26, K4-14 | M6.4 |
| B-28 | Der Wechsel Primär zu Fallback ist ein Integrationstest mit zwei Fake-Providern: Primär liefert Timeout, Fallback antwortet, beide Antworten laufen durch dasselbe Schema; Ausfall beider Anbieter ergibt Ablage 01_Unklar mit Fall. | K3-04 | M8.4 |
| B-29 | Aktion "In anderes Objekt übernehmen" wird ausgearbeitet: neue `documents`-Zeile im Zielobjekt (`source = moved_in`), alte Zeile Status `moved_out` mit Verweis, Drive-Verschiebung über die Benennungsfunktion, Audit beidseitig. | K2-24 | M6.12, M7.4 |
| B-30 | Im Review-Formular sind Eigentümer und Einheit für Zielbereich 05 nicht formal Pflicht; stattdessen gibt es die ausdrücklichen Auswahlen "Einheit unbekannt" und "Eigentümer unbekannt" mit der resultierenden Akte (`Unbekannte_WE_Nachname`, `Unzugeordnet`, nach F7 `WE03_Unbekannt`). | K3-07 | M7.3 |
| B-31 | Datensatzstatus nach H 5.5 (`incomplete` vor `ai_suggested` vor `confirmed`) in `field_provenance`; D 3.10 wird angepasst. KPI Anteil 06_Sonstiges nach D 12.4 (`documents.category_code = '06'`) als Referenzdefinition; zusätzlich ein bereinigter Wert ohne 03_Dubletten und 04_Nicht_objektbezogen. H nutzt die vorhandenen D-Spalten `documents_total`, `documents_misc`, `misc_share_pct`. | K2-23, K3-11, K3-16 | M2.9, M6.13, M12.2 |
| B-32 | Alle Planungsgrößen zu RAM, CPU, Platte und Zeit tragen das Präfix ANNAHME und stehen nur in Anhang A (A-03 bis A-08, A-41, A-42); E und G werden auf diese Werte verwiesen. Compose-Grenzen (`DB_CPUS`, `DB_MEM` und weitere) sind `.env`-Werte mit Formel und Messvorschrift in M0. | K17, K4-18, K4-19 | M0.7, M0.8, M1.3 |
| B-33 | Downloads aus Drive gehen nach `/srv/objektakte/work/<sha256>/`, der Transitbereich `transit/` ist Uploads von Sachbearbeitern vorbehalten; F wird angepasst. | K18 | M5.4 |
| B-34 | Auslösemodell des Listenimports: die Pipeline startet keinen Import. Eine als Eigentümer- oder Mieterliste erkannte Datei in 02_Stammakte erzeugt den Fall `import_candidate` mit vorbelegtem Profil; der Sachbearbeiter startet den Import. E Test T08 wird entsprechend geändert. | K3-20 | M5.5, M9.5 |
| B-35 | Messmethode für "Review Center unter 2 Sekunden": Lastmessung mit Locust oder k6, 5 gleichzeitige Nutzer, Endpunkte Liste, Detail, Eigentümersuche, Entscheidung, Objektansicht, während des 10.000-Seiten-Laufs; Kriterium p95 unter 2 s, p99 unter 4 s als Warnschwelle. Zusätzlich ein Lauf mit 100 Prozent Scan als ungünstigster Fall. | K3-14 | M7.10, M13.3 |
| B-36 | Die Session-URI eines Resumable-Uploads wird in `processing_jobs.payload` persistiert und nach Prozessabsturz fortgesetzt (E), nicht neu gestartet (F). | K2-18 | M4.4 |
| B-37 | Keine Versionsnummern als Fakt: Mindestversionen von MariaDB, MySQL oder Bibliotheken erhalten den Zusatz "zum Umsetzungszeitpunkt prüfen" und werden in M1 gegen die Dokumentation verifiziert. Aussagen über externe Dienste ohne Beleg (Refresh-Token-Grenzen, `pageSize`, `sha256Checksum`) werden als ANNAHME geführt (A-24, A-31) oder gestrichen. | K23, K24, K2-21, K3-18, K4-28 | M1.1, M4.3 |
| B-38 | Die Prosa von E wird mit Umlauten geschrieben; ASCII-Schreibweisen bleiben nur in Bezeichnern und Ordnernamen, die der CR so vorgibt. | K26 | M2.1 (Umschreiben von E) |
| B-39 | Beispiele sind synthetisch: Nachnamen `Mustermann`, `Beispiel`, `Altmuster`, `Neumuster`; Objektnummern aus einem reservierten Bereich (`623`, `624`, `625`, `631`, `700`), niemals reale Nummern des Bestands. `scripts/check_no_pii.sh` vergleicht Repository und Dokumentation gegen die Stammdaten-CSVs, gibt nur Trefferzähler aus und läuft in der CI. | K25, K2-22, K4-26 | M1.1 (CI), M2.1 |
| B-40 | Der Bindestrich mit Leerzeichen in H 6.5 wird umformuliert; das Befundmuster wird als Codeblock zitiert. | K4-27 | M2.1 |
| B-41 | Die Einschübe 0, 0b, A und 3 in der Entscheidungsreihenfolge (Dubletten, Fremdobjekt, Legitimation, Mieterbezug) bleiben als Vorschlag mit Begründung im Plan und sind dem Auftraggeber in F9 (c) vorgelegt; bis zur Antwort wird E 6.1 gebaut. | K27 | M6.6 |
| B-42 | Der Konfigurationsschlüssel heißt `documents.duplicate_owner_documents_in_drive`; die Admin-Oberfläche zeigt den CR-Wortlaut `duplicate_owner_documents_in_drive` als Bezeichnung. `document_owner_links.drive_copy_file_id` und `documents.duplicate_of_document_id` sind in D vorhanden; die Ergänzungsvorschläge aus F 8.1 und H 9 entfallen, die Behauptungen über D werden korrigiert. | K20, K4-21, K4-23 | M2.1, M2.3 |
| B-43 | Zwei Anwendungskonten in der Datenbank: `app_rw` für `web`, `app_worker` für die Worker ohne Schreibrecht auf `owners`, `units`, `owner_unit_assignments`, `tenants`, `leases`, `users`, `app_settings` und nur INSERT auf `audit_events`; dazu `app_migrate`, `app_backup`, `app_ro`. Trigger gegen UPDATE und DELETE auf `audit_events` und `iban_access_log` sind Pflicht; ein Test erwartet, dass UPDATE fehlschlägt. | Gutachten (Ü9, Ü10) | M1.5 |
| B-44 | Zwei Build-Targets aus einem Dockerfile: `web` ohne OCR-Binärdateien, `worker` mit Tesseract `deu`, ocrmypdf, Ghostscript, qpdf, unpaper, pngquant, img2pdf und den Python-Paketen; Paketliste steht im Dockerfile und in `docs/architektur/image.md`. | K2-16, K4-29, Gutachten (Ü7) | M1.2 |
| B-45 | Drive-Schreibidempotenz: SHA-256 als `appProperties` an jeder hochgeladenen Datei, Prüfung im Zielordner vor jedem Upload, `drive_file_id` unmittelbar nach dem Upload persistiert, Resumable-Session fortgesetzt; Integrationstest mit Abbruch zwischen Upload und Persistierung. | Gutachten (Ü21) | M4.4 |
| B-46 | `scripts/deploy.sh --first-run` startet zuerst `db` und `redis`, wartet auf Health, führt Migration und Seeds aus und startet dann die übrigen Dienste; der Aufruf `docker compose exec backup` entfällt im Erstlauf. Der Deployment-Test T1 des CR wird als `deploy.sh --first-run` gelesen (Auslegung F20). | K3-15, K4-20 | M1.11 |
| B-47 | Rest der Bezeichner- und Formkorrekturen: `document_subfolders` ist die einzige Quelle der Unterordner, der Schlüssel `owner_file.subfolders` entfällt (Konfigurierbarkeit über den Katalog); DDL-Kommentar zu `folder_name` lautet `05_Eigentümerakte`; bei Mietverwaltung führt die Einheit (CR 4), also eine Akte je Einheit, E 2.4 wird korrigiert; die Stack-Begründung nach CR 15 liefern die drei Gutachten, G und H verweisen darauf; alle Fragen der Entwürfe sind in Abschnitt 4 gebündelt und in den Entwürfen durch Verweise ersetzt. | K21, K22, K3-19, K3-21, K4-24 | M2.1 |

---

## Anhang C: Übernahmen aus den Vorschlägen B und C

Das Gremium empfiehlt Stack A (Django, HTMX, Celery, Redis, MariaDB). Die drei Gutachten nennen Auflagen aus B und C; dieser Plan übernimmt sie vollständig. Nicht übernommen wurden das SPA-Frontend aus B und die zweite Laufzeitumgebung aus C, weil beides den Betriebs- und Wartungsaufwand auf einem VPS für einen Entwickler erhöht (Gutachten 1 bis 3, Kriterium 1 und 5).

### C.1 Auflagen Ü1 bis Ü22 (Gutachten 3, bestätigt durch Gutachten 1 und 2)

| Nr. | Auflage | Quelle | Meilenstein |
|---|---|---|---|
| Ü1 | OCR-Probelauf auf dem VPS vor M1; misst `t_ocr`, `t_txt`, `m_ocr`, Plattenfaktor, Skalierung bei P = 1, 2, C minus 1 | B, C | M0.6 bis M0.8 |
| Ü2 | Chunking großer Dokumente mit pikepdf, Blockgröße konfigurierbar, Blöcke als eigene Tasks, Merge je Originalseite | C | M5.6 (B-04) |
| Ü3 | `OMP_THREAD_LIMIT=1` im Container `worker` | B, C | M1.3, M5.7 |
| Ü4 | Trennung schwerer und leichter Prozesse: `ocr` ohne Modellimport, `classify` mit kleinem Pool, der Modelle hält | C, B | M1.3, M6.3 (B-13) |
| Ü5 | Textebenen-Vorprüfung je Seite, ocrmypdf startet nicht für rein digitale Dateien | B | M5.5 |
| Ü6 | Skalierungsfaktor (A-06) und Ungünstig-Szenario im Rechenweg | C, B | 2.8 (P1) |
| Ü7 | Zwei Build-Targets `web` und `worker` | B | M1.2 (B-44) |
| Ü8 | Netztrennung: `data` internal, `egress` nur für `web`, `worker`, `worker-io`, `beat`; `db`, `redis`, `backup` ohne Internet; `web` zusätzlich am Traefik-Netz | B | M1.3 |
| Ü9 | Zweites Datenbankkonto für Worker mit Mindestrechten | C | M1.5 (B-43) |
| Ü10 | Trigger gegen UPDATE und DELETE auf `audit_events` | C, B | M1.5 (B-43) |
| Ü11 | Container-Härtung: nicht privilegierter Nutzer, `no-new-privileges`, Read-only-Root für `web` mit tmpfs | C | M1.2, M1.12 |
| Ü12 | Vorschau über vorgerenderte Seitenbilder, Original nur auf Wunsch | B | M5.9, M7.2 (mit B-15 rechtegeprüft statt statisch) |
| Ü13 | Vorschau-Endpunkt ohne Schreibwirkung für die Massenbearbeitung, HTMX-Partial | B | M7.5 |
| Ü14 | Maskierung im Provider-Interface, Circuit Breaker je Provider, Muster für Ausweisnummern | C | M8.3, M8.4 (B-11) |
| Ü15 | Datenminimierung im KI-Request: Kontext ohne Personennamen, Textauszug maskiert und gekürzt | B | M8.2 |
| Ü16 | Aufbewahrungsfristen mit Freigabefeldern; Löschkonzept umfasst `transit`, OCR-Cache, Seitenbilder, Sicherungen | C | M2.4, F31 |
| Ü17 | Zweiter Faktor für alle Rollen unabhängig vom Anmeldeweg | C | M1.7, F14 |
| Ü18 | Statistiktabelle `object_progress` für Fortschrittszähler | C | M2.3, M5.11 |
| Ü19 | Lizenzabschnitt (Ghostscript AGPL, Redis-Lizenzlage mit Valkey als Option, Datenbanktreiber) | C, B | M15.4, F29 |
| Ü20 | Lastmessung mit fünf gleichzeitigen Nutzern (Locust oder k6) während des 10.000-Seiten-Laufs, dazu iostat | C, B | M7.10, M13.2 bis M13.4 (B-35) |
| Ü21 | Drive-Schreibidempotenz | Gutachten 3 | M4.4 (B-45) |
| Ü22 | Host-Härtung als Checkliste mit Abnahme | Gutachten 3 | M1.12 |

### C.2 Weitere Übernahmen aus Gutachten 1 und 2

| Nr. | Übernahme | Quelle | Meilenstein |
|---|---|---|---|
| C-01 | Keyset-Paginierung und benannte Indizes auf `documents(object_id, status)`, `review_cases(status, created_at)`, `owner_unit_assignments(unit_id, valid_from, valid_to)`; getrennte Verbindungspools mit Obergrenzen für `web` und Worker | Gutachten 1 Nr. 4 | M2.2, M7.1 |
| C-02 | Szenariotabelle mit ausgewiesenen Fehlfällen statt eines einzelnen ungünstigsten Falls | Gutachten 1 Nr. 5 | 2.8 (P1) |
| C-03 | Korpusgenerator mit konfigurierbarem Scan-Anteil | Gutachten 1 Nr. 6 | M0.6, M5.12 |
| C-04 | `nice` und `ionice` für OCR-Prozesse, OCR-Temp auf Platte statt tmpfs | Gutachten 1 Nr. 10, Gutachten 2 Nr. 9 | M5.7 |
| C-05 | Volltextparameter: Stoppwortliste aus, Mindesttokenlänge 2 | Gutachten 1 Nr. 14 | M1.5, M12.1 |
| C-06 | Einstellungskatalog als JSON-Schema-Datei, aus dem die Admin-Formulare erzeugt werden | Gutachten 1 Nr. 15 | M1.9 |
| C-07 | Ende-zu-Ende-Test des 40-Dokumente-Falls (Playwright oder gleichwertig) als Abnahmekriterium des Review Centers | Gutachten 2 Nr. 5 | M7.10 |
| C-08 | Gemeinsame Tabellenkomponente (Template-Partial) für Review Center und Importvorschläge | Gutachten 2 Nr. 6 | M3.8, M7.1 |
| C-09 | Massenbearbeitung mit Vorschau im Review-Center-Meilenstein statt später | Gutachten 2, 7.3 Nr. 1 | M7.6 |
| C-10 | Import der Eigentümerlisten vorgezogen: Excel und CSV direkt nach dem Datenmodell, PDF-Import nach der Pipeline | Gutachten 2, 7.3 Nr. 2 | M3, M9 |
| C-11 | Verbindlicher Prüfpunkt Performance-Entscheidung nach der Pipeline, vor Klassifikation und Review Center | Gutachten 2, 7.3 Nr. 3 | P1 |
| C-12 | Speicherformel P mal `m_ocr` plus L mal `m_nlp` plus Grundlast, beide Größen zu messen | Gutachten 2 Nr. 8 | 2.8, A-41 |

---

## Anhang D: Bezeichnerregister (Kurzfassung) und Ergänzungsmigration

Die Kurzfassung nennt die Bezeichner, die in diesem Plan verwendet werden. Die vollständige Fassung entsteht in M2.1 unter `docs/architektur/bezeichnerregister.md` und übernimmt alle Tabellen und Spalten aus Fachentwurf D. Abweichungen vom CR-Wortlaut sind gekennzeichnet.

### D.1 Container und Queues

| Container | Queues | Build-Target | Netze |
|---|---|---|---|
| `web` | keine | `web` | `data`, `egress`, Traefik-Netz |
| `worker` | `ocr` | `worker` | `data`, `egress` |
| `worker-nlp` | `classify` | `worker` | `data` |
| `worker-io` | `ai`, `io`, `lists` | `worker` | `data`, `egress` |
| `beat` | keine (Zeitplan) | `web` | `data`, `egress` |
| `db` | | MariaDB-Image | `data` |
| `redis` | | Redis-Image | `data` |
| `backup` | | eigenes Image | `data`, `egress` nur bei Offsite-Kopie |

Abweichung vom CR (Abschnitt 7): CR nennt `web`, `worker`, `queue`, `db`, optional `classifier`. Zusätzlich `worker-io`, `worker-nlp`, `beat`, `backup`; `queue` heißt `redis` (F2).

### D.2 Verzeichnisse unter `/srv/objektakte/`

`db`, `redis`, `transit` (Uploads), `work` (Downloads und OCR-Temp), `ocr-cache`, `previews`, `models`, `lists`, `imports`, `requests`, `exports`, `backup`, `secrets`, `deploy`. Im Backup: `db` als Dump, `transit`, `ocr-cache`, `models`, `lists`, `imports`, `requests`, `exports`.

### D.3 Statuswerte und Aufzählungen

| Feld | Werte |
|---|---|
| `documents.source` | Werte aus D (darunter `drive_existing`, `upload`, `import`) ergänzt um `moved_in` (B-29) |
| `documents.status` | Werte aus D ergänzt um `hashed`, `moved_out` |
| `processing_jobs.job_type` | `discover`, `hash`, `analyze_pages`, `ocr_chunk`, `merge_pages`, `render_previews`, `extract_entities`, `classify`, `classify_ai`, `decide`, `file_to_drive`, `link_segments`, `generate_lists`, `evaluate_completeness`, `reconcile_drive`, `train_classifier`, `sweep` (Idempotenzschlüssel je Typ in `docs/architektur.md` 5.4) |
| `processing_jobs.status` | `pending`, `running`, `done`, `failed`, `skipped` (aus D) |
| `processing_runs.run_type` | `full`, `incremental`, `reconcile_drive`, `regenerate_lists`, `reclassify`; `dry_run` als Kennzeichen |
| `review_cases.case_type` | `owner_candidates`, `missing_metadata`, `move_proposal`, `drive_structure`, `import_row_uncertain`, `import_candidate`, `duplicate_object_number`, `unclear` |
| `ai_calls.status` | Werte aus D ergänzt um `schema_error`, `provider_error`, `blocked_by_mask_check` |
| `drive_sync_actions.action_type` | Werte aus D ergänzt um `inventory` |
| `list_generations.trigger_kind` | `run`, `review_confirm`, `manual`, `import_commit`, `masterdata_change`, `scheduled` |
| `roles.code` | `admin`, `sachbearbeiter` |
| `owner_files.file_kind` | `unit_owner`, `unknown_unit`, `unassigned` |

### D.4 Konfigurationsgruppen in `app_settings`

`owner_file.*` (Namensmuster, `unit_prefix_mode`, `create_folders_eagerly`), `documents.*` (`duplicate_owner_documents_in_drive`), `classification.*` (Schwellwertsatz B-08), `ai.*` (B-06), `drive.*` (`legacy_folder_aliases`, `object_number_digits_min`, `object_number_digits_max`), `ocr.*` (`chunk_pages`, `digital_min_chars`, `tessdata_variant`), `documents.max_download_bytes`, `processing.max_parallel_objects`, `jobs.stale_minutes.<job_type>`, `previews.retention_days_after_resolve`, `import.*` (`name_split_thresholds`), `lists.*` (`debounce_seconds`), `completeness.*`, `reports.*`, `security.*` (`store_full_iban`, `iban_decrypt_roles`), `retention.*` (Fristen je Kategorie, leer bis F31). Die Formulare entstehen aus `src/apps/config/catalog.json` (C-06).

### D.5 `.env`-Startparameter

Vollständige Vorlage in `docs/betrieb.md` Abschnitt 3.7 (`.env.example`). Auszug: `OCR_PROCESSES` (C minus 1), `NLP_CONCURRENCY`, `IO_CONCURRENCY`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `OMP_THREAD_LIMIT=1`, alle Ressourcenlimits (`WEB_CPUS`, `WEB_MEM`, `WORKER_CPUS`, `WORKER_MEM`, `WORKER_NLP_CPUS`, `WORKER_NLP_MEM`, `WORKER_IO_CPUS`, `WORKER_IO_MEM`, `DB_CPUS`, `DB_MEM`, `REDIS_CPUS`, `REDIS_MEM`, `BEAT_CPUS`, `BEAT_MEM`, `BACKUP_CPUS`, `BACKUP_MEM`), `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` (`app_rw`), `DB_WORKER_USER` (`app_worker`), `DB_MIGRATE_USER`, `DB_BACKUP_USER` (Passwörter nur über `*_FILE`-Variablen aus `/run/secrets/`, keine Verbindungs-URL mit Passwort), `REDIS_URL`, `TRAEFIK_NETWORK`, `TRAEFIK_ENTRYPOINT`, `TRAEFIK_ENTRYPOINT_INSECURE`, `TRAEFIK_REDIRECT_ROUTER`, `TRAEFIK_CERTRESOLVER`, `TRUSTED_PROXY_CIDR`, `APP_DOMAIN`, `APP_BASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI`, `GOOGLE_LOGIN_REDIRECT_URI`, `DRIVE_ACCOUNT_EMAIL`, `BACKUP_CRON`, `BACKUP_RETENTION_DAYS`, `BACKUP_MAX_AGE_HOURS`, `OFFSITE_ENABLED`, `ALERTS_ENABLED`, `DISK_RESERVE_GB`, `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS`, `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`. Secrets als Dateien unter `/srv/objektakte/secrets/` (Namen nach `docs/betrieb.md` 3.8 und `docs/architektur.md` 5.7): `db_root_password`, `db_app_password`, `db_worker_password`, `db_migrate_password`, `db_backup_password`, `db_ro_password`, `redis_password`, `app_secret_key`, `iban_key`, `iban_hmac_key`, `token_key`, `totp_key`, `google_client_secret`, `openai_api_key`, `anthropic_api_key`, `smtp_password`, `readyz_token`, `backup_age_recipient`, `rclone.conf`.

### D.6 Audit-Aktionen (Auszug)

Vollständige Liste in `docs/architektur.md` 5.7. Auszug: `auth.login`, `auth.login_failed`, `auth.denied`, `auth.totp_reset`, `drive.authorize`, `drive.token_refresh`, `drive.rename`, `drive.move`, `drive.create_folder`, `drive.upload`, `document.view`, `document.download` (beide nur bei `security.log_document_views`), `review.assign`, `review.confirm`, `review.reclassify`, `review.merge_duplicate`, `review.split`, `review.defer`, `review.dismiss`, `review.reopen`, `review.bulk_execute`, `review.transfer_object`, `review.dismiss_object_case`, `import.commit`, `import.rollback`, `setting.update`, `owner.update`, `unit.update`, `assignment.create`, `retention.approve`, `deletion.propose`, `deletion.execute`, `list.generate`, `list.export`, `request.approve`, `request.withdraw`.

### D.7 Ergänzungsmigration zu Fachentwurf D

Neue Tabellen: `classification_rules`, `classifier_models`, `training_samples`, `completeness_checks`, `document_requests`, `request_text_blocks`, `review_saved_filters`, `import_column_profiles`, `object_progress`.

Neue Spalten: `documents.drive_md5`, `documents.sha256` optional bis Status `hashed`, `documents.target_drive_node_id`, `documents.classifier_version`, `owner_files.owner_id` optional, `review_cases.snoozed_until`, `drive_sync_actions.id_hash_before`, `drive_sync_actions.id_hash_after`, `completeness_findings.manual_status`, `completeness_findings.manual_reason`, `completeness_findings.manual_by`, `completeness_findings.manual_at`, `objects.expected_unit_count`, `objects.sepa_used`, `objects.special_levies_in_period`, `objects.previous_manager_street`, `objects.previous_manager_house_number`, `objects.previous_manager_postal_code`, `objects.previous_manager_city`, `objects.previous_manager_contact_person`, `objects.previous_manager_reference`, `objects.is_test`, `owner_unit_assignments.balance_at_takeover`, `units.se_managed`, `units.vacancy_confirmed`.

CHECK-Erweiterungen: `documents.status` plus `hashed`, `moved_out`; `documents.source` plus `moved_in`; `processing_jobs.job_type` plus `analyze_pages`, `ocr_chunk`, `merge_pages`, `render_previews`, `decide`, `classify_ai`, `link_segments`, `generate_lists`, `evaluate_completeness`, `reconcile_drive`, `train_classifier`, `sweep`; `review_cases.case_type` plus `missing_metadata`, `drive_structure`, `import_candidate`; `ai_calls.status` plus `schema_error`, `provider_error`, `blocked_by_mask_check`; `drive_sync_actions.action_type` plus `inventory`; `list_generations.trigger_kind` plus `import_commit`, `masterdata_change`, `scheduled`.

Eindeutigkeit: `documents (object_id, drive_file_id)`; `processing_jobs (idempotency_key)`.

Umbenennungen von Schlüsseln: `classification.stage3_threshold` zu `classification.threshold_stage3_call`, `classification.auto_file_threshold` zu `classification.threshold_auto_file`; entfallen: `ocr.worker_processes`, `jobs.stale_running_minutes`, `owner_file.subfolders`.

Jede Zeile dieser Migration verweist auf den Beschluss in Anhang B; die Migration wird in M2.3 geschrieben und ist Teil der Freigabe FG-2.

---

Ende des Umsetzungsplans. Stand 10.09.2026, Entwurf zur Freigabe.
