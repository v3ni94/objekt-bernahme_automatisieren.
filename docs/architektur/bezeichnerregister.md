# Bezeichnerregister

Stand: 10.09.2026 (M2). Verbindlich für Code, Konfiguration, Dokumentation und alle Fachentwürfe (Beschluss B-01). Quelle der Tabellen und Spalten ist Fachentwurf D (docs/entwurf/fach_D_datenmodell.md) mit der Ergänzungsmigration (docs/architektur.md 5.6); Abweichungen und Ergänzungen sind in docs/architektur/datenmodell.md begründet. Die Arbeitspapiere E, F, G und H verwenden die hier genannten Bezeichner; E trägt dazu den Anhang "Bezeichner E zu D".

## Pflege

Der Abschnitt unterhalb der Markierung wird aus dem Code erzeugt und nicht von Hand geändert:

```
DJANGO_SETTINGS_MODULE=objektakte.settings.development PYTHONPATH=src .venv/bin/python manage.py bezeichnerregister --write
```

Quellen der Erzeugung: Django-Modellregister (Tabellen, Spalten, Typen, Wertevorräte, Constraints, Indizes), `CELERY_TASK_QUEUES`, `src/apps/config/catalog.json` mit `db/seeds/app_settings.json`, `src/apps/audit/actions.py`, `src/apps/accounts/permissions.py`, `.env.example`. Der Test `tests/unit/test_bezeichnerregister.py` vergleicht die eingecheckte Fassung mit der Erzeugung; ein neuer Bezeichner ohne Registeraktualisierung lässt die CI fehlschlagen.

## Feste Bezeichner (nicht erzeugt)

| Gegenstand | Verbindlich |
|---|---|
| Rollencodes | `admin`, `sachbearbeiter` |
| Verwaltungsarten | `weg`, `rental`, `weg_with_se` |
| Hauptordner | Codes `01` bis `06`; Ordnernamen nur aus `db/seeds/document_categories.json` |
| Unterordner | Code je Kategorie (`05/01` bis `05/11`, `06/01` bis `06/04`) aus `db/seeds/document_subfolders.json` |
| Dokumentunterarten | Codes aus `db/seeds/document_types.json` (z. B. `einzelabrechnung`, `einzelwirtschaftsplan`) |
| Aktenarten | `owner_files.file_kind`: `unit_owner`, `unknown_unit`, `unassigned`; `tenant_files.file_kind`: `unit_tenant`, `unknown_unit`, `unassigned` |
| Idempotenzschlüssel | je Jobtyp nach docs/architektur.md 5.4 |
| OAuth-Redirect-URIs | `/auth/google/callback`, `/auth/google/login/callback` |
| Altbezeichnung des Auffangordners | ausschließlich `db/seeds/app_settings.json`, Schlüssel `drive.legacy_folder_aliases` (B-22) |
| Verzeichnisse im Repository | docs/architektur.md 11 |

<!-- generiert: ab hier nicht von Hand aendern; Erzeugung: manage.py bezeichnerregister -->

## Tabellen und Spalten

Quelle: Django-Modellregister (`db_table`, `db_column`). Typ nach MariaDB-Backend. Wertevorräte als CHECK-Constraint in der Datenbank.

### account_emailaddress

Modell `account.EmailAddress`. Tabelle der Bibliothek django-allauth.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | integer AUTO_INCREMENT | nein |  |
| user_id | bigint | nein | → users.id |
| email | varchar(254) | nein |  |
| verified | tinyint(1) | nein |  |
| primary | tinyint(1) | nein |  |

Constraints und Indizes: `unique_primary_email`, `unique_verified_email`

### account_emailconfirmation

Modell `account.EmailConfirmation`. Tabelle der Bibliothek django-allauth.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | integer AUTO_INCREMENT | nein |  |
| email_address_id | integer | nein | → account_emailaddress.id |
| created | datetime(6) | nein |  |
| sent | datetime(6) | ja |  |
| key | varchar(64) | nein |  |

### roles

Modell `accounts.Role`. roles.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| code | varchar(24) | nein |  |
| name | varchar(80) | nein |  |
| permissions | json | nein |  |
| is_system | tinyint(1) | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |

### users

Modell `accounts.User`. users.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| email | varchar(254) | nein |  |
| display_name | varchar(120) | nein |  |
| role_id | bigint | nein | → roles.id |
| password_hash | varchar(255) | ja |  |
| password_changed_at | datetime(6) | ja |  |
| google_subject | varchar(255) | ja |  |
| status | varchar(16) | nein | invited, active, disabled |
| failed_login_count | smallint UNSIGNED | nein |  |
| locked_until | datetime(6) | ja |  |
| last_login_at | datetime(6) | ja |  |
| created_by | bigint | ja | → users.id |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| active_key | varchar(254) GENERATED | nein |  |

Constraints und Indizes: `uq_users_email_active`, `ix_users_role`

### ai_calls

Modell `ai.AiCall`. ai calls.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| object_id | bigint | ja | → objects.id |
| document_id | bigint | ja | → documents.id |
| run_id | bigint | ja | → processing_runs.id |
| job_id | bigint | ja | → processing_jobs.id |
| purpose | varchar(24) | nein | classify, extract_entities, parse_list, other |
| provider | varchar(24) | nein | openai, anthropic |
| model | varchar(80) | nein |  |
| endpoint | varchar(255) | ja |  |
| region | varchar(40) | ja |  |
| page_from | integer UNSIGNED | ja |  |
| page_to | integer UNSIGNED | ja |  |
| prompt_hash | varchar(64) | ja |  |
| prompt_chars | integer UNSIGNED | ja |  |
| masked_entities_count | smallint UNSIGNED | nein |  |
| tokens_in | integer UNSIGNED | ja |  |
| tokens_out | integer UNSIGNED | ja |  |
| cost_eur | numeric(12, 6) | ja |  |
| price_list_version | varchar(24) | ja |  |
| duration_ms | integer UNSIGNED | ja |  |
| status | varchar(24) | nein | ok, error, timeout, rate_limited, budget_blocked, schema_error, provider_error, blocked_by_mask_check |
| http_status | smallint UNSIGNED | ja |  |
| error_message | varchar(1000) | ja |  |
| fallback_used | tinyint(1) | nein |  |
| fallback_of_call_id | bigint | ja | → ai_calls.id |
| response_summary | json | ja |  |
| requested_at | datetime(6) | nein |  |

Constraints und Indizes: `ck_ai_calls_purpose`, `ck_ai_calls_provider`, `ck_ai_calls_status`, `ix_ai_calls_object_time`, `ix_ai_calls_provider_time`, `ix_ai_calls_status`

### app_settings

Modell `appconfig.AppSetting`. app settings.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| key | varchar(128) | nein |  |
| value | json | ja |  |
| value_type | varchar(16) | nein | string, integer, decimal, boolean, list, object |
| category | varchar(32) | nein |  |
| description | varchar(500) | nein |  |
| validation | json | ja |  |
| is_secret | tinyint(1) | nein |  |
| is_deprecated | tinyint(1) | nein |  |
| updated_by | bigint | ja | → users.id |
| updated_at | datetime(6) | nein |  |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `ix_settings_category`

### audit_events

Modell `audit.AuditEvent`. audit events.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| occurred_at | datetime(6) | nein |  |
| actor_type | varchar(16) | nein | user, system, worker |
| user_id | bigint | ja |  |
| user_email | varchar(254) | ja |  |
| action | varchar(64) | nein |  |
| entity_type | varchar(48) | nein |  |
| entity_id | bigint | ja |  |
| object_id | bigint | ja |  |
| before_state | json | ja |  |
| after_state | json | ja |  |
| reason | varchar(500) | ja |  |
| request_id | varchar(36) | ja |  |
| ip_address | longblob | ja |  |
| user_agent | varchar(255) | ja |  |

Constraints und Indizes: `ck_audit_actor`, `ix_audit_entity`, `ix_audit_user`, `ix_audit_object`, `ix_audit_action`

### iban_access_log

Modell `audit.IbanAccessLog`. iban access logs.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| accessed_at | datetime(6) | nein |  |
| actor_type | varchar(16) | nein |  |
| user_id | bigint | ja |  |
| user_email | varchar(254) | ja |  |
| entity_type | varchar(16) | nein |  |
| entity_id | bigint | nein |  |
| purpose | varchar(24) | nein |  |
| request_id | varchar(36) | ja |  |
| ip_address | longblob | ja |  |

Constraints und Indizes: `ck_iban_log_actor`, `ck_iban_log_entity`, `ck_iban_log_purpose`, `ix_iban_log_entity`, `ix_iban_log_user`

### classification_rules

Modell `documents.ClassificationRule`. classification rules.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| code | varchar(48) | nein |  |
| name | varchar(120) | nein |  |
| rule_kind | varchar(24) | nein | filename, folder, keyword, entity, period |
| pattern | longtext | nein |  |
| target_category_code | varchar(2) | ja | → document_categories.code |
| target_subfolder_id | bigint | ja | → document_subfolders.id |
| target_document_type_id | bigint | ja | → document_types.id |
| scope_decision | varchar(16) | ja | object, accounting, owner, tenant, unclear |
| weight | numeric(5, 4) | nein |  |
| is_hard | tinyint(1) | nein |  |
| management_types | json | ja |  |
| sort_order | smallint UNSIGNED | nein |  |
| is_active | tinyint(1) | nein |  |

Constraints und Indizes: `ck_rules_kind`, `ck_rules_weight`

### classifier_models

Modell `documents.ClassifierModel`. classifier models.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| version | varchar(40) | nein |  |
| algorithm | varchar(48) | nein |  |
| trained_at | datetime(6) | nein |  |
| samples_count | integer UNSIGNED | nein |  |
| metrics | json | ja |  |
| artifact_path | varchar(255) | nein |  |
| is_active | tinyint(1) | nein |  |
| activated_at | datetime(6) | ja |  |
| activated_by | bigint | ja | → users.id |
| notes | varchar(500) | ja |  |
| created_at | datetime(6) | nein |  |

### document_categories

Modell `documents.DocumentCategory`. document categorys.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| code | varchar(2) | nein |  |
| folder_name | varchar(80) | nein |  |
| display_name | varchar(80) | nein |  |
| scope | varchar(16) | nein | object, owner, tenant, misc |
| sort_order | smallint UNSIGNED | nein |  |
| is_active | tinyint(1) | nein |  |

Constraints und Indizes: `ck_categories_scope`

### document_classifications

Modell `documents.DocumentClassification`. document classifications.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| document_id | bigint | nein | → documents.id |
| page_from | integer UNSIGNED | ja |  |
| page_to | integer UNSIGNED | ja |  |
| stage | smallint UNSIGNED | nein |  |
| provider | varchar(24) | nein | rules, local_model, openai, anthropic, human |
| model | varchar(80) | ja |  |
| category_code | varchar(2) | ja | → document_categories.code |
| subfolder_id | bigint | ja | → document_subfolders.id |
| document_type_id | bigint | ja | → document_types.id |
| period_year | smallint UNSIGNED | ja |  |
| scope_decision | varchar(16) | ja | object, accounting, owner, tenant, unclear |
| confidence | numeric(5, 4) | nein |  |
| reasoning | longtext | ja |  |
| owner_candidates | json | ja |  |
| features | json | ja |  |
| ai_call_id | bigint | ja | → ai_calls.id |
| tokens_in | integer UNSIGNED | ja |  |
| tokens_out | integer UNSIGNED | ja |  |
| cost_eur | numeric(12, 6) | ja |  |
| duration_ms | integer UNSIGNED | ja |  |
| is_final | tinyint(1) | nein |  |
| decided_by | bigint | ja | → users.id |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `ck_class_stage`, `ck_class_provider`, `ck_class_scope`, `ck_class_confidence`, `ck_class_pages`, `ix_class_document_stage`, `ix_class_document_final`

### document_entities

Modell `documents.DocumentEntity`. document entitys.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| document_id | bigint | nein | → documents.id |
| page_no | integer UNSIGNED | nein |  |
| entity_type | varchar(24) | nein | person_name, company_name, unit_label, amount, date, period, iban, mandate_ref, address, object_number, id_document_number |
| value_text | varchar(255) | ja |  |
| value_normalized | varchar(255) | ja |  |
| iban_last4 | varchar(4) | ja |  |
| iban_hash | varchar(64) | ja |  |
| char_from | integer UNSIGNED | ja |  |
| char_to | integer UNSIGNED | ja |  |
| confidence | numeric(5, 4) | ja |  |
| matched_owner_id | bigint | ja | → owners.id |
| matched_unit_id | bigint | ja | → units.id |
| matched_tenant_id | bigint | ja | → tenants.id |
| match_confidence | numeric(5, 4) | ja |  |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `ck_entities_type`, `ck_entities_iban_masked`, `ix_entities_doc_page`, `ix_entities_type_value`, `ix_entities_iban_hash`

### document_owner_links

Modell `documents.DocumentOwnerLink`. document owner links.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| document_id | bigint | nein | → documents.id |
| link_kind | varchar(16) | nein | whole_document, page_range |
| page_from | integer UNSIGNED | ja |  |
| page_to | integer UNSIGNED | ja |  |
| unit_id | bigint | ja | → units.id |
| subfolder_id | bigint | ja | → document_subfolders.id |
| document_type_id | bigint | ja | → document_types.id |
| period_year | smallint UNSIGNED | ja |  |
| period_from | date | ja |  |
| period_to | date | ja |  |
| document_date | date | ja |  |
| confidence | numeric(5, 4) | ja |  |
| status | varchar(16) | nein | suggested, confirmed, rejected |
| classification_id | bigint | ja | → document_classifications.id |
| review_case_id | bigint | ja | → review_cases.id |
| drive_copy_node_id | bigint | ja | → drive_nodes.id |
| drive_copy_file_id | varchar(128) | ja |  |
| created_by | bigint | ja | → users.id |
| confirmed_by | bigint | ja | → users.id |
| confirmed_at | datetime(6) | ja |  |
| owner_id | bigint | ja | → owners.id |
| assignment_id | bigint | ja | → owner_unit_assignments.id |
| owner_file_id | bigint | ja | → owner_files.id |
| active_key | varchar(80) GENERATED | nein |  |

Constraints und Indizes: `uq_dol_document_segment`, `ck_dol_kind`, `ck_dol_pages`, `ck_dol_reference`, `ck_dol_status`, `ck_dol_period`, `ix_dol_owner_file`, `ix_dol_owner_year`, `ix_dol_unit_year`, `ix_dol_document_page`

### document_pages

Modell `documents.DocumentPage`. document pages.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| document_id | bigint | nein | → documents.id |
| page_no | integer UNSIGNED | nein |  |
| text_source | varchar(16) | nein | text_layer, ocr, mixed, empty |
| is_scan | tinyint(1) | nein |  |
| text_content | longtext | ja |  |
| text_hash | varchar(64) | ja |  |
| char_count | integer UNSIGNED | ja |  |
| word_count | integer UNSIGNED | ja |  |
| ocr_confidence | numeric(5, 2) | ja |  |
| ocr_engine | varchar(40) | ja |  |
| ocr_language | varchar(8) | ja |  |
| rotation_deg | smallint | ja |  |
| masked_entities_count | smallint UNSIGNED | nein |  |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `uq_pages_document_page`, `ck_pages_source`, `ck_pages_confidence`, `ix_pages_text_hash`

### document_subfolders

Modell `documents.DocumentSubfolder`. document subfolders.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| category_code | varchar(2) | nein | → document_categories.code |
| code | varchar(2) | nein |  |
| folder_name | varchar(80) | nein |  |
| display_name | varchar(80) | nein |  |
| sort_order | smallint UNSIGNED | nein |  |
| is_active | tinyint(1) | nein |  |

Constraints und Indizes: `uq_subfolders_code`, `uq_subfolders_name`

### document_tenant_links

Modell `documents.DocumentTenantLink`. document tenant links.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| document_id | bigint | nein | → documents.id |
| link_kind | varchar(16) | nein | whole_document, page_range |
| page_from | integer UNSIGNED | ja |  |
| page_to | integer UNSIGNED | ja |  |
| unit_id | bigint | ja | → units.id |
| subfolder_id | bigint | ja | → document_subfolders.id |
| document_type_id | bigint | ja | → document_types.id |
| period_year | smallint UNSIGNED | ja |  |
| period_from | date | ja |  |
| period_to | date | ja |  |
| document_date | date | ja |  |
| confidence | numeric(5, 4) | ja |  |
| status | varchar(16) | nein | suggested, confirmed, rejected |
| classification_id | bigint | ja | → document_classifications.id |
| review_case_id | bigint | ja | → review_cases.id |
| drive_copy_node_id | bigint | ja | → drive_nodes.id |
| drive_copy_file_id | varchar(128) | ja |  |
| created_by | bigint | ja | → users.id |
| confirmed_by | bigint | ja | → users.id |
| confirmed_at | datetime(6) | ja |  |
| tenant_id | bigint | ja | → tenants.id |
| assignment_id | bigint | ja | → tenant_unit_assignments.id |
| lease_id | bigint | ja | → leases.id |
| tenant_file_id | bigint | ja | → tenant_files.id |
| active_key | varchar(80) GENERATED | nein |  |

Constraints und Indizes: `uq_dtl_document_segment`, `ck_dtl_kind`, `ck_dtl_pages`, `ck_dtl_reference`, `ck_dtl_status`, `ix_dtl_tenant_file`, `ix_dtl_tenant_year`, `ix_dtl_unit_year`

### document_types

Modell `documents.DocumentType`. document types.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| category_code | varchar(2) | nein | → document_categories.code |
| subfolder_id | bigint | ja | → document_subfolders.id |
| code | varchar(48) | nein |  |
| name | varchar(120) | nein |  |
| requires_period | tinyint(1) | nein |  |
| requires_owner | tinyint(1) | nein |  |
| requires_tenant | tinyint(1) | nein |  |
| keywords | json | ja |  |
| is_active | tinyint(1) | nein |  |

Constraints und Indizes: `ix_doctypes_category`

### documents

Modell `documents.Document`. documents.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| object_id | bigint | nein | → objects.id |
| sha256 | varchar(64) | ja |  |
| drive_md5 | varchar(32) | ja |  |
| size_bytes | bigint UNSIGNED | nein |  |
| mime_type | varchar(120) | nein |  |
| original_name | varchar(255) | nein |  |
| current_name | varchar(255) | nein |  |
| source | varchar(16) | nein | drive_existing, upload, import, generated, moved_in |
| source_path | varchar(1000) | ja |  |
| drive_file_id | varchar(128) | ja |  |
| drive_node_id | bigint | ja | → drive_nodes.id |
| target_drive_node_id | bigint | ja | → drive_nodes.id |
| drive_moved_at | datetime(6) | ja |  |
| page_count | integer UNSIGNED | ja |  |
| origin_kind | varchar(16) | ja | digital, scan, mixed |
| ocr_cache_key | varchar(160) | ja |  |
| status | varchar(16) | nein | registered, hashed, ocr_done, classified, filed, review, duplicate, moved_out, error |
| duplicate_of_document_id | bigint | ja | → documents.id |
| category_code | varchar(2) | ja | → document_categories.code |
| subfolder_id | bigint | ja | → document_subfolders.id |
| document_type_id | bigint | ja | → document_types.id |
| document_date | date | ja |  |
| period_year | smallint UNSIGNED | ja |  |
| period_from | date | ja |  |
| period_to | date | ja |  |
| final_confidence | numeric(5, 4) | ja |  |
| final_decided_by | varchar(16) | ja | stage1, stage2, stage3, human |
| classifier_version | varchar(40) | ja |  |
| is_master_with_segments | tinyint(1) | nein |  |
| error_message | longtext | ja |  |
| first_seen_at | datetime(6) | nein |  |
| filed_at | datetime(6) | ja |  |

Constraints und Indizes: `uq_documents_object_hash`, `uq_documents_object_drive_file`, `ck_documents_source`, `ck_documents_origin`, `ck_documents_status`, `ck_documents_decider`, `ck_documents_period`, `ix_documents_drive_file`, `ix_documents_object_status`, `ix_documents_category`, `ix_documents_period`

### retention_policies

Modell `documents.RetentionPolicy`. retention policys.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| category_code | varchar(2) | nein | → document_categories.code |
| subfolder_id | bigint | ja | → document_subfolders.id |
| document_type_id | bigint | ja | → document_types.id |
| retention_years | smallint UNSIGNED | ja |  |
| retention_basis | varchar(500) | ja |  |
| trigger_event | varchar(24) | ja | document_date, period_end, assignment_end |
| is_approved | tinyint(1) | nein |  |
| approved_by | bigint | ja | → users.id |
| approved_at | datetime(6) | ja |  |
| updated_by | bigint | ja | → users.id |

Constraints und Indizes: `uq_retention_scope`, `ck_retention_trigger`, `ck_retention_approval`

### training_samples

Modell `documents.TrainingSample`. training samples.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| document_id | bigint | ja | → documents.id |
| page_from | integer UNSIGNED | ja |  |
| page_to | integer UNSIGNED | ja |  |
| text_hash | varchar(64) | ja |  |
| label_category_code | varchar(2) | ja | → document_categories.code |
| label_subfolder_id | bigint | ja | → document_subfolders.id |
| label_document_type_id | bigint | ja | → document_types.id |
| label_scope | varchar(16) | ja | object, accounting, owner, tenant, unclear |
| label_source | varchar(24) | nein | review_decision, seed, rules_high_confidence, manual |
| weight | numeric(5, 4) | nein |  |
| review_decision_id | bigint | ja | → review_decisions.id |
| is_active | tinyint(1) | nein |  |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `ck_samples_source`, `ix_samples_text_hash`

### drive_nodes

Modell `drive.DriveNode`. drive nodes.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| object_id | bigint | ja | → objects.id |
| node_kind | varchar(24) | nein | data_root, object_root, main_folder, subfolder, owner_file_folder, owner_file_subfolder, tenant_file_folder, tenant_file_subfolder, list_file |
| category_code | varchar(2) | ja | → document_categories.code |
| subfolder_id | bigint | ja | → document_subfolders.id |
| owner_file_id | bigint | ja | → owner_files.id |
| tenant_file_id | bigint | ja | → tenant_files.id |
| list_type | varchar(16) | ja | owner_list, tenant_list |
| list_format | varchar(8) | ja | xlsx, pdf |
| parent_node_id | bigint | ja | → drive_nodes.id |
| drive_file_id | varchar(128) | nein |  |
| drive_parent_id | varchar(128) | ja |  |
| drive_name | varchar(255) | nein |  |
| expected_name | varchar(255) | ja |  |
| mime_type | varchar(120) | ja |  |
| is_folder | tinyint(1) | nein |  |
| created_by_app | tinyint(1) | nein |  |
| status | varchar(16) | nein | active, missing, trashed |
| last_verified_at | datetime(6) | ja |  |
| position_key | varchar(190) GENERATED | nein |  |

Constraints und Indizes: `uq_drive_nodes_position`, `ck_drive_nodes_kind`, `ck_drive_nodes_status`, `ck_drive_nodes_list`, `ix_drive_nodes_object_kind`

### drive_sync_actions

Modell `drive.DriveSyncAction`. drive sync actions.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| sync_run_id | bigint | nein | → drive_sync_runs.id |
| seq_no | integer UNSIGNED | nein |  |
| action_type | varchar(24) | nein | inventory, find_root, register_folder, create_folder, rename_folder, move_file, create_review |
| drive_node_id | bigint | ja | → drive_nodes.id |
| target_drive_id | varchar(128) | ja |  |
| parent_drive_id | varchar(128) | ja |  |
| name_before | varchar(255) | ja |  |
| name_after | varchar(255) | ja |  |
| file_count_before | integer UNSIGNED | ja |  |
| file_count_after | integer UNSIGNED | ja |  |
| id_hash_before | varchar(64) | ja |  |
| id_hash_after | varchar(64) | ja |  |
| planned | tinyint(1) | nein |  |
| executed | tinyint(1) | nein |  |
| executed_at | datetime(6) | ja |  |
| result | varchar(16) | ja |  |
| error_message | longtext | ja |  |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `uq_sync_actions_seq`, `ck_sync_actions_type`, `ck_sync_actions_result`

### drive_sync_runs

Modell `drive.DriveSyncRun`. drive sync runs.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| object_id | bigint | ja | → objects.id |
| dry_run | tinyint(1) | nein |  |
| status | varchar(16) | nein | running, done, failed, aborted |
| triggered_by | bigint | ja | → users.id |
| root_matches | smallint UNSIGNED | ja |  |
| file_count_before | integer UNSIGNED | ja |  |
| file_count_after | integer UNSIGNED | ja |  |
| actions_planned | integer UNSIGNED | nein |  |
| actions_executed | integer UNSIGNED | nein |  |
| no_changes | tinyint(1) | ja |  |
| summary | json | ja |  |
| error_message | longtext | ja |  |
| started_at | datetime(6) | nein |  |
| finished_at | datetime(6) | ja |  |

Constraints und Indizes: `ck_sync_runs_status`, `ix_sync_runs_object`

### oauth_tokens

Modell `drive.OAuthToken`. o auth tokens.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| provider | varchar(24) | nein |  |
| account_email | varchar(254) | nein |  |
| scopes | varchar(500) | nein |  |
| storage_mode | varchar(16) | nein |  |
| access_token_encrypted | longblob | ja |  |
| refresh_token_encrypted | longblob | ja |  |
| key_version | smallint UNSIGNED | ja |  |
| access_expires_at | datetime(6) | ja |  |
| refresh_obtained_at | datetime(6) | ja |  |
| last_refresh_at | datetime(6) | ja |  |
| last_refresh_status | varchar(16) | ja |  |
| last_refresh_error | varchar(500) | ja |  |
| consecutive_failures | smallint UNSIGNED | nein |  |
| status | varchar(16) | nein |  |
| created_by | bigint | ja | → users.id |

Constraints und Indizes: `uq_oauth_provider_account`, `ck_oauth_mode`, `ck_oauth_status`

### import_batches

Modell `imports.ImportBatch`. import batchs.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| object_id | bigint | nein | → objects.id |
| import_kind | varchar(16) | nein | owner_list, tenant_list, mixed |
| source_format | varchar(24) | nein | pdf_scan, pdf_digital, xlsx, csv, immoware24, domus, other |
| parser_profile | varchar(48) | nein |  |
| parser_version | varchar(24) | nein |  |
| source_document_id | bigint | ja | → documents.id |
| source_sha256 | varchar(64) | nein |  |
| source_file_name | varchar(255) | nein |  |
| status | varchar(24) | nein | uploaded, parsed, in_review, committed, partially_committed, rejected, failed |
| rows_total | integer UNSIGNED | ja |  |
| rows_uncertain | integer UNSIGNED | ja |  |
| rows_confirmed | integer UNSIGNED | ja |  |
| rows_rejected | integer UNSIGNED | ja |  |
| rows_committed | integer UNSIGNED | ja |  |
| column_mapping | json | ja |  |
| ai_call_id | bigint | ja | → ai_calls.id |
| error_message | longtext | ja |  |
| uploaded_by | bigint | ja | → users.id |
| committed_by | bigint | ja | → users.id |
| committed_at | datetime(6) | ja |  |

Constraints und Indizes: `uq_import_object_hash`, `ck_import_kind`, `ck_import_format`, `ck_import_status`, `ix_import_status`

### import_column_profiles

Modell `imports.ImportColumnProfile`. import column profiles.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| name | varchar(80) | nein |  |
| source_format | varchar(24) | nein | pdf_scan, pdf_digital, xlsx, csv, immoware24, domus, other |
| parser_profile | varchar(48) | nein |  |
| column_mapping | json | nein |  |
| previous_manager_name | varchar(160) | ja |  |
| created_by | bigint | ja | → users.id |

### import_rows

Modell `imports.ImportRow`. import rows.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| batch_id | bigint | nein | → import_batches.id |
| row_no | integer UNSIGNED | nein |  |
| sub_index | smallint UNSIGNED | nein |  |
| page_no | integer UNSIGNED | ja |  |
| raw_data | json | nein |  |
| raw_text | longtext | ja |  |
| parsed_fields | json | nein |  |
| confidence | numeric(5, 4) | ja |  |
| status | varchar(16) | nein | parsed, uncertain, confirmed, rejected, committed, duplicate |
| target_entity_type | varchar(32) | ja | owner, unit, owner_unit_assignment, tenant, lease, tenant_unit_assignment |
| matched_owner_id | bigint | ja | → owners.id |
| matched_unit_id | bigint | ja | → units.id |
| matched_tenant_id | bigint | ja | → tenants.id |
| committed_owner_id | bigint | ja | → owners.id |
| committed_unit_id | bigint | ja | → units.id |
| committed_assignment_id | bigint | ja | → owner_unit_assignments.id |
| committed_tenant_id | bigint | ja | → tenants.id |
| committed_lease_id | bigint | ja | → leases.id |
| committed_targets | json | ja |  |
| review_case_id | bigint | ja | → review_cases.id |
| uncertainty_reasons | json | ja |  |
| confirmed_by | bigint | ja | → users.id |
| confirmed_at | datetime(6) | ja |  |
| committed_at | datetime(6) | ja |  |
| notes | varchar(500) | ja |  |

Constraints und Indizes: `uq_import_rows_position`, `ck_import_rows_status`, `ck_import_rows_target`, `ix_import_rows_status`

### list_generations

Modell `lists.ListGeneration`. list generations.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| object_id | bigint | nein | → objects.id |
| list_type | varchar(16) | nein |  |
| list_format | varchar(8) | nein |  |
| drive_node_id | bigint | ja | → drive_nodes.id |
| trigger_kind | varchar(24) | nein | run, review_confirm, manual, import_commit, masterdata_change, scheduled |
| triggered_by | bigint | ja | → users.id |
| rows_current | integer UNSIGNED | ja |  |
| rows_history | integer UNSIGNED | ja |  |
| rows_open | integer UNSIGNED | ja |  |
| content_hash | varchar(64) | ja |  |
| status | varchar(24) | nein | done, failed, skipped_unchanged |
| error_message | longtext | ja |  |
| duration_ms | integer UNSIGNED | ja |  |
| generated_at | datetime(6) | nein |  |

Constraints und Indizes: `ck_list_gen_type`, `ck_list_gen_format`, `ck_list_gen_trigger`, `ck_list_gen_status`, `ix_list_gen_object`

### mfa_authenticator

Modell `mfa.Authenticator`. Tabelle der Bibliothek django-allauth.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| user_id | bigint | nein | → users.id |
| type | varchar(20) | nein | recovery_codes, totp, webauthn |
| data | json | nein |  |
| created_at | datetime(6) | nein |  |
| last_used_at | datetime(6) | ja |  |

Constraints und Indizes: `unique_authenticator_type`

### objects

Modell `objects.ManagedObject`. managed objects.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| object_number | varchar(8) | nein |  |
| object_number_numeric | integer UNSIGNED GENERATED | nein |  |
| name | varchar(160) | ja |  |
| street | varchar(120) | ja |  |
| house_number | varchar(20) | ja |  |
| postal_code | varchar(10) | ja |  |
| city | varchar(80) | ja |  |
| management_type | varchar(16) | nein | weg, rental, weg_with_se |
| status | varchar(16) | nein | new, takeover, active, archived |
| takeover_from | date | ja |  |
| takeover_to | date | ja |  |
| fiscal_year_start_month | smallint UNSIGNED | ja |  |
| previous_manager_name | varchar(160) | ja |  |
| previous_manager_street | varchar(120) | ja |  |
| previous_manager_house_number | varchar(20) | ja |  |
| previous_manager_postal_code | varchar(10) | ja |  |
| previous_manager_city | varchar(80) | ja |  |
| previous_manager_contact_person | varchar(160) | ja |  |
| previous_manager_reference | varchar(80) | ja |  |
| expected_unit_count | integer UNSIGNED | ja |  |
| sepa_used | tinyint(1) | ja |  |
| special_levies_in_period | tinyint(1) | ja |  |
| is_test | tinyint(1) | nein |  |
| drive_root_folder_id | varchar(128) | ja |  |
| drive_root_folder_name | varchar(255) | ja |  |
| drive_root_verified_at | datetime(6) | ja |  |
| notes | longtext | ja |  |
| created_by | bigint | ja | → users.id |
| active_key | integer UNSIGNED GENERATED | nein |  |

Constraints und Indizes: `uq_objects_number_active`, `ck_objects_number_digits`, `ck_objects_mgmt`, `ck_objects_status`, `ck_objects_takeover`, `ix_objects_status`, `ix_objects_drive_root`

### units

Modell `objects.Unit`. units.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| object_id | bigint | nein | → objects.id |
| unit_number | varchar(16) | ja |  |
| unit_type | varchar(24) | nein | apartment, commercial, parking, garage, underground_parking, cellar, other |
| unit_label | varchar(80) | nein |  |
| unit_label_normalized | varchar(80) | nein |  |
| co_ownership_share | numeric(14, 4) | ja |  |
| co_ownership_share_base | integer UNSIGNED | ja |  |
| building | varchar(80) | ja |  |
| location | varchar(120) | ja |  |
| external_ref | varchar(32) | ja |  |
| house_fee_monthly | numeric(12, 2) | ja |  |
| status | varchar(16) | nein |  |
| data_status | varchar(16) | nein | confirmed, ai_suggested, incomplete |
| se_managed | tinyint(1) | ja |  |
| vacancy_confirmed | tinyint(1) | ja |  |
| notes | varchar(500) | ja |  |
| active_key | varchar(80) GENERATED | nein |  |

Constraints und Indizes: `uq_units_object_label_active`, `ck_units_type`, `ck_units_status`, `ck_units_data_status`, `ix_units_object_number`, `ix_units_external_ref`

### field_provenance

Modell `parties.FieldProvenance`. field provenances.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| entity_type | varchar(32) | nein | owner, unit, owner_unit_assignment, tenant, lease, tenant_unit_assignment, object |
| entity_id | bigint UNSIGNED | nein |  |
| field_name | varchar(64) | nein |  |
| source_kind | varchar(16) | nein | document, import_row, manual, ai, system |
| source_document_id | bigint | ja | → documents.id |
| source_page_from | integer UNSIGNED | ja |  |
| source_page_to | integer UNSIGNED | ja |  |
| source_import_row_id | bigint | ja | → import_rows.id |
| confidence | numeric(5, 4) | ja |  |
| status | varchar(16) | nein | confirmed, ai_suggested, incomplete |
| set_by | bigint | ja | → users.id |
| set_at | datetime(6) | nein |  |

Constraints und Indizes: `uq_provenance_field`, `ck_provenance_kind`, `ck_provenance_status`, `ck_provenance_pages`

### leases

Modell `parties.Lease`. leases.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| object_id | bigint | nein | → objects.id |
| lease_reference | varchar(60) | ja |  |
| start_date | date | ja |  |
| end_date | date | ja |  |
| base_rent | numeric(12, 2) | ja |  |
| utilities_prepayment | numeric(12, 2) | ja |  |
| heating_prepayment | numeric(12, 2) | ja |  |
| total_rent | numeric(12, 2) GENERATED | nein |  |
| deposit_amount | numeric(12, 2) | ja |  |
| deposit_type | varchar(24) | ja | cash_account, savings_book, bank_guarantee, pledge, insurance, other, unknown |
| deposit_notes | varchar(255) | ja |  |
| rent_adjustment_type | varchar(16) | nein | none, graduated, indexed, unknown |
| persons_count | smallint UNSIGNED | ja |  |
| source_document_id | bigint | ja | → documents.id |
| source_import_row_id | bigint | ja | → import_rows.id |
| confidence | numeric(5, 4) | ja |  |
| data_status | varchar(16) | nein | confirmed, ai_suggested, incomplete |
| notes | varchar(500) | ja |  |

Constraints und Indizes: `ck_leases_period`, `ck_leases_amounts`, `ck_leases_deposit_type`, `ck_leases_adjustment`, `ck_leases_data_status`, `ix_leases_object_start`

### owner_file_assignments

Modell `parties.OwnerFileAssignment`. owner file assignments.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| owner_file_id | bigint | nein | → owner_files.id |
| assignment_id | bigint | nein | → owner_unit_assignments.id |
| created_at | datetime(6) | nein |  |

### owner_files

Modell `parties.OwnerFile`. owner files.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| object_id | bigint | nein | → objects.id |
| unit_id | bigint | ja | → units.id |
| owner_id | bigint | ja | → owners.id |
| file_kind | varchar(16) | nein | unit_owner, unknown_unit, unassigned |
| folder_name | varchar(255) | nein |  |
| name_basis | json | ja |  |
| status | varchar(16) | nein |  |
| active_key | varchar(255) GENERATED | nein |  |

Constraints und Indizes: `uq_owner_files_name_active`, `ck_owner_files_kind`, `ck_owner_files_status`, `ix_owner_files_unit`

### owner_unit_assignments

Modell `parties.OwnerUnitAssignment`. owner unit assignments.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| owner_id | bigint | nein | → owners.id |
| unit_id | bigint | nein | → units.id |
| valid_from | date | ja |  |
| valid_to | date | ja |  |
| share | numeric(9, 6) | ja |  |
| source_document_id | bigint | ja | → documents.id |
| source_import_row_id | bigint | ja | → import_rows.id |
| confidence | numeric(5, 4) | ja |  |
| data_status | varchar(16) | nein | confirmed, ai_suggested, incomplete |
| confirmed_by | bigint | ja | → users.id |
| confirmed_at | datetime(6) | ja |  |
| balance_at_takeover | numeric(12, 2) | ja |  |
| notes | varchar(500) | ja |  |
| is_current | bool GENERATED | nein |  |

Constraints und Indizes: `ck_oua_period`, `ck_oua_share`, `ck_oua_data_status`, `ix_oua_unit_period`, `ix_oua_owner_period`, `ix_oua_unit_current`

### owners

Modell `parties.Owner`. owners.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| type | varchar(16) | nein | natural_person, legal_entity, community |
| salutation | varchar(40) | ja |  |
| first_name | varchar(120) | ja |  |
| last_name | varchar(120) | ja |  |
| company_name | varchar(200) | ja |  |
| short_name | varchar(60) | ja |  |
| search_name | varchar(240) | nein |  |
| email | varchar(254) | ja |  |
| phone | varchar(40) | ja |  |
| mobile | varchar(40) | ja |  |
| iban_last4 | varchar(4) | ja |  |
| iban_encrypted | varbinary(160) | ja |  |
| iban_key_version | smallint UNSIGNED | ja |  |
| iban_hash | varbinary(32) | ja |  |
| sepa_mandate_present | tinyint(1) | ja |  |
| sepa_mandate_reference | varchar(35) | ja |  |
| notes | varchar(500) | ja |  |
| data_status | varchar(16) | nein | confirmed, ai_suggested, incomplete |
| correspondence_street | varchar(120) | ja |  |
| correspondence_house_number | varchar(20) | ja |  |
| correspondence_postal_code | varchar(10) | ja |  |
| correspondence_city | varchar(80) | ja |  |
| correspondence_country | varchar(2) | ja |  |
| correspondence_addition | varchar(120) | ja |  |
| delivery_street | varchar(120) | ja |  |
| delivery_house_number | varchar(20) | ja |  |
| delivery_postal_code | varchar(10) | ja |  |
| delivery_city | varchar(80) | ja |  |
| delivery_country | varchar(2) | ja |  |
| delivery_addition | varchar(120) | ja |  |

Constraints und Indizes: `ck_owners_type`, `ck_owners_data_status`, `ck_owners_iban_pair`, `ix_owners_search_name`, `ix_owners_last_name`, `ix_owners_company`, `ix_owners_iban_hash`, `ix_owners_email`

### tenant_file_assignments

Modell `parties.TenantFileAssignment`. tenant file assignments.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| tenant_file_id | bigint | nein | → tenant_files.id |
| assignment_id | bigint | nein | → tenant_unit_assignments.id |
| created_at | datetime(6) | nein |  |

### tenant_files

Modell `parties.TenantFile`. tenant files.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| object_id | bigint | nein | → objects.id |
| unit_id | bigint | ja | → units.id |
| file_kind | varchar(16) | nein |  |
| folder_name | varchar(255) | nein |  |
| name_basis | json | ja |  |
| status | varchar(16) | nein |  |
| active_key | varchar(255) GENERATED | nein |  |

Constraints und Indizes: `uq_tenant_files_name_active`, `ck_tenant_files_kind`, `ck_tenant_files_status`, `ix_tenant_files_unit`

### tenant_unit_assignments

Modell `parties.TenantUnitAssignment`. tenant unit assignments.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| tenant_id | bigint | nein | → tenants.id |
| unit_id | bigint | nein | → units.id |
| lease_id | bigint | ja | → leases.id |
| role | varchar(16) | nein | tenant, co_tenant, guarantor |
| valid_from | date | ja |  |
| valid_to | date | ja |  |
| source_document_id | bigint | ja | → documents.id |
| source_import_row_id | bigint | ja | → import_rows.id |
| confidence | numeric(5, 4) | ja |  |
| data_status | varchar(16) | nein | confirmed, ai_suggested, incomplete |
| confirmed_by | bigint | ja | → users.id |
| confirmed_at | datetime(6) | ja |  |
| notes | varchar(500) | ja |  |
| is_current | bool GENERATED | nein |  |

Constraints und Indizes: `ck_tua_role`, `ck_tua_period`, `ck_tua_data_status`, `ix_tua_unit_period`, `ix_tua_tenant_period`

### tenants

Modell `parties.Tenant`. tenants.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| deleted_at | datetime(6) | ja |  |
| deleted_by | bigint | ja | → users.id |
| delete_reason | varchar(255) | ja |  |
| type | varchar(16) | nein | natural_person, legal_entity, community |
| salutation | varchar(40) | ja |  |
| first_name | varchar(120) | ja |  |
| last_name | varchar(120) | ja |  |
| company_name | varchar(200) | ja |  |
| short_name | varchar(60) | ja |  |
| search_name | varchar(240) | nein |  |
| email | varchar(254) | ja |  |
| phone | varchar(40) | ja |  |
| mobile | varchar(40) | ja |  |
| iban_last4 | varchar(4) | ja |  |
| iban_encrypted | varbinary(160) | ja |  |
| iban_key_version | smallint UNSIGNED | ja |  |
| iban_hash | varbinary(32) | ja |  |
| sepa_mandate_present | tinyint(1) | ja |  |
| sepa_mandate_reference | varchar(35) | ja |  |
| notes | varchar(500) | ja |  |
| data_status | varchar(16) | nein | confirmed, ai_suggested, incomplete |

Constraints und Indizes: `ck_tenants_type`, `ck_tenants_data_status`, `ck_tenants_iban_pair`, `ix_tenants_search_name`, `ix_tenants_last_name`, `ix_tenants_iban_hash`

### object_progress

Modell `pipeline.ObjectProgress`. object progresss.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| object_id | bigint | nein | → objects.id |
| documents_total | integer UNSIGNED | nein |  |
| documents_hashed | integer UNSIGNED | nein |  |
| documents_ocr_done | integer UNSIGNED | nein |  |
| documents_classified | integer UNSIGNED | nein |  |
| documents_filed | integer UNSIGNED | nein |  |
| documents_review | integer UNSIGNED | nein |  |
| documents_duplicate | integer UNSIGNED | nein |  |
| documents_error | integer UNSIGNED | nein |  |
| documents_misc | integer UNSIGNED | nein |  |
| pages_total | integer UNSIGNED | nein |  |
| pages_done | integer UNSIGNED | nein |  |
| review_open | integer UNSIGNED | nein |  |
| last_run_id | bigint | ja | → processing_runs.id |
| updated_at | datetime(6) | nein |  |

### processing_job_events

Modell `pipeline.ProcessingJobEvent`. processing job events.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| job_id | bigint | nein | → processing_jobs.id |
| from_status | varchar(16) | ja |  |
| to_status | varchar(16) | nein |  |
| worker_id | varchar(80) | ja |  |
| attempt_no | smallint UNSIGNED | ja |  |
| message | varchar(500) | ja |  |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `ix_job_events_job`

### processing_jobs

Modell `pipeline.ProcessingJob`. processing jobs.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| run_id | bigint | ja | → processing_runs.id |
| object_id | bigint | nein | → objects.id |
| document_id | bigint | ja | → documents.id |
| job_type | varchar(24) | nein | discover, hash, analyze_pages, ocr_chunk, merge_pages, render_previews, extract_entities, classify, classify_ai, decide, file_to_drive, link_segments, generate_lists, evaluate_completeness, reconcile_drive, train_classifier, sweep |
| idempotency_key | varchar(160) | nein |  |
| status | varchar(16) | nein | pending, running, done, failed, skipped |
| priority | smallint | nein |  |
| attempt_count | smallint UNSIGNED | nein |  |
| max_attempts | smallint UNSIGNED | nein |  |
| locked_by | varchar(80) | ja |  |
| locked_at | datetime(6) | ja |  |
| heartbeat_at | datetime(6) | ja |  |
| next_attempt_at | datetime(6) | ja |  |
| started_at | datetime(6) | ja |  |
| finished_at | datetime(6) | ja |  |
| duration_ms | integer UNSIGNED | ja |  |
| pages_processed | integer UNSIGNED | ja |  |
| payload | json | ja |  |
| result | json | ja |  |
| last_error | longtext | ja |  |
| error_class | varchar(80) | ja |  |
| skip_reason | varchar(64) | ja |  |

Constraints und Indizes: `ck_jobs_type`, `ck_jobs_status`, `ck_jobs_attempts`, `ix_jobs_pickup`, `ix_jobs_run_status`, `ix_jobs_stale`

### processing_runs

Modell `pipeline.ProcessingRun`. processing runs.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| object_id | bigint | nein | → objects.id |
| run_type | varchar(24) | nein | full, incremental, reconcile_drive, regenerate_lists, reclassify |
| status | varchar(16) | nein | pending, running, done, failed, aborted |
| dry_run | tinyint(1) | nein |  |
| triggered_by | bigint | ja | → users.id |
| worker_count | smallint UNSIGNED | ja |  |
| documents_total | integer UNSIGNED | ja |  |
| documents_done | integer UNSIGNED | ja |  |
| documents_failed | integer UNSIGNED | ja |  |
| documents_skipped | integer UNSIGNED | ja |  |
| pages_total | integer UNSIGNED | ja |  |
| pages_done | integer UNSIGNED | ja |  |
| documents_misc | integer UNSIGNED | ja |  |
| misc_share_pct | numeric(5, 2) | ja |  |
| pages_per_minute | numeric(10, 2) | ja |  |
| ram_peak_mb | integer UNSIGNED | ja |  |
| ai_calls_count | integer UNSIGNED | ja |  |
| ai_cost_eur | numeric(12, 6) | ja |  |
| ai_fallback_count | integer UNSIGNED | ja |  |
| review_cases_created | integer UNSIGNED | ja |  |
| error_message | longtext | ja |  |
| started_at | datetime(6) | ja |  |
| finished_at | datetime(6) | ja |  |

Constraints und Indizes: `ck_runs_type`, `ck_runs_status`, `ix_runs_object_started`, `ix_runs_status`

### completeness_checks

Modell `requirements.CompletenessCheck`. completeness checks.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| code | varchar(48) | nein |  |
| name | varchar(120) | nein |  |
| description | varchar(500) | ja |  |
| scope_type | varchar(24) | nein | object, unit, assignment |
| management_types | json | ja |  |
| period_based | tinyint(1) | nein |  |
| evidence_document_types | json | ja |  |
| request_text_block_id | bigint | ja | → request_text_blocks.id |
| sort_order | smallint UNSIGNED | nein |  |
| is_active | tinyint(1) | nein |  |

Constraints und Indizes: `ck_checks_scope`

### completeness_findings

Modell `requirements.CompletenessFinding`. completeness findings.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| object_id | bigint | nein | → objects.id |
| check_code | varchar(48) | nein |  |
| scope_type | varchar(24) | nein | object, unit, assignment |
| unit_id | bigint | ja | → units.id |
| assignment_id | bigint | ja | → owner_unit_assignments.id |
| period_year | smallint UNSIGNED | ja |  |
| status | varchar(16) | nein | missing, partial, fulfilled, not_applicable |
| evidence_document_id | bigint | ja | → documents.id |
| evidence_link_id | bigint | ja | → document_owner_links.id |
| details | json | ja |  |
| include_in_request | tinyint(1) | nein |  |
| manual_status | varchar(16) | ja |  |
| manual_reason | varchar(500) | ja |  |
| manual_by | bigint | ja | → users.id |
| manual_at | datetime(6) | ja |  |
| last_evaluated_at | datetime(6) | nein |  |
| position_key | varchar(120) GENERATED | nein |  |

Constraints und Indizes: `uq_findings_position`, `ck_findings_scope`, `ck_findings_status`, `ck_findings_manual`, `ix_findings_status`

### document_requests

Modell `requirements.DocumentRequest`. document requests.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| object_id | bigint | nein | → objects.id |
| status | varchar(16) | nein | draft, approved, sent, withdrawn |
| recipient_name | varchar(160) | nein |  |
| recipient_street | varchar(120) | ja |  |
| recipient_house_number | varchar(20) | ja |  |
| recipient_postal_code | varchar(10) | ja |  |
| recipient_city | varchar(80) | ja |  |
| recipient_contact_person | varchar(160) | ja |  |
| reference | varchar(80) | ja |  |
| subject | varchar(200) | nein |  |
| body | longtext | nein |  |
| finding_ids | json | ja |  |
| file_path | varchar(255) | ja |  |
| created_by | bigint | ja | → users.id |
| approved_by | bigint | ja | → users.id |
| approved_at | datetime(6) | ja |  |
| sent_at | datetime(6) | ja |  |
| withdrawn_by | bigint | ja | → users.id |
| withdrawn_at | datetime(6) | ja |  |

Constraints und Indizes: `ck_requests_status`, `ix_requests_object_status`

### request_text_blocks

Modell `requirements.RequestTextBlock`. request text blocks.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| code | varchar(48) | nein |  |
| title | varchar(120) | nein |  |
| text | longtext | nein |  |
| sort_order | smallint UNSIGNED | nein |  |
| is_active | tinyint(1) | nein |  |

### review_cases

Modell `review.ReviewCase`. review cases.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| object_id | bigint | ja | → objects.id |
| case_type | varchar(32) | nein | owner_candidates, missing_metadata, move_proposal, drive_structure, import_row_uncertain, import_candidate, duplicate_object_number, data_consistency, unclear |
| case_subtype | varchar(32) | ja |  |
| document_id | bigint | ja | → documents.id |
| page_from | integer UNSIGNED | ja |  |
| page_to | integer UNSIGNED | ja |  |
| owner_link_id | bigint | ja | → document_owner_links.id |
| import_row_id | bigint | ja | → import_rows.id |
| drive_node_id | bigint | ja | → drive_nodes.id |
| misc_subfolder_id | bigint | ja | → document_subfolders.id |
| candidates | json | ja |  |
| proposed_action | json | ja |  |
| context | json | ja |  |
| batch_key | varchar(120) | ja |  |
| priority | smallint | nein |  |
| status | varchar(16) | nein | open, in_progress, resolved, dismissed |
| assigned_to | bigint | ja | → users.id |
| assigned_at | datetime(6) | ja |  |
| snoozed_until | datetime(6) | ja |  |
| resolved_by | bigint | ja | → users.id |
| resolved_at | datetime(6) | ja |  |
| resolution | json | ja |  |
| created_by_run_id | bigint | ja | → processing_runs.id |

Constraints und Indizes: `ck_review_type`, `ck_review_status`, `ck_review_pages`, `ix_review_object_status`, `ix_review_queue`, `ix_review_batch`, `ix_review_assigned`

### review_decisions

Modell `review.ReviewDecision`. review decisions.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| review_case_id | bigint | nein | → review_cases.id |
| document_id | bigint | ja | → documents.id |
| page_from | integer UNSIGNED | ja |  |
| page_to | integer UNSIGNED | ja |  |
| decision_type | varchar(24) | nein | confirm, correct, reject, move, assign_owner, assign_tenant, split, merge, select_folder, transfer_object, revert |
| decided_by | bigint | nein | → users.id |
| decided_at | datetime(6) | nein |  |
| is_bulk | tinyint(1) | nein |  |
| bulk_key | varchar(120) | ja |  |
| before_state | json | ja |  |
| after_state | json | nein |  |
| features_snapshot | json | ja |  |
| text_hashes | json | ja |  |
| label_category_code | varchar(2) | ja | → document_categories.code |
| label_subfolder_id | bigint | ja | → document_subfolders.id |
| label_document_type_id | bigint | ja | → document_types.id |
| label_owner_id | bigint | ja | → owners.id |
| label_unit_id | bigint | ja | → units.id |
| label_assignment_id | bigint | ja | → owner_unit_assignments.id |
| label_period_year | smallint UNSIGNED | ja |  |
| label_scope | varchar(16) | ja |  |
| system_was_correct | tinyint(1) | ja |  |
| used_for_training | tinyint(1) | nein |  |
| training_export_ref | varchar(80) | ja |  |
| comment | varchar(500) | ja |  |
| created_at | datetime(6) | nein |  |

Constraints und Indizes: `ck_decisions_type`, `ck_decisions_scope`, `ix_decisions_training`, `ix_decisions_user`

### review_saved_filters

Modell `review.ReviewSavedFilter`. review saved filters.

| Spalte | Typ | Null | Verweis oder Wertevorrat |
|---|---|---|---|
| id | bigint AUTO_INCREMENT | nein |  |
| created_at | datetime(6) | nein |  |
| updated_at | datetime(6) | nein |  |
| user_id | bigint | nein | → users.id |
| name | varchar(80) | nein |  |
| filters | json | nein |  |
| is_shared | tinyint(1) | nein |  |
| sort_order | smallint UNSIGNED | nein |  |

Constraints und Indizes: `uq_review_filters_user_name`

## Container und Queues

| Container | Queues | Build-Target | Netze |
|---|---|---|---|
| web | keine | web | data, egress, Traefik-Netz |
| worker | ocr | worker | data, egress |
| worker-nlp | classify | worker | data |
| worker-io | ai, io, lists | worker | data, egress |
| beat | keine (Zeitplan) | web | data, egress |
| db |  | MariaDB-Image | data |
| redis |  | Redis-Image | data |
| backup |  | eigenes Image | data, egress nur bei Offsite-Kopie |
| classifier (optional, Profil) | classify | worker | data |

Celery-Queues aus den Settings: `ai`, `classify`, `io`, `lists`, `ocr`

## Verzeichnisse unter /srv/objektakte/

`db`, `redis`, `transit`, `work`, `ocr-cache`, `previews`, `models`, `lists`, `imports`, `requests`, `exports`, `backup`, `secrets`, `deploy` (Beschluss B-14)

## Secrets (Dateinamen unter /srv/objektakte/secrets/)

`db_root_password`, `db_app_password`, `db_worker_password`, `db_migrate_password`, `db_backup_password`, `db_ro_password`, `redis_password`, `app_secret_key`, `iban_key`, `iban_hmac_key`, `token_key`, `totp_key`, `google_client_secret`, `openai_api_key`, `anthropic_api_key`, `smtp_password`, `readyz_token`, `backup_age_recipient`, `rclone_conf`

## Konfigurationsschlüssel app_settings

| Schlüssel | Kategorie | Typ | Seed |
|---|---|---|---|
| ai.chars_per_token | ai | decimal | `3.5` |
| ai.max_input_tokens | ai | integer | `3000` |
| ai.monthly_budget_eur | ai | object | `{"openai": null, "anthropic": null}` |
| ai.provider_order | ai | list | `["openai", "anthropic"]` |
| ai.providers | ai | object | `{"openai": {"enabled": false, "model": null, "endpoint": null, "region": null, "timeout...` |
| ai.store_masked_prompts | ai | boolean | `false` |
| ai.wall_budget_s | ai | integer | `120` |
| classification.ai_sample_pct | classification | integer | `10` |
| classification.bonus_agree | classification | decimal | `0.05` |
| classification.embedding_switch_macro_f1 | classification | decimal | `0.85` |
| classification.fuzzy_auto | classification | integer | `90` |
| classification.fuzzy_candidate_min | classification | integer | `78` |
| classification.gap_factor | classification | decimal | `0.5` |
| classification.label_weights | classification | object | `{"rule": 0.6, "synthetic": 0.3, "ai": 0.5, "review": 1.0}` |
| classification.malus_disagree | classification | decimal | `0.25` |
| classification.mask_id_documents_block_stage3 | classification | boolean | `true` |
| classification.retrain_after_new_labels | classification | integer | `50` |
| classification.retrain_f1_tolerance | classification | decimal | `0.01` |
| classification.stage2_conflict_p | classification | decimal | `0.9` |
| classification.stage2_min_samples_per_class | classification | integer | `15` |
| classification.stage3_max_tokens | classification | integer | `3000` |
| classification.threshold_auto_file | classification | decimal | `0.9` |
| classification.threshold_stage3_call | classification | decimal | `0.9` |
| classification.threshold_stage3_override | classification | decimal | `0.9` |
| completeness.contact_channels_required | completeness | integer | `1` |
| completeness.default_period_years | completeness | integer | `3` |
| completeness.mostly_complete_pct | completeness | integer | `90` |
| completeness.statement_expected_after | completeness | string | `"30.06."` |
| documents.duplicate_name_pattern | documents | string | `"{original_stem}_S{page_from}-{page_to}.pdf"` |
| documents.duplicate_owner_documents_in_drive | documents | boolean | `false` |
| documents.max_download_bytes | documents | integer | `524288000` |
| drive.backoff | drive | object | `{"base_s": 1, "factor": 2, "max_s": 64, "attempts": 8}` |
| drive.legacy_conflict_rename_pattern | drive | string | `null` |
| drive.legacy_folder_aliases | drive | object | `siehe db/seeds/app_settings.json (einzige Fundstelle der Altbezeichnung)` |
| drive.max_requests_per_second | drive | integer | `5` |
| drive.object_folder_name_pattern | drive | string | `"{number} {city}, {street} {house_number}"` |
| drive.object_number_digits_max | drive | integer | `6` |
| drive.object_number_digits_min | drive | integer | `2` |
| drive.object_number_separators | drive | list | `[" ", "_", ",", ".", "-"]` |
| drive.object_number_zero_pad_to | drive | integer | `null` |
| drive.protocol_folder_id | drive | string | `null` |
| drive.reconcile_on_open_min_interval_minutes | drive | integer | `15` |
| drive.resumable_threshold_bytes | drive | integer | `5242880` |
| drive.root_drive_id | drive | string | `null` |
| drive.root_folder_id | drive | string | `null` |
| drive.upload_chunk_bytes | drive | integer | `8388608` |
| import.column_confidence_min | import | decimal | `0.8` |
| import.name_split_thresholds | import | object | `{"high": 0.9, "medium": 0.6}` |
| import.ocr_cell_confidence_min | import | integer | `70` |
| jobs.max_attempts_default | jobs | integer | `3` |
| jobs.stale_minutes | jobs | object | `{"default": 15, "ocr_chunk": 10, "classify": 3, "classify_ai": 5, "file_to_drive": 5}` |
| lists.debounce_seconds | lists | integer | `60` |
| lists.include_provenance_sheet | lists | boolean | `false` |
| lists.owner_columns | lists | list | `["Einheit", "Einheitentyp", "Anrede", "Vorname", "Nachname", "Firma", "Straße und Hausn...` |
| lists.owner_list_name_pattern | lists | string | `"00_Eigentuemerliste_{number}.{ext}"` |
| lists.pdf_table_font_pt | lists | integer | `8` |
| lists.restore_position | lists | boolean | `true` |
| lists.tenant_columns | lists | list | `["Einheit", "Einheitentyp", "Anrede", "Vorname", "Nachname", "Firma", "Telefon", "Mobil...` |
| lists.tenant_list_name_pattern | lists | string | `"00_Mieterliste_{number}.{ext}"` |
| ocr.chunk_pages | ocr | integer | `20` |
| ocr.digital_alnum_ratio | ocr | decimal | `0.6` |
| ocr.digital_min_chars | ocr | integer | `50` |
| ocr.dpi_cap | ocr | integer | `300` |
| ocr.tessdata_variant | ocr | string | `"standard"` |
| ocr.two_phase_enabled | ocr | boolean | `false` |
| ocr.two_phase_head_pages | ocr | integer | `3` |
| owner_file.collision_suffix_mode | owner_file | string | `"year_then_counter"` |
| owner_file.create_folders_eagerly | owner_file | boolean | `false` |
| owner_file.legal_form_tokens | owner_file | list | `["GmbH", "AG", "KG", "GmbH & Co. KG", "UG", "e.V.", "GbR", "OHG", "eG", "SE", "mbH", "h...` |
| owner_file.name_max_length | owner_file | integer | `100` |
| owner_file.name_max_names | owner_file | integer | `3` |
| owner_file.name_overflow_suffix | owner_file | string | `"ua"` |
| owner_file.name_separator | owner_file | string | `"-"` |
| owner_file.name_unassigned | owner_file | string | `"Unzugeordnet"` |
| owner_file.name_unknown_owner | owner_file | string | `"Unbekannt"` |
| owner_file.name_unknown_unit_prefix | owner_file | string | `"Unbekannte_WE"` |
| owner_file.transliterate_umlauts | owner_file | boolean | `false` |
| owner_file.unit_number_pad | owner_file | integer | `2` |
| owner_file.unit_prefix_map | owner_file | object | `{"apartment": "WE", "commercial": "GE", "parking": "ST", "garage": "GA", "underground_p...` |
| owner_file.unit_prefix_mode | owner_file | string | `"always_we"` |
| previews.jpeg_quality | previews | integer | `80` |
| previews.long_edge_px | previews | integer | `1200` |
| previews.retention_days_after_resolve | previews | integer | `90` |
| processing.max_parallel_objects | processing | integer | `1` |
| reports.misc_share_target_pct | reports | integer | `5` |
| reports.review_age_warning_days | reports | integer | `10` |
| retention.hint | retention | string | `"durch Geschäftsführung und Steuerberater festzulegen"` |
| review.bulk_max_cases | review | integer | `500` |
| review.list_page_size | review | integer | `50` |
| review.snooze_default_days | review | integer | `7` |
| security.iban_decrypt_roles | security | list | `[]` |
| security.iban_key_version_current | security | integer | `1` |
| security.log_document_views | security | boolean | `true` |
| security.store_full_iban | security | boolean | `false` |
| tenant_file.subfolders | tenant_file | list | `[]` |
| units.normalize_strip_leading_zeros | units | boolean | `true` |
| units.type_prefix_mapping | units | object | `{"WE": "apartment", "WOHNUNG": "apartment", "GE": "commercial", "S": "parking", "ST": "...` |

## Audit-Aktionen

| Aktion | Bedeutung |
|---|---|
| auth.login | Anmeldung |
| auth.login_failed | fehlgeschlagene Anmeldung |
| auth.denied | verweigerter Zugriff (fehlendes Recht) |
| auth.totp_reset | zweiter Faktor zurückgesetzt |
| user.create | Nutzer angelegt |
| user.status | Nutzer gesperrt oder entsperrt |
| user.role | Rolle geändert |
| user.unlock | Sperre nach Fehlversuchen aufgehoben |
| setting.update | Konfigurationswert geändert |
| setting.seed | Konfigurationswert durch Seed überschrieben |
| object.create | Objekt angelegt |
| object.update | Objekt geändert |
| unit.create | Einheit angelegt |
| unit.update | Einheit geändert |
| owner.create | Eigentümer angelegt |
| owner.update | Eigentümer geändert |
| assignment.create | Zuordnung Eigentümer zu Einheit angelegt |
| assignment.end | Zuordnung beendet (Eigentümerwechsel) |
| drive.authorize | Google-Verbindung hergestellt |
| drive.token_refresh | Token erneuert |
| drive.rename | Ordner in Drive umbenannt |
| drive.move | Datei in Drive verschoben |
| drive.create_folder | Ordner in Drive angelegt |
| drive.upload | Datei nach Drive hochgeladen |
| document.view | Dokument aus Eigentümerakte angesehen (nur bei security.log_document_views) |
| document.download | Dokument aus Eigentümerakte heruntergeladen (nur bei security.log_document_views) |
| review.assign | Fall zugewiesen |
| review.confirm | Vorschlag bestätigt |
| review.reclassify | neu klassifiziert |
| review.merge_duplicate | Dublette zusammengeführt |
| review.split | Dokument aufgeteilt |
| review.defer | Fall zurückgestellt |
| review.dismiss | Fall verworfen |
| review.reopen | Fall wiedereröffnet |
| review.bulk_execute | Massenbearbeitung ausgeführt |
| review.transfer_object | in anderes Objekt übernommen |
| review.dismiss_object_case | Fall zur Objektzuordnung verworfen (nur Admin) |
| import.commit | Import übernommen |
| import.rollback | Import zurückgenommen |
| retention.approve | Aufbewahrungsfrist freigegeben |
| deletion.propose | Löschvorschlag erzeugt |
| deletion.execute | Löschung ausgeführt |
| list.generate | Liste erzeugt |
| list.export | Liste heruntergeladen |
| request.approve | Nachforderung freigegeben |
| request.withdraw | Nachforderung zurückgezogen |

## Startparameter (.env)

`IMAGE_TAG`, `APP_UID`, `APP_ENV`, `TZ`, `APP_DOMAIN`, `APP_BASE_URL`, `TESSDATA_VARIANT`, `TRAEFIK_NETWORK`, `TRAEFIK_ENTRYPOINT`, `TRAEFIK_ENTRYPOINT_INSECURE`, `TRAEFIK_CERTRESOLVER`, `TRAEFIK_REDIRECT_ROUTER`, `TRUSTED_PROXY_CIDR`, `MARIADB_TAG`, `REDIS_IMAGE`, `REDIS_TAG`, `DB_NAME`, `DB_USER`, `DB_WORKER_USER`, `DB_MIGRATE_USER`, `DB_BACKUP_USER`, `DB_MAX_CONNECTIONS`, `DB_INNODB_BUFFER_POOL`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`, `OCR_PROCESSES`, `NLP_CONCURRENCY`, `IO_CONCURRENCY`, `WORKER_MAX_TASKS_PER_CHILD`, `WORKER_MAX_MEMORY_PER_CHILD_KB`, `WORKER_STOP_GRACE`, `JOBS_VISIBILITY_TIMEOUT_S`, `WEB_CPUS`, `WEB_MEM`, `WORKER_CPUS`, `WORKER_MEM`, `WORKER_NLP_CPUS`, `WORKER_NLP_MEM`, `WORKER_IO_CPUS`, `WORKER_IO_MEM`, `BEAT_CPUS`, `BEAT_MEM`, `DB_CPUS`, `DB_MEM`, `REDIS_CPUS`, `REDIS_MEM`, `REDIS_MAXMEMORY`, `BACKUP_CPUS`, `BACKUP_MEM`, `CLASSIFIER_CPUS`, `CLASSIFIER_MEM`, `LOG_LEVEL`, `LOG_FORMAT`, `LOG_MAX_SIZE`, `LOG_MAX_FILE`, `DISK_RESERVE_GB`, `BACKUP_CRON`, `BACKUP_RETENTION_DAYS`, `BACKUP_MAX_AGE_HOURS`, `OFFSITE_ENABLED`, `OFFSITE_REMOTE`, `GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI`, `GOOGLE_LOGIN_REDIRECT_URI`, `DRIVE_ACCOUNT_EMAIL`, `DRIVE_SCOPES`, `GOOGLE_LOGIN_ENABLED`, `GOOGLE_LOGIN_HOSTED_DOMAIN`, `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_FROM`, `ALERT_EMAIL_TO`, `ALERTS_ENABLED`, `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS`, `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`, `PASSWORD_MIN_LENGTH`, `REAUTH_TIMEOUT_SECONDS`, `MFA_REQUIRED`

## Rechte (roles.permissions)

| Recht | Bedeutung |
|---|---|
| settings.write | Konfiguration ändern |
| retention.approve | Aufbewahrungsfristen setzen und freigeben |
| users.manage | Nutzer anlegen, sperren, Rolle ändern, TOTP zurücksetzen |
| deletion.approve | Löschläufe freigeben |
| objects.write | Objekte anlegen und bearbeiten, Ordnerabgleich |
| documents.ingest | Dokumente hochladen, Verarbeitung starten |
| review.decide | Review Center bearbeiten, Massenbearbeitung |
| review.dismiss_object_case | Fälle zur Objektzuordnung verwerfen |
| demands.write | Nachforderung erzeugen (Entwurf) |
| demands.approve | Nachforderung freigeben |
| owner_files.read | Eigentümerakten sehen |
| tenant_files.read | Mieterakten sehen |
| masterdata.write | Stammdaten ändern |
| lists.generate | Listen erzeugen und herunterladen |
| status.read | Statusseite lesen |
| status.operate | Statusaktionen (Sweeper, Google-Verbindung erneuern, Listen erzeugen) |
| audit.read_all | Audit vollständig einsehen |
| audit.read_own | Eigene Audit-Einträge einsehen |
| ai.read | KI-Aufrufe und Kosten einsehen |
| imports.write | Import hochladen und bestätigen |
| drive.connect | Google-Drive-Verbindung herstellen und trennen |
