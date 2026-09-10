# Admin-Konfiguration (app_settings)

Stand: 10.09.2026, erzeugt aus `src/apps/config/catalog.json` und `db/seeds/app_settings.json` (Definition of Done CR 14: Namensmuster Objektordner, Unterstruktur, Schwellwerte, Duplikat-Option, KI-Provider, Aufbewahrungsfristen). Änderungen erfolgen in der Anwendung unter Konfiguration (Recht `settings.write`), jede Änderung steht im Audit (`setting.update`). Werte wirken ohne Neustart; Ausnahmen stehen in der Spalte Wirkung. Seed-Werte sind Vorschläge (ANNAHME), sofern die Quelle nichts anderes sagt.

Schlüssel: 117 in 18 Gruppen.

## Google Drive und Objektordner (`drive.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `drive.backoff` | object | `{"base_s": 1, "factor": 2, "max_s": 64, "attempts": 8}` | Exponentieller Backoff: base_s, factor, max_s, attempts | ANNAHME A15 | object |
| `drive.legacy_conflict_rename_pattern` | string | `leer` | Umbenennungsmuster für einen Altordner, wenn Alt- und Zielordner gleichzeitig existieren; leer bedeutet Review-Fall | Frage F24 | ['string', 'null'] |
| `drive.legacy_folder_aliases` | object | siehe db/seeds/app_settings.json (Altbezeichnung nur dort) | Zuordnung Zielordner zu Altbezeichnungen, die beim Abgleich umbenannt werden (CR 9.3); einzige Fundstelle der Altbezeichnung | CR 9.3, Befund 6.4 | object |
| `drive.max_requests_per_second` | integer | `5` | Clientseitige Ratenbegrenzung je Prozess | ANNAHME A15 | min 1, max 50 |
| `drive.object_folder_name_pattern` | string | `"{number} {city}, {street} {house_number}"` | Namensmuster neuer Objektordner | CR 2 | string |
| `drive.object_number_digits_max` | integer | `6` | Größte Stellenzahl der führenden Objektnummer | Befund 4 | min 1, max 8 |
| `drive.object_number_digits_min` | integer | `2` | Kleinste Stellenzahl der führenden Objektnummer | Befund 4 | min 1, max 8 |
| `drive.object_number_separators` | list | `[" ", "_", ",", ".", "-"]` | Zeichen, die die Objektnummer vom Rest des Ordnernamens trennen | Befund 4 | array |
| `drive.object_number_zero_pad_to` | integer | `leer` | Nullauffüllung der Objektnummer bei Neuanlage; null bedeutet Ist-Nummer | Frage F4 | min 2, max 6 |
| `drive.protocol_folder_id` | string | `leer` | Optionaler Drive-Ordner für Abgleichsprotokolle | Frage F13 | ['string', 'null'] |
| `drive.reconcile_on_open_min_interval_minutes` | integer | `15` | Drosselung des Abgleichs beim Öffnen eines Objekts | ANNAHME A15 | min 0 |
| `drive.resumable_threshold_bytes` | integer | `5242880` | Ab dieser Größe resumable Upload | ANNAHME A15 | min 262144 |
| `drive.root_drive_id` | string | `leer` | Drive-ID bei geteilter Ablage; null bedeutet Meine Ablage | Befund 6.6 | ['string', 'null'] |
| `drive.root_folder_id` | string | `leer` | Folder-ID des Wurzelpfads Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten, bei Einrichtung bestätigt | CR 2 | ['string', 'null'] |
| `drive.upload_chunk_bytes` | integer | `8388608` | Blockgröße des resumable Uploads (Vielfaches von 256 KiB) | ANNAHME A15 | min 262144 |

## Einheiten (`units.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `units.normalize_strip_leading_zeros` | boolean | `true` | Führende Nullen im Vergleichsschlüssel entfernen | Vorschlag H | boolean |
| `units.type_prefix_mapping` | object | `{"WE": "apartment", "WOHNUNG": "apartment", "GE": "commercial", "S": "parking...` | Präfix der Einheitenbezeichnung zu Einheitentyp | Befund 4 | object |

## Eigentümerakte (`owner_file.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `owner_file.collision_suffix_mode` | string | `"year_then_counter"` | Suffix bei Namenskollision | Vorschlag F | year_then_counter, counter |
| `owner_file.create_folders_eagerly` | boolean | `false` | Aktenordner sofort für alle Einheiten anlegen statt bei erster Ablage | Frage F24 | boolean |
| `owner_file.legal_form_tokens` | list | `["GmbH", "AG", "KG", "GmbH & Co. KG", "UG", "e.V.", "GbR", "OHG", "eG", "SE",...` | Rechtsformkürzel, die aus Firmennamen entfernt werden | Vorschlag F | array |
| `owner_file.name_max_length` | integer | `100` | Höchstlänge eines Aktennamens | ANNAHME A16 | min 20, max 255 |
| `owner_file.name_max_names` | integer | `3` | Höchstzahl Namen im Aktennamen, danach Überlaufsuffix | CR 4 | min 1, max 5 |
| `owner_file.name_overflow_suffix` | string | `"ua"` | Suffix bei mehr Namen als erlaubt | CR 4 | string |
| `owner_file.name_separator` | string | `"-"` | Trenner zwischen Nachnamen | CR 4 | string |
| `owner_file.name_unassigned` | string | `"Unzugeordnet"` | Aktenname ohne Eigentümer und Einheit | CR 4 | string |
| `owner_file.name_unknown_owner` | string | `"Unbekannt"` | Namensteil bei bekannter Einheit ohne Eigentümer | Frage F7 | string |
| `owner_file.name_unknown_unit_prefix` | string | `"Unbekannte_WE"` | Präfix der Akte bei unbekannter Einheit | CR 4 | string |
| `owner_file.transliterate_umlauts` | boolean | `false` | Umlaute in Aktennamen transliterieren | Frage F5 | boolean |
| `owner_file.unit_number_pad` | integer | `2` | Stellen der Einheitennummer im Aktennamen | CR 4 | min 1, max 4 |
| `owner_file.unit_prefix_map` | object | `{"apartment": "WE", "commercial": "GE", "parking": "ST", "garage": "GA", "und...` | Präfix je Einheitentyp im Modus by_type | Frage F6 | object |
| `owner_file.unit_prefix_mode` | string | `"always_we"` | always_we (CR-Wortlaut) oder by_type (Präfix nach Einheitentyp) | Frage F6 | always_we, by_type |

## Mieterakte (`tenant_file.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `tenant_file.subfolders` | list | `[]` | Unterordner der Mieterakte; leer bis Frage F10 | Befund 6.3, Frage F10 | array |

## Dokumente und Ablage (`documents.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `documents.duplicate_name_pattern` | string | `"{original_stem}_S{page_from}-{page_to}.pdf"` | Dateiname der Teilkopie | CR 6 | string |
| `documents.duplicate_owner_documents_in_drive` | boolean | `false` | Physische Zweitablage von Einzelteilen in der Eigentümerakte (CR 6) | CR 6 | boolean |
| `documents.max_download_bytes` | integer | `524288000` | Größenlimit je Datei für den Download | ANNAHME A14 | min 1048576 |

## Klassifikation und Schwellwerte (`classification.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `classification.ai_sample_pct` | integer | `10` | Stichprobe der KI-Entscheidungen für das Review Center in Prozent | ANNAHME A11, Frage F17 | min 0, max 100 |
| `classification.bonus_agree` | decimal | `0.05` | Bonus bei Übereinstimmung der Stufen | ANNAHME A11 | min 0, max 0.5 |
| `classification.contract_partners` | list | `[]` | Vertragspartner (Versicherer, Dienstleister, Versorger, Vorverwaltung) als Marker für Stammakte und Belege | E 2.1 | array |
| `classification.embedding_switch_macro_f1` | decimal | `0.85` | Unter dieser Makro-F1 wird der Umstieg auf ein Embedding-Modell geprüft | ANNAHME A13 | min 0, max 1 |
| `classification.first_pages` | integer | `3` | Anzahl erster Seiten (plus letzte Seite) als Eingabe für Regeln und Klassifikator | ANNAHME A-16 | min 1, max 20 |
| `classification.fuzzy_auto` | integer | `90` | Unscharfer Namensabgleich: automatischer Treffer ab | ANNAHME A12 | min 50, max 100 |
| `classification.fuzzy_candidate_min` | integer | `78` | Unscharfer Namensabgleich: Kandidat ab | ANNAHME A12 | min 50, max 100 |
| `classification.gap_factor` | decimal | `0.5` | Gewicht des Abstands zur zweiten Klasse ohne Regeltreffer | ANNAHME A11 | min 0, max 1 |
| `classification.label_weights` | object | `{"rule": 0.6, "synthetic": 0.3, "ai": 0.5, "review": 1.0}` | Gewichte der Trainingsquellen rule, synthetic, ai, review | ANNAHME A13 | object |
| `classification.malus_disagree` | decimal | `0.25` | Abzug bei Widerspruch der Stufen | ANNAHME A11 | min 0, max 1 |
| `classification.mask_id_documents_block_stage3` | boolean | `true` | Dokumente mit erkannter Ausweiskopie gehen nie an Stufe 3 | Frage F19 | boolean |
| `classification.ner_support_bonus` | decimal | `0.05` | Bonus auf die Regelkonfidenz einer 05-Regel, wenn Eigentümer oder Einheit erkannt sind (Stufe 2 in der Kaltstartphase) | E 7.1 NER-Stütze, Ergänzung M6 | min 0, max 0.2 |
| `classification.own_company_names` | list | `["Hausverwaltung Müller GmbH", "Müller Holding AG"]` | Eigene Firmen (Verwalterin) für die Regel Verwaltervollmacht und Verwaltervertrag | E 2.1, CR 5 Zeile 10 | array |
| `classification.retrain_after_new_labels` | integer | `50` | Nachtraining nach so vielen neuen Review-Labels | ANNAHME A13 | min 1 |
| `classification.retrain_f1_tolerance` | decimal | `0.01` | Zulässiger Rückgang der Makro-F1 bei automatischer Aktivierung | ANNAHME A13 | min 0, max 0.2 |
| `classification.segment_types` | list | `["gesamtjahresabrechnung", "gesamtwirtschaftsplan", "versammlungsprotokoll"]` | Dokumentunterarten, die auf eingebettete Einzelteile untersucht werden (E 6.3) | E 6.3 | array |
| `classification.segments_require_review` | boolean | `true` | Segmente von Gesamtdokumenten als Review-Fälle in einer Gruppe vorlegen (Massenbestätigung); bei false werden eindeutige Segmente automatisch bestätigt (E 6.3) | H 2.5, Umsetzungsplan M7 | boolean |
| `classification.stage2_conflict_p` | decimal | `0.9` | Wahrscheinlichkeit, ab der Stufe 2 einer harten Regel widersprechen darf | ANNAHME A11 | min 0.5, max 1 |
| `classification.stage2_min_samples_per_class` | integer | `15` | Beispiele je Klasse, ab denen der lokale Klassifikator scharf ist | ANNAHME A13 | min 1 |
| `classification.stage3_max_tokens` | integer | `3000` | Höchstzahl Eingabetoken des Textauszugs für Stufe 3 | CR 7, ANNAHME A17 | min 200, max 20000 |
| `classification.text_max_chars` | integer | `4000` | Kürzung des Eingabetexts für den lokalen Klassifikator in Zeichen | E 3.3 | min 500, max 50000 |
| `classification.threshold_auto_file` | decimal | `0.9` | Konfidenz ab der automatisch abgelegt oder verschoben wird | ANNAHME A11 | min 0.5, max 1 |
| `classification.threshold_stage3_call` | decimal | `0.9` | Unterhalb dieser Konfidenz wird Stufe 3 aufgerufen | ANNAHME A11 | min 0, max 1 |
| `classification.threshold_stage3_override` | decimal | `0.9` | Konfidenz, ab der Stufe 3 die lokale Kategorie überschreibt | ANNAHME A11 | min 0.5, max 1 |

## KI-Provider (Stufe 3) (`ai.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `ai.chars_per_token` | decimal | `3.5` | Näherung Zeichen je Token für die Kürzung | ANNAHME A17 | min 1, max 10 |
| `ai.max_input_tokens` | integer | `3000` | Höchstzahl Eingabetoken | ANNAHME A17 | min 200 |
| `ai.monthly_budget_eur` | object | `{"openai": null, "anthropic": null}` | Optionaler Monatsdeckel je Provider, nur Alarmschwelle | Vorschlag G, Frage F17 | object |
| `ai.price_list` | object | `{"version": "nicht-festgelegt", "models": {}}` | Versionierte Preisliste je Modell in EUR je 1.000 Token (input_per_1k, output_per_1k), vom Auftraggeber freigegeben; Grundlage von ai_calls.cost_eur | E 4.2, B-07 | object |
| `ai.provider_order` | list | `["openai", "anthropic"]` | Reihenfolge Primär und Fallback | Frage F17 | array |
| `ai.providers` | object | `{"openai": {"enabled": false, "model": null, "endpoint": null, "region": null...` | Je Provider: enabled, model, endpoint, region, timeout_s, max_attempts, cost_limit_eur_per_object; enabled erst nach AVV-Freigabe | CR 0.1, ANNAHME A17, Frage F17 | object |
| `ai.reclassify_enabled` | boolean | `false` | Nachklassifikationslauf für Dokumente in 06/01_Unklar mit Grund KI nicht verfügbar oder Kostenlimit (manuell über Kommando ai_reclassify, zeitgesteuert nach F17) | E 4.2, F17 | boolean |
| `ai.store_masked_prompts` | boolean | `false` | Maskierte Prompts zur Fehlersuche speichern | Frage F17 | boolean |
| `ai.wall_budget_s` | integer | `120` | Gesamtzeitbudget je Dokument für Stufe 3 | ANNAHME A17 | min 5 |

## Aufbewahrungsfristen (`retention.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `retention.hint` | string | `"durch Geschäftsführung und Steuerberater festzulegen"` | Hinweistext für Aufbewahrungsfristen; Werte je Kategorie in retention_policies, Standard leer | CR 15 | string |

## Review Center (`review.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `review.bulk_max_cases` | integer | `500` | Höchstzahl Fälle je Massenvorgang | ANNAHME A25 | min 1, max 5000 |
| `review.list_page_size` | integer | `50` | Zeilen je Seite | ANNAHME A25 | min 10, max 500 |
| `review.snooze_default_days` | integer | `7` | Standarddauer beim Zurückstellen | ANNAHME A25 | min 1 |

## Import (`import.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `import.column_confidence_min` | decimal | `0.8` | Spaltenzuordnung ohne Markierung ab dieser Konfidenz | ANNAHME A28 | min 0, max 1 |
| `import.column_synonyms` | object | `{"unit_label": ["WE", "Einheit", "Whg", "Wohnung", "Nr", "Nr.", "VE", "VE-Bes...` | Synonymwörterbuch der Spaltenzuordnung: Zielfeld zu Überschriften der Vorverwaltungen (H 6.3), administrativ erweiterbar | H 6.3, Befund 4 | object |
| `import.deposit_type_synonyms` | object | `{"cash_account": ["Kautionskonto", "Konto", "Sparkonto", "Bar", "Barkaution",...` | Synonyme der Kautionsanlageform zu leases.deposit_type (H 6.9) | H 6.9 | object |
| `import.legal_form_markers` | list | `["GmbH", "AG", "KG", "OHG", "UG", "e.V.", "GbR", "Stiftung", "Genossenschaft"...` | Marker für Firmen und Gemeinschaften im Namens-Splitter (kein Split); Eheleute, Ehepaar, Familie ergeben zwei Personen ohne Vornamen | H 6.4 | array |
| `import.name_split_thresholds` | object | `{"high": 0.9, "medium": 0.6}` | Konfidenzbänder des Namens-Splitters: high, medium | ANNAHME A28 | object |
| `import.ocr_cell_confidence_min` | integer | `70` | Wortkonfidenz, unter der eine Zelle als unsicher gilt | ANNAHME A28 | min 0, max 100 |
| `import.owner_match_thresholds` | object | `{"auto": 90, "candidate": 78}` | Schwellen des Gesamtscores: auto (bestehenden Eigentümer verwenden), candidate (Kandidatenliste, F6); darunter neuer Eigentümer; ANNAHME A-19 | A-19 | object |
| `import.owner_match_weights` | object | `{"same_unit": 15, "same_address": 8, "same_email": 15, "same_iban_hash": 15, ...` | Gewichte der Signale des unscharfen Eigentümerabgleichs (H 6.6): Zuschläge auf die Namensähnlichkeit; ANNAHME A-19 | H 6.6, A-19 | object |
| `import.unit_status_active_values` | list | `["aktiv"]` | Statuswerte der Quelle, die zur Übernahme vorgeschlagen werden (Immoware24: aktiv); andere erhalten Grund unit_status_not_active (F11) | H 6.2.1, F11 | array |

## Listen (`lists.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `lists.debounce_seconds` | integer | `60` | Entprellung nach Review-Bestätigungen | ANNAHME A26 | min 0 |
| `lists.include_provenance_sheet` | boolean | `false` | Blatt Herkunft in der Excel-Datei | Frage F21 | boolean |
| `lists.owner_columns` | list | `["Einheit", "Einheitentyp", "Anrede", "Vorname", "Nachname", "Firma", "Straße...` | Spalten der Eigentümerliste (Mindestumfang CR 12a, erweiterbar) | CR 12a | array |
| `lists.owner_list_name_pattern` | string | `"00_Eigentuemerliste_{number}.{ext}"` | Dateiname der Eigentümerliste | CR 12a | string |
| `lists.pdf_table_font_pt` | integer | `8` | Tabellenschrift der PDF-Listen in Punkt | Frage F21 | min 6, max 11 |
| `lists.restore_position` | boolean | `true` | Manuell verschobene Listen automatisch zurückführen | Frage F24 | boolean |
| `lists.tenant_columns` | list | `["Einheit", "Einheitentyp", "Anrede", "Vorname", "Nachname", "Firma", "Telefo...` | Spalten der Mieterliste (Mindestumfang CR 12a, erweiterbar) | CR 12a | array |
| `lists.tenant_list_name_pattern` | string | `"00_Mieterliste_{number}.{ext}"` | Dateiname der Mieterliste | CR 12a | string |

## Vollständigkeit und Nachforderung (`completeness.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `completeness.contact_channels_required` | integer | `1` | Kommunikationskanäle, ab denen der Prüfpunkt erfüllt ist | ANNAHME A27, Frage F22 | min 1, max 3 |
| `completeness.default_period_years` | integer | `3` | Abgeschlossene Wirtschaftsjahre im Übernahmezeitraum ohne Angabe | ANNAHME A27, Frage F22 | min 1, max 10 |
| `completeness.mostly_complete_pct` | integer | `90` | Prozentsatz für die Einstufung weitgehend vollständig | ANNAHME A27 | min 50, max 100 |
| `completeness.statement_expected_after` | string | `"30.06."` | Tag und Monat, ab dem die Einzelabrechnung des Vorjahres erwartet wird (TT.MM.) | ANNAHME A27, Frage F22 | string |
| `requests.attachment_threshold` | integer | `25` | Ab dieser Anzahl Positionen wandert die Einzelaufstellung der Nachforderung in eine Anlage | ANNAHME (H 4.2 Nr. 6) | min 1, max 500 |

## Berichte (`reports.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `reports.misc_share_target_pct` | integer | `5` | Zielwert Anteil 06_Sonstiges je Objekt in Prozent | CR 8 | min 0, max 100 |
| `reports.review_age_warning_days` | integer | `10` | Warnung ab diesem Alter offener Review-Fälle in Arbeitstagen | ANNAHME A25, Frage F22 | min 1 |

## Verarbeitung (`processing.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `processing.heartbeat_seconds` | integer | `30` | Abstand der Heartbeats laufender Jobs in Sekunden (ANNAHME A-13) | A-13 | min 5 |
| `processing.max_parallel_objects` | integer | `1` | Gleichzeitig verarbeitete Objekte | ANNAHME A20, Frage F18 | min 1, max 4 |
| `processing.work_orphan_hours` | integer | `48` | Frist, nach der verwaiste Arbeitsverzeichnisse unter work/ ohne aktiven Job geräumt werden (E 10.4 Punkt 4); ANNAHME | E 10.4 | min 1 |

## OCR (`ocr.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `ocr.chunk_pages` | integer | `20` | Seiten je OCR-Block | ANNAHME A14 | min 1, max 200 |
| `ocr.digital_alnum_ratio` | decimal | `0.6` | Mindestanteil alphanumerischer Zeichen je Digitalseite | ANNAHME A14 | min 0, max 1 |
| `ocr.digital_min_chars` | integer | `50` | Mindestzeichen je Seite für die Einstufung als Digitalseite | ANNAHME A14 | min 1 |
| `ocr.dpi_cap` | integer | `300` | Höchstauflösung der Rasterung | ANNAHME A14 | min 150, max 600 |
| `ocr.image_cover_ratio` | decimal | `0.9` | Anteil der Seitenfläche, ab dem ein einzelnes Bild als seitenfüllend gilt (Kriterium 3 in E 1.3); ANNAHME A-11 | E 1.3 | min 0.5, max 1 |
| `ocr.language` | string | `"deu"` | Tesseract-Sprachkürzel für ocrmypdf (CR 0.1: deu) | CR 0.1 | string |
| `ocr.tessdata_variant` | string | `"standard"` | Sprachdatenvariante standard oder fast | Frage F18 | standard, fast |
| `ocr.two_phase_enabled` | boolean | `false` | Zwei-Phasen-OCR als Stellhebel | Frage F18 | boolean |
| `ocr.two_phase_head_pages` | integer | `3` | Seiten der Phase A | Frage F18 | min 1, max 20 |

## Vorschaubilder (`previews.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `previews.jpeg_quality` | integer | `80` | JPEG-Qualität der Seitenbilder | ANNAHME A18 | min 40, max 95 |
| `previews.long_edge_px` | integer | `1200` | Lange Kante der Seitenbilder | ANNAHME A18 | min 600, max 3000 |
| `previews.retention_days_after_resolve` | integer | `90` | Aufbewahrung der Seitenbilder nach Erledigung in Tagen | ANNAHME A18, Frage F26 | min 1 |

## Jobs (`jobs.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `jobs.max_attempts_default` | integer | `3` | Versuche je Job | ANNAHME A19 | min 1, max 10 |
| `jobs.stale_minutes` | object | `{"default": 15, "ocr_chunk": 10, "classify": 3, "classify_ai": 5, "file_to_dr...` | Frist je Jobtyp, nach der ein Job ohne Heartbeat zurückgesetzt wird; default für übrige | ANNAHME A19 | object |

## Sicherheit (`security.*`)

| Schlüssel | Typ | Seed | Bedeutung | Quelle | Wertebereich |
|---|---|---|---|---|---|
| `security.iban_decrypt_roles` | list | `[]` | Rollen, die eine IBAN entschlüsseln dürfen; leer bedeutet keine | Beschluss B-18 | array |
| `security.iban_key_version_current` | integer | `1` | Aktive Schlüsselversion für IBAN-Chiffrate | docs/architektur.md 9.6 | min 1 |
| `security.log_document_views` | boolean | `true` | Ansichten und Downloads aus Eigentümerakten als document.view und document.download protokollieren | Frage F15 | boolean |
| `security.store_full_iban` | boolean | `false` | Vollständige IBAN verschlüsselt speichern (sonst nur letzte vier Stellen und Hash) | Frage F16 | boolean |

## Umgebungsvariablen (.env)

Die Werte in `.env` und die Secrets unter `/srv/objektakte/secrets/` sind in docs/betrieb.md Abschnitt 3 und in `.env.example` beschrieben (Formeln zur Dimensionierung in docs/betrieb.md). Änderungen an `.env` brauchen einen Neustart der betroffenen Container (`docker compose up -d`). Dazu gehören Domain, Datenbank- und Redis-Zugang, Ressourcenlimits, `OPENAI_BASE_URL` und `ANTHROPIC_BASE_URL`, `HVM_SIGNATURE_PATH`, `LISTS_AUTO_GENERATE`, `ALERTS_ENABLED`, `ALERT_EMAIL_TO`, `SMTP_*`, `BACKUP_MAX_AGE_HOURS`, `DISK_RESERVE_GB`.

## Hinweise

- Aufbewahrungsfristen (`retention_policies`) sind ohne Werte ausgeliefert und werden von Geschäftsführung und Steuerberater festgelegt (F31); die Statusseite zeigt den Hinweis, solange Werte fehlen.
- Die KI-Provider sind erst nach `ai.providers.<p>.enabled = true` und dokumentierter AVV-Freigabe aktiv (F17, V-10 bis V-13); die Preisliste `ai.price_list` ist leer, bis der Auftraggeber Listenpreise einträgt.
- Textbausteine der Nachforderung (`request_text_blocks`) und der Prüfkatalog (`completeness_checks`) sind Seeds mit eigener Pflege in der Datenbank; im Admin geänderte Textbausteine werden vom Seed nicht überschrieben.
