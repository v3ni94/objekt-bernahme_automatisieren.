# Stack-Vorschlag A: Python-Monolith (Django, HTMX, Celery, Redis, MariaDB)

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Projekt Objektübernahme, Change Request CR-05. Blickwinkel dieses Vorschlags: betriebliche Einfachheit, ein einziger Stack, ein Entwickler, 2 bis 5 Anwender.

Stand: 10.09.2026. Faktenbasis sind ausschließlich der CR-05 und die Befundakte. Jede Planungsgröße, die nicht aus diesen Quellen stammt, ist mit dem Präfix ANNAHME gekennzeichnet und nennt, wie sie im Projekt verifiziert wird. Anhang A führt alle Annahmen zusammen, Anhang B die offenen Fragen an den Auftraggeber.

Versionshinweis: Dieses Dokument nennt bewusst keine Versionsnummern. Für alle Bibliotheken und Werkzeuge gilt: aktuelle LTS bzw. stabile Version zum Umsetzungszeitpunkt prüfen und im Lockfile festschreiben.

---

## 1. Kurzfassung

1. Stack: Python durchgängig, Django in der LTS-Linie mit serverseitig gerenderten Templates und HTMX, Celery mit Redis als Broker, MariaDB, Tesseract und ocrmypdf im Worker, ein TF-IDF-Klassifikator mit scikit-learn, ReportLab und openpyxl für die Listen, google-api-python-client für Drive, die offiziellen SDKs von OpenAI und Anthropic hinter einer eigenen Schnittstelle, alles aus einem Docker-Image und einer Compose-Datei.
2. Kernidee: Ein Codebestand, ein Image, eine Sprache; web, worker und beat starten dasselbe Image mit unterschiedlichem Startbefehl, die Datenbank ist die einzige Wahrheit für Job-Status, Konfiguration und Audit, und Redis transportiert nur IDs, nie Dokumentinhalte.
3. Stärkste Eigenschaft: der geringste Lern- und Betriebsaufwand für einen einzelnen Entwickler, weil Django ORM, Migrationen, Authentifizierung, Formulare, Berechtigungen und Test-Runner ohne weitere Grundsatzentscheidungen mitbringt und OCR, NLP, Klassifikation, Excel, PDF und beide KI-SDKs im selben Ökosystem liegen.
4. Größte Schwäche: Der OCR-Durchsatz ist rein CPU-gebunden und der Server ist nicht vermessen; ob 10.000 Seiten in unter 3 Stunden erreichbar sind, entscheidet die gemessene Kernzahl des VPS und die tatsächliche Scanqualität, nicht der Stack, und dieser Vorschlag kann das erst im Meilenstein 4 auf dem Zielserver belegen.
5. Zweite Schwäche: Celery mit Redis arbeitet nach dem Prinzip mindestens einmal, das heißt, ohne die im CR geforderte Idempotenz über Datei-Hash und Job-Status in der Datenbank entstünden Doppelverarbeitungen; der Stack liefert diese Disziplin nicht mit, sie muss in jedem Pipeline-Schritt bewusst gebaut und getestet werden.

---

## 2. Technologie-Stack

### 2.0 Entscheidungsübersicht

| Baustein | Wahl | Geprüfte Alternative im selben Blickwinkel | Ausschlaggebend |
|---|---|---|---|
| Sprache | Python | Node.js oder Java als Einzelstack | ocrmypdf, spaCy, scikit-learn, ReportLab, beide KI-SDKs, Drive-Client in einem Ökosystem |
| Web-Framework | Django (LTS) | FastAPI mit Jinja2 | Auth, ORM, Migrationen, Formulare, Berechtigungen ohne Eigenbau |
| Frontend | Django-Templates plus HTMX | Vollseiten-Reload ohne JS; React als zweiter Stack | Teilaktualisierungen ohne Build-Kette |
| ORM, Migration | Django ORM, Django Migrations | SQLAlchemy plus Alembic | eine Konvention, reversible Migrationen eingebaut |
| Task-Queue | Celery mit Redis-Broker | Huey, RQ, Dramatiq, Django-Q2 | Prozesspool mit Speicher-Recycling, Zeitlimits, Routing, Beat |
| Datenbank | MariaDB | MySQL | freie Lizenzlage, gleichwertige Django-Unterstützung, Werkzeugkette |
| OCR | Tesseract deu plus ocrmypdf (CR-Vorgabe) | keine, Vorgabe | Sidecar-Text je Seite, Skip-Text für Mischdokumente |
| NER | Muster plus Gazetteer aus DB, spaCy als Ergänzung | nur spaCy; Transformer-Modelle | Präzision bei WE-Nummern, IBAN, Beträgen, bekannten Namen |
| Klassifikator | TF-IDF plus lineares Modell (scikit-learn) | Satz-Embeddings plus kNN | Millisekunden, kein Torch, erklärbar, schnelles Nachtraining |
| KI-Provider | eigene Schnittstelle über offizielle SDKs | LiteLLM oder LangChain | schmale Abhängigkeit, volle Kontrolle über Endpunkt, Region, Kosten |
| Excel | openpyxl | XlsxWriter | eine Bibliothek für Lesen (Importe) und Schreiben (Listen) |
| PDF | ReportLab | WeasyPrint | vorhandene HVM-CI-Bausteine sind ReportLab, keine Rendering-Engine im Image |
| Drive | google-api-python-client, google-auth | PyDrive2 | resumable Upload, Versionierung per update, supportsAllDrives |
| Auth | Django-Auth plus django-allauth (Konto, MFA, Google) | django-two-factor-auth plus social-auth | E-Mail-Login, TOTP und Google-Login aus einem Paket |
| Rollen | Django-Gruppen und Berechtigungen | django-guardian (objektbezogen) | zwei Rollen, keine Objektrechte nötig |
| Audit | eigene Append-only-Tabelle über Service-Schicht | django-auditlog | fachliche Ereignisse statt Modell-Diffs |
| Konfiguration | eigene DB-Tabelle mit Historie und Cache | django-constance | strukturierte Werte (Listen, Mappings), Änderungshistorie |
| Volltext | MariaDB InnoDB FULLTEXT über eigenen Lookup | Meilisearch als Zusatzdienst | kein weiterer Dienst, Suche primär über Metadaten |
| Tests | pytest, pytest-django, factory_boy, Drive-Fake | unittest | Fixtures, Parametrisierung, Markierung von Integrationstests |

### 2.1 Sprache: Python

Wahl: Python, aktuelle stabile Version zum Umsetzungszeitpunkt prüfen. Werkzeuge: uv oder pip-tools für ein Lockfile, ruff für Lint und Format, mypy optional für die Kernmodule (Benennungsregel, Abgrenzungsregel, Maskierung).

Alternative im selben Blickwinkel: Node.js oder Java als Einzelstack. Node hätte für OCR nur Subprozess-Aufrufe, für Klassifikation und NER eine dünne Bibliotheksbasis und keine ocrmypdf-Entsprechung. Java hätte Tesseract-Bindungen, aber ebenfalls kein ocrmypdf, und die vorhandenen HVM-CI-Bausteine (ReportLab, Befund Abschnitt 5) wären neu zu schreiben. Python ist zudem die Sprache von ocrmypdf selbst.

Ehrliche Schwäche: Der Interpreter parallelisiert CPU-Arbeit nur über Prozesse. Das passt zur OCR (ohnehin ein Prozess je Dokument), kostet aber Speicher je Prozess. Die Speicherformel in Abschnitt 3 rechnet damit.

### 2.2 Web-Framework: Django statt FastAPI mit Jinja2

Wahl: Django in der aktuellen LTS-Linie, ausgeliefert über gunicorn, statische Dateien über WhiteNoise (kein zusätzlicher nginx-Container, TLS terminiert Traefik).

Gegen FastAPI plus Jinja2: FastAPI bringt ein schnelles asynchrones HTTP-Gerüst und OpenAPI, aber kein ORM, keine Migrationen, kein Session- und Passwortmanagement, keine Formulare mit CSRF-Schutz, kein Berechtigungsmodell, keinen Admin. Jeder dieser Punkte wäre eine eigene Entscheidung plus eigener Code plus eigene Wartung. Für 2 bis 5 Anwender und serverseitiges Rendering bringt Asynchronität keinen Nutzen, die lange Arbeit läuft ohnehin in Celery.

Was Django für dieses Projekt konkret liefert: Modelle und Migrationen für owners, units, owner_unit_assignments und die Mieterentsprechungen; Authentifizierung mit Passwort-Hashing; Gruppen und Berechtigungen für Admin und Sachbearbeiter; Formulare mit Validierung für das Review Center; Management-Commands für Seed, Abgleich, Wiederaufnahme und Performance-Lauf; Test-Runner mit Datenbank-Fixtures.

Ehrliche Schwäche: Django ist synchron und vergleichsweise schwer. Lang laufende Aktionen dürfen nie im Request laufen, sondern immer als Celery-Task mit Fortschrittsanzeige. Der mitgelieferte Django-Admin ist ein Werkzeug für den Entwickler und den Admin, nicht die Oberfläche für Sachbearbeiter; die Versuchung, ihn als Review Center zu missbrauchen, ist eine bekannte Falle und wird hier ausdrücklich ausgeschlossen.

### 2.3 Template- und Frontend-Ansatz: Django-Templates plus HTMX

Wahl: Serverseitig gerenderte Templates. HTMX (als statische Datei im Repository, kein Build-Schritt) für Teilaktualisierungen: Fortschrittsanzeige der Objektverarbeitung per Polling, Eigentümer- und Einheitensuche mit Vorschlägen im Review Center, Nachladen der Pflichtfelder bei Zielbereich 05_Eigentümerakte, Vorschau der Massenbearbeitung. Ein leichtgewichtiges CSS-Framework als statische Datei. Dokumentvorschau über den PDF-Viewer des Browsers, die Datei wird von Django als Stream ausgeliefert, der Seitenbezug per Fragment (Seite N) im Link.

Gegen Vollseiten-Reload ohne JavaScript: Das Review Center lebt von kleinen, schnellen Korrekturen an vielen Dokumenten. Ein kompletter Seitenaufbau je Klick wäre zumutbar, aber spürbar langsamer in der Bedienung und würde den Fortschritt der Verarbeitung nicht ohne manuelles Neuladen zeigen.

Gegen eine getrennte Single-Page-App (React, Vue): Das wäre ein zweiter Stack mit eigener Build-Kette, eigenem Abhängigkeitsbaum, eigener Authentifizierung über eine API und eigenem Deployment. Der CR erlaubt das ausdrücklich, aber es widerspricht dem Blickwinkel dieses Vorschlags und verdoppelt den Wartungsaufwand für einen Entwickler.

Ehrliche Schwäche: Sehr interaktive Funktionen stoßen an Grenzen. Das Zuschneiden von Seitenbereichen (Gesamtabrechnung mit eingebetteten Einzelabrechnungen) wird als Formular mit Seitenzahlen und Vorschau gebaut, nicht als grafischer Editor. Es gibt kein Server-Push, Fortschritt kommt per Polling in einem Intervall von wenigen Sekunden. Für die 2-Sekunden-Vorgabe genügt das, für ein Echtzeitgefühl nicht.

### 2.4 ORM und Migrationswerkzeug: Django ORM und Django Migrations

Wahl: Django ORM mit dem MySQL-Backend (gilt für MariaDB), Treiber mysqlclient, Zeichensatz utf8mb4, strikter SQL-Modus.

Gegen SQLAlchemy plus Alembic: technisch gleichwertig, in Verbindung mit FastAPI naheliegend, aber ein zweites Konzept neben Django. Django Migrations erzeugen für Schemaänderungen automatisch die Rückwärtsoperation; Datenmigrationen erhalten eine ausdrückliche Rückwärtsfunktion oder einen dokumentierten Rollback. Das erfüllt die CR-Regel, dass jede Migration reversibel ist oder einen dokumentierten Rollback hat, ohne Zusatzwerkzeug.

Ehrliche Schwäche: JSON-Felder und Volltext verhalten sich auf MariaDB anders als auf PostgreSQL, für das viele Django-Beispiele geschrieben sind. Unit-Tests dürfen deshalb nicht auf SQLite laufen, wenn sie diese Felder berühren; die Integrationstests laufen gegen MariaDB im Test-Compose.

### 2.5 Task-Queue: Celery mit Redis-Broker

Wahl: Celery, Broker Redis, kein Ergebnis-Backend (task_ignore_result), der Job-Status steht ausschließlich in der Datenbank. Tasks tragen nur IDs als Argumente. Zwei Warteschlangen: cpu (Ingest, OCR, Extraktion, Klassifikation Stufe 1 und 2) und io (Drive-Operationen, KI-Aufrufe Stufe 3, Listenerzeugung). Prefork-Pool mit Speicher-Recycling (worker_max_tasks_per_child, worker_max_memory_per_child), weiche und harte Zeitlimits je Task, worker_prefetch_multiplier 1, task_acks_late und task_reject_on_worker_lost aktiv, visibility_timeout des Redis-Transports deutlich über dem harten Zeitlimit des längsten Tasks.

Pipeline als Zustandsautomat statt als Celery-Chain: Jeder Schritt (ingest, ocr, extract, classify, classify_ai, file_document, regenerate_lists) prüft zu Beginn den Job-Status in der Datenbank, arbeitet, setzt den Status und reiht den Folgeschritt ein. Ein Abgleich-Task (alle fünf Minuten über beat sowie beim Start des worker-Containers) reiht Jobs erneut ein, die zu lange in queued oder running stehen. Damit ist die Wiederaufnahme nach Abbruch oder Neustart eine Datenbankabfrage, keine Broker-Magie, und die Idempotenz über Datei-Hash und Job-Status liegt an einer Stelle.

Gegen leichtere Queues: RQ ist einfacher, forkt aber je Job einen Prozess, hat keinen eingebauten Scheduler und kein Speicher-Recycling. Huey ist schlank und hat periodische Tasks, aber weniger Steuerung über Pool, Zeitlimits und Routing. Dramatiq ist sauber entworfen, hat aber eine kleinere Gemeinschaft und weniger Django-Anbindung. Django-Q2 nutzt das ORM als Broker und wäre der einfachste Betrieb (kein Redis), skaliert aber die OCR-Last über Datenbank-Polling und ist bei Prozesspool-Steuerung schwächer. Das in neueren Django-Versionen eingeführte Tasks-Framework ist zum Umsetzungszeitpunkt auf Reifegrad und verfügbare Backends zu prüfen; heute wird es hier nicht vorausgesetzt.

Warum trotzdem Celery: Für die OCR zählen genau die Eigenschaften, die die leichteren Werkzeuge nicht oder nur teilweise haben: fest dimensionierter Prozesspool (Standard gemessene Kerne minus 1, CR Abschnitt 7), Recycling von Kindprozessen gegen Speicherwachstum externer Programme, harte Zeitlimits gegen hängende Tesseract-Aufrufe, Routing der IO-Arbeit in eine zweite Warteschlange mit eigener Parallelität, Beat für Abgleich, Token-Prüfung und Nachtraining.

Ehrliche Schwäche: Celery hat eine große Konfigurationsfläche, und die Kombination aus Redis-Transport und lang laufenden Tasks ist eine bekannte Fehlerquelle: Überschreitet ein Task das visibility_timeout, wird er erneut zugestellt und läuft doppelt. Die Gegenmaßnahmen sind kleine Tasks (ein Dokument, nie ein Objekt), Zeitlimits unterhalb des visibility_timeout und der Status-Check zu Beginn jedes Tasks. Das ist beherrschbar, aber es ist Disziplin, kein Automatismus.

### 2.6 Datenbank: MariaDB statt MySQL

Der CR erlaubt beides. Wahl: MariaDB, offizielles Image, persistentes benanntes Volume, utf8mb4, InnoDB.

Gründe gegenüber MySQL: freie Lizenzlage ohne Zwei-Lizenzen-Modell und ohne Bindung an einen einzelnen Hersteller; Django unterstützt beide Backends gleichwertig über dasselbe Backend-Modul; die Werkzeugkette für Backup (mariadb-dump) ist im Image enthalten und für das Backup-Skript ausreichend; das Healthcheck-Skript des offiziellen Images vereinfacht depends_on mit condition service_healthy.

Was MySQL besser kann und hier bewusst in Kauf genommen wird: MySQL bietet einen n-Gramm-Parser für den Volltextindex, der Teilwortsuche erleichtert; ob MariaDB in der eingesetzten Version einen gleichwertigen Parser bietet, ist zum Umsetzungszeitpunkt zu prüfen. Die Suche im CR (Eigentümer, Einheit, Zeitraum, Dokumentunterart) läuft primär über indizierte Metadatenspalten; der Volltext ist Ergänzung. JSON-Spalten sind in MariaDB ein Alias auf Text mit Gültigkeitsprüfung; die Unterstützung von JSONField durch die eingesetzte Django-Version auf MariaDB ist zu prüfen und wird im Datenmodell sparsam genutzt (Konfigurationswerte, Audit-Details, Klassifikationsergebnisse).

Konfiguration: innodb_buffer_pool_size als Formel aus dem Speicherlimit des Containers (Abschnitt 3); Stoppwortliste des Volltextindex für Deutsch anpassen oder deaktivieren, da die Standardliste englisch ist; Mindesttokenlänge so wählen, dass Kürzel wie WE3 gefunden werden.

### 2.7 OCR: Tesseract mit deu und ocrmypdf

Vorgabe des CR, keine Alternative geprüft. Umsetzung im Worker-Image: Systempakete tesseract-ocr mit Sprachpaket deu, ghostscript, qpdf; ocrmypdf und pikepdf als Python-Pakete. Aufruf je Dokument als Subprozess mit Parametern: skip-text (nur Seiten ohne Textebene werden erkannt, Digital-PDFs und Mischdokumente laufen damit automatisch durch denselben Pfad), language deu (weitere Sprachen konfigurierbar), jobs 1 (Parallelität steuert Celery, nicht ocrmypdf), Sidecar-Textdatei (liefert den erkannten Text mit Seitentrennung, daraus entsteht die Seitentabelle), Zeitlimit je Seite konfigurierbar, output-type pdf als Standard (PDF/A-Konvertierung kostet Zeit und ist konfigurierbar), deskew, clean und rotate-pages standardmäßig aus (jeweils zusätzliche Durchläufe, konfigurierbar).

Textextraktion für Seiten, die bereits eine Textebene haben: pypdfium2 oder pdfplumber, Auswahl im Meilenstein 4 nach einer kurzen Messung, Tendenz pypdfium2 wegen Geschwindigkeit. Dateierkennung über python-magic. Hash SHA-256 über die Originaldatei im Ingest-Schritt, eindeutig je Objekt.

Weitere Eingangsformate der Eigentümerlisten (CR Abschnitt 15): Excel über openpyxl, CSV über die Standardbibliothek mit Trennzeichenerkennung, Exporte aus Verwaltungssoftware über Formatprofile (Spaltenzuordnung als Konfiguration). Word-Dateien werden mit python-docx als Text gelesen; eine Konvertierung nach PDF über LibreOffice ist bewusst nicht im Standard-Image, da sie das Image erheblich vergrößert; falls fachlich nötig, als optionaler Zusatz im Worker.

Ehrliche Schwäche: Tesseract ist bei schlechten Scans (schief, Rauschen, Handschrift, Stempel über Text) begrenzt. Die abgeschalteten Vorverarbeitungen verbessern die Qualität, kosten aber Zeit. Die Klassifikation muss mit fehlerhaftem Text umgehen; deshalb Zeichen-n-Gramme im Klassifikator (2.9) und Toleranz im Namensabgleich (2.8).

### 2.8 Named-Entity-Erkennung: Muster und Gazetteer zuerst, spaCy als Ergänzung

Wahl in dieser Reihenfolge:

1. Deterministische Muster: WE-Nummern und Einheitenbezeichnungen nach dem konfigurierbaren Präfix-Mapping aus dem Befund (WE, Wohnung, GE, S, ST, STP, SP, Stellplatz, GA, Garage, TG, mit Normalisierung von WE 14 und WE14), IBAN mit Prüfziffernvalidierung (Bibliothek schwifty oder eigene Modulo-97-Prüfung), Beträge im deutschen Format, Datumsangaben und Zeiträume (Abrechnungsjahr, Wirtschaftsjahr, Stichtage), Aktenzeichen und Kundennummern als konfigurierbare Muster.
2. Gazetteer aus der Datenbank: bekannte Eigentümer, Mieter, Einheiten, Vertragspartner und die Objektadresse des jeweiligen Objekts, abgeglichen mit rapidfuzz (unscharfer Vergleich, Schwellwert konfigurierbar), damit OCR-Fehler in Namen toleriert werden. Das deckt die Stufe 1 des CR (bekannte Eigentümer, Mieter, Einheiten) ab und ist für die Eigentümerzuordnung präziser als ein statistisches Modell.
3. spaCy mit dem mittelgroßen deutschen Modell für Personen- und Organisationsnamen, die noch nicht in der Datenbank stehen (neue Eigentümer, Kanzleien, Dienstleister). Ergebnis nur als Kandidat für das Review Center, nie als stille Übernahme.

Gegen nur spaCy: Auf verrauschtem OCR-Text und bei Kürzeln wie WE 03 oder ST 12 ist ein allgemeines NER-Modell unzuverlässig, Muster und Gazetteer sind hier präziser und schneller. Gegen Transformer-Modelle (Flair, BERT-basierte NER): deutlich bessere Erkennung freier Namen, aber Torch-Abhängigkeit, größeres Image, höhere Latenz je Dokument und mehr Speicher je Worker-Prozess; in diesem Blickwinkel nicht gerechtfertigt, solange die Gazetteer-Stufe den Regelfall trägt.

ANNAHME: spaCy verarbeitet ein zehnseitiges Dokument auf einem Kern in unter einer Sekunde. Verifizierung im Meilenstein 5 durch Messung im Performance-Lauf; bei Verfehlung wird spaCy nur auf die ersten Seiten je Dokument angewendet.

### 2.9 Lokaler Klassifikator: TF-IDF plus lineares Modell

Wahl: scikit-learn, Merkmale aus Wort- und Zeichen-n-Grammen (TF-IDF) über den bereinigten Text der ersten Seiten plus Dateiname plus die aus 2.8 gewonnenen Entitätsmerkmale (Anzahl WE-Nummern, IBAN vorhanden, Zeitraum vorhanden, Treffer auf bekannten Eigentümer, Treffer auf Objektadresse). Modell: logistische Regression oder linearer SVM mit Kalibrierung, damit die Konfidenz als Wahrscheinlichkeit interpretierbar ist. Ziel ist ein flaches Label aus Hauptordner plus Unterordner (zum Beispiel 05_Eigentümerakte/05_Abrechnungen), die Dokumentunterart als zweites Modell oder als Regel je Unterordner. Die Abgrenzungsregel aus CR Abschnitt 6 (Gesamtobjekt vor Gesamtbuchhaltung vor Eigentümerbezug) läuft als deterministische Vorstufe und kann ein Modellergebnis überstimmen; die Reihenfolge ist im Code als eigene Funktion mit den geforderten 20 Beispieldokumenten getestet.

Laufzeit: Inferenz im Millisekundenbereich je Dokument, Modell wenige Megabyte, Training auf einigen hundert bis tausend Beispielen in Sekunden auf CPU. Deshalb kein eigener classifier-Container im ersten Ausbau: das Modell wird je Worker-Prozess einmal geladen. Nachtraining als beat-Task nachts und auf Knopfdruck im Admin-Bereich; jede bestätigte Korrektur aus dem Review Center wird als Trainingsdatum gespeichert (CR Abschnitt 11). Modellartefakte versioniert unter /srv/objektakte/models mit Kennzahlen in der Datenbank; die aktive Version ist ein Konfigurationswert.

Gegen Satz-Embeddings plus kNN oder logistische Regression: Embedding-Modelle generalisieren bei wenigen Beispielen besser und verstehen Synonyme (Hausgeldabrechnung, Einzelabrechnung, Jahresabrechnung). Kosten: Torch oder ONNX-Runtime im Image, Modell-Download, ANNAHME 50 bis 200 Millisekunden je Dokument auf CPU (Verifizierung nur, falls dieser Pfad gewählt wird), mehr Speicher je Prozess, schwächere Erklärbarkeit. Der CR verlangt Millisekunden je Dokument und CPU-Fähigkeit; beides erfüllt TF-IDF sicher, Embeddings nur mit Aufwand. Der optionale classifier-Container (Abschnitt 3) ist der vorbereitete Ausbaupfad, falls die Trefferquote nach den ersten realen Objekten nicht ausreicht.

Ehrliche Schwäche: Kaltstart. Ohne gelabelte Beispiele kann das Modell nichts. Die ersten Objekte laufen überwiegend über Stufe 1 (Regeln) und Stufe 3 (externe KI, kostenpflichtig), und die Reviewer erzeugen die ersten Trainingsdaten. Seltene Unterordner (10_Vollmachten, 03_SEPA) erreichen erst spät genug Beispiele; bis dahin trägt die Regelstufe. Die Kennzahl Anteil 06_Sonstiges unter 5 % ist in den ersten Wochen nicht zu erwarten und sollte als Zielwert nach Einlernphase verstanden werden.

### 2.10 KI-Provider-Abstraktion: eigene Schnittstelle über die offiziellen SDKs

Wahl: Eine kleine Schnittstelle im Paket ai mit genau der vom CR geforderten Signatur: Eingabe bereinigter Textauszug (Tokenzahl begrenzt, IBAN und Kontonummern maskiert), Ausgabe Kategorie, Unterart, Konfidenz, Begründung, dazu Token-Verbrauch und Kosten. Zwei Implementierungen über die offiziellen Python-SDKs openai und anthropic, jeweils mit strukturierter Ausgabe gegen ein JSON-Schema, damit das Ergebnisformat beider Anbieter identisch ist. Je Provider konfigurierbar (DB-Konfiguration, Schlüssel nur aus Umgebungsvariablen): Modellkennung, Endpunkt bzw. Basis-URL, Region soweit der Anbieter das anbietet, Timeout, Anzahl Wiederholungen, Kostenlimit je Objekt und je Tag. Fallback-Logik: Primäranbieter, bei Zeitüberschreitung, Ratenlimit, Serverfehler oder Verbindungsfehler (typisierte Fehlerklassen der SDKs) Wechsel auf den Fallback-Anbieter innerhalb desselben Tasks; Ergebnis mit Anbieter, Modell, Latenz, Tokens und Kosten in der Tabelle ai_calls protokolliert; bei Erreichen des Kostenlimits wird der Task nicht ausgeführt, das Dokument geht mit Kandidatenliste ins Review Center.

Gegen LiteLLM oder LangChain: Beide abstrahieren viele Anbieter und viel mehr Funktionsumfang als hier nötig. Das bedeutet eine große zusätzliche Abhängigkeit mit eigener Release-Kadenz, mehr Angriffsfläche und weniger Kontrolle über die Details, die der CR ausdrücklich fordert (Maskierung vor Übermittlung, Kostenprotokoll je Objekt, konfigurierbare Region und Endpunkt). Zwei Anbieter mit je einer Methode rechtfertigen keine Zwischenschicht.

Kostenkontrolle: Der Preis je Token ist je Modell konfigurierbar und wird zur Laufzeit multipliziert; keine Preisliste im Code. Voraussetzung vor Produktivstart (CR): Auftragsverarbeitungsverträge mit beiden Anbietern, Datenresidenz EU und Opt-out aus Training durch den Auftraggeber geprüft; im Plan als Vorbedingung geführt.

### 2.11 Excel-Erzeugung: openpyxl

Wahl: openpyxl für die Listen nach CR Abschnitt 12a: drei Blätter Aktuell, Historie, Offene Punkte; Formatierung als Tabelle mit Filter, fixierte Kopfzeile, Beträge als Zahl mit zwei Nachkommastellen, Daten als Datum. Spalten aus einer Konfigurationsliste (administrativ erweiterbar), damit Reihenfolge und Zusatzspalten ohne Codeänderung anpassbar sind.

Gegen XlsxWriter: schneller und mit sehr guter Formatierungskontrolle, aber ausschließlich schreibend. openpyxl wird ohnehin zum Lesen der Eigentümerlisten der Vorverwaltung gebraucht; eine Bibliothek für beides ist im Blickwinkel dieses Vorschlags der Ausschlag. Die Listen haben hunderte, nicht hunderttausende Zeilen; der Geschwindigkeitsvorteil von XlsxWriter spielt keine Rolle.

### 2.12 PDF-Erzeugung: ReportLab

Wahl: ReportLab. Die HVM-CI liegt laut Befund als ReportLab-Skript mit Bausteinen vor (Kennlinie, Logo, Fußzeile mit Pflichtangaben, Folgeseite, Anschriftfeld, Infoblock, Betreff, Unterschriftsblock). Das Skript zeichnet direkt auf den Canvas und hat A4 hoch fest eingetragen. Für die Listen wird es nach src/hvm_ci portiert und um eine Seitengrößenvariable ergänzt (A4 quer über landscape(A4)), die Tabellen entstehen über Platypus LongTable mit wiederholter Kopfzeile, Kopf und Fuß je Seite über die portierten Bausteine als Seitenrückruf. Nachforderungsschreiben nutzen dieselben Bausteine in A4 hoch. Farben und Schriften ausschließlich aus den Werten der CI, keine eigenen.

Gegen WeasyPrint: HTML und CSS als Vorlage wären für Tabellen mit Zeilenumbruch bequemer, und Django-Templates könnten wiederverwendet werden. Dagegen sprechen: Die CI-Bausteine müssten in CSS und SVG neu gebaut werden, es gäbe zwei Rendering-Pfade (ReportLab für Briefe, WeasyPrint für Listen), das Image braucht zusätzliche Systembibliotheken für Text-Layout und Rendering, und die Rendering-Zeit ist höher. Im Blickwinkel eines einzigen Stacks ist die Wiederverwendung vorhandener, freigegebener CI-Bausteine der Ausschlag.

Ehrliche Schwäche: Die Eigentümerliste hat im Mindestumfang über zwanzig Spalten. Auf A4 quer mit der CI-Schriftgröße 10 bis 11 pt passt das nicht auf eine Zeile je Datensatz. Optionen: kleinere Schrift in der Tabelle (Abweichung von der CI-Regel, Freigabe nötig), Aufteilung in zwei Tabellenblöcke je Einheit (Stammdaten und Vertragsdaten), oder Umbruch innerhalb der Zellen mit mehrzeiligen Datensätzen. Das ist eine Gestaltungsentscheidung des Auftraggebers (Anhang B).

### 2.13 Google-Drive-Anbindung: google-api-python-client und google-auth

Wahl: Offizieller Client. OAuth-2.0-Ablauf über google-auth-oauthlib im Admin-Bereich (Start-Route, Callback-Route mit der im CR genannten Redirect-URI unter https://uebernahme.muellerhv.de/...), Anforderung eines Refresh-Tokens (offline access), Scope drive wie im CR begründet. Speicherung des Tokens verschlüsselt in der Datenbank (Fernet aus der Bibliothek cryptography, Schlüssel aus der Umgebung), automatischer Refresh durch google-auth, Persistierung nach jedem Refresh, tägliche Prüfung durch beat, Anzeige von Gültigkeit und letztem Refresh im Statusbereich, Warnung bei Refresh-Fehler.

Adapter drive mit einer schmalen Protokollklasse (list_children, find_object_folder, ensure_folder, upload, update_version, rename, move, count_files) und drei Implementierungen: echter Client, Dry-Run-Client (protokolliert geplante Operationen, schreibt nichts, CR Abschnitt 9.5), In-Memory-Fake für Tests. Alle Aufrufe mit supportsAllDrives und includeItemsFromAllDrives, damit ein späterer Umzug in eine geteilte Ablage ohne Codeänderung möglich ist (Befund Abschnitt 6.6). Resumable Upload über MediaFileUpload; neue Version einer bestehenden Datei über files.update mit Medieninhalt (CR 12a: eine Datei je Format, Drive-Versionierung statt Neuanlage). Exponentielles Backoff mit Zufallsanteil bei Ratenlimit (HTTP 403 mit entsprechendem Grund, 429) und 5xx, Retry-After wird beachtet, Anzahl Versuche konfigurierbar. Deterministische Upload-Reihenfolge nach Objekt, Zielordner, Dateiname, Hash. Folder-IDs in der Tabelle drive_folders mit Prüfzeitpunkt; bei 404 wird der Eintrag verworfen und neu aufgelöst.

Gegen PyDrive2: bequemer, aber eine Zwischenschicht mit weniger Kontrolle über resumable Uploads, Versionierung, Felderauswahl und geteilte Ablagen. Der offizielle Client ist die kleinere Abhängigkeit.

Ehrliche Schwäche: Drive ist kein Dateisystem. Es gibt keine Transaktionen über Umbenennen und Verschieben, Listungen können kurz nach Schreibvorgängen veraltet sein, und die Quota ist projektbezogen. Der Ordnerabgleich zählt deshalb vorher und nachher (CR 9.3) und ist idempotent, und alle Drive-Operationen laufen in der io-Warteschlange mit niedriger Parallelität.

### 2.14 Authentifizierung: Django-Auth plus django-allauth

Wahl: django-allauth mit den Modulen Konto (Anmeldung per E-Mail und Passwort, Selbstregistrierung deaktiviert, Nutzer werden vom Admin angelegt), MFA (TOTP mit Recovery-Codes; Funktionsumfang des Moduls in der zum Umsetzungszeitpunkt aktuellen Version prüfen) und Google-Provider (Anmeldung über Google Workspace, nur für bereits angelegte Nutzer, nur für die konfigurierte Workspace-Domain, Startwert muellerhv.de). Erzwingung der Zwei-Faktor-Einrichtung über eine Middleware, die Nutzer ohne aktivierten zweiten Faktor auf die Einrichtungsseite leitet. Admin kann den zweiten Faktor eines Nutzers zurücksetzen (Support-Fall, protokolliert). Passwortregeln über Django-Validatoren, Ratenbegrenzung für Anmeldeversuche, Sitzungsdauer konfigurierbar, sichere Cookies, Proxy-Header für TLS hinter Traefik.

Gegen django-two-factor-auth plus social-auth-app-django: beide ausgereift, aber zwei Pakete mit zwei Konfigurationsmodellen für E-Mail-Login, TOTP und Google. allauth deckt alle drei Anforderungen aus einer Hand ab.

Ehrliche Schwäche: allauth ist ein großes Paket mit eigenem Template-Satz, der an die Oberfläche angepasst werden muss. Änderungen an der Bibliothek in Major-Versionen haben in der Vergangenheit Anpassungen erfordert; die Version wird im Lockfile fixiert und nur geplant angehoben.

### 2.15 Rollen: Django-Gruppen und Berechtigungen

Wahl: Zwei Gruppen admin und sachbearbeiter, Rechte als Django-Berechtigungen, geprüft in Views und Service-Funktionen. Keine objektbezogenen Rechte, weil alle Sachbearbeiter alle Objekte bearbeiten sollen; django-guardian wäre der Ausbaupfad, falls das kommt.

| Funktion | admin | sachbearbeiter |
|---|---|---|
| Objekte anlegen, Ordnerabgleich Dry-Run | ja | ja |
| Ordnerabgleich im Schreibmodus | ja | konfigurierbar |
| Review Center, Massenbearbeitung | ja | ja |
| Eigentümerakten einsehen (maskierte IBAN) | ja | ja |
| Vollständige IBAN entschlüsselt anzeigen (protokolliert) | ja | konfigurierbar, Standard nein |
| Nachforderungen erzeugen | ja | ja |
| Listen manuell auslösen | ja | ja |
| Konfiguration ändern, Provider, Schwellwerte | ja | nein |
| Nutzerverwaltung, MFA-Reset | ja | nein |
| Löschungen (nur nach Löschkonzept) | ja | nein |
| Drive-Konto verbinden | ja | nein |

### 2.16 Audit-Protokoll: eigene Append-only-Tabelle

Wahl: Tabelle audit_events mit Zeitstempel, Akteur (Nutzer, System oder Task), Aktion als fester Bezeichner, Entitätstyp und Entitäts-ID, Objektbezug für die Filterung, Details als JSON (nur die geänderten Felder, keine Volltexte, keine IBAN), Request-ID bzw. Task-ID. Geschrieben ausschließlich über eine Funktion der Service-Schicht; im Anwendungscode gibt es keinen Pfad zum Ändern oder Löschen, optional zusätzlich ein Datenbank-Trigger gegen UPDATE und DELETE. Protokolliert werden alle Review-Center-Aktionen (CR Abschnitt 15), Anzeige entschlüsselter IBAN (CR Abschnitt 10), Konfigurationsänderungen, Drive-Abgleich mit Zähl- und Umbenennungsergebnis (CR 9.3), Anmeldungen und MFA-Ereignisse, Provider-Wechsel. Das Statusfeld jedes Datensatzes (bestätigt, KI-Vorschlag, unvollständig) verweist auf das erzeugende Ereignis; daraus entsteht die Spalte Quelle der Listen.

Gegen django-auditlog oder django-simple-history: automatisieren Modell-Diffs, erfassen aber nicht die fachlich relevanten Aktionen ohne Modelländerung (IBAN angezeigt, Dry-Run ausgeführt, Fallback-Anbieter verwendet) und erzeugen viel Rauschen. Fachliche Ereignisse explizit zu schreiben ist mehr Disziplin, aber die Auswertung ist verständlich für Nichttechniker.

### 2.17 Admin-Konfiguration: DB-gestützt, zur Laufzeit änderbar

Wahl: App config mit Tabelle app_settings (Schlüssel, Wert als JSON, Typ, Gruppe, Beschreibung, Änderungszeitpunkt, Änderer) und app_settings_history. Zugriff über eine typisierte Funktion mit Standardwert, kurzer Cache je Prozess mit Versionsschlüssel in Redis zur Invalidierung; Worker-Prozesse sehen Änderungen spätestens nach dem Cache-Intervall. Oberfläche im Admin-Bereich als eigene Seiten mit Validierung je Schlüssel (JSON-Schema). Startwerte über ein Seed-Kommando aus Dateien unter src/apps/config/seed/, dort stehen wortgetreu die Strukturen des CR: die sechs Hauptordner 01_Legitimationsunterlagen bis 06_Sonstiges, die elf Unterordner der Eigentümerakte, die vier Unterordner von 06_Sonstiges, das Namensmuster NNN Ort, Straße Hausnummer, das Präfix-Mapping der Einheitentypen, die Schwellwerte, duplicate_owner_documents_in_drive mit Standard false, Provider-Einstellungen ohne Schlüssel, Aufbewahrungsfristen mit leeren Werten und dem CR-Hinweistext, und der Alt-Alias für den früheren Namen des Auffangordners (Befund 6.4: so bleibt das Literal aus dem Anwendungscode heraus, die Grep-Prüfung der Definition of Done bezieht sich auf src ohne das Seed-Verzeichnis).

Gegen django-constance: schnell eingerichtet und mit Admin-Anbindung, aber auf flache Skalare ausgelegt. Die Konfiguration hier besteht überwiegend aus Listen und Zuordnungen (Ordnerstrukturen, Präfix-Mappings, Spaltenlisten, Provider-Blöcke) und braucht eine Änderungshistorie für das Audit. Eine eigene kleine App ist hier weniger Aufwand als das Verbiegen einer Bibliothek.

Nicht zur Laufzeit änderbar, sondern nur über .env mit Neustart: Anzahl der OCR-Prozesse (bestimmt die Poolgröße des worker-Containers), Datenbank- und Redis-Verbindungen, Schlüssel und Secrets. Das wird in der Oberfläche als Hinweis angezeigt.

### 2.18 Volltextsuche: MariaDB FULLTEXT über einen eigenen Lookup

Wahl: Tabelle document_text mit dem maskierten Text je Dokument (IBAN und Kontonummern sind vor dem Speichern maskiert, CR Abschnitt 10) und einem InnoDB-Volltextindex. Abfrage über einen kleinen eigenen Django-Lookup, der MATCH AGAINST im Boolean-Modus erzeugt, kombiniert mit den indizierten Metadatenfiltern Eigentümer, Einheit, Zeitraum, Dokumentunterart, Objekt. Der Volltextindex wird in einem eigenen, niedrig priorisierten Schritt nach der OCR befüllt, damit die Indexpflege während eines Objektlaufs nicht die Antwortzeit des Review Centers belastet.

Gegen einen Suchdienst (Meilisearch, OpenSearch): bessere Tippfehlertoleranz und Sprachbehandlung, aber ein weiterer Container mit eigenem Speicher, eigener Sicherung und eigener Synchronisation. Für die im CR beschriebene Suche über Metadaten plus Volltext als Ergänzung ist das im Blickwinkel dieses Vorschlags nicht gerechtfertigt; der Adapter search ist so gebaut, dass ein Suchdienst später als Ausbaupfad angeschlossen werden kann.

Ehrliche Schwäche: Kein deutsches Stemming, keine Zerlegung zusammengesetzter Wörter, Teilwortsuche nur über Präfix-Platzhalter. Wer Hausgeldabrechnung sucht, findet nicht automatisch Abrechnung. Das ist der Preis für den Verzicht auf einen weiteren Dienst.

### 2.19 Tests

Werkzeuge: pytest, pytest-django, factory_boy für Testdaten, freezegun für Zeitbezüge (historische Eigentümerzuordnung), HttpMockSequence aus dem Google-Client für Adapter-Tests auf HTTP-Ebene, der In-Memory-Drive-Fake aus 2.13 für Integrationstests der Pipeline und des Ordnerabgleichs, Celery im synchronen Modus für Unit-Tests der Tasks und ein echter Worker im Test-Compose für Abbruch- und Wiederaufnahmetests.

Unit-Tests (CR Abschnitt 14): Ordnerbenennung alle Fälle aus Abschnitt 4 (WE01_Nachname, WE03_Nachname1-Nachname2 alphabetisch, ab vier Namen WE03_Nachname1-ua, Unbekannte_WE_Nachname, Unzugeordnet, Firmenkurzname, synthetische Beispiele wie WE03_Mustermann), Abgrenzungsregel mit mindestens 20 Beispieldokumenten, historische Zuordnung (Dokument 03/2025 bei Wechsel 01.07.2026 geht zum Alteigentümer), IBAN-Maskierung vor dem KI-Aufruf, Objektnummer-Erkennung mit 2 bis 6 Stellen (Befund Abschnitt 4), Namens-Splitter mit Konfidenz, Präfix-Mapping der Einheiten. Zusätzlich ein Test, der src ohne das Seed-Verzeichnis nach dem Alt-Literal durchsucht und leer sein muss (Definition of Done).

Integrationstests gegen MariaDB und Redis im Test-Compose: Ordnerabgleich mit den drei Testobjekten aus dem CR jeweils als Dry-Run und Ausführung, Dateianzahl vorher gleich nachher, zweiter Lauf ohne Änderungen; Objektordner-Erkennung, Anlage, Review-Eintrag bei doppelter Nummer; Gesamtabrechnung mit 12 Einzelabrechnungen als eine Masterdatei plus 12 relationale Zuordnungen ohne Drive-Duplikate; Pipeline-Abbruch (Worker wird hart beendet) und Wiederaufnahme ohne Doppelverarbeitung; Provider-Wechsel bei simuliertem Ausfall mit identischem Ergebnisformat; Listenerzeugung mit zwei Läufen, genau eine Datei je Format, Historie-Blatt mit Alteigentümer, keine vollständige IBAN.

Performance-Test: Generator unter tests/performance erzeugt ein synthetisches Objekt mit 10.000 Seiten (Mischung aus gerasterten Seiten ohne Textebene und Digital-PDFs, Anteile konfigurierbar, keine realen Daten). Management-Command perf_run fährt die Pipeline mit dem Drive-Fake oder dem Dry-Run-Client, misst Seiten je Minute je Schritt, ein Skript sammelt parallel die Containerwerte (CPU, RAM-Spitze, Plattenverbrauch), ein zweites Skript ruft in einem Intervall von zwei Sekunden die Review-Center-Seiten auf und protokolliert Median und 95. Perzentil der Antwortzeit. Ergebnis im Messprotokoll unter docs/betrieb.

Deployment-Test: wie im CR, als Checkliste in docs/betrieb mit Ausfüllfeldern.

---

## 3. Container-Aufteilung in Compose

### 3.1 Grundsätze

Ein Image (docker/app.Dockerfile) für web, worker, worker-io und beat, damit Code, Migrationen und Abhängigkeiten immer identisch sind. Basis: offizielles Python-Slim-Image auf Debian-Basis, mehrstufiger Build (Build-Stufe mit Compiler für mysqlclient, Laufzeit-Stufe mit tesseract-ocr, tesseract-ocr-deu, ghostscript, qpdf). Der Preis ist, dass der web-Container OCR-Binärdateien trägt, die er nie nutzt; das kostet Plattenplatz, aber keine Laufzeit, und spart einen zweiten Build-Pfad.

Netzwerke: backend (Bridge, nicht als internal markiert, weil worker und web ausgehend Google Drive und die KI-Anbieter erreichen müssen; kein Container veröffentlicht Host-Ports, daher trotzdem nicht von außen erreichbar) und das bestehende externe Traefik-Netz, an dem ausschließlich web hängt. Name des Traefik-Netzes, Cert-Resolver und Entrypoint werden nicht angenommen, sondern auf dem Server ausgelesen und in .env eingetragen (Befund Abschnitt 2).

Persistenz: benannte Volumes für db und queue; Bind-Mounts unter /srv/objektakte/ für transit (Eingang, Originale), ocr_cache (OCR-Ausgaben, Sidecar-Texte), tmp (Arbeitsverzeichnis von ocrmypdf), models (Klassifikator) und backup. Nichts im Container.

Vorschlag zur Abweichung vom CR-Container-Katalog: ein zweiter Worker-Dienst worker-io aus demselben Image für die io-Warteschlange (Drive, KI-Aufrufe, Listen). Begründung: Netzwerkgebundene Arbeit soll keine OCR-Prozesse belegen, und die Drive-Quota verlangt eine eigene, niedrige Parallelität. Alternative ohne zweiten Dienst: ein einzelner worker-Container konsumiert beide Warteschlangen mit einem Pool; einfacher, aber Uploads warten dann hinter OCR-Tasks. Entscheidung liegt beim Umsetzungsplan, Empfehlung ist der zweite Dienst.

### 3.2 Dienste

| Dienst | Aufgabe | Image-Basis | Volumes | Netze | Healthcheck | Restart |
|---|---|---|---|---|---|---|
| web | Django über gunicorn, Review Center, Admin, OAuth-Callback, Statusseite, Dateivorschau | app-Image | transit (rw für Uploads), ocr_cache (ro) | backend, Traefik-Netz | HTTP GET auf /healthz/ (prüft DB und Redis) über einen Python-Einzeiler im Container | unless-stopped |
| worker | Celery-Prozesspool für Warteschlange cpu: ingest, ocr, extract, classify | app-Image | transit (rw), ocr_cache (rw), tmp (rw), models (rw) | backend | celery inspect ping auf den eigenen Knoten, Intervall 60 s | unless-stopped |
| worker-io | Celery-Pool für Warteschlange io: Drive, KI Stufe 3, Listen | app-Image | transit (ro), ocr_cache (rw für Listen-Ausgabe), models (ro) | backend | wie worker | unless-stopped |
| beat | Zeitplan: Job-Abgleich alle 5 min, Token-Prüfung täglich, Nachtraining nachts, Bereinigung tmp | app-Image | keine | backend | Prozessprüfung auf den beat-Prozess | unless-stopped |
| queue | Redis als Broker und Cache-Versionsschlüssel; appendonly ein, maxmemory-policy noeviction (ein Broker darf nie Nachrichten verwerfen) | offizielles Redis-Image (Alpine-Variante) | redis-data | backend | redis-cli ping | unless-stopped |
| db | MariaDB, utf8mb4, InnoDB, eigene my.cnf-Fragmente | offizielles MariaDB-Image | db-data | backend | Healthcheck-Skript des Images (Verbindung und InnoDB initialisiert) | unless-stopped |
| backup | cron im Container: täglicher Dump über das Netz vom db-Dienst, tar der Bind-Mounts transit, ocr_cache, models, Aufbewahrung in Tagen aus .env, Wiederherstellungsskript | MariaDB-Image (enthält mariadb-dump) plus cron und Skripte | backup (rw), transit, ocr_cache, models (ro) | backend | Alter der jüngsten Sicherungsdatei unter 26 h | unless-stopped |
| classifier (optional, nicht im ersten Ausbau) | Eigener HTTP-Dienst für ein Embedding-Modell, falls TF-IDF nicht ausreicht | app-Image mit Zusatzabhängigkeiten oder eigenes Image | models (ro) | backend | HTTP GET /healthz | unless-stopped |

Logging: Alle Dienste schreiben JSON-Zeilen nach stdout (Django, Celery und Skripte über einen gemeinsamen JSON-Formatter mit Request-ID bzw. Task-ID, ein Filter maskiert IBAN-Muster vor der Ausgabe). Docker-Treiber json-file mit Rotation (max-size, max-file) in der Compose-Datei. Die Statusseite in web zeigt je Objekt Fortschritt, Fehler, Anteil 06_Sonstiges, Token-Gültigkeit und die letzten Sicherungen.

Startreihenfolge: web, worker, worker-io und beat warten über depends_on mit condition service_healthy auf db und queue. Migrationen laufen nicht automatisch beim Start, sondern als dokumentierter Deployment-Schritt (Abschnitt 8), damit ein fehlgeschlagener Schema-Schritt nicht in einer Neustartschleife endet.

### 3.3 Ressourcenlimits als Formel

Messgrößen vom Server (Befund: noch nicht erhoben, Befehle nproc, free -h, df -h): C = Anzahl Kerne, M = Arbeitsspeicher in GiB, D = freier Plattenplatz in GiB.

| Dienst | CPU-Limit | Speicherlimit | Herleitung |
|---|---|---|---|
| worker | P = max(1, C-1) | P × m_ocr + 0,5 GiB | P nach CR-Standard; m_ocr = ANNAHME 0,75 GiB je OCR-Prozess (ocrmypdf plus Tesseract auf einer A4-Seite bei 300 dpi), Verifizierung in Meilenstein 4 über docker stats während perf_run, Messwert ersetzt Annahme |
| worker-io | 0,5 | 0,5 GiB | vier bis sechs Threads oder Prozesse für Netzwerkarbeit |
| web | 1,0 (Limit), cpu_shares hoch | 1 GiB | ANNAHME: gunicorn mit 3 synchronen Workern je etwa 150 bis 250 MiB, verifizieren über docker stats; für 2 bis 5 Anwender ausreichend |
| db | 1,0 | max(1 GiB, 0,15 × M) | innodb_buffer_pool_size = 0,5 × Speicherlimit |
| queue | 0,5 | 0,25 GiB | Broker transportiert nur IDs; maxmemory knapp unter dem Limit |
| beat | 0,25 | 128 MiB | Zeitplaner ohne Last |
| backup | 0,5 | 0,25 GiB | Dump und tar; nur nachts aktiv |

Bedingung: Summe der Speicherlimits ≤ 0,85 × M, der Rest bleibt für Host, Traefik und Seitencache. Ist die Bedingung verletzt, wird P verringert, bis sie gilt; P ist dann kleiner als C-1 und wird so in .env dokumentiert.

CPU-Priorität: Limits sind Obergrenzen, keine Garantien. Damit web unter Volllast des Workers antwortet, erhält web über cpu_shares ein deutlich höheres Gewicht als worker (zum Beispiel Faktor 4), db ein mittleres. Zusätzlich ist P = C-1 bereits so gewählt, dass rechnerisch ein Kern für web, db und queue frei bleibt. Bei kleiner Kernzahl (C ≤ 4) ist P = C-2 zu erwägen und im Performance-Test zu vergleichen.

Platte: OCR-Ausgaben sind größer als die Eingaben. ANNAHME: Faktor 1,5 bis 2 gegenüber dem Original bei output-type pdf, Verifizierung in Meilenstein 4. Der Ingest prüft vor dem Start eines Objekts den freien Platz unter /srv/objektakte gegen eine konfigurierbare Reserve und bricht mit Statusmeldung ab, statt die Platte zu füllen.

Rechenbeispiel mit frei gewählten Werten, ausdrücklich keine Aussage über den VPS: Bei C = 6 und M = 12 GiB ergäbe sich P = 5, Speicher worker 5 × 0,75 + 0,5 = 4,25 GiB, db 1,8 GiB, web 1 GiB, übrige Dienste zusammen etwa 0,9 GiB, Summe rund 8 GiB, das sind 66 % von M und damit innerhalb der Bedingung.

### 3.4 Compose-Gerüst (Auszug, Werte aus .env)

```yaml
name: objektakte

x-app: &app
  image: objektakte/app:${IMAGE_TAG}
  build: {context: ., dockerfile: docker/app.Dockerfile}
  env_file: .env
  restart: unless-stopped
  logging: {driver: json-file, options: {max-size: "20m", max-file: "5"}}
  networks: [backend]

services:
  web:
    <<: *app
    command: gunicorn objektakte.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS} --timeout 60
    networks: [backend, traefik]
    volumes: ["/srv/objektakte/transit:/data/transit", "/srv/objektakte/ocr_cache:/data/ocr_cache:ro"]
    labels:
      traefik.enable: "true"
      traefik.docker.network: ${TRAEFIK_NETWORK}
      traefik.http.routers.objektakte.rule: Host(`${APP_DOMAIN}`)
      traefik.http.routers.objektakte.entrypoints: ${TRAEFIK_ENTRYPOINT_SECURE}
      traefik.http.routers.objektakte.tls.certresolver: ${TRAEFIK_CERTRESOLVER}
      traefik.http.services.objektakte.loadbalancer.server.port: "8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=5).status == 200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
    depends_on:
      db: {condition: service_healthy}
      queue: {condition: service_healthy}
    cpu_shares: 1024
    deploy: {resources: {limits: {cpus: "${WEB_CPUS}", memory: "${WEB_MEM}"}}}

  worker:
    <<: *app
    command: celery -A objektakte worker -Q cpu --concurrency ${OCR_PROCESSES} --prefetch-multiplier 1 --max-tasks-per-child ${WORKER_MAX_TASKS_PER_CHILD}
    volumes: ["/srv/objektakte/transit:/data/transit", "/srv/objektakte/ocr_cache:/data/ocr_cache", "/srv/objektakte/tmp:/data/tmp", "/srv/objektakte/models:/data/models"]
    healthcheck:
      test: ["CMD-SHELL", "celery -A objektakte inspect ping -d celery@$$HOSTNAME --timeout 10"]
      interval: 60s
      timeout: 20s
      retries: 3
    depends_on:
      db: {condition: service_healthy}
      queue: {condition: service_healthy}
    cpu_shares: 256
    deploy: {resources: {limits: {cpus: "${WORKER_CPUS}", memory: "${WORKER_MEM}"}}}

  queue:
    image: redis:<stabile Version zum Umsetzungszeitpunkt>-alpine
    command: redis-server --appendonly yes --maxmemory ${REDIS_MAXMEMORY} --maxmemory-policy noeviction
    volumes: ["redis-data:/data"]
    healthcheck: {test: ["CMD", "redis-cli", "ping"], interval: 10s, timeout: 5s, retries: 5}

  db:
    image: mariadb:<stabile Version zum Umsetzungszeitpunkt>
    env_file: .env
    volumes: ["db-data:/var/lib/mysql", "./docker/db/conf.d:/etc/mysql/conf.d:ro"]
    healthcheck: {test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"], interval: 10s, timeout: 5s, retries: 10}

networks:
  backend: {driver: bridge}
  traefik: {external: true, name: "${TRAEFIK_NETWORK}"}

volumes:
  db-data:
  redis-data:
```

Die Dienste worker-io, beat und backup folgen demselben Muster und sind hier aus Platzgründen weggelassen. Die Unterstützung von deploy.resources.limits und cpu_shares durch die installierte Compose-Version ist auf dem Server zu prüfen; die Compose-Datei wird gegen die gemessene Umgebung validiert (docker compose config).

---

## 4. Ordnerstruktur des Repositories

```
<repo-wurzel>/
├── compose.yaml                 Produktion; alle Serverwerte als Variablen aus .env
├── compose.dev.yaml             Override für lokale Entwicklung (Ports, Debug, Test-Mailausgabe)
├── compose.test.yaml            Testprofil mit MariaDB, Redis, Worker und Test-Runner
├── .env.example                 Alle Variablen mit Kommentar, ohne Werte, inkl. Traefik-Platzhalter
├── pyproject.toml               Abhängigkeiten, Lockfile-Quelle, ruff, pytest, mypy
├── manage.py                    Django-Einstieg
├── docker/
│   ├── app.Dockerfile           Ein Image für web, worker, worker-io, beat (mehrstufig, Tesseract deu, ocrmypdf)
│   ├── backup/                  Dockerfile, crontab, backup.sh, restore.sh, Aufbewahrungslogik
│   └── db/conf.d/               MariaDB-Fragmente: utf8mb4, Buffer-Pool aus Variable, Volltext-Stoppwörter
├── docs/
│   ├── anforderungen/           CR-05 (unverändert), spätere CRs
│   ├── architektur/             Stack-Entscheidung, Datenmodell, Entscheidungsprotokolle (ADR)
│   ├── betrieb/                 Deployment, Rollback, Backup und Restore, Messprotokoll Server, Runbook Störungen
│   ├── anleitungen/             Google Cloud Console Schritt für Schritt, Bedienung Review Center und Objektanlage
│   └── plan/                    Umsetzungsplan, Meilensteinstatus, offene Fragen
├── src/
│   ├── objektakte/              Django-Projekt: settings (base, production, development, test), urls, wsgi, celery
│   ├── hvm_ci/                  Portierte CI-Bausteine (ReportLab), Assets (Logo), Farb- und Schriftkonstanten
│   └── apps/
│       ├── accounts/            Nutzer, Gruppen, allauth-Anpassungen, MFA-Erzwingung, Domain-Prüfung Google
│       ├── audit/               audit_events, Schreibfunktion, Ansicht und Filter
│       ├── config/              app_settings, Historie, Cache, Admin-Seiten, seed/ mit CR-Strukturen und Alt-Alias
│       ├── objects/             Objekte, Verwaltungsart, Objektnummer-Erkennung (2 bis 6 Stellen), Statusseite je Objekt
│       ├── parties/             owners, tenants, units, owner_unit_assignments, tenant_unit_assignments, leases, Namens-Splitter
│       ├── documents/           documents, pages, page_ranges, document_text (maskiert), Klassifikationsergebnis, Aufbewahrung
│       ├── pipeline/            Celery-Tasks ingest, ocr, extract, classify, classify_ai, file_document, regenerate_lists; jobs-Tabelle, Abgleich
│       ├── classification/      Stufe 1 Regeln, Abgrenzungsregel, Entitätsmerkmale, TF-IDF-Modell, Training, Kandidatenliste
│       ├── ai/                  Provider-Schnittstelle, OpenAI, Anthropic, Maskierung, Kosten, ai_calls
│       ├── drive/               Protokollklasse, echter Client, Dry-Run, Fake, OAuth, drive_folders, Ordnerabgleich, Benennungsregel
│       ├── review/              Review Center: Warteschlange, Detailansicht, Pflichtfelder 05, Massenbearbeitung, Trainingsdaten
│       ├── requirements/        Requirement Engine (Vollständigkeitsprüfung WEG), Nachforderungsschreiben
│       ├── lists/               Eigentümer- und Mieterlisten: Excel (openpyxl), PDF (ReportLab, A4 quer), Drive-Versionierung
│       ├── imports/             Parser für Eigentümerlisten: PDF, Excel, CSV, Formatprofile (z. B. Immoware24), Vorschläge
│       ├── search/              Volltext-Lookup, Suchansicht über alle Objekte
│       ├── reporting/           KPIs (Anteil 06_Sonstiges, offene Reviews, Vollständigkeit), Statusseite gesamt
│       └── ui/                  Basis-Templates, HTMX-Partials, statische Dateien (CSS, htmx), Formular-Bausteine
├── tests/
│   ├── unit/                    Benennungsregel, Abgrenzung, Historie, Maskierung, Objektnummer, Splitter, Alt-Literal-Prüfung
│   ├── integration/             Ordnerabgleich, Pipeline-Wiederaufnahme, Gesamtabrechnung, Provider-Wechsel, Listen
│   ├── performance/             Generator 10.000-Seiten-Objekt, perf_run-Auswertung, Latenzsonde Review Center
│   └── fixtures/                Synthetische Testdokumente und Formatbeispiele, keine realen Daten
├── scripts/
│   ├── measure_server.sh        nproc, free, df, docker network ls, Traefik-Inspektion; Ausgabe für docs/betrieb
│   ├── deploy.sh                git pull, build mit IMAGE_TAG, up, migrate, Healthcheck-Wartezeit
│   ├── rollback.sh              Vorheriges IMAGE_TAG setzen, up; Hinweis auf Migrations-Rollback
│   └── perf_probe.sh            docker stats-Sammler während perf_run
└── .github/workflows/           CI: ruff, Unit-Tests, Image-Build
```

---

## 5. Wie der Stack die Performance-Vorgabe trägt

Vorgabe (CR Abschnitt 7 und 14): Ein Objekt mit 10.000 Seiten, gemischt Scan und Digital-PDF, vollständig verarbeitet (OCR, Klassifikation, Ablage) in unter 3 Stunden, das sind 10.800 Sekunden; das Review Center antwortet währenddessen unter 2 Sekunden.

### 5.1 Planungsgrößen

| Größe | Wert | Status | Verifizierung |
|---|---|---|---|
| S, Seiten je Objekt | 10.000 | CR | keine |
| d, Anteil Seiten mit vorhandener Textebene | 0,40 | ANNAHME | Ingest zählt Textebene je Seite ohnehin; Anteil aus den ersten drei realen Objekten ablesen |
| t_ocr, Sekunden je OCR-Seite je Prozess (300 dpi, deu, ohne deskew und clean) | 3,0 | ANNAHME | perf_run auf dem VPS in Meilenstein 4; Messwert ersetzt Annahme |
| t_txt, Sekunden je Digital-Seite (Textextraktion) | 0,1 | ANNAHME | wie oben |
| P, OCR-Prozesse | C-1 | CR-Standard, C gemessen | scripts/measure_server.sh |
| Seiten je Dokument (Mittel) | 8 | ANNAHME | Zählung aus den ersten realen Objekten |
| Anteil Dokumente unter Schwellwert (Stufe 3) | 0,20 | ANNAHME | Kennzahl in reporting nach Einlernphase |
| Dauer je KI-Aufruf inkl. Netz | 6 s | ANNAHME | Latenz in ai_calls protokolliert |
| Dauer je Drive-Upload inkl. API-Latenz | 2 s | ANNAHME | Dauer je Drive-Operation protokolliert |
| Parallelität worker-io | 4 | Planwert | .env, gegen Quota-Fehler abgleichen |

### 5.2 Rechenweg

OCR und Textextraktion (Warteschlange cpu, Parallelität P):

```
T_ocr = (S × (1-d) × t_ocr + S × d × t_txt) / P
      = (6.000 × 3,0 + 4.000 × 0,1) / P
      = 18.400 / P Sekunden
```

| P | T_ocr | in Minuten |
|---|---|---|
| 3 | 6.133 s | 102 |
| 5 | 3.680 s | 61 |
| 7 | 2.629 s | 44 |
| 11 | 1.673 s | 28 |

Klassifikation Stufe 1 und 2 (cpu): N = S / 8 = 1.250 Dokumente, ANNAHME 0,5 s je Dokument für Entitäten plus Modell, also 625 / P Sekunden, bei P = 3 rund 3,5 Minuten, sonst weniger.

Stufe 3 (io, Parallelität 4): 0,20 × 1.250 = 250 Aufrufe × 6 s / 4 = 375 s, rund 6 Minuten. Sie laufen überlappend zur OCR, sobald die ersten Dokumente klassifiziert sind.

Ablage in Drive (io, Parallelität 4): 1.250 Uploads × 2 s / 4 = 625 s, rund 10 Minuten. Falls die Quota eine serielle Verarbeitung erzwingt: 2.500 s, rund 42 Minuten. Auch die Ablage läuft überlappend zur OCR.

Listen: Sekunden, vernachlässigbar.

Gesamtdauer im ungünstigen Fall (P = 3, Drive seriell, keine Überlappung): 102 + 4 + 6 + 42 = 154 Minuten. Das liegt unter 180 Minuten, aber ohne Reserve für schlechte Scans. Mit P = 7 und paralleler Ablage: rund 44 + 1 + 6 + 10 = 61 Minuten mit großer Reserve.

Empfindlichkeit: Steigt t_ocr auf 5 s (schlechte Scans, höhere Auflösung), wird T_ocr = 30.400 / P Sekunden; bei P = 3 sind das 169 Minuten und die Vorgabe wird mit serieller Ablage verfehlt, bei P = 5 sind es 101 Minuten und die Vorgabe hält.

Daraus die Anforderung an den Server, ausgedrückt als Formel für den Auftraggeber nach der Messung: Wenn für die OCR ein Budget von 2 Stunden (7.200 s) reserviert wird und der Rest für Klassifikation, Ablage und Reserve bleibt, muss gelten

```
P ≥ (S × (1-d) × t_ocr + S × d × t_txt) / 7.200
```

Bei t_ocr = 3 s ergibt das P ≥ 2,6, also mindestens 3 OCR-Prozesse und damit mindestens 4 Kerne; bei t_ocr = 5 s ergibt das P ≥ 4,2, also mindestens 5 Prozesse und 6 Kerne. Die tatsächliche Kernzahl entscheidet, ob dieser Stack die Vorgabe ohne weitere Hebel erfüllt.

### 5.3 Antwortzeit des Review Centers während der Verarbeitung

Maßnahmen im Stack: eigener web-Container mit CPU-Gewicht über cpu_shares und einem rechnerisch freien Kern (P = C-1); worker mit hartem CPU-Limit; db mit eigenem Speicherlimit und ausreichend Buffer-Pool; Redis nur mit IDs. Auf Anwendungsebene: alle Listen paginiert und über Indizes (object_id, status, created_at) abgefragt; Fortschrittszähler als eine aggregierte Abfrage über die jobs-Tabelle je Objekt, HTMX-Polling alle 5 Sekunden; keine N+1-Abfragen (select_related, prefetch_related); Dateivorschau als Stream; Volltextindex-Befüllung in einem eigenen, nachrangigen Schritt, damit die Indexpflege nicht mit den Review-Abfragen konkurriert; Seitentexte als Massen-Insert je Dokument statt Einzelinserts. Messung: die Latenzsonde aus tests/performance während perf_run, Zielwert 95. Perzentil unter 2 Sekunden, dokumentiert im Messprotokoll.

Restrisiko: IO-Konkurrenz auf einer gemeinsamen Platte zwischen OCR-Zwischendateien und Datenbank. Falls iowait im Test auffällt: tmp-Verzeichnis auf ein separates Volume, MariaDB-Flush-Verhalten prüfen, blkio-Gewichtung in Compose prüfen.

### 5.4 Stellhebel bei Verfehlung, in Reihenfolge des Aufwands

1. P erhöhen, falls die Messung mehr Kerne zeigt als angenommen, oder P = C statt C-1 für Nachtläufe ohne Anwender (Konfiguration je Zeitfenster).
2. OCR-Parameter: schnelleres Tesseract-Sprachmodell (fast statt best, Genauigkeitsverlust messen), Auflösung auf 300 dpi begrenzen, Vorverarbeitungen ausgeschaltet lassen, PDF/A-Ausgabe aus.
3. Zweiphasige OCR: zuerst nur die ersten ein bis zwei Seiten je Dokument (genug für Klassifikation und Ablagevorschlag), vollständige OCR als nachrangige Warteschlange für Suche und Seitenbereiche. Achtung: Gesamtabrechnungen mit eingebetteten Einzelabrechnungen brauchen die volle OCR vor der Aufteilung; diese Dokumente werden über die Abgrenzungsregel früh erkannt und sofort voll verarbeitet.
4. Große Dokumente in Seitenblöcke teilen (pikepdf), Blöcke parallel erkennen, Ergebnis zusammenführen. Hebt den Nachlauf-Effekt auf, bei dem ein einzelnes Dokument mit sehr vielen Seiten am Ende allein einen Prozess belegt.
5. Drive-Parallelität anheben, bis Quota-Fehler auftreten, dann eine Stufe zurück.
6. Vertikal: größerer VPS-Tarif. Die Formel in 5.2 liefert dafür die Zielkernzahl.

---

## 6. Meilensteine

Reihenfolge so gewählt, dass jeder Meilenstein für sich abnehmbar ist und Auftraggeber-Voraussetzungen (Servermessung, DNS, OAuth-App, Verträge) früh angefordert werden. Aufwände sind grobe Spannen in Personentagen für einen Entwickler, ohne Wartezeiten auf Zulieferungen des Auftraggebers.

| Nr. | Meilenstein | Inhalt | Prüfbare Abnahme | Aufwand (PT) |
|---|---|---|---|---|
| M0 | Voraussetzungen und Messung | scripts/measure_server.sh auf dem VPS, Traefik-Werte, DNS-Eintrag, Google-Cloud-Anleitung an Auftraggeber, .env aus Messwerten, Klärung der offenen Fragen aus Anhang B | Messprotokoll in docs/betrieb mit C, M, D, Netzname, Resolver, Entrypoint; .env.example vollständig; offene Fragen beantwortet oder als Annahme im Plan | 1 bis 2 |
| M1 | Gerüst und Betrieb | Repository, Django-Projekt, Compose mit allen Diensten, Healthchecks, Traefik-Anbindung, Login mit E-Mail, Passwort und TOTP, Rollen, Audit-Grundgerüst, config mit Seed, Logging, Backup-Container, deploy.sh und rollback.sh | Deployment-Test aus CR Abschnitt 14: compose up auf frischem Checkout, alle Healthchecks grün, TLS unter uebernahme.muellerhv.de gültig, Server-Neustart bringt alle Container hoch, Backup auf leerer Instanz eingespielt | 6 bis 9 |
| M2 | Datenmodell und Objektverwaltung | objects, units, owners, tenants, Zuordnungstabellen mit Zeiträumen, leases; Objektanlage; Objektnummer-Erkennung mit konfigurierbarer Stellenzahl; Benennungsregel als zentrale Funktion; Präfix-Mapping | Unit-Tests Ordnerbenennung alle Fälle aus CR Abschnitt 4; Unit-Test historische Zuordnung; Migrationen vorwärts und rückwärts getestet | 4 bis 6 |
| M3 | Drive-Adapter und Ordnerabgleich | OAuth-Ablauf, Token verschlüsselt, Refresh, Statusanzeige; Protokollklasse mit echtem Client, Dry-Run und Fake; drive_folders; Abgleich nach CR Abschnitt 9 inkl. Umbenennung des Altordners mit Dateizählung und Anlage 05_Eigentümerakte; Review-Eintrag bei doppelter Nummer | Integrationstests mit den drei Testobjekten (Dry-Run und Ausführung, Zählung gleich, zweiter Lauf ohne Änderung) gegen Fake und einmal gegen ein Testverzeichnis im echten Drive; Token-Refresh-Nachweis über 8 Tage startet hier und läuft mit | 6 bis 9 |
| M4 | Pipeline OCR und Extraktion | Ingest mit Hash und Dedupe, ocrmypdf-Aufruf, Sidecar zu Seitentexten, Textextraktion Digital-PDF, Maskierung, jobs-Tabelle, Abgleich-Task, Fortschrittsanzeige; erster perf_run auf dem VPS nur für OCR | Integrationstest Abbruch und Wiederaufnahme ohne Doppelverarbeitung; Messprotokoll mit t_ocr, m_ocr, Plattenfaktor; Entscheidung über P und Stellhebel dokumentiert | 6 bis 8 |
| M5 | Klassifikation Stufe 1 und 2, Review Center, Ablage | Regeln, Entitätsmerkmale, Gazetteer, spaCy, TF-IDF-Modell mit Kalibrierung, Abgrenzungsregel, Kandidatenliste bei mehreren historischen Eigentümern, Review Center mit Zielbereich 01 bis 06 und Pflichtfeldern für 05, Trainingsdaten aus Korrekturen, Ablage über Drive mit Seitenbereichen, 06_Sonstiges mit automatischem Review-Eintrag, KPI Anteil 06_Sonstiges | Unit-Test Abgrenzung mit 20 Beispielen; Integrationstest Gesamtabrechnung mit 12 Einzelabrechnungen (eine Masterdatei, 12 Zuordnungen, keine Duplikate); jede Review-Aktion im Audit sichtbar | 10 bis 15 |
| M6 | Stufe 3 KI-Provider | Schnittstelle, OpenAI- und Anthropic-Implementierung mit strukturierter Ausgabe, Maskierung, Tokenbegrenzung, Fallback, Kostenlimit, ai_calls, Konfiguration im Admin | Unit-Test IBAN-Maskierung vor Aufruf; Integrationstest Ausfall des Primäranbieters mit Fallback und identischem Ergebnisformat; Kosten je Objekt im Reporting | 3 bis 5 |
| M7 | Import Eigentümerlisten | Parser für PDF (Scan und digital), Excel, CSV, Formatprofile für Verwaltungssoftware, Namens-Splitter mit Konfidenz, Vorschläge ins Review Center, Übernahme erst nach Bestätigung, Zeilenzähler gegen stilles Verwerfen | Testdateien je Format (synthetisch), Zeilen eingelesen gleich Zeilen im Review, unsichere Zeilen markiert | 5 bis 8 |
| M8 | Requirement Engine und Nachforderung | Prüfpunkte aus CR Abschnitt 12 als konfigurierbare Regeln, Vollständigkeitsstatus je Objekt, Nachforderungsschreiben als PDF im HVM-CI (A4 hoch) mit fehlenden Punkten | Prüfpunkte je Testobjekt korrekt; Schreiben-PDF mit Kennlinie, Logo, Fußzeile, Sichtprüfung durch Auftraggeber | 4 bis 6 |
| M9 | Listen | Eigentümer- und Mieterliste als Excel (drei Blätter) und PDF (A4 quer, CI), Spalten konfigurierbar, Erzeugung nach Lauf, nach Bestätigung und manuell, Drive-Versionierung über gespeicherte File-ID | Integrationstest zwei Läufe, genau eine Datei je Format, Historie-Blatt mit Alteigentümer, keine vollständige IBAN; Sichtprüfung PDF durch Auftraggeber | 4 bis 6 |
| M10 | Suche, Reporting, Massenbearbeitung | Volltext-Lookup, Suche über alle Objekte nach Eigentümer, Einheit, Zeitraum, Unterart; Reporting-Seite; Massenbearbeitung mit Vorschau für gleichartige Dokumente | Suchszenarien aus dem CR; Massenbearbeitung von 40 Einzelabrechnungen eines Jahres mit Vorschau und Audit | 4 bis 6 |
| M11 | Performance-Test, Abgleich Bestand, Produktivstart | perf_run mit 10.000 Seiten auf dem VPS mit Latenzsonde, Feinabstimmung, Ordnerabgleich aller bestehenden Objektordner (Dry-Run, Freigabe, Ausführung, Protokoll), Bedienungsanleitung, Doku, Grep-Prüfung | Definition of Done aus CR vollständig: Performance-Test bestanden mit Messwerten, Abgleichprotokoll liegt vor, alle Tests grün, Admin-Konfiguration dokumentiert, Anleitung vorhanden | 4 bis 7 |

Summe: rund 57 bis 87 Personentage. Die Spanne ist breit, weil die Klassifikationsqualität (M5) und die Formatvielfalt der Importe (M7) erst an realen Unterlagen sichtbar werden. Empfehlung: nach M4 mit den gemessenen Werten den Plan für M5 bis M11 nachschärfen.

---

## 7. Ehrliche Schwächen und Risiken dieses Stacks für dieses Projekt

1. Durchsatz hängt an der Kernzahl eines nicht vermessenen Servers. Der Stack skaliert OCR nur über Prozesse. Zeigt die Messung wenige Kerne, hält die 3-Stunden-Vorgabe nur mit den Hebeln aus 5.4 oder einem größeren Tarif. Kein Stack ändert das, aber dieser Vorschlag bietet keine Auslagerung der OCR auf eine zweite Maschine ohne Umbau (Celery könnte einen zweiten Worker-Host anbinden, das widerspräche aber dem Blickwinkel).
2. Celery mit Redis ist mächtig, aber fehleranfällig in der Konfiguration. Sichtbarkeitsfenster, späte Bestätigung, Prefetch, Zeitlimits und Speicher-Recycling müssen zusammenpassen; ein falscher Wert führt zu Doppelverarbeitung oder hängenden Jobs. Die Absicherung liegt in der Datenbank (Job-Status, Hash), nicht im Broker. Das muss im Integrationstest mit hartem Abbruch bewiesen werden und bei jeder Celery-Anhebung erneut.
3. Kaltstart des lokalen Klassifikators. TF-IDF ist schnell und erklärbar, aber ohne gelabelte Daten wertlos. In den ersten Wochen tragen Regeln und die externe KI; das kostet Geld und Reviewer-Zeit, und der Zielwert von unter 5 % in 06_Sonstiges ist anfangs nicht erreichbar. Seltene Unterordner bleiben lange regelbasiert. Ein Embedding-Modell hätte hier Vorteile, kostet aber Image-Größe, Latenz und Erklärbarkeit.
4. Serverseitiges Rendering mit HTMX begrenzt die Bedienung. Kein Server-Push, Fortschritt per Polling; das Zuschneiden von Seitenbereichen als Formular statt grafisch; die PDF-Vorschau hängt vom Browser-Viewer ab. Für 2 bis 5 geübte Anwender tragbar, aber wer ein Werkzeug mit Drag-and-drop und Live-Aktualisierung erwartet, wird das spüren.
5. MariaDB als Volltextsuche ist schwach für Deutsch. Kein Stemming, keine Kompositazerlegung, Teilwortsuche nur per Präfix. Die Suche funktioniert primär über Metadaten, was dem CR entspricht, aber der Volltext ist eine Hilfsfunktion, kein Recherche-Werkzeug. Ein Suchdienst wäre besser und ist bewusst nicht enthalten.
6. Ein Image für alles. Der web-Container trägt Tesseract, ghostscript und spaCy-Modelle, die er nie nutzt. Das Image ist groß, der Build dauert länger, und ein Sicherheitsupdate in einer OCR-Abhängigkeit erzwingt ein Rebuild und Neustart auch von web. Der Gewinn (ein Build, garantiert gleiche Codeversion) wurde höher gewichtet.
7. Google Drive als Ablage-Backend über ein einzelnes technisches Konto. Refresh-Token können ungültig werden (Passwortänderung, Sicherheitsereignis, Testmodus der OAuth-App), die Quota ist projektbezogen, Listungen sind nicht sofort konsistent, es gibt keine Transaktionen über Umbenennen und Verschieben. Der Adapter puffert das mit Zählung, Idempotenz und Backoff, aber die Ablage bleibt der am wenigsten kontrollierbare Teil der Kette. Der Wurzelpfad liegt zudem in einem persönlichen Drive, nicht in einer geteilten Ablage (Befund 6.6).
8. Schlüsselverwaltung auf einer einzelnen Maschine. Fernet-Schlüssel für Token und IBAN, Django-Secret, API-Schlüssel liegen in .env auf dem VPS. Wer Root-Zugriff hat, hat alles. Sicherungen enthalten verschlüsselte Token und IBAN-Werte; ohne den Schlüssel sind sie wertlos, mit dem Schlüssel sind sie vollständig. Der Schlüssel muss getrennt von der Sicherung aufbewahrt werden, sonst ist eine Wiederherstellung unmöglich oder das Backup ein Risiko.
9. Ein Entwickler, ein Stack, viele Bibliotheken. Django, allauth, Celery, spaCy, scikit-learn, ocrmypdf und die beiden KI-SDKs haben eigene Release-Zyklen. Einmal im Quartal Abhängigkeiten anheben und die Testsuite laufen lassen ist Pflicht, sonst wächst der Rückstand und Sicherheitsupdates werden schwer. Die Testsuite ist damit nicht nur Qualitätssicherung, sondern die Voraussetzung für den Betrieb.
10. Der CR setzt eine Basis voraus, die nicht spezifiziert ist (Befund 6.3: Aufbau der Akten 01 bis 04, Requirement Engine, Nachforderungsgenerator). Dieser Vorschlag plant diese Basis minimal mit; der Aufwand dafür ist in M2, M5 und M8 enthalten, aber unsicher, solange kein Lastenheft für CR-01 bis CR-04 vorliegt.

---

## 8. Betriebsaufwand für ein Team aus einem Entwickler und 2 bis 5 Anwendern

### 8.1 Regelbetrieb

| Rhythmus | Tätigkeit | Wer | Aufwand |
|---|---|---|---|
| täglich (automatisch) | Backup-Dump und tar, Token-Prüfung, Job-Abgleich, tmp-Bereinigung, Nachtraining | beat und backup | keiner; Ergebnis auf der Statusseite |
| täglich | Blick auf Statusseite: laufende Objekte, Fehler, offene Reviews, Token-Gültigkeit, letzte Sicherung | Sachbearbeiter, der das Objekt betreut | 5 Minuten |
| wöchentlich | Log-Zusammenfassung (Fehler je Dienst), Plattenfüllstand, KPI Anteil 06_Sonstiges, Kosten Stufe 3 | Entwickler | ANNAHME 1 bis 2 Stunden; Verifizierung nach den ersten vier Betriebswochen |
| monatlich | Image-Rebuild für Betriebssystem-Pakete, Sicherheitsupdates der Python-Abhängigkeiten, Deployment, Smoke-Test | Entwickler | ANNAHME 2 bis 4 Stunden |
| quartalsweise | Abhängigkeiten anheben (Minor), Testsuite, Restore-Test auf leerer Instanz, Prüfung Aufbewahrungsfristen-Konfiguration mit Geschäftsführung | Entwickler | ANNAHME 1 Personentag |
| jährlich | Major-Anhebungen (Django LTS, allauth, Celery), Überprüfung der Provider-Modelle und Kosten, Überprüfung der Verträge (AVV) | Entwickler, Geschäftsführung | ANNAHME 2 bis 3 Personentage |
| bei Bedarf | Nutzer anlegen, MFA zurücksetzen, Konfiguration ändern (Schwellwerte, Spalten, Unterstruktur), OAuth erneut verbinden | Admin (Auftraggeber oder Entwickler) | Minuten je Vorgang, kein Deployment nötig |

Summe im eingeschwungenen Zustand: ANNAHME 2 bis 4 Stunden je Woche für den Entwickler zuzüglich der Quartals- und Jahrestermine. Verifizierung: Zeiterfassung in den ersten drei Monaten, danach Anpassung des Plans.

### 8.2 Deployment und Rollback

Deployment (scripts/deploy.sh): git pull, IMAGE_TAG auf den Commit-Hash setzen, docker compose build, docker compose up -d, docker compose exec web python manage.py migrate, Warten auf Healthchecks, Smoke-Test der Startseite. Ausfallzeit im Bereich von Sekunden während des Containerwechsels; für 2 bis 5 Anwender außerhalb laufender Objektverarbeitung planbar. Laufende Celery-Tasks werden beim Stopp mit Wartezeit beendet (warm shutdown), unfertige Jobs stehen in der Datenbank und werden nach dem Start durch den Abgleich-Task erneut eingereiht.

Rollback (scripts/rollback.sh): vorheriges IMAGE_TAG aus einer Datei mit den letzten Tags setzen, docker compose up -d. Ein Befehl, wie vom CR gefordert. Enthält das fehlgeschlagene Deployment eine Schemaänderung, wird zusätzlich der dokumentierte Migrations-Rollback ausgeführt (python manage.py migrate app vorheriger_stand); die Rückwärtsfähigkeit jeder Migration wird in der CI geprüft.

### 8.3 Sicherung und Wiederherstellung

Täglich: mariadb-dump über das Netz vom db-Dienst, komprimiert; tar der Bind-Mounts transit, ocr_cache, models; Ablage unter /srv/objektakte/backup mit Datum; Aufbewahrung in Tagen aus .env; ältere Sicherungen werden gelöscht. Restore (docker/backup/restore.sh): leere Instanz hochfahren, Dump einspielen, Verzeichnisse zurückkopieren, Migrationen prüfen, Smoke-Test. Einmal in M1 und danach quartalsweise geprobt. Offen: ob und wohin eine Kopie außer Haus gehen soll (Anhang B).

### 8.4 Überwachung und Störungen

Healthchecks in Compose starten hängende Dienste neu; die Statusseite zeigt Dienstzustand, Job-Zähler, Fehlerliste, Token, Sicherungen. Ein Runbook in docs/betrieb beschreibt die häufigen Fälle: Token ungültig (OAuth erneut verbinden), Drive-Quota (Parallelität senken, Job läuft weiter), Platte voll (Ingest stoppt von selbst, tmp und alte OCR-Ausgaben bereinigen), Worker ohne Fortschritt (Logs, inspect, Neustart, Abgleich-Task), Fallback-Anbieter aktiv (Primäranbieter prüfen). Eine aktive Benachrichtigung bei Störungen (zum Beispiel E-Mail aus beat über den Workspace-SMTP) ist kein Bestandteil des CR und in Anhang B als Frage geführt.

### 8.5 Was die Anwender lernen müssen

Review Center (Zielbereich, Pflichtfelder für 05_Eigentümerakte, Kandidatenliste, Massenbearbeitung), Objektanlage mit Dry-Run des Ordnerabgleichs, Statusseite, Listen manuell auslösen, Nachforderung erzeugen. ANNAHME: Einweisung von 2 Stunden je Anwender anhand der Bedienungsanleitung aus M11; Verifizierung durch Rückfragen in den ersten zwei Wochen.

---

## Anhang A: Verzeichnis der Annahmen

| Nr. | ANNAHME | Verifizierung |
|---|---|---|
| A1 | t_ocr = 3,0 s je OCR-Seite je Prozess (300 dpi, deu, ohne deskew und clean) | perf_run auf dem VPS in M4 |
| A2 | d = 0,40 Anteil Seiten mit vorhandener Textebene | Ingest-Zählung der ersten drei realen Objekte |
| A3 | t_txt = 0,1 s je Digital-Seite | perf_run in M4 |
| A4 | 8 Seiten je Dokument im Mittel | Zählung aus den ersten realen Objekten |
| A5 | 20 % der Dokumente gehen in Stufe 3 | KPI in reporting nach Einlernphase |
| A6 | 6 s je KI-Aufruf inkl. Netz | Latenz in ai_calls |
| A7 | 2 s je Drive-Upload inkl. API-Latenz | Dauer je Drive-Operation im Protokoll |
| A8 | m_ocr = 0,75 GiB Speicher je OCR-Prozess | docker stats während perf_run in M4 |
| A9 | web mit 3 gunicorn-Workern braucht rund 1 GiB | docker stats im Betrieb |
| A10 | 0,5 s je Dokument für Entitäten plus Modell in Stufe 2; spaCy unter 1 s je zehnseitigem Dokument | Messung in M5 |
| A11 | Embedding-Klassifikator läge bei 50 bis 200 ms je Dokument auf CPU | nur zu messen, falls dieser Pfad gewählt wird |
| A12 | OCR-Ausgabe 1,5 bis 2 mal so groß wie das Original | Plattenmessung in M4 |
| A13 | Regelbetrieb 2 bis 4 Stunden je Woche für den Entwickler, monatlich 2 bis 4 Stunden Updates, quartalsweise 1 PT, jährlich 2 bis 3 PT | Zeiterfassung der ersten drei Monate |
| A14 | Einweisung 2 Stunden je Anwender | Rückfragen in den ersten zwei Wochen |

Aufwandsspannen in Abschnitt 6 sind Schätzungen, keine Messwerte, und werden nach M4 nachgeschärft.

## Anhang B: Offene Fragen an den Auftraggeber (gebündelt, nur echte Entscheidungen)

1. Objektnummer bei Neuanlage: Nullauffüllung (082) oder Ist-Nummer (82)? Zulässige Stellenzahl 2 bis 6 für die Erkennung bestehender Ordner? (Befund 4)
2. Soll der vorhandene Immoware24-Export als erster Import über das Review Center dienen, obwohl der CR von keinen Bestandsdaten spricht? (Befund 4)
3. Existieren CR-01 bis CR-04, ein Lastenheft oder Vorlagen (Word, Excel) für Nachforderungsgenerator und Requirement Engine? Falls nein: Freigabe, beides minimal neu zu bauen. (Befund 6.2, 6.3)
4. Ordnerschreibweise wortgetreu übernehmen: Hauptordner 05_Eigentümerakte mit Umlaut, Unterordner ASCII (Wirtschaftsplaene, Beschluesse)? (Befund 6.5)
5. Präfix in Eigentümerakten-Ordnern für Einheiten, die keine Wohnungen sind: immer WE oder typbezogen (ST03_Nachname, GE01_Nachname)? (Befund 6.7)
6. Ist ein Umzug des Wurzelpfads in eine geteilte Ablage geplant? Der Adapter unterstützt beides; die Frage betrifft Zeitpunkt und Zuständigkeit. (Befund 6.6)
7. PDF-Listen mit über zwanzig Spalten auf A4 quer: kleinere Tabellenschrift (Abweichung von 10 bis 11 pt der CI), Aufteilung in zwei Tabellenblöcke je Einheit oder mehrzeilige Datensätze?
8. Definition of Done Grep-Prüfung: Ist die Auslegung akzeptiert, dass die Prüfung src ohne das Seed-Verzeichnis umfasst und der Alt-Alias dort als Konfigurationswert steht? (Befund 6.4)
9. Anmeldung über Google Workspace: zusätzlich TOTP in der Anwendung erzwingen oder genügt die Zwei-Faktor-Absicherung des Google-Kontos? Gilt nur die Domain muellerhv.de?
10. Sicherung: Soll eine Kopie außer Haus gehen (Ziel, Verschlüsselung, Aufbewahrung in Tagen)? Wer verwahrt den Fernet-Schlüssel getrennt von der Sicherung?
11. Startwerte für Stufe 3: Primär- und Fallback-Anbieter, Kostenlimit je Objekt und je Tag, maximale Tokenzahl des Textauszugs. Werte sind zur Laufzeit änderbar, aber Startwerte werden gebraucht.
12. Beispieldateien für die Importprofile: Exporte aus Domus und weiterer Verwaltungssoftware der Vorverwaltungen, anonymisiert oder synthetisch.
13. Benachrichtigung bei Störungen: gewünscht? Kanal (E-Mail über Workspace-SMTP) und Empfänger?
14. Zweiter Worker-Dienst worker-io als Ergänzung zum CR-Container-Katalog: akzeptiert, oder ein einzelner worker-Container mit beiden Warteschlangen?
