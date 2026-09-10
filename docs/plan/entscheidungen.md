# Entscheidungen des Auftraggebers

Jede Antwort des Auftraggebers wird hier mit Datum festgehalten und ist danach für den Bau bindend (Umsetzungsplan, Abschnitt 7). Nummern beziehen sich auf docs/umsetzungsplan.md.

| Datum | Nummer | Entscheidung | Wirkung |
|---|---|---|---|
| 10.09.2026 | FG-1, FG-2, FG-3 | Freigabe erteilt („weiter und auch freigabe"): Stack A mit den Auflagen aus Anhang C, Datenmodell D als Bezeichnerquelle mit Ergänzungsmigration, Meilensteinreihenfolge mit den Planänderungen | Umsetzung beginnt mit M1; M0 folgt, sobald der SSH-Zugang zum VPS besteht (V-01, V-03) |
| 10.09.2026 | FG-4, F1 bis F31 | Keine Einzelantworten. Es gelten die im Umsetzungsplan Abschnitt 4 genannten Vorschlagswerte („Ohne Antwort"), bis eine abweichende Antwort mit Nummer eingeht | Seeds, Konfiguration und Compose werden mit den Vorschlagswerten gebaut; spätere Antworten werden per Konfiguration umgesetzt, soweit im Plan so vorgesehen |
| 10.09.2026 | F1 | Umsetzung durch den beauftragten KI-Assistenten in Python und Django; Vertretung nicht benannt | Risiko R-18 bleibt offen |
| 10.09.2026 | FG-5, V-01 bis V-09 | Noch nicht erfüllt: SSH-Zugang, Messcontainer-Freigabe, DNS, technisches Konto, OAuth-App, Test-Wurzelverzeichnis | M0 und M4 verschieben sich, bis die Voraussetzungen vorliegen; alle Serverwerte bleiben Variablen |

## Offene Antworten mit Wirkung auf den Bau

Die folgenden Fragen werden mit dem Vorschlagswert gebaut und lassen sich später umstellen. Sie bleiben zur Beantwortung offen:

- F4 Objektnummer ohne Nullauffüllung, Erkennung 2 bis 6 Stellen
- F5 Ordnernamen wortgetreu mit Umlaut
- F6 Präfix der Eigentümerakten immer WE (CR-Wortlaut), Umstellung auf typabhängig per Konfiguration
- F8 Ablage bei mehrdeutiger Zuordnung nach Entscheidungstabelle docs/architektur.md 6.5
- F10 Requirement Engine und Nachforderung als minimaler Neubau, Unterstrukturen 01 bis 04 leer
- F13 OAuth-App mit den Redirect-URIs aus docs/betrieb.md 7.1 (anzulegen durch den Auftraggeber)
- F16 IBAN nur als letzte vier Stellen und Hash
- F17 Externe KI bis zur AVV-Freigabe deaktiviert
- F18 Entscheidung nach dem OCR-Probelauf in M0

## Ergänzungen aus der Umsetzung (Vorschläge des Umsetzers, gelten bis zu einer abweichenden Entscheidung)

| Datum | Meilenstein | Ergänzung | Begründung und Wirkung |
|---|---|---|---|
| 10.09.2026 | M2 | `review_cases.case_type` um `data_consistency` (Untertyp `assignment_overlap`) | Der nächtliche Konsistenzlauf meldet überlappende Zuordnungen derselben Person auf derselben Einheit; nach B-09 wäre `unclear` falsch, weil ein fachlicher Grund vorliegt. Rückbau: Wert aus CHECK und Task entfernen. |
| 10.09.2026 | M2 | Audit-Aktion `assignment.end` | Beendigung einer Zuordnung (Eigentümerwechsel) wird getrennt vom Anlegen protokolliert. |
| 10.09.2026 | M2 | Codes der 57 Dokumentunterarten (`db/seeds/document_types.json`) | CR 5 und 6 nennen Namen, keine Codes; die Codes sind ab jetzt stabil, weil Regeln, Klassifikator und KI-Schema darauf verweisen (B-12, B-21). Namen folgen dem CR-Wortlaut. Zusätzlich `sonstiges_eigentuemer` als Sammelunterart der Eigentümerakte. |
| 10.09.2026 | M2 | Vergleichsschlüssel der Einheiten ohne führende Nullen (`units.normalize_strip_leading_zeros = true`) | Vorschlag H 6.5; WE01 und WE 1 gelten als dieselbe Einheit. Abschaltbar per Konfiguration, falls eine Vorverwaltung beide Schreibweisen als verschiedene Einheiten führt. |
| 10.09.2026 | M2 | Konsistenzlauf täglich 03:15 Serverzeit (ANNAHME) | Zeitfenster außerhalb der Verarbeitung; endgültige Uhrzeit mit Antwort F27. |
| 10.09.2026 | M2 | Technische Abweichungen von der DDL in D (Typen, Primärschlüssel, CHECK mit AUTO_INCREMENT, FULLTEXT als eigene Migration) | Begründung je Punkt in docs/architektur/datenmodell.md Abschnitt 3; fachlich ohne Wirkung. |
| 10.09.2026 | M3 | Sechs Konfigurationsschlüssel `import.column_synonyms`, `import.legal_form_markers`, `import.owner_match_weights`, `import.owner_match_thresholds`, `import.unit_status_active_values`, `import.deposit_type_synonyms` | Aus H 6.3, 6.4, 6.6, 6.9 und 6.2.1 abgeleitet; Werte sind Vorschläge (A-19, A-20) und zur Laufzeit änderbar. |
| 10.09.2026 | M3 | Audit-Aktionen `import.upload`, `import.parse`, `import.row_commit` | Annahme, Erkennung und Übernahme je Zeile werden getrennt protokolliert; `import.commit` bezeichnet den Lauf. |
| 10.09.2026 | M3 | Leerzeilen und Summenzeilen werden beim Einlesen automatisch mit Grund `not_a_record` abgelehnt | Sie bleiben im Protokoll sichtbar (nichts wird stillschweigend verworfen), erzeugen aber keinen Review-Fall. |
| 10.09.2026 | M3 | Zeilen mit Grund `other_object` oder `unit_status_not_active` erhalten den Vorschlag „nicht übernehmen“ und bleiben unsicher | Entscheidung im Review (F11), gesammelte Ablehnung möglich. |
| 10.09.2026 | M3 | Mehrere Personen einer Quellzeile (z. B. „Mustermann, Erika & Max“) werden bei der Übernahme als Mehrfacheigentum behandelt, kein Konflikt | Die Liste der Vorverwaltung definiert die aktuellen Eigentümer; Konflikte entstehen nur gegen bereits vorhandene Zuordnungen anderer Quellen. |
| 10.09.2026 | M3 | Einheitenbezeichnung endet vor dem Lagezusatz („WE 4 - 1.OG rechts“ ergibt Bezeichnung „WE 4“, Lage „1.OG rechts“) | H 6.5 Schritt 1; die Rohzeile bleibt vollständig erhalten. |
| 10.09.2026 | M3 | Gleiche Personen innerhalb eines Imports (Wohnung und Stellplatz) werden zu einem Eigentümer zusammengeführt (Schlüssel Nachname plus Vorname) | H 6.6 letzter Absatz; Übernahme in Zeilenreihenfolge. |
| 10.09.2026 | M4 | Audit-Aktion `drive.reconcile` | Anstoß eines Ordnerabgleichs aus der Oberfläche wird protokolliert. |
| 10.09.2026 | M4 | Unterordner auf Objektebene nur für Kategorien mit Geltungsbereich Objekt oder Sonstiges | Die elf Unterordner der Eigentümerakte liegen je Akte (F 6.3), nicht direkt unter 05_Eigentümerakte; der Abgleich legt sie mit der Akte an. |
| 10.09.2026 | M4 | Dry-Run setzt `no_changes` aus dem Plan (keine Schreibaktion, kein Review-Fall geplant) | F 4.6 definiert no_changes über ausgeführte Schreibaktionen; der Probelauf braucht denselben Indikator für die Planliste. |
| 10.09.2026 | M4 | Review-Fälle des Abgleichs sind je Objekt, Kategorie und Untertyp einmalig offen (`batch_key drive:<objekt>:<kategorie>:<untertyp>`) | Wiederholte Läufe erzeugen keine doppelten Fälle; nach Erledigung entsteht bei Bedarf ein neuer. |
| 10.09.2026 | M4 | Stündlicher Lesetest um Minute 7, täglicher Refresh 04:00 Serverzeit (ANNAHME) | Zeitpunkte außerhalb der Verarbeitung; endgültig mit F27. |
| 10.09.2026 | M4 | Token-Chiffrate als Base64-Text in den VARBINARY-Spalten (`v1:`-Präfix des FieldCipher) | einheitliches Chiffratformat mit Schlüsselversion für alle Zwecke (docs/architektur.md 9.6). |
| 10.09.2026 | M5 | Versandmodus `JOB_DISPATCH` (`celery` im Betrieb, `none` in Tests und im lokalen Messlauf) | Die Jobtabelle bleibt Quelle der Wahrheit; ohne Celery-Transport führt `apps.pipeline.local.run_pending_jobs` dieselben Tasks aus. Kein Einfluss auf den Betrieb. |
| 10.09.2026 | M5 | Wiederholung eines abgeschlossenen Schritts erhält den Idempotenzschlüssel mit Suffix `#n` | B-03 verhindert doppelte offene Jobs. Läuft ein Vorgänger legitim erneut (Arbeitsverzeichnis geräumt, Dokument erneut geladen), braucht der Folgeschritt einen neuen Job; der Grundschlüssel bleibt erhalten und auswertbar. |
| 10.09.2026 | M5 | Upload legt je Objekt einen Lauf `incremental` an oder hängt sich an den laufenden Lauf | Uploads unterliegen damit der Objektserialität (B-26) und erscheinen mit Warteposition; kein Sonderpfad neben den Läufen. |
| 10.09.2026 | M5 | Review-Fälle der Pipeline: `unclear` mit Untertypen `duplicate` (Vorschlag 03_Dubletten), `file_too_large`, `unsupported_format` (Vorschlag 02_Unlesbar), `job_failed`; `import_candidate` mit Untertyp `owner_list` | Untertypen folgen E 1.2 und B-34; je Dokument höchstens ein offener Fall (`batch_key`). |
| 10.09.2026 | M5 | Audit-Aktionen `document.ingest`, `processing.start`, `processing.sweep` | Upload, Laufstart aus der Oberfläche und manueller Sweeper werden protokolliert. |
| 10.09.2026 | M5 | Konfigurationsschlüssel `ocr.language` (deu), `ocr.image_cover_ratio` (0,9), `processing.work_orphan_hours` (48), `processing.heartbeat_seconds` (30) | A-11 und A-13 als Werte im Register statt im Code; Vorschlagswerte. |
| 10.09.2026 | M5 | Dokumente der Eigentümerakte (Kategorie 05) und Mieterakte (04) sind nur mit `owner_files.read` bzw. `tenant_files.read` einsehbar; Seitenbilder nur über die rechtegeprüfte Route | B-15; vor der Klassifikation (Kategorie leer) gilt der Zugriff für angemeldete Nutzer. |
| 10.09.2026 | M5 | Google-Dokumente (`application/vnd.google-apps.*`) gehen bis M6 mit Fall `unsupported_format` in die Prüfung | Der Export nach PDF braucht den Drive-Exportpfad; kein Datenverlust, Dokument bleibt registriert. |
| 10.09.2026 | Betrieb | Deployment aus GitHub nur manuell (`workflow_dispatch`), Aktionsschlüssel mit erzwungenem Kommando `scripts/deploy_remote.sh` | Deployments im Zeitfenster nach F27 und außerhalb laufender Verarbeitung; ein kompromittierter Schlüssel erlaubt kein Arbeiten auf dem Server. Umstellung auf automatisches Deployment bei Push nach Antwort F27 möglich. |
| 10.09.2026 | M6 | Regeln und Testkatalog als JSON statt YAML | Gleiche Struktur wie E 2.2 (id, version, priority, scope, when, then, examples); vermeidet eine zusätzliche Abhängigkeit im Web-Image. Rückbau: Konverter, wenn YAML gewünscht wird. |
| 10.09.2026 | M6 | `classification_rules` um `definition` (JSON) und `version` erweitert, `rule_kind` um `composite` | Die Tabelle aus D kennt nur einzelne Muster; zusammengesetzte Regeln nach E 2.2 brauchen die vollständige Definition. Zusammenfassung (Ziele, Gewicht, hard) bleibt in den D-Spalten. |
| 10.09.2026 | M6 | NER-Stütze in der Kaltstartphase: weiche 05-Regel plus erkannter Eigentümer oder Einheit ergibt `classification.ner_support_bonus` (0,05) | E 7.1 nennt Eigentümer, Einheit und Zeitbezug als Stützsignale der Stufe 2; ohne Modell trägt Stufe 2 nur diese Signale (B-27). Ohne die Stütze gingen in der Kaltstartphase alle Dokumente weicher Regeln in die Prüfung. |
| 10.09.2026 | M6 | Konfigurationsschlüssel `classification.own_company_names`, `classification.contract_partners`, `classification.first_pages`, `classification.text_max_chars`, `classification.ner_support_bonus`, `classification.segment_types` | Eingangsgrößen aus E 2.1, 3.3 und 6.3 als Werte statt im Code; Vertragspartner zunächst global (je Objekt folgt mit Bedarf). |
| 10.09.2026 | M6 | Dokumentunterarten `verwaltervertrag`, `verwalterbestellung` (01) und `mietvertrag`, `kaution`, `betriebskostenabrechnung`, `mieterkorrespondenz` (04) ergänzt | E 2.3 nennt die Regelgruppen; ohne Codes wären die Regeln nicht abbildbar (B-12). |
| 10.09.2026 | M6 | Dokument mit Fall und Ablage in 06 trägt `category 06` mit dem physischen Unterordner; die fachlich erkannte Kategorie steht in der finalen Klassifikationszeile und im Fall (`context.intended`) | KPI Anteil 06 brutto nach CR-Definition zählt `documents.category = 06` (B-31); der bereinigte Wert nutzt `features.kpi_misc_adjusted` der finalen Zeile. |
| 10.09.2026 | M6 | Benannter Eigentümer schlägt das Dokumentdatum, wenn kein expliziter Zeitraum vorliegt (T21) | E 6.2 P4 stellt Forderungszeitraum und Name über das Dokumentdatum; ein Anwaltsschreiben von 2026 zu Rückständen 2025 gehört zum benannten Alteigentümer. Bei explizitem Zeitraum ohne passende Zuordnung bleibt es beim Fall `owner_candidates`. |
| 10.09.2026 | M6 | Fallart bei Fremdobjekt, Mieterdokument in reiner WEG und WEG-Begriffen in Mietverwaltung: `unclear` mit Untertypen `foreign_object`, `tenant_document_in_weg`, `misplaced_weg_document` und `proposed_action` | B-09 nennt für diese Gründe keine eigene Fallart; der Untertyp trägt den fachlichen Grund, der Vorschlag die Aktion (Übernahme in Objekt NNN, Ablage 08_Korrespondenz). |
| 10.09.2026 | M6 | Dokument mit offenem Fall bleibt nach physischer Ablage im Status `review`, Dubletten behalten `duplicate` | `filed` bezeichnet abgeschlossene Ablagen; der Fall entscheidet über den endgültigen Ort (E 7.3). |
| 10.09.2026 | M6 | Akte Unzugeordnet ohne Zeile in `document_owner_links` | Die Tabelle verlangt einen Bezug (Eigentümer, Einheit oder Zuordnung); die Akte steht im Fall (`context.owner_file_id`) und als Ablageziel. |
| 10.09.2026 | M6 | `processing_runs.misc_share_adjusted_pct` | Bereinigter Anteil 06 nach B-31 als eigene Spalte. |
| 10.09.2026 | M6 | Nachtraining täglich 01:30 Serverzeit (ANNAHME) | Vor dem Konsistenzlauf (03:15) und dem Token-Refresh (04:00); endgültig mit F27. |
| 10.09.2026 | M6 | Übernahme in anderes Objekt: alte Zeile `moved_out` verweist über `duplicate_of_document_id` auf die neue Zeile | Grundform B-29 ohne zusätzliche Spalte; Audit beidseitig. Eigene Spalte, falls das Review Center den Verweis getrennt braucht. |
