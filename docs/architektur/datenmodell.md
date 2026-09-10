# Datenmodell: Umsetzung von Fachentwurf D mit Ergänzungsmigration

Stand: 10.09.2026 (M2). Verbindlich sind die Django-Modelle unter `src/apps/*/models.py` und das daraus erzeugte Bezeichnerregister (docs/architektur/bezeichnerregister.md). Dieses Dokument erklärt die Zuordnung zu Fachentwurf D (docs/entwurf/fach_D_datenmodell.md) und zur Ergänzungsmigration (docs/architektur.md 5.6, Umsetzungsplan Anhang D.7) sowie die bewussten Abweichungen.

## 1. Zuordnung Tabellen zu Modulen

| Bereich D | Tabellen | Modul |
|---|---|---|
| A Stammdaten | objects, units | `apps.objects` |
| A Stammdaten | owners, owner_unit_assignments, owner_files, owner_file_assignments, tenants, leases, tenant_unit_assignments, tenant_files, tenant_file_assignments, field_provenance | `apps.parties` |
| B Katalog | document_categories, document_subfolders, document_types, retention_policies | `apps.documents` |
| B Konfiguration | app_settings | `apps.config` (Label `appconfig`, M1) |
| C Drive | drive_nodes, drive_sync_runs, drive_sync_actions, oauth_tokens | `apps.drive` |
| C Listen | list_generations | `apps.lists` |
| D Dokumente | documents, document_pages, document_entities, document_classifications, document_owner_links, document_tenant_links | `apps.documents` |
| Ergänzung Nr. 1 | classification_rules, classifier_models, training_samples | `apps.documents` |
| E Verarbeitung | processing_runs, processing_jobs, processing_job_events, object_progress (Ergänzung Nr. 4) | `apps.pipeline` |
| F Review | review_cases, review_decisions, review_saved_filters (Ergänzung Nr. 6) | `apps.review` |
| F Audit | audit_events, iban_access_log | `apps.audit` (M1) |
| G Import | import_batches, import_rows, import_column_profiles (Ergänzung Nr. 14) | `apps.imports` |
| H Sicherheit | roles, users | `apps.accounts` (M1); TOTP in `mfa_authenticator` (allauth) |
| I KI | ai_calls | `apps.ai` |
| J Vollständigkeit | completeness_findings, completeness_checks, document_requests, request_text_blocks (Ergänzung Nr. 14) | `apps.requirements` |
| K Schema | django_migrations statt schema_migrations | Django-Migrationsrahmen |

Alle Tabellen- und Spaltennamen entsprechen D (`db_table`, `db_column`); Fremdschlüsselspalten heißen wie in D (`object_id`, `category_code`, `assignment_id`). Die Python-Klasse zu `objects` heißt `ManagedObject`, weil `Object` mit dem Python-Grundtyp kollidiert; der Tabellenname bleibt `objects`.

## 2. Ergänzungsmigration (docs/architektur.md 5.6): Umsetzungsstand

| Nr. | Ergänzung | Umsetzung |
|---|---|---|
| 1 | classification_rules, classifier_models, training_samples | angelegt; Spaltenentwurf in `apps.documents.models` (Regelart, Ziel als Codes, Gewicht; Modellversion mit Metriken; Trainingsbeispiel mit label_source) |
| 2 | documents: sha256 nullable, drive_md5, Status hashed und moved_out, target_drive_node_id, classifier_version, Unique (object_id, drive_file_id) | umgesetzt |
| 3 | processing_jobs.job_type erweitert, Unique idempotency_key | umgesetzt (17 Jobtypen) |
| 4 | object_progress | umgesetzt (Zähler je Objekt) |
| 5 | ai_calls.status erweitert | umgesetzt |
| 6 | review_cases.case_type erweitert, snoozed_until, review_saved_filters | umgesetzt, zusätzlich `data_consistency` (Abschnitt 4) |
| 7 | review_decisions.decision_type um transfer_object | umgesetzt |
| 8 | owner_files.owner_id | umgesetzt (nullable, Pflicht bei unknown_unit wird in der Anwendung geprüft) |
| 9 | Seed document_types aus CR 5 und 6 | `db/seeds/document_types.json`, 57 Unterarten mit stabilen Codes |
| 10 | list_generations.trigger_kind erweitert | umgesetzt |
| 11 | drive_sync_actions.action_type um inventory, id_hash_before, id_hash_after | umgesetzt |
| 12 | objects: Vorverwaltungsanschrift, expected_unit_count, sepa_used, special_levies_in_period, is_test | umgesetzt |
| 13 | owner_unit_assignments.balance_at_takeover, units.se_managed, units.vacancy_confirmed | umgesetzt |
| 14 | completeness_checks, document_requests, request_text_blocks, import_column_profiles, completeness_findings.manual_* | umgesetzt |
| 15 | document_entities.entity_type um id_document_number | umgesetzt |
| 16 | iban_access_log.purpose ohne display_full | umgesetzt (M1) |
| 17 | app_settings ohne owner_file.subfolders und ocr.worker_processes | umgesetzt (M1) |
| 18 | Kommentar 05_Eigentümerakte | Seed trägt den CR-Wortlaut mit Umlaut (F5) |
| 19 | data_status nach Vorrang unvollständig vor KI-Vorschlag vor bestätigt | `apps.parties.services.derive_data_status` |

## 3. Technische Abweichungen von der DDL in D (MariaDB, Django)

| Thema | D | Umsetzung | Grund |
|---|---|---|---|
| Primärschlüssel | BIGINT UNSIGNED | bigint (signed) AUTO_INCREMENT | Django BigAutoField; Wertebereich ausreichend |
| Zeitstempel | DATETIME(3) | datetime(6) | Django-Standard, höhere Auflösung |
| Ganzzahlen UNSIGNED | INT UNSIGNED | integer UNSIGNED mit CHECK >= 0 | Django Positive*Field |
| iban_hash, iban_encrypted | BINARY(32), VARBINARY(160) | varbinary(32), varbinary(160) über `objektakte.db.VarBinaryField` | Django BinaryField wäre LONGBLOB und nicht indexierbar |
| document_entities.iban_hash | BINARY(32) | varchar(64) hex | Vergleich mit HMAC-Hexwert der Maskierung ohne Umwandlung |
| JSON | JSON mit CHECK JSON_VALID | json (MariaDB: longtext mit JSON_VALID) | Django JSONField setzt den CHECK selbst |
| CHECK mit REGEXP | REGEXP '^[0-9]+$' | REGEXP BINARY '^[0-9]+$' | Django-Rendering des regex-Lookups; gleiche Wirkung |
| ck_documents_not_self_dup | CHECK (duplicate_of_document_id <> id) | entfällt, Prüfung in der Anwendung | MariaDB lässt AUTO_INCREMENT-Spalten in CHECK nicht zu |
| FULLTEXT document_pages.text_content | im CREATE TABLE | eigene Migration 0004 mit RunSQL und Rückwärtsoperation | Django kennt keinen FULLTEXT-Index |
| Generierte Spalten | CASE WHEN ... STORED | `GeneratedField(db_persist=True)`; keine generierte Spalte verweist auf eine andere generierte Spalte | Portabilität, Einschränkung in MariaDB |
| Fremdschlüssel | ON DELETE RESTRICT | `on_delete=PROTECT` (Anwendung) plus Datenbank-Standard RESTRICT | Django erzeugt keine ON-DELETE-Klausel |
| Nachtrag zyklischer Fremdschlüssel | Migration 12 in D 13.1 | Migrationen `0002_fremdschluessel_*`, `0003_fremdschluessel_*` je App; generierte Schlüssel und ihre Unique-Constraints folgen nach den Fremdschlüsseln | Django löst Zyklen über Folgemigrationen |
| schema_migrations | eigene Tabelle | django_migrations | Migrationsrahmen des Stacks |
| users.totp_* | Spalten in users | `mfa_authenticator` (allauth), verschlüsselt mit TOTP_KEY | Beschluss M1, docs/architektur.md 9.2 |
| roles.id | SMALLINT UNSIGNED | bigint | einheitliche Schlüssel |

Soft-Delete: `deleted_at`, `deleted_by`, `delete_reason` und generierte `active_key` nach D 1 in objects, units, owners, owner_unit_assignments, owner_files, tenants, leases, tenant_unit_assignments, tenant_files, documents, document_owner_links, document_tenant_links. Der Manager `active` filtert gelöschte Zeilen aus; `objects` bleibt vollständig (Löschläufe, Protokoll).

## 4. Ergänzungen aus M2 (Vorschlag, Kennzeichnung nach Umsetzungsplan Abschnitt 7)

- `review_cases.case_type` um `data_consistency` (Untertyp `assignment_overlap`): Ergebnis des nächtlichen Konsistenzlaufs (Umsetzungsplan M2 Schritt 6). Beschluss B-09 nennt keine Fallart für Stammdatenwidersprüche; `unclear` wäre nach B-09 falsch, weil ein fachlicher Grund vorliegt.
- Audit-Aktion `assignment.end` (Zuordnung beendet, Eigentümerwechsel) ergänzt D.6, damit die Beendigung einer Zuordnung getrennt vom Anlegen protokolliert wird.
- Beat-Task `parties.check_assignment_consistency` täglich 03:15 Serverzeit (ANNAHME, Frage F27), Queue `io`.
- Einheiten-Vergleichsschlüssel entfernt führende Nullen (`units.normalize_strip_leading_zeros = true`, Vorschlag H 6.5): `WE01` und `WE 1` sind dieselbe Einheit. Abschaltbar, falls eine Vorverwaltung beide Schreibweisen als verschiedene Einheiten führt.
- `document_types`: 57 Unterarten (CR 5: 43 für die Eigentümerakte einschließlich Sammelunterart `sonstiges_eigentuemer`; CR 6: 8 Stammakte, 4 Buchhaltung; CR 5 Hinweis: `verwaltervollmacht` in 01; Vorschlag `sonderumlage_einzel`). Codes sind Vorschläge des Umsetzers und ab jetzt stabil; Namen folgen dem CR-Wortlaut. Unterordner der Kategorien 01 bis 04 bleiben leer (F10).

## 5. Fachfunktionen aus M2

| Funktion | Ort | Regel |
|---|---|---|
| Objektnummer-Erkennung | `apps.drive.object_numbers.parse_object_number` | führende Ziffernfolge mit `drive.object_number_digits_min` bis `_max` Stellen, Trenner aus `drive.object_number_separators`; Identität über den Zahlenwert |
| Einheitennormalisierung | `apps.objects.units.parse_unit_label` | Präfix, Nummer, Buchstabenzusatz, Lage; Typ über `units.type_prefix_mapping`; Vergleichsschlüssel Großschreibung ohne Leerzeichen |
| Benennung Eigentümerakte | `apps.drive.naming.build_owner_folder_name` | F 6.1 und 6.2, Konfiguration `owner_file.*`, 32 Testfälle in beiden Präfixmodi |
| Stichtagsabfrage | `apps.parties.services.owners_at`, `owners_for_period`, `owners_for_document` | D 12.1, docs/architektur.md 5.4 (Abrechnungsjahr vor Zeitraum vor Dokumentdatum) |
| Zuordnung anlegen | `apps.parties.services.create_assignment` | Sperre auf die Einheit, Überlappungsprüfung je Eigentümer und Einheit, Anteilssumme über 1,0 als Warnung |
| Datensatzstatus | `apps.parties.services.derive_data_status` | unvollständig vor KI-Vorschlag vor bestätigt (B-31) |
| IBAN | `apps.parties.services.iban_fields` | nur `iban_last4` und HMAC-Hash (B-18); Prüfziffer MOD 97 |

## 6. Migrationen und Rückwärtslauf

Reihenfolge (Django-Abhängigkeiten): objects, documents 0001, ai 0001, pipeline, drive 0001, review, imports 0001, parties, documents 0002 und 0003, drive 0002, ai 0002, imports 0002, requirements, lists, documents 0004 (Volltextindex). `scripts/check_migrations_roundtrip.sh` führt alle Migrationen vorwärts, je App bis `zero` zurück und wieder vorwärts aus (Test T8) und läuft in der CI. Seeds liegen unter `db/seeds/` und werden über `app-seed` (Management-Command `seed`) idempotent geladen (B-22).
