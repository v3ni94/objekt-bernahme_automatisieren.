# Stack-Vorschlag B: Python-API plus eigenständige Single-Page-Anwendung

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Projekt Objektübernahme, CR-05. Stand 10.09.2026. Faktenbasis: CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md) und Befundakte (befund.md). Alle Planungsgrößen, die nicht aus diesen Quellen stammen, sind mit dem Präfix ANNAHME gekennzeichnet und werden im Projekt gemessen.

## 1. Kurzfassung

Stack B trennt die Anwendung in eine Python-API (FastAPI, SQLAlchemy, Alembic, Celery mit Redis, MariaDB) und eine eigenständige Single-Page-Anwendung in TypeScript (Vue mit Vite), die im selben Image ausgeliefert und unter derselben Domain über den Pfad `/api` mit dem Backend spricht; OCR und Klassifikation laufen in Python-Workern mit Tesseract, ocrmypdf, spaCy und einem scikit-learn-Klassifikator. Kernidee ist, dass alle Fachlogik (Benennungsregel, Abgrenzung, historische Zuordnung, Pipeline) in einem Python-Domänenpaket liegt, das API und Worker gemeinsam nutzen, während das Frontend ausschließlich Bedienoberfläche ist und seine Typen aus dem generierten OpenAPI-Schema bezieht. Stärkste Eigenschaft ist das Review Center: Massenbearbeitung von 40 gleichartigen Dokumenten mit Vorschau der Zielpfade, Seitenbereichszuordnung bei Gesamtabrechnungen und Vorschau ohne Seitenwechsel sind in einer SPA deutlich flüssiger und mit weniger Sonderlösungen umsetzbar als in einer serverseitig gerenderten Oberfläche. Größte Schwäche sind zwei Sprachen und zwei Toolchains für ein Team aus einem Entwickler: Login, Zwei-Faktor, Rollen, Formulare, Fehlerbehandlung und jede Admin-Maske müssen im Frontend von Hand gebaut werden, wofür ein Web-Framework mit Server-Rendering fertige Bausteine hätte. Empfehlung: Stack B nur wählen, wenn der umsetzende Entwickler TypeScript und ein SPA-Framework sicher beherrscht und das Review Center als zentrales Arbeitswerkzeug der Sachbearbeiter mit hohem täglichen Nutzungsanteil gesehen wird; andernfalls ist der Mehraufwand von geschätzt 15 bis 25 Prozent gegenüber einem Server-Rendering-Stack nicht gedeckt.

## 2. Technologie-Stack

### 2.1 Übersicht

| Baustein | Wahl | Begründung, Alternative |
|---|---|---|
| Sprachen | Python 3 (API, Worker, Domäne), TypeScript (Frontend) | Python ist für OCR, NLP, beide KI-SDKs und Drive-Client gesetzt. TypeScript liefert typsichere Oberfläche gegen das generierte API-Schema. Versionen: aktuelle stabile Version zum Umsetzungszeitpunkt prüfen. |
| Web-Framework | FastAPI mit Pydantic | Erzeugt das OpenAPI-Schema automatisch, daraus wird der TypeScript-Client generiert. Pydantic-Modelle werden von API und Worker geteilt. Alternative Django REST Framework: bringt Nutzerverwaltung, Rechte und Admin fertig mit, erzeugt aber ein weniger präzises Schema und eine zweite Oberfläche (Django Admin), was der Idee eines getrennten Frontends widerspricht. Wer die Fertigbausteine höher gewichtet als das Schema, nimmt DRF. |
| Frontend | Vue (Hauptversion 3, aktuelle stabile Version zum Umsetzungszeitpunkt prüfen) mit TypeScript, Vite, Pinia, Vue Router | Single-File-Komponenten und Composition API halten den Umfang für einen Entwickler überschaubar. React mit TypeScript ist gleichwertig; die Entscheidung folgt den Vorkenntnissen des umsetzenden Entwicklers (offene Frage). Tabellen: TanStack Table und TanStack Virtual (framework-neutral, virtuelles Scrollen). |
| Auslieferung Frontend | Statisches Bundle, im Multi-Stage-Build in das `web`-Image kopiert, von FastAPI unter `/` mit SPA-Fallback ausgeliefert | Eine Domain, ein Traefik-Router, keine CORS-Konfiguration, Cookie-Sitzungen funktionieren ohne Sonderfälle. Node wird nur zur Build-Zeit benötigt, im Betrieb läuft kein Node-Prozess. Variante mit eigenem `frontend`-Container (nginx) ist möglich, bringt bei 2 bis 5 Nutzern aber nur einen weiteren Container. |
| ORM, Migration | SQLAlchemy (Hauptversion 2, aktuelle stabile Version zum Umsetzungszeitpunkt prüfen) synchron, Alembic | Synchron in API und Worker, damit nur ein Treiberstapel gepflegt wird; FastAPI führt synchrone Endpunkte im Threadpool aus. Jede Migration mit `downgrade` (CR 0.5). Seeds für Konfigurationsstandardwerte als Alembic-Datenmigration. |
| Task-Queue | Celery mit Redis als Broker, Celery Beat als Scheduler | Wiederaufnahme, Retry mit Backoff, getrennte Queues (`ocr`, `classify`, `io`, `lists`), `acks_late` und `task_reject_on_worker_lost` für Idempotenz zusammen mit Job-Status in der DB. Alternative Dramatiq ist schlanker, hat aber weniger Betriebswerkzeuge. Valkey als Redis-kompatibler Ersatz ist ohne Codeänderung möglich (CR: Redis oder vergleichbar). |
| Datenbank | MariaDB | Siehe 2.2. |
| OCR | Tesseract mit Sprachpaket `deu`, ocrmypdf, Ghostscript, qpdf; PyMuPDF für Textschicht-Erkennung, Seitenextraktion und Vorschaubilder | Von CR vorgegeben (Tesseract, ocrmypdf). PyMuPDF entscheidet vorab je Seite, ob eine Textschicht vorhanden ist; nur Seiten ohne Text gehen in ocrmypdf (`--skip-text`). `OMP_THREAD_LIMIT=1` je Prozess, Parallelität ausschließlich über die Worker-Prozesse. |
| NER | spaCy mit deutschem Modell plus regelbasierte Erkenner: WE-Nummern (Präfix-Mapping aus dem Befund), IBAN mit Prüfsummenvalidierung (python-stdnum oder schwifty), Beträge, Datumsangaben und Zeiträume, Gazetteer aus `owners`, `tenants`, `units` (spaCy EntityRuler oder PhraseMatcher) | Personen- und Organisationsnamen aus dem Modell, alles Strukturierte aus Regeln, weil Regeln bei WE-Nummern und IBAN zuverlässiger sind als ein Modell. Zeitraumerkennung ist Pflicht für die historische Zuordnung (CR 7, Prüfpunkte 4 und 5). |
| Lokaler Klassifikator | scikit-learn: TF-IDF (Wort- und Zeichen-n-Gramme) plus linearer Klassifikator mit Kalibrierung, zwei Modelle (Hauptordner 01 bis 06, Dokumentunterart je Hauptordner) | CPU, Millisekunden je Dokument, trainierbar aus `review_corrections` (CR 11), Modellartefakt klein, kein eigener Container nötig. Variante mit Satz-Embeddings (ONNX) hinter derselben Schnittstelle, falls die Trefferquote nicht reicht; dann als optionaler `classifier`-Container. Kein lokales LLM (CR 7). |
| KI-Provider | Eigene Abstraktion `LLMProvider` (Protocol) mit Implementierungen für die offiziellen SDKs von OpenAI und Anthropic; Konfiguration je Provider in der DB (Modellname, Endpunkt, Region, Timeout, Kostenlimit, Rolle primär/fallback) | Einheitliches Ergebnisformat (Kategorie, Unterart, Konfidenz, Begründung, Token, Kosten). Kein LangChain: zwei Anbieter und ein Aufruftyp rechtfertigen keine Framework-Abhängigkeit. IBAN-Maskierung vor dem Aufruf, Kürzung auf konfigurierbare Tokenzahl, Protokoll in `ai_calls` je Objekt. |
| Excel | openpyxl | Blätter Aktuell, Historie, Offene Punkte; Tabellenobjekt mit Filter, fixierte Kopfzeile, Zahlenformat mit zwei Nachkommastellen, Datumszellen als Datum (CR 12a). |
| PDF | ReportLab, Portierung der Bausteine aus `scripts/hvm_briefkopf.py` des Skills hvm-ci in `backend/app/reports/hvm_ci.py` | A4 quer, Kennlinie (vier Segmente in den CI-Farben), Logo rechts oben, Fußzeile mit Pflichtangaben, Kopf mit Objektnummer, Bezeichnung, Verwaltungsart, Stand, Seitenzahl. Vorschlag zur Abweichung: in der Querformat-Tabelle 8 bis 9 pt statt der CI-Vorgabe 10 bis 11 pt, weil die Mindestspalten der Eigentümerliste sonst nicht auf eine Seitenbreite passen. Bestätigung einholen. |
| Google Drive | google-api-python-client, google-auth; Drive API v3 mit `supportsAllDrives=True`; Adapter-Schnittstelle `DriveAdapter` mit In-Memory-Fake für Tests | OAuth 2.0 offline mit Refresh-Token, Token verschlüsselt (Fernet, Schlüssel als Docker Secret oder in `.env`), Refresh automatisch, Ablauf auf der Statusseite. Resumable Upload, Exponential Backoff bei 429, 403 (Quota) und 5xx, Folder-ID-Cache in `drive_nodes`. |
| Authentifizierung | E-Mail und Passwort (argon2id), TOTP (pyotp) mit Wiederherstellungscodes, optional Google Workspace über OpenID Connect (authlib) begrenzt auf die Workspace-Domain; Sitzung als HttpOnly-Cookie mit serverseitigem Sitzungsspeicher in Redis | Cookie statt Token im Browserspeicher, weil SPA und API dieselbe Origin haben. CSRF-Schutz über SameSite-Cookie plus Origin-Prüfung und Custom-Header bei schreibenden Aufrufen. Rate-Limit für Login je IP und Konto. Details in 2.3. |
| Rollen | `admin`, `clerk` (Sachbearbeiter) als Enum an `users`, Rechteprüfung ausschließlich in API-Dependencies | Das Frontend blendet nur aus, die API entscheidet. Sichtbarkeit der Eigentümerakte je Rolle als Konfigurationswert (CR 10, offene Frage, ob Sachbearbeiter sie sehen). |
| Audit-Protokoll | Tabelle `audit_log` (Nutzer, Zeitstempel UTC, Aktion, Entitätstyp, Entitäts-ID, Vorher/Nachher als JSON, Request-ID, IP), geschrieben in der Service-Schicht; DB-Nutzer der Anwendung ohne UPDATE und DELETE auf dieser Tabelle | Jede Review-Center-Aktion (CR 15), Konfigurationsänderungen, Zugriffe auf entschlüsselte IBAN (CR 10). |
| Admin-Konfiguration | Tabelle `settings` (Schlüssel, Wert als JSON, Typ, Beschreibung, geändert von, geändert am), je Schlüssel ein Pydantic-Schema, Prozess-Cache mit Invalidierung über Redis Pub/Sub | Zur Laufzeit änderbar ohne Neustart. Schlüssel aus dem CR: Namensmuster Objektordner, Stellenzahl Objektnummer, Unterstruktur Eigentümerakte, Schwellwerte, `duplicate_owner_documents_in_drive`, KI-Provider, Aufbewahrungsfristen (Standard leer, Hinweistext), Präfix-Mapping Einheitentypen, Legacy-Alias für den Altordner (nur im Seed, nicht im Code, Befund Punkt 4). |
| Volltextsuche | MariaDB InnoDB FULLTEXT auf `document_texts` (maskierter OCR-Text) kombiniert mit relationalen Filtern (Eigentümer, Einheit, Zeitraum, Unterart) über normale Indizes | Kein weiterer Suchdienst, solange die Trefferqualität reicht. Grenzen: keine deutsche Stammformreduktion, Mindest-Tokenlänge konfigurieren. Ausbaupfad Meilisearch als eigener Container, falls nötig. |
| Tests | pytest (Unit, Integration gegen echte MariaDB im Compose-Testprofil, Drive-Fake, SDK-Mocks), Vitest (Frontend-Komponenten), Playwright (Ende-zu-Ende Review Center), Locust oder k6 (Antwortzeiten während der Verarbeitung), OpenAPI-Drift-Prüfung in CI | Details in 2.5. |
| Protokollierung | structlog mit JSON-Ausgabe, Request-ID-Middleware, Celery-Task-ID in jedem Eintrag, Docker-Log-Rotation | Keine IBAN und keine Volltexte in Logs. |

### 2.2 Datenbank: MariaDB statt MySQL

Empfehlung MariaDB. Gründe:

1. Lizenz und Herkunft: vollständig GPL, keine Doppel-Lizenz, offizielles Docker-Image mit eingebautem Healthcheck-Skript.
2. Funktionsumfang für dieses Projekt ausreichend: InnoDB FULLTEXT, CTEs, Fensterfunktionen, JSON-Funktionen, `utf8mb4`.
3. Werkzeuge für Backup (`mariadb-dump`) und Wiederherstellung sind im Image enthalten, kein Zusatzcontainer für Dumps nötig.

Bekannte Unterschiede, die im Entwurf berücksichtigt sind: MariaDB speichert JSON als Textspalte mit Prüfbedingung, nicht als eigenen Binärtyp. Konfigurationswerte werden deshalb in der Anwendung über Pydantic validiert, nicht in der DB. Der MySQL-eigene ngram-Parser für Volltext fehlt; für WE-Nummern und Namen greifen die relationalen Filter, nicht der Volltext. Sollte der Auftraggeber MySQL aus anderen Gründen bevorzugen, ist der Wechsel über den SQLAlchemy-Dialekt ohne Änderung der Fachlogik möglich; die Volltextdefinitionen und das Backup-Skript sind die einzigen anzupassenden Stellen.

### 2.3 Authentifizierung über die API im Detail

Ablauf E-Mail und Passwort mit TOTP:

1. `POST /api/v1/auth/login` mit E-Mail und Passwort. Bei Erfolg und aktivem TOTP: Antwort `mfa_required`, kurzlebiges Vor-Authentifizierungs-Cookie.
2. `POST /api/v1/auth/totp` mit Einmalcode oder Wiederherstellungscode. Bei Erfolg: Sitzungscookie (HttpOnly, Secure, SameSite=Lax), Sitzung in Redis mit absoluter und Leerlauf-Ablaufzeit, Sitzungsliste je Nutzer für Abmeldung auf allen Geräten.
3. Schreibende Aufrufe verlangen den Header `X-Requested-With` und eine passende `Origin`. Damit ist ein CSRF über fremde Seiten ausgeschlossen, ohne dass das Frontend Token verwalten muss.

Google Workspace: `GET /api/v1/auth/google/start` leitet zum Anbieter, `GET /api/v1/auth/google/callback` prüft `iss`, `aud`, `email_verified` und den Workspace-Domain-Anspruch, ordnet den Nutzer über die E-Mail einem bestehenden Konto zu. Keine automatische Kontoanlage, außer der Admin schaltet sie frei. Redirect-URI für die Anleitung: `https://uebernahme.muellerhv.de/api/v1/auth/google/callback`.

Ehrlich: In einem Server-Rendering-Stack wären Login, TOTP-Einrichtung mit QR-Code, Passwortzurücksetzen und Sitzungsverwaltung teilweise fertig. Hier entstehen etwa acht API-Endpunkte, sechs Frontend-Ansichten und die dazugehörigen Tests von Hand. Das ist Aufwand ohne fachlichen Mehrwert.

### 2.4 Wo die Trennung Nutzen bringt und wo sie nur Aufwand ist

| Bereich | Nutzen der SPA | Bewertung |
|---|---|---|
| Review Center, Massenbearbeitung | 40 Einzelabrechnungen markieren, Zielordner, Eigentümer, Einheit, Unterart, Jahr für die Auswahl setzen, je Zeile den berechneten Drive-Pfad sehen (Aufruf `POST /review/preview`, der die Benennungsfunktion ohne Schreiben ausführt), Zeilen einzeln korrigieren, dann in einem Schritt bestätigen. Zwischenzustand liegt im Browser, kein Serverzustand für halbfertige Bearbeitungen, Tastaturnavigation im Raster. | Echter Nutzen |
| Vorschau | Seitenleiste mit vorgerenderten Seitenbildern, Text und Erkennungsergebnisse (Namen, WE-Nummern, Zeiträume hervorgehoben) nebeneinander, Vorladen des nächsten Dokuments, Seitenbereiche für die relationale Zuordnung von Einzelabrechnungen per Auswahl markieren (CR 6). | Echter Nutzen |
| Kandidatenliste historischer Eigentümer | Auswahl mit Zeitstrahl der `owner_unit_assignments` neben dem erkannten Dokumentzeitraum. | Nutzen, aber auch serverseitig gut lösbar |
| Eigentümerlisten-Parser (CR 15) | Zeilen aus PDF, Excel, CSV als Vorschlag in einem bearbeitbaren Raster mit Zellvalidierung, unsichere Zeilen markiert. Nutzt dieselbe Rasterkomponente wie das Review Center. | Echter Nutzen, weil die Komponente doppelt verwendet wird |
| Suche | Vorschläge beim Tippen für Eigentümer und Einheiten, kombinierbare Filter ohne Seitenwechsel. Die Ergebnisliste selbst wäre serverseitig gleichwertig. | Teilnutzen |
| Fortschrittsanzeige Verarbeitung | Server-Sent Events aus Redis Pub/Sub. Funktioniert in beiden Ansätzen gleich. | Kein Unterschied |
| Objektansicht, Ordnerabgleich, Dry-Run | Formulare und Tabellen, eine Diff-Ansicht des Dry-Run. | Nur Aufwand, kein Nutzen gegenüber Server-Rendering |
| Admin-Konfiguration, Nutzerverwaltung, Statusseite | Reine Formulare. | Nur Aufwand |
| Login, TOTP, Passwort | Siehe 2.3. | Nur Aufwand |

Fazit: Der Nutzen konzentriert sich auf das Review Center und den Listen-Parser, also auf die Masken, in denen Sachbearbeiter den größten Teil ihrer Arbeitszeit verbringen werden. Alle anderen Masken kosten mehr als in Stack A und bringen nichts zurück.

### 2.5 Tests

| Ebene | Werkzeug | Inhalt |
|---|---|---|
| Unit Backend | pytest, freezegun | Benennungsfunktion (alle Fälle CR 4), Abgrenzungsregel mit mindestens 20 Beispieldokumenten (CR 6), historische Zuordnung (Dokument 03/2025, Wechsel 01.07.2026, Ergebnis Alteigentümer), IBAN-Maskierung, Objektnummern-Parser (2 bis 6 Stellen, Befund), Einheiten-Normalisierung (`WE 14` und `WE14` gleich). Reine Domänenfunktionen ohne Datenbank. |
| Integration Backend | pytest gegen MariaDB im Compose-Testprofil, `DriveFake` (In-Memory-Implementierung der `DriveAdapter`-Schnittstelle mit Zählung von Dateien je Ordner), Mocks der KI-SDKs | Ordnerabgleich mit den drei Testobjekten aus CR 14 (Dry-Run und Ausführung, Dateianzahl vorher gleich nachher, zweiter Lauf ohne Änderungen), doppelte Objektnummer erzeugt Review-Eintrag, Gesamtabrechnung mit 12 Einzelabrechnungen (eine Masterdatei, 12 relationale Zuordnungen, keine Duplikate), Pipeline-Abbruch und Wiederaufnahme (Worker-Prozess während Lauf beenden, neu starten, keine doppelte Verarbeitung anhand Hash und Job-Status), Provider-Fallback (Primär liefert Timeout, Fallback antwortet, Ergebnisschema identisch), Listenerzeugung (nach zwei Läufen je Objekt genau eine Datei je Format über Drive-Versionierung, Historie enthält Alteigentümer, keine vollständige IBAN). Echte MariaDB, weil Volltext und Kollation in SQLite nicht repräsentativ sind. |
| Unit Frontend | Vitest, Vue Test Utils | Rasterauswahl, Anwendung von Massenänderungen auf die Auswahl, Anzeige der Vorschaupfade, Formularvalidierung Pflichtfelder bei Zielbereich 05. |
| Ende-zu-Ende | Playwright gegen den Compose-Teststack | Login mit TOTP (Testgeheimnis), Review-Center-Massenbearbeitung von 40 Dokumenten bis zur Bestätigung, Audit-Einträge vorhanden. |
| Vertrag API und Frontend | CI-Job exportiert `openapi.json`, generiert den TypeScript-Client und schlägt bei Abweichung zum eingecheckten Stand fehl | Verhindert stille Schema-Drift. |
| Performance | `scripts/generate_test_corpus.py` erzeugt 10.000 Seiten mit konfigurierbarem Scan-Anteil (gerenderte Texte als Bild mit Rauschen für Scans, ReportLab für digitale PDFs); Locust oder k6 ruft während des Laufs die Review-Center-Endpunkte auf; Skript sammelt `docker stats` | Bericht mit Seiten pro Minute, RAM-Spitze, Speicherverbrauch, Antwortzeit p95 (CR 14). |

## 3. Container-Aufteilung in Compose

### 3.1 Dienste

| Dienst | Aufgabe | Image-Basis (Version zum Umsetzungszeitpunkt prüfen) | Volumes | Netze | Healthcheck | Ressourcen (Formel, siehe 3.3) |
|---|---|---|---|---|---|---|
| `migrate` | Einmaliger Lauf `alembic upgrade head` und Seeds; `web`, `worker`, `beat` starten erst nach erfolgreichem Abschluss | Gleiches Image wie `web` | keine | `app` | entfällt (Einmaldienst) | cpus 0,5, mem 512 MiB |
| `web` | FastAPI (uvicorn, mehrere Worker-Prozesse), liefert API unter `/api` und das SPA-Bundle unter `/`, SSE für Fortschritt | Multi-Stage: Node-Image baut das Frontend, Laufzeit auf `python:*-slim` | `ocr-cache` (nur lesen, für Vorschaubilder), `/srv/objektakte/exports` | `app`, `egress`, `${TRAEFIK_NETWORK}` (einziger Dienst am Traefik-Netz) | HTTP `GET /api/v1/health` (prüft DB und Redis) | cpus 1,0, mem 1 GiB, cpu_shares 1024 |
| `worker` | Celery-Worker für Queues `ocr` und `classify`: Download aus Drive in Transit, Hash, Textschicht-Erkennung, ocrmypdf, NER, Klassifikator, Vorschaubilder | `python:*-slim` plus `tesseract-ocr`, `tesseract-ocr-deu`, `ocrmypdf`, `ghostscript`, `qpdf` | `transit`, `ocr-cache`, `models` | `app`, `egress` | Heartbeat-Schlüssel in Redis, geprüft mit kleinem Skript (Alter unter 60 s) | cpus n_ocr, mem n_ocr × m_ocr + 0,5 GiB, cpu_shares 256, `OMP_THREAD_LIMIT=1`, `WORKER_CONCURRENCY=n_ocr` |
| `worker-io` | Celery-Worker für Queues `io` und `lists`: Drive-Verschiebungen und Uploads, externe KI-Aufrufe, Excel- und PDF-Listen, Modelltraining | Gleiches Image wie `worker` | `transit`, `models`, `/srv/objektakte/exports` | `app`, `egress` | Heartbeat wie `worker` | cpus 0,5, mem 768 MiB |
| `beat` | Celery Beat: Token-Refresh-Überwachung, Aufräumen hängender Jobs (Status `running` älter als Lease), Neuerzeugung der Listen mit Entprellung, nächtliches Training, Aufbewahrungsprüfung (nur melden, keine Löschung ohne freigegebenen Wert) | Gleiches Image wie `worker` | keine | `app` | Heartbeat | cpus 0,1, mem 128 MiB |
| `queue` | Redis als Broker, Sitzungsspeicher, Pub/Sub für SSE und Konfigurations-Invalidierung, Rate-Limits | Offizielles Redis- oder Valkey-Image | `queue-data` (AOF) | `app` | `redis-cli ping` | cpus 0,25, mem 256 MiB, `maxmemory` unter dem Limit, `maxmemory-policy noeviction` (Broker darf nichts verwerfen) |
| `db` | MariaDB | Offizielles MariaDB-Image (aktuelle LTS prüfen) | `db-data` | `app` | Healthcheck-Skript des Images oder `mariadb-admin ping` | cpus 1,0, mem max(1 GiB, 0,2 × M), `innodb_buffer_pool_size` 50 Prozent des Limits |
| `classifier` (optional, Compose-Profil) | Nur bei Embedding-Variante: HTTP-Dienst mit ONNX-Laufzeit, Eingabe Text, Ausgabe Kategorie und Konfidenz | `python:*-slim` plus onnxruntime | `models` | `app` | HTTP `GET /health` | cpus 1,0, mem 1 bis 2 GiB je Modell |
| `backup` | Cron im Container: täglich `mariadb-dump` nach `/srv/objektakte/backup/db/`, Archiv der Volumes `ocr-cache`, `models`, `queue-data` nach `/srv/objektakte/backup/volumes/`, Aufbewahrung über `BACKUP_RETENTION_DAYS`, Erfolgsmarker für die Statusseite | MariaDB-Image (enthält Client) oder `debian:*-slim` mit `mariadb-client` | alle Datenvolumes nur lesen, `/srv/objektakte/backup` | `app` | entfällt, Erfolg über Marker-Datei und Statusseite | cpus 0,5, mem 256 MiB |

Alle Dienste mit `restart: unless-stopped`, Log-Treiber `json-file` mit Rotation. Kein Dienst veröffentlicht Host-Ports.

Trennung von `worker` und `worker-io`: OCR-Prozesse dürfen die I/O-Aufgaben nicht blockieren. Wenn 40 Dokumente aus dem Review Center bestätigt werden, laufen die Drive-Verschiebungen sofort in `worker-io`, statt hinter 5.000 OCR-Seiten in der Warteschlange zu stehen. Beide Dienste nutzen dasselbe Image, nur Queue-Namen und Limits unterscheiden sich.

### 3.2 Netze

| Netz | Typ | Teilnehmer | Zweck |
|---|---|---|---|
| `app` | bridge, `internal: true` | alle Dienste | Interner Verkehr zu DB, Redis, Classifier. Kein Internetzugang. |
| `egress` | bridge, Standard | `web`, `worker`, `worker-io`, `beat` | Ausgehender Verkehr zu Google Drive und den KI-Anbietern. `db` und `queue` hängen nicht daran. |
| `${TRAEFIK_NETWORK}` | external | nur `web` | Anbindung an das bestehende Traefik. Name wird auf dem Server ausgelesen (Befund: nicht messbar aus der Entwicklungsumgebung). |

Traefik-Anbindung ausschließlich über Labels mit Platzhaltern, die aus der Messung befüllt werden:

```yaml
services:
  web:
    labels:
      - traefik.enable=true
      - traefik.docker.network=${TRAEFIK_NETWORK}
      - traefik.http.routers.uebernahme.rule=Host(`uebernahme.muellerhv.de`)
      - traefik.http.routers.uebernahme.entrypoints=${TRAEFIK_ENTRYPOINT_HTTPS}
      - traefik.http.routers.uebernahme.tls.certresolver=${TRAEFIK_CERT_RESOLVER}
      - traefik.http.services.uebernahme.loadbalancer.server.port=8000
```

Messpunkte für den Auftraggeber vor dem ersten Deployment (Befehle, Ergebnis in `docs/betrieb/server-messung.md`): `nproc`, `free -h`, `df -h /srv`, `docker network ls`, `docker inspect <traefik-container>` für Netz und Entrypoints, Traefik-Konfigurationsdatei für den Cert-Resolver.

### 3.3 Ressourcenlimits als Formel

Eingangsgrößen aus der Messung: C = Kerne (`nproc`), M = RAM in GiB (`free -h`).

| Größe | Formel | Herkunft |
|---|---|---|
| n_ocr | max(1, C − 1) | CR 7, Standardwert, über `WORKER_CONCURRENCY` änderbar |
| m_ocr | ANNAHME: 0,6 GiB Spitzenbedarf je OCR-Prozess bei A4 mit 300 dpi. Verifikation: `docker stats` während des 100-Seiten-Probelaufs auf dem VPS, Spitze je Prozess ablesen | Planungsgröße |
| mem `worker` | n_ocr × m_ocr + 0,5 GiB | Prozesse plus Celery-Grundlast |
| mem `db` | max(1 GiB, 0,2 × M) | Puffer für Volltext und Dokumenttabellen |
| mem übrige Dienste | Summe aus 3.1: `web` 1 GiB, `worker-io` 0,75 GiB, `queue` 0,25 GiB, `beat` 0,125 GiB, `backup` 0,25 GiB | feste Startwerte, nach Messung anpassen |
| Prüfbedingung | Summe aller Limits ≤ 0,8 × M, sonst n_ocr um 1 senken, bis erfüllt | 20 Prozent Reserve für Betriebssystem, Docker, Traefik |
| CPU `worker` | cpus = n_ocr, cpu_shares 256 | harte Obergrenze, damit mindestens ein Kern für `web`, `db`, `queue` frei bleibt |
| CPU `web` | cpus 1,0, cpu_shares 1024 | Bei Konkurrenz erhält `web` das Vierfache des Gewichts von `worker` |

Rechenbeispiel mit fiktiven Werten (keine Aussage über den VPS): bei C = 4 und M = 8 folgt n_ocr = 3, mem `worker` = 3 × 0,6 + 0,5 = 2,3 GiB, mem `db` = 1,6 GiB, übrige 2,375 GiB, Summe 6,275 GiB, Grenze 0,8 × 8 = 6,4 GiB, Bedingung erfüllt. Bei kleinerem M müsste n_ocr sinken.

## 4. Ordnerstruktur des Repositories

```
objekt-bernahme_automatisieren./
├── docker-compose.yml              Produktionsdefinition aller Dienste, Netze, Volumes, Labels mit Platzhaltern
├── docker-compose.override.yml     Entwicklung: Hot-Reload für API und Vite-Dev-Server, lokale Ports
├── docker-compose.test.yml         Testprofil: MariaDB, Redis, Drive-Fake, Playwright
├── .env.example                    Alle Variablen mit Erklärung, ohne Werte; echte .env nie im Repository
├── Makefile                        Kurzbefehle: up, migrate, test, gen-client, backup, rollback
├── .github/workflows/              CI: Backend-Tests, Frontend-Tests, OpenAPI-Drift, Image-Build, Grep-Prüfung Altbezeichnung
├── docs/
│   ├── anforderungen/              CR-05 unverändert
│   ├── architektur/                Stack-Entscheidung, Datenmodell, ADRs, Pipeline-Zustandsautomat
│   ├── betrieb/                    Server-Messung, Deployment, Rollback, Backup und Restore, OAuth-Anleitung, Traefik-Werte
│   └── bedienung/                  Kurzanleitung Review Center, Objektanlage, Admin-Konfiguration
├── backend/
│   ├── pyproject.toml              Abhängigkeiten, Werkzeugkonfiguration (ruff, mypy, pytest)
│   ├── Dockerfile                  Multi-Stage: Frontend-Build, Ziel api (web), Ziel worker (mit Tesseract, ocrmypdf)
│   ├── alembic/                    Migrationen mit up und down, Seeds als Datenmigration (enthält Legacy-Alias des Altordners)
│   ├── app/
│   │   ├── main.py                 App-Factory, Router-Registrierung, Static-Auslieferung des SPA-Bundles mit Fallback
│   │   ├── api/v1/                 Router: auth, users, objects, owners, units, tenants, documents, review, search, reports, lists, admin, status, health
│   │   ├── core/                   Konfiguration, Sicherheit (Passwort, TOTP, Sitzungen, CSRF), Dependencies, Logging, Request-ID
│   │   ├── db/                     Engine, Session-Factory, Basisklasse
│   │   ├── models/                 SQLAlchemy-Modelle (owners, units, owner_unit_assignments, tenants, leases, tenant_unit_assignments, documents, document_pages, document_texts, jobs, drive_nodes, settings, audit_log, review_items, review_corrections, ai_calls, users, sessions)
│   │   ├── schemas/                Pydantic-Schemata der API, Quelle des OpenAPI-Schemas
│   │   ├── domain/                 Reine Fachregeln ohne I/O: Benennungsfunktion, Abgrenzungsregel, historische Zuordnung, Objektnummern-Parser, Einheiten-Normalisierung, IBAN-Maskierung, Namens-Splitter
│   │   ├── services/               Anwendungsfälle: Ordnerabgleich (mit Dry-Run), Review, Listen, Vollständigkeitsprüfung, Nachforderung, Konfiguration
│   │   ├── adapters/
│   │   │   ├── drive/              DriveAdapter-Protokoll, Google-Implementierung, In-Memory-Fake, Backoff, Resumable Upload
│   │   │   ├── ai/                 LLMProvider-Protokoll, OpenAI, Anthropic, Fallback-Kette, Kostenprotokoll
│   │   │   └── ocr/                PyMuPDF-Textschicht-Erkennung, ocrmypdf-Aufruf, Vorschaubilder
│   │   ├── pipeline/               Celery-App, Queues, Tasks je Schritt, Zustandsautomat, Idempotenz über Hash und Job-Status
│   │   ├── nlp/                    NER (spaCy plus Regeln, Gazetteer), Klassifikator (Features, Training, Inferenz, Modellversionen)
│   │   ├── parsers/                Eigentümerlisten der Vorverwaltung: PDF, Excel, CSV, Formatprofile (z. B. Immoware24, Domus)
│   │   ├── reports/                Excel (openpyxl), PDF (ReportLab, hvm_ci.py mit Kennlinie, Logo, Fußzeile), Assets
│   │   └── seeds/                  Standardwerte der Konfiguration, Unterstruktur Eigentümerakte, Präfix-Mapping
│   └── tests/
│       ├── unit/                   Domänentests
│       ├── integration/            Tests gegen MariaDB, Drive-Fake, SDK-Mocks
│       ├── performance/            Korpus-Generator, Lastskript, Berichtsgenerator
│       └── fixtures/               Synthetische Beispieldokumente (mindestens 20 für die Abgrenzungsregel)
├── frontend/
│   ├── package.json, vite.config.ts, tsconfig.json
│   ├── src/
│   │   ├── api/                    Generierter TypeScript-Client aus openapi.json, dünne Wrapper, SSE-Client
│   │   ├── app/                    Router, Auth-Guard, Pinia-Stores (Sitzung, Konfiguration, Review-Auswahl)
│   │   ├── features/
│   │   │   ├── review-center/      Liste, Detail mit Vorschau, Massenbearbeitung, Kandidatenliste, Seitenbereiche
│   │   │   ├── objects/            Objektanlage, Ordnerabgleich mit Dry-Run-Diff, Objektansicht, Listen auslösen
│   │   │   ├── owners-units/       Stammdaten, Zuordnungen mit Zeiträumen, Listen-Parser-Raster
│   │   │   ├── search/             Suche über alle Objekte
│   │   │   ├── reports/            KPIs, Vollständigkeitsstatus, Anteil 06_Sonstiges
│   │   │   ├── admin/              Konfiguration, Nutzer, KI-Provider, Aufbewahrungsfristen
│   │   │   ├── status/             Verarbeitungsfortschritt, Token-Ablauf, letztes Backup
│   │   │   └── auth/               Login, TOTP-Einrichtung, Wiederherstellungscodes, Google-Anmeldung
│   │   ├── components/             Gemeinsame Bausteine: DataGrid (TanStack), PagePreview, EntityPicker, PeriodInput
│   │   └── styles/                 Basis-Styles, keine CI-Farben der HVM im Frontend nötig (interne Anwendung)
│   └── tests/                      Vitest-Komponententests, Playwright-Ende-zu-Ende
├── deploy/
│   ├── backup/                     Cron-Definition, dump.sh, restore.sh, Aufbewahrung
│   └── traefik/                    Nur Dokumentation der ausgelesenen Werte, keine Änderung am bestehenden Traefik
└── scripts/
    ├── measure_server.sh           Führt die Messbefehle aus und schreibt docs/betrieb/server-messung.md
    ├── gen_ts_client.sh            Exportiert openapi.json und generiert frontend/src/api
    └── check_legacy_names.sh       Grep-Prüfung: Altbezeichnung des Sonstiges-Ordners nur in backend/app/seeds und alembic (Definition of Done)
```

Regel für die Definition of Done (Grep leer): Die Altbezeichnung des Auffangordners steht ausschließlich im Seed und in der Alembic-Datenmigration als Konfigurationswert `legacy_folder_aliases`. Anwendungscode, Frontend, Tests und Dokumentation verwenden nur den Konfigurationsschlüssel. Das Frontend enthält keine Ordnernamen als Literale; es lädt Hauptordner, Unterstruktur und Einheitentypen beim Start über `GET /api/v1/meta`.

## 5. Wie der Stack die Performance-Vorgabe trägt

### 5.1 Vorgabe und Planungsgrößen

Vorgabe (CR 7 und 14): 10.000 Seiten je Objekt, gemischt Scan und Digital-PDF, vollständig verarbeitet (OCR, Klassifikation, Ablage) in unter 3 Stunden = 10.800 s. Erforderlicher Durchsatz: 10.000 / 10.800 ≈ 0,93 Seiten je Sekunde ≈ 56 Seiten je Minute. Review Center antwortet währenddessen unter 2 Sekunden. Planungsziel intern: 2 Stunden, damit ein Drittel Reserve bleibt.

| Größe | Wert | Verifikation |
|---|---|---|
| P | 10.000 Seiten | CR |
| p_d, Anteil Seiten mit Textschicht | ANNAHME: 50 Prozent, Spanne 30 bis 70 Prozent | Zusammensetzung des Testkorpus festlegen; im Betrieb zählt die Pipeline Seiten mit und ohne Textschicht je Objekt und weist den Anteil auf der Statusseite aus |
| t_ocr, Sekunden je Scan-Seite je Prozess | ANNAHME: 4 s bei 300 dpi, `deu`, ohne Deskew und Clean, Spanne 2 bis 8 s | Probelauf auf dem VPS: 100 Scan-Seiten mit `OMP_THREAD_LIMIT=1`, ein Prozess, `time ocrmypdf -l deu --skip-text`, danach mit n_ocr parallelen Prozessen, um Skalierungsverluste (Hyperthreading, Speicherbandbreite) zu messen |
| t_dig, Sekunden je digitale Seite | ANNAHME: 0,05 s (PyMuPDF-Textextraktion plus Vorschaubild) | gleicher Probelauf mit 100 digitalen Seiten |
| Dokumente je Objekt | ANNAHME: 1.250 (durchschnittlich 8 Seiten je Dokument) | Zählung im Testkorpus und im ersten Realobjekt |
| Klassifikation und NER je Dokument | ANNAHME: 0,1 bis 0,3 s | Messung im Probelauf; bei 1.250 Dokumenten in Summe unter 10 Minuten auf einem Kern, überlappt mit OCR |
| Drive-Download und Upload je Dokument | ANNAHME: 1 bis 3 s Netzlaufzeit, vier parallele Übertragungen in `worker-io` | Messung; bei 1.250 Dokumenten und 2 s je Dokument ergibt das etwa 10 Minuten, überlappt mit OCR |
| Externe KI, Anteil Dokumente unter Schwellwert | ANNAHME: 20 Prozent, 3 bis 10 s je Aufruf, vier parallel | 250 Aufrufe × 6 s / 4 ≈ 6 Minuten, überlappt; Anteil wird je Objekt protokolliert (CR 7) |

### 5.2 Rechenweg

OCR ist der einzige CPU-gebundene Engpass. Alle anderen Schritte sind I/O-gebunden und laufen in `worker-io` parallel zur OCR, sie verlängern die Gesamtzeit nur um den Nachlauf am Ende (letzte Uploads, letzte KI-Aufrufe, Listen), ANNAHME: 10 bis 15 Minuten.

T_ocr = P × (1 − p_d) × t_ocr / n_ocr + P × p_d × t_dig / n_ocr

Der zweite Summand ist klein: bei p_d = 50 Prozent sind es 5.000 × 0,05 / n_ocr = 250 / n_ocr Sekunden, also unter zwei Minuten. Entscheidend ist der erste Summand.

| Szenario | n_ocr = 3 | n_ocr = 5 | n_ocr = 7 |
|---|---|---|---|
| p_d 50 Prozent, t_ocr 4 s (5.000 Scan-Seiten) | 6.667 s ≈ 1 h 51 min | 4.000 s ≈ 1 h 07 min | 2.857 s ≈ 48 min |
| p_d 0 Prozent, t_ocr 4 s (10.000 Scan-Seiten, ungünstigster Mix) | 13.333 s ≈ 3 h 42 min, Vorgabe verfehlt | 8.000 s ≈ 2 h 13 min | 5.714 s ≈ 1 h 35 min |
| p_d 50 Prozent, t_ocr 8 s (langsamer Server oder Vorverarbeitung an) | 13.333 s ≈ 3 h 42 min, Vorgabe verfehlt | 8.000 s ≈ 2 h 13 min | 5.714 s ≈ 1 h 35 min |
| p_d 70 Prozent, t_ocr 4 s (3.000 Scan-Seiten) | 4.000 s ≈ 1 h 07 min | 2.400 s ≈ 40 min | 1.714 s ≈ 29 min |

Hinzu kommt jeweils der Nachlauf von etwa 10 bis 15 Minuten. Lesart: Ab fünf parallelen OCR-Prozessen wird die Vorgabe in allen betrachteten Szenarien eingehalten. Mit drei Prozessen hängt das Ergebnis am tatsächlichen Scan-Anteil und an der gemessenen Seitenzeit. Welche Spalte gilt, entscheidet `nproc` auf dem VPS; die Annahmen werden im Probelauf (Meilenstein M0) ersetzt.

### 5.3 Warum das Review Center unter 2 Sekunden bleibt

1. CPU-Trennung: `worker` hat eine harte Obergrenze von n_ocr Kernen und ein niedriges CPU-Gewicht; `web` und `db` haben zusammen mindestens einen freien Kern und das höhere Gewicht. Tesseract läuft mit `OMP_THREAD_LIMIT=1`, sonst startet jeder Prozess mehrere Threads und die Obergrenze wird durch Kontextwechsel teuer.
2. Kleine Antworten: Die API liefert JSON für 50 Zeilen mit Paginierung über Schlüssel (keyset), Indizes auf `documents(object_id, status)`, `review_items(status, created_at)`, `owner_unit_assignments(unit_id, valid_from, valid_to)`. ANNAHME: Datenbankzeit je Listenabfrage unter 50 ms; Verifikation im Performance-Test mit `EXPLAIN` und Messung p95.
3. Vorschau ohne Rechenlast zur Laufzeit: Seitenbilder werden in der Pipeline erzeugt und aus dem `ocr-cache` mit Cache-Headern ausgeliefert. Das Frontend rendert keine großen PDFs im Browser; das Original wird nur auf ausdrücklichen Wunsch gestreamt.
4. Lange Aktionen sind asynchron: Bestätigen von 40 Dokumenten schreibt die Entscheidung in die DB (Millisekunden), die Drive-Verschiebungen laufen in `worker-io`, der Fortschritt kommt per SSE. Die API antwortet sofort.
5. Kurze Transaktionen im Worker: jeder Pipeline-Schritt aktualisiert eine Dokumentzeile und einen Job-Eintrag; keine Tabellensperren, keine langen Transaktionen, Volltextinhalte werden in einer eigenen Tabelle geschrieben.
6. Getrennte Verbindungs-Pools: `web` und `worker` haben eigene Pools mit Obergrenzen, damit der Worker die Verbindungen der API nicht aufbraucht.

### 5.4 Stellhebel bei Verfehlung (in dieser Reihenfolge)

1. Vorverarbeitung abschalten oder reduzieren (`--deskew`, `--clean`, `--rotate-pages` kosten Zeit je Seite), Auflösung auf 200 bis 300 dpi begrenzen.
2. Tesseract mit den `fast`-Trainingsdaten statt `best` (Konfigurationswert, Trefferqualität im Review Center beobachten).
3. Zwei-Phasen-OCR: zuerst die ersten drei Seiten jedes Dokuments für Klassifikation und Review, danach die restlichen Seiten. Die Gesamtzeit bleibt gleich, aber Vorschläge stehen früher im Review Center und die Sachbearbeiter warten nicht auf den Gesamtlauf.
4. n_ocr auf C erhöhen und dafür das CPU-Gewicht von `web` weiter anheben; nur mit Messung der Antwortzeiten, weil dann kein Kern mehr exklusiv frei ist. Umgekehrt: wenn die 2 Sekunden verfehlt werden, n_ocr auf C − 2 senken.
5. Digital-PDFs vollständig an ocrmypdf vorbei: eigene Textschicht-Erkennung sorgt dafür, dass ocrmypdf für rein digitale Dateien gar nicht startet (Prozessstart und PDF/A-Umwandlung entfallen).
6. Serverressourcen erhöhen (mehr Kerne) oder ein zweiter Worker-Host über denselben Broker. Letzteres verlässt die Ein-Server-Vorgabe und wäre eine Entscheidung des Auftraggebers.

## 6. Meilensteine

Aufwände als grobe Spanne in Personentagen (PT) für einen erfahrenen Entwickler, der Python und TypeScript beherrscht. Summe 74 bis 113 PT. Der SPA-Anteil (M4 und die Frontend-Anteile in M1, M2, M6, M7, M8) liegt bei etwa 30 bis 40 PT.

| Nr. | Inhalt | Prüfbare Abnahme | PT |
|---|---|---|---|
| M0 Fundament und Messung | Server-Messung (Skript, Dokumentation), Traefik-Werte, Compose-Skelett mit allen Diensten, Multi-Stage-Dockerfile, Alembic, Auth minimal (E-Mail, Passwort, Rollen), Healthchecks, CI mit Backend- und Frontend-Tests, OpenAPI-Client-Generierung, Deployment- und Rollback-Ablauf, 100-Seiten-OCR-Probelauf | `docker compose up -d` auf frischem Checkout startet alle Dienste, Healthchecks grün, Login unter `https://uebernahme.muellerhv.de` mit gültigem TLS; Rollback auf voriges Image mit einem Befehl; Messbericht mit `nproc`, `free`, `df`, t_ocr, t_dig, m_ocr liegt vor | 6 bis 9 |
| M1 Datenmodell und Konfiguration | Tabellen owners, units, owner_unit_assignments, tenants, leases, tenant_unit_assignments, documents, jobs, settings, audit_log, users, sessions; Benennungsfunktion; Objektnummern-Parser (2 bis 6 Stellen); Einheiten-Normalisierung; Admin-Konfiguration (API und SPA-Maske); Audit in Service-Schicht; TOTP und Google-Anmeldung | Unit-Tests für alle Fälle aus CR 4, Abgrenzungsregel mit 20 Beispielen, historische Zuordnung; Konfigurationsänderung wirkt ohne Neustart; Audit-Eintrag je Änderung; Login mit TOTP; Grep-Prüfung Altbezeichnung leer außerhalb Seeds | 8 bis 12 |
| M2 Drive-Adapter und Ordnerabgleich | OAuth-Anleitung, Token-Speicher verschlüsselt, Refresh, Statusanzeige; DriveAdapter mit Fake; Ordnerabgleich mit Dry-Run, Legacy-Umbenennung mit Zählung, Idempotenz, Review-Eintrag bei doppelter Nummer; SPA: Objektanlage, Dry-Run-Diff | Integrationstests der drei Testobjekte aus CR 14 grün; Dry-Run schreibt nichts; zweiter Lauf ohne Änderungen; Token-Refresh über 8 Tage läuft ab M2 im Hintergrund mit | 8 bis 12 |
| M3 Pipeline Stufe 1 und 2 | Celery-Queues, Zustandsautomat, Hash-Idempotenz, Download, Textschicht-Erkennung, ocrmypdf, Vorschaubilder, NER, TF-IDF-Klassifikator mit Startkorpus, Routing nach Abgrenzungsregel und Eigentümerprüfungen 1 bis 6, Ablage in Drive bei ausreichender Konfidenz, Review-Einträge, 06_Sonstiges-Logik, SSE-Fortschritt, Statusseite | Pipeline-Abbruch und Wiederaufnahme ohne Doppelverarbeitung; Gesamtabrechnung mit 12 Einzelabrechnungen korrekt (eine Masterdatei, 12 Zuordnungen); Anteil 06_Sonstiges je Objekt wird ausgewiesen; Worker unter CPU-Limit, `web` antwortet | 12 bis 18 |
| M4 Review Center SPA | Liste mit Filtern, Detail mit Seitenvorschau und Hervorhebungen, Zielbereich 01 bis 06, Pflichtfelder bei 05, Eigentümersuche mit Vorschlägen, Kandidatenliste historischer Eigentümer, Seitenbereichsauswahl, Massenbearbeitung mit Vorschau der Zielpfade, Bestätigung asynchron mit Fortschritt, Speicherung als Trainingsdatum, Audit | Playwright-Test: 40 gleichartige Dokumente in einem Vorgang zugeordnet, Vorschaupfade stimmen mit der Benennungsfunktion überein, Audit-Einträge vollständig; Antwortzeit p95 der Listen- und Detailaufrufe unter 2 s bei laufender Pipeline im Teststack | 12 bis 18 |
| M5 Stufe 3 KI-Provider | LLMProvider-Schnittstelle, OpenAI- und Anthropic-Implementierung, Maskierung, Kürzung, Schwellwert, Fallback-Kette, Kosten- und Tokenprotokoll je Objekt, Admin-Maske je Provider | Integrationstest Provider-Wechsel (Primär fällt aus, Fallback übernimmt, Ergebnisschema identisch); Unit-Test IBAN-Maskierung vor Aufruf; Kostenlimit stoppt Aufrufe und erzeugt Review-Eintrag | 5 bis 8 |
| M6 Eigentümerlisten-Parser | PDF (Scan und digital), Excel, CSV, Formatprofile für Verwaltungssoftware-Exporte, Namens-Splitter mit Konfidenz, Vorschläge im Raster der SPA, Übernahme erst nach Bestätigung | Testdateien je Format werden als Vorschlag angezeigt, unsichere Zeilen markiert, nichts verworfen; Übernahme erzeugt Zuordnungen mit Zeiträumen | 8 bis 12 |
| M7 Listen, Vollständigkeit, Nachforderung | Excel mit drei Blättern, PDF A4 quer in HVM-CI, Drive-Versionierung über File-ID, Auslösung nach Lauf und Bestätigung sowie manuell; Vollständigkeitsprüfung WEG (CR 12); Nachforderungsschreiben als Entwurf in HVM-CI | Nach zwei Läufen genau eine Datei je Format und Liste; Historie enthält Alteigentümer; keine vollständige IBAN; Blatt Offene Punkte identisch mit Vollständigkeitsprüfung; PDF-Kopf enthält Objektnummer, Bezeichnung, Verwaltungsart, Stand, Seitenzahl | 8 bis 12 |
| M8 Suche und Reporting | Volltext plus Filter über alle Objekte, KPIs (Vollständigkeitsstatus, offene Review-Fälle, Anteil 06_Sonstiges) | Suchergebnisse für Eigentümer, Einheit, Zeitraum, Unterart in unter 2 s auf dem Testkorpus; KPI-Werte stimmen mit Datenbankabfragen überein | 4 bis 6 |
| M9 Performance-Test und Härtung | Korpus mit 10.000 Seiten, Lauf auf dem VPS, Lastmessung Review Center, Tuning der Stellhebel, Backup und Restore auf leerer Instanz, Server-Neustart-Test, Bedienungsanleitung, Dokumentation der Admin-Konfiguration, Ordnerabgleich auf allen Bestandsordnern mit Protokoll | Alle Punkte der Definition of Done aus CR 14 erfüllt und im Bericht belegt | 6 bis 10 |

Reihenfolge-Begründung: M0 liefert die Messwerte, die alle Ressourcenformeln und die Performance-Rechnung brauchen. M1 vor M2, weil der Ordnerabgleich die Benennungsfunktion und die Konfiguration voraussetzt. M3 vor M4, damit das Review Center gegen echte Pipeline-Ergebnisse gebaut wird. M5 nach M3, weil Stufe 3 nur die Fälle unter dem Schwellwert erhält und ohne Stufe 2 nicht sinnvoll getestet werden kann.

## 7. Ehrliche Schwächen und Risiken dieses Stacks für dieses Projekt

1. Zwei Sprachen, zwei Toolchains, zwei Abhängigkeitsbäume. Python (pip oder uv) und Node (npm) mit je eigener Sperrdatei, eigenen Sicherheitsupdates, eigenem Linting und eigenen Tests. Die Lieferkette von npm ist erfahrungsgemäß wartungsintensiver als die von Python. Für einen Entwickler verdoppelt sich die Pflegefläche.
2. Authentifizierung und Rechte von Hand. FastAPI bringt keine Nutzerverwaltung mit. Login, TOTP, Wiederherstellungscodes, Sitzungen, Rollenprüfung, Rate-Limits und CSRF werden selbst gebaut und getestet. Jeder Fehler hier ist sicherheitsrelevant. Ein Server-Rendering-Framework hätte einen Teil davon fertig.
3. Mehraufwand ohne Fachnutzen in den einfachen Masken. Objektansicht, Admin-Konfiguration, Nutzerverwaltung, Statusseite und Login sind Formulare; in der SPA kosten sie Router, Store, Ladezustände, Fehlerbehandlung und Client-Typen zusätzlich. Geschätzt 15 bis 25 Prozent Mehraufwand gegenüber Server-Rendering, konzentriert außerhalb des Review Centers.
4. Schema-Kopplung und Deployment. Frontend und API müssen zusammen ausgerollt werden; das Multi-Stage-Image löst das, erzwingt aber einen Node-Build bei jedem Deployment. Wird auf dem VPS gebaut, belastet der Frontend-Build CPU und RAM und darf nicht in eine laufende Objektverarbeitung fallen. Abhilfe: Images in CI bauen und auf dem Server nur ziehen; das ist ein weiterer Baustein (Registry, Zugangsdaten).
5. Nachfolgerisiko. Wer die Anwendung übernimmt, muss Python, FastAPI, Celery, SQLAlchemy und zusätzlich TypeScript, Vue, Vite und Pinia beherrschen. Der Kreis geeigneter Personen ist kleiner als bei einem einsprachigen Stack.
6. Alterung des Frontend-Ökosystems. Hauptversionen von Vue oder React, Vite und Build-Werkzeugen wechseln schneller als Django oder Flask. Eine Anwendung, die nach der Einführung nur noch gepflegt wird, sammelt im Frontend schneller Aktualisierungsschulden.
7. Fehlersuche über die Grenze. Ein Fehler im Review Center zeigt sich als API-Antwort, Netzwerkstatus oder Frontend-Zustand; Korrelation über Request-IDs ist Pflicht, sonst wird das Debugging langwierig.
8. Celery ist synchron, FastAPI asynchron. Der Entwurf umgeht das mit synchronem SQLAlchemy in beiden Teilen und synchronen Endpunkten im Threadpool. Das ist stabil, verschenkt aber einen Teil des Nutzens von FastAPI. Wer asynchrone DB-Zugriffe in der API will, pflegt zwei Treiberstapel.
9. Volltextsuche in MariaDB ohne deutsche Stammformreduktion und mit Mindest-Tokenlänge. Zusammengesetzte Begriffe in Dokumenten werden nur als Ganzes gefunden. Wenn die Sachbearbeiter mehr erwarten, kommt ein Suchdienst als weiterer Container hinzu.
10. Klassifikator-Startproblem. Der TF-IDF-Klassifikator braucht einen Anfangskorpus. Vor den ersten bestätigten Review-Fällen trägt Stufe 2 wenig, der Anteil externer KI-Aufrufe ist im ersten Objekt hoch, die Kosten entsprechend. Das gilt für jeden Stack, ist aber im Kostenprotokoll sichtbar zu machen.
11. Vorgaben im CR, die nicht zum Bestand passen (Befund): dreistellige Objektnummern, fehlende Basisspezifikation für 01 bis 04, fehlender Nachforderungsgenerator. Der Stack löst das nicht; die Klärung muss vor M1 stehen, sonst wird das Datenmodell zweimal gebaut.

## 8. Betriebsaufwand für ein Team aus einem Entwickler und 2 bis 5 Anwendern

### 8.1 Regelbetrieb

| Tätigkeit | Rhythmus | Aufwand (ANNAHME, Verifikation nach drei Betriebsmonaten anhand der Zeiterfassung) |
|---|---|---|
| Sicherheitsupdates Basis-Images, Python- und npm-Abhängigkeiten, Rebuild, Deployment außerhalb der Verarbeitungsfenster | monatlich, sicherheitskritisch sofort | 3 bis 5 Stunden je Monat, davon etwa ein Drittel für den Frontend-Anteil |
| Kontrolle Statusseite: letztes Backup, Token-Ablauf, hängende Jobs, Plattenplatz `ocr-cache` und `transit`, KI-Kosten je Objekt | wöchentlich, bei aktiver Übernahme täglich | 0,5 Stunden je Woche |
| Klassifikator: Trefferquote der Modellversionen prüfen, Anteil 06_Sonstiges je Objekt, Schwellwert nachjustieren | nach jedem Objekt in den ersten Monaten, später monatlich | 0,5 bis 1 Stunde je Objekt |
| Backup-Wiederherstellung auf leerer Instanz testen | halbjährlich, erstmalig in M9 | 2 bis 3 Stunden |
| Anwenderfragen, kleine Korrekturen an Regeln und Konfiguration | laufend | 1 bis 2 Stunden je Woche in den ersten drei Monaten, danach weniger |

### 8.2 Deployment und Rollback

Ablauf (dokumentiert in `docs/betrieb/deployment.md`): `git pull`, `docker compose build`, `docker compose up -d` (der Dienst `migrate` führt die Migrationen aus, `web`, `worker`, `beat` warten auf dessen Abschluss). Images werden mit dem Git-Commit getaggt (`IMAGE_TAG`). Rollback mit einem Befehl: `IMAGE_TAG=<voriger Tag> docker compose up -d`; falls die Migration Schema geändert hat, zusätzlich `alembic downgrade -1` im `migrate`-Dienst, weshalb jede Migration einen getesteten Rückweg braucht. Empfehlung für den zweiten Schritt nach Produktivstart: Images in GitHub Actions bauen und in eine Registry legen, damit der Node-Build nicht auf dem VPS läuft.

### 8.3 Überwachung

Healthchecks in Compose, Statusseite in der SPA mit Verarbeitungsfortschritt, Token-Ablauf, letztem Backup und Anteil 06_Sonstiges, strukturierte JSON-Logs je Container mit Rotation. Eine Benachrichtigung bei fehlgeschlagenem Backup oder ablaufendem Token ist sinnvoll, setzt aber einen Versandweg voraus (Workspace-SMTP oder ein Webhook), der nicht im CR steht (offene Frage).

### 8.4 Einordnung

Für 2 bis 5 Anwender ist der Betrieb selbst überschaubar; der Unterschied zu einem einsprachigen Stack liegt fast vollständig in der Pflege der zweiten Toolchain und im Deployment-Build. Wer den Stack B wählt, sollte die Frontend-Pflege bewusst als wiederkehrenden Posten einplanen und nicht als einmaligen Bauaufwand.

## Anhang A: Schnittstellen, die beide Teile binden

KI-Provider (Python, gemeinsam für beide Anbieter):

```python
class ClassificationRequest(BaseModel):
    text: str                      # bereinigt, IBAN maskiert, auf max_tokens gekürzt
    object_number: str
    management_type: str           # WEG, Miete, WEG mit SE
    unit_labels: list[str]         # bekannte Einheiten, keine Personennamen (Konfigurationswert)
    candidate_categories: list[str]

class ClassificationResult(BaseModel):
    category: str                  # 01 bis 06 als Konfigurationsschlüssel
    subtype: str | None
    confidence: float              # 0 bis 1
    reasoning: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int

class LLMProvider(Protocol):
    name: str
    def classify(self, request: ClassificationRequest, config: ProviderConfig) -> ClassificationResult: ...
```

Drive-Adapter (Python, echte Implementierung und Fake identisch):

```python
class DriveAdapter(Protocol):
    def find_object_folders(self, root_id: str, object_number: str) -> list[DriveNode]: ...
    def list_children(self, folder_id: str) -> list[DriveNode]: ...
    def create_folder(self, parent_id: str, name: str) -> DriveNode: ...
    def rename(self, node_id: str, new_name: str) -> DriveNode: ...
    def move(self, node_id: str, new_parent_id: str) -> DriveNode: ...
    def upload(self, parent_id: str, path: Path, name: str) -> DriveNode: ...
    def update_content(self, file_id: str, path: Path) -> DriveNode: ...   # neue Version, keine Neuanlage
    def download(self, file_id: str, target: Path) -> None: ...
    def count_files(self, folder_id: str) -> int: ...
```

Review-Vorschau (API, von der SPA für die Massenbearbeitung genutzt):

```
POST /api/v1/review/preview
Eingabe:  Liste von {document_id, target_category, owner_id, unit_id, subtype, period}
Ausgabe:  Liste von {document_id, target_path, folder_exists, warnings[]}
Wirkung:  keine; ruft nur die Benennungsfunktion und den Folder-ID-Cache auf
```
