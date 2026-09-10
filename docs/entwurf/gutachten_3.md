# Gutachten 3 zu den Stack-Vorschlägen A, B und C für CR-05

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Blickwinkel dieses Gutachtens: Risiko, Sicherheit, Datenschutz und Performance-Belastbarkeit (OCR-Durchsatz, Antwortzeit unter Last, Wiederaufnahme nach Abbruch).

Stand: 10.09.2026. Faktenbasis: CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md), Befundakte (befund.md) und die drei Vorschläge stack_A_python_monolith.md, stack_B_api_spa.md und stack_C_polyglott.md. Alle Zahlenwerte stammen aus diesen Dokumenten. Eigene Planungsgrößen dieses Gutachtens sind mit ANNAHME gekennzeichnet und in Abschnitt 10 gesammelt. Beispiele sind synthetisch (WE03_Mustermann).

## 1. Ergebnis und Empfehlung

Rangfolge: A 84,0 Punkte, B 70,0 Punkte, C 67,0 Punkte von 100.

Empfehlung: Vorschlag A (Django, HTMX, Celery, Redis, MariaDB) umsetzen, jedoch nicht in der vorgelegten Form, sondern mit acht Auflagen aus Sicht Sicherheit und Performance, die aus B und C übernommen werden (vollständige Liste in Abschnitt 8). Die wichtigsten:

1. OCR-Probelauf mit rund 100 Seiten auf dem VPS bereits in M0, nicht erst in M4 (aus B und C). Die Entscheidung über P, Tarif und Zwei-Phasen-OCR fällt damit vor dem Bau der Pipeline, nicht nach 16 bis 24 Personentagen.
2. Aufteilung großer Dokumente in Seitenblöcke als Konstruktionsentscheidung (aus C), nicht als Stellhebel. Löst zugleich das Problem der Celery-Sichtbarkeitsfenster bei lang laufenden Tasks.
3. OMP_THREAD_LIMIT=1 im Worker und Trennung eines OCR-Pools ohne Modelle im Speicher von einem Klassifikations-Pool mit spaCy und Klassifikator (aus B und C).
4. Zwei Build-Targets aus einem Dockerfile: web ohne Tesseract und Ghostscript, worker mit (aus B). Der einzige von außen erreichbare Container trägt keine OCR-Binärdateien.
5. Netztrennung: internes Netz ohne Internetzugang für db, queue und backup, Egress-Netz nur für web, worker, worker-io und beat (aus B).
6. Zweites Datenbankkonto für den Worker mit Mindestrechten, Datenbank-Trigger gegen UPDATE und DELETE auf der Audit-Tabelle als Pflicht statt Option, Container-Härtung mit non-root und no-new-privileges (aus C).
7. Dokumentvorschau über vorgerenderte Seitenbilder aus dem OCR-Cache statt Streamen des Original-PDF durch synchrone gunicorn-Worker (aus B).
8. Rechenweg mit Skalierungsfaktor und Ungünstig-Szenario (0 Prozent Digitalseiten, hohe Seitenzeit) führen und in M0 mit Messwerten ersetzen (aus B und C).

Begründung in Kürze: A hat die wenigsten beweglichen Teile, eine Sprache, den größten Personalmarkt, eine ausgereifte Authentifizierung aus einem Paket und ein ORM mit offizieller MariaDB-Unterstützung. B verliert wegen der vollständig selbst gebauten Authentifizierung (Login, TOTP, Sitzungen, CSRF, OIDC) und der zweiten Toolchain, deren Build auf dem Produktionsserver läuft. C verliert wegen neun Containern, zwei Laufzeiten und einer selbst gebauten sprachübergreifenden Queue, obwohl C die reifste Authentifizierung und den belastbarsten Rechenweg vorlegt. Beide unterlegenen Vorschläge enthalten die besseren Antworten auf Netztrennung, Datenbankrechte, Chunking und Messreihenfolge. Ohne diese Übernahmen wäre A aus Risikosicht nur mit Vorbehalt empfehlbar.

Gemeinsame Erkenntnis aller drei Rechenwege: Ob die 3-Stunden-Vorgabe hält, entscheidet die gemessene Kernzahl des VPS, nicht der Stack. Bei P = 3 OCR-Prozessen liegen alle drei im jeweiligen Basisfall zwischen rund 110 und 155 Minuten und verfehlen die Vorgabe im ungünstigen Fall; ab P = 5 halten alle drei den Basisfall mit Reserve. Die Servermessung ist damit die wichtigste einzelne Risikominderung des Projekts und gehört vor jede Bauentscheidung.

## 2. Bewertungsmatrix

| Nr. | Kriterium | Gewicht | A | B | C |
|---|---|---|---|---|---|
| 1 | Betrieb auf einem VPS mit Compose hinter Traefik, Betriebsaufwand, bewegliche Teile | 20 | 9 | 7 | 5 |
| 2 | OCR, NER, ML und beide KI-SDKs ohne Sprachbruch | 15 | 10 | 10 | 8 |
| 3 | TOTP, Rollen, Laufzeitkonfiguration, Audit: ausgereift vorhanden vs. Eigenbau | 15 | 8 | 4 | 9 |
| 4 | Datenmodell, ORM mit MariaDB, rückrollbare Migrationen, Transaktionssicherheit | 10 | 9 | 8 | 6 |
| 5 | Umsetzungsgeschwindigkeit und Wartbarkeit über mehrere Jahre | 15 | 9 | 5 | 5 |
| 6 | Review Center: Massenbearbeitung, Suche, Vorschau, Antwortzeit unter 2 s | 10 | 6 | 9 | 7 |
| 7 | web/worker, Wiederaufnahme, Idempotenz, Rechenweg 10.000 Seiten | 10 | 7 | 8 | 8 |
| 8 | Risiken: Abhängigkeiten, Lizenzen, Sicherheitshistorie, Lock-in | 5 | 7 | 5 | 6 |
| | Gewichtete Summe, auf 100 normiert | 100 | 84,0 | 70,0 | 67,0 |

## 3. Rechenweg der gewichteten Summe

Formel: Gesamt = Summe über alle Kriterien (Gewicht × Punkte) / 10. Die Gewichte summieren sich zu 100, Punkte liegen zwischen 0 und 10, das Ergebnis zwischen 0 und 100.

```
A: 20×9 + 15×10 + 15×8 + 10×9 + 15×9 + 10×6 + 10×7 + 5×7
 = 180 + 150 + 120 + 90 + 135 + 60 + 70 + 35 = 840   →  840 / 10 = 84,0

B: 20×7 + 15×10 + 15×4 + 10×8 + 15×5 + 10×9 + 10×8 + 5×5
 = 140 + 150 + 60 + 80 + 75 + 90 + 80 + 25 = 700     →  700 / 10 = 70,0

C: 20×5 + 15×8 + 15×9 + 10×6 + 15×5 + 10×7 + 10×8 + 5×6
 = 100 + 120 + 135 + 60 + 75 + 70 + 80 + 30 = 670    →  670 / 10 = 67,0
```

Empfindlichkeitsprüfung: Bewertet man B in Kriterium 3 mit 6 statt 4 (Eigenbau als sauber entworfen, aber ungeprüft), steigt B auf 73,0. Bewertet man C in Kriterium 1 mit 7 statt 5, steigt C auf 71,0. Der Abstand zu A bleibt in beiden Fällen zweistellig. Die Rangfolge ist gegen einzelne Bewertungsentscheidungen robust.

## 4. Bewertung je Kriterium

### Kriterium 1 (Gewicht 20): Betrieb auf einem VPS, Betriebsaufwand, bewegliche Teile

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 9 | Sieben Dienste aus einem Image, eine Sprache, eine Toolchain, Compose-Gerüst mit Platzhaltern für alle Traefik-Werte, Migrationen als bewusster manueller Schritt gegen Neustartschleifen. Abzug: Der web-Container trägt Tesseract und Ghostscript, obwohl er sie nie nutzt, und das Backend-Netz ist nicht als internal markiert, sodass db, queue und backup ausgehend ins Internet könnten. |
| B | 7 | Acht Dienste, vorbildliche Netztrennung (internes Netz plus Egress-Netz), migrate-Einmaldienst. Abzug: zweite Toolchain mit Node-Build auf dem VPS bei jedem Deployment, der in eine laufende Objektverarbeitung fallen kann; der Vorschlag nennt als Abhilfe eine Registry, also einen weiteren Baustein. |
| C | 5 | Neun Dienste in zwei Laufzeiten, darunter der Container bridge als PHP-Langzeitprozess mit geplantem Selbstneustart, Horizon, Scheduler, Python-Supervisor und eine selbst gebaute Stream-Queue ohne Dashboard. Die Container-Härtung ist die beste der drei, aber die Zahl der beweglichen Teile ist für einen Entwickler die höchste. |

### Kriterium 2 (Gewicht 15): OCR, NER, ML und beide KI-SDKs

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 10 | Alles in Python: ocrmypdf, Muster und Gazetteer mit rapidfuzz vor spaCy, IBAN-Prüfziffer, TF-IDF mit Kalibrierung, beide offiziellen SDKs mit strukturierter JSON-Ausgabe hinter einer schmalen eigenen Schnittstelle. Kein Sprachbruch, keine Zwischenschicht wie LiteLLM. |
| B | 10 | Identisches Ökosystem. Zusätzlich Textebenen-Vorprüfung mit PyMuPDF, damit ocrmypdf für Digital-PDFs nicht startet, und Datenminimierung im Provider-Request (Kontextfelder ohne Personennamen). |
| C | 8 | Das Worker-Ökosystem ist dasselbe, aber jedes Ergebnis muss über JSON-Schema, Redis Streams und generierte Enums die Sprachgrenze passieren. Die Normalisierung von Einheitenbezeichnungen wird in Python (Import) und PHP (Suche, Review) gebraucht; der Vorschlag benennt die doppelte Logik selbst. Pluspunkt: IBAN-Maskierung ist Teil des Provider-Interfaces, nicht Aufgabe des Aufrufers. |

### Kriterium 3 (Gewicht 15): TOTP, Rollen, Laufzeitkonfiguration, Audit

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 8 | django-allauth deckt E-Mail-Login, TOTP mit Wiederherstellungscodes und Google-Anmeldung aus einem Paket ab, Django-Gruppen für zwei Rollen, Ratenbegrenzung, MFA-Erzwingung per Middleware, Admin-Reset des zweiten Faktors protokolliert. Eigenbau bleibt für Audit-Tabelle und Konfigurations-App, beides überschaubar und fachlich begründet. Abzug: Reifegrad des MFA-Moduls laut Vorschlag selbst zu prüfen, Audit-Trigger nur optional, Frage der TOTP-Erzwingung bei Google-Anmeldung offen gelassen. |
| B | 4 | Login, TOTP, Wiederherstellungscodes, Sitzungen in Redis, CSRF-Schutz, Ratenbegrenzung und OIDC-Anbindung vollständig von Hand. Der Entwurf ist sauber (argon2id, HttpOnly, Origin-Prüfung, Custom-Header), aber nichts davon ist von Dritten geprüfter Bestand, und der Vorschlag räumt selbst ein, dass jeder Fehler hier sicherheitsrelevant ist. Der beschriebene Ablauf ("bei aktivem TOTP") liest sich zudem so, als sei TOTP je Nutzer optional; der CR verlangt Zwei-Faktor für den Login. Gut: DB-Nutzer ohne UPDATE und DELETE auf audit_log, Konfiguration mit Pydantic-Schema je Schlüssel. |
| C | 9 | Laravel Fortify (TOTP, Wiederherstellungscodes, Ratenbegrenzung, Passwort-Reset), Socialite mit serverseitiger Prüfung der Workspace-Domain, Policies und Gates, 2FA-Middleware für alle Rollen, Audit-Tabelle mit Trigger, Einstellungskatalog mit generierten Admin-Formularen, zwei Datenbankkonten mit Mindestrechten. Abzug: Fortify ist headless, die Oberfläche für TOTP-Einrichtung mit QR-Code entsteht in Blade selbst. |

### Kriterium 4 (Gewicht 10): Datenmodell, ORM, Migrationen, Transaktionen

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 9 | Django ORM mit offiziellem MariaDB-Backend, automatisch erzeugte Rückwärtsoperationen für Schemaänderungen, ausdrückliche Rückwärtsfunktion für Datenmigrationen, Rückwärtsfähigkeit in der CI geprüft, Integrationstests gegen MariaDB statt SQLite, JSON-Felder bewusst sparsam. |
| B | 8 | SQLAlchemy 2 plus Alembic sind reif, downgrade ist Pflicht, Seeds als Datenmigration. Abzug: Der Rollback-Ablauf hat eine Lücke (Abschnitt 5.2, Punkt 2), weil der automatische migrate-Dienst beim Rollback mit dem alten Image gegen ein neueres Schema startet. |
| C | 6 | Eloquent-Migrationen mit down() sind reif. Aber zwei Datenzugriffsschichten auf einem Schema (Reflektion plus Konstante REQUIRED_MIGRATION), zwei Schreiber auf documents und audit_log, und semantische Drift fängt laut Vorschlag kein Werkzeug. Die Volltextspalte search_text liegt direkt in documents, also in der heißen Tabelle des Review Centers (Abschnitt 5.3, Punkt 2). |

### Kriterium 5 (Gewicht 15): Umsetzungsgeschwindigkeit und Wartbarkeit

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 9 | 57 bis 87 Personentage, eine Sprache, Django-LTS mit mehrjährigem Supportfenster, größter Personalmarkt der drei Varianten, hervorragende Dokumentation. Abhängigkeitspflege einmal im Quartal ist realistisch. |
| B | 5 | 74 bis 113 Personentage, davon 30 bis 40 für die SPA; laut eigener Aussage 15 bis 25 Prozent Mehraufwand ohne Fachnutzen außerhalb des Review Centers. Frontend-Ökosystem altert schneller, Nachfolger müssen Python und TypeScript mit Vue beherrschen. |
| C | 5 | 65 bis 100 Personentage plus dauerhaft zwei Testsuiten, Vertragsgenerator, zwei Update-Kalender. Laravel bringt jährlich eine Hauptversion mit kürzeren Supportfenstern als Django LTS. Entwickler, die Laravel und das Python-ML-Werkzeug gleichzeitig sicher beherrschen, sind selten. |

### Kriterium 6 (Gewicht 10): Review Center

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 6 | Realisierbar, aber Massenbearbeitung und Seitenbereiche als Formulare, Fortschritt per Polling, Vorschau durch Streamen des Original-PDF über synchrone gunicorn-Worker (ANNAHME des Vorschlags: 3 Worker). Zwei gleichzeitige Vorschauen großer Dateien belegen zwei von drei Workern; die dritte Anfrage wartet. Das ist unter Last ein echtes Antwortzeitrisiko, das der Vorschlag nicht betrachtet. |
| B | 9 | Rasterkomponente mit virtuellem Scrollen, Vorschau-Endpunkt ohne Schreibwirkung, vorgerenderte Seitenbilder aus dem Cache mit Cache-Headern, SSE für Fortschritt, Keyset-Paginierung, Bestätigung asynchron. Beste Passung zur Anforderung. Abzug: Erzeugungszeit und Plattenplatz der Seitenbilder sind nicht beziffert und nicht im Rechenweg. |
| C | 7 | Livewire liefert reaktive Teilaktualisierungen ohne SPA, nginx im web-Image kann Dateien effizient ausliefern, Fortschrittszähler aus einer kleinen Statistiktabelle. Abzug: Jede Interaktion ist ein Server-Roundtrip mit Neu-Rendering der Komponente, bei 40 Zeilen Massenbearbeitung spürbar; Listen und Nachforderung entstehen asynchron über zwei Laufzeiten, was der Vorschlag selbst als Bedienrisiko benennt. |

### Kriterium 7 (Gewicht 10): web/worker, Wiederaufnahme, Idempotenz, Rechenweg

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 7 | Zustandsautomat in der Datenbank, Abgleich-Task alle fünf Minuten, acks_late, kleine Tasks, worker-io getrennt, Formel für die nötige Prozesszahl an den Auftraggeber. Schwächen: optimistischste Seitenzeit der drei (3,0 s), kein Skalierungsverlust, kein OMP_THREAD_LIMIT (Tesseract kann je Prozess mehrere Threads starten, P Prozesse mal mehrere Threads überzeichnen C Kerne und treffen web), Aufteilung großer Dokumente nur als Stellhebel, erster OCR-Messlauf erst in M4. |
| B | 8 | OMP_THREAD_LIMIT=1, Textebenen-Vorprüfung, Lease-basierte Erkennung hängender Jobs, Szenariotabelle mit ungünstigstem Fall (0 Prozent Digitalseiten, 8 s je Seite), Probelauf mit 100 Seiten in M0, Heartbeat-Healthcheck. Abzug: Chunking großer Dokumente fehlt, kein Skalierungsfaktor. |
| C | 8 | Belastbarster Rechenweg: Skalierungsfaktor 85 Prozent, Ungünstig-Szenario, Chunking als Pflicht mit Begründung (ein Dokument mit 2.000 Seiten würde seriell rund 117 Minuten auf einem Kern laufen), nice und ionice, OCR-Temp auf Platte, Messung in M0, atomarer Claim per UPDATE, XAUTOCLAIM, Dead Letter. Abzug: sprachübergreifende Queue-Mechanik ohne Dashboard und Retry-Strategie selbst gebaut; Wirkung von ionice im Container hängt vom I/O-Scheduler des Hosts ab und ist nicht als Annahme gekennzeichnet. |

### Kriterium 8 (Gewicht 5): Risiken

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 7 | Kleinste Abhängigkeitsfläche, Django mit guter Sicherheitshistorie. Abzug: Lizenzlage nicht besprochen (Ghostscript unter AGPL, Redis-Lizenzwechsel, mysqlclient unter GPL; für internen Betrieb unkritisch, aber zu dokumentieren), allauth-Hauptversionen mit Anpassungsbedarf, Celery-Konfigurationsfläche, Ghostscript im web-Image. |
| B | 5 | npm-Lieferkette, Eigenbau-Authentifizierung, schnelle Zyklen von Vue und Vite, Node-Build auf dem Produktionsserver. Valkey als Ausweg beim Redis-Lizenzthema benannt. |
| C | 6 | Vollständigste Lizenzbetrachtung und beste Container-Härtung, aber zwei Laufzeiten, PHP-Langzeitprozesse mit Speicherwachstum, Eigenbau-Queue, jährliche Laravel-Hauptversionen, Docker Secrets werden in Umgebungsvariablen exportiert und verlieren damit ihren Vorteil gegenüber .env. |

## 5. Kritische Prüfung je Vorschlag

### 5.1 Vorschlag A

Lücken:

1. OMP_THREAD_LIMIT fehlt. A steuert die Parallelität über ocrmypdf mit jobs 1 und Celery-Prozesse, begrenzt aber nicht die Threads innerhalb von Tesseract. B und C setzen OMP_THREAD_LIMIT=1. Ohne die Begrenzung ist die Rechnung P = C minus 1 als Schutz für web wertlos, weil der Worker mehr Threads als Kerne belegt.
2. Großdokumente. A rechnet mit im Mittel 8 Seiten je Dokument (ANNAHME A4 des Vorschlags) und einem Task je Dokument. Eine Gesamtabrechnung mit vielen hundert Seiten läuft damit seriell auf einem Prozess und bestimmt allein den Nachlauf. Zusätzlich muss das visibility_timeout des Redis-Transports über der längsten Task-Dauer liegen, was die Wiederzustellung nach einem Absturz um ebenso lange verzögert. Chunking (C) löst beides und gehört ins Design.
3. Messreihenfolge. Die OCR-Kennzahlen t_ocr, m_ocr und Plattenfaktor werden erst in M4 gemessen, also nach M1 bis M3 mit 16 bis 24 Personentagen. Ein Probelauf mit rund 100 Seiten in M0 kostet Stunden und entscheidet über P, Tarif und die Frage Zwei-Phasen-OCR. B und C messen in M0.
4. Ein Image für alles. Der von außen erreichbare web-Container enthält Ghostscript, Tesseract und qpdf. Der Vorschlag benennt das als Größen- und Rebuild-Problem, nicht als Angriffsfläche. Zwei Build-Targets aus einem Dockerfile (B) lösen das mit geringem Mehraufwand und erhalten die Garantie gleicher Codeversion.
5. Netz. Das Backend-Netz ist bewusst nicht internal, weil web und worker ausgehend müssen. Folge: db, queue und backup haben ebenfalls ausgehenden Internetzugang. B trennt in ein internes Netz und ein Egress-Netz; das ist der bessere Schnitt.
6. Vorschau. Das Original-PDF wird von Django als Stream über synchrone gunicorn-Worker ausgeliefert. Ein Download einer großen OCR-Ausgabe belegt einen Worker für die Dauer der Übertragung. Bei drei Workern und fünf Anwendern ist das die wahrscheinlichste Ursache für verfehlte 2-Sekunden-Antworten, nicht die Datenbank.
7. Modelle je Prozess. Alle P Kindprozesse der cpu-Warteschlange laden spaCy und den Klassifikator, weil ingest, ocr, extract und classify im selben Pool laufen. Mit dem geplanten Recycling (max_tasks_per_child) wird das Modell wiederholt geladen. Ob m_ocr = 0,75 GiB den Modellanteil enthält, ist nicht ausgewiesen. C trennt schwere Prozesse (nur ocrmypdf) von leichten Prozessen (Modelle) und spart damit Speicher und Ladezeit.
8. Audit. Der Trigger gegen UPDATE und DELETE ist optional, der Datenbanknutzer der Anwendung hat volle Rechte. Append-only gilt damit nur per Konvention im Code.
9. Lizenzen. Ghostscript (AGPL), Redis (Lizenzwechsel) und mysqlclient (GPL) werden nicht erwähnt. Für internen Betrieb ohne Weitergabe unkritisch, aber ein Satz in der Architekturdoku ist Pflicht.

Widersprüche zum CR: Der zweite Worker-Dienst worker-io ist korrekt als Abweichung vom Container-Katalog und als Frage gekennzeichnet. Die Sichtbarkeit der Eigentümerakten für Sachbearbeiter wird in der Rollentabelle als Standard gesetzt, obwohl CR Abschnitt 10 eine Prüfung und Dokumentation des Rollenmodells verlangt; das ist eine Entscheidung des Auftraggebers und sollte als Frage geführt werden.

Unmarkierte Annahmen: Die Planungsgrößen sind vorbildlich gekennzeichnet (Anhang A des Vorschlags). Nicht gekennzeichnet sind "Listen: Sekunden, vernachlässigbar" und die Folgen der drei synchronen gunicorn-Worker für das Streaming.

Schönrechnen: t_ocr = 3,0 s ist der optimistischste Wert der drei Vorschläge, ein Skalierungsverlust bei P parallelen Prozessen wird nicht angesetzt. Der ungünstige Fall (P = 3, Ablage seriell) landet bei 154 Minuten und damit ohne Reserve. Positiv: Der Vorschlag weist selbst aus, dass die Vorgabe bei t_ocr = 5 s und P = 3 verfehlt wird, und liefert eine Formel für die nötige Prozesszahl.

### 5.2 Vorschlag B

Lücken:

1. Eigenbau-Authentifizierung (Kriterium 3). Zusätzlich liest sich der Ablauf "Bei Erfolg und aktivem TOTP: Antwort mfa_required" so, als könne ein Nutzer ohne TOTP angemeldet werden. Der CR verlangt E-Mail und Passwort plus Zwei-Faktor. Die Erzwingung muss ausdrücklich ins Design.
2. Rollback-Lücke. Der Rollback ist als `IMAGE_TAG=<voriger Tag> docker compose up -d` beschrieben. Dabei startet der migrate-Dienst mit dem alten Image und führt `alembic upgrade head` gegen eine Datenbank aus, die bereits eine neuere Revision trägt. Der alte Code kennt diese Revision nicht, der Dienst schlägt fehl, und web, worker und beat warten auf seinen erfolgreichen Abschluss und starten nicht. Richtige Reihenfolge: Downgrade mit dem neuen Image, dann Tag wechseln. Das gehört in rollback-Skript und Doku; sonst ist der vom CR geforderte Ein-Befehl-Rollback im Fall einer Schemaänderung ein Ausfall.
3. Redis als Broker, Sitzungsspeicher, Pub/Sub und Ratenbegrenzung gleichzeitig, mit maxmemory-policy noeviction. Läuft der Speicher voll, scheitern auch Anmeldungen. Sitzungen gehören in die Datenbank oder eine zweite Redis-Instanz, oder maxmemory wird großzügig gesetzt und mit Alarm überwacht.
4. Seitenbilder. Erzeugungszeit und Plattenplatz für 10.000 Bilder je Objekt sind nicht beziffert; t_dig = 0,05 s "plus Vorschaubild" wirkt knapp und ist als ANNAHME gekennzeichnet, aber der Platzbedarf fehlt ganz. Datenschutz: Die Bilder enthalten Dokumentinhalte unmaskiert (auch IBAN) und liegen im ocr-cache, den web lesend mountet. Zugriffsschutz liegt allein in der API-Autorisierung, das Löschkonzept muss den Cache umfassen.
5. Audit "Vorher/Nachher als JSON" ohne Feldfilter. Ohne Ausschlussliste landen IBAN-Werte oder Textfelder im Audit. A regelt das ausdrücklich (nur geänderte Felder, keine Volltexte, keine IBAN).
6. Chunking großer Dokumente fehlt wie bei A; die Zwei-Phasen-OCR ist kein Ersatz, weil sie die Gesamtzeit nicht senkt.
7. Node-Build auf dem VPS bei jedem Deployment. Der Vorschlag benennt das Risiko und die Abhilfe (Registry), womit ein weiterer Baustein mit Zugangsdaten entsteht.
8. Der Container-Katalog wird um migrate, worker-io, beat und backup erweitert, ohne dies als Abweichung an den Auftraggeber zu stellen.

Widersprüche zum CR: keine harten. Die Sichtbarkeit der Eigentümerakte je Rolle wird korrekt als offene Frage geführt.

Unmarkierte Annahmen: Die Kernannahmen sind gekennzeichnet. Nicht gekennzeichnet: Plattenbedarf der Seitenbilder, Kosten der Vorschau-Erzeugung im Rechenweg.

Schönrechnen: Der Basisfall mit 50 Prozent Textebene ist optimistischer als bei A und C (40 Prozent), aber die Szenariotabelle zeigt auch 0 Prozent und 8 s je Seite. Kein Schönrechnen, der Nachlauf ist als ANNAHME geführt. Positiv: Die Textebenen-Vorprüfung, die ocrmypdf für Digital-PDFs komplett vermeidet, ist der wirksamste Einzelhebel der drei Vorschläge für gemischte Bestände.

### 5.3 Vorschlag C

Lücken:

1. Neun Container, zwei Laufzeiten, Eigenbau-Queue. Der Vorschlag benennt die Kosten ehrlich (Mehraufwand 15 bis 25 Prozent als ANNAHME A13). Aus Risikosicht ist entscheidend, dass die Wiederaufnahme-Logik (Claim, Heartbeat, XAUTOCLAIM, Dead Letter, Reconcile) auf beiden Seiten selbst gebaut und getestet werden muss, während Celery diese Mechanik mitbringt.
2. Zwei Schreiber auf documents und audit_log, und die Volltextspalte search_text liegt direkt in documents. Damit erhält die Tabelle, die das Review Center am häufigsten liest, während der OCR große Textinhalte und Schreiblast je Dokument. A und B trennen den Text in eine eigene Tabelle und A befüllt den Index in einem nachrangigen Schritt. Das ist für die 2-Sekunden-Vorgabe das robustere Muster.
3. Rückrichtung über den Container bridge. Der Vorschlag nennt selbst die einfachere Alternative (Scheduler pollt die Datenbank) als Rückfallweg. Aus Risikosicht wäre die einfachere Variante der Standard und der Stream-Consumer die Ausbaustufe.
4. Der Container-Katalog wird um web-jobs, bridge, scheduler und backup erweitert, ohne dies als Abweichung zu stellen. Die Entscheidung "Eigentümerakten für beide Rollen sichtbar" wird getroffen statt gefragt (CR Abschnitt 10).
5. Wirkung von ionice im Container hängt vom I/O-Scheduler des Hosts ab und ist nicht als Annahme gekennzeichnet. Zu verifizieren auf dem VPS.
6. Docker Secrets werden im Entrypoint in Umgebungsvariablen exportiert. Damit sind sie über Prozessumgebung und docker inspect lesbar und haben dasselbe Schutzniveau wie .env. Der Vorschlag benennt den Grund (Laravel liest keine _FILE-Varianten nativ), aber der Sicherheitsgewinn ist dann null.
7. Versionsnennungen (redis:7-alpine, Python 3.12.x) verstoßen leicht gegen die Vorgabe, Versionsnummern nur mit Prüfhinweis zu nennen; der Vorschlag relativiert sie als Richtung.

Widersprüche zum CR: keine harten. Die Objektnummer-Erkennung mit konfigurierbarer Stellenzahl ist korrekt als Vorschlag mit Begründung aus dem Befund formuliert.

Unmarkierte Annahmen: "Klassifikation je Dokument unter 50 ms ohne OCR" als Abnahmekriterium ohne Herleitung.

Schönrechnen: keines. C rechnet am konservativsten (Skalierungsfaktor, Ungünstig-Szenario mit 80 Prozent Scans und 5 s je Seite) und leitet daraus als einziger Vorschlag eine zwingende Konstruktionsentscheidung (Chunking) ab.

### 5.4 Gemeinsame Lücken aller drei Vorschläge

1. Idempotenz der Drive-Schreiboperationen bei Abbruch mitten in Upload, Verschiebung oder Umbenennung. Alle drei sichern Idempotenz über Datei-Hash und Job-Status in der Datenbank, keiner beschreibt, was passiert, wenn der Upload abgeschlossen war, aber der Absturz vor dem Speichern der drive_file_id erfolgte. Ergebnis wäre ein Duplikat in Drive. Vorschlag: vor jedem Upload im Zielordner nach einer Datei mit gleichem Namen und gleichem Hash suchen (Hash als appProperties an der Drive-Datei), drive_file_id unmittelbar nach Abschluss persistieren, URI der Resumable-Session im Job speichern und nach Neustart fortsetzen statt neu beginnen.
2. Löschkonzept für Dateisystemartefakte. Alle drei planen Aufbewahrungsfristen als Metadatum der Dokumente in der Datenbank. Originale in transit, OCR-Ausgaben, Sidecar-Texte, Seitenbilder und Sicherungen enthalten dieselben personenbezogenen Daten und müssen vom Löschkonzept erfasst werden.
3. Host-Härtung. SSH nur mit Schlüssel, kein Root-Login, UFW auf 22, 80 und 443, automatische Sicherheitsupdates sind CR-Vorgabe (Abschnitt 0.1), erscheinen aber in keinem Meilensteinplan als prüfbare Abnahme. Gehört als Checkliste in M0 oder M1.
4. Sicherheits-Header (HSTS, Content-Security-Policy, Referrer-Policy) und Cookie-Attribute sind nur bei B teilweise benannt. Bei Traefik als Middleware oder in der Anwendung setzen und im Deployment-Test prüfen.
5. Ablage der TOTP-Geheimnisse. Kein Vorschlag sagt, ob die Geheimnisse verschlüsselt gespeichert werden. Festlegen und im gewählten Paket prüfen.
6. Backup außer Haus und Schlüsselverwahrung. Nur A stellt die Frage. Sicherungen enthalten personenbezogene Daten und verschlüsselte Werte; liegt der Schlüssel im selben Backup, ist die Verschlüsselung wertlos, liegt er nur auf dem Server, ist die Wiederherstellung nach Serververlust unmöglich.
7. Maskierung vor dem KI-Aufruf. Alle drei maskieren IBAN und Kontonummern. CR Abschnitt 10 nennt zusätzlich Ausweisdaten. Muster für Ausweisnummern gehören in die Maskierungsfunktion und in den Unit-Test.
8. Kaltstart des Klassifikators wird von allen benannt, aber keiner plant einen Startkorpus (zum Beispiel die 20 Beispieldokumente der Abgrenzungsregel plus regelbasiert gelabelte Dokumente der ersten Objekte als Seed-Trainingsmenge).

## 6. Performance-Belastbarkeit im Vergleich

Alle Werte sind die ANNAHMEN der jeweiligen Vorschläge, keine Messwerte. Der Server ist nicht vermessen (Befund Abschnitt 2).

| Größe | A | B | C |
|---|---|---|---|
| Sekunden je Scan-Seite je Prozess | 3,0 | 4 (Spanne 2 bis 8) | 3,5 (Spanne 2 bis 5) |
| Anteil Seiten mit Textebene | 40 Prozent | 50 Prozent (30 bis 70) | 40 Prozent (20 bis 60) |
| Skalierungsverlust bei P Prozessen | keiner | keiner | 15 Prozent |
| OMP_THREAD_LIMIT=1 | nein | ja | ja |
| Chunking großer Dokumente | Stellhebel Nr. 4 | nicht explizit | Pflicht, Chunk 100 Seiten (ANNAHME A11) |
| Erster OCR-Messlauf auf dem VPS | M4 | M0 | M0 |
| OCR-Wandzeit Basisfall bei P = 3 | 102 min, gesamt 154 min ohne Überlappung | 111 min plus Nachlauf 10 bis 15 min | 144 min plus Nachlauf 10 bis 15 min |
| OCR-Wandzeit Basisfall bei P = 5 | 61 min, gesamt 78 min ohne Überlappung | 67 min plus Nachlauf | 86 min plus Nachlauf |
| Ungünstigster gerechneter Fall bei P = 3 | 221 min (5 s je Seite, Ablage seriell), verfehlt | 222 min (0 Prozent Digitalseiten oder 8 s je Seite), verfehlt | 265 min (5 s, 80 Prozent Scans), verfehlt |
| Ungünstigster Fall bei P = 5 | 101 min OCR plus Nachlauf, hält | 133 min plus Nachlauf, hält | 159 min plus Nachlauf, hält knapp |

Einordnung:

1. Die Vorschläge unterscheiden sich in der OCR-Wandzeit bei gleichem P um rund 40 Prozent (A: 18.400 / P Sekunden; C: 22.000 / (0,85 × P) = rund 25.900 / P Sekunden). Das ist kein Stack-Unterschied, sondern die Spannweite der Annahmen. Genau deshalb ist die Messung in M0 wichtiger als jede Stack-Eigenschaft.
2. Bei P = 3 ist die Vorgabe in allen drei Rechenwegen nur im Basisfall erreichbar. Zeigt die Messung C = 4 Kerne, braucht das Projekt vor M1 eine Entscheidung über Tarif oder Zwei-Phasen-OCR. Diese Entscheidung sollte als Frage vorab an den Auftraggeber gehen (Abschnitt 11).
3. Der wirksamste stackunabhängige Hebel ist die Vermeidung von ocrmypdf für Seiten mit Textebene (B). Der zweitwirksamste ist Chunking gegen den Nachlauf-Effekt großer Einzeldokumente (C). Beides gehört in die Empfehlung.
4. Antwortzeit unter Last: Alle drei setzen CPU-Limit und cpu_shares. Der Unterschied liegt in der Anwendungsebene: A streamt Originale über synchrone Worker (Risiko), B liefert Seitenbilder aus dem Cache (gut), C liefert über nginx (gut), schreibt aber Volltext in die Review-Tabelle (Risiko). Die Kombination aus A-Grundgerüst, Seitenbildern aus B und getrennter Texttabelle (bei A bereits vorhanden) ist die belastbarste.
5. Wiederaufnahme: A und B stützen sich auf Celery mit Datenbank als Wahrheit, C auf Streams mit Datenbank als Wahrheit. Alle drei planen den Integrationstest mit hartem Abbruch. Das Restrisiko liegt bei allen in der Drive-Schreibidempotenz (Abschnitt 5.4, Punkt 1).

## 7. Sicherheit und Datenschutz im Vergleich

| Aspekt | A | B | C |
|---|---|---|---|
| Reife der Authentifizierung | django-allauth (Konto, MFA, Google) | Eigenbau | Laravel Fortify plus Socialite |
| Erzwingung Zwei-Faktor | Middleware; Frage bei Google-Anmeldung offen | nicht eindeutig erzwungen | Middleware für alle Rollen |
| Datenbankrechte | ein Konto mit Vollzugriff | Anwendungskonto ohne UPDATE und DELETE auf audit_log | zwei Konten; Worker ohne Schreibrecht auf Stammdaten |
| Unveränderbarkeit Audit | Konvention, Trigger optional | Datenbankrechte | Trigger |
| Inhalt Audit | nur geänderte Felder, keine IBAN, keine Volltexte | Vorher/Nachher als JSON ohne Filter | Vorher/Nachher als JSON ohne Filter |
| Netztrennung | ein Backend-Netz mit Internetzugang für alle | internes Netz plus Egress-Netz | ein Backend-Netz |
| Container-Härtung | nicht benannt | nicht benannt | non-root, no-new-privileges, read-only wo möglich |
| Angriffsfläche web-Image | mit Tesseract, Ghostscript, qpdf | ohne OCR-Binärdateien | ohne OCR-Binärdateien |
| IBAN | maskiert, Fernet, Anzeige protokolliert, Log-Filter für IBAN-Muster | maskiert, Zugriff protokolliert | iban_last4 plus verschlüsselt, Maskierung im Provider-Interface erzwungen |
| Datenminimierung KI-Aufruf | Textauszug maskiert, Tokenzahl begrenzt | zusätzlich keine Personennamen in Kontextfeldern | Maskierung im Interface, Circuit Breaker, Kostenlimit |
| Geheimnisse | .env | .env oder Docker Secret | Docker Secret, in Umgebungsvariablen exportiert |
| Backup außer Haus, Schlüsselverwahrung | als Frage gestellt | nicht behandelt | nicht behandelt |
| Löschkonzept Dateisystem | nicht behandelt | nicht behandelt | nicht behandelt |
| Aufbewahrungsfristen | Konfiguration mit leeren Werten und Hinweistext | Konfiguration, Prüfung meldet nur | Tabelle retention_policies mit approved_by und approved_at |

Bewertung aus Datenschutzsicht: Kein Vorschlag verstößt gegen CR Abschnitt 10. C ist in der technischen Durchsetzung (Datenbankrechte, Trigger, Maskierung im Interface, Freigabe der Fristen) am weitesten, A in der Inhaltsdisziplin des Audits und beim Log-Filter. B hat die beste Netztrennung und die klarste Datenminimierung beim KI-Aufruf. Die Empfehlung kombiniert diese Elemente in A (Abschnitt 8).

## 8. Übernahmen aus B und C in die Empfehlung

Alle Punkte sind auf Vorschlag A bezogen und so formuliert, dass sie in den Umsetzungsplan übernommen werden können.

| Nr. | Übernahme | Quelle | Umsetzung in A |
|---|---|---|---|
| G1 | OCR-Probelauf mit rund 100 Seiten (Scan und Digital) auf dem VPS in M0; misst t_ocr, t_txt, m_ocr, Plattenfaktor und Skalierung bei P = 1, 2, C minus 1 | B, C | Management-Command perf_probe als Teil von M0; Ergebnis ersetzt A1, A3, A8, A12 vor M1 |
| G2 | Chunking großer Dokumente als Konstruktionsentscheidung: Aufteilung in Seitenblöcke mit pikepdf, Blockgröße konfigurierbar, Blöcke als eigene Tasks, Merge je Originalseite | C | Task ocr_chunk statt ocr je Dokument; Seitentabelle aus Sidecar je Block; visibility_timeout kann klein bleiben |
| G3 | OMP_THREAD_LIMIT=1 im worker-Container | B, C | Umgebungsvariable im Compose-Dienst worker |
| G4 | Trennung schwerer und leichter Prozesse: Warteschlange ocr mit Pool P ohne Modellimport, Warteschlange classify mit kleinem Pool, der spaCy und Klassifikator hält | C (Prinzip), B (Queue-Aufteilung) | Dritter Celery-Dienst oder zwei Pools mit getrennten Queues; Modelle werden nur im leichten Pool importiert |
| G5 | Textebenen-Vorprüfung je Seite mit pypdfium2 oder PyMuPDF, sodass ocrmypdf für rein digitale Dateien nicht startet | B | Schritt extract vor ocr; nur Dateien mit mindestens einer Seite ohne Text gehen in ocrmypdf mit skip-text |
| G6 | Skalierungsfaktor (ANNAHME 85 Prozent, in G1 messen) und Ungünstig-Szenario (0 Prozent Digitalseiten, hohe Seitenzeit) im Rechenweg | C, B | Abschnitt 5 des Vorschlags A ergänzen; Formel für P mit Faktor |
| G7 | Zwei Build-Targets aus einem Dockerfile: web ohne OCR-Binärdateien, worker mit | B | Multi-Stage mit Targets web und worker; Codebasis und Lockfile identisch |
| G8 | Netztrennung: Netz app als internal für alle Dienste, Netz egress nur für web, worker, worker-io, beat; db, queue, backup ohne Internetzugang | B | Compose-Netze anpassen; web zusätzlich am Traefik-Netz |
| G9 | Zweites Datenbankkonto für worker und worker-io mit Mindestrechten: kein Schreibrecht auf owners, units, owner_unit_assignments, tenants, leases, users, app_settings; audit_events nur INSERT | C | Zwei DATABASE_URL-Werte; Migrationen und web mit dem Vollkonto, Worker mit dem eingeschränkten Konto; Rechte als Migration |
| G10 | Trigger gegen UPDATE und DELETE auf audit_events als Pflicht; Anwendungskonto ohne diese Rechte auf der Tabelle | C, B | Migration mit Trigger und GRANT; Test, der UPDATE erwartet fehlschlagen lässt |
| G11 | Container-Härtung: non-root-Nutzer in allen eigenen Images, security_opt no-new-privileges, read-only Root-Dateisystem für web mit tmpfs für /tmp, für worker nur wo ocrmypdf es zulässt | C | Dockerfile und Compose; Abnahme im Deployment-Test |
| G12 | Vorschau über vorgerenderte Seitenbilder in niedriger Auflösung aus dem OCR-Cache mit Cache-Headern; Original-PDF nur auf ausdrücklichen Wunsch | B | Erzeugung im Schritt extract bzw. ocr_chunk; Auslieferung als statische Datei über WhiteNoise-artige Route oder gthread-Worker; Speicherbedarf in G1 messen |
| G13 | Vorschau-Endpunkt ohne Schreibwirkung für die Massenbearbeitung: berechnet Zielpfade über die Benennungsfunktion und den Folder-ID-Cache | B | HTMX-Partial review/preview, das dieselbe Funktion aufruft wie die Ablage |
| G14 | Maskierung als Teil des Provider-Interfaces, nicht Aufgabe des Aufrufers; Circuit Breaker je Provider; zusätzlich Muster für Ausweisnummern | C | Paket ai: Maskierung in der Basisklasse vor jedem Aufruf, Unit-Test mit IBAN und Ausweisnummer-Mustern |
| G15 | Datenminimierung im KI-Request: Kontextfelder (Objektnummer, Verwaltungsart, bekannte Einheitenbezeichnungen) ohne Personennamen; Textauszug maskiert und gekürzt | B | Request-Schema im Paket ai; Test, dass keine Namen aus owners im Kontext stehen |
| G16 | Aufbewahrungsfristen mit Freigabefeldern (approved_by, approved_at); keine automatische Löschung ohne Freigabe; Löschkonzept umfasst transit, OCR-Cache, Seitenbilder und Sicherungen | C (Freigabe), eigene Ergänzung (Dateisystem) | App documents: Tabelle retention_policies; beat-Task meldet nur, löscht nur bei Freigabe |
| G17 | Zwei-Faktor für alle Rollen unabhängig vom Anmeldeweg erzwingen, auch nach Google-Anmeldung (Empfehlung, Entscheidung beim Auftraggeber) | C | Middleware in accounts; Ausnahme nur per Konfiguration durch Admin |
| G18 | Aggregierte Statistiktabelle je Objekt für Fortschrittszähler statt Zählabfragen über die jobs-Tabelle | C | Schritt setzt Zähler per UPDATE; HTMX-Polling liest eine Zeile |
| G19 | Lizenzabschnitt in der Architekturdoku: Ghostscript AGPL, Redis-Lizenzwechsel mit Valkey als protokollkompatible Option, MySQL-Treiber (mysqlclient GPL, PyMySQL als freizügige Alternative) | C, B | docs/architektur; Hinweis für den Fall einer späteren Weitergabe |
| G20 | Latenzsonde mit fünf gleichzeitigen Nutzern (Locust oder k6) gegen Objektansicht, Review-Liste, Suche, Detailansicht, Kriterium p95 unter 2 s während des 10.000-Seiten-Laufs, dazu iostat | C, B | tests/performance ergänzen; A hat bereits eine Sonde, aber ohne Mehrnutzer-Last |
| G21 | Drive-Schreibidempotenz: Hash als appProperties an jeder hochgeladenen Datei, Prüfung vor Upload, drive_file_id sofort persistieren, Resumable-Session fortsetzen | eigene Ergänzung | Adapter drive; Integrationstest mit Abbruch zwischen Upload und Persistierung |
| G22 | Host-Härtung als Checkliste mit Abnahme in M0 oder M1: SSH-Schlüssel, kein Root-Login, UFW 22/80/443, unattended-upgrades; Sicherheits-Header über Traefik-Middleware oder Django | eigene Ergänzung aus CR 0.1 | docs/betrieb; Deployment-Test erweitern |

ANNAHME: Der Mehraufwand für G1 bis G22 gegenüber Vorschlag A liegt bei 4 bis 7 Personentagen, davon der größte Teil für G2 (Chunking mit Merge), G4 (zweiter Pool), G9 (zwei Datenbankkonten) und G12 (Seitenbilder). Verifizierung: Ist-Zeiten nach M1 und M4 gegen die Planspannen des Vorschlags A.

## 9. Risiken der Empfehlung

1. Celery mit Redis bleibt eine Konfigurationsfalle. Sichtbarkeitsfenster, späte Bestätigung, Prefetch, Zeitlimits und Speicher-Recycling müssen zusammenpassen. G2 (kleine Tasks durch Chunking) senkt das Risiko deutlich, ersetzt aber nicht den Integrationstest mit hartem Abbruch bei jeder Celery-Anhebung.
2. Das Review Center mit HTMX ist tragfähig, aber weniger flüssig als die SPA aus B. Wird das Review Center zum ganztägigen Arbeitswerkzeug der Sachbearbeiter, kann später ein SPA-Inselmodul für genau diese Maske nötig werden. Django kann dafür eine API bereitstellen; die Entscheidung sollte nach den ersten drei realen Objekten anhand der Bedienrückmeldungen fallen, nicht vorab.
3. Die Performance-Vorgabe hängt an der ungemessenen Kernzahl. Zeigt G1 wenige Kerne oder hohe Seitenzeiten, hält kein Stack die Vorgabe ohne Tarifwechsel oder Zwei-Phasen-OCR. A's Rechenweg ist der optimistischste der drei; die Empfehlung setzt darauf, dass G1 und G6 ihn vor M1 korrigieren.
4. django-allauth ist ein großes Paket mit eigener Release-Kadenz; Hauptversionen haben in der Vergangenheit Anpassungen erfordert. Der Reifegrad des MFA-Moduls ist laut Vorschlag zum Umsetzungszeitpunkt zu prüfen; fällt die Prüfung negativ aus, ist django-two-factor-auth plus social-auth-app-django der Rückfallweg mit zwei Paketen.
5. Synchrone gunicorn-Worker und Dateiauslieferung. G12 entschärft das Vorschau-Problem, aber jeder weitere Download großer Dateien (Original-PDF, Excel-Listen) belegt einen Worker. Wenn die Latenzsonde (G20) das zeigt, ist ein gthread-Worker oder ein schlanker nginx-Sidecar für Dateien der nächste Schritt; letzterer wäre ein weiterer Container.
6. Ein Entwickler, viele Bibliotheken. Django, allauth, Celery, spaCy, scikit-learn, ocrmypdf und beide KI-SDKs haben eigene Zyklen. Die quartalsweise Anhebung mit vollständiger Testsuite ist Betriebsvoraussetzung, nicht Kür. Fällt der Entwickler aus, ist der Personalmarkt für Django groß, aber die Einarbeitung in Pipeline und Drive-Adapter bleibt.
7. MariaDB-Volltext ohne deutsches Stemming und ohne Kompositazerlegung. Die Suche funktioniert primär über Metadaten, wie im CR beschrieben. Erwarten die Anwender Recherche im Volltext, kommt ein Suchdienst als weiterer Container hinzu.
8. Google Drive als Ablage über ein einzelnes technisches Konto bleibt der am wenigsten kontrollierbare Teil der Kette (Token-Ungültigkeit, Quota, fehlende Transaktionen). G21 senkt das Duplikatrisiko, beseitigt es aber nicht vollständig.
9. Schlüsselverwaltung auf einer Maschine. Fernet-Schlüssel, Django-Secret und API-Schlüssel liegen in .env auf dem VPS; wer Root hat, hat alles. Docker Secrets ändern daran wenig (siehe C, Punkt 6). Die getrennte Verwahrung des Schlüssels für Sicherungen ist eine organisatorische Maßnahme des Auftraggebers.
10. Kaltstart des lokalen Klassifikators. In den ersten Wochen tragen Regeln und die externe KI, mit Kosten und Reviewer-Zeit; der Zielwert unter 5 Prozent in 06_Sonstiges ist erst nach Einlernphase realistisch. Stackunabhängig, aber im Kostenprotokoll sichtbar zu machen.

## 10. Annahmen dieses Gutachtens

| Nr. | ANNAHME | Verifizierung |
|---|---|---|
| G-A1 | Mehraufwand für die Übernahmen G1 bis G22 gegenüber Vorschlag A: 4 bis 7 Personentage | Ist-Zeiten nach M1 und M4 gegen die Planspannen |
| G-A2 | Skalierungsverlust bei P parallelen OCR-Prozessen: 15 Prozent (übernommen aus C, A4) | Messung in G1 mit P = 1, 2, C minus 1 auf dem VPS |
| G-A3 | Kosten der Seitenbilder (Zeit je Seite, Bytes je Seite) sind gegenüber der OCR-Zeit nachrangig | Messung in G1: Zeit und Bytes je Seite bei der gewählten Auflösung, Hochrechnung auf 10.000 Seiten gegen df -h |
| G-A4 | Synchrone gunicorn-Worker blockieren je Dateistream einen Worker für die Dauer der Übertragung; mit drei Workern ist das unter Last die wahrscheinlichste Ursache verfehlter Antwortzeiten | Latenzsonde G20 mit zwei parallelen Vorschauen großer Dateien |
| G-A5 | Wirkung von nice und ionice im Container hängt vom I/O-Scheduler des Hosts ab | Ausgabe des Schedulers unter /sys/block auf dem VPS lesen, iostat während des Performance-Laufs |
| G-A6 | Die Rangfolge A vor B vor C bleibt auch bei abweichender Bewertung einzelner Kriterien um zwei Punkte bestehen (Abschnitt 3) | Abgleich mit den Gutachten 1 und 2 im Gremium |

Alle übrigen Zahlen in diesem Gutachten sind die gekennzeichneten Annahmen der Vorschläge A, B und C und werden dort verifiziert.

## 11. Offene Fragen an den Auftraggeber

Nur echte Entscheidungen, gebündelt. Die in allen drei Vorschlägen bereits gestellten Fragen (Nullauffüllung der Objektnummer, Immoware24-Export als erster Import, Existenz von CR-01 bis CR-04 und Vorlagen für Nachforderung und Requirement Engine, Umlaut-Schreibweise der Ordner, Präfix für Nicht-Wohneinheiten, Umzug in eine geteilte Ablage, Schriftgröße der PDF-Listen, Auslegung der Grep-Prüfung, Startwerte Stufe 3, Beispieldateien der Importprofile) bleiben offen und sind vor M2 zu beantworten. Zusätzlich aus diesem Gutachten:

1. Rollenmodell Eigentümerakte (CR 10): Dürfen Sachbearbeiter Eigentümerakten vollständig einsehen? Dürfen sie die vollständige IBAN entschlüsselt anzeigen lassen, oder ist das Admin-Vorbehalt? Alle drei Vorschläge setzen unterschiedliche Standards; die Entscheidung liegt beim Auftraggeber.
2. Zwei-Faktor bei Google-Anmeldung: Soll die Anwendung zusätzlich TOTP erzwingen (Empfehlung dieses Gutachtens: ja), oder gilt die Zwei-Faktor-Absicherung des Google-Kontos als ausreichend?
3. Vorab-Entscheidung für den Fall, dass die Messung in M0 P kleiner oder gleich 3 ergibt oder t_ocr über 5 s liegt: Tarifwechsel auf mehr Kerne, Akzeptanz der Zwei-Phasen-OCR (Klassifikation aus den ersten Seiten, Rest nachgelagert, was die Bedeutung von "verarbeitet" ändert), oder Akzeptanz einer längeren Laufzeit je Objekt?
4. Sicherung außer Haus: Soll eine verschlüsselte Kopie den Server verlassen (Ziel, Aufbewahrung in Tagen)? Wer verwahrt den Verschlüsselungsschlüssel getrennt von Server und Sicherung?
5. Container-Katalog: Ist die Erweiterung um worker-io, beat und backup (sowie gegebenenfalls einen dritten Worker-Dienst für den Klassifikations-Pool, G4) gegenüber CR Abschnitt 7 akzeptiert?
6. Ist das Review Center als ganztägiges Arbeitswerkzeug der Sachbearbeiter mit hohem Nutzungsanteil vorgesehen? Die Antwort entscheidet, ob ein SPA-Inselmodul für diese eine Maske nach den ersten realen Objekten eingeplant wird (Risiko Nr. 2).
7. Benachrichtigung bei Störungen (Token-Ablauf, fehlgeschlagene Sicherung, Dead-Letter-Jobs): gewünscht, Kanal (E-Mail über Workspace-SMTP) und Empfänger?
8. Aufbewahrung der Vorschaubilder und OCR-Ausgaben auf dem Server nach Ablage in Drive: unbefristet als Cache oder Löschung nach konfigurierbarer Frist? Betrifft Plattenbedarf und Löschkonzept (G16).
