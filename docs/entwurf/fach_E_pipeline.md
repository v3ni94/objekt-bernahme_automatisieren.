# Fachentwurf E: Klassifikationspipeline und Performance (CR-05, Abschnitte 6, 7, 8, 10, 14)

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Stand: 10.09.2026. Faktenbasis: CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md) und Befundakte (befund.md). Alles, was nicht aus diesen beiden Quellen stammt, ist als Vorschlag oder als ANNAHME gekennzeichnet. Worker-Sprache: Python (CR nennt Tesseract und ocrmypdf).

## 0. Ergebnis und Empfehlung

1. Die Pipeline verarbeitet jede Datei in festen, einzeln wiederaufnehmbaren Schritten: Ingest, Hash, Dublettenpruefung, Seitensplitting, OCR je Chunk, Text je Seite, Stufe 1 (Regeln), Stufe 2 (NER plus lokaler Klassifikator), Stufe 3 (externe KI nur unter Schwellwert), Entscheidung, Datenbankeintrag, Drive-Verschiebung als letzter Schritt.
2. Stufe 2 startet mit TF-IDF auf Zeichen-n-Grammen plus linearem Modell. Begruendung: Millisekunden-Latenz, geringer RAM, Training in Sekunden, robust gegen OCR-Fehler, keine Torch-Abhaengigkeit im Worker-Image. Satz-Embeddings bleiben als optionaler Container `classifier` vorgesehen und werden erst aktiviert, wenn die gemessene Trefferquote des linearen Modells nicht ausreicht.
3. Externe KI (Stufe 3) erhaelt ausschliesslich einen maskierten, gekuerzten Textauszug und liefert ein striktes JSON-Schema. IBAN, Kontonummer und BLZ werden vor der Uebermittlung durch Regex ersetzt. Fallen beide Anbieter aus oder ist das Kostenlimit je Objekt erreicht, wird nicht geraten, sondern in 06_Sonstiges/01_Unklar abgelegt und ein Review-Eintrag erzeugt.
4. Die Entscheidungsregel aus CR Abschnitt 6 wird als geordneter Algorithmus umgesetzt, erweitert um einen vorgelagerten Schritt fuer 01_Legitimationsunterlagen (Verwaltervollmacht laut CR Abschnitt 5) und 04_Mieterakte (Vorschlag, da der CR die Basisstruktur dieser Ordner nicht spezifiziert, siehe Befund Punkt 6.3).
5. Die historische Eigentuemerzuordnung laeuft ueber den Leistungszeitraum des Dokuments gegen `owner_unit_assignments`, nicht ueber den aktuellen Eigentuemer. Bei fehlendem Zeitbezug und mehreren historischen Eigentuemern entsteht ein Review-Eintrag mit Kandidatenliste.
6. Performance: Mit dem Modell "Seiten pro Minute = Prozesse x 60 / Sekunden je Seite" liegt das Ziel von 10.000 Seiten in unter 180 Minuten mit den unten markierten Annahmen bereits bei 4 OCR-Prozessen im Rahmen, mit deutlicher Reserve ab 6 Prozessen. Alle Planungsgroessen sind Annahmen und werden auf dem VPS nach dem Messplan in Abschnitt 9 ersetzt.
7. Idempotenz: Doppelverarbeitung ist ueber den Schluessel (object_id, sha256) und einen Job-Status-Automaten in der DB ausgeschlossen. Redis dient nur als Transport, nie als Wahrheit.

## 1. Ablauf je Datei

### 1.1 Quellen und Verzeichnisse

| Quelle | Eingang | Ergebnis |
|---|---|---|
| Drive-Bestand | Ordnerabgleich (CR Abschnitt 9.4) listet alle Dateien im Objektordner rekursiv; je Datei ein Job mit `drive_file_id`, aktuellem Elternordner, Name, Groesse und der von der Drive-API gelieferten Pruefsumme (sofern vorhanden) | Datei bleibt in Drive und wird nach Klassifikation innerhalb des Objektordners verschoben |
| Upload | Web-App schreibt nach `/srv/objektakte/transit/<object_id>/incoming/<uuid>/<originalname>`; je Datei ein Job | Datei wird nach Klassifikation in den Zielordner in Drive hochgeladen (resumable Upload, CR Abschnitt 13) |

Arbeitsverzeichnisse (alle unter `/srv/objektakte`, gemountet in `worker` und `web`):

```text
/srv/objektakte/
  transit/<object_id>/incoming/<uuid>/   Uploads bis zur Uebernahme nach Drive
  work/<sha256>/                         Original, Chunks, OCR-Zwischenstand; wird nach DONE geloescht (konfigurierbar)
  ocr-cache/<sha256>/pages/NNNN.txt      maskierter Seitentext, bleibt erhalten (Re-Klassifikation ohne erneute OCR)
  models/<version>/                      Klassifikator-Artefakte und Metriken
```

### 1.2 Schrittfolge

| Nr | Schritt | Kernaussage |
|---|---|---|
| 1 | Ingest | Job anlegen (Status NEW), Metadaten erfassen: Quelle, Name, MIME-Typ, Groesse, Drive-Elternordner. Dateien groesser als ein konfigurierbares Limit (ANNAHME: 500 MB, auf dem VPS nach Plattenplatz festlegen) werden nicht heruntergeladen, sondern direkt als Review-Fall gemeldet. |
| 2 | Download und Hash | Datei nach `work/<tmp>` laden, SHA-256 ueber die Bytes bilden, Verzeichnis nach `work/<sha256>` umbenennen. Fuer Drive-Dateien vorab die API-Pruefsumme gegen `documents` pruefen, um den Download bekannter Dubletten zu sparen. |
| 3 | Dublettenpruefung | Schluessel (object_id, sha256). Treffer: Status DUPLICATE, Ablage in 06_Sonstiges/03_Dubletten, Review-Eintrag mit Verweis auf das Original. Kein Treffer: weiter. Gleicher Hash in einem anderen Objekt: kein Dublettenstatus, nur Hinweis im Dokumentdatensatz (Verdacht auf Fehlablage), Pruefung in Schritt 8. |
| 4 | Formatweiche | PDF: weiter mit 5. Bildformate (JPG, PNG, TIFF inkl. Mehrseiten-TIFF): mit `img2pdf` verlustfrei in PDF wandeln, dann wie Scan-PDF. Office (DOCX, XLSX, CSV): Text direkt extrahieren, keine OCR; Eigentuemerlisten zusaetzlich an den Listenparser (CR Abschnitt 15) uebergeben. Andere Formate: 06_Sonstiges/02_Manuelle_Pruefung mit Review-Eintrag. |
| 5 | Seitenanalyse | Seitenzahl ermitteln, je Seite pruefen, ob eine Textebene vorhanden ist (Kriterium in 1.3). Ergebnis: Liste digitaler Seiten und OCR-pflichtiger Seiten. |
| 6 | Chunking | OCR-pflichtige Seiten in Chunks von K Seiten (ANNAHME: K = 20, konfigurierbar) aufteilen. Je Chunk ein eigener OCR-Job (Status OCR_QUEUED). Digitale Seiten werden ohne OCR direkt extrahiert (pdftotext je Seite). Dokumente mit weniger als K OCR-Seiten bilden einen Chunk. |
| 7 | OCR je Chunk | `ocrmypdf` mit `--skip-text`, `--jobs 1`, `--sidecar`, Sprache `deu`, Umgebungsvariable `OMP_THREAD_LIMIT=1`, damit ein Prozess genau einen Kern belegt. Text je Seite aus dem Sidecar (Seitentrenner Form Feed) in `ocr-cache/<sha256>/pages/NNNN.txt` schreiben, vorher IBAN-Maskierung (Abschnitt 5.4) anwenden. Erst wenn alle Chunks DONE sind, wechselt das Dokument nach OCR_DONE. |
| 8 | Klassifikation | Stufe 1, Stufe 2, bei Bedarf Stufe 3, dann Entscheidungsalgorithmus (Abschnitt 6) und Konfidenzmodell (Abschnitt 7). Ergebnis in `documents` und ggf. `document_segments` schreiben. Status CLASSIFIED. |
| 9 | Ablageentscheidung | Zielordner (Hauptordner, Unterordner, Eigentuemerakte) ueber Folder-ID-Cache aufloesen, fehlende Ordner anlegen (CR Abschnitt 4 und 9). Status MOVE_PENDING mit Ziel-Folder-ID als Zustandsmarke. |
| 10 | Drive-Verschiebung | Letzter Schritt. Drive-Datei: Elternordner wechseln (addParents/removeParents). Upload-Datei: resumable Upload in den Zielordner, danach Transitdatei loeschen. Anschliessend Elternordner zurueckelesen und mit Ziel vergleichen. Status DONE. Bei Ablage in 06_Sonstiges zusaetzlich Review-Eintrag (CR Abschnitt 8). |

Der Text je Seite wird in der Tabelle `document_pages` (document_id, page_no, text_masked, source digital|ocr, ocr_engine, ocr_mean_confidence) sowie im OCR-Cache abgelegt. Der Volltextindex wird ausschliesslich aus `text_masked` gebaut.

### 1.3 Kriterium Digital-PDF ohne OCR

Eine Seite gilt als digital und wird nicht durch Tesseract geschickt, wenn alle drei Bedingungen zutreffen:

1. Die extrahierte Textebene enthaelt mindestens N Zeichen (ANNAHME: N = 50, konfigurierbar; Verifikation an einer Stichprobe leerer Deckblaetter und gescannter Seiten mit Stempeltext).
2. Der Anteil alphanumerischer Zeichen am Text liegt ueber einem Schwellwert (ANNAHME: 60 Prozent), damit Seiten mit einer verunglueckten Alt-OCR-Ebene (Zeichensalat) nicht als digital gelten.
3. Die Seite besteht nicht nur aus einem einzigen Bild, das die Seitenflaeche nahezu vollstaendig bedeckt, waehrend die Textebene nur aus Kopf- oder Fusszeile stammt (Pruefung ueber Bildobjekte der Seite mit `pikepdf`).

Faellt Bedingung 2 durch, wird die Seite mit `ocrmypdf --redo-ocr` neu erkannt. Der `--skip-text`-Modus von ocrmypdf setzt dieselbe Entscheidung auf Werkzeugebene um; die eigene Vorpruefung dient der Chunk-Planung und der Kostenschaetzung, damit digitale Seiten gar nicht erst in OCR-Jobs landen.

### 1.4 Zwei-Phasen-OCR (Stellhebel, Vorschlag)

Fuer die Klassifikation reichen in der Regel die ersten Seiten. Vorschlag: Phase A erkennt die ersten M Seiten (ANNAHME: M = 3) plus letzte Seite mit hoher Prioritaet, danach laeuft Stufe 1 und 2 sofort. Phase B erkennt die restlichen Seiten in einer Warteschlange niedriger Prioritaet fuer Volltextsuche und Segmentierung. Ausnahme: Dokumente, die Stufe 1 oder 2 als Gesamtdokument mit eingebetteten Einzelteilen erkennen (Gesamtabrechnung, Protokoll), erhalten die vollstaendige OCR vor der Segmentierung, weil die Seitenbereiche sonst nicht bestimmbar sind. Die Ablage in Drive erfolgt erst nach Phase B, damit Segmente und Volltext vollstaendig sind; das Review Center zeigt den Vorschlag bereits nach Phase A.

## 2. Stufe 1: deterministisches Regelwerk

Stufe 1 laeuft immer und liefert zwei Arten von Treffern: harte Treffer (eindeutige Zuordnung, Konfidenz 1,0) und weiche Treffer (Hinweise mit Gewicht). Sie arbeitet auf Dateiname, Drive-Elternordner, Objektstammdaten, Verwaltungsart, bekannten Eigentuemern, Mietern, Einheiten, Vertragspartnern und dem in Stufe 2 erkannten Zeitbezug (Stufe 1 wird nach der NER ein zweites Mal ausgewertet, damit Regeln mit Zeitbezug greifen).

### 2.1 Eingangsgroessen

| Merkmal | Herkunft | Verwendung |
|---|---|---|
| Dateiname | Drive oder Upload | Muster wie `Einzelabrechnung_2025_WE03.pdf`, Jahreszahlen, Einheitenkuerzel, Nachnamen |
| Drive-Elternordner | Ordnerabgleich | Liegt die Datei bereits in einem der sechs Hauptordner oder einem Unterordner, ist das ein weicher Hinweis (Vorverwaltung oder Mitarbeiter hat sortiert), nie ein harter Treffer |
| Objektadresse und Objektnummer | `objects` | Positiv: Bezug zum Objekt. Negativ: Adresse oder Nummer eines anderen Objekts aus dem Bestand erkannt, eigene fehlt |
| Verwaltungsart | `objects.management_type` (WEG-Verwaltung, Mietverwaltung, WEG mit SE-Verwaltung; Werte laut Befund) | Steuert Kategorie-Prioren und Sonderregeln (Abschnitt 2.4) |
| Eigentuemer, Mieter, Einheiten | `owners`, `tenants`, `units`, Assignments | Gazetteer fuer Stufe 2, Bedingung `entity_required` in Regeln |
| Vertragspartner | Konfigurierbare Liste je Objekt (Versicherer, Hausmeisterdienst, Energieversorger, Vorverwaltung, eigene Firma) | Dienstleistervertraege und Rechnungen erkennen; eigene Firma als Bevollmaechtigte kennzeichnet die Verwaltervollmacht |
| Dokumentdatum, Zeitraum | Stufe 2 NER | Regeln mit Jahr oder Zeitraum, historische Zuordnung |

### 2.2 Regelformat

Regeln liegen als YAML im Repository (Seed) und werden in die Tabelle `classification_rules` geladen, dort versioniert und im Admin-Bereich editierbar. Jede Regel hat Prioritaet, Geltungsbereich, Bedingungen, Wirkung und Testbeispiele. Bedingungen werden UND-verknuepft; `any_of` erlaubt ODER. Negativbedingungen verhindern Fehltreffer.

```yaml
id: R-05-ABR-001
version: 3
priority: 100
scope: {management_types: [WEG-Verwaltung, WEG mit SE-Verwaltung]}
when:
  any_of:
    filename_regex: ['(?i)einzelabrechnung', '(?i)einzel.?abr']
    text_regex: ['(?i)^\s*einzelabrechnung\b', '(?i)abrechnungsergebnis.*(nachzahlung|guthaben)']
  entity_required: [unit, period_year]
  not_text_regex: ['(?i)gesamtabrechnung', '(?i)alle\s+einheiten']
then:
  category: 05_Eigentümerakte
  subfolder: 05_Abrechnungen
  subtype: Einzelabrechnung
  metadata: {abrechnungsjahr: '{period_year}'}
  confidence: 0.95
  hard: false
examples:
  positive: ['Einzelabrechnung 2025 WE03 Mustermann, Abrechnungsergebnis: Nachzahlung 312,40 EUR']
  negative: ['Gesamtabrechnung 2025 der WEG, alle Einheiten']
```

Weitere Regelbeispiele (verkuerzt):

```yaml
id: R-02-TE-001            # Teilungserklaerung, hart
when: {any_of: {text_regex: ['(?i)teilungserkl(ae|ä)rung', '(?i)gemeinschaftsordnung']}, not_text_regex: ['(?i)auszug.*(WE|einheit)\s*\d']}
then: {category: 02_Stammakte, subtype: Teilungserklärung, confidence: 1.0, hard: true}

id: R-01-VOLLM-001         # Verwaltervollmacht bleibt in 01 (CR Abschnitt 5, Zeile 10_Vollmachten)
when: {text_regex: ['(?i)vollmacht'], entity_required: [own_company_as_agent], not_text_regex: ['(?i)eigent(ue|ü)merversammlung.*vertret']}
then: {category: 01_Legitimationsunterlagen, subtype: Verwaltervollmacht, confidence: 0.9, hard: false}

id: R-05-VOLLM-002         # Eigentuemervollmacht
when: {text_regex: ['(?i)vollmacht'], entity_required: [owner], not_entity: [own_company_as_agent]}
then: {category: 05_Eigentümerakte, subfolder: 10_Vollmachten, subtype: Versammlungsvollmacht, confidence: 0.85, hard: false}

id: R-06-FREMD-001         # anderes Objekt des Bestands erkannt, eigenes nicht
when: {entity_required: [foreign_object_marker], not_entity: [own_object_marker]}
then: {category: 06_Sonstiges, subfolder: 04_Nicht_objektbezogen, confidence: 0.9, hard: false, proposal: 'Verschieben nach Objekt {foreign_object_number}'}
```

Regelauswertung: alle passenden Regeln werden gesammelt, nach Prioritaet sortiert; der beste harte Treffer gewinnt Stufe 1, sonst der beste weiche Treffer. Widerspruechliche harte Treffer (zwei Regeln mit `hard: true` und unterschiedlichen Kategorien) gelten als Konflikt und senken die Stufe-1-Konfidenz auf 0, damit Stufe 2 und 3 entscheiden. Jede Regel hat Unit-Tests aus ihren `examples`.

### 2.3 Regelkatalog (Startumfang, aus CR Abschnitt 5 und 6 abgeleitet)

| Zielbereich | Regelgruppe | Typische Marker |
|---|---|---|
| 01_Legitimationsunterlagen | Verwaltervertrag, Verwalterbestellung, Verwaltervollmacht | eigene Firma als Vertragspartner oder Bevollmaechtigte, "Bestellung des Verwalters" |
| 02_Stammakte | Teilungserklaerung, Gemeinschaftsordnung, vollstaendige Eigentuemerliste, Beschlusssammlung, Versammlungsprotokoll, Gebaeudeversicherung, Energieausweis, Dienstleistervertraege | Begriffe aus CR Abschnitt 6 Punkt 1; Versicherer und Dienstleister aus Vertragspartnerliste |
| 03_Buchhaltung | Gesamtjahresabrechnung, Gesamtwirtschaftsplan, Kontoauszuege, Belege, Rechnungen | "Gesamt", "alle Einheiten", Kontoauszug mit Gemeinschaftskonto, Rechnungsnummer plus Vertragspartner |
| 04_Mieterakte | Mietvertrag, Kaution, Betriebskostenabrechnung Mieter, Mieterkorrespondenz | Mietername aus `tenants`, "Mieter", "Vermieter", "Kaltmiete" |
| 05_Eigentümerakte | je Unterordner 01 bis 10 eine Regelgruppe nach der Tabelle in CR Abschnitt 5 | Eigentuemername, Einheit, Begriffe je Unterart (Lastschriftmandat, Sollstellung, Einzelabrechnung, Einzelwirtschaftsplan, Mahnung, Grundbuch, Veraeusserungsanzeige) |
| 06_Sonstiges/04 | Fremdobjekt, kein Objektmarker | Adresse oder Nummer anderes Objekt; keinerlei Bezug |

### 2.4 Verwaltungsart als Steuergroesse

| Verwaltungsart | Auswirkung |
|---|---|
| WEG-Verwaltung | Standardregeln. Mieterdokumente sind nicht Verwaltungsgegenstand. Vorschlag: bei erkanntem Mieter und bekannter Einheit Zuordnung zur Eigentuemerakte des Sondereigentuemers in 08_Korrespondenz mit Review-Pflicht; ohne Einheit 06_Sonstiges/02_Manuelle_Pruefung. |
| WEG mit SE-Verwaltung | Standardregeln plus 04_Mieterakte fuer Mieter der verwalteten Sondereigentumseinheiten. Ein Mietvertrag mit bekanntem Mieter geht nach 04_Mieterakte. |
| Mietverwaltung | Es gibt keine Gemeinschaft. Eigentuemer ist der Auftraggeber; dessen Unterlagen liegen in 05_Eigentümerakte mit einer Akte je Eigentuemer. Abrechnungen sind Betriebskostenabrechnungen an Mieter (04_Mieterakte). Begriffe Hausgeld, Wirtschaftsplan, Beschluss haben hier keine Zielregel und fuehren zu 06_Sonstiges/02_Manuelle_Pruefung (Verdacht auf Fehlablage aus einer WEG). |

Die Regel fuer reine WEG-Verwaltung ist ein Vorschlag, weil der CR diesen Fall nicht regelt (offene Frage).

## 3. Stufe 2: lokale Erkennung und Klassifikation

### 3.1 Named-Entity-Erkennung

Empfehlung: regel- und woerterbuchbasierte NER (Regex plus Gazetteer aus den Stammdaten des Objekts) als Standard. Ein statistisches NER-Modell (z. B. spaCy mit deutschem Modell) bleibt optional fuer Personennamen, die noch nicht in der DB stehen; es kostet RAM und ist auf OCR-Text weniger verlaesslich. Personen, die nicht in `owners` oder `tenants` stehen, werden zusaetzlich ueber Muster wie "Herr/Frau/Eheleute/Familie <Name>", "Eigentuemer: <Name>", Anschriftenblock und Grussformel gefunden und als Kandidaten ohne DB-Treffer gemeldet.

| Entitaet | Erkennung | Ausgabe |
|---|---|---|
| Einheit | Variantengenerator aus `units.unit_label` des Objekts. Fuer Label "WE 14" entstehen die Varianten `WE14`, `WE 14`, `WE-14`, `WE_14`, `Wohnung 14`, `Whg. 14`, `Einheit 14`, `WE 14` mit Lagezusatz `3.OG rechts` (Praefix-Treffer). Befund-Schreibweisen werden ueber ein konfigurierbares Praefix-Mapping abgedeckt: WE, Wohnung, WE N mit Lagezusatz N.OG rechts, mitte oder links, nur N (nur mit Kontextwort Einheit oder Wohnung), GE (Gewerbe), S, ST, STP, SP, Stellplatz Nr. N, GA, Garage, TG (Tiefgarage), MVW, Haus N, Container N, Lagerhalle N, VEN_SILN_N.L. Normalisierung: Grossschreibung, Leerzeichen und Trennzeichen entfernen, fuehrende Nullen kuerzen. | unit_id, Trefferposition, Seite, Anzahl Treffer je Einheit |
| Personen und Firmen | Fuzzy-Abgleich (Abschnitt 3.2) der Namenskandidaten gegen `owners.last_name`, `owners.company_name`, `tenants` | owner_id oder tenant_id mit Score, Kandidaten ohne DB-Treffer |
| Betraege | `\d{1,3}(?:\.\d{3})*,\d{2}\s?(?:EUR|€)` sowie Kontextwoerter (Nachzahlung, Guthaben, Rueckstand, Hausgeld, Miete) | Betrag, Kontext, Seite |
| Zeitbezug | Datum `\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b`, Monat `\b(0?[1-9]|1[0-2])/(20\d{2})\b`, Jahr mit Kontext (Abrechnungsjahr, Wirtschaftsjahr, Abrechnungszeitraum von bis, Wirtschaftsperiode), Datumszeile im Kopf | document_date, period_from, period_to, period_year, jeweils mit Quelle |
| IBAN, Kontonummer, BLZ, BIC | Muster in Abschnitt 5.4; IBAN zusaetzlich mit MOD-97-Pruefung fuer die Erkennungskonfidenz (die Maskierung greift unabhaengig davon) | nur letzte 4 Stellen und Bankname im Datensatz, Klartext nie |
| Objektmarker | eigene Objektnummer und Adresse (Strasse plus Hausnummer, Ortsname) sowie Adressen aller anderen Objekte des Bestands als Negativliste | own_object_marker, foreign_object_marker mit Objektnummer |

Zeitbezug-Praezedenz fuer die Zuordnung: Abrechnungs- oder Wirtschaftsjahr vor explizitem Zeitraum vor Forderungszeitraum (Mahnwesen) vor Dokumentdatum. Begruendung: ein Mahnschreiben vom 09/2026 ueber Hausgeld 2025 betrifft den Eigentuemer des Jahres 2025.

### 3.2 Unscharfer Abgleich gegen owners, tenants, units

- Bibliothek: `rapidfuzz` (aktuelle stabile Version zum Umsetzungszeitpunkt pruefen).
- Normalisierung vor dem Vergleich: Kleinschreibung, Umlaute und ß aufloesen (ae, oe, ue, ss), Bindestriche und Punkte entfernen, typische OCR-Verwechslungen in Ziffernkontexten zuruecknehmen (0 und O, 1 und l und I, 5 und S, 8 und B).
- Namen aus dem Eigentuemerfeld der Vorverwaltung liegen laut Befund in wechselnden Schreibweisen vor (Nachname, Vorname & Vorname; Vorname und Vorname Nachname; GbR; c/o; Schraegstrich). Der Namens-Splitter aus dem Listenparser erzeugt je Eigentuemer normalisierte Suchformen (Nachname allein, Nachname plus Vorname, Firmenkurzname), gegen die verglichen wird.
- Scores: `token_set_ratio` fuer Personennamen, `partial_ratio` fuer Firmennamen. Schwellwerte (ANNAHME: automatischer Treffer ab 90, Kandidat zwischen 78 und 89, darunter kein Treffer; Kalibrierung an den ersten zwei Objekten ueber die Review-Entscheidungen) sind Konfigurationswerte.
- Mehrere Eigentuemer mit gleichem Nachnamen im Objekt (laut Befund kommen doppelte Namen vor): ein Nachnamentreffer allein reicht nicht fuer einen automatischen Treffer; zusaetzlich muss Vorname oder Einheit passen, sonst Kandidatenliste.
- Einheitenabgleich ist exakt auf der normalisierten Form; unscharf nur bei OCR-Ziffernfehlern mit einer Stelle Abstand und wenn genau eine Einheit des Objekts passt.

### 3.3 Lokaler Textklassifikator: Vergleich und Empfehlung

| Kriterium | TF-IDF (Zeichen-n-Gramme 3 bis 5 plus Wort-1-2-Gramme) plus lineares Modell (logistische Regression oder linearer SVM mit Kalibrierung) | Satz-Embedding (mehrsprachiges Sentence-Transformer-Modell) plus logistische Regression |
|---|---|---|
| Latenz je Dokument auf CPU | ANNAHME: unter 10 ms bei 4.000 Zeichen Eingabe; Verifikation per Benchmark im Worker-Image | ANNAHME: 50 bis 300 ms je Dokument bei 512 Token Eingabe; abhaengig von Modellgroesse und Kernzahl |
| RAM | ANNAHME: unter 300 MB inkl. scikit-learn und Vokabular | ANNAHME: 0,5 bis 1,5 GB fuer Torch-Laufzeit und Modell; Worker-Image waechst um die Torch-Abhaengigkeit |
| Trainingsbedarf | Training in Sekunden auf CPU; braucht Beispiele je Klasse, lernt Vokabular der Domaene direkt | Embedding ist vortrainiert, der Klassifikator darauf braucht weniger Beispiele je Klasse; Feintuning des Embeddings nicht vorgesehen |
| Robustheit gegen OCR-Fehler | hoch, weil Zeichen-n-Gramme Teiltreffer in verstuemmelten Woertern liefern ("Einzelabrechn ng") | mittel; Subword-Tokenisierung reagiert empfindlich auf Zeichensalat, semantische Naehe hilft bei Synonymen |
| Erklaerbarkeit | hoch: Gewichte je n-Gramm sind lesbar, im Review Center als Begruendung anzeigbar | gering |
| Betrieb | im Worker-Prozess, kein eigener Dienst | eigener Container `classifier` (CR Abschnitt 7 optional), damit das Modell nur einmal im Speicher liegt |

Empfehlung: TF-IDF plus lineares Modell als Standard (Millisekunden je Dokument wie in CR Abschnitt 7 gefordert, kein zusaetzlicher Speicherbedarf neben den OCR-Prozessen). Der Klassifikator wird hinter einem Interface `LocalClassifier.predict(text, context) -> list[ScoredLabel]` gekapselt; die Embedding-Variante ist eine zweite Implementierung im optionalen Container `classifier` und wird per Konfiguration aktiviert, wenn die gemessene Makro-F1 des linearen Modells nach dem dritten Objekt unter einem Zielwert liegt (Zielwert als Konfiguration, ANNAHME: 0,85).

Ausgestaltung:
- Eingabe: normalisierter, maskierter Text der ersten M Seiten plus letzte Seite, gekuerzt auf ANNAHME 4.000 Zeichen, mit vorangestellten Kontexttoken (`__mgmt_weg__`, `__src_folder_03__`, `__has_unit__`, `__has_owner__`, `__has_iban__`, `__period_year__`), damit Verwaltungsart und Stufe-1-Signale als Merkmale wirken.
- Zwei Modelle: Modell A fuer Hauptkategorie (01 bis 05, nicht_objektbezogen), Modell B fuer Unterordner plus Unterart innerhalb von 05_Eigentümerakte (flaches Label `05/05_Abrechnungen/Einzelabrechnung`). Unterarten fuer 01 bis 04 werden erst geplant, wenn die Basisstruktur dieser Ordner spezifiziert ist (Befund Punkt 6.3).
- Ausgabe: kalibrierte Wahrscheinlichkeiten; die Top-2-Differenz geht als Sicherheitsmass in das Konfidenzmodell ein.

### 3.4 Kaltstart ohne Trainingsdaten

1. Regel-Labels: jeder harte Stufe-1-Treffer erzeugt ein Trainingsbeispiel mit `label_source = rule` und Gewicht 0,6.
2. Synthetische Beispiele: je Unterart eine Vorlagensammlung typischer Formulierungen (Kopfzeilen, Betreffe, Tabellenueberschriften) mit Platzhaltern fuer Namen, Einheiten, Betraege und Jahre; je Vorlage werden Varianten mit zufaelligen Platzhalterwerten und einer OCR-Stoerung (ANNAHME: 1 bis 3 Prozent Zeichenersetzungen, Verifikation gegen echte OCR-Fehlerraten der ersten Objekte) erzeugt, `label_source = synthetic`, Gewicht 0,3. Platzhalterwerte sind erfundene Namen, keine Stammdaten.
3. KI-Labels: Stufe-3-Ergebnisse mit Konfidenz ueber dem KI-Schwellwert werden mit `label_source = ai`, Gewicht 0,5 uebernommen.
4. Review-Labels: jede bestaetigte oder korrigierte Entscheidung im Review Center mit `label_source = review`, Gewicht 1,0 (CR Abschnitt 11).
5. Scharfschaltung: Modell A liefert erst dann eine Konfidenz ueber 0, wenn je Klasse mindestens n Beispiele vorliegen (ANNAHME: n = 15 gewichtet, konfigurierbar). Vorher traegt Stufe 2 nur NER und Abgleich bei; Stufe 3 uebernimmt entsprechend haeufiger, begrenzt durch das Kostenlimit je Objekt. Diese Kaltstartphase ist im Statusbereich sichtbar.

### 3.5 Nachtraining aus Review-Entscheidungen

- Ausloeser: naechtlicher Lauf oder nach ANNAHME 50 neuen Review-Labels (konfigurierbar), nie waehrend ein Objekt in Verarbeitung ist; jeder Job traegt die Modellversion, mit der er klassifiziert wurde (`documents.classifier_version`).
- Ablauf: Trainingsdaten aus `training_samples` (text_masked, label, label_source, weight, object_id) laden, 5-fache Kreuzvalidierung, Makro-F1 und Konfusionsmatrix je Klasse berechnen, Artefakt nach `/srv/objektakte/models/<version>/` schreiben (`model.joblib`, `metrics.json`, `labels.json`).
- Freigabe: automatische Aktivierung nur, wenn Makro-F1 nicht schlechter als das aktive Modell minus Toleranz (ANNAHME: 0,01) ist; sonst bleibt das alte Modell aktiv und der Admin sieht die Metriken. Rollback ist das Setzen der vorherigen Version auf aktiv (`classifier_models.active`).
- Schutz vor Drift: Labels aus 06_Sonstiges-Ablagen ohne Review-Bestaetigung werden nie trainiert; korrigierte Review-Entscheidungen ueberschreiben Regel- und KI-Labels desselben Dokuments.

## 4. Stufe 3: externe KI ueber Provider-Abstraktion

Stufe 3 wird nur aufgerufen, wenn die kombinierte Konfidenz aus Stufe 1 und 2 unter dem Schwellwert `threshold_ai_call` liegt (Abschnitt 7). Sie erhaelt nie Originaldateien, nie Bank- oder Ausweisdaten und keine Stammdatenlisten des Objekts, sondern nur den maskierten Textauszug und die Taxonomie.

### 4.1 Interface

```python
class ClassificationProvider(Protocol):
    name: str                       # "openai" | "anthropic"
    def classify(self, req: ClassificationRequest, timeout_s: float) -> ClassificationResult: ...
    def estimate_cost_eur(self, prompt_tokens: int, completion_tokens: int) -> Decimal: ...

@dataclass
class ClassificationRequest:
    excerpt_masked: str             # bereinigter, maskierter Textauszug, auf Tokenbudget gekuerzt
    filename_masked: str
    management_type: str            # Verwaltungsart
    taxonomy: Taxonomy              # erlaubte Kategorien, Unterordner, Unterarten (aus Konfiguration)
    unit_label_patterns: list[str]  # nur Praefixmuster (WE, GE, ST), keine konkreten Einheiten oder Namen
    hints: dict                     # Stufe-1/2-Signale ohne Personenbezug, z. B. {"rule_candidates": ["03_Buchhaltung"], "has_period_year": true}
```

Antwortschema (strikt, `additionalProperties: false`, wird beim jeweiligen Anbieter ueber dessen Mechanismus fuer strukturierte Ausgaben erzwungen und serverseitig mit `pydantic` validiert):

```json
{
  "type": "object",
  "required": ["object_related", "category", "subfolder", "subtype", "period", "mentioned_units", "mentioned_parties", "confidence", "reasoning"],
  "properties": {
    "object_related": {"type": "boolean"},
    "category": {"enum": ["01_Legitimationsunterlagen", "02_Stammakte", "03_Buchhaltung", "04_Mieterakte", "05_Eigentümerakte", "06_Sonstiges"]},
    "subfolder": {"type": ["string", "null"], "description": "nur aus der uebergebenen Taxonomie"},
    "subtype": {"type": ["string", "null"]},
    "period": {"type": "object", "properties": {"year": {"type": ["integer", "null"]}, "from": {"type": ["string", "null"]}, "to": {"type": ["string", "null"]}, "document_date": {"type": ["string", "null"]}}, "required": ["year", "from", "to", "document_date"], "additionalProperties": false},
    "mentioned_units": {"type": "array", "items": {"type": "string"}},
    "mentioned_parties": {"type": "array", "items": {"type": "string"}, "description": "Namen wie im Text, Abgleich erfolgt lokal"},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "reasoning": {"type": "string", "maxLength": 400}
  },
  "additionalProperties": false
}
```

Der Abgleich von `mentioned_parties` und `mentioned_units` gegen `owners`, `tenants` und `units` erfolgt ausschliesslich lokal (Abschnitt 3.2). Dadurch verlaesst keine Stammdatenliste das System.

### 4.2 Primaer, Fallback, Timeout, Wiederholung

| Parameter | Konfiguration je Provider | Startwert |
|---|---|---|
| Modell, Endpunkt, Region | `ai.providers.<name>.model`, `.endpoint`, `.region` | vom Auftraggeber nach AVV-Pruefung festzulegen (CR Abschnitt 0.1) |
| Timeout je Aufruf | `.timeout_s` | ANNAHME: 30 s; Verifikation anhand der gemessenen Latenzen der ersten 200 Aufrufe |
| Versuche je Provider | `.max_attempts` | 2 (zweiter Versuch nur bei Timeout, 5xx oder Schemaverletzung; bei Schemaverletzung mit Reparaturhinweis) |
| Reihenfolge | `ai.primary`, `ai.fallback` | konfigurierbar, Wechsel ohne Neustart |
| Gesamtbudget je Dokument | `ai.wall_budget_s` | ANNAHME: 120 s, danach Abbruch mit Status AI_FAILED |
| Nebenlaeufigkeit | eigene Queue `ai` mit Concurrency | ANNAHME: 3, damit KI-Aufrufe keine OCR-Slots belegen |
| Kostenlimit je Objekt | `ai.cost_limit_per_object_eur` | vom Auftraggeber festzulegen; Preis je 1.000 Tokens je Modell als Konfigurationswert aus der Preisliste des Anbieters zum Umsetzungszeitpunkt |
| Tokenbudget Eingabe | `ai.max_input_tokens` | ANNAHME: 3.000 Tokens; Kuerzung ueber konservative Zeichen-zu-Token-Umrechnung (ANNAHME: 3,5 Zeichen je Token fuer deutschen Text), Verifikation gegen die vom Anbieter zurueckgemeldeten Tokenzahlen |

Ablauf: Primaeranbieter, bis zu `max_attempts`; danach Fallback mit identischem Request; Ergebnisformat ist durch das Schema identisch (CR Abschnitt 14, Integrationstest Provider-Wechsel). Bei Ausfall beider Anbieter: Status AI_FAILED, Ablage 06_Sonstiges/01_Unklar mit Grund "KI nicht verfuegbar", Review-Eintrag, Hinweis im Statusbereich. Ein Nachklassifikationslauf (manuell oder zeitgesteuert, konfigurierbar) darf diese Dokumente spaeter erneut durch Stufe 3 schicken, solange kein Mensch sie im Review Center bearbeitet hat. Es wird an keiner Stelle geraten.

Kostenlimit erreicht: Stufe 3 wird fuer das Objekt deaktiviert, alle weiteren Dokumente unter dem Schwellwert gehen nach 06_Sonstiges/01_Unklar mit Grund "Kostenlimit erreicht"; der Admin kann das Limit erhoehen und den Nachklassifikationslauf starten.

### 4.3 Auszugsbildung und Prompt

- Auszug: erste M Seiten plus letzte Seite aus `text_masked`, Kopf- und Fusszeilen entdoppelt, Leerzeilen verdichtet, auf das Tokenbudget gekuerzt, Kuerzungsstelle mit `[...]` markiert.
- Systemprompt (Deutsch, versioniert in der Konfiguration): Taxonomie mit den Definitionen aus CR Abschnitt 5, Entscheidungsregel aus Abschnitt 6 in der dortigen Reihenfolge, Anweisung "nicht objektbezogen" und "unklar" explizit zu waehlen statt zu raten, Ausgabe nur im Schema.
- Der Prompt enthaelt nie Stammdatenlisten, nie IBAN oder Kontonummern (Maskierung vor der Auszugsbildung, zweite Pruefung unmittelbar vor dem Senden als Sicherheitsnetz, bei Treffer wird der Aufruf abgebrochen und protokolliert).

### 4.4 Protokollierung

Tabelle `ai_calls`: id, document_id, object_id, provider, model, prompt_tokens, completion_tokens, cost_eur, latency_ms, status (ok, timeout, schema_error, provider_error, blocked_by_mask_check), attempt, created_at, masked_prompt_sha256, response_json. Der maskierte Prompt selbst wird nur gespeichert, wenn `ai.store_masked_prompts = true` (Standard aus, Aufbewahrungsdauer konfigurierbar). Kosten je Objekt werden aus `ai_calls` summiert und auf der Statusseite und im Reporting (CR Abschnitt 13) angezeigt.

## 5. Datenschutz in der Pipeline

### 5.1 Grundsatz

IBAN und Kontonummern erscheinen nirgends im Klartext: nicht in `document_pages.text_masked`, nicht im Volltextindex, nicht im OCR-Cache, nicht in Logs, nicht in Prompts, nicht in Review-Center-Vorschauen. Die Maskierung ist der erste Verarbeitungsschritt nach der Texterkennung und laeuft vor jeder Persistierung.

### 5.2 Was gespeichert wird

| Ort | Inhalt |
|---|---|
| `document_pages.text_masked`, OCR-Cache, Volltextindex | Text mit Ersetzung `[IBAN_****1234]` (nur letzte 4 Stellen, konsistent mit `owners.iban_masked` aus CR Abschnitt 3), `[KONTONR]`, `[BLZ]`, `[BIC]` |
| Prompt an externe KI | `[IBAN]`, `[KONTONR]`, `[BLZ]`, `[BIC]` ohne Reststellen |
| Logs | nie Textinhalte; nur Kennungen (document_id, Seite, Anzahl maskierter Treffer). Zusaetzlich ein Log-Filter, der dieselben Muster auf jede Logzeile anwendet, falls doch Text in eine Fehlermeldung gelangt |
| `owners.iban_masked` | letzte 4 Stellen; die vollstaendige IBAN wird nach Empfehlung nicht gespeichert, sofern kein Prozess (z. B. SEPA-Einzug durch die Anwendung) sie braucht; falls doch, verschluesselt mit Schluessel als Docker Secret (offene Frage) |
| Originaldatei in Drive | unveraendert; das Original bleibt die einzige Quelle des Klartexts |

### 5.3 Zugriffsprotokoll

Tabelle `access_log`: user_id, action (view_document, download_document, reveal_iban_last4, export_list), document_id oder owner_id, object_id, timestamp, ip. Jede Anzeige eines Dokuments aus 05_Eigentümerakte und jeder Abruf maskierter Bankdaten wird protokolliert (CR Abschnitt 10 und 15). Eigentuemerakten sind nur fuer die Rollen Admin und Sachbearbeiter sichtbar; das Rollenmodell wird im Web-Entwurf dokumentiert.

### 5.4 Maskierungsmuster

Reihenfolge der Anwendung: DE-IBAN, generische IBAN, BIC, BLZ mit Kontextwort, Kontonummer mit Kontextwort. Die Muster sind bewusst weit gefasst; ein falsch positiver Treffer kostet nur ein maskiertes Zahlwort, ein falsch negativer waere ein Datenschutzverstoss.

```python
import re

# DE-IBAN: DE + 2 Pruefziffern + 18 Ziffern, mit oder ohne Leerzeichen in Vierergruppen,
# OCR-tolerant fuer O statt 0 in Ziffernpositionen
IBAN_DE = re.compile(r"\bDE[0-9O]{2}(?:[ \t]?[0-9O]{4}){4}[ \t]?[0-9O]{2}\b", re.IGNORECASE)

# Generische IBAN: 2 Buchstaben Laendercode, 2 Pruefziffern, 11 bis 30 alphanumerische Zeichen,
# in Vierergruppen mit optionalen Leerzeichen
IBAN_ANY = re.compile(r"\b[A-Z]{2}[0-9]{2}(?:[ \t]?[A-Z0-9]{4}){2,7}(?:[ \t]?[A-Z0-9]{1,3})?\b")

# BIC (optional, konfigurierbar): 4 Buchstaben Bank, 2 Buchstaben Land, 2 Zeichen Ort, optional 3 Zeichen Filiale
BIC = re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")

# BLZ: Kontextwort plus 8 Ziffern, optional in 3-3-2 gruppiert
BLZ = re.compile(r"(?i)\b(?:BLZ|Bankleitzahl)\b\s*[:.]?\s*(\d{3}[ ]?\d{3}[ ]?\d{2})")

# Kontonummer: Kontextwort plus 4 bis 12 Ziffern (auch mit Leerzeichen gruppiert)
KONTO = re.compile(r"(?i)\b(?:Konto(?:-|\s)?(?:Nr\.?|Nummer)|Kto\.?(?:-?Nr\.?)?|Kontonummer)\b\s*[:.]?\s*((?:\d[ ]?){4,12}\d)")

def mask(text: str, keep_last4: bool) -> tuple[str, int]:
    hits = 0
    def iban_repl(m):
        nonlocal hits; hits += 1
        raw = re.sub(r"\s", "", m.group(0))
        return f"[IBAN_****{raw[-4:]}]" if keep_last4 else "[IBAN]"
    text = IBAN_DE.sub(iban_repl, text)
    text = IBAN_ANY.sub(iban_repl, text)
    text = BIC.sub("[BIC]", text)
    text = BLZ.sub(lambda m: m.group(0).replace(m.group(1), "[BLZ]"), text)
    text = KONTO.sub(lambda m: m.group(0).replace(m.group(1), "[KONTONR]"), text)
    return text, hits
```

Das generische IBAN-Muster trifft auch Woerter wie Aktenzeichen der Form `AB12 3456 7890 12`; das ist beabsichtigt (lieber maskieren). Fuer den Volltextindex gilt `keep_last4 = true`, fuer Prompts `keep_last4 = false`. Unit-Tests (CR Abschnitt 14): DE-IBAN mit und ohne Leerzeichen, IBAN mit OCR-Fehler O statt 0, oesterreichische und niederlaendische IBAN, BLZ mit und ohne Gruppierung, Kontonummer mit "Kto.-Nr.", Negativfaelle (Datum, Betrag, Telefonnummer, Rechnungsnummer ohne Kontextwort bleibt unmaskiert), sowie ein Test, der nach der Maskierung mit dem IBAN-Erkenner erneut prueft und keinen Treffer finden darf.

## 6. Entscheidungsalgorithmus (CR Abschnitt 6 und 7)

### 6.1 Reihenfolge

Die drei Stufen liefern Signale; der Algorithmus legt die Reihenfolge fest, in der Signale zu einer Ablage werden. Die Reihenfolge aus CR Abschnitt 6 (Gemeinschaft vor Buchhaltung vor Eigentuemer) ist verbindlich; die Schritte 0, A und 3 sind Ergaenzungen, die der CR fuer 01, 04 und 06 impliziert, aber nicht ausformuliert (Vorschlag).

```text
ENTSCHEIDE(dokument, signale, kontext):
  0  Dublette (Hash bekannt im Objekt)                          -> 06_Sonstiges/03_Dubletten, Review
  0b Kein Objektbezug (Fremdobjekt erkannt und eigener Marker fehlt,
     oder alle Stufen melden nicht objektbezogen)               -> 06_Sonstiges/04_Nicht_objektbezogen, Review,
                                                                   Vorschlag "Verschieben nach Objekt NNN" falls erkannt
  A  Verwaltungslegitimation (Verwaltervertrag, Bestellung,
     Verwaltervollmacht mit eigener Firma als Bevollmaechtigte) -> 01_Legitimationsunterlagen
  1  Gesamtes Objekt oder gesamte Gemeinschaft (Teilungserklaerung,
     Gemeinschaftsordnung, vollstaendige Eigentuemerliste,
     Beschlusssammlung, Versammlungsprotokoll, Gebaeudeversicherung,
     Energieausweis, Dienstleistervertraege)                     -> 02_Stammakte
                                                                   plus Segmente mit Einheitenbezug relational zu 05 (6.3)
  2  Gesamtdokument der Buchhaltung (Gesamtjahresabrechnung,
     Gesamtwirtschaftsplan, Kontoauszuege, Belege)               -> 03_Buchhaltung
                                                                   plus eingebettete Einzelteile relational zu 05 (6.3)
  3  Mieterbezug (bekannter Mieter oder Mietvertragsmerkmale)   -> 04_Mieterakte bei Mietverwaltung und WEG mit SE-Verwaltung;
                                                                   bei reiner WEG-Verwaltung Review (Abschnitt 2.4)
  4  Bestimmter Eigentuemer, einzelne Einheit oder konkretes
     Eigentuemerkonto                                            -> 05_Eigentümerakte, danach ZUSATZPRUEFUNGEN (6.2)
  5  sonst                                                      -> 06_Sonstiges/01_Unklar, Review
```

Konfliktregel: Erkennt eine fruehere Stufe der Reihenfolge ein Gesamtdokument (Schritt 1 oder 2) und gleichzeitig Stufe 2 einen Einheiten- oder Eigentuemerbezug, gewinnt das Gesamtdokument fuer die physische Ablage; der Einzelbezug wird als Segment oder Relation ergaenzt. So landet ein Protokoll mit einem Beschluss zu WE03 physisch in 02_Stammakte und erscheint ueber die Relation in der Eigentuemerakte WE03 unter 07_Beschluesse (CR Abschnitt 5, Zeile 07_Beschluesse).

Jeder Schritt wird nur betreten, wenn die kombinierte Konfidenz fuer diese Kategorie den Schwellwert erreicht (Abschnitt 7). Ist die Konfidenz fuer keine Kategorie ausreichend, endet der Algorithmus in Schritt 5.

### 6.2 Die sechs Zusatzpruefungen fuer Eigentuemerdokumente

```text
ZUSATZPRUEFUNGEN(dokument, signale, kontext):
  P1 Eigentuemername erkennbar?      owners_found = Treffer aus 3.2 (owner_id, score) oder Namenskandidaten ohne DB-Treffer
  P2 Wohneinheit erkennbar?          units_found  = Treffer aus 3.1 (unit_id)
  P3 Verbindung bekannt?             assignments  = owner_unit_assignments zu (owner_id, unit_id)
  P4 Zeitbezug?                      period       = Praezedenz Abrechnungs-/Wirtschaftsjahr > Zeitraum > Forderungszeitraum > Dokumentdatum
  P5 Wer war im Zeitraum Eigentuemer?
       kandidaten(unit, period) = alle Assignments der Einheit mit
           valid_from <= period.to  UND (valid_to IST NULL ODER valid_to >= period.from)
       ohne period: kandidaten = alle Assignments der Einheit ueber die gesamte Historie
  P6 Eigentuemerbezogen oder objektbezogen?  bereits durch Reihenfolge 6.1 entschieden (Schritt 1 und 2 vor 4)

  FALLUNTERSCHEIDUNG:
  a) owner und unit erkannt, genau ein Assignment deckt period ab
       -> Ablage in Akte dieses Assignments (assignment_id), automatisch
  b) owner und unit erkannt, kein Assignment
       -> Unterart aus 02_Eigentumsnachweise (Kaufvertrag, Veraeusserungsanzeige, Mitteilung Eigentumswechsel):
          Review mit Vorschlag "neues Assignment anlegen" (CR Abschnitt 3: Wechsel als neue Zeile)
       -> sonst: 06_Sonstiges/02_Manuelle_Pruefung, Grund "Verbindung Eigentuemer/Einheit unbekannt", Kandidaten = Assignments der Einheit
  c) nur unit erkannt
       -> kandidaten(unit, period) hat genau einen Eigentuemer bzw. eine Eigentuemergruppe: automatisch
       -> mehrere (Wechsel im Zeitraum oder kein Zeitbezug): 06_Sonstiges/02_Manuelle_Pruefung mit Kandidatenliste (CR Abschnitt 7, letzter Satz)
       -> keine: 06_Sonstiges/02_Manuelle_Pruefung, Grund "Einheit ohne Eigentuemer im Zeitraum"
  d) nur owner erkannt
       -> owner hat genau ein Assignment, das period abdeckt: automatisch
       -> mehrere Einheiten: 06_Sonstiges/02_Manuelle_Pruefung, Kandidaten = Einheiten des Eigentuemers
          (Vorschlag: Sachbearbeiter kann im Review "Unbekannte_WE_Nachname" nach CR Abschnitt 4 waehlen)
  e) weder owner noch unit, Kategorie 05 aber sicher
       -> 06_Sonstiges/02_Manuelle_Pruefung ohne Kandidaten
          (Vorschlag: "Unzugeordnet" nach CR Abschnitt 4 nur als Review-Entscheidung, nie automatisch)
  f) Dokument benennt mehrere Eigentuemer derselben Einheit (Alt und Neu, z. B. Veraeusserungsanzeige)
       -> Ablage relational zu beiden Akten (document_owner_links), physisch in der Akte des im Dokument
          zuerst genannten Eigentuemers, kein Drive-Duplikat (CR Abschnitt 6)
  g) period ueberspannt einen Wechsel (Abrechnungsjahr 2026 bei Wechsel 01.07.2026)
       -> benennt das Dokument genau einen der Kandidaten: dieser; benennt es beide: Fall f; sonst Fall c mit Kandidatenliste
```

Sonderregel fuer 02_Eigentumsnachweise: Kaufvertrag und Veraeusserungsanzeige tragen ein Datum vor dem Uebergang von Nutzen und Lasten. Sie werden nach den genannten Parteien zugeordnet, nicht nach Dokumentdatum; sonst wuerde ein Kaufvertrag vom 05/2026 dem Alteigentuemer zugeschlagen, obwohl er den Neueigentuemer begruendet.

Pflichtmetadaten: fuer 05_Abrechnungen ist das Abrechnungsjahr, fuer 06_Wirtschaftsplaene das Wirtschaftsjahr Pflicht (CR Abschnitt 5). Fehlt es trotz sicherer Kategorie, geht das Dokument nach 06_Sonstiges/02_Manuelle_Pruefung mit Grund "Pflichtmetadatum fehlt".

### 6.3 Gesamtdokumente mit eingebetteten Einzelteilen

- Erkennung: Stufe 1 oder 2 klassifiziert das Dokument als Gesamtabrechnung, Gesamtwirtschaftsplan oder Versammlungsprotokoll; die Seitenanalyse findet wiederkehrende Abschnittsanfaenge (z. B. je Seite "Einzelabrechnung", "Eigentuemer:", Einheitenlabel im Kopf; bei Protokollen "TOP n" mit Einheiten- oder Eigentuemernennung).
- Segmentierung: Seitenbereiche von einem Abschnittsanfang bis vor den naechsten; je Segment eigene NER und Zusatzpruefungen (6.2) mit dem Zeitbezug des Gesamtdokuments.
- Speicherung: Tabelle `document_segments` (document_id, page_from, page_to, category, subfolder, subtype, owner_id, unit_id, assignment_id, period_year, confidence, review_state). Die Masterdatei liegt physisch in 03_Buchhaltung bzw. 02_Stammakte, die Eigentuemerakte zeigt das Segment als Eintrag mit Seitenbereich.
- Nur wenn `duplicate_owner_documents_in_drive = true` (Admin-Konfiguration, Standard false) wird je Segment ein PDF-Auszug erzeugt und in die Eigentuemerakte hochgeladen; der Auszug erhaelt einen eigenen Dokumentdatensatz mit Verweis auf Master und Seitenbereich, damit die Dublettenpruefung ihn nicht als Fremdkoerper meldet.
- Integrationstest (CR Abschnitt 14): Gesamtabrechnung mit 12 Einzelabrechnungen ergibt eine Masterdatei in 03_Buchhaltung, 12 Zeilen in `document_segments`, keine Drive-Duplikate. Segmente ohne eindeutigen Eigentuemer erzeugen je Segment einen Review-Eintrag mit Kandidatenliste; die Masterdatei wird trotzdem abgelegt.

## 7. Konfidenzmodell

### 7.1 Signale

| Stufe | Signal | Wertebereich |
|---|---|---|
| 1 | bester Regeltreffer: Kategorie, Unterordner, Unterart, `confidence`, `hard` | 0 bis 1, harter Treffer 1,0; Konflikt harter Treffer 0 |
| 2 | Klassifikator: kalibrierte Wahrscheinlichkeit der Top-Klasse und Abstand zur zweiten Klasse; NER-Stuetze: Eigentuemer, Einheit, Zeitbezug gefunden | 0 bis 1 |
| 3 | KI: `confidence` aus dem Schema, Uebereinstimmung mit Stufe 1 oder 2 | 0 bis 1 |

### 7.2 Kombination

```text
KOMBINIERE(s1, s2):
  wenn s1.hard und s2.top == s1.kategorie:               c = 1,0
  wenn s1.hard und s2.top != s1.kategorie und s2.p > p_widerspruch:
                                                           c = t_ai_call minus 0,01   (Konflikt: Stufe 3 erzwingen)
  wenn s1.hard sonst:                                      c = s1.confidence
  wenn s1.kategorie == s2.top:                             c = min(1, max(s1.confidence, s2.p) + bonus_einig)
  wenn s1.kategorie != s2.top:                             c = max(s1.confidence, s2.p) minus malus_uneinig, Kategorie des Groesseren
  wenn Stufe 1 ohne Treffer:                               c = s2.p * (1 minus abstandsfaktor * (1 minus s2.abstand))
  wenn c < t_auto:                                         Stufe 3 aufrufen (sofern Kostenlimit und Verfuegbarkeit)

NACH STUFE 3(c, kategorie_lokal, s3):
  wenn s3.kategorie == kategorie_lokal:                    c = min(1, max(c, s3.confidence) + bonus_einig)
  wenn s3.kategorie != kategorie_lokal und s3.confidence >= t_ai_override:
                                                           kategorie = s3.kategorie, c = s3.confidence, decided_by = ai, Stichprobenflag
  sonst:                                                   c bleibt unter t_auto -> 06_Sonstiges/01_Unklar
```

Startwerte, alle konfigurierbar in `classification.thresholds`, und ANNAHME bis zur Kalibrierung an den Review-Entscheidungen der ersten zwei Objekte: t_auto = 0,85, t_ai_call = 0,85 (identisch zu t_auto, kann niedriger gesetzt werden, um Kosten zu sparen), t_ai_override = 0,90, p_widerspruch = 0,90, bonus_einig = 0,05, malus_uneinig = 0,25, abstandsfaktor = 0,5, Stichprobe fuer KI-Entscheidungen 10 Prozent. Die Kalibrierung vergleicht je Schwellwertstufe den Anteil spaeter korrigierter Entscheidungen; die Werte werden so gesetzt, dass der Korrekturanteil bei automatischen Ablagen unter einem Zielwert liegt (Zielwert vom Auftraggeber, Vorschlag 2 Prozent).

### 7.3 Zuordnung zu 06_Sonstiges

| Unterordner | Regel | Review-Eintrag |
|---|---|---|
| 01_Unklar | Objektbezug vorhanden oder unbekannt, aber keine Kategorie erreicht t_auto nach allen verfuegbaren Stufen; oder Stufe 3 nicht verfuegbar bzw. Kostenlimit erreicht und lokale Konfidenz unter t_auto | ja, mit Top-3-Kandidaten aller Stufen und Begruendungen |
| 02_Manuelle_Pruefung | Kategorie sicher, aber Zuordnung nicht: Eigentuemer mehrdeutig (Kandidatenliste), Verbindung Eigentuemer/Einheit unbekannt, Pflichtmetadatum fehlt, Widerspruch zwischen harter Regel und Klassifikator, den Stufe 3 nicht aufloest, Mieterdokument in reiner WEG-Verwaltung, nicht unterstuetztes Dateiformat, Datei ueber Groessenlimit | ja, mit Kandidatenliste und Pflichtfeldern nach CR Abschnitt 11 |
| 03_Dubletten | identischer SHA-256 innerhalb desselben Objekts | ja, mit Verweis auf Original; Loeschung nur durch Admin, nie automatisch |
| 04_Nicht_objektbezogen | Fremdobjekt-Marker ohne eigenen Marker mit Konfidenz ueber t_auto; oder alle Stufen `object_related = false` | ja, mit Vorschlag "Verschieben nach Objekt NNN", falls erkannt |

Jede Ablage in 06_Sonstiges erzeugt den Review-Eintrag im selben Datenbank-Transaktionsschritt wie den Dokumentdatensatz, damit kein Dokument in 06_Sonstiges ohne Review-Eintrag existiert (CR Abschnitt 8). Der Anteil 06_Sonstiges je Objekt wird als KPI ausgewiesen, Zielwert unter 5 Prozent.

Vorschlag zur Physik der Ablage: Dokumente in 06_Sonstiges/02_Manuelle_Pruefung werden erst nach der Review-Entscheidung an den finalen Ort verschoben; die Ordner `Unbekannte_WE_Nachname` und `Unzugeordnet` aus CR Abschnitt 4 sind Ziele von Review-Entscheidungen, nicht der Automatik. Begruendung: die Drive-Ordner sind eine Ansicht auf das Datenmodell (CR Abschnitt 4), und eine mehrdeutige Zuordnung hat keinen Datenmodell-Eintrag. Bestaetigung durch den Auftraggeber erforderlich.

## 8. Testkatalog (synthetische Beispieldokumente, CR Abschnitt 14)

Testumgebung (synthetisch, keine Stammdaten des Bestands):

| Objekt | Verwaltungsart | Einheiten und Zuordnungen |
|---|---|---|
| 623 Düsseldorf, Joachimstraße 49 (Beispielobjekt des CR, laut Befund nicht im Bestand) | WEG mit SE-Verwaltung | WE03 Mustermann (seit 2015); WE05 Altmann (bis 30.06.2026), Neumann (ab 01.07.2026); WE07 und ST02 Beispiel; WE02 Eigentuemer Sonder, Mieter Mieterling (SE-Verwaltung) |
| 624 Musterstadt, Musterweg 1 | WEG-Verwaltung | WE01 Erste, WE02 Zweite; keine Mieter erfasst |
| 625 Musterstadt, Mietstraße 5 | Mietverwaltung | Eigentuemer Auftraggeber GmbH; Mieter Mieterling-Zwei in Wohnung 4 |
| 631 Musterstadt, Beispielweg 2 | WEG-Verwaltung | nur als Fremdobjekt-Marker relevant |

Spalte Stufe: 0 = Ingest/Hash, 1 = Regelwerk, 2 = lokal, 3 = externe KI, R = Ablage in 06_Sonstiges mit Review-Eintrag. Kategorie 05 nennt die Zielakte nach CR Abschnitt 4.

| Nr | Titel (Dateiname) | Kurzinhalt | Kategorie | Unterordner / Akte | Unterart | Stufe | Erwartung und Pruefpunkt |
|---|---|---|---|---|---|---|---|
| T01 | Teilungserklaerung_1998.pdf | Notarielle Teilungserklaerung mit Gemeinschaftsordnung, alle Einheiten mit MEA | 02_Stammakte | (Unterstruktur 02 nicht Teil des CR) | Teilungserklärung | 1 | harter Regeltreffer; Einheitennennungen erzeugen keine 05-Zuordnung (Schritt 1 vor 4) |
| T02 | Protokoll_ETV_2025.pdf | Protokoll der Eigentuemerversammlung, TOP 1 bis 6 allgemein (Jahresabrechnung, Wirtschaftsplan, Verwalterentlastung) | 02_Stammakte | | Versammlungsprotokoll | 1 | keine Segmente |
| T03 | Protokoll_ETV_2026.pdf | wie T02, zusaetzlich TOP 7 "Genehmigung bauliche Veraenderung WE03, Antragsteller Mustermann", Seiten 5 bis 6 | 02_Stammakte | Segment Seiten 5 bis 6 relational zu WE03_Mustermann/07_Beschluesse | Versammlungsprotokoll; Segment Einzelbeschluss | 2 | Master in 02, ein Segment, kein Drive-Duplikat |
| T04 | Beschlusssammlung_Stand_2026.pdf | Fortlaufende Beschlusssammlung mit Beschluessen zu mehreren Einheiten | 02_Stammakte | | Beschlusssammlung | 1 | trotz Einheitennennungen kein 05 (CR Abschnitt 5, Zeile 07) |
| T05 | Police_Gebaeudeversicherung.pdf | Versicherungsschein Wohngebaeude, Versicherer aus Vertragspartnerliste | 02_Stammakte | | Gebäudeversicherung | 1 | Vertragspartner-Marker |
| T06 | Energieausweis_2024.pdf | Energieausweis fuer das Gebaeude | 02_Stammakte | | Energieausweis | 1 | |
| T07 | Hausmeistervertrag.pdf | Dienstleistervertrag zwischen WEG und Hausmeisterdienst | 02_Stammakte | | Dienstleistervertrag | 1 | |
| T08 | Eigentuemerliste_Vorverwaltung.xlsx | Vollstaendige Eigentuemerliste mit Einheit, Name, Anschrift | 02_Stammakte | | Eigentümerliste | 1 | zusaetzlich Uebergabe an Listenparser mit Review (CR Abschnitt 15); kein 05 |
| T09 | Gesamtabrechnung_2025.pdf | Gesamtjahresabrechnung 2025 der WEG, 18 Seiten, ohne Einzelabrechnungen | 03_Buchhaltung | | Gesamtjahresabrechnung | 1 | Abrechnungsjahr 2025 als Metadatum |
| T10 | Jahresabrechnung_2025_komplett.pdf | Gesamtabrechnung 2025, Seiten 1 bis 20, danach 12 Einzelabrechnungen je 4 Seiten mit Kopf "Einzelabrechnung, Eigentuemer, Einheit" | 03_Buchhaltung | 12 Segmente relational zu den Eigentuemerakten, Unterordner 05_Abrechnungen | Gesamtjahresabrechnung; Segmente Einzelabrechnung, Jahr 2025 | 2 | Segment WE05 geht an Altmann (Eigentuemer 2025); eine Masterdatei, 12 Zeilen `document_segments`, keine Duplikate |
| T11 | Einzelabrechnung_2025_WE03.pdf | Einzelabrechnung 2025 fuer WE03, Eigentuemer Mustermann, Nachzahlung 312,40 EUR | 05_Eigentümerakte | WE03_Mustermann/05_Abrechnungen | Einzelabrechnung, Jahr 2025 | 1 | Dateiname und Inhalt konsistent |
| T12 | Einzelwirtschaftsplan_2026_WE07.pdf | Einzelwirtschaftsplan 2026, Hausgeld monatlich 245,00 EUR, Eigentuemer Beispiel | 05_Eigentümerakte | WE07_Beispiel/06_Wirtschaftsplaene | Einzelwirtschaftsplan, Wirtschaftsjahr 2026 | 2 | Beispiel besitzt WE07 und ST02; Einheit im Dokument entscheidet |
| T13 | Gesamtwirtschaftsplan_2026.pdf | Gesamtwirtschaftsplan mit Verteilerschluessel, alle Einheiten | 03_Buchhaltung | | Gesamtwirtschaftsplan | 1 | |
| T14 | Kontoauszug_Gemeinschaftskonto_2026-03.pdf | Kontoauszug des Gemeinschaftskontos mit IBAN und Buchungen einzelner Eigentuemer | 03_Buchhaltung | | Kontoauszug | 1 | IBAN im Volltext als `[IBAN_****NNNN]`; Eigentuemernamen in Buchungszeilen erzeugen kein 05 |
| T15 | SEPA_Mandat_Mustermann.pdf | Lastschriftmandat, Zahlungspflichtiger Mustermann, WE03, IBAN mit Leerzeichen | 05_Eigentümerakte | WE03_Mustermann/03_SEPA | Lastschriftmandat | 2 | Maskierung vor Persistierung; Test prueft, dass kein IBAN-Klartext in `document_pages` steht |
| T16 | Bankverbindung_neu_Beispiel.pdf | Schreiben von Beispiel: neue Bankverbindung ab 01.10.2026, IBAN ohne Leerzeichen, Einheit WE07 | 05_Eigentümerakte | WE07_Beispiel/01_Stammdaten | Bankverbindungsmitteilung | 2 | letzte 4 Stellen fuer `owners.iban_masked` als Vorschlag ins Review |
| T17 | Grundbuchauszug_WE05_2026-08.pdf | Grundbuchauszug vom 12.08.2026, Eigentuemer Neumann, Sondereigentum Nr. 5 | 05_Eigentümerakte | WE05_Neumann/02_Eigentumsnachweise | Grundbuchauszug | 2 | Datum nach Wechsel, Name Neumann; Sondereigentumsnummer als Einheitenvariante |
| T18 | Kaufvertrag_WE05.pdf | Notarieller Kaufvertrag vom 05.05.2026, Verkaeufer Altmann, Kaeufer Neumann, Uebergang Nutzen und Lasten 01.07.2026 | 05_Eigentümerakte | relational zu WE05_Altmann und WE05_Neumann/02_Eigentumsnachweise, physisch beim zuerst genannten | Kaufvertrag | 2 | Sonderregel Parteien vor Datum; ohne diese Regel Fehlzuordnung allein an Altmann |
| T19 | Veraeusserungsanzeige_WE05.pdf | Mitteilung des Notars ueber den Eigentumswechsel WE05 zum 01.07.2026 | 05_Eigentümerakte | relational zu beiden Akten, 02_Eigentumsnachweise | Mitteilung Eigentumswechsel | 2 | Fall f; Review-Vorschlag "Assignment pruefen" nur, wenn Neumann noch nicht in der DB |
| T20 | Mahnung_Hausgeld_2025-03_WE05.pdf | Zahlungserinnerung vom 15.04.2025 ueber Hausgeld 03/2025, WE05, Rueckstand 245,00 EUR | 05_Eigentümerakte | WE05_Altmann/09_Mahnwesen | Zahlungserinnerung | 2 | Pflichtfall CR Abschnitt 14: Dokument 03/2025 bei Wechsel 01.07.2026 gehoert zum Alteigentuemer |
| T21 | Anwaltsschreiben_Hausgeld_2025.pdf | Schreiben einer Kanzlei vom 03.09.2026 zu Hausgeldrueckstaenden 2025, Schuldner Altmann, WE05 | 05_Eigentümerakte | WE05_Altmann/09_Mahnwesen | Anwaltliches Schreiben | 2 | Forderungszeitraum und Name schlagen Dokumentdatum |
| T22 | Ratenzahlung_WE05.pdf | Zahlungsvereinbarung vom 15.08.2026 mit Neumann ueber Sonderumlage | 05_Eigentümerakte | WE05_Neumann/04_Hausgeld | Zahlungsvereinbarung | 2 | Datum nach Wechsel |
| T23 | Eigentuemerkonto_WE05.pdf | Kontoauszug Eigentuemerkonto WE05, Scan ohne lesbares Datum, Name durch Stempel verdeckt | 06_Sonstiges | 02_Manuelle_Pruefung | Eigentümerkonto (Kategorie sicher) | R | kein Zeitbezug, zwei historische Eigentuemer: Kandidatenliste Altmann, Neumann; kein Raten (CR Abschnitt 7) |
| T24 | Vollmacht_Verwaltung.pdf | Vollmacht der Gemeinschaft an die Hausverwaltung Müller GmbH zur Vertretung gegenueber Dritten | 01_Legitimationsunterlagen | | Verwaltervollmacht | 1 | eigene Firma als Bevollmaechtigte (CR Abschnitt 5, Zeile 10) |
| T25 | Vollmacht_ETV_2026_Mustermann.pdf | Mustermann bevollmaechtigt Beispiel zur Vertretung in der Versammlung am 20.05.2026 | 05_Eigentümerakte | WE03_Mustermann/10_Vollmachten | Versammlungsvollmacht | 2 | Vollmachtgeber fuehrt, nicht der Bevollmaechtigte; Abgrenzung zu T24 |
| T26 | Einwendung_Abrechnung_2024_Mustermann.pdf | Schreiben von Mustermann mit Einwendungen gegen die Einzelabrechnung 2024 | 05_Eigentümerakte | WE03_Mustermann/08_Korrespondenz | Einwendung gegen Abrechnung | 2 | Begriff Einzelabrechnung im Text darf nicht zu 05_Abrechnungen fuehren (Regel R-05-ABR-001 verlangt Abrechnungskopf) |
| T27 | Mietvertrag_WE02_Mieterling.pdf | Mietvertrag ueber WE02, Mieter Mieterling, Vermieter Sonder, Objekt 623 | 04_Mieterakte | (Unterstruktur 04 nicht Teil des CR) | Mietvertrag | 1 | Pflichtfall: Mieterdokument in WEG mit SE-Verwaltung |
| T28 | Mietvertrag_Unbekannt.pdf | Mietvertrag ueber WE01 in Objekt 624 (reine WEG-Verwaltung), Mieter unbekannt | 06_Sonstiges | 02_Manuelle_Pruefung, Vorschlag WE01_Erste/08_Korrespondenz | Mietvertrag | R | Regel aus Abschnitt 2.4 (Vorschlag, offene Frage) |
| T29 | Einzelabrechnung_2025_WE03 (Kopie).pdf | byteidentische Kopie von T11, anderer Dateiname | 06_Sonstiges | 03_Dubletten | | 0 | Hash-Treffer vor OCR; Review verweist auf T11; keine erneute OCR |
| T30 | Rechnung_Dachdecker.pdf | Handwerkerrechnung fuer "Beispielweg 2, Musterstadt" (Objekt 631), abgelegt im Ordner von 623 | 06_Sonstiges | 04_Nicht_objektbezogen | | 1 | Fremdobjekt-Marker; Vorschlag "Verschieben nach Objekt 631" |
| T31 | Scan_Werbeprospekt.pdf | Werbeprospekt eines Baumarkts, kein Objekt, keine Person | 06_Sonstiges | 04_Nicht_objektbezogen | | 3 | Stufe 3 liefert `object_related = false`; ohne KI 01_Unklar |
| T32 | Notiz_handschriftlich.pdf | Handschriftliche Notiz, OCR-Text unlesbar, keine Entitaeten | 06_Sonstiges | 01_Unklar | | R | alle Stufen unter Schwellwert; Review mit Top-3-Kandidaten |
| T33 | Betriebskostenabrechnung_2025_Whg4.pdf | Betriebskostenabrechnung 2025 an Mieter Mieterling-Zwei, Wohnung 4, Objekt 625 | 04_Mieterakte | | Betriebskostenabrechnung | 1 | Mietverwaltung: Begriff Abrechnung fuehrt nicht zu 05 |
| T34 | Hausgeld_Sollstellung_2026.pdf | Sollstellung Hausgeld 2026 fuer Wohnung 4 in Objekt 625 | 06_Sonstiges | 02_Manuelle_Pruefung | | R | Hausgeld in Mietverwaltung: Verdacht auf Fehlablage aus einer WEG (Abschnitt 2.4) |

Abnahme des Katalogs: mindestens 20 Beispieldokumente fuer die Abgrenzungsregel (CR Abschnitt 14) sind mit T01 bis T22 und T24 bis T27 abgedeckt; die Pflichtfaelle Eigentuemerwechsel (T20), IBAN-Maskierung (T15, T16), Gesamtabrechnung mit Einzelabrechnungen (T10) und Dublette (T29) sind enthalten. Der Katalog liegt als Fixture im Repository (Textinhalt plus erwartete Entscheidung als YAML), die Testdokumente werden daraus generiert (digital und als 300-dpi-Rasterung fuer den OCR-Pfad).

## 9. Performance-Modell und Messplan

### 9.1 Modell

```text
Seiten pro Minute = Prozesse x 60 / Sekunden je Seite
Dauer in Minuten  = Seiten / (Seiten pro Minute)
Sekunden je Seite (gemischt) = Anteil Scan x s_scan + Anteil Digital x s_digital
```

Planungsgroessen (alle ANNAHME, Verifikation nach 9.3):

| Groesse | Wert | Verifikation |
|---|---|---|
| s_scan Standard: Sekunden je Scan-Seite bei 300 dpi, Tesseract mit tessdata `deu` Standard, ein Prozess auf einem Kern, inkl. Rasterung durch ocrmypdf | ANNAHME: 4,0 s | Benchmark 50 Seiten auf dem VPS |
| s_scan Fast: dieselbe Seite mit `tessdata_fast` `deu` | ANNAHME: 2,0 s | Benchmark, zusaetzlich Fehlerrate gegen Standard vergleichen |
| s_digital: Textextraktion je Seite ohne OCR | ANNAHME: 0,1 s | Benchmark 500 digitale Seiten |
| Anteil Digital-PDF im Uebernahmebestand | ANNAHME: 40 Prozent | Seitenanalyse (1.3) der ersten zwei realen Objekte |
| Seiten je Dokument im Mittel | ANNAHME: 8 | Statistik der ersten zwei Objekte |
| Anteil Dokumente mit Stufe-3-Aufruf | ANNAHME: 20 Prozent in der Kaltstartphase, sinkend | `ai_calls` je Objekt |
| Dauer je Stufe-3-Aufruf inkl. Netz | ANNAHME: 8 s | `ai_calls.latency_ms` |
| Dauer je Drive-Verschiebung oder Upload | ANNAHME: 1 s | Adapter-Metrik |
| RAM je OCR-Prozess in der Spitze | ANNAHME: 0,8 GB | `docker stats` und cgroup `memory.peak` waehrend Benchmark |

Gemischte Sekunden je Seite: Standard 0,6 x 4,0 + 0,4 x 0,1 = 2,44 s; Fast 0,6 x 2,0 + 0,4 x 0,1 = 1,24 s.

### 9.2 Rechenweg fuer 10.000 Seiten

| Prozesse | Seiten/min Standard gemischt | Dauer | Seiten/min Fast gemischt | Dauer | Worst Case nur Scan Standard | Dauer | Worst Case nur Scan Fast | Dauer |
|---|---|---|---|---|---|---|---|---|
| 4 | 4 x 60 / 2,44 = 98,4 | 101,6 min | 4 x 60 / 1,24 = 193,5 | 51,7 min | 4 x 60 / 4,0 = 60,0 | 166,7 min | 120,0 | 83,3 min |
| 6 | 6 x 60 / 2,44 = 147,5 | 67,8 min | 290,3 | 34,4 min | 90,0 | 111,1 min | 180,0 | 55,6 min |
| 8 | 8 x 60 / 2,44 = 196,7 | 50,8 min | 387,1 | 25,8 min | 120,0 | 83,3 min | 240,0 | 41,7 min |

Zusatzlast ausserhalb der OCR (parallel in eigenen Queues, nicht auf dem kritischen Pfad, solange die Queue-Concurrency reicht): 10.000 / 8 = 1.250 Dokumente; Klassifikation lokal 1.250 x 10 ms = 12,5 s; Stufe 3: 0,2 x 1.250 = 250 Aufrufe x 8 s / Concurrency 3 = 667 s, rund 11 min; Drive: 1.250 x 1 s / Concurrency 2 = 625 s, rund 10 min.

Bewertung: Mit den Annahmen wird das Ziel von unter 180 Minuten bereits mit 4 Prozessen im gemischten Fall deutlich erreicht (rund 102 min). Der Worst Case (10.000 reine Scan-Seiten, Standardmodell, 4 Prozesse) liegt mit rund 167 min knapp unter dem Ziel und ohne Reserve; dafuer ist `tessdata_fast` oder eine hoehere Prozesszahl vorzusehen. Interne Planungsvorgabe: OCR-Anteil unter 120 min, damit Stufe 3, Drive-Ablage und Wiederholungen Platz haben. Die tatsaechliche Prozesszahl ergibt sich aus `nproc` minus 1 (CR Abschnitt 7); ob 4, 6 oder 8 Prozesse verfuegbar sind, ist nicht bekannt (Befund Punkt 2).

### 9.3 Messplan auf dem VPS

1. Ressourcen erfassen: `nproc`, `free -h`, `df -h`, `cat /sys/fs/cgroup/cpu.max` im Worker-Container; Werte in den Plan uebernehmen und als Compose-Variablen `WORKER_OCR_PROCESSES`, `WORKER_CPUS`, `WORKER_MEM_LIMIT` setzen.
2. Benchmark Einzelprozess: 50 synthetische Scan-Seiten (300 dpi, Text mit Tabellen, aus dem Testkatalog gerastert) mit `deu` Standard und `tessdata_fast`; Messwerte s_scan je Variante, Zeichenfehlerrate gegen den bekannten Quelltext.
3. Skalierung: gleiche Seiten mit 2, 4 und `nproc` minus 1 Prozessen; pruefen, ob Seiten pro Minute linear skaliert (Hinweis auf I/O- oder Speicherengpass, wenn nicht); RAM-Spitze je Prozess und gesamt.
4. Gemischter Lauf 1.000 Seiten (Scan und Digital gemischt) inkl. Klassifikation und Drive-Ablage in ein Testobjekt; Messung Seiten pro Minute Ende zu Ende, Anteil Stufe 3, Kosten.
5. Abnahmelauf 10.000 Seiten (CR Abschnitt 14) mit parallelem Latenzmesser: alle 5 Sekunden ein Aufruf einer Review-Center-Seite mit Datenbankzugriff, p95 unter 2 s; Aufzeichnung Seiten pro Minute, RAM-Spitze (`docker stats` im Intervall), Plattenverbrauch `work/` und `ocr-cache/`.
6. Bericht: Messwerte ersetzen die Annahmen in 9.1; Konfiguration (Prozesse, Modell Standard oder Fast, Chunkgroesse) wird aus den Messwerten abgeleitet und dokumentiert.

### 9.4 Stellhebel

| Hebel | Wirkung | Kosten |
|---|---|---|
| `tessdata_fast` statt Standard | ANNAHME: etwa Faktor 2 bei der OCR-Zeit | geringere Erkennungsguete; Entscheidung nach gemessener Fehlerrate, konfigurierbar je Objekt |
| Aufloesung: Rasterung auf 300 dpi begrenzen (hoeher aufgeloeste Scans herunterrechnen) | weniger Pixel je Seite | unter 300 dpi nicht empfohlen fuer kleine Schrift in Abrechnungstabellen |
| ocrmypdf ohne `--clean`, `--deskew`, mit `--optimize 0` | spart Vor- und Nachverarbeitung je Seite | keine Bildaufbereitung; nur bei schlechten Scans gezielt aktivieren |
| Chunking (K Seiten je Job) | gleichmaessige Auslastung aller Prozesse, grosse Dateien blockieren nicht | Zusammenfuehrung der Seitentexte, mehr Jobs |
| Zwei-Phasen-OCR (1.4) | Klassifikation und Review-Vorschlag frueh, Volltext spaeter | Komplexitaet im Status-Automaten |
| Digital-PDF ohne OCR (1.3) | jede digitale Seite kostet Bruchteile einer Sekunde | keine |
| Prioritaeten: P0 Eigentuemerlisten und Legitimation, P1 digitale PDFs, P2 Scans aufsteigend nach Seitenzahl, P3 grosse Scans | Stammdaten stehen frueh fuer den Abgleich bereit, Review Center fuellt sich schnell mit fertigen Faellen | Warteschlange mit Prioritaetsstufen |
| Getrennte Queues `ocr`, `classify`, `ai`, `io` | KI- und Drive-Wartezeiten belegen keine OCR-Slots | mehrere Worker-Prozesse mit eigener Concurrency |

### 9.5 CPU- und Speicherlimits

- Worker-Container: `cpus: <nproc minus 1>`, Memory-Limit = OCR-Prozesse x RAM je Prozess plus 1 GB Grundlast (mit ANNAHME 0,8 GB je Prozess: bei 4 Prozessen rund 4,2 GB, bei 8 rund 7,4 GB; nach Messung anpassen). `OMP_THREAD_LIMIT=1` und `ocrmypdf --jobs 1`, damit die Prozesszahl der tatsaechlichen Kernbelegung entspricht. OCR-Unterprozesse mit `nice`.
- Web-Container: eigenes CPU-Limit (mindestens 1 Kern) und hoehere `cpu_shares` als der Worker; DB und Redis eigene Limits. Die Summe der Limits darf die Kernzahl uebersteigen, entscheidend ist, dass der Worker nie mehr als `nproc` minus 1 belegt.
- Abnahmekriterium: p95 der Review-Center-Antwortzeit unter 2 s waehrend des 10.000-Seiten-Laufs (CR Abschnitt 14). Wird es verfehlt, zuerst Prozesse um eins reduzieren, dann Chunkgroesse verkleinern.

## 10. Idempotenz und Wiederaufnahme

### 10.1 Grundsatz

Die Datenbank ist die einzige Wahrheit ueber den Zustand eines Dokuments. Redis transportiert nur Auftraege. Jeder Schritt prueft vor der Arbeit den DB-Zustand und schreibt nach der Arbeit den Folgezustand in einer Transaktion. Ein Auftrag, der einen bereits erledigten Schritt vorfindet, endet ohne Wirkung.

### 10.2 Job-Status-Automat

```text
NEW -> HASHED -> DEDUP_CHECKED -> ANALYZED -> OCR_QUEUED -> OCR_RUNNING -> OCR_DONE
    -> CLASSIFY_RUNNING -> CLASSIFIED -> [AI_QUEUED -> AI_RUNNING -> AI_DONE | AI_FAILED]
    -> DECIDED -> MOVE_PENDING -> MOVED -> DONE

Seitenzustaende: DUPLICATE (aus DEDUP_CHECKED), REVIEW_PENDING (aus DECIDED bei 06_Sonstiges, Drive-Verschiebung nach 06 laeuft trotzdem),
                 FAILED (nach max_attempts), CANCELLED (Admin)
Chunks: OCR_QUEUED -> OCR_RUNNING -> OCR_DONE | OCR_FAILED, je Chunk eigene Zeile in ocr_chunks
```

Tabellen: `jobs` (id, document_id, object_id, step, state, attempt, locked_by, heartbeat_at, payload_json, last_error, created_at, updated_at), `ocr_chunks` (document_id, chunk_no, page_from, page_to, state, attempt, text_path). Uebergang OCR_DONE nur, wenn `SELECT COUNT(*) ... WHERE state <> 'OCR_DONE'` fuer das Dokument 0 ergibt; die Pruefung laeuft unter Zeilensperre auf dem Dokument, damit zwei gleichzeitig endende Chunks den Uebergang nicht doppelt ausloesen.

### 10.3 Queue-Semantik

Empfehlung: Celery mit Redis-Broker (aktuelle stabile Version zum Umsetzungszeitpunkt pruefen); die endgueltige Wahl der Task-Bibliothek trifft der Architekturvorschlag, die folgenden Eigenschaften sind Pflicht:

- `acks_late = true` und `reject_on_worker_lost = true`: ein Auftrag gilt erst nach erfolgreichem Abschluss als erledigt; stirbt der Worker, wird er erneut zugestellt.
- `visibility_timeout` groesser als die laengste erwartete Laufzeit eines OCR-Chunks (ANNAHME: 1 Stunde bei K = 20 und 4 s je Seite ergeben rund 80 s Regelfall, das Timeout laesst grosszuegig Luft), damit laufende Auftraege nicht vorzeitig doppelt zugestellt werden.
- Sperre in der DB: `UPDATE jobs SET state = 'RUNNING', locked_by = :worker, heartbeat_at = NOW() WHERE id = :id AND state = 'QUEUED'`; nur bei einer betroffenen Zeile arbeitet der Worker. Eine doppelt zugestellte Nachricht findet den Job bereits in RUNNING oder DONE vor und endet.
- Heartbeat: laufende Jobs aktualisieren `heartbeat_at` je Seite bzw. alle 30 s.
- `worker_prefetch_multiplier = 1`, damit lange OCR-Jobs nicht in einem einzelnen Prozess gestapelt werden.

### 10.4 Sweeper

Periodischer Beat-Task (jede Minute) und einmalig beim Start jedes Worker-Containers:

1. Jobs in RUNNING-Zustaenden mit `heartbeat_at` aelter als T (ANNAHME: 10 min fuer OCR-Chunks, 3 min fuer Klassifikation, 5 min fuer KI und Drive) auf den letzten stabilen Zustand zuruecksetzen, `attempt + 1`, erneut einreihen.
2. `attempt > max_attempts` (ANNAHME: 3): FAILED, Review-Eintrag mit Fehlertext, Statusseite zeigt den Fall.
3. Dokumente in MOVE_PENDING oder MOVED: Drive-Elternordner lesen; liegt die Datei bereits im Ziel, direkt DONE; sonst Verschiebung wiederholen.
4. Verwaiste `work/<sha256>`-Verzeichnisse ohne Job in einem aktiven Zustand nach Ablauf einer Frist loeschen (konfigurierbar).

Nach `docker compose up -d` oder Serverneustart (`restart: unless-stopped`, CR Abschnitt 14) laeuft der Sweeper sofort, alle unterbrochenen Jobs werden ohne manuelle Aktion fortgesetzt.

### 10.5 Ausschluss der Doppelverarbeitung

- Eindeutiger Schluessel `documents (object_id, sha256)`. Ein zweiter Ingest derselben Bytes im selben Objekt endet in Schritt 3 als Dublette, ohne OCR.
- Wiederholte Laeufe des Ordnerabgleichs (CR Abschnitt 9.6) erzeugen fuer bekannte `drive_file_id` und gleichen Hash keine neuen Jobs; ein veraenderter Hash bei gleicher `drive_file_id` (Datei in Drive ueberschrieben) erzeugt eine neue Dokumentversion mit Verweis auf die alte.
- Re-Klassifikation (neues Modell, neue Regeln) liest den OCR-Cache und laeuft ohne erneute OCR; sie erzeugt eine neue Entscheidungszeile in `document_decisions` und veraendert die Ablage nur, wenn das Dokument nicht bereits durch einen Menschen bestaetigt wurde.

### 10.6 Drive-Verschiebung als letzter Schritt

1. DECIDED: Zielordner-ID ermitteln (Cache, sonst anlegen), in `documents.target_folder_id` schreiben, Zustand MOVE_PENDING. Diese Zeile ist die Zustandsmarke.
2. Verschiebung ausfuehren (Drive: Elternordner tauschen; Upload: resumable Upload, Upload-Session-URI in `payload_json` fuer Wiederaufnahme).
3. MOVED: Elternordner der Datei zurueckelesen und mit `target_folder_id` vergleichen; bei Uebereinstimmung DONE, Transitdatei loeschen, `work/` aufraeumen.
4. Bei Fehlern (Quota, 5xx): Exponential Backoff (CR Abschnitt 13), Zustand bleibt MOVE_PENDING, kein Zurueckrollen der Klassifikation.

Damit ist nach jedem Abbruch eindeutig, ob die Datei schon verschoben wurde: Der Elternordner in Drive ist die Wahrheit, `target_folder_id` das Soll.

## 11. Offene Fragen an den Auftraggeber

1. Mieterdokumente in reiner WEG-Verwaltung: Zuordnung in die Eigentuemerakte des Sondereigentuemers unter 08_Korrespondenz (mit Review) oder immer 06_Sonstiges/02_Manuelle_Pruefung?
2. Vollstaendige IBAN: darf sie ueberhaupt gespeichert werden (verschluesselt), oder reichen die letzten 4 Stellen? Gibt es einen Prozess (SEPA-Einzug aus der Anwendung), der die vollstaendige IBAN braucht?
3. Kostenlimit je Objekt fuer externe KI in EUR und gewuenschtes Verhalten nach Erreichen (nur Review oder automatische Freigabe eines zweiten Budgets durch Admin).
4. Zielwert fuer den Anteil spaeter korrigierter automatischer Ablagen (Vorschlag 2 Prozent), an dem die Schwellwerte kalibriert werden.
5. Sollen OCR-Ergebnisse (durchsuchbares PDF mit Textebene) als neue Version der Datei in Drive hochgeladen werden, oder bleibt das Original unveraendert und der Text nur in der Anwendung (Empfehlung: Original unveraendert lassen)?
6. Physik der Ablage bei mehrdeutiger Zuordnung: Verbleib in 06_Sonstiges/02_Manuelle_Pruefung bis zur Review-Entscheidung (Vorschlag) oder sofortige Ablage in `Unzugeordnet` bzw. `Unbekannte_WE_Nachname` nach CR Abschnitt 4?
7. `tessdata_fast` als Standard, wenn die gemessene Fehlerrate akzeptabel ist, oder immer Standardmodell mit mehr Laufzeit?
8. Nachklassifikationslauf bei KI-Ausfall: automatisch zeitgesteuert (z. B. stuendlich) oder nur manuell durch den Sachbearbeiter?
9. Stichprobenpruefung von KI-Entscheidungen (Vorschlag 10 Prozent) gewuenscht, und wer prueft?

## 12. Liste der Annahmen

| Nr | Annahme | Verifikation |
|---|---|---|
| A01 | Dateigroessenlimit fuer Download 500 MB | Plattenplatz `df -h` auf dem VPS |
| A02 | Chunkgroesse K = 20 Seiten | Skalierungsbenchmark 9.3 Schritt 3 |
| A03 | Digitalkriterium N = 50 Zeichen, 60 Prozent alphanumerisch | Stichprobe Deckblaetter und Alt-OCR-Seiten der ersten Objekte |
| A04 | Zwei-Phasen-OCR mit M = 3 Kopfseiten | Trefferquote der Klassifikation auf Kopfseiten gegen Volltext im Testkatalog |
| A05 | Fuzzy-Schwellwerte 90 automatisch, 78 bis 89 Kandidat | Review-Entscheidungen der ersten zwei Objekte |
| A06 | TF-IDF-Klassifikator unter 10 ms und unter 300 MB | Benchmark im Worker-Image |
| A07 | Embedding-Klassifikator 50 bis 300 ms, 0,5 bis 1,5 GB | Benchmark im optionalen Container `classifier` |
| A08 | Zielwert Makro-F1 0,85 fuer Umstieg auf Embedding | Metriken in `classifier_models` nach drittem Objekt |
| A09 | Eingabe Klassifikator 4.000 Zeichen | Vergleich Trefferquote 2.000 / 4.000 / 8.000 Zeichen im Testkatalog |
| A10 | OCR-Stoerung fuer synthetische Beispiele 1 bis 3 Prozent | gemessene Zeichenfehlerrate aus 9.3 Schritt 2 |
| A11 | Scharfschaltung Klassifikator ab 15 gewichteten Beispielen je Klasse | Lernkurve auf Review-Labels |
| A12 | Nachtraining nach 50 neuen Review-Labels, Toleranz Makro-F1 0,01 | Metrikvergleich je Trainingslauf |
| A13 | KI-Timeout 30 s, 2 Versuche, Gesamtbudget 120 s, Concurrency 3 | `ai_calls.latency_ms` der ersten 200 Aufrufe |
| A14 | Tokenbudget 3.000, Umrechnung 3,5 Zeichen je Token | Rueckgemeldete Tokenzahlen der Anbieter |
| A15 | Schwellwerte t_auto 0,85, t_ai_call 0,85, t_ai_override 0,90, p_widerspruch 0,90, bonus 0,05, malus 0,25, abstandsfaktor 0,5, Stichprobe 10 Prozent | Kalibrierung an Korrekturanteil (7.2) |
| A16 | s_scan Standard 4,0 s, Fast 2,0 s, s_digital 0,1 s | Benchmark 9.3 Schritt 2 |
| A17 | Anteil Digital-PDF 40 Prozent, 8 Seiten je Dokument | Seitenanalyse der ersten zwei realen Objekte |
| A18 | Stufe-3-Anteil 20 Prozent, 8 s je Aufruf; Drive 1 s je Datei | `ai_calls`, Adapter-Metrik |
| A19 | RAM je OCR-Prozess 0,8 GB | `docker stats`, cgroup `memory.peak` |
| A20 | `tessdata_fast` etwa Faktor 2 | Benchmark 9.3 Schritt 2 |
| A21 | visibility_timeout 1 Stunde; Sweeper-Fristen 10 / 3 / 5 min; max_attempts 3 | Laufzeitverteilung der Jobs aus `jobs` |

## Anhang: Bezeichner E zu D (Beschluss B-01, M2)

Dieses Arbeitspapier verwendet an einigen Stellen eigene Bezeichner. Verbindlich sind die Bezeichner aus Fachentwurf D mit Ergänzungsmigration (docs/architektur/bezeichnerregister.md). Zuordnung:

| Bezeichner in E | Verbindlich (D, Register) |
|---|---|
| `jobs` | `processing_jobs` |
| `ocr_chunks` | `processing_jobs` mit `job_type = ocr_chunk`, Blocknummer in `payload.chunk_no` |
| `document_segments` | `document_owner_links` mit `link_kind = page_range` (analog `document_tenant_links`) |
| `document_decisions` | `document_classifications` (`is_final = 1` fuer die Entscheidung) |
| `document_pages.text_masked` | `document_pages.text_content` (immer maskiert) |
| `ocr_mean_confidence` | `document_pages.ocr_confidence` |
| `documents.target_folder_id` | `documents.target_drive_node_id` |
| `access_log` | `audit_events` (document.view, document.download) und `iban_access_log` |
| `iban_masked` | `iban_last4` plus `iban_hash`; Anzeigeform wird gebildet |
| `ai_calls.latency_ms` | `ai_calls.duration_ms` |
| `ai_calls.masked_prompt_sha256` | `ai_calls.prompt_hash` |
| `prompt_tokens`, `completion_tokens` | `tokens_in`, `tokens_out` |
| `response_json` | `response_summary` |
| `payload_json` | `processing_jobs.payload` |
| Zustandsautomat NEW bis DONE | `documents.status` (registered, hashed, ocr_done, classified, filed, review, duplicate, moved_out, error) und `processing_jobs.status` (pending, running, done, failed, skipped) |
| `threshold_ai_call` | `classification.threshold_stage3_call` |
| `WORKER_OCR_PROCESSES` | `OCR_PROCESSES` |
| `WORKER_MEM_LIMIT` | `WORKER_MEM` |
| `classification_rules`, `classifier_models`, `training_samples` | gleichnamige Tabellen der Ergaenzungsmigration (docs/architektur/datenmodell.md Abschnitt 2) |
| Queues `cpu`, `drive-io` | `ocr`, `classify`, `ai`, `io`, `lists` (B-13) |
| Ordnernamen in Regeln | Codes `category_code`, `subfolder_code`, `document_type_code` (B-12) |
