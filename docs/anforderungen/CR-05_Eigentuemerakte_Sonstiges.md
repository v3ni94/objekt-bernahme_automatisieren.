# Change Request CR-05: Einführung 05_Eigentümerakte und Umstellung auf 06_Sonstiges

Stand: 10.09.2026. Dieses Dokument ist die unveränderte Anforderungsquelle des Auftraggebers. Änderungen nur über einen neuen Change Request.

## 0. Arbeitsweise (verbindlich, vor jeder Codeänderung)

1. Es gibt keinen Bestandscode. Erstelle zuerst einen Architekturvorschlag: Technologie-Stack (Empfehlung mit Begründung), Container-Aufteilung, Datenmodell, Ordnerstruktur des Repositories, Reihenfolge der Umsetzung in Meilensteinen. Falls dir doch ein Repository oder Vorarbeiten übergeben werden, analysiere diese zuerst und liste alle Stellen, die von einer Fünf-Ordner-Struktur oder `05_Sonstiges` ausgehen.
2. Lege vor der Umsetzung einen Umsetzungsplan vor: betroffene Module, Reihenfolge, Risiken, offene Fragen. Warte auf Freigabe.
3. Erfinde keine Annahmen über den bestehenden Code. Wenn etwas unklar ist (z. B. wie Einheiten heute modelliert sind, ob eine Eigentümerliste bereits geparst wird), frage nach oder markiere es ausdrücklich als Annahme im Plan.
4. Arbeite in kleinen, einzeln testbaren Schritten. Nach jedem Schritt: Tests laufen lassen, Ergebnis berichten.
5. Kein Datenverlust. Jede Migration ist reversibel oder hat einen dokumentierten Rollback.
6. Sprache in Code-Kommentaren und Doku: Deutsch für Fachbegriffe, Englisch für Bezeichner ist erlaubt. Keine Gedankenstriche in deutschen Texten.

## 0.1 Zielumgebung (verbindlich)

| Merkmal | Vorgabe |
|---|---|
| Server | IONOS VPS, Tarif KVM 8 |
| IP-Adresse | 187.124.23.80 |
| Betriebssystem | Ubuntu 24.04 LTS |
| Container | Docker mit Docker Compose |
| Reverse Proxy | Traefik (bereits vorhanden), TLS über Let's Encrypt |
| Domain | `uebernahme.muellerhv.de` (DNS-A-Record auf 187.124.23.80 wird vom Auftraggeber gesetzt) |
| Google Drive | OAuth 2.0 über das technische Workspace-Konto `ablage@muellerhv.de` (wird vom Auftraggeber angelegt) |
| Externe KI | OpenAI API und Anthropic Claude API, beide über eine Provider-Abstraktion, Primär- und Fallback-Anbieter konfigurierbar |

Regeln für den Betrieb:
- Traefik-Konfiguration nicht annehmen, sondern auf dem Server auslesen: Name des externen Docker-Netzwerks, Cert-Resolver, Entrypoints (`docker network ls`, `docker inspect`, Traefik-Konfigurationsdateien). Ergebnis im Plan dokumentieren und in der Compose-Datei verwenden.
- Google-OAuth-App in der Google Cloud Console als **interne** Anwendung der Workspace-Organisation anlegen (Nutzertyp „Intern"), sonst verfällt der Refresh-Token im Testmodus nach sieben Tagen. Scope minimal (`drive.file` reicht nicht, da bestehende Ordner gelesen werden müssen; `drive` verwenden und dokumentieren). Token verschlüsselt in der DB oder als Docker Secret speichern, Refresh automatisch, Ablauf überwachen und im Statusbereich anzeigen.
- KI-Provider: einheitliches Interface (Eingabe: bereinigter Textauszug, Ausgabe: Kategorie, Unterart, Konfidenz, Begründung). Modell, Endpunkt, Region, Timeout und Kostenlimit je Provider konfigurierbar. Vor Produktivstart durch den Auftraggeber zu prüfen und im Plan als Voraussetzung zu führen: Auftragsverarbeitungsvertrag mit beiden Anbietern, verfügbare Datenresidenz (EU-Region), Opt-out aus Training. Token-Verbrauch und Kosten je Objekt protokollieren.
- Alle Komponenten laufen als Docker-Container, orchestriert über eine `docker-compose.yml` im Projekt. Keine Installation direkt auf dem Host außer Docker und Traefik.
- Ermittle zu Beginn die tatsächlichen Ressourcen des Servers (`nproc`, `free -h`, `df -h`) und dimensioniere Worker-Anzahl und Speichergrenzen danach. Keine Annahmen über vCPU und RAM treffen, sondern messen und im Plan dokumentieren.
- Traefik ist der einzige öffentlich erreichbare Dienst. Web-App, Worker, Datenbank und Queue hängen in einem internen Docker-Netzwerk und binden keine Host-Ports. Anbindung an Traefik ausschließlich über Labels; das bestehende Traefik-Setup nicht verändern, sondern das vorhandene externe Netzwerk nutzen (Name vorher prüfen).
- Datenbank: MySQL oder MariaDB als Container mit persistentem Volume. Es gibt keinen Bestandscode und keine Bestandsdaten, das Projekt startet auf dem VPS neu.
- Persistente Daten (DB, OCR-Cache, Transitdateien, Modelle) ausschließlich in benannten Volumes oder unter `/srv/objektakte/`, nie im Container.
- Ressourcen-Limits je Container in Compose setzen (CPU und Memory), damit der OCR-Worker die Web-App nicht verdrängt.
- Sicherheit: SSH nur mit Schlüssel, kein Root-Login, UFW mit Freigabe nur für 22, 80, 443. Zugangsdaten und API-Keys nur in `.env` (nicht im Repository) oder Docker Secrets. Automatische Sicherheitsupdates aktivieren.
- Backup: tägliches DB-Dump und Volume-Snapshot per Cron in ein separates Verzeichnis, Aufbewahrung konfigurierbar, Wiederherstellung dokumentiert und einmal getestet.
- Logging und Monitoring: strukturierte Logs je Container, Healthchecks in Compose, einfache Statusseite oder Log-Zusammenfassung für den Fortschritt der Objektverarbeitung.
- Deployment: Ablauf dokumentieren (git pull, docker compose build, docker compose up -d, Migrationen). Ein Rollback auf das vorherige Image muss mit einem Befehl möglich sein.

## 1. Ziel

Die Objektstruktur wird von fünf auf sechs Hauptordner erweitert. Eigentümerbezogene Unterlagen (insbesondere bei WEG-Verwaltung) erhalten eine eigene Akte und dürfen nicht mehr in Stammakte, Buchhaltung oder Sonstiges landen.

## 2. Verbindliche Hauptstruktur (Endversion)

Wurzelpfad in Google Drive (Konto `ablage@muellerhv.de`, Folder-ID wird beim Einrichten ermittelt und in der Konfiguration gespeichert):

```
Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten/
```

Darunter liegt je Objekt ein Objektordner. Jeder Objektordner beginnt mit der dreistelligen Objektnummer. Verbindliches Namensmuster für neu angelegte Ordner: `NNN Ort, Straße Hausnummer`, Beispiel:

```
623 Düsseldorf, Joachimstraße 49/
├── 01_Legitimationsunterlagen
├── 02_Stammakte
├── 03_Buchhaltung
├── 04_Mieterakte
├── 05_Eigentümerakte
└── 06_Sonstiges
```

Erkennung und Anlage der Objektordner (siehe auch Abschnitt 9):
- Die Software identifiziert einen Objektordner ausschließlich über die führende dreistellige Objektnummer, nie über den vollständigen Namen. Bestehende Objektordner werden nicht umbenannt.
- Existiert kein Ordner mit dieser Nummer, wird er neu angelegt nach dem Muster `NNN Ort, Straße Hausnummer` (Muster in der Konfiguration hinterlegt). Bestehende Ordner, die vom Muster abweichen, werden nicht umbenannt, sondern über die Nummer erkannt.
- Innerhalb des Objektordners werden die sechs Hauptordner geprüft: vorhandene bleiben unverändert, fehlende werden angelegt.

`05_Sonstiges` entfällt vollständig und wird durch `06_Sonstiges` ersetzt. Diese Struktur gilt für Code, Datenmodell, Seeding, Requirement Engine, KI-Prompts, Drive-Ablage, Tests, UI und Dokumentation.

## 3. Datenmodell Eigentümer

Kein `unit.owner_id`. Mindestens folgende Entitäten (Namen an die bestehende Konvention anpassen):

**owners**
- id, type (natural_person | legal_entity | community), first_name, last_name, company_name, salutation
- correspondence_address, delivery_address (abweichend), email, phone
- iban_masked (nur letzte 4 Stellen im Klartext, Rest verschlüsselt oder nicht gespeichert; siehe Abschnitt 10)
- created_at, updated_at

**units**
- id, object_id, unit_number (WE-Nummer), unit_type (apartment | commercial | parking | garage | underground_parking | cellar | other)
- unit_label (z. B. "WE03"), co_ownership_share (Miteigentumsanteil, nullable)
- created_at, updated_at

**owner_unit_assignments**
- id, owner_id, unit_id, valid_from, valid_to (nullable = aktuell), share (Anteil bei Mehrfacheigentum, nullable)
- source_document_id (nullable), created_at

Regeln:
- Ein Eigentümer kann mehrere Einheiten halten, eine Einheit mehrere Eigentümer (Ehegemeinschaft, Erbengemeinschaft, GbR).
- Eigentümerwechsel werden als neue Assignment-Zeile mit Zeitraum abgebildet, nie durch Überschreiben.
- Die Verknüpfung Dokument zu Eigentümerakte erfolgt über `unit_id` und `owner_id` bzw. `assignment_id`, nie über den Ordnernamen.

## 4. Eigentümerakte: Ordnerbenennung in Google Drive

Die Drive-Ordner sind eine Ansicht auf das Datenmodell, nicht dessen Quelle.

- Standard: `WE01_Nachname` (Einheit führt, dann Nachname bzw. Firmenkurzname)
- Mehrere Eigentümer derselben Einheit: `WE03_Nachname1-Nachname2` (alphabetisch, max. 3 Namen, danach `WE03_Nachname1-ua`)
- Einheit unbekannt, Eigentümer bekannt: `Unbekannte_WE_Nachname`
- Weder Einheit noch Eigentümer sicher: `Unzugeordnet`
- Benennungsregel zentral in einer Funktion, konfigurierbar, mit Unit-Tests.
- Bei Eigentümerwechsel bleibt der Ordner des Alteigentümers bestehen; neuer Ordner für den neuen Eigentümer. Drive-Folder-IDs werden in der DB gespeichert und gecacht.

Unterstruktur je Eigentümerakte (administrativ erweiterbar, als Konfiguration, nicht hartkodiert):

```
WE01_Nachname/
├── 01_Stammdaten
├── 02_Eigentumsnachweise
├── 03_SEPA
├── 04_Hausgeld
├── 05_Abrechnungen
├── 06_Wirtschaftsplaene
├── 07_Beschluesse
├── 08_Korrespondenz
├── 09_Mahnwesen
├── 10_Vollmachten
└── 11_Sonstiges
```

## 5. Dokumenttypen der Eigentümerakte

Erkenne und ordne zu (Dokumentunterart als Metadatum speichern):

| Unterordner | Dokumenttypen |
|---|---|
| 01_Stammdaten | Eigentümerstammdaten, Datenblätter, Kontaktdaten, Korrespondenz- und Zustelladressen, Bankverbindungsmitteilungen |
| 02_Eigentumsnachweise | Grundbuchauszüge, Kaufverträge (soweit übergeben), Veräußerungsanzeigen, Mitteilungen Eigentumswechsel, Übergang Nutzen und Lasten, Verwalter- und Veräußerungszustimmungen |
| 03_SEPA | Lastschriftmandate, Änderungen, Widerrufe |
| 04_Hausgeld | Sollstellungen, Anpassungen, Zahlungsvereinbarungen, Eigentümerkonten, Rückstands- und Guthabenaufstellungen |
| 05_Abrechnungen | Einzelabrechnungen, Abrechnungsspitzen, Korrekturabrechnungen (Abrechnungsjahr als Pflicht-Metadatum) |
| 06_Wirtschaftsplaene | Einzelwirtschaftspläne, Hausgeldvorschüsse (Wirtschaftsjahr als Pflicht-Metadatum) |
| 07_Beschluesse | Nur Beschlüsse oder Auszüge mit konkretem Eigentümer- oder Einheitenbezug; allgemeine Protokolle und Beschlusssammlung bleiben in 02_Stammakte |
| 08_Korrespondenz | Schriftverkehr, Beschwerden, Anfragen, Einwendungen gegen Abrechnungen, Kommunikation zu Baumaßnahmen, Beirat |
| 09_Mahnwesen | Zahlungserinnerungen, Mahnungen, Forderungsaufstellungen, anwaltliche Schreiben, Mahnverfahren, Klageunterlagen mit Eigentümerbezug |
| 10_Vollmachten | Vertretungs-, Versammlungs-, Zustellvollmachten des Eigentümers. Die Verwaltervollmacht bleibt in 01_Legitimationsunterlagen |

## 6. Abgrenzung Stammakte / Buchhaltung / Eigentümerakte

Entscheidungsregel in der Klassifikation, in dieser Reihenfolge:

1. Betrifft das Dokument das gesamte Objekt oder die gesamte Gemeinschaft (Teilungserklärung, Gemeinschaftsordnung, vollständige Eigentümerliste, Beschlusssammlung, Versammlungsprotokoll, Gebäudeversicherung, Energieausweis, Dienstleisterverträge)? → 02_Stammakte
2. Ist es ein Gesamtdokument der Buchhaltung (Gesamtjahresabrechnung, Gesamtwirtschaftsplan, Kontoauszüge, Belege)? → 03_Buchhaltung
3. Betrifft es einen oder mehrere bestimmte Eigentümer, eine einzelne Einheit oder ein konkretes Eigentümerkonto? → 05_Eigentümerakte

Gesamtdokumente mit eingebetteten Einzelteilen (z. B. Gesamtabrechnung mit allen Einzelabrechnungen): Masterdatei nach 03_Buchhaltung, Einzelabrechnungen per Seitenbereich (page ranges) relational den Eigentümerakten zuordnen. Keine physischen Duplikate in Drive erzeugen. Falls betrieblich gewünscht, physische Zweitablage nur über Admin-Konfiguration `duplicate_owner_documents_in_drive = true`.

## 7. Klassifikationslogik (dreistufig, Performance-Vorgabe)

Lastprofil: Objekte werden seriell verarbeitet, 1 bis 4 Objekte pro Tag, 1.000 bis 10.000 Seiten je Objekt. Ein Objekt mit 10.000 Seiten muss in unter 3 Stunden vollständig verarbeitet sein (OCR, Klassifikation, Ablage), ohne dass das Review Center blockiert.

Stufe 1, deterministisch (immer): Regelwerk, Dateiname, Objektadresse, Verwaltungsart, bekannte Eigentümer, Mieter, Einheiten, Vertragspartner, Dokumentdatum bzw. Zeitraum.

Stufe 2, lokal (immer): OCR-Text, Named-Entity-Erkennung (Namen, WE-Nummern, Beträge, Zeiträume, IBAN-Muster), lokales Klassifikationsmodell (Embedding- oder Textklassifikator, CPU-fähig, Millisekunden je Dokument). Kein lokales LLM als Standardpfad.

Stufe 3, externe KI (nur bei Konfidenz unter Schwellwert, Schwellwert konfigurierbar): Nur bereinigter Textauszug (max. konfigurierbare Tokenzahl), IBAN und Kontonummern vor Übermittlung maskieren, Ergebnis mit Konfidenz und Begründung speichern.

Zusätzliche Prüfungen für Eigentümerdokumente:
1. Eigentümername erkennbar?
2. Wohneinheit erkennbar?
3. Bekannte Verbindung Eigentümer zu Einheit in `owner_unit_assignments`?
4. Zeitbezug des Dokuments?
5. Wer war im Zeitraum Eigentümer? Zuordnung historisch korrekt, nicht zum aktuellen Eigentümer, nur weil dieser aktuell in der DB steht.
6. Eigentümerbezogen oder objektbezogen (Abschnitt 6)?

Bei fehlendem Zeitbezug und mehreren historischen Eigentümern: nicht raten, sondern in Review Center mit Kandidatenliste.

Pipeline-Architektur (Container in Compose): `web` (App und Review Center, hinter Traefik), `worker` (OCR und Klassifikation, eigener Container, Anzahl paralleler OCR-Prozesse konfigurierbar, Standard = gemessene CPU-Kerne minus 1), `queue` (Redis oder vergleichbar), `db` (MySQL oder MariaDB), optional `classifier` (lokales Modell als eigener Dienst). Tesseract mit Sprachpaket `deu` und `ocrmypdf` im Worker-Image. Wiederaufnahme nach Abbruch oder Container-Neustart ohne Doppelverarbeitung (Idempotenz über Datei-Hash und Job-Status in der DB). Der Worker läuft mit CPU-Limit, damit `web` unter Last antwortet.

## 8. 06_Sonstiges (Auffangbereich)

```
06_Sonstiges/
├── 01_Unklar
├── 02_Manuelle_Pruefung
├── 03_Dubletten
└── 04_Nicht_objektbezogen
```

Ablage hier nur, wenn alle drei Klassifikationsstufen keine ausreichend sichere Zuordnung liefern. Jede Ablage in 06_Sonstiges erzeugt automatisch einen Review-Center-Eintrag. Reporting: Anteil 06_Sonstiges je Objekt als KPI ausweisen; Zielwert unter 5 %.

## 9. Ordnerabgleich in Google Drive (ersetzt die Migration)

Es gibt keinen Bestandscode. In Drive existieren bereits Objektordner, teilweise mit Unterordnern. Die Software führt beim Anlegen oder Öffnen eines Objekts einen Abgleich durch:

1. Objektordner über die dreistellige Nummer im Wurzelpfad suchen. Treffer: Folder-ID speichern. Kein Treffer: Ordner nach Namensmuster anlegen. Mehrere Treffer mit derselben Nummer: nicht raten, Review-Center-Eintrag mit Auswahl.
2. Unterordner 01 bis 06 prüfen. Vorhandene bleiben unverändert, fehlende werden angelegt, Folder-IDs werden gespeichert.
3. Existiert in einem Objektordner noch ein `05_Sonstiges`: in `06_Sonstiges` umbenennen (Umbenennen, nicht kopieren, Dateien bleiben erhalten), danach `05_Eigentümerakte` anlegen. Vorher Dateianzahl erfassen, nachher vergleichen, Ergebnis protokollieren.
4. Bereits im Objektordner liegende Dateien (lose oder in Unterordnern) werden in die Klassifikations-Pipeline gegeben. Verschiebungen innerhalb des Objektordners nur nach Klassifikation mit ausreichender Konfidenz, sonst Vorschlag im Review Center. Nichts wird gelöscht.
5. Dry-Run-Modus für den Abgleich: zeigt geplante Anlagen, Umbenennungen und Verschiebungen, ohne zu schreiben.
6. Der Abgleich ist idempotent und kann jederzeit wiederholt werden.

## 10. Datenschutz und Sicherheit

- Bankverbindungen: IBAN nicht im Klartext in Volltextindex, Logs oder KI-Prompts. Speicherung nur maskiert oder verschlüsselt, Zugriff protokolliert.
- Externe KI: nur Textauszüge, keine Originaldateien, keine Bank- oder Ausweisdaten. Anbieter mit Auftragsverarbeitungsvertrag, Verarbeitung in der EU soweit verfügbar; Endpunkt und Region konfigurierbar.
- Zugriffsrechte: Eigentümerakten nur für berechtigte Rollen sichtbar; Rollenmodell prüfen und dokumentieren.
- Löschkonzept: Aufbewahrungsfristen als Metadatum vorsehen (Werte werden fachlich vorgegeben, nicht selbst festlegen).

## 11. Review Center

Zielbereich wählbar: 01 bis 06. Bei 05_Eigentümerakte zusätzlich Pflichtfelder Eigentümer (Suche mit Vorschlägen aus `owners`), Wohneinheit (aus `units`), Dokumentunterart, Jahr bzw. Zeitraum. Kandidatenliste bei mehreren historischen Eigentümern. Massenbearbeitung für gleichartige Dokumente (z. B. 40 Einzelabrechnungen eines Jahres) mit Vorschau. Jede manuelle Korrektur wird als Trainingsdatum für Stufe 2 gespeichert.

## 12. Vollständigkeitsprüfung WEG-Übernahme (Requirement Engine erweitern)

Prüfpunkte: vollständige Eigentümerliste, alle Einheiten erfasst, Eigentümer je Einheit bekannt, Eigentümerwechsel erfasst, Anschriften, Kommunikationsdaten, Eigentümerkonten, offene Hausgelder, Guthaben, Einzelabrechnungen (je Jahr im Übernahmezeitraum), Wirtschaftspläne, SEPA-Mandate (soweit verwendet), Sonderumlagen je Eigentümer, laufende Zahlungsvereinbarungen, laufende Mahnverfahren.

Fehlende Punkte fließen automatisch in die Nachforderung an die Vorverwaltung ein (bestehenden Nachforderungsgenerator erweitern, nicht neu bauen).

## 12a. Generierte Eigentümer- und Mieterlisten (PDF und Excel)

Die Software erzeugt je Objekt aus den erkannten und bestätigten Daten zwei Übersichtslisten und legt sie in Google Drive ab:

- `05_Eigentümerakte/00_Eigentuemerliste_NNN.xlsx` und `.pdf`
- `04_Mieterakte/00_Mieterliste_NNN.xlsx` und `.pdf`

Regeln:
- Genau eine Datei je Format und Objekt. Bei jedem Lauf wird die bestehende Datei über die gespeicherte Drive-File-ID aktualisiert (neue Version), nicht neu angelegt. Keine Datumsversionen im Dateinamen; Stand und Erzeugungszeitpunkt stehen im Dokumentkopf.
- Grundlage sind die Stammdatentabellen (`owners`, `units`, `owner_unit_assignments` sowie analog `tenants`, `leases`, `tenant_unit_assignments`), nicht einzelne Dokumente. Für Mieter das Datenmodell analog zu Eigentümern anlegen (mehrere Mieter je Einheit, Mieterwechsel mit Zeiträumen, historische Zuordnung).
- Nur Daten „soweit erkannt": leere Felder bleiben leer, keine Platzhalter. Je Datensatz eine Spalte Status (bestätigt / KI-Vorschlag / unvollständig) und eine Spalte Quelle (Dokument, aus dem der Wert stammt).
- Sortierung nach Einheitennummer, aktuelle Zuordnung; ehemalige Eigentümer oder Mieter in einem zweiten Tabellenblatt „Historie".

Spalten Eigentümerliste (Mindestumfang, administrativ erweiterbar):
Einheit (WE-Nummer), Einheitentyp, Anrede, Vorname, Nachname, Firma, Straße und Hausnummer, PLZ, Ort, abweichende Zustelladresse, Telefon, Mobil, E-Mail, Miteigentumsanteil, Eigentumsbeginn, Eigentumsende, Hausgeld monatlich, SEPA-Mandat vorhanden (ja/nein), IBAN nur maskiert (letzte 4 Stellen), Zuordnung mehrerer Eigentümer je Einheit, Bemerkung, Status, Quelle.

Spalten Mieterliste (Mindestumfang, administrativ erweiterbar):
Einheit, Einheitentyp, Anrede, Vorname, Nachname, Firma, Telefon, Mobil, E-Mail, Mietbeginn, Mietende, Kaltmiete, Nebenkostenvorauszahlung, Heizkostenvorauszahlung, Gesamtmiete, Kaution (Betrag, Anlageform, soweit erkannt), Staffel oder Index (ja/nein), Anzahl Personen, SEPA-Mandat vorhanden (ja/nein), IBAN nur maskiert, Bemerkung, Status, Quelle.

Excel: ein Blatt „Aktuell", ein Blatt „Historie", ein Blatt „Offene Punkte" (fehlende Pflichtdaten je Einheit, identisch mit der Vollständigkeitsprüfung aus Abschnitt 12). Formatierung als Tabelle mit Filter, Kopfzeile fixiert, Beträge als Zahl mit zwei Nachkommastellen, Daten als Datum.

PDF: DIN A4 quer, Kopf mit Objektnummer, Objektbezeichnung, Verwaltungsart, Stand (Datum und Uhrzeit), Seitenzahl. Vollständige Bankdaten erscheinen in keiner der Listen.

Die Listen werden nach jedem Verarbeitungslauf und nach jeder Bestätigung im Review Center neu erzeugt; manuell auslösbar über die Objektansicht.

## 13. Suche, Reporting, Drive

- Suche nach Eigentümer, Einheit, Zeitraum, Dokumentunterart über alle Objekte.
- Reporting: Eigentümerakte je Objekt mit Vollständigkeitsstatus, offene Review-Fälle, Anteil 06_Sonstiges.
- Drive-Adapter: Folder-IDs cachen, resumable Upload, Exponential Backoff bei Quota- und 5xx-Fehlern, Upload-Reihenfolge deterministisch, Wiederaufnahme nach Abbruch.

## 14. Tests und Akzeptanzkriterien

Unit-Tests:
- Ordnerbenennung (alle Fälle aus Abschnitt 4)
- Abgrenzungsregel Stammakte / Buchhaltung / Eigentümerakte (Abschnitt 6) mit mindestens 20 Beispieldokumenten
- Historische Zuordnung bei Eigentümerwechsel (Dokument 03/2025 bei Wechsel 01.07.2026 → Alteigentümer)
- IBAN-Maskierung vor KI-Aufruf

Integrationstests:
- Ordnerabgleich (Abschnitt 9): Testobjekt ohne Unterordner, Testobjekt mit `05_Sonstiges` und Dateien, Testobjekt mit vollständiger Struktur; jeweils Dry-Run und Ausführung, Dateianzahl vorher gleich nachher, zweiter Lauf ohne Änderungen (Idempotenz)
- Objektordner-Erkennung über dreistellige Nummer, Anlage bei Fehlen, Review-Eintrag bei doppelter Nummer
- Gesamtabrechnung mit 12 Einzelabrechnungen: eine Masterdatei in 03_Buchhaltung, 12 relationale Zuordnungen, keine Drive-Duplikate
- Pipeline-Abbruch und Wiederaufnahme ohne Doppelverarbeitung
- KI-Provider-Wechsel: Ausfall des Primäranbieters, Fallback übernimmt, Ergebnisformat identisch
- Listenerzeugung (Abschnitt 12a): nach zwei Läufen existiert je Objekt genau eine Excel- und eine PDF-Datei je Liste (Drive-Versionierung statt Neuanlage), Historie-Blatt enthält den Alteigentümer nach Wechsel, keine vollständige IBAN in der Ausgabe

Performance-Test:
- Testobjekt mit 10.000 Seiten (gemischt Scan und Digital-PDF) auf dem VPS 187.124.23.80 in unter 3 Stunden, Review Center währenddessen antwortet unter 2 Sekunden. Messwerte (Seiten pro Minute, RAM-Spitze, Speicherverbrauch) im Bericht dokumentieren.

Deployment-Test:
- `docker compose up -d` auf frischem Checkout startet alle Dienste, Healthchecks grün, App über `https://uebernahme.muellerhv.de` mit gültigem TLS erreichbar.
- Neustart des Servers: alle Container kommen automatisch hoch (`restart: unless-stopped`), laufende Jobs werden fortgesetzt.
- Backup einspielen auf leerer Instanz: Daten vollständig.
- OAuth: Token-Refresh über mindestens 8 Tage ohne erneute Anmeldung nachgewiesen.

Definition of Done:
- Anwendung läuft produktiv auf dem VPS hinter Traefik unter `uebernahme.muellerhv.de`
- Kein Vorkommen von `05_Sonstiges` im Code (Grep leer), Doku aktualisiert
- Ordnerabgleich auf allen bestehenden Objektordnern durchgeführt, Protokoll liegt vor
- Alle Tests grün, Performance-Test bestanden
- Admin-Konfiguration für Namensmuster Objektordner, Unterstruktur, Schwellwerte, Duplikat-Option und KI-Provider dokumentiert
- Kurze Bedienungsanleitung für Review Center und Objektanlage

## 15. Geklärte Rahmenbedingungen

- Kein Bestandscode, keine Bestandsdaten. Neustart auf dem VPS.
- Drive-Wurzelpfad: Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten im Konto `ablage@muellerhv.de`. Objektordner existieren bereits, teilweise mit Unterordnern.
- Domain `uebernahme.muellerhv.de`, DNS setzt der Auftraggeber.
- Traefik-Netzwerk und Cert-Resolver werden auf dem Server ausgelesen.
- KI-Anbieter: OpenAI und Anthropic, konfigurierbar.
- Technologie-Stack: keine Vorgabe. Du empfiehlst im Architekturvorschlag den Stack, der auf dem VPS mit Docker am besten trägt, mit Begründung. Naheliegend ist Python für Worker und OCR (Tesseract, ocrmypdf, Bibliotheken für Klassifikation und beide KI-SDKs); die Web-Oberfläche kann im selben Stack oder als getrenntes Frontend laufen. Entscheidung begründen, nicht voraussetzen.
- Eigentümerlisten der Vorverwaltung kommen in allen Formaten: PDF (Scan und digital), Excel, CSV, Exporte aus Verwaltungssoftware (z. B. ImmoWare, Domus). Der Parser muss alle Formate annehmen, erkannte Eigentümer und Einheiten als Vorschlag ins Review Center stellen und erst nach Bestätigung in `owners`, `units` und `owner_unit_assignments` übernehmen. Zeilen mit unsicherer Zuordnung markieren, nichts stillschweigend verwerfen.
- Nutzer: Timo Müller plus 2 bis 5 Mitarbeiter. Rollenmodell mindestens Admin (Konfiguration, Nutzerverwaltung, Löschungen) und Sachbearbeiter (Objekte anlegen, Review Center, Nachforderungen). Login mit E-Mail und Passwort plus Zwei-Faktor, optional Anmeldung über Google Workspace. Jede Aktion im Review Center wird mit Nutzer und Zeitstempel protokolliert.
- Aufbewahrungsfristen: als Konfiguration je Dokumentkategorie vorsehen, Standardwerte leer lassen und im Admin-Bereich mit Hinweis „durch Geschäftsführung und Steuerberater festzulegen" anzeigen. Keine automatische Löschung, solange ein Wert nicht gesetzt und freigegeben ist.
- OAuth-App: Du erstellst eine Schritt-für-Schritt-Anleitung für die Google Cloud Console (Projekt, Drive API aktivieren, Consent Screen intern, Client-ID Webanwendung, Redirect-URI `https://uebernahme.muellerhv.de/...`). Anlage und Freigabe erfolgen durch den Auftraggeber als Workspace-Administrator.

## 16. Offene Punkte

Alle Rahmenbedingungen sind geklärt. Sollten bei der Umsetzung neue Fragen entstehen, sammle sie im Plan und stelle sie gebündelt, statt Annahmen zu treffen.
