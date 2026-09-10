# Gutachten 1: Bewertung der Stack-Vorschläge A, B und C für CR-05

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Blickwinkel dieses Gutachtens: Betrieb auf einem einzelnen VPS, Wartbarkeit durch einen Entwickler und Gesamtkosten über fünf Jahre für ein kleines Unternehmen mit 2 bis 5 Anwendern.

Stand: 10.09.2026. Faktenbasis sind ausschließlich CR-05, die Befundakte vom 10.09.2026 und die drei Vorschläge (stack_A_python_monolith.md, stack_B_api_spa.md, stack_C_polyglott.md). Planungsgrößen, die nicht aus diesen Quellen stammen, tragen das Präfix ANNAHME und nennen den Weg der Verifizierung. Versionsnummern werden nicht als Fakt genannt.

---

## 1. Ergebnis und Empfehlung

| Rang | Vorschlag | Gewichtete Summe (0 bis 100) |
|---|---|---|
| 1 | A: Python-Monolith (Django, HTMX, Celery, Redis, MariaDB) | 84,0 |
| 2 | B: Python-API plus Single-Page-Anwendung (FastAPI, SQLAlchemy, Alembic, Celery, Vue) | 64,0 |
| 3 | C: Polyglott (Laravel, Livewire, Python-Worker, Redis Streams) | 60,0 |

Empfehlung: Vorschlag A umsetzen, ergänzt um die in Abschnitt 7 genannten Übernahmen aus B und C.

Begründung in Kurzform:

1. A hat die kleinste Betriebsfläche: eine Sprache, ein Image, eine Werkzeugkette, sieben Container. B braucht eine zweite Werkzeugkette (npm, Vite, Vitest, Playwright) und einen Node-Build je Deployment, C zwei Laufzeiten, drei Verträge zwischen den Sprachen, einen Codegenerator und neun Container.
2. A hat den geringsten sicherheitsrelevanten Eigenbau. Anmeldung, TOTP, Wiederherstellungscodes, Sitzungen, CSRF und Rollen kommen aus Django und django-allauth. B baut all das von Hand, mit acht Endpunkten und sechs Oberflächen, wie der Vorschlag selbst einräumt.
3. A hat die niedrigste Aufwandsschätzung (57 bis 87 Personentage gegenüber 74 bis 113 bei B und 65 bis 100 bei C) und den größten Personalmarkt für eine spätere Übergabe.
4. Die Schwachstelle von A ist das Review Center mit serverseitigem Rendering. Sie ist durch die Übernahmen aus B (vorgerenderte Seitenbilder, Vorschau-Endpunkt für die Massenbearbeitung) und C (Statistiktabelle, Chunking) so weit zu schließen, dass die Vorgaben aus CR 11 und CR 14 erfüllbar sind.

Wichtiger Vorbehalt, der für alle drei Vorschläge gilt: Die Performance-Vorgabe (10.000 Seiten unter 3 Stunden) hängt an der Kernzahl eines nicht vermessenen Servers. Alle drei Rechenwege zeigen, dass mit drei OCR-Prozessen keine Reserve bleibt und ab fünf Prozessen die Vorgabe sicher hält. Empfehlung: Der 100-Seiten-OCR-Benchmark auf dem VPS gehört in den ersten Meilenstein vor jeden Bau (so bei B und C geplant, bei A erst in M4), und die Entscheidung über einen größeren Tarif ist vor M1 zu treffen, nicht danach.

---

## 2. Bewertungsmaßstab und Rechenweg

| Nr. | Kriterium | Gewicht |
|---|---|---|
| 1 | Betrieb auf einem VPS mit Docker Compose hinter Traefik, Betriebsaufwand für einen Entwickler, Zahl der beweglichen Teile | 20 |
| 2 | OCR-, NER- und ML-Ökosystem, Integration beider KI-SDKs ohne Sprachbruch | 15 |
| 3 | Authentifizierung mit TOTP, Rollen, Admin-Konfiguration zur Laufzeit, Audit: ausgereift vorhanden oder Eigenbau | 15 |
| 4 | Datenmodell und Migrationen: ORM-Reife mit MariaDB, rückrollbare Migrationen, Transaktionssicherheit | 10 |
| 5 | Umsetzungsgeschwindigkeit für ein kleines Team, Wartbarkeit über Jahre (Personalmarkt, Dokumentation, Langzeitsupport) | 15 |
| 6 | Review Center: Massenbearbeitung mit Vorschau, Suche mit Vorschlägen, Dokumentvorschau, Antwortzeit unter 2 Sekunden unter Last | 10 |
| 7 | Trennung web/worker, Wiederaufnahme, Idempotenz, Belastbarkeit des Rechenwegs für 10.000 Seiten unter 3 Stunden | 10 |
| 8 | Risiken: Abhängigkeiten, Lizenzen, Sicherheitshistorie, Lock-in | 5 |
| | Summe | 100 |

Punkte je Kriterium von 0 bis 10. Gewichtete Summe = Summe über alle Kriterien von (Gewicht × Punkte), geteilt durch 10. Ergebnis liegt zwischen 0 und 100.

---

## 3. Gemeinsame Basis der drei Vorschläge

Die Entscheidung ist enger, als drei Vorschläge vermuten lassen. Alle drei stimmen in folgenden Punkten überein:

- MariaDB statt MySQL, Redis mit AOF und noeviction (Valkey als Ausweichoption), InnoDB FULLTEXT statt Suchdienst.
- Tesseract deu mit ocrmypdf im Worker, skip-text für Digitalseiten, OMP_THREAD_LIMIT=1, Parallelität über Prozesse, Standard C minus 1.
- NER als Kombination aus deterministischen Mustern (WE-Nummern, IBAN mit Prüfziffer, Beträge, Zeiträume) und spaCy für freie Namen; lokaler Klassifikator als TF-IDF plus lineares Modell mit Kalibrierung; kein lokales LLM; optionaler classifier-Container als Ausbaupfad.
- Eigene KI-Provider-Schnittstelle über die offiziellen SDKs von OpenAI und Anthropic, kein LangChain oder LiteLLM, Maskierung vor dem Aufruf, Kostenprotokoll in ai_calls.
- openpyxl für Excel, ReportLab mit Portierung der vorhandenen HVM-CI-Bausteine für PDF.
- Legacy-Bezeichnung 05_Sonstiges nur als Konfigurationswert im Seed, nicht im Anwendungscode (Auflösung des Widerspruchs aus Befund 6.4).
- Objektnummer als führende Ziffernfolge mit konfigurierbarer Stellenzahl 2 bis 6 (Vorschlag zur Abweichung vom CR-Wortlaut, Befund 4).
- Traefik-Werte, Kernzahl und RAM als Platzhalter in .env, Messung auf dem Server vor dem ersten Deployment.
- Job-Status und Datei-Hash in der Datenbank als einzige Wahrheit für Wiederaufnahme und Idempotenz.
- Backup-Container mit täglichem Dump und Volume-Archiv.

Damit reduziert sich die Entscheidung auf die Web-Schicht (Django mit HTMX, FastAPI mit Vue, Laravel mit Livewire) und auf die Zahl der Sprachen (eine, anderthalb, zwei). Genau dort liegen die Unterschiede in Betrieb, Wartung und Kosten.

Alle drei gehen über den Container-Katalog des CR (web, worker, queue, db, optional classifier) hinaus: A um worker-io, beat und backup; B um migrate, worker-io, beat und backup; C um web-jobs, bridge, scheduler und backup. Alle drei kennzeichnen das als Vorschlag. Die Annahme des erweiterten Katalogs ist eine Entscheidung des Auftraggebers (Abschnitt 10).

---

## 4. Kritische Prüfung je Vorschlag

### 4.1 Vorschlag A: Python-Monolith

Stärken: geschlossenes Ökosystem, ein Image für web, worker, worker-io und beat, Pipeline als Zustandsautomat in der Datenbank statt Celery-Chain, ehrliche Aufzählung der Celery-Fallen (visibility_timeout, acks_late, prefetch), vollständiges Annahmenverzeichnis, die Betriebsaufwände sind als ANNAHME gekennzeichnet und mit Zeiterfassung zu verifizieren.

Gefundene Lücken, Widersprüche und Schönrechnen:

1. Rechenweg ist der optimistischste der drei. A setzt t_ocr = 3,0 s (B: 4,0 s, C: 3,5 s), keinen Skalierungsverlust bei parallelen Prozessen (C: 85 Prozent Effizienz) und keine Download-Zeit aus Drive an. Das Chunking großer Einzeldokumente steht nur als Stellhebel 4, nicht als Konstruktionsentscheidung. Nachrechnung mit den eigenen Planwerten von A, ergänzt um die Effizienz-ANNAHME von C und A's eigenen Wert von 2 s je Drive-Operation:

   ```
   OCR:            18.400 s / (3 × 0,85)      = 7.216 s  ≈ 120 min
   Download:       1.250 Dok. × 2 s / 4       =   625 s  ≈  10 min
   Klassifikation:                                        ≈   4 min
   KI Stufe 3:     250 × 6 s / 4              =   375 s  ≈   6 min
   Ablage seriell: 1.250 × 2 s                = 2.500 s  ≈  42 min
   Summe ohne Überlappung                                 ≈ 182 min
   ```

   Der von A als ungünstigster Fall ausgewiesene Wert (154 Minuten) ist damit nicht der ungünstigste. Mit Überlappung der IO-Schritte bleibt das Ergebnis bei P = 3 unter 180 Minuten, aber ohne Reserve. A's eigene Formel bestätigt: bei 5 s je Seite sind mindestens 5 OCR-Prozesse und damit 6 Kerne nötig. Fazit: kein Fehler in der Richtung, aber das Etikett "ungünstigster Fall" ist zu freundlich.

2. Dateivorschau über gunicorn. A liefert PDFs als Stream über den Browser-Viewer aus, mit ANNAHME 3 synchronen gunicorn-Workern. Zwei bis drei gleichzeitig geöffnete große PDFs belegen alle Worker, und die 2-Sekunden-Vorgabe für das Review Center ist gefährdet. Der Vorschlag adressiert das nicht. Lösung: vorgerenderte Seitenbilder aus der Pipeline (Übernahme aus B) und gthread-Worker oder höhere Worker-Zahl für web.

3. Netz backend nicht als internal markiert. Damit haben auch db und queue potenziell ausgehenden Internetzugang. B trennt sauber in app (internal) und egress. Kleine, aber kostenlose Härtung, die fehlt.

4. Container-Härtung (nicht privilegierter Nutzer, no-new-privileges, read-only Root-Dateisystem wo möglich) wird nicht erwähnt. C führt sie auf.

5. Datenbank-Trigger gegen UPDATE und DELETE auf der Audit-Tabelle ist nur optional. Für ein Protokoll, das jede Review-Aktion mit Nutzer und Zeitstempel belegen soll (CR 15), gehört er als Pflicht in M1.

6. OCR-Benchmark erst in M4. Die Entscheidung über P und einen eventuellen Tarifwechsel braucht den Messwert vor M1, nicht nach dem Bau von Datenmodell und Drive-Adapter. B und C legen den 100-Seiten-Probelauf in M0.

7. Vorschau der Zielpfade bei der Massenbearbeitung wird nicht ausdrücklich beschrieben. CR 11 verlangt Massenbearbeitung mit Vorschau. B beschreibt dafür einen Endpunkt, der die Benennungsfunktion ohne Schreiben ausführt. In A ist das als HTMX-Partial ebenso möglich, muss aber eingeplant werden.

8. Abhängigkeiten mit Prüfbedarf: allauth ist ein großes Paket, dessen MFA-Modul A selbst als "zum Umsetzungszeitpunkt prüfen" kennzeichnet. Der Datenbanktreiber mysqlclient steht nach meiner Kenntnis unter GPL (zu verifizieren); für internen Betrieb unkritisch, bei Weitergabe der Software zu prüfen. Alternativen (PyMySQL, MariaDB Connector) sind ohne Änderung der Fachlogik austauschbar.

9. Betriebsaufwand: A schätzt 2 bis 4 Stunden je Woche zuzüglich Monats-, Quartals- und Jahrestermine. Das ist die höchste und aus meiner Sicht ehrlichste Schätzung der drei. Die Schätzungen von B und C sind anders zugeschnitten und nicht direkt vergleichbar (Abschnitt 6).

### 4.2 Vorschlag B: Python-API plus Single-Page-Anwendung

Stärken: klarste Trennung von Fachlogik (Domänenpaket ohne I/O) und Oberfläche, Netztrennung app/egress, vorgerenderte Seitenbilder mit Cache-Headern, Keyset-Paginierung mit benannten Indizes, getrennte Verbindungspools für web und worker, Server-Sent Events für Fortschritt, Szenariotabelle in der Performance-Rechnung mit ausdrücklich ausgewiesenen Fehlfällen, ehrliches eigenes Fazit ("nur wählen, wenn der Entwickler TypeScript sicher beherrscht").

Gefundene Lücken, Widersprüche und Risiken:

1. Authentifizierung vollständig im Eigenbau: Login, TOTP mit Wiederherstellungscodes, Sitzungen in Redis, CSRF über Header und Origin, Ratenbegrenzung, Passwort-Zurücksetzen, Google-Anmeldung über authlib. B nennt das selbst "Aufwand ohne fachlichen Mehrwert". Aus Betriebssicht ist es mehr: jede Schwachstelle darin ist eine Sicherheitslücke, die kein Upstream-Projekt für uns schließt.

2. Zwei Werkzeugketten und zwei Sperrdateien (pip oder uv, npm). Die npm-Lieferkette ist nach Erfahrung wartungsintensiver; Vitest und Playwright bringen Browser-Binärdateien in die CI. Über fünf Jahre ist mindestens eine Hauptversionsmigration der Frontend-Kette (Vue, Vite, TypeScript-Tooling) wahrscheinlich, zusätzlich zur Python-Seite.

3. Deployment: Der Node-Build läuft im Multi-Stage-Build auf dem VPS und konkurriert mit einer laufenden Objektverarbeitung um CPU und RAM. B's Abhilfe (Images in CI bauen, Registry) ist richtig, aber ein weiterer beweglicher Teil mit Zugangsdaten.

4. Rechenweg: ehrlich, mit Fehlfällen. Zwei Lücken: kein Skalierungsverlust im Rechenweg (nur als Messpunkt geplant) und kein Chunking großer Einzeldokumente. Eine Gesamtabrechnung mit vielen hundert Seiten läuft in B auf einem Prozess seriell und reißt die Vorgabe unabhängig von n_ocr (C erkennt dieses Risiko und löst es).

5. Dienst migrate als Einmaldienst mit depends_on: Schlägt die Migration fehl, startet kein Dienst. Rollback bleibt ein Befehl, aber das Verhalten ist zu dokumentieren. A wählt bewusst den manuellen Migrationsschritt, um eine Neustartschleife zu vermeiden; beides ist vertretbar.

6. Höchste Aufwandsschätzung (74 bis 113 PT), davon 30 bis 40 PT für die SPA. Nach B's eigener Tabelle in Abschnitt 2.4 bringt die SPA nur im Review Center und im Listen-Parser echten Nutzen; alle anderen Masken kosten mehr als bei Server-Rendering und bringen nichts zurück.

7. Nachfolgerisiko: Wer die Anwendung übernimmt, muss Python, FastAPI, SQLAlchemy, Celery und zusätzlich TypeScript, Vue, Vite und Pinia beherrschen. Der Kreis ist deutlich kleiner als für einen Django-Monolithen.

### 4.3 Vorschlag C: Polyglott (Laravel und Python-Worker)

Stärken: der vollständigste und konservativste Rechenweg (Skalierungseffizienz, Download-Zeit, Chunking als zwingende Konstruktionsentscheidung), zwei Datenbankkonten mit eingeschränkten Rechten für den Worker (setzt technisch durch, dass Stammdaten nur nach Bestätigung entstehen), Einstellungskatalog als JSON-Schema mit generierten Admin-Formularen, nice und ionice für OCR-Prozesse, aggregierte Statistiktabelle für Fortschrittszähler, Container-Härtung, Fortify und Socialite als Erstpartei-Pakete für Anmeldung und Google-Login, Horizon als Queue-Dashboard, Lizenzhinweise zu Ghostscript und Redis.

Gefundene Lücken, Widersprüche und Risiken:

1. Neun Container, dazu supervisord mit nginx und php-fpm im web-Image, zwei PHP-Langzeitprozesse (bridge, Horizon) mit Pflicht zum Selbstneustart gegen Speicherwachstum. Das ist die größte Betriebsfläche der drei Vorschläge, für einen Entwickler auf einem einzelnen VPS.

2. Die sprachübergreifende Queue ist Eigenbau: Consumer mit XREADGROUP, XAUTOCLAIM, Heartbeat, Dead Letter und Reconcile-Kommando, ohne Dashboard und ohne fertige Wiederholungsstrategie auf der Python-Seite. C räumt das ein. Celery liefert genau das fertig.

3. Drei Verträge plus Codegenerator: Jede Änderung an Dokumentunterarten, Enums, Einstellungen oder Nachrichten berührt PHP und Python. C schätzt den Mehraufwand auf 15 bis 25 Prozent (ANNAHME A13 des Vorschlags). Semantische Drift (gleiche Spalte, andere Bedeutung) fängt kein Werkzeug.

4. Keine Transaktion über die Sprachgrenze. Worker schreibt Klassifikation, bridge schreibt Ablageentscheidung; Konsistenz hängt an der Zustandsmaschine in pipeline_jobs und an Disziplin.

5. Fortify ist bewusst ohne Oberfläche ausgeliefert. Anmelde-, TOTP-Einrichtungs- und Wiederherstellungsseiten sind als Blade-Views selbst zu bauen oder aus einem Starter-Kit zu übernehmen. Dieser Aufwand ist im Vorschlag nicht ausgewiesen.

6. Versionsnummern sind entgegen der Vorgabe eingeflossen (Python 3.12.x, redis:7-alpine im Compose-Auszug). Geringfügig, aber ein Zeichen für ungeprüfte Konkretheit.

7. Nicht als ANNAHME gekennzeichnete Aussagen: eigener MariaDB-Treiber in Laravel, Verfügbarkeit von SELECT FOR UPDATE SKIP LOCKED in der eingesetzten MariaDB-Version. Beides ist plausibel, aber zum Umsetzungszeitpunkt zu prüfen.

8. Personalmarkt: Laravel allein ist gut besetzt, Python-ML allein ebenso. Die Kombination in einer Person ist selten. Laravel liefert jährlich eine Hauptversion; damit entsteht ein jährliches Upgrade-Projekt zusätzlich zur Python-Seite, jedes Mal mit beiden Testsuiten und Vertragstests.

9. Listen und Nachforderungsschreiben entstehen asynchron über Stream, Worker, Ereignis und Drive-Upload. Sekunden bis eine Minute Verzögerung, Fehler kommen verzögert an. C erkennt das und fordert einen sichtbaren Auftragsstatus; es bleibt ein Bedienungsnachteil gegenüber A, wo die Liste im io-Worker desselben Codebestands entsteht.

---

## 5. Bewertung je Kriterium

### Kriterium 1 (Gewicht 20): Betrieb auf einem VPS, Betriebsaufwand, bewegliche Teile

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 9 | Ein Image, eine Sprache, sieben Container, kein Build-Schritt für die Oberfläche (HTMX als statische Datei). Abzug: web-Image trägt Tesseract und Ghostscript, jede OCR-Sicherheitsaktualisierung erzwingt Rebuild und Neustart von web; Netz backend nicht internal. |
| B | 6 | Acht Container, zur Laufzeit ähnlich schlank wie A, aber Node-Build je Deployment auf dem VPS oder zusätzliche Registry, zwei Sperrdateien, Browser-Testwerkzeuge in der CI. |
| C | 4 | Neun Container, zwei Laufzeiten, supervisord im web-Image, PHP-Langzeitprozesse mit Selbstneustart, Eigenbau-Consumer ohne Dashboard, Codegenerator als Build-Schritt. Zwei Sicherheitsupdate-Pfade (composer audit, pip-audit). |

### Kriterium 2 (Gewicht 15): OCR-, NER- und ML-Ökosystem, beide KI-SDKs ohne Sprachbruch

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 10 | ocrmypdf, spaCy, scikit-learn, rapidfuzz, ReportLab, openpyxl, beide SDKs und der Drive-Client im selben Prozessraum und Image. Kein Sprachbruch an keiner Stelle. |
| B | 9 | Gleiches Python-Ökosystem für alles Fachliche; die Oberfläche ist reine Bedienschicht ohne Fachlogik. Kleiner Abzug, weil Kataloge (Hauptordner, Einheitentypen) per API in das Frontend gespiegelt werden müssen. |
| C | 6 | Innerhalb des Workers kein Bruch, aber die Ergebnisse überqueren die Sprachgrenze: Klassifikation in Python, Ablageentscheidung und Benennung in PHP, Trainingsdaten sammelt PHP, trainiert wird in Python, Normalisierung der Einheitenbezeichnungen wird auf beiden Seiten gebraucht. C benennt das selbst als doppelte Logik mit Golden-Tests. |

### Kriterium 3 (Gewicht 15): Authentifizierung, Rollen, Admin-Konfiguration, Audit

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 8 | Anmeldung, Passwort-Hashing, TOTP mit Wiederherstellungscodes und Google-Login aus django-allauth, Rollen über Django-Gruppen, Nutzerverwaltung über den Django-Admin. Eigenbau: kleine Konfigurations-App mit Historie und Cache sowie Audit-Tabelle über die Service-Schicht. Prüfpunkt: Reife des allauth-MFA-Moduls zum Umsetzungszeitpunkt. |
| B | 3 | Nahezu alles Eigenbau und sicherheitsrelevant: Login, TOTP, Wiederherstellungscodes, Sitzungen, CSRF, Ratenbegrenzung, Passwort-Zurücksetzen, OIDC, dazu sechs Frontend-Ansichten. Konfiguration und Audit ebenfalls selbst gebaut (sauber entworfen, aber ohne Upstream). |
| C | 8 | Fortify (TOTP, Wiederherstellungscodes, Ratenbegrenzung, Passwort-Reset) und Socialite mit Domain-Prüfung sind fertig, Policies für zwei Rollen trivial, Audit mit Trigger, Einstellungskatalog mit generierten Formularen ist eine gute Idee. Abzug: Fortify ohne Oberfläche, Audit und Einstellungen werden aus zwei Laufzeiten geschrieben und gelesen. |

### Kriterium 4 (Gewicht 10): Datenmodell und Migrationen

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 9 | Django ORM mit MariaDB als offiziell unterstütztem Backend, Migrationen mit automatisch erzeugter Rückwärtsoperation für Schemaänderungen, Datenmigrationen mit ausdrücklicher Rückwärtsfunktion, Transaktionen über atomic. Prüfpunkt: JSONField-Verhalten auf MariaDB, von A selbst benannt. |
| B | 8 | SQLAlchemy und Alembic sind ausgereift; downgrade je Migration muss geschrieben und geprüft werden (autogenerate hilft, ersetzt aber keine Prüfung). Ein Modell, ein Zugriffspfad. |
| C | 6 | Eloquent-Migrationen mit down() sind ausgereift, aber der Python-Worker reflektiert dasselbe Schema als zweiter Zugriffspfad mit Migrationsstufenprüfung. Keine Transaktion über die Grenze. Pluspunkt: zwei Datenbankkonten mit Rechtetrennung. |

### Kriterium 5 (Gewicht 15): Umsetzungsgeschwindigkeit und Wartbarkeit über Jahre

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 9 | Niedrigste Aufwandsschätzung (57 bis 87 PT), Django in LTS-Linie mit planbarem Anhebungsrhythmus, größter Personalmarkt, eine Dokumentationswelt. Abzug für allauth als Paket mit Anpassungsbedarf bei Hauptversionen. |
| B | 5 | Höchste Schätzung (74 bis 113 PT), zwei Ökosysteme mit unterschiedlichem Alterungstempo, Frontend-Werkzeuge wechseln Hauptversionen schneller als Django. Nachfolger muss beide Welten beherrschen. |
| C | 5 | Mittlere Schätzung (65 bis 100 PT), kurze Zeit bis zum ersten Review Center dank Fortify und Livewire, aber jährliche Laravel-Hauptversion plus Python-Pflege plus Vertragstests bei jedem Update. Kombination Laravel und Python-ML in einer Person ist am Personalmarkt selten. |

### Kriterium 6 (Gewicht 10): Review Center

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 6 | Massenbearbeitung, Suche mit Vorschlägen und Pflichtfelder sind mit HTMX-Partials machbar; Seitenbereiche als Formular, Fortschritt per Polling, Vorschau über den Browser-Viewer. Machbar und günstig (M10 mit 4 bis 6 PT), aber weniger flüssig, und die PDF-Auslieferung über synchrone gunicorn-Worker ist ein Risiko für die 2-Sekunden-Vorgabe, das erst mit Seitenbildern aus B behoben ist. |
| B | 9 | Stärkster Entwurf: Raster mit virtuellem Scrollen, Vorschau-Endpunkt für Zielpfade je Zeile, vorgerenderte Seitenbilder, Seitenbereichsauswahl, Kandidatenliste mit Zeitstrahl, SSE. Abzug nur für den Preis (12 bis 18 PT für M4) und die Notwendigkeit, dieselbe Qualität auch für die einfachen Masken zu bezahlen. |
| C | 7 | Livewire liefert reaktive Formulare, Suche mit Vorschlägen und Massenbearbeitung ohne SPA-Projekt, Statistiktabelle für Zähler ist ein guter Kunstgriff. Abzug: Livewire-Roundtrips bei 40 Zeilen sind gesprächig, Listen und Vorschauen entstehen asynchron über die Sprachgrenze. |

### Kriterium 7 (Gewicht 10): Trennung web/worker, Wiederaufnahme, Idempotenz, Belastbarkeit des Rechenwegs

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 7 | Idempotenz solide: Zustandsautomat in der DB, Statusprüfung zu Beginn jedes Tasks, Abgleich alle fünf Minuten, kleine Tasks je Dokument, Celery-Fallen benannt. Rechenweg jedoch der optimistischste (kein Skalierungsverlust, keine Download-Zeit, Chunking nur als Hebel), siehe Nachrechnung in 4.1. |
| B | 7 | Gleiche Idempotenzmechanik wie A, saubere Trennung worker/worker-io, Szenariotabelle mit ehrlich ausgewiesenen Fehlfällen, Messung der Skalierungsverluste geplant. Abzug: kein Chunking großer Einzeldokumente, Skalierungsverlust nicht im Rechenweg. |
| C | 8 | Konservativster Rechenweg (85 Prozent Effizienz, Download eingerechnet, Chunking als Pflicht mit konfigurierbarer Blockgröße), Claim über atomares UPDATE, Heartbeat, XAUTOCLAIM, Reconcile aus der DB. Abzug, weil der Consumer Eigenbau ist und die Zustandsmaschine über zwei Laufzeiten identisch gehalten werden muss. |

### Kriterium 8 (Gewicht 5): Risiken

| Vorschlag | Punkte | Begründung |
|---|---|---|
| A | 7 | Quelloffene Bibliotheken mit großen Gemeinschaften; Prüfpunkte: mysqlclient-Lizenz, Ghostscript unter AGPL (internen Betrieb unkritisch), Redis-Lizenzwechsel (Valkey möglich), allauth als große Abhängigkeit, Celery-Konfigurationsfehler als bekannte Fehlerquelle. Lock-in auf Google Drive ist durch den CR vorgegeben und in allen drei Vorschlägen gleich. |
| B | 5 | Zusätzlich npm-Lieferkette mit vielen transitiven Abhängigkeiten, schnelle Alterung der Frontend-Werkzeuge und der selbst gebaute Authentifizierungspfad als Sicherheitsrisiko. |
| C | 5 | Zwei Paketökosysteme, Eigenbau-Consumer, PHP-Langzeitprozesse, Speicherdruck durch Modellkopien in leichten Prozessen. Lizenzhinweise sind vorbildlich dokumentiert. |

---

## 6. Rechenweg der gewichteten Summen

| Kriterium | Gewicht | A Punkte | A gewichtet | B Punkte | B gewichtet | C Punkte | C gewichtet |
|---|---|---|---|---|---|---|---|
| 1 Betrieb | 20 | 9 | 180 | 6 | 120 | 4 | 80 |
| 2 Ökosystem | 15 | 10 | 150 | 9 | 135 | 6 | 90 |
| 3 Auth, Rollen, Konfiguration, Audit | 15 | 8 | 120 | 3 | 45 | 8 | 120 |
| 4 Datenmodell, Migrationen | 10 | 9 | 90 | 8 | 80 | 6 | 60 |
| 5 Geschwindigkeit, Wartbarkeit | 15 | 9 | 135 | 5 | 75 | 5 | 75 |
| 6 Review Center | 10 | 6 | 60 | 9 | 90 | 7 | 70 |
| 7 Pipeline, Performance | 10 | 7 | 70 | 7 | 70 | 8 | 80 |
| 8 Risiken | 5 | 7 | 35 | 5 | 25 | 5 | 25 |
| Summe gewichtet | 100 | | 840 | | 640 | | 600 |
| Normiert (geteilt durch 10) | | | 84,0 | | 64,0 | | 60,0 |

Empfindlichkeit: Selbst wenn Kriterium 6 (Review Center) doppelt gewichtet würde (20 statt 10) und Kriterium 1 dafür auf 10 sinkt, ergäbe sich A 81,0, B 67,0, C 63,0 (Rechenweg: gleiche Punkte, Gewichte 10, 15, 15, 10, 15, 20, 10, 5). Die Reihenfolge ist robust gegenüber der Gewichtung. Sie kippt nur, wenn ein Entwickler mit sehr starken TypeScript-Kenntnissen und ausdrücklicher Priorität auf Bedienkomfort vorausgesetzt wird; das ist eine Frage an den Auftraggeber (Abschnitt 10).

---

## 7. Gesamtkosten über fünf Jahre (qualitativ)

Die drei Vorschläge schätzen ihren Betriebsaufwand unterschiedlich zugeschnitten (A in Stunden je Woche plus Quartals- und Jahrestermine, B in Stunden je Monat und je Objekt, C in Personentagen je Monat und Jahr) und sämtlich als ANNAHME. Ein Vergleich der Zahlen ist deshalb nicht belastbar; belastbar sind die Kostentreiber.

| Kostentreiber | A | B | C |
|---|---|---|---|
| Erstellung (Schätzung der Autoren, PT) | 57 bis 87 | 74 bis 113 | 65 bis 100 |
| Zu pflegende Werkzeugketten | 1 (Python) | 2 (Python, Node) | 2 (PHP, Python) plus Codegenerator |
| Hauptversionsrhythmus, der Upgrade-Projekte auslöst | Django LTS in mehrjährigem Abstand, allauth, Celery | Python-Seite wie A, zusätzlich Vue, Vite, TypeScript-Tooling mit kürzeren Zyklen | Laravel jährlich, Livewire eigene Zyklen, zusätzlich Python-Seite und Vertragstests |
| Container im Regelbetrieb | 7 | 8 | 9 |
| Build-Pipeline | Ein Image, Build auf dem VPS möglich | Node-Build; Registry empfohlen | Zwei Images, Generator-Schritt |
| Sicherheitsrelevanter Eigenbau | Konfiguration, Audit | Gesamte Authentifizierung, Sitzungen, CSRF, Konfiguration, Audit | Stream-Consumer, Verträge |
| Ersetzbarkeit des Entwicklers | Python und Django: sehr guter Markt | Python plus TypeScript und SPA-Framework: kleinerer Kreis | Laravel plus Python-ML: seltene Kombination |
| Wiederkehrende Fremdkosten | VPS, KI-Aufrufe (Kostenlimit), Workspace vorhanden | wie A | wie A |

Einordnung: Über fünf Jahre entsteht der Unterschied nicht bei den Fremdkosten (identisch) und nur zum Teil bei der Erstellung, sondern bei den Upgrade-Projekten und der Vertretbarkeit. Zwei Werkzeugketten bedeuten grob zwei Upgrade-Kalender; B und C tragen diesen Posten jedes Jahr, A nicht. ANNAHME G1: Über fünf Jahre fallen bei A zwei bis drei planbare Django-LTS-Anhebungen an, bei B und C zusätzlich jährlich eine Anhebung der zweiten Kette. Verifizierung: Release-Kalender von Django, Laravel und Vue zum Umsetzungszeitpunkt prüfen und im Betriebshandbuch als Wartungsplan festhalten.

---

## 8. Übernahmen aus B und C in die Empfehlung (Graft)

Aus B:

1. Vorgerenderte Seitenbilder je Dokumentseite in der Pipeline erzeugen, im ocr_cache ablegen und mit Cache-Headern ausliefern. Die Dokumentvorschau im Review Center zeigt Seitenbilder, das Original-PDF wird nur auf ausdrücklichen Wunsch gestreamt. Schützt die 2-Sekunden-Vorgabe gegen blockierte gunicorn-Worker.
2. Vorschau-Endpunkt für die Massenbearbeitung, der die Benennungsfunktion und den Folder-ID-Cache ohne Schreiben ausführt und je Dokument den Zielpfad mit Warnungen zurückgibt. In A als HTMX-Partial umsetzen; erfüllt CR 11 wörtlich.
3. Netztrennung: backend als internal, zusätzliches Netz egress nur für web, worker, worker-io und beat. db und queue ohne Internetzugang.
4. Keyset-Paginierung und benannte Indizes auf documents(object_id, status), review_items(status, created_at), owner_unit_assignments(unit_id, valid_from, valid_to); getrennte Verbindungspools mit Obergrenzen für web und worker, damit der Worker die Verbindungen der Oberfläche nicht aufbraucht.
5. Szenariotabelle in der Performance-Dokumentation mit ausdrücklich ausgewiesenen Fehlfällen (ungünstiger Scan-Anteil, ungünstige Seitenzeit) statt eines einzelnen "ungünstigsten Falls".
6. Korpusgenerator mit konfigurierbarem Scan-Anteil und Messung der Skalierungsverluste mit P = 1, 2 und C minus 1 im ersten Probelauf.

Aus C:

7. Chunking großer Einzeldokumente in Seitenbereiche (pikepdf, Blockgröße konfigurierbar) als Konstruktionsentscheidung in M4, nicht als Stellhebel. Ohne Chunking kann eine einzelne Gesamtabrechnung mit vielen hundert Seiten die Vorgabe unabhängig von P reißen. Dieselbe Seitenbereichslogik trägt die relationale Zuordnung der Einzelabrechnungen (CR 6).
8. Skalierungseffizienz als ANNAHME (C: 85 Prozent) in den Rechenweg aufnehmen und in M0 messen; Download-Zeit aus Drive in den Rechenweg aufnehmen.
9. 100-Seiten-OCR-Benchmark auf dem VPS in M0 vor M1, mit dokumentierter Entscheidung über P und gegebenenfalls Tarifwechsel.
10. OCR-Prozesse mit nice und ionice starten, OCR-Temp auf Platte statt tmpfs.
11. Aggregierte Statistiktabelle je Objekt für Fortschrittszähler statt Zählabfragen über die Job-Tabelle beim Polling.
12. Zwei Datenbankkonten: web mit vollem DML und DDL für Migrationen, worker nur mit SELECT auf Stammdaten und INSERT/UPDATE auf Verarbeitungstabellen sowie INSERT auf audit_events. In Django über eine zweite Datenbankverbindung im worker-Container (Umgebungsvariable je Dienst) umsetzbar. Setzt technisch durch, dass owners, units und Zuordnungen nur nach Bestätigung im Review Center entstehen. Vorbehalt: Prüfen, ob die Celery-Tasks für Listen und Ablage weitere Schreibrechte brauchen; dann feiner trennen.
13. Datenbank-Trigger gegen UPDATE und DELETE auf audit_events als Pflicht in M1.
14. Konkrete Volltextparameter: Stoppwortliste deaktivieren, Mindesttokenlänge auf 2, damit Kürzel wie GE oder TG gefunden werden.
15. Einstellungskatalog als JSON-Schema-Datei im Repository, aus dem die Admin-Formulare der Konfigurations-App generiert werden. Spart je Schlüssel eine handgeschriebene Maske und dokumentiert die Konfiguration an einer Stelle.
16. Container-Härtung: nicht privilegierter Nutzer, no-new-privileges, read-only Root-Dateisystem wo möglich, Docker Secrets über Entrypoint.
17. Circuit Breaker im KI-Provider-Router, damit ein dauerhaft gestörter Primäranbieter nicht bei jedem Dokument einen Timeout kostet.

---

## 9. Risiken der Empfehlung (Vorschlag A)

1. Bedienkomfort des Review Centers. Serverseitiges Rendering mit HTMX ist für 2 bis 5 geübte Anwender tragbar, aber ohne Drag-and-drop, ohne Server-Push und mit Formularen für Seitenbereiche. Falls sich nach den ersten Objekten herausstellt, dass die Sachbearbeiter den größten Teil ihrer Zeit im Review Center verbringen und die Bedienung als Bremse empfinden, entsteht Nachrüstaufwand. Abfederung: Die Übernahmen 1 und 2 aus B, und die Architektur so anlegen, dass eine JavaScript-Insel (etwa Alpine.js oder ein einzelnes Vue-Modul) nur für das Review-Raster nachgerüstet werden kann, ohne die Anwendung in eine SPA zu verwandeln.
2. Celery-Konfiguration als Fehlerquelle. visibility_timeout, acks_late, prefetch, Zeitlimits und Speicher-Recycling müssen zusammenpassen, sonst Doppelverarbeitung oder hängende Jobs. Abfederung: Integrationstest mit hartem Abbruch des Worker-Prozesses in M4, wiederholt bei jeder Celery-Anhebung; Statusprüfung zu Beginn jedes Tasks.
3. Ein Image für alles. Sicherheitsaktualisierungen in Tesseract, Ghostscript oder spaCy erzwingen Rebuild und Neustart auch von web. Abfederung: Deployment-Fenster außerhalb laufender Objektverarbeitung, Warm-Shutdown der Worker, Abgleich-Task reiht unfertige Jobs neu ein. Alternativ später ein zweites Image-Ziel nur für web aus demselben Dockerfile.
4. Synchroner Web-Prozess. Lang laufende Aktionen dürfen niemals im Request laufen. Ein einzelner Verstoß (etwa Listenerzeugung im Klick) blockiert Worker und verletzt die 2-Sekunden-Vorgabe. Abfederung: Regel im Entwicklungsleitfaden, gunicorn-Timeout als Sicherung, Seitenbilder statt PDF-Streams.
5. allauth als große Abhängigkeit. Hauptversionen haben in der Vergangenheit Anpassungen erfordert; das MFA-Modul ist jünger als der Rest. Abfederung: Version im Lockfile fixieren, Anhebung geplant mit Testsuite, Login und TOTP als Ende-zu-Ende-Test.
6. Performance hängt an der Kernzahl. Bei drei OCR-Prozessen keine Reserve; das gilt für jeden Stack. Abfederung: Benchmark in M0, Tarifentscheidung vor M1, Chunking und die Stellhebel aus A 5.4.
7. Deutsche Volltextsuche in MariaDB ohne Stammformreduktion und Kompositazerlegung. Die Suche läuft primär über Metadaten (dem CR entsprechend); wer Recherche im Volltext erwartet, wird enttäuscht. Abfederung: Adapter für einen späteren Suchdienst vorgesehen; Erwartung mit dem Auftraggeber abstimmen.
8. Lizenz- und Weitergabefrage. Ghostscript (AGPL) und nach meiner Kenntnis mysqlclient (GPL) sind für internen Betrieb unkritisch, bei Weitergabe der Software an Dritte prüfungspflichtig. Abfederung: Frage an den Auftraggeber (Abschnitt 10); Treiber austauschbar.
9. Schlüsselverwaltung auf einer Maschine. Fernet-Schlüssel, Django-Secret und API-Schlüssel liegen in .env auf dem VPS; Sicherungen enthalten verschlüsselte Token und IBAN-Werte. Abfederung: Schlüssel getrennt von der Sicherung verwahren, Restore-Probe quartalsweise, Verantwortlichkeit benennen.
10. Der CR setzt eine nicht spezifizierte Basis voraus (Akten 01 bis 04, Requirement Engine, Nachforderungsgenerator; Befund 6.2 und 6.3). A plant sie minimal mit; der Aufwand in M2, M5 und M8 ist unsicher, solange kein Lastenheft vorliegt. Das gilt für alle drei Vorschläge.

---

## 10. Annahmen dieses Gutachtens

| Nr. | ANNAHME | Verifizierung |
|---|---|---|
| G1 | Über fünf Jahre fallen bei A zwei bis drei planbare Django-LTS-Anhebungen an; bei B und C zusätzlich jährlich eine Anhebung der zweiten Werkzeugkette (Frontend-Tooling bzw. Laravel). | Release-Kalender von Django, Laravel, Vue und Vite zum Umsetzungszeitpunkt prüfen und als Wartungsplan im Betriebshandbuch festhalten. |
| G2 | Der umsetzende Entwickler beherrscht Python sicher; TypeScript mit SPA-Framework und PHP mit Laravel werden nicht vorausgesetzt. | Rückfrage beim Auftraggeber vor Freigabe des Stacks (Abschnitt 11, Frage 1). |
| G3 | Über die Laufzeit steht ein Entwickler ohne feste Vertretung zur Verfügung; Ersetzbarkeit ist deshalb ein Kostentreiber. | Rückfrage beim Auftraggeber. |
| G4 | Die Software wird nicht an Dritte weitergegeben; Lizenzfragen zu AGPL- und GPL-Komponenten sind damit für den Betrieb unkritisch. | Rückfrage beim Auftraggeber (Abschnitt 11, Frage 5). |
| G5 | Die Nachrechnung in 4.1 verwendet A's eigene Planwerte (t_ocr 3,0 s, d 0,40, 2 s je Drive-Operation, Parallelität 4) und die Effizienz-ANNAHME von C (85 Prozent). Sie zeigt eine Richtung, keinen Messwert. | 100-Seiten-Benchmark auf dem VPS in M0 ersetzt alle Planwerte. |
| G6 | Die Lizenz von mysqlclient ist GPL. | Lizenzdatei des Pakets zum Umsetzungszeitpunkt prüfen; bei Bedarf PyMySQL oder MariaDB Connector einsetzen. |
| G7 | Die Aufwandsspannen der drei Vorschläge sind untereinander vergleichbar, weil sie denselben CR-Umfang abdecken und denselben Entwicklertyp voraussetzen. | Nach M4 des gewählten Vorschlags Ist-Aufwände gegen die Schätzung stellen. |

---

## 11. Offene Fragen an den Auftraggeber

Nur echte Entscheidungen; die fachlichen Fragen aus den Anhängen der Vorschläge (Objektnummer, Umlaute, Präfixe, Shared Drive, PDF-Spalten) werden hier nicht wiederholt.

1. Kenntnisprofil des umsetzenden Entwicklers: Python sicher? TypeScript mit SPA-Framework? PHP mit Laravel? Gibt es eine Vertretung, und welche Sprachen beherrscht sie? Die Antwort entscheidet, ob die Empfehlung A bestehen bleibt oder B (bei starkem TypeScript) neu zu bewerten ist.
2. Stellenwert des Review Centers: Reicht ein serverseitig gerendertes Werkzeug mit Formularen, Seitenbildern, Suche mit Vorschlägen und Fortschritt per Polling, oder wird eine SPA-artige Bedienung (Drag-and-drop, Live-Aktualisierung, grafische Seitenbereichsauswahl) als Pflicht gesehen? Im zweiten Fall ist der Mehraufwand von B (30 bis 40 PT) bewusst zu bezahlen.
3. Erweiterter Container-Katalog: Werden worker-io, beat und backup zusätzlich zu web, worker, queue und db akzeptiert?
4. Tarifentscheidung: Falls der Benchmark in M0 weniger als fünf nutzbare OCR-Prozesse ergibt, ist ein größerer VPS-Tarif vor M1 zulässig, oder sollen zuerst die Stellhebel (fast-Sprachdaten, Zwei-Phasen-OCR) ausgereizt werden?
5. Zwei-Phasen-OCR als Stellhebel: Darf "verarbeitet" bedeuten, dass ein Dokument nach OCR der ersten Seiten klassifiziert und abgelegt wird und die restlichen Seiten nachgelagert erkannt werden, oder muss die vollständige OCR vor der Ablage stehen?
6. Weitergabe der Software: Ist eine spätere Nutzung außerhalb der Hausverwaltung Müller GmbH (andere Gesellschaften der Gruppe, Dritte) denkbar? Dann sind AGPL- und GPL-Komponenten vorab durch einen Rechtsanwalt zu prüfen.
7. Getrennte Datenbankkonten für web und worker (Übernahme 12): gewünscht als technische Durchsetzung der Regel "Stammdaten nur nach Bestätigung", oder genügt die Durchsetzung im Anwendungscode?
