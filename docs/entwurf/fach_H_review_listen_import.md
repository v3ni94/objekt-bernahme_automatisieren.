# Fachentwurf H: Review Center, Listen, Vollständigkeitsprüfung, Nachforderung und Import-Parser (CR-05, Abschnitte 11, 12, 12a, 13, 15)

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Stand: 10.09.2026. Faktenbasis sind ausschließlich der CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md) und die Befundakte (docs/entwurf/befundakte.md). Tabellen- und Feldnamen folgen dem Fachentwurf D (Datenmodell), die Pipeline-Begriffe dem Fachentwurf E, die Drive-Mechanik dem Fachentwurf F. Diese Schwesterentwürfe sind Entwürfe derselben Phase, keine Fakten; wo dieser Entwurf etwas braucht, das dort fehlt, ist es als Vorschlag zur Ergänzung gekennzeichnet. Planungsgrößen, die nicht aus CR oder Befund stammen, tragen das Präfix ANNAHME und nennen die Verifikation. Bibliotheken werden ohne Versionsnummer genannt; es gilt jeweils: aktuelle LTS bzw. stabile Version zum Umsetzungszeitpunkt prüfen und im Lockfile festschreiben.

## 0. Ergebnis und Empfehlung

| Nr. | Entscheidung | Begründung (kurz) |
|---|---|---|
| 1 | Das Review Center ist eine Arbeitsliste über `review_cases` mit sieben Fallarten, einer filterbaren Listenansicht, einer dreiteiligen Detailansicht (Fall, Dokumentvorschau, Entscheidung) und einem Serienmodus mit Tastaturbedienung. Alle Lesezugriffe gehen gegen Datenbank und vorgerenderte Vorschaubilder, nie synchron gegen Drive, OCR oder KI. | Nur so ist die Vorgabe "unter 2 Sekunden auch während Verarbeitung" (CR 7 und 14) unabhängig von der Worker-Last erreichbar. |
| 2 | Massenbearbeitung arbeitet über einen vom Worker vergebenen `batch_key` (gleichartige Fälle) plus freie Mehrfachauswahl in der Liste, immer mit Vorschautabelle vor der Ausführung. Jede Zeile erzeugt eine eigene `review_decisions`-Zeile mit `is_bulk = 1`. | 40 Einzelabrechnungen eines Jahres (CR 11) werden in einem Schritt bestätigt, bleiben aber einzeln nachvollziehbar und einzeln trainierbar. |
| 3 | Requirement Engine und Nachforderungsgenerator werden neu gebaut (Befund 6.2: kein Bestand). Die Engine ist ein deklarativer Regelkatalog (Tabelle `completeness_checks`) mit einem Bewerter je Prüfpunkt; die 15 Prüfpunkte aus CR 12 sind vollständig auf Datenquellen abgebildet. Ergebnis sind `completeness_findings` je Objekt, Einheit oder Zuordnung und Jahr, Ausgabe als "Offene Punkte". | Datengetriebene Prüfpunkte lassen sich ohne Codeänderung erweitern, sobald die im CR nicht spezifizierte Basis für 01 bis 04 vorliegt. |
| 4 | Der Nachforderungsgenerator erzeugt aus den offenen Punkten einen Briefentwurf an die Vorverwaltung als DOCX und PDF im HVM-CI (ReportLab-Bausteine aus Befund 5), mit Frist als Platzhalter, Wasserzeichen "ENTWURF" und Freigabeschritt. Die Anwendung versendet nie. | CR 12 und 15 (Entwurf, Freigabe durch Geschäftsführung, Protokoll). |
| 5 | Listen (CR 12a) werden ausschließlich aus den Stammdatentabellen erzeugt: Excel mit openpyxl, PDF A4 quer mit ReportLab. Auslöser sind Verarbeitungslauf, Bestätigung im Review Center (entprellt), Import-Übernahme und manueller Knopf; Veröffentlichung über die gespeicherte Drive-File-ID nach Fachentwurf F. | openpyxl liest auch die Import-Dateien (eine Bibliothek für beide Richtungen); ReportLab, weil die CI-Bausteine bereits darin vorliegen und keine Browser-Engine ins Image kommt. |
| 6 | Der Import-Parser ist eine Kette aus Formatprofil, Tabellenextraktion, Spaltenzuordnung mit Vorschlag, Feldnormalisierung, Namens-Splitter, Einheitennormalisierung und unscharfem Abgleich. Ergebnis sind ausschließlich Vorschläge in `import_rows` und `review_cases`; Übernahme in `owners`, `units`, `owner_unit_assignments` (bzw. Mietertabellen) erst nach Bestätigung, nichts wird verworfen. | CR 15 wortgetreu; der Befund zeigt, dass Namensfeld und Einheitenbezeichnung der Vorverwaltungen Freitext sind. |
| 7 | Suche über relationale Filter (Eigentümer, Einheit, Zeitraum, Unterart, Objekt) plus InnoDB-Volltext auf `document_pages.text_content`. Kein eigener Suchdienst in der ersten Ausbaustufe; Ausbaupfad mit Kriterien definiert. | Bei 3 bis 6 Nutzern und überwiegend metadatengetriebener Suche rechtfertigt ein weiterer Container mit Synchronisationsproblem den Aufwand nicht. |
| 8 | Drei KPI je Objekt: Anteil 06_Sonstiges (brutto nach CR, zusätzlich bereinigt), Vollständigkeitsstatus (Anteil erfüllter anwendbarer Prüfpunkte), offene Review-Fälle mit Alter und Trefferquote des Systems. | CR 8 und 13; die Trefferquote steuert die Schwellwertkalibrierung aus Fachentwurf E. |

## 1. Geltungsbereich und Schnittstellen

| Baustein dieses Entwurfs | Nutzt aus D (Datenmodell) | Nutzt aus E (Pipeline) | Nutzt aus F (Drive) |
|---|---|---|---|
| Review Center | `review_cases`, `review_decisions`, `audit_events`, `document_classifications` (Stufe 4), `document_owner_links`, `document_tenant_links`, `document_pages`, `owners`, `units`, `owner_unit_assignments`, `document_types` | Konfidenzmodell, Kandidatenbildung, Segmentierung von Gesamtdokumenten, Nachtraining aus Review-Labels | Verschiebung nach Entscheidung als Job, Folder-ID-Cache |
| Requirement Engine | `completeness_findings`, `objects.takeover_from`, `objects.takeover_to`, `objects.management_type`, Stammdaten, `document_owner_links`, `import_batches` | keine | keine |
| Nachforderungsgenerator | `completeness_findings`, `objects.previous_manager_name`, `users` | keine | optionaler Upload des freigegebenen Schreibens |
| Listen | `owners`, `units`, `owner_unit_assignments`, `owner_files`, `tenants`, `leases`, `tenant_unit_assignments`, `field_provenance`, `completeness_findings`, `list_generations` | Auslöser nach Lauf | `publish_list` (File-ID, Versionierung, Entprellung) |
| Import-Parser | `import_batches`, `import_rows`, Stammdaten, `field_provenance`, `review_cases` | OCR-Ergebnis je Seite, unscharfer Abgleich (E 3.2), Maskierung (E 5) | Quelldatei liegt als Dokument in Drive |
| Suche, Reporting | alle Indizes aus D, `ai_calls` | keine | keine |

Ergänzungen am Datenmodell, die dieser Entwurf vorschlägt, stehen gesammelt in Abschnitt 9.

## 2. Review Center (CR 11)

### 2.1 Fallarten

Die Fallarten sind auf `review_cases.case_type` und `case_subtype` aus Fachentwurf D abgebildet. Zwei Fallarten der Aufgabenstellung fehlen dort und werden als Erweiterung vorgeschlagen (`missing_metadata` als neuer `case_type`; "Ablage in 06_Sonstiges" als `unclear` mit gesetztem `misc_subfolder_id`, so wie D es bereits vorsieht).

| Nr. | Fallart | `case_type` / `case_subtype` | Auslöser | Was der Bearbeiter entscheidet | Ergebnis bei Bestätigung |
|---|---|---|---|---|---|
| F1 | Unklare Klassifikation | `unclear` / `low_confidence`, `stage_conflict` | Konfidenz nach Stufe 3 unter Schwellwert (E 7) oder Widerspruch zwischen Stufen | Zielbereich 01 bis 06, bei 05 die Pflichtfelder, bei 04 analog Mieter | `document_classifications` Stufe 4 mit `is_final = 1`, Verschiebungsjob, Trainingsdatum |
| F2 | Kandidatenliste historische Eigentümer | `owner_candidates` / `no_time_reference`, `multiple_in_period`, `no_assignment_in_period` | Dokument mit Eigentümerbezug, aber kein Zeitbezug oder mehrere Eigentümer im Zeitraum (CR 7, Zusatzprüfung 5) | Auswahl eines Kandidaten (Zuordnung mit Zeitraum) oder Anlage einer neuen Zuordnung | `document_owner_links` mit `assignment_id`, Status `confirmed` |
| F3 | Doppelte Objektnummer | `duplicate_object_number` | Ordnerabgleich findet mehrere Ordner mit derselben Nummer (CR 9.1) | Auswahl des gültigen Ordners; die anderen bleiben unverändert | `objects.drive_root_folder_id` gesetzt, Abgleich wird fortgesetzt |
| F4 | Verschiebungsvorschlag | `move_proposal` / `existing_file_reclassified`, `legacy_folder` | Bestandsdatei im Objektordner mit Klassifikation unterhalb der Verschiebeschwelle (CR 9.4) | Vorschlag annehmen, anderes Ziel wählen oder Datei belassen | Verschiebungsjob oder Vermerk "belassen" |
| F5 | Ablage in 06_Sonstiges | `unclear` mit `misc_subfolder_id` / `misc_01_unklar`, `misc_02_manuelle_pruefung`, `misc_03_dubletten`, `misc_04_nicht_objektbezogen` | Jede Ablage in 06_Sonstiges (CR 8) | Endgültiges Ziel oder Bestätigung des Verbleibs (z. B. tatsächlich nicht objektbezogen) | Verschiebung aus 06 heraus oder Statuswechsel auf `resolved` mit Verbleib |
| F6 | Import-Zeile unsicher | `import_row_uncertain` / `name_split_ambiguous`, `unit_label_unknown`, `owner_match_ambiguous`, `date_unparseable`, `owner_conflict` | Parser vergibt Zeilenkonfidenz unter Schwellwert (Abschnitt 6.7) | Korrektur der Felder, Auswahl bestehender Eigentümer, Zeile ablehnen | `import_rows.status = confirmed`, Übernahme in Stammdaten |
| F7 | Fehlende Pflichtmetadaten | `missing_metadata` (Vorschlag, neu) / `period_year`, `document_type`, `unit`, `owner` | Klassifikation sicher, aber ein Pflichtmetadatum der Unterart fehlt (`document_types.requires_period = 1`, `requires_owner = 1`) | Eintrag des fehlenden Werts, sonst nichts | Metadatum gesetzt, Ablage wird abgeschlossen |

Weitere Fälle aus Fachentwurf F (mehrere Listen-Dateien mit Sollnamen, gleichzeitiges Vorkommen von Alt- und Neuordner Sonstiges, Treffer nur im Papierkorb) laufen über dieselbe Mechanik als `case_type = duplicate_object_number` bzw. `move_proposal` mit eigenem `case_subtype`.

Regeln für die Fallerzeugung:

1. Ein Dokument hat zu jedem Zeitpunkt höchstens einen offenen Fall je Seitenbereich. Erzeugt die Pipeline einen zweiten Grund, wird der bestehende Fall um den Grund ergänzt (`context.reasons[]`), kein zweiter Fall.
2. Priorität (`priority`, kleiner ist dringender): F3 und F4 mit Legacy-Bezug vor F2 und F7 vor F1 und F5 vor F6. Innerhalb der Stufe nach Alter. Begründung: F3 blockiert den Abgleich eines Objekts, F7 blockiert eine ansonsten fertige Ablage.
3. Der Fall speichert den Systemvorschlag vollständig in `proposed_action` und `candidates`, damit die Entscheidung "bestätigen" ohne Rückfrage an Pipeline oder KI ausgeführt werden kann.

### 2.2 Listenansicht

Die Liste ist die Arbeitsfläche für die Serienarbeit. Sie liest ausschließlich `review_cases` mit den Indizes `ix_review_queue`, `ix_review_object_status` und `ix_review_batch` aus D; alle Anzeigewerte, die sonst Joins erfordern (Dateiname, Kurzbegründung, vorgeschlagenes Ziel), stehen bereits in `context` und `proposed_action`.

| Spalte | Quelle | Bemerkung |
|---|---|---|
| Objekt | `objects.object_number`, `objects.name` | Sortierbar, Filter |
| Fallart | `case_type`, `case_subtype` | Anzeigetext aus Katalog |
| Dokument | `context.file_name`, `page_from`, `page_to` | Bei Seitenbereich "S. 12 bis 15" |
| Vorschlag | `proposed_action` (Kategorie, Unterordner, Unterart, Jahr, Akte) | Als Pfadtext, z. B. "05 / 05_Abrechnungen / Einzelabrechnung / 2025 / WE03_Mustermann" |
| Konfidenz | `context.confidence` | Balken und Zahl, farbcodiert in den drei Bändern aus E 7 |
| Kandidaten | Anzahl aus `candidates` | Nur bei F2 |
| Gruppe | `batch_key` | Anzahl gleichartiger Fälle als Kennzeichen, Klick öffnet die Massenbearbeitung |
| Alter | `created_at` | In Tagen |
| Status, Bearbeiter | `status`, `assigned_to` | Eigene Fälle hervorgehoben |

Filter: Objekt (Mehrfachauswahl), Fallart, Unterfall, Status (Standard: offen und in Bearbeitung), Bearbeiter (Standard: alle), Zielbereich des Vorschlags 01 bis 06, Konfidenzband, Zeitraum der Erzeugung, Jahr des Vorschlags, nur Fälle mit Gruppe, Volltext über Dateiname und Kurzbegründung. Filter werden je Nutzer als benannte Sichten gespeichert (Tabelle `review_saved_filters`, Vorschlag; alternativ `users.preferences` als JSON). Sortierung standardmäßig nach `priority`, dann `created_at`. Seitenweise Ausgabe serverseitig (ANNAHME: 50 Zeilen je Seite, Verifikation: Antwortzeit der Listenabfrage im Performance-Test). Zähler je Fallart und je Objekt kommen aus einer gruppierten Abfrage über denselben Index, nicht aus Einzelabfragen.

### 2.3 Detailansicht

Bildschirmaufbau in drei Spalten (auf schmalen Bildschirmen untereinander, Reihenfolge Vorschau, Entscheidung, Fall):

Linke Spalte, Fall: Fallart und Grund im Klartext, Objekt mit Verwaltungsart, Dateiname und Ablageort in Drive (Pfadtext, Link auf die Datei), Klassifikationsverlauf je Stufe (Stufe, Ergebnis, Konfidenz, Begründung, Modell) aus `document_classifications`, erkannte Entitäten aus `document_entities` (Namen, Einheiten, Zeiträume, Beträge; IBAN nur als maskierte Form), bei F2 die Kandidatenliste mit Eigentümer, Einheit, Zeitraum der Zuordnung, Score und Begründung, bei F6 die Rohzeile und die erkannten Felder. Darunter das Protokoll des Falls (Zuweisungen, frühere Entscheidungen, Wiedereröffnungen).

Mittlere Spalte, Dokumentvorschau: Seitenbilder als Streifen (Miniaturen) und Hauptansicht, Blättern mit Tastatur, Zoom, Umschalter auf den Textauszug der Seite (`document_pages.text_content`, also bereits maskiert), Anzeige der OCR-Konfidenz je Seite. Der Seitenbereich des Falls ist hervorgehoben; im Modus "Aufteilen" setzt der Bearbeiter Trenner zwischen Seiten, jedes Segment erhält einen eigenen Eintrag in der Entscheidungsspalte. Treffer der Entitätenerkennung sind im Textauszug markiert.

Rechte Spalte, Entscheidung: Zielbereich als Auswahl 01 bis 06 (Tasten 1 bis 6). Je Zielbereich blendet das Formular die passenden Felder ein:

| Zielbereich | Felder | Pflicht |
|---|---|---|
| 01, 02, 03 | Unterordner (falls für die Kategorie im Katalog vorhanden), Unterart, Jahr | Unterart, sofern Katalog für die Kategorie befüllt ist; Jahr nach `requires_period` |
| 04_Mieterakte | Mieter (Suche mit Vorschlägen aus `tenants`), Einheit (aus `units` des Objekts), Unterart, Jahr bzw. Zeitraum | Mieter, Einheit; Unterstruktur im CR nicht spezifiziert (Befund 6.3) |
| 05_Eigentümerakte | Eigentümer (Suche mit Vorschlägen aus `owners`), Einheit (aus `units` des Objekts), Zuordnung (aus `owner_unit_assignments`, wird aus Eigentümer und Einheit vorbelegt), Unterordner 01 bis 11, Unterart, Jahr bzw. Zeitraum | Eigentümer, Einheit, Unterart; Jahr bei `requires_period = 1` (CR 5: Abrechnungen und Wirtschaftspläne) |
| 06_Sonstiges | Unterordner 01 bis 04, Begründung (Freitext) | Unterordner |

Eigentümersuche: Eingabe ab zwei Zeichen, Vorschläge aus `owners.search_name` zuerst für Eigentümer mit Zuordnung im aktuellen Objekt, danach objektübergreifend (gekennzeichnet). Jeder Vorschlag zeigt Einheiten und Gültigkeitszeiträume. Wählt der Bearbeiter einen Eigentümer, der im erkannten Zeitraum des Dokuments laut `owner_unit_assignments` nicht Eigentümer der gewählten Einheit war, erscheint eine Warnung mit dem Eigentümer, der es laut Datenbank war (CR 7, Zusatzprüfung 5). Die Warnung blockiert nicht; sie wird mit der Entscheidung protokolliert. Fehlt eine passende Zuordnung, bietet das Formular "Neue Zuordnung anlegen" mit Eigentumsbeginn und optional Eigentumsende; die Zuordnung entsteht mit `data_status = confirmed`, `source_document_id` = Dokument, `confirmed_by` = Bearbeiter.

Einheitenauswahl: Liste der aktiven Einheiten des Objekts, sortiert nach `unit_number`, mit `unit_label` und Typ; Schnellsuche über Ziffern. Eine Einheit, die nicht existiert, wird nicht im Review Center angelegt (Stammdatenpflege bleibt im Import oder in der Objektansicht), stattdessen Zielakte `Unbekannte_WE_Nachname` nach CR 4.

Jahr bzw. Zeitraum: Jahr als vierstellige Zahl mit Vorschlag aus `document_entities` (erkannter Zeitraum), alternativ Zeitraum von bis. Bei `requires_period = 1` ist das Feld Pflicht; die Entscheidung kann ohne Wert nicht gespeichert werden.

### 2.4 Aktionen

| Aktion | Taste | `review_decisions.decision_type` | Wirkung in der Datenbank | Wirkung in Drive | Protokoll |
|---|---|---|---|---|---|
| Bestätigen | B | `confirm` | Vorschlag wird final: `document_classifications` Stufe 4, `document_owner_links.status = confirmed` bzw. `document_tenant_links`, Fall `resolved`, `system_was_correct = 1` | Verschiebungsjob in Queue (Zielordner aus Vorschlag), nie synchron | `audit_events.action = review.confirm` |
| Umklassifizieren | U | `correct` (Kategorie, Unterart, Jahr) oder `move` (nur Ordner) oder `assign_owner` / `assign_tenant` | Wie Bestätigen mit den geänderten Werten, `system_was_correct = 0`, `before_state` = Vorschlag, `after_state` = Entscheidung | Verschiebungsjob | `review.reclassify` |
| Zusammenführen als Dublette | D | `merge` | `documents.duplicate_of_document_id` (Vorschlag: Spalte ergänzen) verweist auf das Original; bestehende Verknüpfungen des Duplikats werden auf das Original übertragen, soweit dort nicht vorhanden | Verschiebung nach `06_Sonstiges/03_Dubletten`; nichts wird gelöscht (CR 9.4) | `review.merge_duplicate` |
| Aufteilen nach Seitenbereichen | S | `split` | Je Segment eine Zeile `document_owner_links` mit `link_kind = page_range`, eigene Unterart, Jahr, Zuordnung; Masterdatei behält ihre Kategorie (CR 6) | Keine Verschiebung des Masters außer Zielkorrektur; physische Auszüge nur bei `duplicate_owner_documents_in_drive = true` | `review.split` |
| Zurückstellen | Z | keine Entscheidung, Statuswechsel | `status = open` bleibt, `snoozed_until` (Vorschlag: Spalte ergänzen) gesetzt, Fall verschwindet bis dahin aus der Standardsicht; ANNAHME: Standard 7 Tage, Verifikation: Rückmeldung der Sachbearbeiter nach dem ersten Monat | keine | `review.defer` mit Grund |
| Verwerfen | V | `reject` | `status = dismissed`, Pflichtfeld Grund; Anwendung bei F3 (Ordner ist keiner der Kandidaten) und F6 (Zeile ist kein Datensatz) | keine | `review.dismiss` |
| Zuweisen | A | keine Entscheidung | `assigned_to`, `assigned_at`, `status = in_progress` | keine | `review.assign` |
| Wiedereröffnen | W | `revert` | Neue Entscheidungszeile mit `decision_type = revert`; Klassifikation Stufe 4 wird durch neue Zeile ersetzt, alte Zeile `is_final = 0` | Rückverschiebung als Job, falls bereits verschoben | `review.reopen` |

Verbindliche Regeln:

1. Jede Aktion schreibt in einer Transaktion: `review_decisions` (bei Entscheidungen), `review_cases` (Status), `audit_events` (immer, mit `user_id`, `user_email`, `occurred_at`, `before_state`, `after_state`, `request_id`). Schlägt ein Schreibvorgang fehl, wird nichts gespeichert und die Oberfläche zeigt den Fehler an.
2. Jede Entscheidung ist ein Trainingsdatum (CR 11): `review_decisions` erhält `features_snapshot` aus `document_classifications.features` der Stufen 1 und 2, `text_hashes` der betroffenen Seiten und die Label-Spalten. Das Nachtraining (E 3.5) liest ausschließlich diese Tabelle. Bestätigungen ohne Änderung zählen ebenfalls als Trainingsdatum (positives Beispiel).
3. Drive-Schreibvorgänge laufen nie in der Anfrage. Die Entscheidung legt einen Job an; der Fall zeigt "Verschiebung ausstehend", bis der Job `done` meldet. Scheitert der Job endgültig (Backoff erschöpft, F 2.5), entsteht ein `move_proposal`-Fall mit Fehlertext, die Entscheidung bleibt bestehen.
4. Nach jeder bestätigenden Entscheidung wird die Listenerzeugung des Objekts angestoßen (entprellt, Abschnitt 5.6) und die Vollständigkeitsprüfung des Objekts neu bewertet (Abschnitt 3.5).
5. Rechte: Sachbearbeiter dürfen alle Aktionen außer dem Verwerfen von F3 (Objektzuordnung) ausführen; das bleibt Admin. ANNAHME: Sachbearbeiter sehen alle Objekte; Verifikation: Rollenentscheidung des Auftraggebers (Abschnitt 11, Frage 12).

### 2.5 Massenbearbeitung gleichartiger Dokumente

Erkennung "gleichartig": Der Worker vergibt beim Anlegen eines Falls einen `batch_key` aus Objekt, Herkunft (Masterdokument bei Segmenten, sonst Verarbeitungslauf), vorgeschlagener Kategorie, Unterordner, Unterart und Jahr. Beispiel: Eine Gesamtabrechnung 2025 mit 40 Einzelabrechnungen ergibt 40 Segmente mit demselben `batch_key` (Objekt, Masterdokument, 05, 05_Abrechnungen, Einzelabrechnung, 2025); nur Eigentümer und Einheit unterscheiden sich je Segment. Fälle ohne gemeinsame Herkunft, aber mit gleichem Vorschlag (40 Einzeldateien im Buchhaltungsordner) erhalten einen `batch_key` ohne Herkunftsanteil, wenn sie im selben Lauf entstanden sind. ANNAHME: Diese Gruppierung trifft die Arbeitsweise der Sachbearbeiter; Verifikation: Anteil der Gruppen, die ohne Einzelkorrektur bestätigt werden, nach den ersten drei Objekten (Zielwert oberhalb 80 Prozent, sonst Gruppierung verfeinern).

Zusätzlich kann der Bearbeiter in der Liste beliebige Fälle derselben Fallart markieren (Leertaste, Umschalt plus Klick) und "Gemeinsam bearbeiten" wählen.

Vorschau vor der Ausführung: Tabelle mit einer Zeile je Fall und den Spalten Seitenbereich, erkannter Eigentümer, Einheit, Zielakte (`owner_files.folder_name`), Unterordner, Unterart, Jahr, Konfidenz, Hinweis. Zeilen mit Konflikt (kein Eigentümer erkannt, mehrere Kandidaten, Eigentümer im Zeitraum nicht zugeordnet, Pflichtmetadatum fehlt) sind markiert und von der Sammelaktion ausgenommen, bis sie in der Zeile selbst korrigiert sind. Sammelfelder oberhalb der Tabelle setzen einen Wert für alle Zeilen (Jahr, Unterart, Unterordner, Zielbereich); die Vorschau aktualisiert sich vor der Ausführung. Die Ausführung ist erst möglich, wenn keine markierte Zeile mehr in der Auswahl ist oder der Bearbeiter die Konfliktzeilen ausdrücklich ausschließt.

Ausführung: als Hintergrundjob mit Fortschrittsanzeige, ANNAHME: höchstens 500 Fälle je Sammelaktion, Verifikation: Laufzeit im Test mit 500 synthetischen Fällen unter 60 Sekunden. Jede Zeile erzeugt eine eigene `review_decisions`-Zeile mit `is_bulk = 1` und gemeinsamem `bulk_key`, eigene `audit_events`-Zeile, eigenen Verschiebungsjob. Scheitert eine Zeile, laufen die übrigen weiter; das Ergebnis listet Erfolge und Fehler. Die Listenerzeugung wird für die Gruppe genau einmal angestoßen (Entprellung).

Beispiel (synthetisch): Objekt 999, Gesamtabrechnung 2025, 40 Segmente. Vorschau zeigt 37 Zeilen grün (Eigentümer eindeutig, Zuordnung 2025 gültig), 2 Zeilen gelb (Eigentümerwechsel 2025, zwei Kandidaten), 1 Zeile rot (Einheit "WE 41" unbekannt). Der Bearbeiter setzt für die zwei gelben Zeilen je den Kandidaten, schließt die rote Zeile aus, bestätigt 39 Segmente in einer Aktion. Die rote Zeile bleibt als F2 offen mit Hinweis "Einheit im Stammdatenbestand anlegen oder Import prüfen".

### 2.6 Bedienkonzept

Bildschirmaufbau: Oben eine feste Leiste mit Objektwahl, Filterzeile und Zählern (offen gesamt, offen im Filter, davon in Gruppen). Darunter im Listenmodus die Tabelle, im Detailmodus die drei Spalten aus 2.3 mit schmaler Fallnavigation (Position im Filter, vorheriger, nächster). Ein Umschalter "Serienmodus" sorgt dafür, dass nach jeder Entscheidung automatisch der nächste Fall im aktuellen Filter geöffnet wird; die Tastenbelegung gilt in beiden Modi.

| Taste | Liste | Detail |
|---|---|---|
| J / K oder Pfeil ab / auf | Zeile wechseln | nächster / vorheriger Fall im Filter |
| Enter | Fall öffnen | Entscheidung speichern (wie B) |
| Leertaste | Zeile markieren | nächste Seite der Vorschau |
| Umschalt plus Leertaste | Gruppe markieren | vorherige Seite |
| 1 bis 6 | Filter Zielbereich | Zielbereich setzen |
| E | Filter Eigentümer | Fokus Eigentümersuche |
| N | Filter Einheit | Fokus Einheit |
| Y | Filter Jahr | Fokus Jahr |
| T | Filter Unterart | Fokus Unterart |
| B, U, D, S, Z, V, A, W | Sammelaktion auf Markierung (nur B, Z, A) | Aktion nach 2.4 |
| Umschalt plus Enter | Gemeinsam bearbeiten | Speichern und nächster Fall (Serienmodus erzwingen) |
| Strg plus Z | keine | letzte eigene Entscheidung zurücknehmen (erzeugt `revert`, nur bis zur Verschiebung in Drive ohne Rückverschiebung) |
| Strg plus K | Globale Suche | Globale Suche |
| ? | Tastenübersicht | Tastenübersicht |
| Esc | Markierung aufheben | zurück zur Liste |

Weitere Regeln: Die Fokusreihenfolge im Entscheidungsformular ist Zielbereich, Eigentümer, Einheit, Unterordner, Unterart, Jahr, Speichern; Tabulator folgt dieser Reihenfolge. Autovervollständigung übernimmt mit Enter, Pfeiltasten wählen den Vorschlag. Alle Aktionen haben eine sichtbare Schaltfläche mit Tastenhinweis, damit die Bedienung ohne Tastatur vollständig bleibt. Fehlermeldungen erscheinen am Feld, nicht als Dialog. Der Serienmodus zeigt nach jeder Entscheidung kurz das Ergebnis (Ziel, Akte) und lädt den nächsten Fall; der letzte Fall führt zurück in die Liste.

### 2.7 Antwortzeitziel unter 2 Sekunden

Das Ziel gilt laut CR 14 während der Verarbeitung eines 10.000-Seiten-Objekts. Maßnahmen und Messgröße:

1. Messgröße: ANNAHME: p95 der Antwortzeit der Endpunkte Liste, Detail, Vorschaubild, Eigentümersuche und Entscheidung, gemessen über die gesamte Dauer des Performance-Tests aus CR 14 mit einem Lasttest-Werkzeug und einem simulierten Bearbeiter. Verifikation: Messprotokoll im Bericht des Performance-Tests; Zielwert p95 unter 2 Sekunden, p99 unter 4 Sekunden als Warnschwelle.
2. Keine synchronen Aufrufe an Drive, OCR, Klassifikator oder KI in Anfragen des Review Centers. Alles, was länger dauert als eine Datenbankabfrage, ist ein Job mit Statusanzeige.
3. Vorschaubilder werden im Worker während der Pipeline erzeugt (Seitenrendering aus dem PDF, beim Scan aus dem Bild), unter `/srv/objektakte/previews/<document_id>/<page_no>.jpg` abgelegt und von `web` als statische Datei mit Cache-Kopfzeilen ausgeliefert. ANNAHME: 1.200 Pixel lange Kante, JPEG, im Mittel 150 KB je Seite, damit rund 1,5 GB je 10.000 Seiten; Verifikation: gemessene Dateigrößen und `df -h` im Performance-Test. Aufbewahrung konfigurierbar (`previews.retention_days_after_resolve`, ANNAHME 90 Tage), danach Löschung und Neuerzeugung auf Anforderung als Job.
4. Der Worker läuft mit CPU-Limit (CR 7, G), Vorschau- und Listenjobs laufen in eigenen Queues (`io`, `lists`) mit eigener Nebenläufigkeit, damit der OCR-Rückstau sie nicht blockiert. ANNAHME: Diese Trennung genügt; Verifikation: Wartezeit der Vorschaujobs im Performance-Test unter 5 Minuten.
5. Datenbank: Liste und Zähler nutzen ausschließlich die Indizes aus D; die Detailansicht lädt Fall, Klassifikationen, Entitäten und Kandidaten in höchstens vier Abfragen; Eigentümer- und Einheitenlisten je Objekt werden im Prozess bzw. in Redis zwischengespeichert (Invalidierung bei Stammdatenänderung). Verbindungs-Pool von `web` getrennt vom Worker dimensionieren.
6. Entscheidungen schreiben nur in `review_*`, `audit_events`, `document_classifications`, `document_*_links`; die Verschiebung ist ein Job. Damit bleibt die Schreibtransaktion kurz und blockiert keine Pipeline-Tabellen.
7. Massenbearbeitung ist ein Job mit Fortschrittsabfrage, die Oberfläche bleibt bedienbar.

### 2.8 Rollen und Protokoll

Alle Aktionen tragen Nutzer und Zeitstempel (CR 15) über `audit_events` mit `actor_type = user`. Aktionsschlüssel: `review.assign`, `review.confirm`, `review.reclassify`, `review.merge_duplicate`, `review.split`, `review.defer`, `review.dismiss`, `review.reopen`, `review.bulk_execute`, `review.filter_saved`. Systemerzeugte Fälle tragen `actor_type = worker` mit `created_by_run_id`. Die Fallhistorie in der Detailansicht ist eine Sicht auf `audit_events` (`entity_type = review_case`). Admin sieht zusätzlich eine Auswertung je Bearbeiter (Anzahl Entscheidungen, Anteil Änderungen gegenüber Vorschlag), Sachbearbeiter sehen nur eigene Zahlen.

## 3. Vollständigkeitsprüfung WEG-Übernahme (Requirement Engine, CR 12)

### 3.1 Grundsatz: Neubau als deklarativer Regelkatalog

Es existiert kein Bestandscode (Befund 1 und 6.2). Die im CR verlangte "Erweiterung" wird als Neubau mit dem Mindestumfang aus CR 12 umgesetzt. Aufbau:

1. Katalogtabelle `completeness_checks` (Vorschlag, Ergänzung zu D): `check_code` (Schlüssel wie in `completeness_findings`), `name`, `category` (Gruppe für Listen und Nachforderung), `scope_type` (object, unit, assignment), `period_mode` (none, per_year, at_takeover), `applies_to` (JSON-Liste der Verwaltungsarten `weg`, `rental`, `weg_with_se`), `evaluator` (Schlüssel des Bewerters im Code), `evidence_document_type_codes` (JSON), `severity` (blocking, standard, info), `request_text_key` (Textbaustein der Nachforderung), `sort_order`, `is_active`. Seed mit den 15 WEG-Prüfpunkten, administrativ erweiterbar.
2. Bewerter im Code: je `evaluator` eine Funktion mit Signatur `evaluate(object, check, context) -> list[Finding]`, die ausschließlich liest und Ergebnisse mit `status` in `missing`, `partial`, `fulfilled`, `not_applicable`, `evidence_document_id`, `evidence_link_id`, `details` liefert. Bewerter sind rein, ohne Seiteneffekte, und damit einzeln testbar.
3. Lauf: `evaluate_object(object_id)` lädt die anwendbaren Prüfpunkte, bildet den Zeitraum (3.3), ruft die Bewerter, schreibt die Ergebnisse per Upsert auf `completeness_findings` (Unique `position_key` aus D). Positionen, die im aktuellen Lauf nicht mehr entstehen (z. B. Zuordnung gelöscht), werden auf `not_applicable` gesetzt, nicht gelöscht. Änderungen des Status werden in `audit_events` mit `actor_type = system` protokolliert.
4. Manuelle Übersteuerung (Vorschlag, Ergänzung zu D): `completeness_findings.manual_status`, `manual_reason`, `manual_by`, `manual_at`. Der Bewerter überschreibt einen manuellen Status nicht; er trägt seinen Wert in `status` ein, die Anzeige zeigt beide. Anwendungsfall: Negativerklärung der Vorverwaltung ("keine laufenden Mahnverfahren") liegt als Schreiben vor.

### 3.2 Regelkatalog WEG (wortgetreu aus CR 12)

| Nr. | Prüfpunkt (CR 12) | `check_code` (D) | Geltung | Zeitraum | Datenquelle, die den Punkt erfüllt | Statuslogik |
|---|---|---|---|---|---|---|
| 1 | vollständige Eigentümerliste | `owner_list_complete` | Objekt | keiner | Bestätigtes Dokument der Unterart `eigentuemerliste` in 02_Stammakte (CR 6: vollständige Eigentümerliste ist Stammakte) oder `import_batches` mit `import_kind = owner_list`, `status = committed` | fulfilled bei bestätigtem Dokument oder abgeschlossenem Import; partial bei vorhandenem, aber unbestätigtem Dokument oder Import `in_review`; sonst missing |
| 2 | alle Einheiten erfasst | `all_units_known` | Objekt | keiner | Anzahl aktiver `units` gegen Sollzahl `objects.expected_unit_count` (Vorschlag, Spalte ergänzen; Quelle Teilungserklärung oder Eigentümerliste, manuell erfasst); ergänzend Summe `co_ownership_share` gegen `co_ownership_share_base`, wenn beides vorliegt | fulfilled bei Anzahl gleich Sollzahl (und MEA-Summe gleich Nenner, falls bekannt); partial, wenn Sollzahl fehlt (Hinweis "Sollzahl erfassen") oder Anzahl abweicht; missing bei null Einheiten |
| 3 | Eigentümer je Einheit bekannt | `owner_per_unit` | Einheit | Stichtag Übernahme | `owner_unit_assignments` mit `is_current = 1` für die Einheit; `data_status` der Zuordnung und des Eigentümers | fulfilled bei mindestens einer aktuellen Zuordnung mit `data_status = confirmed`; partial bei `ai_suggested` oder `incomplete`; missing ohne Zuordnung |
| 4 | Eigentümerwechsel erfasst | `owner_changes` | Einheit | Übernahmezeitraum | Zuordnungen mit `valid_from` im Zeitraum benötigen `source_document_id` mit Unterart aus 02_Eigentumsnachweise (Veräußerungsanzeige, Mitteilung Eigentumswechsel, Übergang Nutzen und Lasten, CR 5); aktuelle Zuordnung mit `valid_from IS NULL` gilt als offen | fulfilled, wenn jede Zuordnung im Zeitraum `valid_from` und Nachweisdokument hat; partial, wenn `valid_from` bekannt, Nachweis fehlt; missing, wenn `valid_from` der aktuellen Zuordnung unbekannt |
| 5 | Anschriften | `addresses` | Zuordnung (aktueller Eigentümer je Einheit) | keiner | `owners.correspondence_street`, `correspondence_postal_code`, `correspondence_city`; `field_provenance.status` dieser Felder | fulfilled bei allen drei Feldern mit Status confirmed; partial bei Teilangaben oder `ai_suggested`; missing bei leer |
| 6 | Kommunikationsdaten | `contact_data` | Zuordnung | keiner | `owners.email`, `phone`, `mobile` | ANNAHME: fulfilled bei mindestens einem Kanal (bestätigt), partial bei nur unbestätigtem Wert, missing bei keinem; Verifikation: Vorgabe des Auftraggebers (Frage 2) |
| 7 | Eigentümerkonten | `owner_accounts` | Zuordnung | Stand zum Übernahmestichtag | `document_owner_links` mit Unterart `eigentuemerkonto` (04_Hausgeld, CR 5), `document_date` oder `period_to` nahe `objects.takeover_to` | fulfilled bei bestätigter Verknüpfung; partial bei `suggested`; missing sonst |
| 8 | offene Hausgelder | `open_house_fees` | Objekt und Zuordnung | Stichtag | Unterart `rueckstands_guthabenaufstellung` (04_Hausgeld, CR 5) auf Objektebene; je Zuordnung optional strukturierter Wert `owner_unit_assignments.balance_at_takeover` (Vorschlag, Spalte ergänzen, nullable) | Objektebene fulfilled bei bestätigter Aufstellung; Zuordnungsebene fulfilled, wenn Wert erfasst oder Aufstellung die Einheit nennt (aus `document_entities`); sonst missing |
| 9 | Guthaben | `credits` | Objekt und Zuordnung | Stichtag | wie Nr. 8 (dieselbe Aufstellung enthält Rückstände und Guthaben) | wie Nr. 8; ein Dokument erfüllt beide Punkte |
| 10 | Einzelabrechnungen (je Jahr im Übernahmezeitraum) | `annual_statement_year` | Zuordnung je Jahr | je Wirtschaftsjahr Y im Zeitraum | `document_owner_links` mit Unterart `einzelabrechnung` (oder `korrekturabrechnung`), `period_year = Y`, Zuordnung überlappt Y | fulfilled bei bestätigter Verknüpfung; partial bei `suggested`; missing sonst; not_applicable, wenn Eigentümer in Y nicht zugeordnet war oder Abrechnung noch nicht fällig (3.3) |
| 11 | Wirtschaftspläne | `business_plan_year` | Zuordnung je Jahr | je Wirtschaftsjahr Y im Zeitraum einschließlich laufendes Jahr | Unterart `einzelwirtschaftsplan` oder `hausgeldvorschuss` mit `period_year = Y` | wie Nr. 10 |
| 12 | SEPA-Mandate (soweit verwendet) | `sepa_mandate` | Zuordnung | keiner | `objects.sepa_used` (Vorschlag, Spalte ergänzen: ja, nein, unbekannt); `owners.sepa_mandate_present`; Unterart `lastschriftmandat` (03_SEPA) | Objekt `sepa_used = nein`: alle not_applicable; `unbekannt`: Objektfinding missing "Klärung SEPA-Nutzung"; je Zuordnung fulfilled bei `sepa_mandate_present = 1` und bestätigtem Mandatsdokument; partial bei Kennzeichen ohne Dokument; not_applicable bei `sepa_mandate_present = 0`; missing bei NULL |
| 13 | Sonderumlagen je Eigentümer | `special_levy` | Objekt und Zuordnung | Übernahmezeitraum | `objects.special_levies_in_period` (Vorschlag, Spalte: ja, nein, unbekannt, vom Sachbearbeiter aus Beschlusssammlung gesetzt); Unterart `sonderumlage_einzel` (Vorschlag, neue Unterart unter 04_Hausgeld, im CR 5 nicht gelistet) | `nein`: not_applicable; `unbekannt`: Objektfinding missing "Klärung Sonderumlagen"; `ja`: je Zuordnung fulfilled bei bestätigtem Dokument, sonst missing |
| 14 | laufende Zahlungsvereinbarungen | `payment_agreement` | Objekt und Zuordnung | Stichtag | Unterart `zahlungsvereinbarung` (04_Hausgeld, CR 5); Objektfinding "Aufstellung oder Negativerklärung laufender Zahlungsvereinbarungen" | Objektebene fulfilled bei manueller Übersteuerung (Negativerklärung liegt vor) oder bestätigter Aufstellung; je Zuordnung fulfilled bei Dokument, sonst not_applicable (Abwesenheit ist kein Mangel je Eigentümer) |
| 15 | laufende Mahnverfahren | `dunning_procedure` | Objekt und Zuordnung | Stichtag | Unterarten aus 09_Mahnwesen (Mahnverfahren, anwaltliche Schreiben, Klageunterlagen, Forderungsaufstellungen, CR 5); Objektfinding "Aufstellung oder Negativerklärung laufender Mahnverfahren" | wie Nr. 14 |

Muster "Negativnachweis" (Nr. 12 bis 15): Das Fehlen eines Dokuments kann bedeuten, dass es den Sachverhalt nicht gibt oder dass die Vorverwaltung ihn nicht geliefert hat. Die Engine kann das nicht unterscheiden. Deshalb erhält jeder dieser Prüfpunkte ein Objektfinding, das eine Aufstellung oder Negativerklärung der Vorverwaltung verlangt und in die Nachforderung fließt, bis der Sachbearbeiter es manuell schließt (3.1, Punkt 4). Je Zuordnung wird nur positiv bewertet.

### 3.3 Zeitraumlogik

1. Übernahmezeitraum: von `objects.takeover_from` bis `objects.takeover_to` (Stichtag Verwaltungsübergang, D). Fehlt `takeover_from`, gilt ANNAHME: `completeness.default_period_years = 3` abgeschlossene Wirtschaftsjahre vor dem Stichtag zuzüglich des laufenden Jahres; Verifikation: Vorgabe der Geschäftsführung (Frage 1). Der berechnete Zeitraum wird in der Objektansicht angezeigt und kann dort überschrieben werden.
2. Wirtschaftsjahr: `objects.fiscal_year_start_month` (1 = Kalenderjahr). Ein Wirtschaftsjahr Y läuft vom Startmonat in Y bis zum Vormonat in Y+1; `period_year` bezeichnet das Startjahr. Fehlt der Wert, gilt Kalenderjahr mit Hinweis im Finding.
3. Jahresmenge: alle Wirtschaftsjahre, deren Zeitraum sich mit dem Übernahmezeitraum überschneidet. Für Wirtschaftspläne zusätzlich das auf den Stichtag folgende Wirtschaftsjahr, falls der Stichtag im letzten Quartal des Wirtschaftsjahres liegt (Plan für das Folgejahr ist dann üblicherweise beschlossen; ANNAHME, Verifikation durch Sachbearbeiter nach den ersten Objekten).
4. Anwendbarkeit je Zuordnung und Jahr: Die Zuordnung überlappt Y, wenn `valid_from` (oder unbekannt) vor dem Ende von Y liegt und `valid_to` (oder NULL) nach dem Beginn von Y. Bei Eigentümerwechsel innerhalb von Y sind beide Zuordnungen anwendbar; die Einzelabrechnung Y wird je Eigentümer erwartet (Vorverwaltungen rechnen je Eigentümer zeitanteilig oder auf den Erwerber ab; die Engine erwartet mindestens ein Dokument, das die Einheit und Y nennt, und ordnet es über den Seitenbereich der zutreffenden Zuordnung zu).
5. Fälligkeit: Die Einzelabrechnung für Y gilt als erwartbar ab einem konfigurierbaren Datum im Folgejahr. ANNAHME: `completeness.statement_expected_after = "30.06."` des Folgejahres; davor Status not_applicable mit Hinweis "noch nicht fällig". Verifikation: Vorgabe der Geschäftsführung, gegebenenfalls Rücksprache mit dem Steuerberater; kein Rechtsrat durch die Anwendung.
6. Stichtagsprüfpunkte (Nr. 3, 7, 8, 9, 14, 15) beziehen sich auf `takeover_to`. Liegt der Stichtag in der Zukunft, wird geprüft, aber im Finding "vorläufig" vermerkt.

### 3.4 Bewertung je Einheit und je Objekt

Je Einheit: Zusammenfassung aller Findings mit `scope_type` unit oder assignment (aktuelle Zuordnung) und der zugehörigen Jahresfindings. Einheitenstatus: `complete` (kein missing, kein partial), `partial` (kein missing, mindestens ein partial), `incomplete` (mindestens ein missing), `not_evaluable` (kein Eigentümer bekannt, Nr. 3 missing; dann werden Nr. 5 bis 15 für die Einheit nicht gezählt, sondern als Folgefehler gekennzeichnet, damit die Einheit nicht 13 Mängel zeigt, sondern einen).

Je Objekt: Kennzahl `completeness_ratio` = Anzahl fulfilled geteilt durch Anzahl anwendbarer Findings (fulfilled, partial, missing; not_applicable zählt nicht). Status: ANNAHME: `complete` bei 100 Prozent, `mostly_complete` ab 90 Prozent ohne blocking-Mangel, sonst `incomplete`; `not_evaluated` vor dem ersten Lauf. Verifikation: Rückmeldung der Sachbearbeiter nach drei Objekten, Schwellen als Konfiguration `completeness.mostly_complete_pct`. Zusätzlich je Objekt: Anzahl missing je Kategorie, Anzahl Einheiten je Einheitenstatus, Datum der letzten Bewertung.

Ausgabe "Offene Punkte" (Grundlage für Blatt 3 der Listen und für die Nachforderung): eine Zeile je Finding mit Status missing oder partial: Einheit (`unit_label`), Eigentümer (Nachname bzw. Firma der Zuordnung), Prüfpunkt (Name), Jahr, Status, Hinweis (aus `details`), Nachweis vorhanden (Dateiname des `evidence_document_id`, sonst leer), in Nachforderung (ja/nein aus `include_in_request`), manuelle Übersteuerung (Text). Sortierung: Einheit, Prüfpunkt, Jahr. Objektfindings stehen vor den Einheiten mit leerer Einheitenspalte.

### 3.5 Auslöser und Idempotenz

Bewertung nach jedem Verarbeitungslauf des Objekts, nach jeder bestätigenden Review-Entscheidung des Objekts (mit derselben Entprellung wie die Listen, 5.6), nach jeder Import-Übernahme, nach Stammdatenänderung in der Objektansicht, manuell über die Objektansicht und nächtlich für alle Objekte im Status `takeover`. Der Lauf ist idempotent: gleiche Daten ergeben gleiche Findings, der Upsert ändert dann nur `last_evaluated_at`. Dauer: ANNAHME unter 5 Sekunden je Objekt mit 100 Einheiten und 4 Jahren (rund 15 Prüfpunkte mal 100 mal 4 gleich 6.000 Findings, Mengenabfragen statt Einzelabfragen); Verifikation: Messung im Integrationstest mit synthetischem Objekt.

### 3.6 Minimalumfang Miete (Vorschlag, im CR nicht spezifiziert)

CR 12 nennt nur die WEG-Übernahme; CR 12a verlangt aber auch für die Mieterliste ein Blatt "Offene Punkte", das mit der Vollständigkeitsprüfung identisch sein soll. Deshalb wird ein Mietkatalog als Vorschlag angelegt, bewertet ausschließlich aus Stammdaten, weil die Unterstruktur von 04_Mieterakte im CR fehlt (Befund 6.3). Dokumentbasierte Prüfpunkte folgen, sobald diese Struktur vorliegt.

| `check_code` | Prüfpunkt | Geltung | Datenquelle | Statuslogik |
|---|---|---|---|---|
| `tenant_list_complete` | vollständige Mieterliste | Objekt | `import_batches` mit `import_kind` tenant_list oder mixed, `status = committed`; alternativ bestätigtes Dokument der Unterart Mieterliste | wie WEG Nr. 1 |
| `all_units_known` | alle Einheiten erfasst | Objekt | wie WEG Nr. 2 | wie WEG Nr. 2 |
| `tenant_per_unit` | Mieter je Einheit bekannt oder Leerstand vermerkt | Einheit | `tenant_unit_assignments` mit `is_current = 1`; Leerstand als `units.vacancy_confirmed` (Vorschlag, Spalte) | fulfilled bei bestätigter Zuordnung oder bestätigtem Leerstand; partial bei ai_suggested; missing sonst |
| `lease_terms` | Mietbeginn, Kaltmiete, Nebenkosten- und Heizkostenvorauszahlung | Zuordnung | `leases.start_date`, `base_rent`, `utilities_prepayment`, `heating_prepayment` | fulfilled bei allen vier Werten bestätigt; partial bei Teilwerten; missing bei keinem |
| `deposit` | Kaution (Betrag, Anlageform) | Zuordnung | `leases.deposit_amount`, `deposit_type` | fulfilled bei beiden Werten; partial bei einem; missing bei keinem |
| `rent_adjustment` | Staffel oder Index bekannt | Zuordnung | `leases.rent_adjustment_type` ungleich unknown | fulfilled oder missing |
| `tenant_contact_data` | Kommunikationsdaten | Zuordnung | `tenants.email`, `phone`, `mobile` | wie WEG Nr. 6 |
| `tenant_sepa_mandate` | SEPA-Mandat (soweit verwendet) | Zuordnung | `tenants.sepa_mandate_present`, `objects.sepa_used` | wie WEG Nr. 12 ohne Dokumentprüfung |
| `tenant_accounts` | Mieterkonten, offene Mieten, Guthaben | Objekt | Aufstellung der Vorverwaltung als Dokument (Unterart offen) | missing bis Negativerklärung oder Dokument, manuell schließbar |
| `tenant_dunning_procedure` | laufende Mahnverfahren | Objekt | wie WEG Nr. 15, Unterart offen | wie WEG Nr. 15 |

Verwaltungsart `weg_with_se` (Befund: "WEG mit SE-Verwaltung"): beide Kataloge gelten, der WEG-Katalog für alle Einheiten, der Mietkatalog nur für Einheiten mit Sondereigentumsverwaltung. Die Kennzeichnung je Einheit fehlt im Datenmodell (Vorschlag: `units.se_managed` TINYINT, nullable) und ist Frage 3.

### 3.7 Basis-Prüfpunkte für 01 bis 04 (nicht spezifiziert)

CR 12 enthält keine Prüfpunkte für die Hauptordner 01 bis 04. Aus CR 5 und 6 lassen sich Dokumentarten ableiten, die dort erwartet werden; sie sind hier als ANNAHME geführt und werden erst nach Antwort auf Frage 4 in den Katalog aufgenommen. Verifikation: Bestätigung des Auftraggebers oder ein Lastenheft (CR-01 bis CR-04).

| Hauptordner | Aus dem CR ableitbare Dokumentarten | Kandidaten-Prüfpunkte (ANNAHME) |
|---|---|---|
| 01_Legitimationsunterlagen | Verwaltervollmacht (CR 5, Zeile 10_Vollmachten) | Verwaltervollmacht vorhanden; Bestellungsbeschluss und Verwaltervertrag (nicht im CR genannt, fachlich naheliegend) |
| 02_Stammakte | Teilungserklärung, Gemeinschaftsordnung, vollständige Eigentümerliste, Beschlusssammlung, Versammlungsprotokoll, Gebäudeversicherung, Energieausweis, Dienstleisterverträge (CR 6.1) | je Dokumentart ein Objektprüfpunkt "vorhanden"; Versammlungsprotokolle je Jahr im Übernahmezeitraum |
| 03_Buchhaltung | Gesamtjahresabrechnung, Gesamtwirtschaftsplan, Kontoauszüge, Belege (CR 6.2) | Gesamtabrechnung je Jahr, Gesamtwirtschaftsplan je Jahr, Kontoauszüge zum Stichtag (Saldenbestätigung) |
| 04_Mieterakte | keine Angabe im CR | offen |

## 4. Nachforderungsgenerator

### 4.1 Grundsatz

Der Generator wird neu gebaut (Befund 6.2). Er erzeugt aus den Findings mit `include_in_request = 1` und Status missing oder partial ein Schreiben an die Vorverwaltung, immer als Entwurf. Die Anwendung versendet nichts, kennt keinen E-Mail-Versand und keine Postschnittstelle; der Versand erfolgt außerhalb und wird in der Anwendung nur vermerkt.

Datenhaltung (Vorschlag, Ergänzung zu D): Tabelle `document_requests` mit `object_id`, `version`, `status` (draft, reviewed, approved, marked_sent, withdrawn), `findings_snapshot` (JSON der einbezogenen Findings zum Erzeugungszeitpunkt), `deadline_date` (NULL, bis der Sachbearbeiter sie setzt), `salutation_block`, `custom_text_blocks` (JSON), `docx_path`, `pdf_path`, `content_hash`, `created_by`, `reviewed_by`, `approved_by`, `approved_at`, `marked_sent_by`, `marked_sent_at`, `sent_channel_note`, `drive_file_id_docx`, `drive_file_id_pdf`, Zeitstempel. Jede Statusänderung in `audit_events`. Dateien unter `/srv/objektakte/requests/<object_id>/<version>/`.

Adressat: `objects.previous_manager_name` (D) reicht für das Anschriftfeld nicht. Vorschlag: `objects.previous_manager_street`, `previous_manager_house_number`, `previous_manager_postal_code`, `previous_manager_city`, `previous_manager_contact_person`, `previous_manager_reference` (deren Zeichen). Fehlen Werte, bleibt das Anschriftfeld mit Platzhalter, und das Schreiben kann nicht freigegeben werden.

### 4.2 Struktur des Schreibens

Aufbau nach DIN 5008 mit den HVM-Bausteinen aus Befund 5 (`briefkopf_seite1`, `anschriftfeld`, `infoblock`, `betreff`, `unterschriftsblock`, `fusszeile`, `folgeseite`):

1. Briefkopf Seite 1: Kennlinie, Logo rechts oben, Absenderzeile Hausverwaltung Müller GmbH, Rheinpromenade 13, 40789 Monheim am Rhein.
2. Anschriftfeld: Vorverwaltung mit Ansprechpartner.
3. Infoblock: Ihr Zeichen (Referenz der Vorverwaltung), Unser Zeichen (Objektnummer und Kürzel des Sachbearbeiters), Ansprechpartner (Sachbearbeiter, Name aus `users`), Datum (Datum der Erzeugung, wird bei Freigabe auf das Freigabedatum gesetzt).
4. Betreff: "Übernahme der Verwaltung NNN Ort, Straße Hausnummer zum TT.MM.JJJJ, Nachforderung fehlender Unterlagen".
5. Einleitung: Bezug auf den Verwaltungsübergang zum Übernahmedatum (`objects.takeover_to`) und die bisher erhaltenen Unterlagen (Anzahl erfüllter Prüfpunkte wird nicht genannt, nur der Dank für das Übergebene).
6. Liste der fehlenden Unterlagen, gruppiert nach Kategorie (aus `completeness_checks.category`): Eigentümerliste und Einheiten; Eigentümerstammdaten (Anschriften, Kommunikationsdaten); Eigentümerkonten, Rückstände und Guthaben; Einzelabrechnungen je Jahr; Wirtschaftspläne je Jahr; SEPA-Mandate; Sonderumlagen; Zahlungsvereinbarungen; Mahnverfahren. Innerhalb der Gruppe je Zeile Einheit und Eigentümer (Nachname bzw. Firma), Jahr, gegebenenfalls Hinweis (z. B. "Eigentumsbeginn unbekannt"). Personenbezogene Angaben nur soweit für die Nachforderung erforderlich (Einheit und Name), keine Kontaktdaten, keine Bankdaten. Bei mehr als ANNAHME 25 Positionen wandert die Einzelaufstellung in eine Anlage (Tabelle Einheit, Eigentümer, Unterlage, Jahr) und der Brieftext nennt nur die Gruppen mit Anzahl; Verifikation: Lesbarkeit der ersten realen Schreiben.
7. Fristsatz mit Platzhalter: "Wir bitten um Übersendung bis zum [FRIST]." Der Platzhalter wird vom Sachbearbeiter im Formular gesetzt; solange er leer ist, trägt das Dokument das Wasserzeichen ENTWURF und der Status kann nicht auf reviewed wechseln.
8. Schlusssatz mit Hinweis auf Rückfragen und Grußformel.
9. Unterschriftsblock: Hausverwaltung Müller GmbH, Timo Müller, Geschäftsführer. Im Entwurf ohne Unterschriftsbild; das Unterschriftsbild aus dem CI wird erst in der freigegebenen Fassung eingebettet (Freigabe, 4.5).
10. Fußzeile mit Pflichtangaben laut Befund 5: Hausverwaltung Müller GmbH, Rheinpromenade 13, 40789 Monheim am Rhein, Amtsgericht Düsseldorf HRB 104762, Geschäftsführer Timo Müller, www.muellerhv.de. Keine Telefon-, E-Mail-, Bank- oder Steuerangaben, weil sie im Befund nicht vorliegen.
11. Hinweiszeile im Entwurf (nur solange Status draft oder reviewed): "Entwurf, Freigabe durch die Geschäftsführung erforderlich" als Wasserzeichen und als Zeile über dem Betreff. In der freigegebenen Fassung entfällt beides.

### 4.3 Textbausteine konfigurierbar

Tabelle `request_text_blocks` (Vorschlag): `key`, `title`, `body` mit Platzhaltern, `version`, `is_active`, `updated_by`, `updated_at`. Platzhalter: `{objekt_nr}`, `{objekt_bezeichnung}`, `{verwaltungsart}`, `{uebernahmedatum}`, `{vorverwaltung_name}`, `{frist}`, `{sachbearbeiter}`, `{anzahl_positionen}`. Je `check_code` ein Zeilenbaustein mit Platzhaltern `{einheit}`, `{eigentuemer}`, `{jahr}`, `{hinweis}`; Beispiel für `annual_statement_year`: "Einzelabrechnung {jahr} für {einheit} ({eigentuemer})". Bausteine werden im Admin-Bereich bearbeitet, jede Änderung ist eine neue Version mit Protokoll; ein Schreiben speichert die verwendeten Versionen im Snapshot. Die Seed-Texte sind Vorschläge und vor Produktivstart durch die Geschäftsführung freizugeben (Frage 5).

### 4.4 Erzeugung DOCX und PDF

Workflow: Der Sachbearbeiter setzt Frist, Ansprechpartner und optionale Freitexte im Formular der Anwendung. Aus denselben Daten entstehen beide Dateien; es findet keine Konvertierung von DOCX nach PDF statt.

| Format | Werkzeug | Umsetzung | Zweck |
|---|---|---|---|
| PDF | ReportLab, Portierung der HVM-Bausteine aus `scripts/hvm_briefkopf.py` (Befund 5) in ein Anwendungsmodul, Logo aus `assets/Logo_HVM.jpg` | Kennlinie, Logo, Anschriftfeld, Infoblock, Betreff, Fließtext, Aufzählungen, Anlagentabelle mit wiederholtem Kopf, Folgeseitenkopf, Fußzeile; Wasserzeichen ENTWURF als halbtransparenter Text im Hintergrund | Ansicht, Ablage, Ausdruck |
| DOCX | python-docx | Vorlage `nachforderung_hvm.docx` mit Kopf- und Fußzeile im HVM-CI (Kennlinie als Bild, Logo, Pflichtangaben), Absatzformate für Betreff und Fließtext, Tabelle für die Anlage; Inhalte werden in die Vorlage geschrieben | Nachbearbeitung durch den Sachbearbeiter außerhalb der Anwendung |

Die Word-Vorlage existiert laut Befund nicht; sie wird aus den CI-Werten (Farben, Schrift Helvetica/Arial 10 bis 11 pt, Logo) erstellt und vom Auftraggeber freigegeben (Frage 5). Wird die DOCX-Fassung außerhalb der Anwendung verändert, ist die veränderte Fassung nicht mehr die Fassung der Anwendung; die Anwendung bietet dafür "Externe Fassung hochladen", die als Anhang zum `document_requests`-Datensatz gespeichert wird.

Ablage in Drive: Der CR nennt keinen Zielordner für Nachforderungen. Vorschlag: Upload der freigegebenen Fassung (PDF und DOCX) in `02_Stammakte` unter einem Unterordner "Uebernahme_Korrespondenz", sobald die Unterstruktur von 02 festgelegt ist; bis dahin nur lokale Ablage und Download aus der Anwendung (Frage 5).

### 4.5 Freigabe

| Status | Wer | Bedingung | Wirkung |
|---|---|---|---|
| draft | System bei Erzeugung, Sachbearbeiter bei Neuerzeugung | Findings vorhanden | Dateien mit Wasserzeichen |
| reviewed | Sachbearbeiter | Frist gesetzt, Anschrift vollständig, Textbausteine geprüft | Dateien neu erzeugt, weiterhin Wasserzeichen |
| approved | Rolle mit Freigaberecht; ANNAHME: Admin (Geschäftsführung), Verifikation: Frage 5 | Status reviewed | Dateien ohne Wasserzeichen, mit Unterschriftsbild, Datum = Freigabedatum, `content_hash` eingefroren, Upload in Drive (falls konfiguriert) |
| marked_sent | Sachbearbeiter | Status approved | Vermerk Datum und Kanal (Freitext), Frist erscheint in "Offene Punkte" als "angefordert bis TT.MM.JJJJ" |
| withdrawn | Admin | jeder Status | Schreiben gilt als nicht versendet, neue Version möglich |

Nach `marked_sent` erhalten die einbezogenen Findings `details.requested_at` und `details.requested_deadline`; die Listen zeigen das im Blatt "Offene Punkte". Trifft eine Unterlage ein und der Prüfpunkt wird fulfilled, bleibt der Vermerk im Verlauf erhalten. Eine zweite Nachforderung (Erinnerung) ist eine neue Version mit Bezug auf die erste; der Textbaustein "Erinnerung" ist ein eigener Schlüssel. Fristen werden von der Anwendung nur angezeigt, nicht überwacht; eine Fristenkontrolle mit Erinnerung wäre ein Folge-CR.

## 5. Listen (CR 12a)

### 5.1 Datenquelle

Ausschließlich Stammdatentabellen: `owners`, `units`, `owner_unit_assignments`, `owner_files` (für die Angabe gemeinsamer Eigentümer), `field_provenance` (Status und Quelle), `completeness_findings` (Blatt "Offene Punkte"); für die Mieterliste `tenants`, `leases`, `tenant_unit_assignments`. Kein Zugriff auf `documents` außer zur Auflösung des Dateinamens für die Spalte Quelle.

Datensatzbildung Eigentümerliste: eine Zeile je Zuordnung (`owner_unit_assignments`), nicht je Einheit; eine Einheit mit zwei Eigentümern ergibt zwei Zeilen mit identischer Einheit. Blatt "Aktuell": `is_current = 1` und `deleted_at IS NULL`, Einheit aktiv. Blatt "Historie": `valid_to IS NOT NULL`. Sortierung: `units.unit_number` numerisch, dann `unit_label`, dann Nachname. Einheiten ohne Zuordnung erscheinen im Blatt "Aktuell" mit leeren Eigentümerspalten und Status "unvollständig" (nur Daten soweit erkannt, keine Platzhalter).

### 5.2 Spalten

Eigentümerliste (Mindestumfang wortgetreu nach CR 12a, Reihenfolge wie im CR):

| Spalte (CR) | Quelle | Regel |
|---|---|---|
| Einheit (WE-Nummer) | `units.unit_label` | wie erfasst |
| Einheitentyp | `units.unit_type` | Anzeigetext deutsch (Wohnung, Gewerbe, Stellplatz, Garage, Tiefgarage, Keller, Sonstige) |
| Anrede | `owners.salutation` | |
| Vorname | `owners.first_name` | |
| Nachname | `owners.last_name` | |
| Firma | `owners.company_name` | bei `type` legal_entity oder community |
| Straße und Hausnummer | `correspondence_street`, `correspondence_house_number` | zusammengesetzt mit Leerzeichen |
| PLZ | `correspondence_postal_code` | als Text, führende Null bleibt |
| Ort | `correspondence_city` | |
| abweichende Zustelladresse | `delivery_*` | einzeilig zusammengesetzt, leer wenn keine |
| Telefon | `owners.phone` | |
| Mobil | `owners.mobile` | |
| E-Mail | `owners.email` | |
| Miteigentumsanteil | `units.co_ownership_share`, `co_ownership_share_base` | Anzeige "Zähler/Nenner" wenn Nenner bekannt, sonst Zahl |
| Eigentumsbeginn | `owner_unit_assignments.valid_from` | Datum |
| Eigentumsende | `owner_unit_assignments.valid_to` | Datum, leer bei aktuell |
| Hausgeld monatlich | `units.house_fee_monthly` | Zahl 0,00 |
| SEPA-Mandat vorhanden (ja/nein) | `owners.sepa_mandate_present` | ja, nein, leer bei NULL |
| IBAN nur maskiert (letzte 4 Stellen) | `owners.iban_last4` | Anzeigeform nach D aus `iban_last4`; niemals aus `iban_encrypted` |
| Zuordnung mehrerer Eigentümer je Einheit | `owner_files`, weitere Zuordnungen derselben Einheit | Text "gemeinsam mit Beispiel (Anteil 1/2)"; leer bei Alleineigentum |
| Bemerkung | `owners.notes`, `owner_unit_assignments.notes` | zusammengeführt |
| Status | abgeleitet (5.5) | bestätigt, KI-Vorschlag, unvollständig |
| Quelle | `field_provenance`, `owner_unit_assignments.source_document_id`, `import_batches.source_file_name` | Dateiname des Quelldokuments der Zuordnung; sonst Import-Dateiname; sonst "manuell" |

Mieterliste (wortgetreu nach CR 12a): Einheit, Einheitentyp, Anrede, Vorname, Nachname, Firma, Telefon, Mobil, E-Mail, Mietbeginn (`leases.start_date`, sonst `tenant_unit_assignments.valid_from`), Mietende, Kaltmiete (`base_rent`), Nebenkostenvorauszahlung, Heizkostenvorauszahlung, Gesamtmiete (`total_rent`), Kaution (Betrag, Anlageform, soweit erkannt), Staffel oder Index (ja/nein aus `rent_adjustment_type`: graduated oder indexed gleich ja, none gleich nein, unknown leer), Anzahl Personen, SEPA-Mandat vorhanden (ja/nein), IBAN nur maskiert, Bemerkung, Status, Quelle. Vorschlag: "Kaution (Betrag, Anlageform)" als zwei Spalten "Kaution Betrag" (Zahl) und "Kaution Anlageform" (Text), damit der Betrag als Zahl formatiert werden kann; ohne Bestätigung eine Spalte mit Text "1.500,00 EUR, Sparbuch" (Frage 6).

Erweiterbarkeit: Die Spaltenlisten liegen als `lists.owner_columns` und `lists.tenant_columns` in `app_settings` (D); jede Spalte hat Schlüssel, Überschrift, Quelle, Format. Zusätzliche Spalten werden angehängt, der Mindestumfang kann nicht entfernt werden (Validierung im Admin-Bereich).

### 5.3 Excel-Formatierung (openpyxl)

1. Arbeitsmappe mit den Blättern "Aktuell", "Historie", "Offene Punkte" in dieser Reihenfolge. Eigenschaften: Titel "Eigentuemerliste NNN", Erstellt durch "Hausverwaltung Müller GmbH, Objektakte", Erstellungszeit.
2. Dokumentkopf je Blatt in den Zeilen 1 bis 3: Zeile 1 Objektnummer und Objektbezeichnung (fett), Zeile 2 Verwaltungsart, Zeile 3 "Stand: TT.MM.JJJJ HH:MM" und Zahl der Datensätze. Zeile 4 leer.
3. Tabelle ab Zeile 5 als Excel-Tabellenobjekt (`Table` mit `TableStyleInfo`, Autofilter aktiv, gestreifte Zeilen über den Tabellenstil); Kopfzeile mit Füllung Orange #E6A83C und Schrift #1A1A1A (CI, Befund 5), Schrift Arial 10.
4. Fixierung: `freeze_panes` auf Zelle A6, damit Dokumentkopf und Tabellenkopf beim Blättern stehen bleiben.
5. Zahlenformate: Beträge `#,##0.00` (in deutscher Excel-Oberfläche 1.234,56), Miteigentumsanteil als Text "Zähler/Nenner" oder Zahl mit vier Nachkommastellen, Datumszellen als echte Datumswerte mit Format `DD.MM.YYYY`, PLZ als Text. Leere Werte sind leere Zellen, keine Platzhalter, keine Bindestriche.
6. Spaltenbreiten aus Überschrift und längstem Wert (begrenzt auf 60 Zeichen), Zeilenumbruch in Bemerkung und Zustelladresse.
7. Blatt "Offene Punkte" mit den Spalten aus 3.4, gleiche Formatierung; leeres Blatt mit Zeile "Keine offenen Punkte", wenn nichts vorliegt.
8. Prüfung vor Veröffentlichung: regulärer Ausdruck auf IBAN-Muster (E 5.4) über alle Zellwerte muss leer bleiben; sonst Abbruch mit Fehler und Review-Fall (Abschnitt 5.6).

### 5.4 PDF A4 quer im HVM-CI (ReportLab)

1. Seitenformat A4 quer, Ränder ANNAHME 12 mm links und rechts, 15 mm oben unter der Kennlinie, 18 mm unten über der Fußzeile; Verifikation: Rendering-Test.
2. Kennlinie am oberen Blattrand mit vier Segmenten in den Anteilen aus Befund 5 (Anthrazit 0 bis 40 Prozent, Mittelgrau 40 bis 60 Prozent, Orange 60 bis 67,5 Prozent, Hellgrau 67,5 bis 100 Prozent), Logo rechts oben (`assets/Logo_HVM.jpg`, proportional skaliert).
3. Kopf auf jeder Seite: Objektnummer und Objektbezeichnung (fett), Verwaltungsart, "Stand: TT.MM.JJJJ HH:MM", "Seite X von Y" (Gesamtseitenzahl über zweiten Durchlauf oder Canvas-Nachbearbeitung), Listentitel "Eigentümerliste" bzw. "Mieterliste", Abschnittstitel "Aktuell", "Historie", "Offene Punkte".
4. Tabelle (Platypus `Table`, `repeatRows = 1`): Kopfzeile Orange #E6A83C mit Schrift #1A1A1A, Zeilen alternierend Weiß und Hellgrau #D7D8DA, Linien Umrissgrau #ECECEC, Schrift Helvetica.
5. Schriftgröße: Die CI sieht 10 bis 11 pt vor (Befund 5). 23 Spalten der Eigentümerliste passen damit nicht auf 297 mm Breite. Vorschlag: Tabellenschrift 8 pt, Kopf 8 pt fett, Zeilenumbruch in Textzellen, Adress- und Kontaktspalten mit Mindestbreite; alle Spalten des Mindestumfangs bleiben erhalten. ANNAHME: Damit passt eine Zeile in ein bis zwei Textzeilen; Verifikation: Rendering-Test mit den längsten Werten der Testdaten. Alternative bei Ablehnung: zwei Teiltabellen je Abschnitt (Teil A Person und Anschrift, Teil B Zeiträume, Beträge, Status), jeweils mit Schlüsselspalten Einheit und Nachname (Frage 6).
6. Fußzeile mit Pflichtangaben (Befund 5), keine erfundenen Kontaktdaten.
7. Vollständige Bankdaten erscheinen nicht; dieselbe IBAN-Musterprüfung wie bei Excel läuft über den erzeugten Text vor der Veröffentlichung.
8. Blatt "Offene Punkte" als dritter Abschnitt; bei leerer Menge eine Zeile "Keine offenen Punkte".

### 5.5 Status und Quelle je Datensatz

Status (CR 12a: bestätigt, KI-Vorschlag, unvollständig) wird je Zeile aus den `data_status`-Werten von Eigentümer, Einheit und Zuordnung sowie den `field_provenance`-Einträgen der Pflichtfelder abgeleitet. Pflichtfelder der Zeile: Einheit, Nachname oder Firma, Eigentumsbeginn. Vorrang (Vorschlag): unvollständig, wenn ein Pflichtfeld leer ist; sonst KI-Vorschlag, wenn ein befülltes Feld den Provenienzstatus `ai_suggested` trägt oder eine der drei Entitäten `ai_suggested` ist; sonst bestätigt. Begründung: Eine Zeile mit Lücke ist für den Sachbearbeiter zuerst eine Lücke, unabhängig davon, ob die vorhandenen Werte bestätigt sind.

Quelle: Dateiname des Dokuments aus `owner_unit_assignments.source_document_id`; fehlt es, `import_batches.source_file_name` über `source_import_row_id`; fehlt beides, "manuell". Stammen Felder aus verschiedenen Dokumenten (Adresse aus Eigentümerliste, SEPA aus Mandat), zeigt die Spalte die Quelle der Zuordnung und den Zusatz "u. a."; die Herkunft je Feld ist in der Objektansicht abrufbar. Vorschlag: viertes, ausgeblendetes Blatt "Herkunft" in der Excel-Datei mit einer Zeile je Feld (Einheit, Eigentümer, Feld, Quelle, Status), zuschaltbar über `lists.include_provenance_sheet`.

### 5.6 Regeneration, Entprellung, Drive-Aktualisierung

1. Auslöser (CR 12a): Ende eines Verarbeitungslaufs des Objekts, bestätigende Entscheidung im Review Center, Import-Übernahme, manueller Knopf "Listen jetzt erzeugen" in der Objektansicht. Zusätzlich nach Stammdatenänderung in der Objektansicht (nicht im CR, folgt aber aus "aus den bestätigten Daten").
2. Entprellung je Objekt: Review-Bestätigungen setzen einen Zeitgeber `lists:pending:<object_id>`; erst nach `lists.debounce_seconds` (ANNAHME 60 Sekunden, aus F übernommen; Verifikation: Anzahl `list_generations` je Objekt und Tag im ersten Betriebsmonat) ohne weitere Bestätigung startet der Job. Der manuelle Knopf startet sofort und setzt den Zeitgeber zurück.
3. Job in Queue `lists`, Lock je Objekt; Ablauf: Daten laden, `content_hash` über die sortierten Zeilen aller drei Blätter bilden, bei unverändertem Hash `list_generations.status = skipped_unchanged` ohne Drive-Aufruf; sonst Excel und PDF erzeugen, IBAN-Prüfung, Veröffentlichung über `publish_list` aus F (gespeicherte Drive-File-ID, neue Version, kein Datum im Dateinamen), `list_generations` mit Zeilenzahlen, Dauer, Auslöser.
4. Fehler (Drive nicht erreichbar, Quota): Dateien bleiben lokal unter `/srv/objektakte/lists/<object_id>/`, Job wiederholt mit Backoff (F 2.5), Status im Objekt "Listen veraltet seit TT.MM.JJJJ HH:MM". Nach Ausschöpfung Review-Fall `move_proposal` mit `case_subtype = list_publish_failed`.
5. Der manuelle Knopf zeigt nach Abschluss Ergebnis (erzeugt, unverändert, Fehler) und Links auf die Drive-Dateien.

### 5.7 Werkzeugempfehlung

| Aufgabe | Empfehlung | Geprüfte Alternativen | Begründung |
|---|---|---|---|
| Excel schreiben | openpyxl | XlsxWriter (nur schreiben, sehr schnell), pandas mit Writer (zusätzliche Abhängigkeit, wenig Formatkontrolle) | Eine Bibliothek für Lesen (Import-Parser, 6.2) und Schreiben; Tabellenobjekt mit Filter, `freeze_panes`, Zahlen- und Datumsformate, Dokumenteigenschaften werden unterstützt; Datenmenge (hunderte Zeilen je Objekt) macht Geschwindigkeit unerheblich |
| PDF erzeugen | ReportLab | WeasyPrint (HTML zu PDF, braucht Rendering-Bibliotheken im Image, Seitenkopf mit "Seite X von Y" nur über CSS-Umwege), LibreOffice headless (großes Image, Rendering-Abweichungen) | Die HVM-CI-Bausteine liegen bereits als ReportLab-Skript vor (Befund 5); exakte Kontrolle über Kennlinie, wiederholte Tabellenköpfe, Wasserzeichen und Seitenzahlen; kein Browser im Container; dieselbe Bibliothek für Listen und Nachforderung |
| DOCX erzeugen | python-docx | docxtpl (Vorlagen mit Platzhaltern, baut auf python-docx auf) | Vorlage mit Kopf- und Fußzeile, Absatzformate, Tabellen; docxtpl ist eine mögliche Erleichterung für die Textbausteine, aber keine Notwendigkeit |

Versionen jeweils: aktuelle stabile Version zum Umsetzungszeitpunkt prüfen.

## 6. Import-Parser für Eigentümer- und Mieterlisten der Vorverwaltung (CR 15)

### 6.1 Architektur

Kette aus austauschbaren Schritten; jeder Schritt schreibt sein Zwischenergebnis, damit der Sachbearbeiter an jeder Stelle korrigieren kann und nichts verloren geht.

```
Datei (Upload in Objektansicht oder Dokument aus 02_Stammakte "Als Liste importieren")
  -> 1 Annahme: SHA-256, import_batches (status uploaded), Quelldatei als documents-Zeile
  -> 2 Formaterkennung: MIME, Endung, Inhaltsprobe; PDF: Textebene vorhanden (E 1.3) oder Scan
  -> 3 Formatprofil wählen (Score je Profil, höchster gewinnt, manuell änderbar)
  -> 4 Tabellenextraktion -> RawTable (Zeilen, Zellen, Seite, Zellkonfidenz)
  -> 5 Kopfzeile erkennen, Spaltenzuordnung vorschlagen -> column_mapping (Konfidenz je Spalte)
  -> [Sachbearbeiter prüft und korrigiert die Zuordnung, speichert optional als Profil]
  -> 6 Normalisierung je Feld (Einheit, Namen-Splitter, Adresse, Telefon, E-Mail, Datum, Betrag, IBAN sofort maskieren)
  -> 7 Abgleich gegen units (exakt normalisiert) und owners bzw. tenants (unscharf, E 3.2)
  -> 8 Zeilenkonfidenz, Unsicherheitsgründe -> import_rows (status parsed oder uncertain), review_cases F6 für uncertain
  -> [Review Center: sichere Zeilen gesammelt bestätigen, unsichere einzeln]
  -> 9 Übernahme je bestätigter Zeile in owners, units, owner_unit_assignments (bzw. tenants, leases, tenant_unit_assignments), field_provenance
  -> 10 Protokoll (import_batches Zähler, Importprotokoll als Datei), Listen und Vollständigkeit neu erzeugen
```

Schnittstellen im Code: `FormatProfile.detect(file) -> score`, `FormatProfile.extract(file) -> RawTable`, `ColumnMapper.propose(raw_table, target_schema) -> Mapping`, `FieldNormalizer.normalize(field, value) -> NormalizedValue(value, confidence, notes)`, `NameSplitter.split(text) -> list[Person]`, `EntityMatcher.match(row, object_id) -> MatchResult`, `ImportCommitter.commit(row, decision) -> CommittedTargets`. Schritte 1 bis 8 laufen als Job im Worker (Queue `io`), Schritt 9 als kurze Transaktionen aus der Bestätigung heraus.

### 6.2 Formatprofile

| Profil (`parser_profile`) | `source_format` | Erkennung | Extraktion | Werkzeug | Besonderheiten |
|---|---|---|---|---|---|
| `csv_generic` | csv | Endung csv, txt; Trennzeichen aus Inhaltsprobe (Semikolon, Komma, Tabulator); Zeichensatz aus Probe (UTF-8 mit und ohne BOM, Windows-1252, Latin-1) | Python `csv` mit erkanntem Dialekt | Standardbibliothek, Zeichensatzerkennung über eine Erkennungsbibliothek | Anführungszeichen, Zeilenumbrüche in Zellen; Leerzeilen und Summenzeilen werden als Zeilen mit Grund `not_a_record` geführt, nicht verworfen |
| `xlsx_generic` | xlsx | Endung xlsx, xlsm, xls | openpyxl (xlsx); für altes xls eine Lesebibliothek nur bei Bedarf | openpyxl | Mehrere Blätter: Vorschlag des Blatts mit den meisten Zeilen, die eine Einheitenspalte enthalten; Sachbearbeiter kann wechseln; verbundene Zellen werden auf alle betroffenen Zellen übertragen; Kopfzeile ist die erste Zeile mit mindestens drei Treffern im Synonymwörterbuch |
| `pdf_digital_table` | pdf_digital | PDF mit Textebene auf den Tabellenseiten | Tabellenerkennung über Linien und Textausrichtung; Fallback: Wörter mit Koordinaten, Spaltenbildung über Lücken in der x-Verteilung, Zeilenbildung über y | pdfplumber für Tabellen, PyMuPDF für Wörter mit Koordinaten | Mehrseitige Tabellen mit wiederholter Kopfzeile werden zusammengeführt; Fußzeilen (Seitenzahlen) erkannt und markiert |
| `pdf_scan_ocr` | pdf_scan | PDF ohne Textebene oder Bilddatei | OCR-Ergebnis mit Wortkoordinaten und Wortkonfidenz (Tesseract TSV-Ausgabe aus der Pipeline), Layoutanalyse wie beim digitalen PDF, Zellkonfidenz aus den Wortkonfidenzen | Tesseract `deu` über die Pipeline (E), eigene Layoutanalyse | ANNAHME: Zellen mit mittlerer Wortkonfidenz unter 70 gelten als unsicher; Verifikation: Stichprobe der ersten gescannten Listen. Optional Stufe 3 (externe KI) zur Strukturierung eines Seitentexts in Zeilen, nur mit maskiertem Text und nur nach Freigabe (Frage 9) |
| `immoware24_export` | immoware24 | Kopfzeile entspricht den Spalten aus Befund 4 | wie `csv_generic` | Standardbibliothek | Siehe 6.2.1 |
| `domus_export` | domus | Kopfzeile unbekannt | wie `csv_generic` oder `xlsx_generic` | | Spalten nicht bekannt; Profil wird angelegt, sobald eine Beispieldatei vorliegt (Frage 8); bis dahin greift `generic_table` |
| `generic_table` | other | Rückfall | je nach Datei | | Vollständig manuelle Spaltenzuordnung |

#### 6.2.1 Profil Immoware24 (Spalten aus Befund 4)

Datei einheiten.csv mit Trennzeichen Semikolon und Spalten Objekt-Nr; Status; Objekt; Verwaltungsart; Gebaeude; VE-Nr; VE-Beschreibung; Lage; Eigentuemer; Hausgeld_EUR_mtl; Mieter; Miete_EUR_mtl. Datei kontakte.csv mit ID; Name; Briefanrede; Adresse; PLZ; Stadt; Land; Telefon; E-Mail.

| Quellspalte | Zielfeld | Regel |
|---|---|---|
| Objekt-Nr | Filter | Nur Zeilen des gewählten Objekts (Zahlenwert, `82` gleich `082`); Zeilen anderer Objekte erhalten Grund `other_object` und Vorschlag "nicht übernehmen", bleiben im Protokoll |
| Status | Steuerung | Nur Status aktiv wird zur Übernahme vorgeschlagen; abrechnung, archiv, technisch erhalten Grund `unit_status_not_active` mit Vorschlag "nicht übernehmen"; Entscheidung im Review Center (Frage 8) |
| Objekt, Verwaltungsart | Abgleich | Gegen `objects.name` und `management_type`; Abweichung als Hinweis am Import, keine Änderung der Objektstammdaten |
| Gebaeude | `units.building` | |
| VE-Nr | `units.external_ref` | rein numerische Fremdkennung (Befund), nie als `unit_number` |
| VE-Beschreibung | `units.unit_label`, daraus `unit_number`, `unit_type` (6.5) | |
| Lage | `units.location` | |
| Eigentuemer | Namens-Splitter (6.4), ergibt einen oder mehrere `owners`, je Person eine Zuordnung | Freitext mit mehreren Personen (Befund) |
| Hausgeld_EUR_mtl | `units.house_fee_monthly` | Dezimaltrennzeichen Komma, Tausenderpunkt |
| Mieter | Namens-Splitter, ergibt `tenants` und `tenant_unit_assignments` | `import_kind = mixed` |
| Miete_EUR_mtl | offen | Bedeutung (Kaltmiete oder Gesamtmiete) nicht aus dem Befund ableitbar; bis zur Klärung in `leases.notes` mit Kennzeichnung "Miete lt. Export" (Frage 8) |
| kontakte.csv Name | Verknüpfung zu `owners` bzw. `tenants` | Unscharfer Namensabgleich (E 3.2) gegen die gesplitteten Personen; 46 doppelte Namen laut Befund ergeben Kandidatenlisten, nie automatische Übernahme bei Mehrdeutigkeit |
| Briefanrede | `salutation` | |
| Adresse | `correspondence_street`, `correspondence_house_number` | Trennung der Hausnummer über regulären Ausdruck (Ziffernfolge mit optionalem Buchstaben am Ende) |
| PLZ, Stadt, Land | `correspondence_postal_code`, `correspondence_city`, `correspondence_country` | Land in ISO-2 übersetzen, unbekannt bleibt leer mit Hinweis |
| Telefon, E-Mail | `phone` oder `mobile` (Vorwahlmuster), `email` | |

Nicht im Export enthalten (Befund 4): Miteigentumsanteile, Wirtschaftsjahr, IBAN, Kautionen, Salden. Diese Felder bleiben leer und erscheinen in der Vollständigkeitsprüfung.

### 6.3 Spaltenzuordnung mit Vorschlag und manueller Korrektur

Zielschema Eigentümerliste: `unit_label`, `unit_type_hint`, `owner_name_raw`, `salutation`, `first_name`, `last_name`, `company_name`, `street`, `house_number`, `postal_code`, `city`, `country`, `delivery_address_raw`, `phone`, `mobile`, `email`, `co_ownership_share`, `co_ownership_share_base`, `valid_from`, `valid_to`, `house_fee_monthly`, `sepa_mandate_present`, `iban_raw`, `mandate_reference`, `notes`, `external_ref`. Zielschema Mieterliste zusätzlich `base_rent`, `utilities_prepayment`, `heating_prepayment`, `deposit_amount`, `deposit_type`, `rent_adjustment_type`, `persons_count`, `start_date`, `end_date`.

Vorschlagslogik je Quellspalte: Score aus Überschrift (exakter, normalisierter und unscharfer Treffer im Synonymwörterbuch `import.column_synonyms` in `app_settings`, z. B. "WE", "Einheit", "Whg", "Wohnung", "Nr" für `unit_label`; "Eigentümer", "Eigentuemer", "Name" für `owner_name_raw`; "MEA", "Miteigentumsanteil", "Anteil" für `co_ownership_share`; "Hausgeld", "Wohngeld", "Vorschuss" für `house_fee_monthly`; "Straße", "Strasse", "Anschrift" für `street`) und aus dem Inhalt (Musterprüfung der ersten Zeilen: fünfstellige Zahl für PLZ, Einheitenmuster aus 6.5, E-Mail-Muster, IBAN-Muster, Bruch "Zähler/Nenner" für MEA, Datumsmuster). ANNAHME: Vorschlag ohne Markierung ab Konfidenz 0,80, darunter gelb markiert, ohne Treffer leer; Verifikation: Anteil manueller Korrekturen der Zuordnung in den ersten zehn Importen.

Oberfläche: Tabelle mit einer Zeile je Quellspalte (Überschrift, drei Beispielwerte, vorgeschlagenes Zielfeld als Auswahlliste, Konfidenz). Ein Zielfeld darf nur einmal vergeben werden, Ausnahme `notes` (mehrere Quellspalten werden mit Überschrift als Präfix zusammengeführt). Nicht zugeordnete Spalten bleiben in `raw_data` erhalten und werden als "nicht übernommen" protokolliert. Die bestätigte Zuordnung wird in `import_batches.column_mapping` gespeichert und kann als benanntes Profil (`import_column_profiles`, Vorschlag) für weitere Objekte derselben Vorverwaltung gesichert werden.

### 6.4 Namens-Splitter für Mehrfacheigentümer

Eingabemuster laut Befund 4: "Nachname, Vorname & Vorname", "Nachname, Vorname u. Vorname", "Vorname und Vorname Nachname", "Vorname u. Vorname Nachname", GbR-Bezeichnungen, "c/o"-Zusätze, Trennung mehrerer Personen durch Schrägstrich.

Verfahren in Reihenfolge:

1. Vorverarbeitung: Leerzeichen normalisieren, Anführungszeichen entfernen, "c/o ..." abtrennen und als `correspondence_addition` vormerken.
2. Rechtsformerkennung: Marker (GmbH, AG, KG, OHG, UG, e.V., GbR, Stiftung, Genossenschaft, Gemeinschaft, Erbengemeinschaft, WEG, Eheleute, Ehepaar, Familie) aus konfigurierbarer Liste `import.legal_form_markers`. GbR, GmbH und Ähnliche ergeben `type = legal_entity` mit `company_name` gleich Gesamttext, kein Split. "Erbengemeinschaft Mustermann" ergibt `type = community`, `company_name` gleich Gesamttext, Konfidenz hoch. "Eheleute Mustermann" ergibt zwei Personen mit Nachname Mustermann ohne Vornamen, Konfidenz niedrig (Vornamen fehlen), Grund `first_names_missing`.
3. Personentrennung: Schrägstrich, Semikolon oder " / " trennen Personen mit jeweils vollständigem Namen. Innerhalb eines Teils gilt:
   Kommaform "Nachname, Vorname & Vorname": Text vor dem Komma ist Nachname, danach die Vornamen, getrennt durch "&", "u.", "und", "+"; jede Person erhält den gemeinsamen Nachnamen. Konfidenz hoch.
   Leerzeichenform "Vorname und Vorname Nachname": das letzte Token (bei Doppelnamen mit Bindestrich das letzte Bindestrich-Token) ist der Nachname, die Tokens vor der Konjunktion und zwischen Konjunktion und Nachname sind Vornamen. Konfidenz mittel, weil Vornamen aus zwei Wörtern (z. B. "Anna Maria") nicht sicher von Nachnamenbestandteilen zu trennen sind.
   Einzelname "Vorname Nachname" oder "Nachname, Vorname": Konfidenz hoch bei Kommaform, mittel bei Leerzeichenform mit mehr als zwei Tokens.
4. Titel und Anreden (Dr., Prof., Dipl.-Ing., Herr, Frau) werden abgetrennt: Anrede nach `salutation`, Titel als Präfix des Vornamens bewahrt.
5. Ausgabe je Person: `first_name`, `last_name`, `salutation`, `type`, `split_pattern` (Code des angewendeten Musters), `confidence`. Der Originaltext bleibt in `import_rows.raw_data`; jede Person wird eine eigene `import_rows`-Zeile mit `sub_index` (D).
6. Konfidenzschwellen (ANNAHME): ab 0,90 Vorschlag "übernehmen", 0,60 bis 0,89 Vorschlag mit gelber Markierung, unter 0,60 unsicher mit Alternativvorschlag "als Gemeinschaft mit Gesamttext übernehmen" (Zeile wird F6). Verifikation: Anteil korrigierter Splits in den Review-Entscheidungen der ersten fünf Importe, Schwellen als Konfiguration `import.name_split_thresholds`.

Testfälle (synthetisch, verbindlich für die Unit-Tests):

| Eingabe | Erwartung |
|---|---|
| "Mustermann, Erika & Max" | zwei Personen Mustermann Erika, Mustermann Max, hoch |
| "Mustermann, Erika u. Max" | wie oben |
| "Erika und Max Mustermann" | wie oben, mittel |
| "Erika u. Max Mustermann" | wie oben, mittel |
| "Mustermann, Erika / Beispiel, Hans" | Mustermann Erika, Beispiel Hans, hoch |
| "Muster GbR" | legal_entity, company_name "Muster GbR", hoch, kein Split |
| "Erbengemeinschaft Mustermann" | community, hoch |
| "Mustermann, Erika c/o Beispiel Hausverwaltung" | Mustermann Erika, correspondence_addition "c/o Beispiel Hausverwaltung", hoch |
| "Eheleute Mustermann" | zwei Personen Nachname Mustermann ohne Vornamen, niedrig, Grund first_names_missing |
| "Dr. Erika Mustermann-Beispiel" | Mustermann-Beispiel Erika, Titel Dr., hoch |
| "Anna Maria Mustermann Beispiel" | unsicher (Nachname zweiteilig oder Doppelvorname), unter 0,60, F6 |

### 6.5 Einheitennormalisierung

Muster aus Befund 4: "WEN", "WE N", "SN", "Garage N", "N", "GE N", "MVW N", "Stellplatz Nr. N", "GAN", "TGN", "SPN", "GA N", "GEN", "STN", "Haus N", "Container N", "Wohnung N", "WE N - N.OG rechts/mitte/links", "Lagerhalle N", "STPN", "VEN_SILN_N.L".

Verfahren:

1. Regulärer Ausdruck: Präfix (Buchstaben, Punkte, Wort "Nr."), Ziffernfolge, Rest (ein Trennstrich zwischen Nummer und Rest, wie im Befundmuster, wird verworfen). Beispiel Quellwert "WE 14" mit Zusatz "2.OG rechts": Präfix "WE", Nummer "14", Rest "2.OG rechts" nach `units.location`.
2. `unit_number` ist die Ziffernfolge ohne führende Nullen; `unit_label` bleibt wie in der Quelle; Vergleichsschlüssel `unit_label_normalized` nach D (Großschreibung, ohne Leerzeichen). Vorschlag zur Ergänzung von D: führende Nullen im Vergleichsschlüssel entfernen, damit "WE01" und "WE 1" als dieselbe Einheit erkannt werden; Verifikation an den Bestandsdaten, ob eine Vorverwaltung "WE01" und "WE1" nebeneinander als verschiedene Einheiten führt (dann Option abschaltbar).
3. `unit_type` über `units.type_prefix_mapping` (D, aus Befund): WE, WOHNUNG gleich apartment; GE gleich commercial; S, ST, STP, SP, STELLPLATZ gleich parking; GA, GARAGE gleich garage; TG gleich underground_parking. Vorschlag: KE, KELLER gleich cellar ergänzen (CR-Typ cellar ist sonst nicht erreichbar); HAUS, CONTAINER, LAGERHALLE, MVW gleich other mit Hinweis.
4. Präfixlose Nummer ("N"): ANNAHME: in WEG-Objekten als apartment mit Grund `prefix_missing` und Konfidenz mittel; existiert im Objekt bereits "WE N", wird die Zeile als Kandidat für dieselbe Einheit geführt, nicht automatisch zusammengelegt. Verifikation: Review-Entscheidungen der ersten Importe (Frage 8).
5. Unterschiedliche Präfixe mit gleicher Nummer ("WE 3" und "Garage 3") sind verschiedene Einheiten.
6. Sonderformen ("VEN_SILN_N.L") werden nicht zerlegt: `unit_label` wie Quelle, `unit_number` leer, `unit_type = other`, Grund `unit_label_unparsed`, Zeile F6.

### 6.6 Dublettenabgleich gegen owners (unscharf)

Signale und Gewichtung (Vorschlag, Werte in `import.owner_match_weights`):

| Signal | Quelle | Gewicht |
|---|---|---|
| Namensähnlichkeit (E 3.2, `rapidfuzz`, `token_set_ratio` für Personen, `partial_ratio` für Firmen, Normalisierung Umlaute, Bindestriche, OCR-Verwechslungen) | `owners.search_name` | Basis |
| gleiche Einheit im selben Objekt bereits mit diesem Eigentümer verknüpft | `owner_unit_assignments` | stark |
| gleiche Anschrift (PLZ und Straße normalisiert) | `owners.correspondence_*` | mittel |
| gleiche E-Mail | `owners.email` | stark |
| gleicher `iban_hash` (HMAC der normalisierten IBAN, vor der Maskierung gebildet) | `owners.iban_hash` | stark |
| gleicher Vorname bei gleichem Nachnamen | `owners.first_name` | mittel |

Entscheidung je gesplitteter Person: Gesamtscore ab Schwelle "automatisch" (E, ANNAHME A05: 90) ergibt Vorschlag "bestehenden Eigentümer verwenden, fehlende Felder ergänzen, keine bestehenden Felder überschreiben"; im Kandidatenband (78 bis 89) Vorschlag mit Kandidatenliste (F6, `owner_match_ambiguous`); darunter Vorschlag "neuen Eigentümer anlegen". Ein Nachnamentreffer allein reicht nie für einen automatischen Treffer (Befund: doppelte Namen), zusätzlich muss Vorname, Einheit, Anschrift oder E-Mail passen. Objektübergreifende Treffer (derselbe Eigentümer in einem anderen Objekt) werden angezeigt, aber immer bestätigt, nie automatisch übernommen. Innerhalb eines Imports werden gleiche Personen über mehrere Zeilen (ein Eigentümer mit Wohnung und Stellplatz) zu einem Eigentümer mit mehreren Zuordnungen zusammengeführt; die Zeilen verweisen auf denselben vorgeschlagenen Eigentümer.

Konflikt mit bestehender Zuordnung: Hat die Einheit bereits eine aktuelle Zuordnung zu einem anderen Eigentümer, wird nichts überschrieben (CR 3). Die Zeile wird F6 mit `owner_conflict` und drei Optionen: Eigentümerwechsel (alte Zuordnung mit `valid_to` beenden, neue mit `valid_from` aus der Liste anlegen), Mehrfacheigentum (zweite Zuordnung mit Anteil ergänzen), Dublette (Zeile meint den bestehenden Eigentümer).

### 6.7 Ergebnis im Review Center und Übernahme

Importansicht (Teil des Review Centers, Filter `import_batch_id`): Kopf mit Datei, Profil, Zeilen gesamt, sicher, unsicher, Dublettenkandidaten, nicht übernehmen, Fehler. Darunter die Zeilen mit Rohzeile (aufklappbar), erkannten Feldern, Abgleichergebnis, Vorschlag und Konfidenz. Sichere Zeilen tragen denselben `batch_key` (Import) und werden über die Massenbearbeitung (2.5) mit Vorschau gesammelt bestätigt; unsichere Zeilen sind einzelne F6-Fälle mit Feldkorrektur im Formular (dieselben Felder wie die Zielentitäten, Einheiten- und Eigentümerauswahl mit Vorschlägen).

Übernahme je bestätigter Zeile in einer Transaktion: `owners` anlegen oder ergänzen (nur leere Felder füllen), `units` anlegen oder über `unit_label_normalized` bzw. `external_ref` wiederverwenden, `owner_unit_assignments` anlegen (`valid_from` aus der Liste, sonst NULL; `source_import_row_id`; `data_status = confirmed`, `confirmed_by`), `field_provenance` je übernommenem Feld mit `source_kind = import_row`, `import_rows.status = committed`, `committed_*`-Verweise, `audit_events`. ANNAHME: unter 100 ms je Zeile, also 1.000 Zeilen in unter zwei Minuten als Hintergrundjob mit Fortschritt; Verifikation: Integrationstest mit 1.000 synthetischen Zeilen. Fehler in einer Zeile stoppen die anderen nicht; `import_batches.status = partially_committed`, bis alle Zeilen entschieden sind.

Abgelehnte Zeilen erhalten `status = rejected` mit Grund und bleiben erhalten. Eine Zeile mit Status `duplicate` verweist auf die Zeile oder den Eigentümer, den sie doppelt. Prüfung am Ende: `rows_total` gleich Summe aus `committed`, `rejected`, `duplicate`, `uncertain`, `parsed`; sonst Fehler im Protokoll. Nach Abschluss: Listen und Vollständigkeitsprüfung des Objekts neu erzeugen.

### 6.8 Protokoll je Import

`import_batches` hält die Zähler (D). Zusätzlich erzeugt die Anwendung ein Importprotokoll als Datei (Vorschlag: Excel über openpyxl, ein Blatt "Zeilen" mit Quellzeile, Rohtext, erkannten Feldern, Vorschlag, Entscheidung, Bearbeiter, Zeitpunkt, Zielentitäten; ein Blatt "Zusammenfassung" mit Datei, Hash, Profil, Spaltenzuordnung, Zählern) unter `/srv/objektakte/imports/<batch_id>/protokoll.xlsx`, abrufbar aus der Importansicht. Das Protokoll enthält keine vollständige IBAN (Maskierung vor dem Speichern in `raw_data`, D 9.2).

### 6.9 Mieterlisten

Gleiche Kette mit `import_kind = tenant_list` (oder `mixed`, wenn eine Quelle beide Spalten führt, wie der Immoware24-Export). Zielentitäten `tenants`, `leases`, `tenant_unit_assignments`; Zusatzfelder nach 6.3. Ein Mietverhältnis (`leases`) entsteht je Zeile mit Mietfeldern; mehrere Mieter einer Zeile (Namens-Splitter) erhalten je eine Zuordnung mit `role = tenant` bzw. `co_tenant` (Vorschlag: erste Person tenant, weitere co_tenant, im Review änderbar). Kaution: Betrag als Zahl, Anlageform über Synonymliste auf `leases.deposit_type` (D) abgebildet, unbekannt bleibt `unknown`.

## 7. Suche und Reporting (CR 13)

### 7.1 Suchfelder

Globale Suche (Strg plus K) mit strukturierten Filtern und Freitext:

| Feld | Quelle | Verhalten |
|---|---|---|
| Eigentümer | `owners.search_name`, `last_name`, `company_name` | Präfixsuche, bei weniger als drei Treffern unscharfe Suche (E 3.2); Treffer zeigen Objekte, Einheiten, Zeiträume |
| Mieter | `tenants.search_name` | analog |
| Einheit | `units.unit_label_normalized`, `unit_number` | "WE 14" und "WE14" treffen dieselbe Einheit; optional mit Objektfilter |
| Objekt | `objects.object_number_numeric`, `name`, `street`, `city` | Zahl oder Text |
| Zeitraum | `document_owner_links.period_year`, `period_from`, `period_to`, `documents.document_date` | Jahr oder Von bis |
| Dokumentunterart | `document_types` | Auswahlliste, gruppiert nach Hauptordner und Unterordner |
| Hauptordner | `document_categories.code` 01 bis 06 | |
| Status | `document_owner_links.status`, `documents` (final klassifiziert, in Review) | |
| Verwaltungsart | `objects.management_type` | |
| Volltext | `document_pages.text_content` (maskiert) | Wortsuche, Treffer mit Seitenangabe und Textausschnitt |

Ergebnisarten: Dokumente (mit Seitenbereich, Akte, Drive-Pfad, Link auf Datei, offener Review-Fall), Eigentümer (mit allen Zuordnungen), Einheiten, Mieter. Rechte: Eigentümerakten nur für berechtigte Rollen (CR 10); die Suche filtert Ergebnisse serverseitig nach Rolle. Suchanfragen mit Personenbezug werden nicht in Anwendungslogs geschrieben (nur Anzahl Treffer und Dauer).

### 7.2 Indexstrategie

| Kriterium | Datenbank-Volltext (InnoDB FULLTEXT auf `document_pages.text_content`) plus relationale Indizes | Eigener Suchdienst als Container (z. B. Meilisearch oder OpenSearch) |
|---|---|---|
| Abdeckung von CR 13 | Eigentümer, Einheit, Zeitraum, Unterart sind relationale Felder; Volltext ist Zusatz | gleich, erfordert aber Nachführung aller Metadaten in den Index |
| Betrieb | kein weiterer Container, kein zweiter Datenbestand, Backup mit DB | weiterer Container, eigenes Volume, Synchronisation nach jeder Entscheidung, zweiter Backup-Pfad |
| Datenschutz | Text ist vor dem Speichern maskiert (E 5), damit ist "IBAN nicht im Volltextindex" (CR 10) automatisch erfüllt | Maskierung ebenfalls vorher, zusätzlicher Speicherort personenbezogener Texte |
| Sprache | keine deutsche Stammformreduktion, Mindesttokenlänge als Serverparameter, Umlautvarianten über die Anwendung (Abfrage mit ae/ä-Varianten, ODER-verknüpft) | Tippfehlertoleranz, Ranking, Facetten, Stammformen je Konfiguration |
| Volumen | ANNAHME: bis 2,5 Millionen Seiten je Jahr als Planungsgröße (CR-Lastprofil 1 bis 4 Objekte je Tag mit 1.000 bis 10.000 Seiten, mittlere Auslastung angenommen); Verifikation: Zeilenzahl `document_pages` je Quartal | gleiches Volumen, Index etwa in Textgröße |
| Antwortzeit | ANNAHME: p95 unter 2 Sekunden bei diesem Volumen mit Objekt- oder Zeitraumfilter; Verifikation: Suchabfragen im Performance-Test (CR 14) bei laufender Verarbeitung | typischerweise unter 100 ms, aber ohne Nachweis auf diesem Server |

Empfehlung: Datenbank-Volltext in der ersten Ausbaustufe. Begründung: Alle im CR genannten Suchkriterien sind Metadaten; der Volltext dient dem Auffinden von Dokumenten, deren Metadaten unvollständig sind, und wird immer mit mindestens einem relationalen Filter (Objekt, Zeitraum oder Kategorie) kombiniert, was die Trefferliste vorab eingrenzt. Ausbaupfad mit Auslösekriterien: eigener Suchdienst als Folge-CR, wenn im Performance-Test oder im Betrieb p95 der Suche über 2 Sekunden liegt, wenn die Sachbearbeiter Tippfehlertoleranz im Volltext benötigen, oder wenn Facetten über alle Objekte gefordert werden. Die Suchschicht wird hinter einer Schnittstelle `SearchIndex.query(filters, text) -> Hits` gekapselt, damit der Austausch ohne Änderung der Oberfläche möglich ist.

Umsetzungshinweise: Volltextabfrage im booleschen Modus mit Mindestlänge der Suchwörter laut Serverparameter (Wert in der Admin-Konfiguration anzeigen, damit der Sachbearbeiter weiß, warum "OG" keinen Treffer bringt); Ergebnisse auf `documents` aggregiert (bester Seitentreffer je Dokument); Zeitraumsuche über `period_year` und `document_date` mit den Indizes aus D (`ix_dol_owner_year`, `ix_dol_unit_year`).

### 7.3 KPI-Definitionen

| KPI | Formel | Datenquelle | Granularität | Zielwert |
|---|---|---|---|---|
| Anteil 06_Sonstiges (brutto, CR 8) | Anzahl Dokumente mit finaler Kategorie 06 nach dem Verarbeitungslauf geteilt durch Anzahl aller im Lauf klassifizierten Dokumente, in Prozent | `document_classifications` mit `is_final = 1` zum Zeitpunkt des Laufendes (Momentaufnahme in `processing_runs`, Vorschlag: Spalten `docs_total`, `docs_misc`), D 12.4 | je Objekt und Lauf; Bestandsansicht aller Objekte | unter 5 Prozent (CR 8) |
| Anteil 06_Sonstiges (aktuell) | wie oben, aber Stand jetzt nach Review-Entscheidungen | `document_classifications` aktuell | je Objekt | sinkt mit Review-Fortschritt |
| Anteil 06_Sonstiges (bereinigt, Vorschlag) | wie brutto, ohne Unterordner 03_Dubletten und 04_Nicht_objektbezogen | wie oben mit `subfolder` | je Objekt | Kennzahl für die Klassifikationsqualität; die beiden ausgenommenen Unterordner sind korrekte Ergebnisse |
| Vollständigkeitsstatus | `completeness_ratio` und Status nach 3.4, Anzahl missing je Kategorie | `completeness_findings` | je Objekt, je Einheit | 100 Prozent vor Abschluss der Übernahme |
| Offene Review-Fälle | Anzahl `status in (open, in_progress)` je Fallart; Median und Maximum des Alters in Tagen; Zugang und Erledigung je Tag | `review_cases` | je Objekt, gesamt | keine Fälle älter als ANNAHME 10 Arbeitstage; Verifikation: Vorgabe Geschäftsführung |
| Trefferquote System | Anteil Entscheidungen mit `system_was_correct = 1` an allen Entscheidungen, getrennt nach Stufe des finalen Vorschlags | `review_decisions`, `document_classifications` | je Objekt, je Monat | steigend; Eingang in die Schwellwertkalibrierung (E 7) |
| Eigentümerakte je Objekt | Anzahl Akten, Anzahl Dokumente je Akte, Akten ohne Dokument, Akten `Unbekannte_WE`, `Unzugeordnet` | `owner_files`, `document_owner_links` | je Objekt | keine Akte `Unzugeordnet` mit Inhalt vor Abschluss |
| KI-Kosten je Objekt (CR 0.1) | Summe `cost_eur`, Tokens, Anzahl Aufrufe, Anteil Stufe 3 an allen Dokumenten | `ai_calls`, `document_classifications` | je Objekt, je Monat | Kostenlimit aus Konfiguration |
| Importstand | Zeilen gesamt, bestätigt, unsicher, abgelehnt je Import | `import_batches` | je Objekt | keine unsicheren Zeilen vor Abschluss |

Darstellung: Objektübersicht als Tabelle aller Objekte im Status `takeover` und `active` mit den KPI-Spalten, sortierbar; Objektdetail mit Kacheln und den Listen der offenen Punkte und Fälle; Export der Übersicht als CSV. Keine Diagramme in der ersten Ausbaustufe; Zahlen reichen für 3 bis 6 Nutzer und lassen sich in Excel weiterverarbeiten.

## 8. Bedienungsanleitung (Gliederung)

Format: Markdown unter `docs/bedienung/` im Repository, aus dem CI-Lauf als PDF im HVM-CI exportiert (ReportLab-Bausteine), Bildschirmfotos nach Fertigstellung der Oberfläche. Umfang: kurz, aufgabenorientiert, je Kapitel ein Ablauf in nummerierten Schritten und ein Abschnitt "Wenn etwas nicht klappt".

1. Anmeldung, Rollen, Zwei-Faktor, Passwort ändern.
2. Objekt anlegen: Objektnummer (Ist-Nummer, Nullauffüllung laut Konfiguration), Bezeichnung, Adresse, Verwaltungsart, Übernahmedatum und Übernahmezeitraum, Wirtschaftsjahr, Vorverwaltung mit Anschrift, Sollzahl Einheiten, SEPA-Nutzung, Sonderumlagen im Zeitraum.
3. Ordnerabgleich: Dry-Run starten, Plan lesen (Anlagen, Umbenennungen, Verschiebungen), Ausführung, Protokoll prüfen, Wiederholung.
4. Eigentümer- oder Mieterliste importieren: Datei hochladen, Profil prüfen, Blatt wählen, Spaltenzuordnung bestätigen oder korrigieren, Profil speichern, Zeilen prüfen (sichere gesammelt, unsichere einzeln), Übernahme, Protokoll.
5. Verarbeitung starten und verfolgen: Lauf starten, Statusseite, Fortschritt, Kosten, Abbruch und Wiederaufnahme.
6. Review Center: Liste und Filter, gespeicherte Sichten, Detailansicht, Zielbereich und Pflichtfelder, Kandidatenliste, Aktionen, Aufteilen nach Seitenbereichen, Dublette, Zurückstellen, Massenbearbeitung mit Vorschau, Serienmodus und Tastatur, Rückgängig, Protokoll eines Falls.
7. Listen: wo sie liegen, wann sie sich aktualisieren, manuell erzeugen, Status und Quelle lesen, was "unvollständig" bedeutet, warum keine IBAN erscheint.
8. Vollständigkeit und Offene Punkte: Bewertung lesen, manuell schließen (Negativerklärung), Zeitraum anpassen, Neubewertung.
9. Nachforderung: erzeugen, Frist und Anschrift setzen, Textbausteine, Prüfung, Freigabe, Download DOCX und PDF, als versendet markieren, Erinnerung.
10. Suche und Objektübersicht: Suchfelder, Ergebnisarten, KPI lesen.
11. Häufige Fragen und Fehlermeldungen: Drive nicht erreichbar, Token abgelaufen, doppelte Objektnummer, Listen veraltet, Import-Zeile nicht zuordenbar.
12. Anhang: Tastenübersicht, Begriffe (Fallart, Zuordnung, Akte, Unterart, Zeitraum), Ansprechpartner Admin.

## 9. Abweichungen und Ergänzungsvorschläge (keine Entscheidungen)

| Nr. | CR bzw. Entwurf D | Vorschlag | Begründung |
|---|---|---|---|
| 1 | D `review_cases.case_type` ohne Typ für fehlende Pflichtmetadaten | neuer `case_type = missing_metadata` | Aufgabenstellung nennt die Fallart ausdrücklich; Trennung von "unklar" hält die Statistik sauber |
| 2 | D `review_cases` ohne Wiedervorlage | Spalte `snoozed_until` | Aktion "zurückstellen" braucht einen Zeitpunkt |
| 3 | D `documents` ohne Dublettenverweis | Spalte `duplicate_of_document_id` | Aktion "zusammenführen als Dublette" |
| 4 | D `completeness_findings` ohne manuelle Übersteuerung | Spalten `manual_status`, `manual_reason`, `manual_by`, `manual_at` | Negativerklärungen der Vorverwaltung |
| 5 | kein Prüfkatalog als Daten | Tabelle `completeness_checks` mit Seed der 15 CR-Prüfpunkte | Erweiterbarkeit ohne Codeänderung, wenn die Basis 01 bis 04 spezifiziert wird |
| 6 | D `objects` ohne Sollzahl, SEPA-Nutzung, Sonderumlagen, Anschrift Vorverwaltung | Spalten `expected_unit_count`, `sepa_used`, `special_levies_in_period`, `previous_manager_*` | Prüfpunkte 2, 12, 13 und Anschriftfeld der Nachforderung |
| 7 | D `owner_unit_assignments` ohne Saldo | Spalte `balance_at_takeover` (nullable) | Prüfpunkte 8 und 9 je Eigentümer |
| 8 | CR 5 kennt keine Unterart Sonderumlage | Unterart `sonderumlage_einzel` unter 04_Hausgeld | Prüfpunkt 13 braucht ein Nachweisdokument |
| 9 | CR 12 nur WEG | Mietkatalog als Vorschlag (3.6), Kennzeichen `units.se_managed` für WEG mit SE | CR 12a verlangt "Offene Punkte" auch für die Mieterliste; Befund kennt "WEG mit SE-Verwaltung" |
| 10 | kein Nachforderungsdatensatz | Tabellen `document_requests`, `request_text_blocks` | Entwurf, Freigabe, Versandvermerk, Textbausteine mit Versionen |
| 11 | CR 12a "Kaution (Betrag, Anlageform)" | zwei Spalten | Betrag als Zahl formatierbar |
| 12 | CI 10 bis 11 pt | 8 pt in der Listentabelle des PDF | 23 Spalten auf A4 quer |
| 13 | D `unit_label_normalized` ohne Nullenbereinigung | führende Nullen im Vergleichsschlüssel entfernen, abschaltbar | "WE01" und "WE 1" |
| 14 | D `units.type_prefix_mapping` ohne Keller | KE, KELLER gleich cellar; HAUS, CONTAINER, LAGERHALLE, MVW gleich other | CR-Typ cellar erreichbar, Befundmuster abgedeckt |
| 15 | kein Ort für Nachforderungen in Drive | 02_Stammakte, Unterordner "Uebernahme_Korrespondenz", erst nach Festlegung der Struktur von 02 | Korrespondenz betrifft das Objekt, nicht einen Eigentümer |
| 16 | CR 13 Reporting | zusätzlich bereinigter Anteil 06_Sonstiges, Trefferquote System, KI-Kosten | Steuerung der Klassifikationsqualität und des Kostenlimits |
| 17 | keine gespeicherten Sichten | Tabelle `review_saved_filters` (oder `users.preferences`) | Serienarbeit mit wiederkehrenden Filtern |
| 18 | keine Spaltenprofile für Importe | Tabelle `import_column_profiles` | Vorverwaltung liefert mehrere Objekte im selben Format |

## 10. Annahmen (mit Verifikation)

| Nr. | ANNAHME | Verifikation |
|---|---|---|
| H01 | Antwortzeitziel "unter 2 Sekunden" wird als p95 der Endpunkte Liste, Detail, Vorschaubild, Eigentümersuche und Entscheidung während des Performance-Tests gemessen; p99 unter 4 Sekunden als Warnschwelle | Messprotokoll des Performance-Tests (CR 14) mit Lasttest-Werkzeug und einem simulierten Bearbeiter |
| H02 | Listenansicht 50 Zeilen je Seite | Antwortzeit der Listenabfrage im Performance-Test |
| H03 | Vorschaubilder 1.200 Pixel lange Kante, JPEG, im Mittel 150 KB je Seite, rund 1,5 GB je 10.000 Seiten; Aufbewahrung 90 Tage nach Erledigung | gemessene Dateigrößen und `df -h` im Performance-Test; Konfiguration `previews.retention_days_after_resolve` |
| H04 | Trennung der Queues `io` und `lists` von der OCR-Queue genügt, damit Vorschauen und Listen nicht im Rückstau stehen | Wartezeit der Vorschaujobs im Performance-Test unter 5 Minuten |
| H05 | Gruppierung "gleichartig" über `batch_key` aus Objekt, Herkunft, Kategorie, Unterordner, Unterart, Jahr trifft die Arbeitsweise | Anteil der Gruppen ohne Einzelkorrektur nach drei Objekten über 80 Prozent |
| H06 | höchstens 500 Fälle je Sammelaktion | Laufzeit im Test mit 500 synthetischen Fällen unter 60 Sekunden |
| H07 | Zurückstellen standardmäßig 7 Tage | Rückmeldung der Sachbearbeiter nach dem ersten Monat |
| H08 | Sachbearbeiter sehen alle Objekte | Rollenentscheidung des Auftraggebers (Frage 12) |
| H09 | Übernahmezeitraum ohne `takeover_from`: 3 abgeschlossene Wirtschaftsjahre plus laufendes Jahr | Vorgabe der Geschäftsführung (Frage 1) |
| H10 | Einzelabrechnung Jahr Y erwartbar ab 30.06. des Folgejahres, davor "noch nicht fällig" | Vorgabe der Geschäftsführung, gegebenenfalls Steuerberater (Frage 1) |
| H11 | Wirtschaftsplan für das Folgejahr wird erwartet, wenn der Stichtag im letzten Quartal des Wirtschaftsjahres liegt | Rückmeldung der Sachbearbeiter nach den ersten Objekten |
| H12 | Kommunikationsdaten erfüllt bei mindestens einem Kanal | Vorgabe des Auftraggebers (Frage 2) |
| H13 | Objektstatus `mostly_complete` ab 90 Prozent erfüllter anwendbarer Prüfpunkte ohne blocking-Mangel | Rückmeldung der Sachbearbeiter nach drei Objekten, Konfiguration `completeness.mostly_complete_pct` |
| H14 | Bewertungslauf unter 5 Sekunden je Objekt mit 100 Einheiten und 4 Jahren | Integrationstest mit synthetischem Objekt |
| H15 | Einzelaufstellung der Nachforderung ab 25 Positionen als Anlage | Lesbarkeit der ersten realen Schreiben |
| H16 | Freigaberolle für Nachforderungen ist Admin (Geschäftsführung) | Frage 5 |
| H17 | Listen-Entprellung 60 Sekunden (aus F) | Anzahl `list_generations` je Objekt und Tag im ersten Betriebsmonat |
| H18 | PDF-Ränder 12 mm seitlich, Tabellenschrift 8 pt reicht für alle 23 Spalten in ein bis zwei Textzeilen | Rendering-Test mit den längsten Werten der Testdaten; Bestätigung der CI-Abweichung (Frage 6) |
| H19 | OCR-Zellen mit mittlerer Wortkonfidenz unter 70 gelten als unsicher | Stichprobe der ersten gescannten Listen |
| H20 | Spaltenzuordnung ohne Markierung ab Konfidenz 0,80 | Anteil manueller Korrekturen in den ersten zehn Importen |
| H21 | Namens-Splitter: Übernahme ab 0,90, Markierung 0,60 bis 0,89, darunter unsicher | Anteil korrigierter Splits in den ersten fünf Importen, Konfiguration `import.name_split_thresholds` |
| H22 | Präfixlose Einheitennummer in WEG-Objekten als Wohnung mit mittlerer Konfidenz | Review-Entscheidungen der ersten Importe (Frage 8) |
| H23 | Übernahme je Zeile unter 100 ms, 1.000 Zeilen unter zwei Minuten | Integrationstest mit 1.000 synthetischen Zeilen |
| H24 | Suchvolumen bis 2,5 Millionen Seiten je Jahr als Planungsgröße | Zeilenzahl `document_pages` je Quartal |
| H25 | InnoDB-Volltext mit relationalem Filter p95 unter 2 Sekunden | Suchabfragen im Performance-Test bei laufender Verarbeitung; sonst Suchdienst als Folge-CR |
| H26 | Kein Review-Fall älter als 10 Arbeitstage als Zielwert | Vorgabe der Geschäftsführung |
| H27 | Fuzzy-Schwellen 90 automatisch, 78 bis 89 Kandidat (übernommen aus E, A05) | Review-Entscheidungen der ersten zwei Objekte |

## 11. Offene Fragen an den Auftraggeber (gebündelt)

1. Übernahmezeitraum und Fälligkeit: Wie viele Wirtschaftsjahre vor dem Stichtag gehören standardmäßig zum Übernahmezeitraum (Vorschlag 3 plus laufendes Jahr)? Ab welchem Datum im Folgejahr gilt eine Einzelabrechnung als erwartbar (Vorschlag 30.06.)? Soll der Wirtschaftsplan für das Folgejahr erwartet werden, wenn der Stichtag im letzten Quartal liegt?
2. Prüfregeln: Gilt "Kommunikationsdaten" als erfüllt bei einem Kanal (E-Mail oder Telefon oder Mobil) oder sind mehrere Pflicht? Sollen die Objektfindings "Aufstellung oder Negativerklärung" für SEPA, Sonderumlagen, Zahlungsvereinbarungen und Mahnverfahren standardmäßig in die Nachforderung, und wer darf sie manuell schließen (Sachbearbeiter oder nur Admin)?
3. Mietverwaltung: Ist der vorgeschlagene Mietkatalog (3.6) als Minimalumfang in Ordnung? Bei "WEG mit SE-Verwaltung": Wie wird je Einheit erkannt, ob Sondereigentumsverwaltung besteht (manuelles Kennzeichen oder aus dem Export)?
4. Basis 01 bis 04: Existieren CR-01 bis CR-04, ein Lastenheft oder Vorlagen, aus denen Unterstrukturen und Prüfpunkte für 01_Legitimationsunterlagen, 02_Stammakte, 03_Buchhaltung und 04_Mieterakte hervorgehen? Falls nein: Sollen die aus CR 6 abgeleiteten Kandidaten (3.7) als erste Fassung in den Katalog?
5. Nachforderung: Existiert eine Word-Vorlage im HVM-CI, oder wird sie aus den CI-Werten neu erstellt und von Ihnen freigegeben? Wer gibt frei (Rolle Admin gleich Geschäftsführung)? Soll der Entwurf ohne Unterschriftsbild und die freigegebene Fassung mit Unterschriftsbild erzeugt werden? Ablage der freigegebenen Fassung in Drive unter 02_Stammakte, oder nur in der Anwendung? Sind die Seed-Textbausteine vor Produktivstart von Ihnen zu prüfen (empfohlen)?
6. Listen: Ist die CI-Abweichung auf 8 pt in der PDF-Tabelle akzeptabel, oder sollen zwei Teiltabellen mit Schlüsselspalten gedruckt werden? Darf "Kaution (Betrag, Anlageform)" als zwei Spalten erscheinen? Soll ein ausgeblendetes Blatt "Herkunft" je Feld in die Excel-Datei?
7. Sonderumlagen: Woran soll die Anwendung erkennen, ob im Übernahmezeitraum Sonderumlagen beschlossen wurden (manuelles Kennzeichen am Objekt nach Lesen der Beschlusssammlung, Vorschlag)? Ist eine neue Unterart "Sonderumlage" unter 04_Hausgeld in Ordnung?
8. Import: Bedeutung der Immoware24-Spalte "Miete_EUR_mtl" (Kaltmiete oder Gesamtmiete)? Umgang mit Zeilen im Status abrechnung, archiv, technisch (nicht übernehmen, als inaktive Einheit übernehmen)? Präfixlose Einheitennummern als Wohnung behandeln? Liegt eine Beispieldatei eines Domus-Exports vor, um das Profil anzulegen?
9. Externe KI im Import: Darf Stufe 3 (OpenAI oder Anthropic) zur Strukturierung gescannter Eigentümerlisten eingesetzt werden? Diese Listen enthalten Namen und Anschriften; Bankdaten würden maskiert. Ohne Freigabe bleibt es bei OCR und Layoutanalyse ohne externe KI.
10. Review Center: Ist der Bestandsexport Immoware24 (Stand 01.07.2026) als erster Import gewünscht (bereits in D gefragt, hier wegen des Parserprofils erneut gebündelt)? Sollen Massenbestätigungen auch bei Fällen mit Konfidenz über der automatischen Schwelle möglich sein (Stichprobenprüfung), oder nur bei Fällen unterhalb?
11. Reporting: Zielwert für das Alter offener Review-Fälle (Vorschlag 10 Arbeitstage)? Soll neben dem CR-Wert "Anteil 06_Sonstiges" der bereinigte Anteil ohne Dubletten und Nicht-Objektbezogenes ausgewiesen werden?
12. Rollen: Sehen Sachbearbeiter alle Objekte und alle Eigentümerakten, oder ist eine Zuweisung von Objekten je Sachbearbeiter gewünscht (CR 10: "Eigentümerakten nur für berechtigte Rollen sichtbar")?

## 12. Testkatalog für diesen Entwurf (Ergänzung zu CR 14)

| Bereich | Test | Erwartung |
|---|---|---|
| Review Center | Jede Aktion aus 2.4 gegen einen synthetischen Fall | `review_decisions`, `review_cases.status`, `audit_events` in einer Transaktion; Verschiebungsjob angelegt, kein Drive-Aufruf in der Anfrage |
| Review Center | Historische Zuordnung: Dokument 03/2025, Wechsel 01.07.2026 (CR 14) | Kandidatenliste bevorzugt Alteigentümer; Auswahl des Neueigentümers erzeugt Warnung und wird protokolliert |
| Review Center | Massenbearbeitung mit 40 Segmenten, davon 2 mit Kandidaten und 1 mit unbekannter Einheit | 37 sofort bestätigbar, 2 nach Auswahl, 1 ausgeschlossen; 39 Entscheidungen mit `is_bulk = 1` und gemeinsamem `bulk_key`; genau eine Listenerzeugung |
| Review Center | Antwortzeit unter Last (CR 14) | p95 unter 2 Sekunden für die fünf Endpunkte während des 10.000-Seiten-Laufs |
| Requirement Engine | Je Prüfpunkt Fixture mit den Zuständen missing, partial, fulfilled, not_applicable | Bewerter liefert erwarteten Status und Nachweis |
| Requirement Engine | Zeitraumlogik: Stichtag 01.07.2026, Kalenderjahr, Zeitraum 3 Jahre | Jahre 2023 bis 2026; Abrechnung 2025 vor 30.06.2026 "noch nicht fällig", danach erwartet |
| Requirement Engine | Idempotenz | zweiter Lauf ohne Datenänderung ändert nur `last_evaluated_at` |
| Nachforderung | Erzeugung ohne Frist | Wasserzeichen ENTWURF, Status kann nicht auf reviewed wechseln |
| Nachforderung | Freigabe | PDF ohne Wasserzeichen, DOCX und PDF mit identischem Positionsbestand, `audit_events` mit Freigebendem |
| Nachforderung | Textbausteinversion | Änderung eines Bausteins ändert bestehende Schreiben nicht |
| Listen | Zwei Läufe (CR 14) | genau eine Datei je Format in Drive, zweite Generation `done` oder `skipped_unchanged` |
| Listen | Eigentümerwechsel | Alteigentümer im Blatt Historie mit Eigentumsende, Neueigentümer in Aktuell |
| Listen | IBAN-Prüfung | regulärer Ausdruck über alle Zellen und den PDF-Text liefert keinen Treffer; Test mit absichtlich vollständiger IBAN in `notes` bricht die Erzeugung ab |
| Listen | Formatierung | Tabellenobjekt mit Filter, `freeze_panes` A6, Beträge numerisch mit zwei Nachkommastellen, Datumszellen als Datum |
| Import | Namens-Splitter: alle Fälle aus 6.4 | erwartete Personen, Typen, Konfidenzbänder |
| Import | Einheitennormalisierung: alle Muster aus Befund 4 | erwartete `unit_number`, `unit_type`, `location`; "WE 14" und "WE14" gleiche Einheit |
| Import | Immoware24-Profil mit synthetischer Datei in Originalspaltenstruktur | Zuordnung aller Spalten ohne manuelle Korrektur; Zeilen anderer Objekte und inaktiver Status als "nicht übernehmen" protokolliert; `rows_total` gleich Summe der Statuswerte |
| Import | Konflikt mit bestehender Zuordnung | keine Überschreibung; F6 mit drei Optionen; jede Option erzeugt die erwarteten Zeilen |
| Import | Scan-Liste mit niedriger OCR-Konfidenz | betroffene Zellen unsicher, Zeilen F6, nichts verworfen |
| Suche | "WE 14" und "WE14", Umlautvarianten | gleiche Treffer; Volltexttreffer mit Seitenangabe; keine IBAN im Index (Prüfabfrage auf `document_pages`) |
| Reporting | KPI Anteil 06_Sonstiges | brutto und bereinigt nachrechenbar aus `document_classifications` (D 12.4) |
