# Stack-Vorschlag C: Polyglott (Laravel-Web-Schicht, Python-Worker)

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Projekt Objektuebernahme, CR-05. Stand 10.09.2026. Faktenbasis: CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md) und Befundakte vom 10.09.2026. Alle Planungsgroessen, die nicht aus diesen Quellen stammen, sind mit ANNAHME gekennzeichnet und werden im Meilenstein M0 auf dem VPS gemessen.

## 1. Kurzfassung

Stack C setzt die Web-Schicht (Login, Rollen, Review Center, Admin-Konfiguration, Google-Drive-Anbindung, Audit) in PHP mit Laravel um und laesst alles Rechen- und Dokumentenlastige (OCR, NER, lokaler Klassifikator, externe KI, Listen- und PDF-Erzeugung im HVM-CI, Import der Eigentuemerlisten) in einem Python-Worker laufen. Kernidee: Laravel ist Eigentuemer der Wahrheit (Datenbankschema, Migrationen, Nutzer, Einstellungen, Drive-Zugang), Python ist reiner Auftragnehmer, und die Sprachgrenze wird durch genau drei explizite Vertraege gesichert: das relationale Schema in MariaDB (Laravel schreibt Migrationen, Python reflektiert), JSON-Nachrichten ueber Redis Streams (JSON Schema im Ordner contracts/) und ein Einstellungskatalog in der Tabelle settings. Staerkste Eigenschaft: fuer eine kleine interne Fachanwendung liefert Laravel Login mit TOTP, Google-Anmeldung, Rollen, Queue-Dashboard, Scheduler, ORM mit reversiblen Migrationen und serverseitig gerenderte, reaktive Oberflaechen (Livewire) nahezu ohne Eigenbau, waehrend Python das mit Abstand beste Werkzeugangebot fuer Tesseract, ocrmypdf, spaCy, scikit-learn, beide KI-SDKs und ReportLab (die HVM-CI-Bausteine existieren bereits in ReportLab) behaelt. Groesste Schwaeche: zwei Laufzeiten, zwei Abhaengigkeitswelten und zwei Testsuiten fuer einen einzelnen Entwickler; jede fachliche Aenderung, die Dokumenttypen, Enums oder Einstellungen beruehrt, kostet Arbeit auf beiden Seiten und braucht Vertragstests, sonst driftet das System auseinander.

## 2. Technologie-Stack

### 2.1 Wahl des Web-Frameworks

Bewertet wurden Laravel (PHP), ASP.NET Core (C#), NestJS (TypeScript) und Spring Boot (Java) fuer eine interne Anwendung mit 3 bis 6 Nutzern, einem Entwickler, Docker auf einem VPS und einem Python-Worker daneben.

| Kriterium | Laravel | ASP.NET Core | NestJS | Spring Boot |
|---|---|---|---|---|
| Login E-Mail+Passwort, TOTP, Wiederherstellungscodes | Fortify, erste Partei, fertig | ASP.NET Identity, fertig | Passport plus otplib, Eigenbau der Ablaeufe | Spring Security plus Eigenbau TOTP |
| Google-Workspace-Anmeldung | Socialite, erste Partei | Google-Provider integriert | Passport-Google | Spring Security OAuth2 |
| ORM und reversible Migrationen | Eloquent, Migrationen mit up/down | EF Core Migrations | TypeORM oder Prisma | JPA plus Flyway/Liquibase |
| Interne Queue mit Dashboard | Horizon (Redis) | Hangfire | BullMQ (Redis) | Eigenbau oder Spring Batch |
| Serverseitige, reaktive Oberflaeche ohne SPA-Build | Blade plus Livewire | Razor Pages oder Blazor Server | Kein Standard, meist React/Vue als SPA | Thymeleaf plus HTMX, Eigenbau |
| Google Drive Client | Offizieller google/apiclient | Offizielles Google.Apis.Drive | Offizielles googleapis | Offizieller Java-Client |
| Speicherbedarf Web-Prozess neben OCR-Worker | Gering (php-fpm) | Mittel | Gering bis mittel | Hoch (JVM) |
| Sprachgrenze zur Python-Queue | Eigenbau (Streams, JSON) | Eigenbau (Streams, JSON) | BullMQ hat einen offiziellen Python-Port, dieser ist juenger und weniger verbreitet | Eigenbau |
| Zeit bis zum ersten nutzbaren Review Center | Kurz | Kurz bis mittel | Mittel (Frontend separat) | Mittel bis lang |

Empfehlung: Laravel. Begruendung: Es deckt den fachlich unspektakulaeren, aber umfangreichen Teil (Nutzer, Rollen, 2FA, Audit, Einstellungen, Queue, Scheduler, Formulare mit Suche und Massenbearbeitung) mit Erstpartei-Paketen ab, laeuft schlank neben einem CPU-hungrigen Worker und erzeugt mit Livewire ein reaktives Review Center ohne separates Frontend-Projekt. ASP.NET Core ist der engste Konkurrent und die bessere Wahl, wenn der umsetzende Entwickler in C# deutlich staerker ist als in PHP; die Architektur dieses Vorschlags (drei Vertraege, gleiche Container) bleibt dann unveraendert. NestJS zwingt zu einem separaten Frontend, Spring Boot ist fuer 5 Nutzer auf einem geteilten VPS unverhaeltnismaessig schwer.

### 2.2 Stack im Ueberblick

Versionsnummern nur als Richtung; jeweils aktuelle LTS/stabile Version zum Umsetzungszeitpunkt pruefen.

| Baustein | Wahl | Begruendung und Hinweise |
|---|---|---|
| Sprachen | PHP 8.x (Web), Python 3.12.x oder von spaCy und ocrmypdf unterstuetzte Version (Worker) | Zwei Sprachen sind der Kern dieses Vorschlags, siehe Abschnitt 2.4 zu den Kosten |
| Web-Framework | Laravel (aktuelle Hauptversion, Support-Zeitraum pruefen) | Siehe 2.1 |
| Oberflaeche | Blade plus Livewire plus Alpine.js, Tailwind CSS, Build ueber Vite nur im Docker-Build | Review Center mit Suche, Kandidatenlisten, Massenbearbeitung und Vorschau ohne SPA. Kein Filament als Admin-Panel im Standard, um eine einzige UI-Denkweise zu behalten; Filament bleibt Beschleunigeroption fuer Nutzer- und Einstellungsverwaltung |
| ORM und Migrationen | Eloquent, Laravel-Migrationen mit verpflichtender down()-Methode, Seeder fuer Einstellungskatalog | Erfuellt CR 0.5 (jede Migration reversibel). Schema-Eigentuemer ist ausschliesslich Laravel |
| Datenzugriff Worker | SQLAlchemy Core mit Schema-Reflektion beim Start, kein zweites Modell | Python definiert keine Tabellen, es liest die Struktur aus der DB und prueft die Migrationsstufe (Abschnitt 2.4) |
| Task-Queue intern (Laravel) | Laravel Queue auf Redis, Horizon fuer Worker-Prozesse und Dashboard | Drive-Downloads, Verschiebungen, Uploads, Listen-Upload, E-Mail |
| Task-Queue sprachuebergreifend | Redis Streams mit Consumer Groups (XADD, XREADGROUP, XACK, XAUTOCLAIM), Nachrichten als JSON nach JSON Schema | Sprachneutral, Bestaetigung und Wiederzustellung eingebaut, beide Redis-Clients (phpredis/predis, redis-py) unterstuetzen Streams nativ. Laravel-eigene Job-Payloads sind PHP-serialisiert und fuer Python nicht konsumierbar; Celery-Protokoll aus PHP ist brueckig. Wahrheit ueber den Jobzustand liegt in der Tabelle pipeline_jobs, nicht im Stream |
| Datenbank | MariaDB, LTS-Linie | Siehe 2.3 |
| OCR | Tesseract mit Sprachpaket deu, ocrmypdf, Ghostscript, qpdf/pikepdf, poppler-utils (pdftotext, pdfinfo) | Im Worker-Image (CR 7). OMP_THREAD_LIMIT=1, Parallelitaet ueber Prozesse statt Threads. Digital-PDF-Seiten werden ueber --skip-text nicht neu erkannt, Text kommt aus pdftotext |
| NER | spaCy mit deutschem Modell (de_core_news_md als Start, Modellgroesse zum Umsetzungszeitpunkt pruefen) plus deterministische Extraktoren (WE-Nummern, IBAN mit Pruefziffer, Betraege, Datums- und Zeitraumangaben) | Personen und Organisationen aus spaCy, alles Strukturierte per Regex und Pruefziffer, weil das stabiler ist als gelerntes Erkennen |
| Lokaler Klassifikator | scikit-learn: TF-IDF (Wort- und Zeichen-n-Gramme) plus linearer Klassifikator mit kalibrierter Wahrscheinlichkeit; Modellartefakt versioniert unter /srv/objektakte/models | CPU, Inferenz im Sub-Millisekunden- bis Millisekundenbereich, Training in Sekunden aus Review-Korrekturen (CR 11). Ausbaustufe: multilinguale Satz-Embeddings plus logistische Regression, dann optional als eigener Dienst classifier |
| KI-Provider | Python-Paket providers mit Protocol-Interface, Implementierungen fuer OpenAI und Anthropic ueber die offiziellen SDKs, strukturierte JSON-Ausgabe, pydantic-Ergebnismodell (Kategorie, Unterart, Konfidenz, Begruendung) | Modell, Endpunkt, Region, Timeout, Kostenlimit je Provider aus settings; Primaer und Fallback konfigurierbar; Circuit Breaker; Token und Kosten je Aufruf in ai_calls. IBAN- und Kontonummernmaskierung vor jedem Aufruf ist Teil des Interfaces, nicht des Aufrufers |
| Excel-Erzeugung | openpyxl im Worker | Tabellenobjekt mit Autofilter, fixierte Kopfzeile, Zahlenformat mit zwei Nachkommastellen, echte Datumszellen, drei Blaetter Aktuell, Historie, Offene Punkte (CR 12a) |
| PDF-Erzeugung | ReportLab im Worker, Portierung der vorhandenen HVM-Bausteine (Kennlinie, Logo rechts oben, Fusszeile mit Pflichtangaben, Folgeseite) in ein Paket worker/reports/ci | A4 quer, Kopf mit Objektnummer, Objektbezeichnung, Verwaltungsart, Stand, Seitenzahl. Farben und Schrift aus dem Skill hvm-ci. Auch Nachforderungsschreiben (Entwurf) laufen ueber diesen Pfad. Alternative PhpSpreadsheet plus mpdf in Laravel wurde verworfen, weil die CI-Bausteine bereits in ReportLab vorliegen und Excel und PDF aus derselben Datenabfrage entstehen sollen |
| Google Drive | google/apiclient in Laravel, Adapter-Interface DriveClient mit Implementierungen GoogleDriveClient und FakeDriveClient | Einziger Ort mit OAuth-Token und Drive-Logik. Folder-IDs in drive_nodes, resumable Upload, Exponential Backoff mit Jitter bei 403 Rate Limit, 429 und 5xx, supportsAllDrives=true, deterministische Reihenfolge ueber eine Horizon-Queue drive mit fester Prozesszahl. Python beruehrt Drive nie |
| Authentifizierung | Laravel Fortify: E-Mail plus Passwort, TOTP-Zweifaktor mit Wiederherstellungscodes, Ratenbegrenzung, Passwort-Reset. Socialite Google mit Parameter hd (Workspace-Domain) und serverseitiger Pruefung des hd-Anspruchs, nur Verknuepfung mit vorhandenem Nutzer gleicher verifizierter E-Mail, keine automatische Anlage | 2FA per Middleware fuer alle Rollen erzwungen |
| Rollen | Spalte users.role mit Enum admin, sachbearbeiter, Laravel Policies und Gates | Zwei Rollen rechtfertigen kein Berechtigungspaket. Eigentuemerakten sind fuer beide Rollen sichtbar, Policies sind vorbereitet fuer eine spaetere Leserolle |
| Audit-Protokoll | Tabelle audit_log (actor_type user/system/worker, actor_id, action, subject_type, subject_id, before, after, ip, created_at), geschrieben von Laravel (Observer und explizit im Review Center) und vom Worker (Pipeline-Entscheidungen) | Nur Einfuegen; DB-Trigger blockiert UPDATE und DELETE |
| Admin-Konfiguration | Tabelle settings (key, value als JSON, type, group, updated_by, updated_at) mit Katalog in contracts/settings.schema.json, Bearbeitung ueber Livewire-Admin, Cache mit Invalidierung ueber Redis-Pub/Sub settings.changed | Beide Seiten lesen dieselbe Tabelle, siehe 2.4 |
| Volltextsuche | InnoDB FULLTEXT auf documents.search_text (IBAN-maskiert, vom Worker geschrieben) plus strukturierte Filter (Eigentuemer, Einheit, Zeitraum, Unterart) ueber Eloquent | Fuer geschaetzt fuenfstellige Dokumentzahlen ausreichend; Laravel Scout mit Meilisearch-Container bleibt Ausbauoption |
| Tests | PHP: Pest, Feature-Tests gegen MariaDB (gleiche Engine wie Produktion wegen FULLTEXT und SKIP LOCKED), FakeDriveClient fuer Ordnerabgleich. Python: pytest mit Fixtures, Golden-Files fuer OCR-Text und Klassifikation. Vertragstests: Beispielnachrichten in contracts/examples werden von beiden Suiten gegen dieselben Schemas validiert. Performance: Korpusgenerator und Lastskript unter scripts/perf, Locust fuer die Review-Center-Antwortzeit | Details in Abschnitt 6 |
| Logging | Laravel Monolog mit JSON-Formatter auf stderr, Python structlog JSON auf stdout; Pflichtfelder job_id, document_id, object_id in jeder Zeile | Korrelation ueber die Sprachgrenze |

### 2.3 MariaDB statt MySQL

Empfehlung MariaDB, LTS-Linie. Gruende: vollstaendig quelloffen ohne Doppellizenz, schlankes offizielles Docker-Image mit eingebautem Healthcheck-Skript, SELECT ... FOR UPDATE SKIP LOCKED verfuegbar, JSON-Funktionen fuer settings und Kandidatenlisten ausreichend, Laravel bringt einen eigenen MariaDB-Treiber mit. Was MySQL besser koennte und hier nicht gebraucht wird: n-Gramm-Volltextparser fuer Sprachen ohne Wortgrenzen, mehrwertige JSON-Indizes. Fuer die deutsche Volltextsuche auf Namen, WE-Nummern und Unterarten reicht der wortbasierte InnoDB-Index; Stoppwortliste abschalten (innodb_ft_enable_stopword=0) und innodb_ft_min_token_size auf 2 setzen, damit Kuerzel wie GE oder TG gefunden werden. Zeichensatz utf8mb4, Kollation utf8mb4_unicode_ci.

Zwei DB-Konten als technische Absicherung der Sprachgrenze:

| Konto | Rechte | Nutzer |
|---|---|---|
| app_web | Vollzugriff DML auf alle Tabellen, DDL fuer Migrationen | web, web-jobs, bridge, scheduler |
| app_worker | SELECT auf Stammdaten (objects, units, owners, owner_unit_assignments, tenants, leases, tenant_unit_assignments, settings, drive_nodes), INSERT/UPDATE auf pipeline_jobs, documents (nur Verarbeitungsfelder), document_page_ranges, classifications, entities, import_proposals, ai_calls, training_samples, report_files, audit_log (nur INSERT) | worker, classifier |

Der Worker kann damit Stammdaten nicht veraendern, sondern nur Vorschlaege schreiben. Das setzt die CR-Vorgabe technisch durch, dass Eigentuemer und Einheiten erst nach Bestaetigung im Review Center in die Stammtabellen gelangen.

### 2.4 Die Sprachgrenze: was geteilt wird und was es kostet

Drei Vertraege, alle im Repository unter contracts/ versioniert.

| Vertrag | Quelle der Wahrheit | Laravel-Seite | Python-Seite | Pruefung | Kosten |
|---|---|---|---|---|---|
| Schema (Datenmodell) | Laravel-Migrationen | Eloquent-Modelle | SQLAlchemy Core reflektiert Tabellen beim Start; Worker liest die Tabelle migrations und verweigert den Start, wenn die letzte erwartete Migration (Konstante REQUIRED_MIGRATION im Worker) fehlt | Kontrakttest in Python prueft, dass jede vom Worker benutzte Spalte existiert und den erwarteten Typ hat; laeuft in CI gegen eine frisch migrierte MariaDB | Jede Schemaaenderung: Migration schreiben, Konstante im Worker heben, Test anpassen. Semantische Drift (gleiche Spalte, andere Bedeutung) faengt kein Werkzeug, nur Disziplin |
| Enums und Kataloge | contracts/enums.json (unit_type, owner_type, Hauptordner 01 bis 06, Unterordner der Eigentuemerakte, Dokumentunterarten, Jobstatus, Review-Status) | Generierte PHP-Enum-Klassen | Generierte Python-Enums | Generator tools/contracts-gen, CI schlaegt fehl, wenn generierte Dateien nicht dem JSON entsprechen | Ein zusaetzlicher Build-Schritt; neue Unterart heisst: JSON aendern, generieren, beide Suiten laufen lassen |
| Nachrichten | contracts/messages/*.schema.json, Typname mit Version (document.ocr.v1, document.classify.v1, document.classified.v1, import.parse.v1, import.parsed.v1, report.render.v1, report.rendered.v1, model.train.v1) | Publisher in app/Pipeline validiert vor XADD; Consumer im Container bridge validiert nach XREADGROUP | Consumer validiert nach XREADGROUP, Publisher vor XADD | Beispielnachrichten in contracts/examples werden von beiden Suiten validiert | Neue Felder nur additiv; inkompatible Aenderung heisst neue Version und Uebergangszeit mit zwei Consumern |
| Einstellungen | contracts/settings.schema.json (Schluessel, Typ, Standard, Gruppe, Beschreibung, Pflicht) | Seeder fuellt settings, Admin-UI erzeugt Formulare aus dem Katalog | SettingsRepository liest settings mit 30 s Cache und Invalidierung ueber Pub/Sub; prueft beim Start, dass alle Pflichtschluessel gesetzt sind | Beide Suiten laden den Katalog | Neue Einstellung an einer Stelle definieren, beide Seiten lesen sie; Abweichung faellt beim Start auf |
| Geheimnisse | .env oder Docker Secrets (nicht im Repository) | env() beim Start | os.environ beim Start | Compose-Konfiguration prueft Pflichtvariablen | Keine Doppelpflege, aber Rotation muss beide Container neu starten |

Beispiel: Die Legacy-Bezeichnung 05_Sonstiges (CR 9.3) steht ausschliesslich als Wert in contracts/settings.defaults.json unter drive.legacy_folder_aliases und wird per Seeder in settings geladen. Damit bleibt der Grep auf app/app und worker/worker leer (Definition of Done), der Abgleich erkennt den Altordner trotzdem.

Ablauf eines Dokuments ueber die Grenze (vereinfacht):

```mermaid
sequenceDiagram
    participant WJ as web-jobs (Laravel Horizon)
    participant DB as MariaDB
    participant RS as Redis Streams
    participant PW as worker (Python)
    participant BR as bridge (Laravel Consumer)
    WJ->>DB: documents: sha256, status=fetched; pipeline_jobs: queued
    WJ->>RS: XADD jobs:heavy document.ocr.v1 {job_id, document_id, path}
    PW->>RS: XREADGROUP jobs:heavy
    PW->>DB: UPDATE pipeline_jobs SET status=running WHERE id=? AND status IN (queued, stale)
    PW->>PW: ocrmypdf, pdftotext, Seitentexte in OCR-Cache
    PW->>DB: documents.search_text (maskiert), status=ocr_done
    PW->>RS: XADD jobs:light document.classify.v1
    PW->>RS: XACK jobs:heavy
    PW->>RS: XREADGROUP jobs:light
    PW->>DB: Stufe 1 bis 3, classifications, entities, ai_calls, status=classified
    PW->>RS: XADD events:laravel document.classified.v1 {document_id, category, subtype, candidates, confidence}
    PW->>RS: XACK jobs:light
    BR->>RS: XREADGROUP events:laravel
    BR->>DB: Ablageentscheidung: filings oder review_items
    BR->>WJ: dispatch FileDocument (Queue drive, geordnet)
    WJ->>DB: filings.drive_file_id, status=filed
```

Idempotenz und Wiederaufnahme: Die Zustandsmaschine liegt in pipeline_jobs (queued, running, done, failed, stale, dead). Ein Consumer darf nur arbeiten, wenn sein atomares UPDATE genau eine Zeile trifft; sonst bestaetigt er die Nachricht und tut nichts. Ein Heartbeat (heartbeat_at) alle 30 Sekunden erlaubt dem Scheduler, haengende Jobs als stale zu markieren und neu zu veroeffentlichen. Beim Start reklamiert jeder Consumer haengende Stream-Eintraege per XAUTOCLAIM. Dateihash (sha256) plus object_id ist eindeutig in documents, der OCR-Cache ist ueber den Hash adressiert; ein zweiter Lauf erkennt Ergebnisse und ueberspringt. Redis laeuft mit AOF-Persistenz und Verdraengung aus (noeviction). Weil die DB die Wahrheit ist, duerfen Streams getrimmt werden (XTRIM), und ein Reconcile-Kommando kann jederzeit Jobs aus der DB neu in den Stream stellen.

Verworfene Alternativen fuer die Rueckrichtung Python nach Laravel: (a) Python erzeugt Laravel-Job-Payloads im internen Format, verworfen wegen Abhaengigkeit von Framework-Interna; (b) Laravel-Scheduler pollt alle zehn Sekunden die DB nach neuen Ergebnissen, tragfaehig und einfacher, aber asymmetrisch zur Hinrichtung. (b) bleibt als Vereinfachung dokumentiert, falls der Container bridge im Betrieb mehr Aerger macht als er nutzt.

### 2.5 Datenmodell und Eigentuemerschaft je Tabelle

Entitaeten nach CR 3 und 12a, Bezeichner englisch. Die Spalte Schreiber zeigt, wer die Zeile anlegt oder aendert.

| Tabelle | Zweck | Schreiber |
|---|---|---|
| objects | Objektnummer (fuehrende Ziffernfolge, Stellenzahl konfigurierbar, Vorschlag siehe unten), Bezeichnung, Verwaltungsart, Drive-Folder-ID, Namensmuster-Ergebnis | Laravel |
| units | object_id, unit_number, unit_type, unit_label, co_ownership_share | Laravel (nach Bestaetigung) |
| owners | type, first_name, last_name, company_name, salutation, correspondence_address, delivery_address, email, phone, iban_last4, iban_encrypted (nullable) | Laravel |
| owner_unit_assignments | owner_id, unit_id, valid_from, valid_to, share, source_document_id | Laravel |
| tenants, leases, tenant_unit_assignments | analog zu Eigentuemern (CR 12a) | Laravel |
| documents | object_id, sha256, drive_file_id, original_name, page_count, status, search_text (maskiert), ocr_cache_key, has_text_layer | Laravel (Anlage), Worker (Verarbeitungsfelder) |
| document_page_ranges | document_id, page_from, page_to, owner_id, unit_id, assignment_id, subtype, period_from, period_to | Worker (Vorschlag), Laravel (Bestaetigung) |
| classifications | document_id, stage (1, 2, 3), category (01 bis 06), subtype, confidence, reasoning, model_version, provider, created_at | Worker |
| entities | document_id, kind (person, org, unit_ref, amount, period, iban_masked), value_normalized, page, confidence | Worker |
| filings | document_id, target_folder_node_id, path_rendered, decided_by (rule, review), decided_at | Laravel |
| review_items | document_id oder import_proposal_id, reason, candidates (JSON), status, assigned_to | Laravel (aus Worker-Ereignissen), Laravel (Bearbeitung) |
| review_actions | review_item_id, user_id, action, payload, created_at | Laravel |
| training_samples | document_id, label (category, subtype), source (review), created_at | Laravel (beim Bestaetigen), Worker liest fuer Training |
| import_batches, import_proposals | Datei, Formatprofil (pdf_scan, pdf_digital, xlsx, csv, immoware24, domus), Zeilen mit erkanntem Eigentuemer, Einheit, Konfidenz, Status | Worker (Vorschlaege), Laravel (Bestaetigung) |
| pipeline_jobs | type, payload, status, attempts, locked_by, heartbeat_at, error, idempotency_key | Laravel (Anlage), Worker (Zustand) |
| ai_calls | document_id, provider, model, prompt_tokens, completion_tokens, cost_estimate, latency_ms, fallback_used | Worker |
| drive_nodes | object_id, path_key (z. B. 05/WE03_Mustermann/04_Hausgeld), drive_id, parent_drive_id, kind, last_seen_at | Laravel |
| report_files | object_id, kind (owner_list_xlsx, owner_list_pdf, tenant_list_xlsx, tenant_list_pdf), drive_file_id, generated_at, transit_path | Worker (Erzeugung), Laravel (Drive-ID) |
| requirement_checks | object_id, check_key, status, evidence_document_id, checked_at | Laravel |
| retention_policies | category, subtype, retention_years (nullable), approved_by, approved_at | Laravel (Admin) |
| settings, users, audit_log, oauth_tokens | Betrieb | Laravel (audit_log auch Worker) |

### 2.6 Fachliche Festwerte des CR im Vertragskatalog

Die folgenden Werte stehen wortgetreu aus CR 2, 4, 5 und 8 in contracts/settings.defaults.json und contracts/enums.json. Kein Anwendungscode auf beiden Seiten enthaelt sie als Literal; beide Seiten lesen sie aus settings beziehungsweise den generierten Enums. Damit sind Unterstruktur und Benennung administrativ aenderbar (CR 4) und der Grep auf 05_Sonstiges bleibt leer.

| Katalogschluessel | Werte |
|---|---|
| drive.main_folders | 01_Legitimationsunterlagen, 02_Stammakte, 03_Buchhaltung, 04_Mieterakte, 05_Eigentümerakte, 06_Sonstiges |
| drive.owner_file_subfolders | 01_Stammdaten, 02_Eigentumsnachweise, 03_SEPA, 04_Hausgeld, 05_Abrechnungen, 06_Wirtschaftsplaene, 07_Beschluesse, 08_Korrespondenz, 09_Mahnwesen, 10_Vollmachten, 11_Sonstiges |
| drive.misc_subfolders | 01_Unklar, 02_Manuelle_Pruefung, 03_Dubletten, 04_Nicht_objektbezogen |
| drive.object_folder_pattern | NNN Ort, Straße Hausnummer |
| drive.owner_folder_rules | Standard WE01_Nachname; mehrere Eigentuemer WE03_Nachname1-Nachname2 (alphabetisch, hoechstens drei Namen, danach WE03_Nachname1-ua); Einheit unbekannt Unbekannte_WE_Nachname; weder Einheit noch Eigentuemer sicher Unzugeordnet |
| drive.legacy_folder_aliases | 05_Sonstiges nach 06_Sonstiges (nur Erkennung und Umbenennung im Abgleich, CR 9.3) |
| classification.confidence_threshold, classification.max_prompt_tokens, drive.duplicate_owner_documents_in_drive (Standard false), pipeline.ocr_chunk_pages, pipeline.ocr_processes | Schwellwerte und Betriebsparameter nach CR 6, 7 |
| report.owner_list_columns, report.tenant_list_columns | Mindestspalten nach CR 12a, administrativ erweiterbar |

Die Benennungsfunktion fuer Eigentuemerordner liegt in Laravel (app/Domain/Filing/OwnerFolderNamer) mit Unit-Tests fuer alle Faelle aus CR 4; sie ist die einzige Stelle, die Ordnernamen aus Datenmodell und Regeln erzeugt. Die Schreibweise der Hauptordner mit Umlaut (05_Eigentümerakte) neben ASCII-Unterordnern (06_Wirtschaftsplaene) wird wie im CR uebernommen; der Befund fuehrt sie als Bestaetigungspunkt.

Vorschlag zur Objektnummer (Abweichung vom CR-Wortlaut, Begruendung aus dem Befund): Der Bestand enthaelt zwei- bis fuenfstellige Objektnummern. Die Erkennung sollte als fuehrende Ziffernfolge mit konfigurierbarer Mindest- und Hoechststellenzahl (Standard 2 bis 6) und Trennzeichen Leerzeichen, Unterstrich oder Komma geplant werden; Eindeutigkeit ueber den Zahlwert. Ob bei Neuanlage mit Nullen aufgefuellt wird, ist Entscheidung des Auftraggebers (offene Frage).

## 3. Container-Aufteilung in Compose

Formelgroessen: C = gemessene Kerne (nproc), M = gemessener RAM in GiB (free -h), P = Anzahl OCR-Prozesse = C minus 1 (CR 7), L = Anzahl leichter Worker-Prozesse (NER, Klassifikation, Berichte, Import), Standard 1. Alle Zahlenwerte in den Speicherformeln sind ANNAHME und werden in M0 per docker stats unter Last verifiziert.

| Container | Aufgabe | Image-Basis | Volumes | Netze | Healthcheck | CPU-Limit | Speicherlimit |
|---|---|---|---|---|---|---|---|
| web | nginx plus php-fpm (supervisord im selben Image), Laravel-App, Review Center, Admin, Statusseite, OAuth-Callback | php:8.x-fpm (Debian-Variante), nginx aus Distribution, Vite-Build in Multi-Stage | app_storage (Sessions nicht, die liegen in Redis), /srv/objektakte/transit lesend fuer Vorschau | backend, ${TRAEFIK_NETWORK} (einziger Container mit externem Netz) | curl -f http://localhost/up | 1,0 | 768 MiB (ANNAHME: 8 php-fpm-Kinder mal 60 MiB plus nginx) |
| web-jobs | Horizon: Drive-Downloads, Verschiebungen, Uploads, Listen-Upload, E-Mail | gleiches Image wie web, Kommando php artisan horizon | /srv/objektakte/transit | backend | php artisan horizon:status | 0,5 | 512 MiB |
| bridge | Stream-Consumer events:laravel, setzt Ablageentscheidungen um, legt Review-Eintraege an; Prozess beendet sich nach N Nachrichten oder 1 h selbst und wird neu gestartet (PHP-Langzeitprozess) | gleiches Image, Kommando php artisan pipeline:consume | keine | backend | Heartbeat-Schluessel in Redis juenger als 60 s | 0,25 | 256 MiB |
| scheduler | php artisan schedule:work: Token-Refresh-Pruefung, stale-Erkennung, Reconcile Stream gegen DB, Listen-Neuerzeugung mit Entprellung, KPI-Berechnung, Modelltraining anstossen | gleiches Image | keine | backend | Heartbeat wie bridge | 0,25 | 256 MiB |
| worker | Python-Supervisor startet P schwere Prozesse (nur ocrmypdf, keine ML-Modelle im Speicher) und L leichte Prozesse (spaCy, Klassifikator, Provider, openpyxl, ReportLab, Import-Parser); Prozesse laufen mit nice und ionice | python:3.x-slim plus tesseract-ocr, tesseract-ocr-deu, ocrmypdf, ghostscript, qpdf, poppler-utils, unpaper optional | /srv/objektakte/transit, /srv/objektakte/ocr-cache, /srv/objektakte/models, /srv/objektakte/tmp (OCR-Temp auf Platte, nicht tmpfs) | backend | Heartbeat-Schluessel je Prozess in Redis | C minus 1 | P mal 0,6 GiB plus L mal 0,8 GiB plus 0,3 GiB (ANNAHME: Spitze ocrmypdf plus Tesseract je Prozess 0,4 bis 0,6 GiB bei 300 dpi A4; leichter Prozess mit spaCy-Modell 0,5 bis 0,8 GiB) |
| classifier (optional) | Nur wenn Embedding-Modell eingefuehrt wird: haelt ein Modell im Speicher, HTTP intern (FastAPI/uvicorn), ersetzt Modellkopien in den leichten Prozessen | python:3.x-slim | /srv/objektakte/models | backend | GET /healthz | 1,0 | 1,5 GiB (ANNAHME) |
| queue | Redis mit AOF (appendfsync everysec), maxmemory-policy noeviction | redis:7-alpine (Lizenzlage Redis seit 2024 geaendert, fuer internen Betrieb unkritisch; Valkey als protokollkompatible BSD-Alternative) | redis_data | backend | redis-cli ping | 0,25 | 384 MiB, maxmemory 256 MiB |
| db | MariaDB | mariadb (LTS-Tag) | db_data, docker/db/conf.d | backend | healthcheck.sh --connect --innodb_initialized (im Image enthalten) | 1,0 | 0,20 mal M; innodb_buffer_pool_size = 50 Prozent davon |
| backup | Taeglich mariadb-dump --single-transaction plus tar der Volumes models und ocr-cache nach /srv/objektakte/backup, Aufbewahrung BACKUP_RETENTION_DAYS, Restore-Skript | alpine plus mariadb-client plus supercronic | db-Socket ueber Netz, /srv/objektakte/backup, /srv/objektakte/models, /srv/objektakte/ocr-cache lesend | backend | Zeitstempeldatei des letzten Backups juenger als 26 h | 0,25 | 256 MiB |

Zusaetzlich cpu_shares: web 1024, db 1024, worker 256. Limits kappen, Shares priorisieren im Wettbewerb. So bleibt das Review Center unter Volllast des Workers bedient, ohne dass Kerne ungenutzt bleiben, wenn der Worker pausiert.

Plausibilitaet der Speichersumme: 0,768 plus 0,5 plus 0,25 plus 0,25 plus Worker plus 0,384 plus 0,2 M plus 0,25 GiB muss unter 0,85 mal M bleiben (1 GiB Reserve fuer Betriebssystem und Docker). Bei kleinem M wird P reduziert, nicht das Limit von web oder db. Bei C kleiner oder gleich 2 traegt der Ansatz nicht; dann ist vor M1 ueber einen groesseren Tarif zu entscheiden.

Compose-Auszug mit Platzhaltern (keine Traefik-Namen angenommen, alle Werte aus .env nach Messung auf dem Server):

```yaml
services:
  web:
    build: { context: ., dockerfile: docker/web/Dockerfile }
    restart: unless-stopped
    env_file: .env
    networks: [backend, traefik_external]
    volumes:
      - /srv/objektakte/transit:/srv/objektakte/transit:ro
    deploy:
      resources:
        limits: { cpus: "${WEB_CPUS}", memory: "${WEB_MEM}" }
    cpu_shares: 1024
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost/up"]
      interval: 30s
      timeout: 5s
      retries: 3
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=${TRAEFIK_NETWORK}"
      - "traefik.http.routers.uebernahme.rule=Host(`${APP_HOST}`)"
      - "traefik.http.routers.uebernahme.entrypoints=${TRAEFIK_ENTRYPOINT}"
      - "traefik.http.routers.uebernahme.tls.certresolver=${TRAEFIK_CERTRESOLVER}"
      - "traefik.http.services.uebernahme.loadbalancer.server.port=8080"
  worker:
    build: { context: ., dockerfile: docker/worker/Dockerfile }
    restart: unless-stopped
    env_file: .env
    environment:
      OCR_PROCESSES: "${OCR_PROCESSES}"
      LIGHT_PROCESSES: "${LIGHT_PROCESSES}"
      OMP_THREAD_LIMIT: "1"
    networks: [backend]
    volumes:
      - /srv/objektakte/transit:/srv/objektakte/transit
      - /srv/objektakte/ocr-cache:/srv/objektakte/ocr-cache
      - /srv/objektakte/models:/srv/objektakte/models
      - /srv/objektakte/tmp:/srv/objektakte/tmp
    deploy:
      resources:
        limits: { cpus: "${WORKER_CPUS}", memory: "${WORKER_MEM}" }
    cpu_shares: 256
networks:
  backend: {}
  traefik_external:
    external: true
    name: "${TRAEFIK_NETWORK}"
```

Kein Container bindet Host-Ports. Alle Container laufen als nicht privilegierter Nutzer, mit no-new-privileges und, wo moeglich, read-only Root-Dateisystem. Geheimnisse (DB-Passwoerter, APP_KEY, API-Schluessel, OAuth-Client-Secret) kommen aus .env oder Docker Secrets; der Entrypoint exportiert Dateien aus /run/secrets in Umgebungsvariablen, weil Laravel _FILE-Varianten nicht nativ liest.

## 4. Ordnerstruktur des Repositories

```
objekt-bernahme_automatisieren./
├── docker-compose.yml          Produktions-Compose, alle Serverwerte als Variablen aus .env
├── docker-compose.dev.yml      Overrides fuer lokale Entwicklung (Bind-Mounts, Xdebug, Mailpit)
├── .env.example                Alle Pflichtvariablen mit Erklaerung, ohne Werte
├── Makefile                    Standardbefehle: up, migrate, test, contracts, perf, backup-test, rollback
├── contracts/                  Sprachneutrale Vertraege, einzige Quelle fuer Enums, Nachrichten, Einstellungen
│   ├── enums.json              unit_type, owner_type, Hauptordner, Unterordner, Unterarten, Zustaende
│   ├── settings.schema.json    Katalog aller Einstellungen (Schluessel, Typ, Standard, Gruppe, Pflicht)
│   ├── settings.defaults.json  Startwerte inkl. Namensmuster, Unterstruktur, Schwellwerte, Legacy-Aliase
│   ├── messages/               JSON Schema je Nachrichtentyp und Version
│   └── examples/               Gueltige und ungueltige Beispielnachrichten fuer beide Testsuiten
├── docker/
│   ├── web/                    Dockerfile (Multi-Stage mit Vite), nginx.conf, php.ini, supervisord.conf, entrypoint.sh
│   ├── worker/                 Dockerfile mit Tesseract deu, ocrmypdf, Ghostscript, Modell-Download beim Build
│   ├── db/                     conf.d mit Buffer Pool, Volltextparameter, Zeichensatz
│   ├── queue/                  redis.conf (AOF, noeviction, maxmemory)
│   └── backup/                 Dockerfile, backup.sh, restore.sh, crontab
├── app/                        Laravel-Anwendung
│   ├── app/Domain/             Fachmodule: Objects, Units, Owners, Tenants, Documents, Filing, Review, Import, Requirements, Reports, Retention
│   ├── app/Drive/              DriveClient-Interface, GoogleDriveClient, FakeDriveClient, FolderCache, Backoff, Reconciler (Ordnerabgleich, Dry-Run)
│   ├── app/Pipeline/           StreamPublisher, EventConsumer, JobRepository, Reconcile-Kommando
│   ├── app/Settings/           SettingsRepository, Katalog-Loader, Admin-Formulargenerator
│   ├── app/Http/               Controller, Middleware (2FA erzwingen), Livewire-Komponenten
│   ├── app/Policies/           Zugriffsregeln je Rolle
│   ├── database/migrations/    Schema, jede Migration mit down()
│   ├── database/seeders/       Einstellungskatalog, Rollen, Testobjekte
│   ├── resources/views/        Blade und Livewire (Review Center, Objektansicht, Admin, Status)
│   ├── routes/                 web.php, auth ueber Fortify
│   └── tests/                  Pest: Unit (Benennung, Abgrenzung, historische Zuordnung), Feature (Abgleich mit FakeDrive, Auth, Review)
├── worker/                     Python-Worker
│   ├── worker/
│   │   ├── __main__.py         Supervisor: startet P schwere und L leichte Consumer-Prozesse
│   │   ├── consumer.py         XREADGROUP-Schleife, Claim, Heartbeat, XAUTOCLAIM, Fehlerbehandlung, Dead Letter
│   │   ├── db.py               SQLAlchemy Core, Reflektion, Migrationspruefung, Repositories
│   │   ├── settings.py         Lesen der settings-Tabelle mit Cache und Pub/Sub-Invalidierung
│   │   ├── ocr/                Seitenanalyse, ocrmypdf-Aufruf, Chunking in Seitenbereiche, Merge, OCR-Cache
│   │   ├── ner/                spaCy-Pipeline, Extraktoren (WE-Nummer, IBAN, Betrag, Zeitraum), Normalisierung
│   │   ├── classify/           stage1_rules.py, stage2_local.py, stage3_llm.py, cascade.py, masking.py, history.py (historische Eigentuemerzuordnung)
│   │   ├── providers/          base.py (Protocol), openai_provider.py, anthropic_provider.py, router.py (Primaer/Fallback, Kosten, Circuit Breaker)
│   │   ├── importers/          Eigentuemerlisten: pdf_scan, pdf_digital, xlsx, csv, Formatprofile immoware24, domus; Namens-Splitter mit Konfidenz
│   │   ├── reports/            owner_list.py, tenant_list.py, demand_letter.py; ci/ mit portierten HVM-Bausteinen und Logo
│   │   └── training/           Aufbau des Trainingskorpus aus training_samples, Training, Kalibrierung, Modellversionierung
│   ├── tests/                  pytest: Unit, Golden-Files, Kontrakttests, Schema-Reflektionstest
│   └── pyproject.toml          Abhaengigkeiten, Werkzeugkonfiguration
├── tools/contracts-gen/        Generator contracts/*.json nach PHP-Enums und Python-Enums, mit Pruefmodus fuer CI
├── scripts/
│   ├── deploy.sh               git pull, compose build, compose up -d, migrate, Image-Tag merken
│   ├── rollback.sh             Vorheriges Image-Tag aktivieren, ein Befehl
│   ├── measure-server.sh       nproc, free -h, df -h, docker network ls, Traefik-Werte auslesen, in .env-Vorlage schreiben
│   └── perf/                   Korpusgenerator (10.000 Seiten gemischt), Lauf- und Messskript, Locust-Datei fuer Review Center
├── docs/
│   ├── anforderungen/          CR-05 unveraendert
│   ├── architektur/            Dieser Vorschlag, Entscheidungen, Vertraege, Datenmodell
│   ├── betrieb/                Deployment, Rollback, Backup und Restore, OAuth-Anleitung Google Cloud Console, Messprotokoll
│   └── bedienung/              Kurzanleitung Review Center und Objektanlage
└── .github/workflows/          CI: Lint und Tests beider Seiten, Kontraktpruefung, Image-Build, Schema-Reflektionstest gegen MariaDB
```

## 5. Tragfaehigkeit der Performance-Vorgabe

Vorgabe (CR 7 und 14): 10.000 Seiten gemischt in unter 3 Stunden inklusive OCR, Klassifikation und Ablage; Review Center antwortet waehrenddessen unter 2 Sekunden. Erforderlicher Durchsatz: 10.000 Seiten / 180 min = 55,6 Seiten/min.

### 5.1 Planungsgroessen

| Kennung | Groesse | Wert | Verifikation |
|---|---|---|---|
| ANNAHME A1 | Sekunden je gescannter A4-Seite bei 300 dpi, Tesseract deu, ein Prozess, ein Thread | 3,5 s (Spanne 2 bis 5 s) | M0: time ocrmypdf -l deu --jobs 1 auf 50 echten Scanseiten auf dem VPS |
| ANNAHME A2 | Sekunden je Digital-PDF-Seite (Textebene vorhanden, pdftotext plus Analyse) | 0,25 s | M0: gleiche Messung mit Digital-PDF |
| ANNAHME A3 | Anteil Scan-Seiten am Objektkorpus | 60 Prozent (Spanne 40 bis 80 Prozent) | Erstes echtes Objekt: has_text_layer je Seite auswerten |
| ANNAHME A4 | Skalierungseffizienz bei P parallelen Prozessen (Speicherbandbreite, I/O) | 85 Prozent | M0: Messung mit P = 1, 2, C minus 1 |
| ANNAHME A5 | Mittlere Seitenzahl je Dokument | 8 Seiten, also 1.250 Dokumente je 10.000 Seiten | Erstes echtes Objekt |
| ANNAHME A6 | Zeit je Drive-API-Aufruf (Download, Verschiebung, Upload) | 0,6 s bei Parallelitaet 2 | M2: Messung gegen echtes Drive-Konto |
| ANNAHME A7 | NER plus lokaler Klassifikator je Dokument | 0,3 s | M4: Messung auf dem VPS |
| ANNAHME A8 | Anteil Eskalationen an externe KI und Antwortzeit | 25 Prozent der Dokumente, 4 s je Aufruf, Parallelitaet 4 | M5: Messung; Anteil sinkt mit Trainingsdaten |
| ANNAHME A9 | Speicherspitze je OCR-Prozess | 0,4 bis 0,6 GiB | M0: docker stats unter Last |

Der Server (Kerne, RAM, Platte) ist aus der Entwicklungsumgebung nicht erreichbar (Befund 2). Kernzahlen unten sind Szenarien, keine Annahmen ueber den Tarif.

### 5.2 Rechenweg

CPU-Sekunden OCR im Basisfall: 6.000 Scanseiten mal 3,5 s = 21.000 s; 4.000 Digitalseiten mal 0,25 s = 1.000 s; Summe 22.000 s. Wandzeit OCR = 22.000 s / (P mal 0,85).

| Szenario Kerne C | P = C minus 1 | Basisfall (A1 3,5 s, A3 60 Prozent) | Ungünstig (A1 5 s, A3 80 Prozent: 40.500 CPU-s) | Guenstig (A1 2 s, A3 40 Prozent: 9.500 CPU-s) |
|---|---|---|---|---|
| 4 | 3 | 8.627 s = 144 min | 15.882 s = 265 min, Ziel verfehlt | 3.725 s = 62 min |
| 6 | 5 | 5.176 s = 86 min | 9.529 s = 159 min | 2.235 s = 37 min |
| 8 | 7 | 3.697 s = 62 min | 6.807 s = 113 min | 1.597 s = 27 min |

Uebrige Schritte laufen parallel zur OCR (Pipeline) und bestimmen nur den Nachlauf: Download 1.250 Dokumente mal 0,6 s / 2 = 6 min (vor der OCR), Klassifikation 1.250 mal 0,3 s = 6 min auf einem leichten Prozess, externe KI 313 Aufrufe mal 4 s / 4 = 5 min, Ablage 1.250 mal 0,6 s / 2 = 6 min, Listen unter 1 min. Nachlauf nach der letzten OCR-Seite: ANNAHME A10 10 bis 15 min. Ergebnis: Mit P groesser oder gleich 5 wird die Vorgabe im Basis- und im unguenstigen Fall erfuellt; mit P = 3 nur im Basis- und guenstigen Fall. Die Messung in M0 entscheidet, ob Stellhebel gezogen werden muessen, bevor gebaut wird.

Zwingende Konstruktionsentscheidung: Grosse Einzeldokumente (etwa eine Gesamtabrechnung mit 2.000 Seiten) muessen in Seitenbereiche aufgeteilt werden (pikepdf, Chunk-Groesse konfigurierbar, ANNAHME A11 100 Seiten), sonst laeuft ein solches Dokument auf einem Kern 2.000 mal 3,5 s = 117 min seriell und reisst das Ziel unabhaengig von P. Die Chunks laufen als eigene Jobs im Stream, der Merge erzeugt die Seitentexte je Originalseite. Dieselbe Seitenbereichslogik traegt die relationale Zuordnung von Einzelabrechnungen (CR 6).

### 5.3 Stellhebel bei Verfehlung, nach Aufwand geordnet

1. Sprachdaten pruefen: ist das installierte deu-Paket aus tessdata_fast oder tessdata_best? fast ist deutlich schneller bei geringfuegig niedrigerer Genauigkeit. Nur deu laden, nicht deu plus eng.
2. ocrmypdf ohne Bildoptimierung (--optimize 0), ohne clean/deskew im Standardpfad, Scans mit mehr als 300 dpi vor der Erkennung herunterrechnen.
3. Dubletten ueber Hash vor der OCR aussortieren, Digital-PDF-Seiten konsequent ueberspringen (--skip-text), OCR-Cache ueber Laeufe hinweg behalten.
4. Zwei-Phasen-Verarbeitung: Klassifikation aus den ersten drei Seiten, vollstaendige OCR der Restseiten nachgelagert mit niedrigerer Prioritaet. Aendert, was "verarbeitet" heisst; braucht Entscheidung des Auftraggebers.
5. Mehr Kerne (Tarifwechsel): OCR skaliert nahezu linear mit P.
6. Zweiter Worker-Host: Das Stream-Design erlaubt es, dasselbe Worker-Image auf einem zweiten Server gegen dieselbe Redis und DB laufen zu lassen. Voraussetzung ist ein gemeinsamer oder synchronisierter Transitspeicher, also zusaetzliche Betriebskomplexitaet.

### 5.4 Review Center unter 2 Sekunden waehrend der Verarbeitung

Massnahmen: CPU-Limit des Workers auf C minus 1 und cpu_shares 256 gegen 1024 fuer web und db; OCR-Prozesse mit nice 10 und ionice Klasse Idle, OCR-Temp auf Platte statt RAM; Worker schreibt je Dokument eine Transaktion (etwa 1.250 in drei Stunden), keine Schreiblast je Seite; Sessions und Cache in Redis; Livewire-Fortschrittsanzeige pollt hoechstens alle 5 Sekunden und liest aggregierte Zaehler aus einer kleinen Statistiktabelle statt aus pipeline_jobs; Listenansichten paginiert, alle Filterspalten indiziert; InnoDB-Buffer-Pool nach Formel. Messung im Performance-Test: Locust mit 5 gleichzeitigen Nutzern gegen Objektansicht, Review-Liste, Suche und Detailansicht, Kriterium p95 unter 2 s waehrend des 10.000-Seiten-Laufs, dazu iostat fuer Platten-Wartezeiten.

## 6. Meilensteine

Aufwaende sind grobe Spannen in Personentagen (PT) fuer einen erfahrenen Entwickler und enthalten den Polyglott-Mehraufwand (Vertraege, zwei Suiten). ANNAHME A12: Gesamtaufwand 65 bis 100 PT; wird nach M3 anhand der Ist-Werte neu geschaetzt.

| Nr. | Inhalt | Pruefbare Abnahme | PT |
|---|---|---|---|
| M0 Messung und Grundgeruest | measure-server.sh auf dem VPS (nproc, free, df, Docker-Netze, Traefik-Werte), OCR-Benchmark A1/A2/A4/A9, Repo-Skelett, Compose mit Platzhaltern, CI, contracts-gen, leere Laravel-App mit /up, Python-Worker mit Heartbeat | Messprotokoll in docs/betrieb liegt vor; docker compose up -d auf frischem Checkout startet alle Dienste mit gruenen Healthchecks; App unter https://uebernahme.muellerhv.de mit gueltigem TLS; Entscheidung ueber P dokumentiert | 4 bis 6 |
| M1 Kernmodell, Auth, Rollen, Audit, Einstellungen | Migrationen fuer alle Tabellen aus 2.5 mit down(), Fortify mit TOTP, Socialite Google (Domain-Beschraenkung), Rollen und Policies, audit_log mit Trigger, settings mit Katalog und Admin-UI, Aufbewahrungsfristen leer mit Hinweistext | Login nur mit 2FA moeglich; Google-Login verknuepft nur vorhandene Nutzer; Admin aendert Einstellung, Worker liest sie binnen 30 s; migrate:rollback laeuft fehlerfrei rueckwaerts; Audit-Zeile nicht aenderbar | 6 bis 9 |
| M2 Drive-Adapter und Ordnerabgleich | OAuth-Einrichtung mit Anleitung, Token verschluesselt, Refresh und Ablaufanzeige, DriveClient mit Backoff, FolderCache, Reconciler nach CR 9 inkl. Dry-Run, Erkennung ueber Nummer, Legacy-Alias 05_Sonstiges aus settings, Objektanlage nach Namensmuster | Integrationstests mit FakeDrive fuer die drei Testobjekte aus CR 14 (ohne Unterordner, mit Altordner und Dateien, vollstaendig), Dateianzahl vorher gleich nachher, zweiter Lauf ohne Aenderung, Review-Eintrag bei doppelter Nummer; Token-Refresh ueber 8 Tage nachgewiesen (laeuft parallel weiter) | 7 bis 10 |
| M3 Pipeline-Skelett polyglott | Streams, Nachrichtenschemas, Publisher, bridge-Consumer, Python-Consumer mit Claim, Heartbeat, XAUTOCLAIM, Dead Letter, OCR mit Chunking und Cache, Statusseite, Reconcile-Kommando | Testobjekt mit 500 Seiten laeuft durch; Container-Neustart mitten im Lauf fuehrt zu keiner Doppelverarbeitung (Zaehler in pipeline_jobs und OCR-Cache); Kontrakttests beider Seiten gruen; Schema-Reflektionstest gruen | 8 bis 12 |
| M4 Klassifikation Stufe 1 und 2 | Regelwerk (Dateiname, Adresse, Verwaltungsart, bekannte Personen und Einheiten), NER und Extraktoren, Normalisierung der Einheitenbezeichnungen (Praefix-Mapping aus settings), TF-IDF-Klassifikator mit Kalibrierung, Abgrenzungsregel CR 6, historische Zuordnung, Kandidatenlisten | Unit-Tests: Abgrenzung mit mindestens 20 Beispieldokumenten, historische Zuordnung (Dokument 03/2025 bei Wechsel 01.07.2026 zum Alteigentuemer), Benennungsfunktion alle Faelle aus CR 4; Klassifikation je Dokument unter 50 ms ohne OCR | 8 bis 12 |
| M5 Stufe 3 externe KI | Provider-Interface, OpenAI und Anthropic, Maskierung, Tokenbegrenzung, Primaer/Fallback, Kosten und Token je Objekt, Schwellwert aus settings | Unit-Test IBAN-Maskierung; Integrationstest Ausfall Primaeranbieter, Fallback liefert identisches Ergebnisformat; Kostenlimit stoppt Aufrufe und erzeugt Review-Eintrag | 4 bis 6 |
| M6 Review Center | Zielbereich 01 bis 06, Pflichtfelder bei 05, Suche mit Vorschlaegen aus owners und units, Kandidatenliste, Massenbearbeitung mit Vorschau, Speicherung als training_samples, automatische Eintraege fuer 06_Sonstiges, Protokollierung je Aktion | Feature-Tests: jede Aktion erzeugt review_actions und audit_log; Massenbearbeitung von 40 Einzelabrechnungen in einem Schritt; Ablage in 06 erzeugt Eintrag; p95 der Seiten unter 2 s im Leerlauf | 8 bis 12 |
| M7 Import der Eigentuemerlisten | Parser fuer PDF (Scan, digital), XLSX, CSV, Formatprofile Immoware24 und Domus, Namens-Splitter mit Konfidenz, Vorschlaege ins Review Center, Uebernahme erst nach Bestaetigung | Testdateien je Format (synthetisch) liefern erwartete Vorschlaege; unsichere Zeilen markiert, keine stillschweigend verworfen; Stammtabellen bleiben bis zur Bestaetigung leer | 6 bis 9 |
| M8 Listen, Requirement Engine, Nachforderung | Eigentuemer- und Mieterliste als XLSX und PDF im HVM-CI (A4 quer), Drive-Aktualisierung ueber File-ID, Entprellung, Vollstaendigkeitspruefung CR 12, Nachforderungsentwurf | Nach zwei Laeufen genau eine Datei je Format und Liste; Historie-Blatt enthaelt Alteigentuemer; keine vollstaendige IBAN in der Ausgabe; PDF-Kopf vollstaendig; Offene-Punkte-Blatt identisch mit Requirement Engine | 7 bis 10 |
| M9 Suche, Reporting, KPI | Suche ueber alle Objekte nach Eigentuemer, Einheit, Zeitraum, Unterart; Bericht je Objekt mit Vollstaendigkeitsstatus, offenen Review-Faellen, Anteil 06_Sonstiges | Suchtests mit Umlauten und Kuerzeln; KPI-Wert stimmt mit Zaehlung ueberein | 3 bis 5 |
| M10 Performance, Backup, Deployment, Doku | Korpus 10.000 Seiten gemischt, Lauf auf dem VPS mit Messung (Seiten/min, RAM-Spitze, Platte), Locust parallel, Backup und Restore auf leerer Instanz, Server-Neustart, Rollback per Befehl, Bedienungsanleitung, Grep-Pruefung 05_Sonstiges, Ordnerabgleich aller Bestandsordner mit Protokoll | Alle Punkte aus CR 14 Performance-, Deployment-Test und Definition of Done mit Nachweis im Bericht | 5 bis 8 |

Reihenfolge ist bewusst: M2 vor M3, weil der Worker ohne Dateien aus Drive nichts zu tun hat und der Ordnerabgleich unabhaengig vom Worker testbar ist. M3 vor M4, damit die Sprachgrenze steht, bevor Fachlogik auf beiden Seiten waechst.

## 7. Ehrliche Schwaechen und Risiken dieses Stacks fuer dieses Projekt

1. Zwei Sprachen fuer einen Entwickler. Jede Aenderung an Dokumentunterarten, Enums, Einstellungen oder Nachrichten beruehrt PHP und Python. Der Generator und die Vertragstests senken das Risiko, kosten aber laufend Zeit. ANNAHME A13: Mehraufwand 15 bis 25 Prozent gegenueber einer einsprachigen Loesung; wird nach M3 anhand der Ist-Zeiten ueberprueft. Vertretung und spaetere Uebergabe brauchen jemanden, der Laravel und das Python-ML-Werkzeug beherrscht.
2. Ein Schema, zwei Datenzugriffe. Reflektion faengt fehlende Spalten, nicht geaenderte Bedeutung. Ein Feld wie documents.status wird von beiden Seiten geschrieben; die Zustandsmaschine muss in contracts/enums.json und in beiden Codebasen identisch bleiben.
3. Die sprachuebergreifende Queue ist Eigenbau. Redis Streams liefern Bestaetigung und Wiederzustellung, aber kein Dashboard, keine Wiederholungsstrategie und keine Dead-Letter-Sicht wie Horizon oder Celery. Beides muss in der Statusseite und im Reconcile-Kommando selbst gebaut und ueberwacht werden.
4. Fachlogik an der Grenze. Klassifikation braucht Stammdatenwissen (Python liest owners, units, assignments), Ordnerbenennung braucht das Klassifikationsergebnis (Laravel). Die Normalisierung von Einheitenbezeichnungen (WE 14 gleich WE14) wird im Import (Python) und in Suche und Review Center (PHP) gebraucht. Loesung ist, dass der Worker normalisierte Schluessel in die DB schreibt und PHP nur liest; wo das nicht reicht, entsteht doppelte Logik mit Golden-Tests.
5. Listen und Nachforderung entstehen asynchron. Ein Klick "Liste neu erzeugen" laeuft ueber Stream, Worker, Ereignis und Drive-Upload. Das dauert Sekunden bis zu einer Minute und Fehler kommen verzoegert an. Die Oberflaeche muss das sichtbar machen (Auftragsstatus), sonst wirkt die Anwendung defekt.
6. Fehlersuche ueber zwei Laufzeiten und bis zu neun Container. Ohne Korrelations-IDs in jeder Logzeile ab dem ersten Tag ist ein haengendes Dokument schwer zu verfolgen.
7. Zwei Wartungskalender. Laravel bringt jaehrlich eine Hauptversion, Livewire eigene Zyklen, PHP-Images Sicherheitsupdates; Python-seitig sind spaCy-Modelle an spaCy-Versionen gebunden und ocrmypdf an Ghostscript- und Tesseract-Staende. Ein Upgrade der einen Seite darf die Vertraege nicht brechen, also immer beide Suiten laufen lassen.
8. PHP als Langzeitprozess (bridge, Horizon) neigt zu Speicherwachstum. Geplanter Selbstneustart nach N Nachrichten oder einer Stunde ist Pflicht, nicht Option.
9. Speicherdruck durch Modellkopien. Jeder leichte Prozess haelt spaCy und Klassifikator im Speicher. Bei knappem RAM bleibt L = 1, oder der optionale Container classifier wird eingefuehrt, was einen weiteren Dienst bedeutet.
10. Kaltstart des lokalen Klassifikators. Ohne Trainingsdaten faellt Stufe 2 anfangs schwach aus, die Eskalationsquote an externe KI und damit Kosten und Review-Aufwand sind zu Beginn hoeher als im Ziel. Das ist stackunabhaengig, wirkt hier aber auf zwei Seiten (Trainingsdaten sammelt PHP, trainiert wird in Python).
11. Lizenzhinweis: Ghostscript (Abhaengigkeit von ocrmypdf) steht unter AGPL, Redis hat seine Lizenz geaendert. Fuer rein internen Betrieb ohne Weitergabe der Software unkritisch; bei einer spaeteren Weitergabe an Dritte vorher pruefen lassen.
12. Die Performance-Vorgabe haengt an der ungemessenen Kernzahl. Bei P = 3 ist das Ziel nur im Basisfall erreichbar (Abschnitt 5.2). Dieses Risiko traegt jeder Stack, hier kommt hinzu, dass Web- und Worker-Prozesse zweier Laufzeiten um denselben RAM konkurrieren.

## 8. Betriebsaufwand fuer einen Entwickler und 2 bis 5 Anwender

| Taetigkeit | Rhythmus | Werkzeug | Aufwand (ANNAHME A14, zu verifizieren nach drei Betriebsmonaten) |
|---|---|---|---|
| Backup pruefen (Zeitstempel, Groesse), monatlich Restore-Probe auf Testinstanz | taeglich automatisch, Kontrolle woechentlich, Probe monatlich | backup-Container, restore.sh, Statusseite | 0,5 h je Woche, 1 h je Monat |
| Sicherheitsupdates: Basis-Images neu bauen, composer audit, pip-audit, Laravel- und Python-Patchstaende | monatlich, bei kritischen Meldungen sofort | CI-Build, deploy.sh, rollback.sh | 2 bis 4 h je Monat |
| Hauptversionen Laravel, Livewire, spaCy, ocrmypdf | jaehrlich gebuendelt | Beide Testsuiten, Kontrakttests | 2 bis 4 PT je Jahr |
| OAuth-Token und Drive-Quota im Blick | automatisch mit Warnung auf Statusseite | Scheduler, E-Mail bei Ablauf unter 3 Tagen | nur bei Alarm |
| KI-Kosten und Eskalationsquote je Objekt pruefen, Schwellwert nachjustieren | je Objekt kurz, monatlich Trend | Reporting, ai_calls | 0,5 h je Monat |
| Lokalen Klassifikator aus Review-Korrekturen neu trainieren und Modellversion freigeben | monatlich oder ab N neuen Trainingsdaten | model.train-Job, Vergleich alter gegen neuer Modellversion auf Holdout | 1 h je Monat |
| Haengende Jobs, Dead Letter, Review-Rueckstau | woechentlich Blick auf Statusseite | pipeline:status, Horizon-Dashboard | 0,5 h je Woche |
| Nutzerverwaltung, 2FA-Reset, Rollen | anlassbezogen | Admin-UI | minimal |
| Log-Durchsicht bei Fehlern | anlassbezogen | docker logs, JSON-Felder, Korrelations-ID | anlassbezogen |

Summe im eingeschwungenen Zustand: ANNAHME A14 0,5 bis 1 PT je Monat plus 2 bis 4 PT je Jahr fuer Hauptversionen. Der Polyglott-Anteil daran (zwei Update-Pfade, zwei Audit-Werkzeuge, Vertragstests bei jedem Update) ist ANNAHME A15 etwa ein Drittel dieses Aufwands. Lizenzkosten fallen nicht an; alle genannten Bibliotheken sind quelloffen. Laufende Fremdkosten: VPS, KI-Aufrufe (nach Kostenlimit je Provider begrenzt), Google Workspace (bereits vorhanden).

Voraussetzungen des Auftraggebers, ohne die M0 bis M2 nicht abgeschlossen werden koennen (aus CR 0.1 und Befund): SSH-Zugang zum VPS fuer die Messung, DNS-Eintrag, OAuth-App als interne Workspace-Anwendung, Auftragsverarbeitungsvertraege mit OpenAI und Anthropic samt EU-Region und Trainings-Opt-out, Uebergabe der HVM-CI-Assets (Logo, ReportLab-Bausteine) in das Repository.
