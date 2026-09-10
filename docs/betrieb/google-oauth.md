# Google Drive: OAuth-App und Erstautorisierung (Anleitung, Stand M4)

Verbindliche Parameter und Begründungen stehen in docs/betrieb.md Abschnitt 7 (Beschluss B-16). Diese Anleitung ergänzt die Schritte in der Anwendung. Bildschirmfotos werden nach der Durchführung durch den Auftraggeber ergänzt (ohne Secrets); bis dahin gilt der Text. Voraussetzungen: V-05 (technisches Konto mit 2FA), V-06 (OAuth-App), V-07 (Client-Secret auf dem Server), V-08 (Test-Wurzelverzeichnis), V-09 (Freigabe in der Admin-Console).

## 1. Werte in der Konfiguration

| Ort | Eintrag |
|---|---|
| `.env` | `GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_URI=https://uebernahme.muellerhv.de/auth/google/callback`, `DRIVE_ACCOUNT_EMAIL=ablage@muellerhv.de` |
| Secret-Datei | `/srv/objektakte/secrets/google_client_secret` (Variable `GOOGLE_CLIENT_SECRET_FILE` in Compose) |
| Secret-Datei | `/srv/objektakte/secrets/token_key` (Schlüssel für die Verschlüsselung der Tokens, `TOKEN_KEY`) |
| `app_settings` | `drive.root_folder_id` wird in der Anwendung bestätigt, nicht von Hand eingetragen |

Die Redirect-URI in der Cloud Console muss zeichengleich mit `GOOGLE_REDIRECT_URI` sein (https, ohne Schrägstrich am Ende).

## 2. Ablauf in der Anwendung

1. Als Admin anmelden (Recht `drive.connect`), Menü „Google Drive“ (`/verwaltung/drive/`).
2. „Google Drive verbinden“ startet den Ablauf mit `access_type=offline`, `prompt=consent`, `include_granted_scopes=false`, `state` und PKCE. Vor dem Start verlangt die Anwendung den zweiten Faktor (Step-up).
3. Im Google-Dialog mit `ablage@muellerhv.de` anmelden. Jedes andere Konto wird nach dem Callback abgelehnt und im Protokoll als `drive.authorize` mit Ergebnis `rejected` festgehalten. Ebenso abgelehnt: fehlender Drive-Bereich, fehlender Refresh-Token, ungültiger `state`.
4. Nach Erfolg zeigt die Seite Status `aktiv`, Konto, Bereich, Zeitpunkt der Autorisierung und „Tage ohne Neuanmeldung“.
5. Wurzelordner: Pfad ab „Meine Ablage“ eingeben (Standard `01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten`), „Pfad auflösen“ zeigt die Treffer je Ebene mit Folder-ID. Die richtige ID in „Wurzel bestätigen“ eintragen (Step-up). Bei mehreren Treffern wird nichts geraten. Für Tests die ID des Test-Wurzelverzeichnisses eintragen.
6. Erster Abgleich: Objekt öffnen, „Ordnerabgleiche“, „Probelauf (Dry-Run)“. Der Probelauf schreibt nichts in Drive; die Planliste zeigt Anlagen, Umbenennungen, Registrierungen, geplante Review-Fälle und die Inventur. Danach „Abgleich ausführen“.

## 3. Überwachung und Nachweis (T9)

- `beat` führt stündlich einen Lesetest auf den Wurzelordner und täglich um 04:00 Serverzeit (ANNAHME) einen erzwungenen Refresh aus. Beides erzeugt `drive.token_refresh` im Revisionsprotokoll.
- Bei Fehlern zählt `consecutive_failures` hoch (Anzeige gelb). Antwortet Google mit `invalid_grant`, wechselt der Status auf `widerrufen` (Anzeige rot); Drive-Schreibjobs pausieren, OCR und Klassifikation laufen weiter. Neuautorisierung nur durch einen Admin; sie unterbricht die Nachweiskette.
- Nachweis nach acht Tagen: `docker compose run --rm --no-deps web python manage.py drive_oauth_proof` gibt Token-Metadaten (ohne Chiffrate) und die Ereignisse als Tabelle aus; Ergebnis in `docs/betrieb/oauth-nachweis.md` ablegen. Kriterium: kein `drive.authorize` nach Tag 0, alle Refreshes `ok`, Tage seit Autorisierung größer oder gleich 8.

## 4. Befehle

| Befehl | Zweck |
|---|---|
| `manage.py drive_reconcile --object 623 --dry-run [--export]` | Probelauf für ein Objekt, optional Protokoll als JSON und Excel |
| `manage.py drive_reconcile --all [--dry-run]` | Sammellauf über alle Objekte mit Sammelfassung (Definition of Done) |
| `manage.py drive_undo_rename <action_id>` | Umbenennung des Altordners zurücknehmen (Name aus dem Protokoll) |
| `manage.py drive_export_protocol --run <id> [--format json]` | Protokoll eines Laufs exportieren; `--all` für die Sammelfassung |
| `manage.py drive_oauth_proof` | Nachweistabelle T9 |

Exporte liegen unter `/srv/objektakte/exports/drive-sync/`.

## 5. Live-Test gegen das Test-Wurzelverzeichnis

`tests/integration/drive/test_live.py` läuft nur, wenn `DRIVE_LIVE_TEST_ROOT_ID` und `DRIVE_LIVE_TEST_TOKEN_FILE` gesetzt sind. Der Test bricht ab, wenn die Test-Wurzel gleich `drive.root_folder_id` ist oder fremde Ordner ohne Präfix `TESTLAUF_` enthält, arbeitet nur in einem Laufordner `TESTLAUF_<Zeitstempel>` und verschiebt ihn am Ende in den Papierkorb (nichts wird endgültig gelöscht).

## 6. Zu vermeiden

Wiederholte Erstautorisierungen, Widerruf des App-Zugriffs im Konto, Wechsel des Nutzertyps auf „Extern“, Löschen des OAuth-Clients, ungeplanter Wechsel des Client-Secrets (docs/betrieb.md 7.11).
