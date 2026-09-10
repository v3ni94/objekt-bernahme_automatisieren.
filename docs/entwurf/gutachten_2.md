# Gutachten 2: Bewertung der Stack-Vorschläge A, B und C für CR-05

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Gutachter Nr. 2 im dreiköpfigen Gremium. Blickwinkel: fachliche Umsetzbarkeit (Review Center mit Massenbearbeitung, Import-Parser, Listen im HVM-CI, Requirement Engine) und Geschwindigkeit bis zum produktiven Nutzen.

Stand: 10.09.2026. Faktenbasis: CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md), Befundakte vom 10.09.2026 und die drei Vorschläge stack_A_python_monolith.md, stack_B_api_spa.md, stack_C_polyglott.md. Das Gutachten 1 wurde bewusst nicht gelesen, damit dieses Gutachten eine unabhängige Zweitmeinung bleibt. Alle Personentage (PT) in diesem Dokument sind die Schätzungen der jeweiligen Vorschläge, keine eigenen Messwerte. Eigene Planungsgrößen sind mit ANNAHME gekennzeichnet.

---

## 1. Ergebnis und Empfehlung

Empfehlung: Stack A (Django, HTMX, Celery, Redis, MariaDB), ergänzt um acht konkrete Übernahmen aus B und C und um drei Änderungen am Meilensteinplan.

| Vorschlag | Gewichtete Summe (von 100) | Rang |
|---|---|---|
| A Python-Monolith | 79,0 | 1 |
| B Python-API plus SPA | 64,5 | 2 |
| C Polyglott Laravel plus Python | 63,5 | 3 |

Kurzbegründung aus meinem Blickwinkel:

1. Alle vier Fachbausteine meines Blickwinkels (Review Center, Import-Parser, Listen, Requirement Engine mit Nachforderung) liegen in A in einer Codebasis, einer Datenbankverbindung und einem Testlauf. In C schneidet die Sprachgrenze genau durch diese Bausteine: Requirement Engine in PHP, Nachforderungsbrief und Blatt "Offene Punkte" in Python, Trainingsdaten von PHP geschrieben und von Python gelesen. In B liegt die Fachlogik zwar in Python, aber jede Maske kostet Router, Store, Client-Typen und Fehlerbehandlung zusätzlich, und die gesamte Authentifizierung ist Eigenbau.
2. A ist nach den eigenen Schätzungen der Vorschläge am schnellsten beim ersten produktiven Nutzen (Pipeline plus Review Center plus Import): rund 41 bis 62 PT gegenüber 51 bis 76 PT bei C und 59 bis 89 PT bei B (Rechenweg in Abschnitt 6.2).
3. A hat den geringsten Betriebsaufwand für einen Entwickler auf einem einzelnen VPS: ein Image, eine Sprache, sieben Container.
4. A hat drei technische Lücken, die vor der Umsetzung geschlossen werden müssen und aus B und C übernommen werden können: fehlende Thread-Begrenzung für Tesseract, Speicherformel ohne spaCy je Prozess, Aufteilung großer Dokumente nur als Notfallhebel statt als Grundkonstruktion.
5. A hat eine Planschwäche, die für meinen Blickwinkel schwer wiegt: Die Massenbearbeitung mit Vorschau (CR Abschnitt 11) steht erst im vorletzten Meilenstein M10. Sie gehört in den Review-Center-Meilenstein.

B ist die bessere Wahl nur unter zwei Bedingungen, die der Vorschlag selbst nennt: Der umsetzende Entwickler beherrscht TypeScript und ein SPA-Framework sicher, und das Review Center wird als täglicher Hauptarbeitsplatz mit hohem Interaktionsanteil gesehen. Beides ist aus CR und Befund nicht belegt (offene Frage 1).

C ist die bessere Wahl nur, wenn der umsetzende Entwickler in Laravel deutlich stärker ist als in Django. Die Architektur mit drei Verträgen ist sauber durchdacht, aber sie ist für 2 bis 5 Anwender und einen Entwickler die aufwendigste der drei.

---

## 2. Prüfgrundlage und Vorgehen

Geprüft wurden je Vorschlag: Vollständigkeit gegenüber den CR-Abschnitten 3 bis 15, Widersprüche zum CR-Wortlaut, nicht gekennzeichnete Annahmen, Belastbarkeit des Performance-Rechenwegs (Abschnitt 7 und 14 des CR), und speziell die Realisierbarkeit der Fachbausteine aus meinem Blickwinkel. Jedes Kriterium erhält 0 bis 10 Punkte; die gewichtete Summe ist Summe(Gewicht mal Punkte) geteilt durch 10, damit das Maximum 100 ist.

---

## 3. Bewertungsmatrix

| Nr. | Kriterium | Gewicht | A | B | C |
|---|---|---|---|---|---|
| 1 | Betrieb auf einem VPS mit Compose hinter Traefik, Betriebsaufwand für einen Entwickler, bewegliche Teile | 20 | 9 | 6 | 4 |
| 2 | OCR-, NER-, ML-Ökosystem, beide KI-SDKs ohne Sprachbruch | 15 | 9 | 9 | 7 |
| 3 | Auth mit TOTP, Rollen, Admin-Konfiguration zur Laufzeit, Audit: ausgereift vorhanden vs. Eigenbau | 15 | 8 | 4 | 9 |
| 4 | Datenmodell und Migrationen: ORM-Reife mit MariaDB, rückrollbar, Transaktionssicherheit | 10 | 8 | 7 | 6 |
| 5 | Umsetzungsgeschwindigkeit kleines Team, Wartbarkeit über Jahre, Personalmarkt | 15 | 8 | 5 | 5 |
| 6 | Review Center: Massenbearbeitung mit Vorschau, Suche mit Vorschlägen, Dokumentvorschau, unter 2 s unter Last | 10 | 6 | 9 | 7 |
| 7 | Trennung web/worker, Wiederaufnahme, Idempotenz, Belastbarkeit des Rechenwegs 10.000 Seiten unter 3 h | 10 | 6 | 7 | 8 |
| 8 | Risiken: Abhängigkeiten, Lizenzen, Sicherheitshistorie, Lock-in | 5 | 7 | 5 | 6 |

Rechenweg der gewichteten Summen:

```
A: 20*9 + 15*9 + 15*8 + 10*8 + 15*8 + 10*6 + 10*6 + 5*7
 = 180 + 135 + 120 + 80 + 120 + 60 + 60 + 35 = 790  -> 79,0

B: 20*6 + 15*9 + 15*4 + 10*7 + 15*5 + 10*9 + 10*7 + 5*5
 = 120 + 135 + 60 + 70 + 75 + 90 + 70 + 25 = 645  -> 64,5

C: 20*4 + 15*7 + 15*9 + 10*6 + 15*5 + 10*7 + 10*8 + 5*6
 = 80 + 105 + 135 + 60 + 75 + 70 + 80 + 30 = 635  -> 63,5
```

Empfindlichkeit: Würde man Kriterium 6 (Review Center) von 10 auf 20 Gewicht heben und Kriterium 1 auf 10 senken, ergäbe sich A 76,0, B 67,5, C 66,5. Die Reihenfolge bleibt. A verliert seinen Vorsprung erst, wenn man Kriterium 3 und 5 zusammen unter 15 Gewicht drückt, also die Zeit bis zum Nutzen und die Wartbarkeit für nachrangig erklärt. Das widerspräche dem CR-Rahmen (ein Entwickler, 2 bis 5 Anwender).

---

## 4. Bewertung je Kriterium

### Kriterium 1: Betrieb auf einem VPS, Betriebsaufwand, bewegliche Teile (Gewicht 20)

A (9): Sieben Container aus zwei Images (App-Image für web, worker, worker-io, beat; offizielle Images für db, queue, backup). Eine Sprache, ein Lockfile, ein Build-Pfad. Der zweite Worker worker-io ist als Abweichung vom CR-Katalog sauber gekennzeichnet und begründet. Abzug: das backend-Netz ist nicht als internal markiert, und der web-Container trägt OCR-Binärdateien, die er nicht nutzt.

B (6): Acht Dienste einschließlich Einmaldienst migrate, dazu Node als Build-Werkzeug. Im Betrieb läuft nur Python, aber jedes Deployment baut das Frontend auf dem VPS, was der Vorschlag selbst als Kollisionsrisiko mit laufender Objektverarbeitung benennt und mit einer Registry als weiterem Baustein lösen will. Zwei Toolchains für Sicherheitsupdates. Positiv: sauberer Netzschnitt in app (internal) und egress.

C (4): Bis zu neun Container (web mit nginx, php-fpm und supervisord im selben Image; web-jobs; bridge; scheduler; worker mit eigenem Prozess-Supervisor; queue; db; backup; optional classifier), zwei Laufzeiten, zwei DB-Konten, ein Generator-Build-Schritt für Enums, eine selbstgebaute Stream-Brücke mit geplantem Selbstneustart gegen PHP-Speicherwachstum. Jede dieser Entscheidungen ist einzeln begründet, in der Summe ist es für einen Entwickler die höchste Zahl beweglicher Teile.

### Kriterium 2: OCR-, NER-, ML-Ökosystem und beide KI-SDKs (Gewicht 15)

A (9): Tesseract, ocrmypdf, spaCy, rapidfuzz-Gazetteer, scikit-learn, ReportLab, openpyxl und beide offiziellen SDKs im selben Prozessraum und derselben Testsuite. Die Gazetteer-vor-Modell-Reihenfolge ist für WE-Nummern und bekannte Namen die richtige Wahl.

B (9): Gleiche Python-Seite wie A, zusätzlich PyMuPDF für Textschicht-Erkennung und Seitenbilder, was für die Vorschau ein echter Gewinn ist. Der Sprachbruch zu TypeScript berührt das Ökosystem nicht.

C (7): Der Python-Worker enthält dasselbe Werkzeug. Die KI-SDKs, Maskierung und Kostenprotokoll liegen sauber in Python. Abzug, weil der Nutzungskreislauf über die Grenze läuft: Trainingsdaten schreibt PHP, trainiert wird in Python; Enums und Unterarten müssen generiert werden, sonst driftet die Klassifikation von der Oberfläche weg.

### Kriterium 3: Authentifizierung, Rollen, Admin-Konfiguration, Audit (Gewicht 15)

A (8): django-allauth deckt E-Mail-Login, TOTP mit Wiederherstellungscodes und Google-Anmeldung aus einem Paket ab; Gruppen und Berechtigungen sind Bordmittel. Eigenbau bleiben Konfigurations-App mit Historie und Audit-Tabelle, beides in allen drei Vorschlägen Eigenbau. Der Vorschlag weist selbst darauf hin, dass der Funktionsumfang des MFA-Moduls in der eingesetzten Version zu prüfen ist; das ist ehrlich und ein Restrisiko.

B (4): Alles Eigenbau: Passwort-Hashing, TOTP, Wiederherstellungscodes, Sitzungen in Redis, CSRF über Origin und Header, Ratenbegrenzung, Google-OIDC. Der Vorschlag beziffert das mit acht Endpunkten und sechs Ansichten und nennt jeden Fehler dort sicherheitsrelevant. Das ist korrekt und der Hauptgrund, warum B für ein Ein-Personen-Team schwer zu verantworten ist.

C (9): Fortify (TOTP, Wiederherstellungscodes, Ratenbegrenzung, Passwort-Reset) und Socialite mit Domain-Beschränkung sind Erstpartei-Pakete; Policies für zwei Rollen; Audit-Tabelle mit Datenbank-Trigger gegen UPDATE und DELETE; Einstellungskatalog mit Formulargenerator. Das ist die höchste Fertigquote der drei.

### Kriterium 4: Datenmodell und Migrationen (Gewicht 10)

A (8): Django ORM mit MySQL-Backend ist seit vielen Jahren mit MariaDB im Einsatz; Schemamigrationen erzeugen die Rückwärtsoperation automatisch, Datenmigrationen brauchen eine Rückwärtsfunktion; transaction.atomic ist Bordmittel. Der Vorschlag benennt die JSONField- und Volltext-Unterschiede zu PostgreSQL und zieht die richtige Konsequenz (Tests gegen MariaDB, nicht SQLite).

B (7): SQLAlchemy und Alembic sind ausgereift; downgrade ist bei Schemaänderungen generierbar, bei Datenmigrationen Handarbeit. Der Einmaldienst migrate mit Wartebedingung für web ist ein sauberes Muster. Abzug, weil Modelle, Pydantic-Schemata und der generierte TypeScript-Client dieselbe Struktur an drei Stellen abbilden.

C (6): Eloquent und Migrationen mit down() sind ausgereift. Abzug, weil es zwei Datenzugriffe auf ein Schema gibt (Eloquent und SQLAlchemy Core per Reflektion), beide Seiten documents.status schreiben, und die Kopplung über eine Konstante REQUIRED_MIGRATION im Worker gesichert wird. Der Vorschlag benennt selbst, dass Reflektion fehlende Spalten fängt, aber keine geänderte Bedeutung. Die zwei DB-Konten sind ein guter Schutz, kosten aber Pflege bei jeder neuen Tabelle.

### Kriterium 5: Umsetzungsgeschwindigkeit und Wartbarkeit (Gewicht 15)

A (8): Eigene Schätzung 57 bis 87 PT, die niedrigste der drei. Django in der LTS-Linie hat lange Supportzyklen, große deutschsprachige Verfügbarkeit von Entwicklern, umfangreiche Dokumentation. Abzug, weil die Massenbearbeitung erst in M10 liegt und der Import (M7) hinter dem Review Center (M5) steht, was die fachliche Reihenfolge einer Objektübernahme umkehrt (Abschnitt 6.3).

B (5): Eigene Schätzung 74 bis 113 PT, davon 30 bis 40 PT SPA. Der Vorschlag beziffert den Mehraufwand gegenüber Server-Rendering mit 15 bis 25 Prozent, konzentriert in Masken ohne fachlichen Nutzen. Frontend-Ökosysteme altern schneller; der Vorschlag benennt das selbst. Python plus TypeScript ist am Personalmarkt zwar häufig, aber der Nachfolger muss zusätzlich Celery, SQLAlchemy und die selbstgebaute Auth verstehen.

C (5): Eigene Schätzung 65 bis 100 PT. Laravel beschleunigt Auth und Admin, aber der Vorschlag beziffert den Polyglott-Mehraufwand ebenfalls mit 15 bis 25 Prozent und benennt zwei Wartungskalender. Der Kreis von Personen, die Laravel und Python-ML gleichzeitig beherrschen, ist klein; Vertretung ist schwer.

### Kriterium 6: Review Center (Gewicht 10)

A (6): Alle Funktionen sind mit HTMX realisierbar: Suche mit Vorschlägen als verzögerter Teilaufruf, Pflichtfelder für 05 als Nachladen, Massenbearbeitung als Formular mit Auswahl und Vorschau-Partial, Fortschritt per Polling. Abzüge: Die Dokumentvorschau läuft über den PDF-Viewer des Browsers mit Streaming der Originaldatei; bei einer Gesamtabrechnung mit vielen hundert Seiten ist das für die 2-Sekunden-Vorgabe nicht belegt, weil keine vorgerenderten Seitenbilder vorgesehen sind. Die Seitenbereichszuordnung ist ein Zahlenformular. Die Massenbearbeitung liegt im Plan erst in M10. Die Latenzmessung ist eine Einzelsonde, keine Mehrnutzer-Last.

B (9): Das stärkste Konzept: Raster mit virtuellem Scrollen und Tastaturnavigation, Vorschau-Endpunkt POST /review/preview, der die Benennungsfunktion ohne Schreiben ausführt, vorgerenderte Seitenbilder aus der Pipeline mit Cache-Headern, grafische Seitenbereichsauswahl, SSE für Fortschritt, Playwright-Test für 40 Dokumente, Locust oder k6 als Last. Die Rasterkomponente wird für den Import-Parser wiederverwendet. Abzug, weil der Zwischenzustand einer halbfertigen Massenbearbeitung nur im Browser liegt und bei Neuladen verloren geht.

C (7): Livewire liefert serverseitig reaktive Suche, Kandidatenlisten und Massenbearbeitung ohne SPA-Build; die Massenbearbeitung ist in M6 enthalten, die Latenzmessung mit Locust und 5 gleichzeitigen Nutzern ist die realistischste der drei. Abzüge: Die Dokumentvorschau ist nur als lesender Mount von transit im web-Container erwähnt, weder Seitenbilder noch Seitenbereichsauswahl in der Oberfläche sind beschrieben; jede Interaktion in Livewire ist ein Server-Roundtrip, was bei 40 Zeilen mit Zellkorrekturen spürbar werden kann.

### Kriterium 7: web/worker-Trennung, Wiederaufnahme, Idempotenz, Rechenweg (Gewicht 10)

A (6): Zustandsautomat in der Datenbank, Status-Check zu Beginn jedes Tasks, Abgleich-Task alle fünf Minuten, acks_late, reject_on_worker_lost, visibility_timeout über dem harten Zeitlimit; die Celery-Fallen sind korrekt benannt. Der Rechenweg hat aber drei Lücken: keine Thread-Begrenzung für Tesseract (ohne OMP_THREAD_LIMIT starten P Prozesse ein Mehrfaches an Threads und verdrängen web trotz CPU-Limit), kein Skalierungsverlust bei P parallelen Prozessen, und die Aufteilung großer Dokumente ist nur Stellhebel 4 statt Grundkonstruktion. t_ocr 3,0 s ist der optimistischste Wert der drei. Nachrechnung in Abschnitt 5.1.

B (7): Gleiche Celery-Disziplin wie A, Lease-basierte Erkennung hängender Jobs, OMP_THREAD_LIMIT gesetzt, ehrliche Szenariotabelle, die Verfehlungen bei drei Prozessen offen ausweist. Lücken: keine Aufteilung großer Dokumente (ein Dokument mit 2.000 Scanseiten liefe bei 4 s je Seite rund 2 h 13 min seriell auf einem Prozess), kein Skalierungsverlust; die Aussage "ab fünf Prozessen in allen Szenarien eingehalten" hält mit Skalierungsverlust nur noch knapp (Abschnitt 5.2).

C (8): Der belastbarste Rechenweg: Skalierungseffizienz als eigene Annahme, Chunking großer Dokumente als zwingende Konstruktionsentscheidung, OMP_THREAD_LIMIT, nice und ionice für OCR-Prozesse, OCR-Temp auf Platte, Trennung in schwere Prozesse ohne Modelle und leichte Prozesse mit Modellen, Fortschritt aus einer Statistiktabelle statt aus pipeline_jobs. Idempotenz über atomares UPDATE mit Statusbedingung, Heartbeat und XAUTOCLAIM ist sauber. Abzug, weil die sprachübergreifende Queue Eigenbau ohne Dashboard, Wiederholungsstrategie und Dead-Letter-Sicht ist (vom Vorschlag selbst benannt) und weil mit L = 1 Import-Parsing und Listenerzeugung während eines Laufs hinter der Klassifikation warten.

### Kriterium 8: Risiken (Gewicht 5)

A (7): Abhängigkeitsrisiko konzentriert auf allauth (großes Paket, Major-Versionen mit Anpassungsbedarf, selbst benannt) und Celery-Konfiguration. Lock-in gering. Lizenzhinweise zu Ghostscript (AGPL, Abhängigkeit von ocrmypdf) und zur geänderten Redis-Lizenz fehlen in A; beide betreffen alle drei Vorschläge gleich und sind für internen Betrieb unkritisch, gehören aber ins Risikoregister.

B (5): npm-Lieferkette (selbst benannt), selbstgebaute Authentifizierung als Sicherheitsrisiko, schnellere Alterung des Frontend-Ökosystems, Registry als zusätzlicher Baustein.

C (6): Zwei Ökosysteme mit zwei Sicherheitskalendern, PHP-Langzeitprozesse, selbstgebaute Stream-Brücke. Positiv: C ist der einzige Vorschlag, der Ghostscript-AGPL und Redis-Lizenz anspricht und Valkey als Alternative nennt.

---

## 5. Befunde je Vorschlag: Lücken, Widersprüche, unmarkierte Annahmen, Schönrechnen

### 5.1 Stack A

1. Fehlende Thread-Begrenzung: A setzt ocrmypdf mit jobs 1, sagt aber nichts zu OMP_THREAD_LIMIT für Tesseract. Ohne diese Variable nutzt jeder Tesseract-Prozess mehrere Threads; bei P Prozessen entsteht Überzeichnung, das CPU-Limit des Containers wird über Kontextwechsel teuer, und die 2-Sekunden-Vorgabe für web ist gefährdet. B und C setzen die Variable. Muss übernommen werden.
2. Speicherformel ohne Modelle je Prozess: A rechnet worker mit P mal 0,75 GiB plus 0,5 GiB, sagt aber in 2.9, dass das Klassifikationsmodell je Worker-Prozess geladen wird, und in 2.8, dass spaCy mit dem mittelgroßen deutschen Modell läuft. Beide laufen in derselben cpu-Warteschlange wie die OCR, also in jedem der P Prozesse. Mit der Planungsgröße aus C (ANNAHME 0,5 bis 0,8 GiB je Prozess mit spaCy) fehlen in A's Formel P mal 0,5 bis 0,8 GiB. Lösung aus C: Klassifikation und NER in eine eigene, kleine Warteschlange mit ein bis zwei Prozessen routen, OCR-Prozesse ohne Modelle.
3. Chunking nur als Notfallhebel: Die Aufteilung großer Dokumente in Seitenblöcke ist Stellhebel 4 in 5.4. Der CR nennt die Gesamtabrechnung mit eingebetteten Einzelabrechnungen ausdrücklich; solche Dokumente sind der Regelfall bei WEG-Übernahmen, nicht die Ausnahme. C behandelt das korrekt als Grundkonstruktion.
4. Nachrechnung des ungünstigen Falls: A rechnet bei P = 3 mit 102 min OCR plus 4 plus 6 plus 42 min serieller Ablage gleich 154 min und nennt das "unter 180 Minuten, aber ohne Reserve". Mit dem Skalierungsverlust aus C (ANNAHME 85 Prozent Effizienz) werden aus 102 min rund 120 min, Summe 172 min. Mit t_ocr 3,5 s statt 3,0 s (Wert aus C) ergibt sich (6.000 mal 3,5 plus 400) geteilt durch 3 gleich 7.133 s, mit Effizienzfaktor 8.392 s gleich 140 min, Summe 192 min, Vorgabe verfehlt. A ist bei P = 3 also schöner gerechnet, als die eigene Formel hergibt. Die Empfindlichkeitsbetrachtung mit 5 s ist vorhanden, aber der Effizienzfaktor fehlt durchgehend.
5. Massenbearbeitung mit Vorschau erst in M10: CR Abschnitt 11 definiert sie als Teil des Review Centers. Der Integrationstest "Gesamtabrechnung mit 12 Einzelabrechnungen" liegt in M5 und braucht bereits Seitenbereichszuordnung. Die Massenbearbeitung gehört in M5.
6. Import nach Review Center: Eine Objektübernahme beginnt fachlich mit der Eigentümerliste der Vorverwaltung (CR Abschnitt 15). Ohne owners und units in der Datenbank kann Stufe 1 (bekannte Eigentümer) nichts leisten, und das Review Center hat keine Vorschläge für die Pflichtfelder. A plant den Import in M7 nach Review Center (M5) und KI (M6). Das gilt für alle drei Vorschläge (Abschnitt 6.3).
7. Dokumentvorschau ohne Seitenbilder: Streaming der Originaldatei in den Browser-Viewer. Für ein 20-seitiges Dokument unproblematisch, für eine Gesamtabrechnung mit vielen hundert Seiten ist die 2-Sekunden-Vorgabe nicht belegt. B's vorgerenderte Seitenbilder lösen das.
8. Latenzmessung als Einzelsonde: Ein Skript ruft alle zwei Sekunden Seiten auf. Der CR verlangt Antwortzeit unter Last; C misst mit 5 gleichzeitigen Nutzern, B mit Locust oder k6. A sollte auf Mehrnutzer-Last umstellen.
9. Unmarkiert, aber harmlos: "Inferenz im Millisekundenbereich, Modell wenige Megabyte, Training in Sekunden" wird als Tatsache formuliert; für TF-IDF plus lineares Modell plausibel, dennoch eine Planungsgröße.
10. Positiv hervorzuheben: ehrliche Celery-Risikobeschreibung, Dry-Run-Client als eigene Adapterimplementierung, Alt-Alias im Seed für die Definition of Done, 14 offene Fragen sauber gebündelt, Migration bewusst nicht im Container-Start.

### 5.2 Stack B

1. Authentifizierung als Eigenbau ist das zentrale Risiko und vom Vorschlag selbst als "Aufwand ohne fachlichen Mehrwert" und sicherheitsrelevant bezeichnet. Für ein Ein-Personen-Team ist das der Punkt, an dem B scheitert, nicht an der SPA.
2. Kein Chunking großer Dokumente: weder in der Pipeline noch in den Stellhebeln. Ein Dokument mit 2.000 Scanseiten läuft bei t_ocr 4 s rund 8.000 s seriell auf einem Prozess und kann am Ende des Laufs allein einen Kern belegen, während die übrigen leer laufen. Die Seitenbereichszuordnung (CR Abschnitt 6) ist nur als Oberflächenfunktion beschrieben, nicht als Verarbeitungseinheit.
3. Kein Skalierungsverlust im Rechenweg: Die Aussage "ab fünf parallelen OCR-Prozessen wird die Vorgabe in allen betrachteten Szenarien eingehalten" hält mit dem Effizienzfaktor aus C nur noch knapp: 8.000 s geteilt durch 0,85 gleich 9.412 s gleich 157 min plus 10 bis 15 min Nachlauf, also rund 172 min.
4. Abweichungen vom CR-Container-Katalog (worker-io, migrate) sind nicht als Abweichung gekennzeichnet, anders als in A. Inhaltlich richtig, formal fehlt der Hinweis.
5. Node-Build auf dem VPS bei jedem Deployment: Der Vorschlag benennt das Risiko und empfiehlt eine Registry als zweiten Schritt. Das ist ein weiterer Baustein mit Zugangsdaten, der in der Betriebsaufwandsschätzung nur am Rande vorkommt.
6. Vorschaubild-Rendering ist in t_dig (0,05 s je Digitalseite) enthalten, in t_ocr für Scanseiten aber nicht erwähnt. Kleine Lücke im Rechenweg.
7. Zwischenzustand der Massenbearbeitung nur im Browser: bewusst gewählt (kein Serverzustand für Halbfertiges), aber bei 40 Zeilen mit Einzelkorrekturen ein reales Verlustrisiko beim Neuladen. Ein Entwurfsspeicher in der Datenbank wäre die sichere Variante.
8. Import in M6 nach Review Center M4: gleiche Reihenfolgeschwäche wie A.
9. Positiv hervorzuheben: Abschnitt 2.4 ist die ehrlichste Nutzen-Aufwand-Tabelle der drei Vorschläge; OpenAPI-Drift-Prüfung in CI; app-Netz internal mit getrenntem egress-Netz; Einmaldienst migrate; Vorschau-Endpunkt ohne Schreibwirkung; Playwright-Test für den 40-Dokumente-Fall; Wiederverwendung der Rasterkomponente für Import und Review.

### 5.3 Stack C

1. Widerspruch zwischen Rechtekonzept und Datenfluss: Das Konto app_worker hat SELECT nur auf Stammdaten (objects, units, owners, owner_unit_assignments, tenants, leases, tenant_unit_assignments, settings, drive_nodes). Die Listen (Python) brauchen aber die Spalten Status und Quelle (CR 12a), also review_items, filings und documents vollständig, und das Blatt "Offene Punkte" muss identisch mit der Vollständigkeitsprüfung sein, die in requirement_checks von Laravel geschrieben wird. Der Nachforderungsbrief (demand_letter.py in Python) braucht dieselben requirement_checks. Entweder werden die Rechte erweitert (dann schützt das Konzept weniger) oder die Requirement Engine wandert nach Python (dann liegt Fachlogik an der Grenze). Beides ist Mehrarbeit genau in den Bausteinen meines Blickwinkels.
2. Sprachgrenze im fachlichen Kern: CR Abschnitt 12 verlangt, dass fehlende Punkte automatisch in die Nachforderung fließen. In C entscheidet PHP, was fehlt, und Python schreibt den Brief. Jede neue Prüfregel berührt beide Seiten und den Enum-Generator.
3. Listenerzeugung über vier Stationen (Livewire, Stream, Python, Ereignis, Laravel-Upload) ist asynchron mit Sekunden bis Minuten Verzögerung; der Vorschlag benennt das selbst als Risiko für die Wahrnehmung der Anwender.
4. Vier zusätzliche Container gegenüber dem CR-Katalog (web-jobs, bridge, scheduler, Aufteilung des Workers in schwere und leichte Prozesse) werden nicht als Abweichung gekennzeichnet. Die Alternative (b), Polling der Datenbank durch den Scheduler statt Container bridge, ist dokumentiert und wäre die einfachere Wahl.
5. Versionsnummern entgegen der Vorgabe: redis:7-alpine, PHP 8.x, Python 3.12.x. Der Prüfhinweis steht dabei, das Format ist trotzdem nicht regelkonform.
6. Dokumentvorschau nicht ausgearbeitet: nur "transit lesend für Vorschau" am web-Container. Keine Seitenbilder, keine Seitenbereichsauswahl in der Oberfläche, obwohl das Worker-Chunking die Seitenbereiche technisch vorbereitet.
7. L = 1 leichter Prozess als Standard: Während eines 10.000-Seiten-Laufs teilen sich Klassifikation, Import-Parsing und Listenerzeugung einen Prozess. Ein manuell ausgelöster Listenlauf oder ein Import wartet dann hinter der Klassifikation.
8. Eigenbau-Queue ohne Dashboard und Dead-Letter-Sicht (selbst benannt); Horizon zeigt nur die Laravel-Seite.
9. Positiv hervorzuheben: der belastbarste Rechenweg (A1 bis A11 einzeln benannt, Effizienzfaktor, Chunking als Pflicht), zwei DB-Konten als technische Durchsetzung der Regel "Stammdaten erst nach Bestätigung", Lizenzhinweise, Fortify und Socialite als Erstpartei-Pakete, Locust mit 5 Nutzern, Statistiktabelle für den Fortschritt, no-new-privileges und read-only Root-Dateisystem.

### 5.4 Gemeinsame Befunde aller drei Vorschläge

- Kaltstart des TF-IDF-Klassifikators wird überall ehrlich benannt; der KPI-Zielwert unter 5 Prozent 06_Sonstiges ist erst nach Einlernphase erreichbar. Das gehört als Erwartungshinweis an den Auftraggeber.
- MariaDB-Volltext ohne deutsche Stammformreduktion; alle drei halten Meilisearch als Ausbaupfad vor. Für die CR-Suche über Metadaten ausreichend.
- Die Performance-Vorgabe hängt in allen drei Vorschlägen an der ungemessenen Kernzahl. Bei P = 3 ist die Vorgabe in keinem Vorschlag mit Reserve erreichbar. Das ist keine Stackfrage, sondern eine Serverfrage (offene Frage 4).
- Alle drei planen den Import der Eigentümerliste nach dem Review Center. Das widerspricht dem fachlichen Ablauf einer Übernahme (Abschnitt 6.3).
- Alle drei behandeln die Befund-Widersprüche (Objektnummern mit 2 bis 6 Stellen, fehlende Basis CR-01 bis CR-04, Umlaut-Schreibweise, Alt-Alias) korrekt als offene Fragen oder Konfigurationswerte.

---

## 6. Blickwinkel: fachliche Umsetzbarkeit und Zeit bis zum produktiven Nutzen

### 6.1 Fachbausteine im Vergleich

| Fachbaustein (CR) | A | B | C |
|---|---|---|---|
| Massenbearbeitung mit Vorschau (11) | HTMX-Formular mit Vorschau-Partial, realisierbar; im Plan erst M10 | SPA-Raster mit Vorschau-Endpunkt, M4, stärkstes Konzept | Livewire, M6, realisierbar; Roundtrip je Interaktion |
| Suche mit Vorschlägen aus owners und units (11) | HTMX verzögerter Teilaufruf | Typeahead gegen API | Livewire |
| Kandidatenliste historischer Eigentümer (7, 11) | Partial aus owner_unit_assignments | Zeitstrahl-Ansicht | Kandidaten als JSON in review_items |
| Dokumentvorschau | Browser-Viewer, Streaming der Originaldatei | vorgerenderte Seitenbilder mit Cache | nicht ausgearbeitet |
| Seitenbereiche Gesamtabrechnung (6) | Zahlenformular | grafische Auswahl | Worker-Chunks, Oberfläche nicht beschrieben |
| Import-Parser alle Formate (15) | Python, M7, Vorschläge ins Review Center | Python, M6, geteiltes Raster mit Review | Python-Vorschläge, Bestätigung in PHP, M7 |
| Listen XLSX und PDF im HVM-CI (12a) | openpyxl und ReportLab in derselben App, M9 | gleich, M7 | Erzeugung Python, Upload PHP, asynchron, M8 |
| Requirement Engine und Nachforderung (12) | eine Codebasis, M8 | eine Codebasis, M7 | Engine PHP, Brief Python, Rechteproblem (5.3 Nr. 1) |
| Trainingsdaten aus Korrekturen (11) | gleiche Tabelle, gleicher Code | gleich | PHP schreibt, Python liest |
| Audit je Review-Aktion (15) | Service-Schicht | Service-Schicht, DB-Nutzer ohne UPDATE/DELETE | Observer plus Trigger |

Ergebnis: Für die vier Bausteine meines Blickwinkels ist A der Stack mit den wenigsten Nahtstellen, B der mit der besten Bedienbarkeit im Review Center, C der mit den meisten Grenzübertritten.

### 6.2 Zeit bis zum ersten produktiven Nutzen

Definition "erster produktiver Nutzen": Ein reales Objekt kann angelegt, der Ordnerabgleich durchgeführt, die Eigentümerliste der Vorverwaltung importiert und bestätigt, die Dokumente verarbeitet und im Review Center zugeordnet werden. Stufe 3 (externe KI) und die Listen sind für den ersten Nutzen hilfreich, aber nicht zwingend.

Summierung der Meilensteine nach den Schätzungen der Vorschläge (Untergrenze bis Obergrenze in PT):

| Vorschlag | Meilensteine bis zum ersten Nutzen | PT |
|---|---|---|
| A | M0 bis M5 (Gerüst, Datenmodell, Drive, OCR, Klassifikation plus Review Center) plus M7 (Import) | 1+6+4+6+6+10+5 = 38 bis 2+9+6+9+8+15+8 = 57; mit M6 (KI) 41 bis 62 |
| B | M0 bis M4 (Fundament, Datenmodell, Drive, Pipeline, Review Center SPA) plus M6 (Import) | 6+8+8+12+12+8 = 54 bis 9+12+12+18+18+12 = 81; mit M5 (KI) 59 bis 89 |
| C | M0 bis M6 (Messung, Kernmodell, Drive, Pipeline, Klassifikation, KI, Review Center) plus M7 (Import) | 4+6+7+8+8+4+8+6 = 51 bis 6+9+10+12+12+6+12+9 = 76 |

A erreicht den ersten Nutzen rund 10 bis 15 PT vor C und rund 20 bis 25 PT vor B. Hinzu kommt bei A nach meiner Planänderung (Abschnitt 7.3) die Massenbearbeitung in M5; ANNAHME: das kostet 2 bis 3 PT zusätzlich in M5 und spart dieselben in M10, Verifizierung durch Neuschätzung nach M4.

### 6.3 Fachliche Reihenfolge einer Übernahme und Konsequenz für den Plan

Der Ablauf einer WEG-Übernahme ist: Objekt anlegen, Ordnerabgleich, Eigentümerliste der Vorverwaltung importieren und bestätigen (owners, units, owner_unit_assignments), erst dann Dokumente verarbeiten, Review, danach Listen und Nachforderung. Stufe 1 der Klassifikation (bekannte Eigentümer, Einheiten) und die Pflichtfelder des Review Centers setzen befüllte Stammtabellen voraus. Alle drei Vorschläge planen den Import nach dem Review Center. Für A empfehle ich: Import (M7) direkt nach dem Datenmodell (M2) beginnen, zunächst mit den Formatprofilen XLSX und CSV (auch Immoware24, sofern der Auftraggeber den Export freigibt, offene Frage 5), PDF-Scan-Import nach der OCR-Pipeline (M4). Damit hat das Review Center in M5 von Anfang an echte Vorschlagslisten.

---

## 7. Empfehlung im Detail

### 7.1 Übernahmen aus B in A

1. Vorgerenderte Seitenbilder in der Pipeline (pypdfium2 oder PyMuPDF, Auflösung konfigurierbar), Auslieferung aus dem ocr_cache mit Cache-Headern; das Original nur auf Wunsch streamen. Das ersetzt A's Browser-Viewer-Streaming als Standardvorschau und ist die Voraussetzung dafür, dass die 2-Sekunden-Vorgabe auch bei Gesamtabrechnungen hält.
2. Vorschau-Endpunkt ohne Schreibwirkung für die Massenbearbeitung: Eingabe Liste von Dokument-IDs mit Zielbereich, Eigentümer, Einheit, Unterart, Zeitraum; Ausgabe je Zeile der berechnete Drive-Pfad, ob der Ordner existiert, Warnungen. In A als HTMX-Partial umgesetzt; die Benennungsfunktion bleibt die einzige Stelle, die Pfade erzeugt.
3. Netzschnitt: backend-Netz als internal, zusätzliches egress-Netz nur für web, worker, worker-io und beat; db, queue und backup ohne Internetzugang.
4. OMP_THREAD_LIMIT=1 als Umgebungsvariable im worker-Container.
5. Ende-zu-Ende-Test des 40-Dokumente-Falls (Playwright oder gleichwertig) als Abnahmekriterium des Review-Center-Meilensteins, plus Mehrnutzer-Lastmessung (Locust oder k6, 5 gleichzeitige Nutzer) statt Einzelsonde.
6. Eine gemeinsame Tabellenkomponente (Template-Partial mit Zeilenauswahl, Zellstatus, Markierung unsicherer Zeilen) für Review Center und Import-Vorschläge, damit Sachbearbeiter beide Masken gleich bedienen.

### 7.2 Übernahmen aus C in A

7. Chunking großer Dokumente als Grundkonstruktion: pikepdf teilt Dokumente über einer konfigurierbaren Seitenzahl in Blöcke, jeder Block ist ein eigener Celery-Task, der Merge erzeugt Seitentexte je Originalseite. Dieselbe Seitenbereichslogik trägt die relationale Zuordnung der Einzelabrechnungen (CR Abschnitt 6).
8. Trennung schwerer und leichter Prozesse: OCR-Tasks in der cpu-Warteschlange ohne Modelle im Speicher; NER, Klassifikation, Import-Parsing und Listenerzeugung in einer eigenen Warteschlange nlp mit ein bis zwei Prozessen, die spaCy und das TF-IDF-Modell einmal laden. Die Speicherformel wird damit P mal m_ocr plus L mal m_nlp plus Grundlast, beide Größen in M4 zu messen.
9. Rechenweg um Skalierungseffizienz erweitern (ANNAHME 85 Prozent, aus C; Messung in M4 mit P = 1, 2 und C minus 1) und nice sowie ionice für OCR-Prozesse setzen, OCR-Temp auf Platte statt tmpfs.
10. Fortschrittsanzeige aus einer kleinen Statistiktabelle je Objekt statt aus einer Aggregation über die jobs-Tabelle.
11. Lizenzhinweise (Ghostscript AGPL, Redis-Lizenzänderung mit Valkey als Alternative) ins Risikoregister von A aufnehmen; für internen Betrieb unkritisch, bei Weitergabe prüfen lassen.

### 7.3 Änderungen am Meilensteinplan von A

1. Massenbearbeitung mit Vorschau von M10 nach M5 (Review Center). Abnahme in M5: 40 gleichartige Einzelabrechnungen eines Jahres in einem Vorgang zugeordnet, Vorschaupfade stimmen mit der Benennungsfunktion überein, Audit vollständig, p95 der Review-Seiten unter 2 s bei laufender Pipeline und 5 gleichzeitigen Nutzern.
2. Import der Eigentümerlisten (M7) vorziehen: Formatprofile XLSX und CSV direkt nach M2, PDF-Scan-Import nach M4. Damit steht das Review Center in M5 auf befüllten Stammtabellen.
3. Nach M4 verbindlicher Prüfpunkt "Performance-Entscheidung": Messwerte t_ocr, Effizienzfaktor, m_ocr, m_nlp liegen vor, die Formel aus A 5.2 wird mit Effizienzfaktor neu gerechnet, und der Auftraggeber entscheidet über Stellhebel oder Tarif, bevor M5 bis M11 gebaut werden.

### 7.4 Bewusst nicht übernommen

- Zwei DB-Konten aus C: In A schreiben Worker und Web denselben Code; die Regel "Stammdaten erst nach Bestätigung" wird in der Service-Schicht durchgesetzt und getestet. Zwei Konten würden zwei DATABASES-Einträge und Routing kosten, ohne dass in einem Monolithen eine Sprachgrenze zu sichern wäre.
- Einmaldienst migrate aus B: A's Entscheidung, Migrationen als dokumentierten Deployment-Schritt und nicht im Container-Start laufen zu lassen, ist für einen Entwickler die kontrollierbarere Variante. Beide Wege verhindern Neustartschleifen; A's Weg vermeidet zusätzlich, dass web bei fehlgeschlagener Migration gar nicht startet.
- SPA-Zwischenzustand im Browser aus B: Halbfertige Massenbearbeitungen sollen in A serverseitig als Entwurf gespeichert werden, damit ein Neuladen nichts verliert.
- Container bridge und Redis Streams aus C: In einem Monolithen ohne Sprachgrenze überflüssig.

---

## 8. Risiken der Empfehlung

1. Bedienbarkeit des Review Centers: HTMX trägt Auswahl, Vorschau und Suche, aber kein grafisches Ziehen von Seitenbereichen und kein Server-Push. Wenn die Sachbearbeiter nach den ersten Objekten eine Raster-Bedienung wie in B erwarten, ist die Nachrüstung eines kleinen JavaScript-Anteils (Alpine.js oder Vanilla) für Auswahl und Tastaturnavigation einzuplanen. Das ist kein Stackwechsel, aber zusätzlicher Aufwand.
2. Entwicklerprofil: A's Geschwindigkeitsvorteil setzt voraus, dass der umsetzende Entwickler Django mindestens so gut beherrscht wie Laravel oder TypeScript. Ist das nicht der Fall, verschiebt sich der Vorteil zu C (Laravel) oder schmilzt gegenüber B (TypeScript). Vor M0 zu klären (offene Frage 1).
3. Performance bei kleiner Kernzahl: Mit P = 3 hält die 3-Stunden-Vorgabe nach Nachrechnung mit Effizienzfaktor nur ohne Reserve oder gar nicht. Das gilt für alle drei Stacks, aber A's Rechenweg hat es bisher zu günstig dargestellt. Der Prüfpunkt nach M4 (7.3 Nr. 3) ist deshalb verbindlich.
4. Celery-Konfiguration: visibility_timeout, acks_late, Prefetch, Zeitlimits und Speicher-Recycling müssen zusammenpassen; ein falscher Wert erzeugt Doppelverarbeitung. Der Integrationstest mit hartem Worker-Abbruch ist Pflicht und bei jeder Celery-Anhebung zu wiederholen.
5. allauth als großes Paket: Der MFA-Funktionsumfang der eingesetzten Version ist zu prüfen; falls TOTP mit Wiederherstellungscodes und Erzwingung nicht vollständig abgedeckt ist, ist django-two-factor-auth die Rückfalloption, mit dann zwei Paketen für Login und Google-Anmeldung.
6. Ein Image für alles: Ein Sicherheitsupdate in einer OCR-Abhängigkeit erzwingt Rebuild und Neustart auch von web. Für 2 bis 5 Anwender außerhalb laufender Objektverarbeitung planbar, aber ein wiederkehrender Posten.
7. Volltextsuche in MariaDB bleibt eine Hilfsfunktion. Wenn die Sachbearbeiter Recherche über Dokumentinhalte erwarten, kommt ein Suchdienst als weiterer Container hinzu.
8. Planänderungen (7.3) verschieben Aufwand innerhalb des Plans, verkürzen ihn aber nicht. Die Untergrenze von 57 PT ist nach meiner Einschätzung optimistisch, weil die Basis 01 bis 04 (Befund 6.3) nicht spezifiziert ist und in M2, M5 und M8 mitgebaut werden muss.

---

## 9. Annahmen dieses Gutachtens

| Nr. | ANNAHME | Verifizierung |
|---|---|---|
| G1 | Skalierungseffizienz bei P parallelen OCR-Prozessen 85 Prozent (Planungsgröße aus Vorschlag C, für die Nachrechnung von A und B verwendet) | Messung in M4 auf dem VPS mit P = 1, 2 und C minus 1 |
| G2 | Speicherbedarf eines Prozesses mit spaCy-Modell und Klassifikator 0,5 bis 0,8 GiB (Planungsgröße aus Vorschlag C, für die Kritik an A's Speicherformel verwendet) | docker stats während perf_run in M4 |
| G3 | Verlagerung der Massenbearbeitung von M10 nach M5 kostet 2 bis 3 PT zusätzlich in M5 und spart dieselben in M10 | Neuschätzung des Plans nach M4 |
| G4 | Der umsetzende Entwickler beherrscht Python und Django mindestens so gut wie PHP/Laravel oder TypeScript | Klärung mit dem Auftraggeber vor M0 (offene Frage 1) |
| G5 | Ein HTMX-Formular mit 40 bis 100 Zeilen, Zeilenauswahl und Vorschau-Partial antwortet bei 5 gleichzeitigen Nutzern und laufender Pipeline unter 2 s | Lastmessung in M5 mit Locust oder k6 |
| G6 | Vorgerenderte Seitenbilder verlängern den OCR-Schritt je Seite nur geringfügig gegenüber t_ocr | perf_run in M4 mit und ohne Bilderzeugung |

Die Aufwandsangaben in Abschnitt 6.2 sind Summen der Schätzungen aus den Vorschlägen, keine eigenen Messwerte.

---

## 10. Offene Fragen an den Auftraggeber (gebündelt, nur echte Entscheidungen)

1. Entwicklerprofil: Wer setzt um, und wo liegen die Schwerpunkte (Python/Django, PHP/Laravel, TypeScript/SPA)? Bei deutlichem Laravel-Schwerpunkt ist C statt A zu wählen; bei starkem TypeScript-Schwerpunkt und dem Review Center als täglichem Hauptarbeitsplatz sind B-Elemente stärker zu gewichten.
2. Freigabe der Planänderungen aus Abschnitt 7.3: Massenbearbeitung mit Vorschau im Review-Center-Meilenstein, Import der Eigentümerlisten vor dem Review Center, verbindlicher Performance-Prüfpunkt nach M4.
3. Definition des ersten produktiven Einsatzes: Reicht Pipeline plus Review Center plus Import (ohne Stufe 3 und ohne Listen), oder soll der Produktivstart erst mit Listen und Nachforderung erfolgen?
4. Serverdimensionierung: Wenn die Messung in M0 und M4 ergibt, dass die 3-Stunden-Vorgabe mit P = 3 nur ohne Reserve oder nicht erreichbar ist, ist ein größerer VPS-Tarif eine Option, oder gilt die Vorgabe als weich (Nachtlauf zulässig)?
5. Zweiphasige OCR als Stellhebel (Klassifikation aus den ersten Seiten, Rest nachgelagert) ändert die Bedeutung von "vollständig verarbeitet in unter 3 Stunden". Zulässig?
6. Immoware24-Export als erster Import über das Review Center, obwohl der CR von keinen Bestandsdaten spricht (Befund 4)?
7. Existieren CR-01 bis CR-04, ein Lastenheft oder Vorlagen (Word, Excel) für Nachforderungsgenerator und Requirement Engine? Falls nein: Freigabe, beides minimal neu zu bauen (Befund 6.2, 6.3).
8. PDF-Listen mit über zwanzig Spalten auf A4 quer: kleinere Tabellenschrift (Abweichung von 10 bis 11 pt der CI, wie in B vorgeschlagen), Aufteilung in zwei Tabellenblöcke je Einheit oder mehrzeilige Datensätze?
9. Anmeldung über Google Workspace: zusätzlich TOTP in der Anwendung erzwingen oder genügt die Zwei-Faktor-Absicherung des Google-Kontos? Nur Domain muellerhv.de?
10. Aus dem Befund weiterhin offen und von allen drei Vorschlägen gleich behandelt: Objektnummer bei Neuanlage mit Nullauffüllung oder Ist-Nummer; Schreibweise 05_Eigentümerakte mit Umlaut neben ASCII-Unterordnern; Präfix der Eigentümerordner für Nicht-Wohnungen (WE oder typbezogen).
