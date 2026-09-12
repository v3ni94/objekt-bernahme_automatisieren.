# Synchronisation Paperless-ngx und Google Drive

Stand: 12.09.2026. Betriebsdokumentation der Anbindung an Paperless-ngx und des Drive-Änderungsabgleichs (Modul `src/apps/sync`). Verbindliche Vorgaben zu Verzeichnissen und Secrets stehen in docs/betrieb.md (Abschnitte 3.3 und 3.8), die Befehlsfolge des Deployments in docs/betrieb/deployment.md, Störungsbilder in docs/betrieb/runbook.md. Quelle der Wahrheit für Schlüssel, Endpunkte und Zeiten ist der Code; alle Werte in diesem Dokument stammen aus `src/apps/sync`, `src/objektakte/settings/base.py`, `src/apps/config/catalog.json`, `db/seeds/app_settings.json`, `docker-compose.yml` und `scripts/deploy.sh`.

Offene Punkte beim Betreiber, vor der Einrichtung zu klären: Adresse der Paperless-Instanz, installierte Paperless-Version und die vom Server gemeldete API-Version, Paperless-Benutzer für den API-Token, Platzhaltersyntax der Webhook-Aktion in der installierten Version (Abschnitt 4).

## 1 Zweck und Grenzen

- Die Anwendung ist der einzige Koordinator. Sie entscheidet, was nach Paperless übertragen, was aus Paperless übernommen und was als Konflikt vorgelegt wird. Paperless-Workflows lösen nur aus (Webhook), sie schreiben nichts in die Anwendung.
- Paperless wird ausschließlich über die REST-API angesprochen (Kopfzeile `Authorization: Token`, API-Version über `Accept: application/json; version=<n>`). Kein Zugriff auf Datenbank, Dateisystem oder Consume-Ordner von Paperless.
- Keine Löschspiegelung. Eine Löschung oder ein Papierkorb-Ereignis in Paperless oder Drive setzt nur den Zustand der Verknüpfung (`missing`, `trashed`) und legt einen Konfliktfall an. Die Anwendung löscht weder lokal noch in einem der beiden Systeme; der Paperless-Client besitzt keine Löschfunktion.
- Originale in Drive werden nie überschrieben. Aus Paperless fließt kein Dateiinhalt nach Drive. Native Google-Dokumente bleiben bearbeitbar in Drive, Paperless erhält eine als Exportfassung gekennzeichnete PDF. An Drive-Dateien setzt die Anwendung nur technische Kennzeichen (`appProperties`: `mhv_uuid`, `sha256`, `document_id`, `object_id`, `mhv_gen`).
- Schreibzugriffe nach Paperless laufen nur, wenn `paperless.enabled` an ist, Adresse und Token vorliegen und das Objekt nach `paperless.mode` freigegeben ist (Funktion `writes_allowed`). Das Eingangsobjekt gilt in den Modi pilot und full immer als freigegeben.
- Keine automatische Konfliktauflösung nach Zeitstempel. Jede Entscheidung trifft ein Mensch im Review Center und wird protokolliert.
- Datenschutz: In Paperless landen nur Objektnummer, Ort und Straße (Feld Objekt), der Zuordnungsstatus, die Dokument-UUID und der Drive-Link. Keine Personendaten in Feldern, Indexbelegen oder Protokollen. Das Token erscheint in keiner Protokollzeile (Maskierung im Client).

## 2 Voraussetzungen

| Bereich | Anforderung | Herkunft |
|---|---|---|
| Paperless-ngx | REST-API mit API-Version 9 oder 10. Der Client fordert API-Version 10 an (`paperless.api_version`; zulässig 9 und 10, andere Werte fallen auf 10 zurück) und liest die vom Server gemeldete Version aus dem Antwortkopf `X-Api-Version`, die Serverversion aus `X-Version` oder ersatzweise aus `/api/ui_settings/`. Beide Werte zeigt der Verbindungstest an. | `paperless/client.py` |
| Erkannte Serverfunktionen | `document_versions` (Endpunkt `update_version` im Schema oder Felder `versions` und `root_document` in der Dokumentliste), `custom_fields` (`/api/custom_fields/` erreichbar), `bulk_edit`, `tasks_v10` (API ab 10), `schema_available` (`/api/schema/` lesbar). Der Befund steht nach dem Verbindungstest in `sync_cursors` (paperless, connection). | `client.server_info()` |
| Feldsuche | Die Suche nach der Dokument-UUID nutzt den Filter `custom_field_query` (laut Client ab Paperless-ngx 2.13). Fehlt er, arbeitet die Übertragung ohne diese Vorprüfung; die Dublettenerkennung von Paperless über die Prüfsumme bleibt wirksam. | `client.find_by_custom_field` |
| Netz | `web` und `worker-io` erreichen die Paperless-Adresse über das Netz `egress`; TLS-Prüfung nach `paperless.verify_tls` (Standard an). Adresse mit https und ohne `/api`. | `docker-compose.yml`, Katalog |
| Google Drive | Bestehende Drive-Verbindung mit bestätigter Wurzel `drive.root_folder_id` (docs/betrieb/google-oauth.md). Die Synchronisation nutzt denselben Adapter; zusätzliche Google-Berechtigungen sind nicht erforderlich. | `services.get_drive()` |
| Rechte | Einrichtung durch einen Admin mit `sync.manage`; Bearbeitung des Eingangs mit `inbox.work` (Abschnitt 11). | `accounts/permissions.py`, `db/seeds/roles.json` |

## 3 Einrichtung Schritt für Schritt

### 3.1 Paperless-Benutzer und API-Token

1. In Paperless einen eigenen Benutzer für die Anwendung anlegen (kein persönliches Konto; so weisen die Protokolle in Paperless die Anwendung aus und ein Personalwechsel entwertet den Token nicht).
2. Berechtigungen des Benutzers, abgeleitet aus den Aufrufen der Abläufe: Dokumente anzeigen, hinzufügen und ändern (Liste und Detail `/api/documents/`, Metadaten, Download, `post_document`, `bulk_edit` mit `modify_tags` und `modify_custom_fields`, bei Servern mit Dokumentversionen `update_version`); Tags anzeigen und anlegen; benutzerdefinierte Felder anzeigen und anlegen; Aufgaben (`/api/tasks/`) anzeigen. Löschrechte sind nicht erforderlich; der Client hat keine Löschfunktion. `/api/schema/`, `/api/ui_settings/` und `/api/status/` werden beim Verbindungstest nur als Probe abgefragt; ein Verbot dort stört den Betrieb nicht. Korrespondenten anzeigen (`/api/correspondents/`, Name des Absenders als Lieferantenmerkmal der Lernfunktion; ohne dieses Recht fehlt nur das Merkmal). Weitere Methoden des Clients (Notizen, Teilaktualisierung, Dokumenttypen) rufen die Abläufe derzeit nicht auf.
3. Sichtbarkeit prüfen: Paperless kennt Berechtigungen je Dokument. Der Benutzer muss die Dokumente sehen dürfen, die abgeglichen werden sollen; Dokumente, die die Anwendung anlegt, laufen unter diesem Benutzer. Wie der vorhandene Bestand für ihn freigegeben wird, ist beim Betreiber zu klären.
4. API-Token für diesen Benutzer in Paperless erzeugen. Der Token wird direkt auf dem Server eingetragen (3.2), nie per E-Mail oder Chat übertragen.

### 3.2 Secrets auf dem Server

Zwei Dateien unter `/srv/objektakte/secrets/`, eingebunden als `/run/secrets/paperless_token` und `/run/secrets/paperless_webhook_token` (Variablen `PAPERLESS_TOKEN_FILE` und `PAPERLESS_WEBHOOK_TOKEN_FILE`, gelesen in `settings.OBJEKTAKTE`).

| Datei | Inhalt | Erzeugung | Verwendet von |
|---|---|---|---|
| `paperless_token` | API-Token des Paperless-Benutzers aus 3.1 | Paperless | `web` (Verbindungstest), `worker-io` (alle Operationen) |
| `paperless_webhook_token` | Gemeinsames Geheimnis des Webhooks, Kopfzeile `X-MHV-Webhook-Token` | `openssl rand -hex 32` | `web` (Webhook), Paperless-Workflow |

```bash
cd /srv/objektakte/secrets
printf '%s' '<api-token-aus-paperless>' | sudo tee paperless_token >/dev/null
printf '%s' "$(openssl rand -hex 32)"   | sudo tee paperless_webhook_token >/dev/null
sudo chown root:root paperless_token paperless_webhook_token
sudo chmod 444 paperless_token paperless_webhook_token
sudo cat paperless_webhook_token   # Wert für den Paperless-Workflow (Abschnitt 4), danach Terminal leeren
```

Hinweise:

- Eine Zeile ohne Zeilenumbruch (`printf`, nicht `echo`); die Anwendung entfernt Leerzeichen am Rand.
- Dateirechte 0444 mit Eigentümer root, Verzeichnis 0700 root (docs/betrieb.md 3.8). Die Dienste laufen unprivilegiert als `APP_UID` und können 0600-Dateien nicht lesen; der Container bricht dann mit `PermissionError` auf `/run/secrets/...` ab (Runbook).
- Beide Dateien müssen vor `docker compose up` vorhanden sein. `scripts/deploy.sh` legt fehlende Dateien als leere Platzhalter mit Rechten 0444 und Eigentümer root an, sofern es sie prüfen kann (sudo ohne Passwort oder beschreibbares Verzeichnis). Auf dem bestehenden Server ist beides nicht gegeben; der Deploy-Nutzer kann in das Verzeichnis (root, 0700) nicht hineinsehen und meldet nur einen Hinweis. Fehlt eine Datei tatsächlich, bricht `docker compose` beim Start mit einer klaren Meldung ab; dann einmalig als root anlegen: `install -m 0444 -o root -g root /dev/null /srv/objektakte/secrets/<name>` (auf diesem Server am 12.09.2026 erledigt). Leer bedeutet: Anbindung aus (`configured()` ist falsch, es entsteht kein Client).
- Die Werte werden beim Start des Prozesses gelesen. Nach dem Eintragen oder Wechseln auf dem Server als Deploy-Nutzer: `docker compose restart web worker-io`. Wurde die Datei nicht in place beschrieben, sondern durch eine neue Datei ersetzt (Editor), Container neu erstellen: `docker compose up -d --force-recreate web worker-io`. Der Workflow `Deploy` (docs/betrieb/github-deploy.md) hat keine eigene Neustart-Aktion; seine Aktion `deploy` erstellt Container nur bei geändertem Image neu.
- `beat` erhält die Paperless-Secrets nicht und braucht sie nicht.
- Rotation: neuen Token in Paperless erzeugen, Datei überschreiben, Dienste neu starten, alten Token in Paperless entfernen. Webhook-Geheimnis: Datei neu befüllen, Dienste neu starten, danach die Kopfzeile im Paperless-Workflow anpassen.

### 3.3 Konfigurationsschlüssel

Alle fachlichen Werte liegen in `app_settings` (Verwaltung, Konfiguration; Recht `settings.write`) oder werden über die Workflow-Aktion `config-set` gesetzt (Argument `schluessel=wert`; der Workflow lässt nur Buchstaben, Ziffern und `@._+=-` zu, also Zahlen, Wahrheitswerte und Text ohne Leerzeichen; Listen wie `paperless.pilot_object_numbers` über das Formular). Startwerte aus `db/seeds/app_settings.json`:

| Schlüssel | Startwert | Bedeutung |
|---|---|---|
| `paperless.base_url` | leer | Basisadresse der Instanz mit https, ohne `/api` |
| `paperless.enabled` | false | Hauptschalter; aus stoppt jeden Abgleich, gesicherte Daten bleiben |
| `paperless.mode` | readonly | readonly (nur lesen und inventarisieren), pilot (Schreiben nur für Objekte in `paperless.pilot_object_numbers` und den Eingang), full (alle Objekte) |
| `paperless.pilot_object_numbers` | leer | Objektnummern des Pilotumfangs, nur im Modus pilot wirksam; führende Nullen werden ignoriert |
| `paperless.tag_name` | MHV-Sync | Kennzeichnungs-Tag für Dokumente, die die Anwendung führt |
| `paperless.field_uuid_name` | MHV Dokument-UUID | Feld für die Dokument-UUID der Anwendung (Typ string) |
| `paperless.field_object_name` | MHV Objekt | Feld für den Objektbezug (Typ string) |
| `paperless.field_status_name` | MHV Zuordnung | Feld für den Zuordnungsstatus (Typ string) |
| `paperless.field_drive_name` | MHV Drive-Link | Feld für den Drive-Link (Typ url) |
| `paperless.poll_interval_minutes` | 5 | Abstand des regelmäßigen API-Abgleichs; holt verpasste Webhook-Ereignisse nach |
| `paperless.webhook_enabled` | true | Webhook-Endpunkt annehmen; aus liefert 404 |
| `paperless.import_new_documents` | true | In Paperless neu eingegangene Dokumente übernehmen |
| `paperless.max_upload_mb` | 100 | Größte Datei, die nach Paperless übertragen wird; größere erhalten einen Indexbeleg |
| `paperless.verify_tls` | true | TLS-Zertifikat prüfen; nur für interne Testinstanzen abschaltbar |
| `paperless.timeout_seconds` | 30 | Zeitlimit je API-Aufruf |
| `paperless.page_size` | 100 | Seitengröße der Listenabfragen |
| `paperless.api_version` | 10 | Angeforderte API-Version (9 oder 10) |
| `sync.drive_changes_enabled` | false | Änderungsprotokoll von Drive regelmäßig lesen |
| `sync.drive_changes_interval_minutes` | 5 | Abstand des Drive-Änderungsabgleichs |
| `sync.inbox_folder_name` | _Eingang_Nicht_zugeordnet | Name des Eingangsordners unter der Drive-Wurzel und des Eingangsobjekts |
| `sync.inbox_folder_id` | leer | Ordner-ID des Eingangsordners nach Einrichtung (wird gesetzt) |
| `sync.inbox_object_number` | 0 | Objektnummer des technischen Eingangsobjekts |
| `sync.assignment_auto_min` | 0,85 | Mindestbewertung für eine automatische Objektzuordnung |
| `sync.assignment_gap_min` | 0,25 | Mindestabstand zum zweitbesten Kandidaten |
| `sync.rule_min_confirmations` | 2 | Bestätigte Beispiele, ab denen eine Zuordnungsregel entsteht |
| `sync.operation_max_attempts` | 5 | Höchstzahl der Versuche je Operation |
| `sync.inventory_page_size` | 200 | Paketgröße des Bestandslaufs je Schritt |

Empfohlene Reihenfolge: `paperless.base_url` setzen, Verbindungstest (3.4) im Modus readonly, Trockenlauf des Bestands (Abschnitt 6), dann `paperless.mode` auf pilot mit `paperless.pilot_object_numbers`, Kennzeichnung einrichten, Eingang einrichten (3.5), zuletzt `paperless.enabled` auf true. Der Verbindungstest braucht nur Adresse und Token, nicht den Hauptschalter.

### 3.4 Verbindungstest und Einrichtung der Kennzeichnung

Seite `/verwaltung/sync/` (Recht `sync.manage`), Abschnitt Verbindung:

- Verbindung prüfen (POST `/verwaltung/sync/pruefen/`): liest `/api/documents/` mit einer Zeile, Tags und benutzerdefinierte Felder; meldet Serverversion, API-Version, erkannte Funktionen und die fehlenden Elemente der Kennzeichnung. Ergebnis in `sync_cursors` (paperless, connection) mit `tag_id` und `field_ids`; Protokoll `sync.paperless_check`.
- Kennzeichnung einrichten (gleiches Formular, `einrichten=1`): legt den Tag und die vier Felder an, die fehlen. Nicht im Modus readonly; dort meldet die Seite nur, was fehlt. Protokoll `sync.paperless_setup`.
- Schlägt die Verbindung fehl, bleibt der zuletzt bekannte Befund (Tag-ID, Feld-IDs, Serverstand) erhalten, `ok` ist bis zum nächsten erfolgreichen Test falsch.
- Ohne vollständige Kennzeichnung warten Metadatenoperationen 900 Sekunden mit dem Hinweis „Kennzeichnung in Paperless nicht eingerichtet“ und werden danach erneut geprüft; sie zählen keinen Fehlversuch.

### 3.5 Eingangsobjekt und Eingangsordner

Schaltfläche Eingang einrichten auf `/verwaltung/sync/` (erneute Anmeldung erforderlich):

- legt das technische Eingangsobjekt an (Nummer `sync.inbox_object_number`, Name `sync.inbox_folder_name`, Verwaltungsart rental, Status active, Kennzeichen `is_system_inbox`). Es erscheint in keiner Objektliste, bekommt keine Ordnerstruktur, keine Listen und keine Vollständigkeitsbewertung.
- legt unter der bestätigten Drive-Wurzel den Ordner `sync.inbox_folder_name` an oder verwendet einen vorhandenen Ordner gleichen Namens; speichert die ID in `sync.inbox_folder_id` und am Eingangsobjekt. Ohne bestätigte Drive-Wurzel oder ohne Google-Verbindung bleibt es beim Objekt, die Seite meldet das.
- Dokumente ohne Objektbezug (aus Paperless ohne Feld Objekt, aus dem Drive-Eingangsordner) laufen als Dokumente des Eingangsobjekts durch die Pipeline (Hash, Text, Klassifikation) und erhalten danach einen Zuordnungsvorschlag im Dokumenteneingang `/eingang/` (Schwellen `sync.assignment_auto_min`, `sync.assignment_gap_min`). Nach Zuordnung wandert das Dokument in das Zielobjekt, die Drive-Datei wird verschoben, nie kopiert.

## 4 Webhook

Endpunkt: `POST https://<app-host>/webhooks/paperless/` (URL-Name `webhook_paperless`), ohne Sitzung und ohne CSRF-Token.

| Element | Wert |
|---|---|
| Methode | POST |
| Kopfzeile | `X-MHV-Webhook-Token: <Inhalt von paperless_webhook_token>`; alternativ `Authorization: Bearer <Inhalt>` |
| Body | JSON `{"doc_id": <Paperless-ID>}`; angenommen werden auch `document_id`, `doc_pk`, `id` oder `doc_url` (die Anwendung liest die Nummer am Ende der Adresse); optional `event` (bis 24 Zeichen, geht in den Operationsschlüssel ein). Formularkodierte Bodys werden ebenfalls gelesen. |
| Antworten | 202 `{"accepted": true, "operation": <id>, "created": true/false}`; 202 `{"accepted": true, "ignored": "paperless inaktiv"}` bei ausgeschaltetem Hauptschalter oder fehlender Adresse oder Token (`active()` falsch); 400 ohne `doc_id` oder bei ungültigem JSON; 401 ohne oder mit falschem Geheimnis (Protokoll `auth.denied`); 404 bei `paperless.webhook_enabled` false |

Einrichtung in Paperless: Workflow mit den Auslösern Dokument hinzugefügt und Dokument aktualisiert, Aktion Webhook, obige Adresse, Methode POST, Body als JSON, Kopfzeile wie oben. Beispiel für den Body: `{"doc_id": "{{doc_id}}", "event": "added"}`. Name und Schreibweise des Platzhalters richten sich nach der Paperless-Dokumentation zu Workflows in der installierten Version (offener Punkt); steht kein Platzhalter für die ID zur Verfügung, genügt `{"doc_url": "<Platzhalter für die Dokumentadresse>"}`. Die Datei nicht mitsenden: Der Body ist nur Auslöser.

Sicherheitsmodell: Paperless signiert Webhooks nicht. Deshalb dient das gemeinsame Geheimnis in der Kopfzeile als Zugangsschutz (Vergleich in konstanter Zeit), und die Verarbeitung liest das Dokument ausschließlich über die API. Kein Wert aus dem Body wird als Wahrheit übernommen; erfundene Felder im Body bleiben ohne Wirkung.

Verhalten: Je Dokument, Ereignis und Minute entsteht höchstens eine Operation `paperless_pull` (Priorität 50, vor dem regelmäßigen Abgleich). Fällt der Webhook aus (Paperless ohne Netzverbindung zur Anwendung, falsche Adresse, Geheimnis geändert), holt der regelmäßige Abgleich nach `paperless.poll_interval_minutes` alle Dokumente mit geändertem `modified` nach; der Webhook beschleunigt nur.

Prüfung von Hand (Geheimnis nicht in der Shell-Historie ablegen):

```bash
curl -sS -X POST "https://<app-host>/webhooks/paperless/" \
  -H "Content-Type: application/json" \
  -H "X-MHV-Webhook-Token: <inhalt-der-datei>" \
  -d '{"doc_id": 1}'
```

Erwartet: 202 mit `operation` und `created`; die Operation erscheint auf `/verwaltung/sync/` unter Letzte Operationen, im Protokoll `sync.webhook`.

## 5 Laufende Verarbeitung

### 5.1 Zeitplan (Celery Beat, `CELERY_BEAT_SCHEDULE`, Serverzeit)

| Eintrag | Task | Zeit | Wirkung |
|---|---|---|---|
| `sync-dispatch` | `sync.dispatch_due` | jede Minute | gibt hängende Operationen frei (Heartbeat älter als 30 Minuten) und reiht bis zu 200 fällige Operationen nach Priorität ein |
| `sync-paperless-poll` | `sync.paperless_poll` | Minute 0, 5, 10 ... | Abgleich nach dem Cursor `modified_cursor`, wenn `paperless.poll_interval_minutes` abgelaufen ist und der Hauptschalter an ist; höchstens 2.000 Dokumente je Lauf |
| `sync-drive-changes` | `sync.drive_changes` | Minute 2, 7, 12 ... | Drive-Änderungsprotokoll nach dem Cursor `page_token`, wenn `sync.drive_changes_enabled` an ist und `sync.drive_changes_interval_minutes` abgelaufen ist; höchstens 20 Seiten je Lauf |
| `sync-derive-rules` | `sync.derive_rules` | täglich 01:15 | leitet Zuordnungsregeln aus bestätigten Beispielen ab (`sync.rule_min_confirmations`) |

Manuell auf `/verwaltung/sync/`: Paperless jetzt abgleichen, Drive-Änderungen jetzt lesen (beide ohne Intervallprüfung), Fällige Operationen einreihen.

Alle Tasks der Synchronisation laufen in der Queue `io` (Dienst `worker-io`, Threads). Nur dieser Worker schreibt nach Paperless und Drive; `web` ruft Paperless nur beim Verbindungstest auf, `beat` nie. Der Drive-Änderungsabgleich setzt beim ersten Lauf nur einen Ausgangspunkt (kein Rückblick) und registriert danach neue Dateien in Objektordnern und im Eingangsordner, vermerkt Papierkorb und Entfernen bekannter Dateien und legt Inhaltsänderungen als Konflikt vor.

### 5.2 Operationsliste

Jede Schreib- oder Leseaktion gegen ein externes System ist eine dauerhafte Operation (`sync_operations`) mit eindeutigem Schlüssel. Ein zweites Einreihen mit gleichem Schlüssel erzeugt keine zweite Zeile; eine abgeschlossene Operation wird nicht wiederholt, eine fehlgeschlagene, verworfene oder blockierte wird beim nächsten Einreihen neu gestartet.

| Art | Auslöser | Wirkung |
|---|---|---|
| `paperless_push` | Hash eines Dokuments (nicht aus Paperless), echter Bestandslauf, Entscheidung reupload | prüft zuerst per UUID-Suche, ob die Kopie schon in Paperless liegt; sonst Upload mit Tag und Feldern oder Weiche zu Exportfassung oder Indexbeleg |
| `paperless_await_task` | nach jedem Upload, 5 Sekunden verzögert | verfolgt die Paperless-Aufgabe, legt die Verknüpfung an, erkennt Dubletten |
| `paperless_push_meta` | Ablage in Drive, Objektübernahme, Verknüpfung, Entscheidung push_local_object | schreibt die vier Felder und den Tag; unveränderter Stand löst keinen Schreibzugriff aus |
| `paperless_pull` | Webhook, regelmäßiger Abgleich, echter Bestandslauf | übernimmt neue Dokumente, verknüpft identische Dateien, meldet zweite Kopien als Konflikt, vergleicht bekannte, vermerkt Löschung und Papierkorb |
| `paperless_index_stub` | nicht übertragbare Datei | Indexbeleg als PDF nach Paperless |
| `drive_export_snapshot` | natives Google-Dokument | PDF-Export nach Paperless |
| `drive_set_props` | Ablage in Drive (bei aktiver Anbindung oder Drive-Änderungsabgleich) | technische Kennzeichen an der Drive-Datei, Drive-Verknüpfung |

Ausgänge eines Laufs und Wiederholung:

| Ausgang | Zustand danach | Zählt als Versuch | Wartezeit |
|---|---|---|---|
| erledigt | `done` | ja | keine |
| nichts zu tun (Skip) | `skipped` | ja | keine |
| Voraussetzung fehlt (Defer) | `pending` | nein | 300 Sekunden Standard; 30 Sekunden bis Paperless die Aufgabe zeigt; 600 Sekunden ohne Client oder Google-Verbindung; 900 Sekunden ohne Kennzeichnung |
| vorübergehender Fehler (Retry) | `pending` | ja | 30 Sekunden, je Versuch verdoppelt, höchstens 3.600 Sekunden; bei Ratenbegrenzung der Wert aus `Retry-After`, sonst 60 Sekunden |
| Versuche erschöpft | `failed` | | nach `sync.operation_max_attempts` Versuchen (Startwert 5, beim Einreihen festgeschrieben) |
| dauerhaft nicht lösbar (Block) | `blocked` | | sofort, ohne weitere Versuche |
| unerwarteter Fehler | wie Retry | ja | Backoff wie oben, sichtbar im Protokoll |

Zustände: `pending` (wartet), `running` (läuft), `done`, `failed`, `blocked`, `cancelled` (verworfen), `skipped`. Bedeutung im Betrieb:

- `blocked` heißt Konfiguration oder Rechte: Schreiben für dieses Objekt nicht erlaubt (Modus oder Pilotumfang), Zugriff verweigert (401 oder 403, Token oder Rechte prüfen), vom Server nicht unterstützte Funktion, Dokument in Paperless nicht gefunden, Paperless meldet eine Dublette ohne Kennung oder eine fehlgeschlagene Aufgabe, externe Kennung bereits mit einem anderen Dokument verknüpft, Größengrenze überschritten, leere Exportdatei, Aufgabe erfolgreich ohne Dokument-ID, keine Verarbeitungsroutine für die Art. Eine blockierte Operation läuft erst nach einem Eingriff wieder: erneut auf `/verwaltung/sync/` oder `/eingang/` (Versuchszähler auf 0) oder automatisch beim nächsten Einreihen desselben Schlüssels.
- `failed` heißt: nach den Wiederholungen weiterhin vorübergehender Fehler (Paperless nicht erreichbar, 5xx, Ratenbegrenzung, Quelldatei nicht verfügbar). Nach Behebung erneut einreihen.
- Hängende Operationen (`running` mit Heartbeat älter als 30 Minuten, Worker verloren) gibt `sync-dispatch` jede Minute wieder frei; die Schaltfläche Fällige Operationen einreihen reiht nur wartende Operationen ein und gibt keine hängenden frei. Zeitgrenze je Task 1.500 Sekunden (weich), 1.620 Sekunden (hart).
- Zusätzlich unternimmt der Client je Aufruf bei 429, 5xx und Verbindungsfehlern bis zu fünf Versuche mit steigender Wartezeit (1 Sekunde verdoppelt bis 60 Sekunden, `Retry-After` wird beachtet). Uploads werden nach Verbindungsabbruch nicht blind wiederholt, weil der Server die Datei bereits angenommen haben kann; die Operation geht in Retry, und der nächste Versuch sucht zuerst per UUID.

Sichtbarkeit: Fehlerwarteschlange (`failed` und `blocked`) und Letzte Operationen auf `/verwaltung/sync/`; Synchronisationsfehler auf `/eingang/` (Reiter Fehler). Protokolleinträge unter anderem `sync.paperless_push`, `sync.paperless_pull`, `sync.paperless_meta`, `sync.paperless_index_stub`, `sync.drive_snapshot`, `sync.drive_props_set`, `sync.drive_changes`, `sync.webhook`, `sync.operation_retry`, `sync.operation_cancelled`.

## 6 Bestandslauf

Start auf `/verwaltung/sync/` (Bestandslauf starten, POST `/verwaltung/sync/bestand/starten/`) mit Art `paperless` oder `drive`, Option echter Lauf (`echt=1`, sonst Trockenlauf) und optional Objektnummern (kommagetrennt). Je Art läuft höchstens ein Bestandslauf gleichzeitig. Der Lauf arbeitet in Paketen von `sync.inventory_page_size` Dateien je Schritt (Paperless höchstens 200 je Seite), verkettet über den Task `sync.inventory_step`, und ist nach Abbruch fortsetzbar (Seitenstand in `page_state`).

- Trockenlauf zuerst. Er schreibt nur das Manifest (`InventoryItem` je Datei) und ändert nichts in Paperless, Drive oder an den Dokumenten. Detailseite `/verwaltung/sync/bestand/<id>/` mit Zusammenfassung und Manifest, Filter nach Disposition (`stand`).
- Pilotumfang: Die Objektnummern begrenzen beim Drive-Lauf die Objektwurzeln (der Eingangsordner ist dann nicht dabei). Der Paperless-Lauf liest immer den gesamten sichtbaren Bestand; geschrieben wird beim echten Lauf trotzdem nur nach `writes_allowed`.
- Manifest, Dispositionen: `link_existing` (vorhandenes Dokument verknüpft), `import_new` (neu übernommen oder zu übertragen), `duplicate` (Prüfsumme bei mehreren lokalen Dokumenten), `export_snapshot` (Google-Dokument), `index_stub`, `unsupported`, `out_of_scope` (Papierkorb in Paperless, Drive-Verknüpfungsdatei), `in_progress` (Operation läuft), `error`, `pending`.
- Vollständigkeitsregel: Der Lauf gilt nur dann als vollständig (`counters.complete`), wenn er den Status `done` erreicht und kein Eintrag `pending` oder `error` ist. Offene Fehler bleiben sichtbar und werden nicht weggezählt; die Detailseite überträgt den Ausgang der Operationen in das Manifest.
- Pause und Fortsetzen: Aktionen pause, resume (auch nach `failed`), abort auf der Detailseite; ohne Celery zusätzlich step. Ein Fehler im Schritt setzt den Lauf auf `failed` mit Fehlertext statt in eine Endlosschleife.
- Cursor vor Inventar: Vor dem Drive-Lauf wird der Ausgangspunkt der Changes-API gesichert (`cursor_before.drive_page_token`), vor dem Paperless-Lauf der aktuelle `modified_cursor` samt Startzeit. Änderungen während des Laufs werden dadurch vom anschließenden Änderungsabgleich erfasst, nicht übersehen.
- Echter Lauf: Paperless-Einträge ohne Verknüpfung werden als `paperless_pull` (Priorität 120, nach dem laufenden Betrieb) übernommen; registrierte Drive-Dateien ohne Paperless-Verknüpfung als `paperless_push`, sofern das Objekt freigegeben ist; noch nicht registrierte Drive-Dateien werden als Dokument (Quelle drive_existing) angelegt und durchlaufen die Pipeline, die Übertragung folgt nach dem Hash. Google-Dokumente erhalten nach der Registrierung eine Exportfassung.

Empfohlene Abfolge im Pilot: Modus readonly, Trockenlauf Paperless und Drive, Manifest sichten; Modus pilot mit Pilotobjekten und Hauptschalter an; echter Drive-Lauf für die Pilotobjekte; Fehlerwarteschlange und Dokumenteneingang prüfen; erst danach Umfang erweitern.

## 7 Konflikte und Löschungen

Konflikte sind Review-Fälle vom Typ `sync_conflict`; je Dokument, Art und Anlass gibt es höchstens einen offenen Fall. Entscheidung auf der Falldetailseite des Review Centers (`/review/<id>/`, Aktion Konflikt entscheiden mit Begründung, Recht `review.decide`); Übersicht im Dokumenteneingang `/eingang/?art=konflikt`. Protokoll `sync.conflict_resolved`, bei bestätigter Löschung zusätzlich `sync.tombstone`.

| Fallart | Erkennung | Verknüpfung danach | Zulässige Entscheidungen |
|---|---|---|---|
| `content_changed_remote` | Prüfsumme in Paperless weicht vom zuletzt abgeglichenen Stand ab | `changed_remote` | keep_local |
| `object_changed_remote` | Feld Objekt in Paperless nennt ein anderes Objekt (Dokument nicht im Eingang) | `conflict` | apply_remote_object, push_local_object |
| `deleted_remote` | Dokument in Paperless nicht mehr vorhanden (404) | `missing` | keep_local, tombstone, reupload |
| `trashed_remote` | Dokument in Paperless im Papierkorb (`deleted_at`) | `trashed` | keep_local, tombstone |
| `duplicate_remote` | zweite Kopie in Paperless (UUID-Feld oder Prüfsumme) zu einem Dokument, das bereits mit einer anderen Paperless-ID verknüpft ist; nichts wird geladen oder umgehängt | unverändert, bleibt auf der bekannten Kopie | keep_local |
| `checksum_mismatch` | nach dem Upload meldet Paperless eine andere Prüfsumme als lokal | `synced` (Fall bleibt offen) | keep_local, reupload |
| `drive_trashed` | Drive-Datei im Papierkorb | `trashed` | keep_local, tombstone |
| `drive_removed` | Drive-Datei entfernt oder kein Zugriff mehr | `missing` | keep_local, tombstone |
| `drive_content_changed` | md5 der Drive-Datei weicht vom registrierten Stand ab | `changed_remote` | keep_local |

Bedeutung der Entscheidungen:

- `keep_local`: lokaler Stand bleibt, die Gegenseite gilt als bekannt (neue Prüfsumme wird als akzeptiert vermerkt); bei Papierkorb oder Löschung bleibt der Zustand `trashed` oder `missing` stehen. Nichts wird übertragen.
- `apply_remote_object`: Zuordnung aus Paperless übernehmen; das Dokument wandert in das genannte Objekt (Objektübernahme wie im Dokumenteneingang, Drive-Datei wird verschoben).
- `push_local_object`: eigene Zuordnung zurückschreiben (neue Operation `paperless_push_meta`); nur, wenn Schreiben für das Objekt erlaubt ist.
- `tombstone`: Löschung bestätigen. Die Verknüpfung erhält die dauerhafte Löschmarkierung, eine spätere Kopie derselben Paperless-ID wird nicht erneut übernommen. Das lokale Dokument und die Drive-Datei bleiben unangetastet; auch in Paperless löscht die Anwendung nichts.
- `reupload`: Verknüpfung verwerfen und Datei erneut nach Paperless übertragen (neue Operation `paperless_push`).

Nennt das Feld Objekt in Paperless ein Objekt für ein Dokument, das noch im Eingang liegt, entsteht kein Konflikt, sondern ein Zuordnungsvorschlag (`object_assignment`, Untertyp `from_paperless`) mit hoher Bewertung; auch dieser wird von einer Person bestätigt. Es gibt in keiner Fallart eine automatische Auflösung nach dem neuesten Zeitstempel.

## 8 Versionen und Darstellungen

Rollen der Verknüpfung (`sync_links`, System paperless): `original` (Datei selbst), `snapshot` (Exportfassung eines Google-Dokuments), `index_stub` (Indexbeleg); die im Modell vorgesehene Rolle `archive` wird derzeit nicht verwendet. Zustände: `pending`, `linked`, `synced`, `changed_remote`, `conflict`, `missing`, `trashed`, `no_access`, `tombstone`.

| Datei | Darstellung in Paperless | Rolle | Regeln |
|---|---|---|---|
| Endungen `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.gif`, `.webp`, `.bmp`, `.txt`, `.docx`, `.xlsx`, `.pptx`, `.doc`, `.xls`, `.ppt`, `.odt`, `.ods`, `.odp`, `.rtf`, `.eml`, `.csv` bis `paperless.max_upload_mb` (`PAPERLESS_SUFFIXES` in `flows/paperless_push.py`) | Original hochgeladen | `original` | Titel ist der Dateiname ohne Endung (bis 128 Zeichen), Dateiname unverändert, Datum ist das Dokumentdatum, Tag und vier Felder gesetzt. Ob Paperless Office- und E-Mail-Formate verarbeitet, hängt von der Paperless-Installation ab; lehnt Paperless die Datei ab, endet die Aufgabe fehlgeschlagen und die Operation blockiert. |
| Native Google-Dokumente (`application/vnd.google-apps.*`) | PDF-Export | `snapshot` | Titel „Exportfassung: <Name>“, Dateiname `<Name>_Export.pdf`, Feld Zuordnung „Exportfassung (Snapshot) der Drive-Version <Revision>; Original bleibt in Drive“. Neuer Export nur nach Änderung der Quelle (Revision); bietet der Server Dokumentversionen an, wird die vorhandene Exportfassung als neue Version fortgeschrieben, sonst entsteht ein neues Dokument. Leerer Export oder Export über der Größengrenze blockiert. |
| Andere Endungen oder Datei größer als `paperless.max_upload_mb` | Indexbeleg, eine PDF-Seite „Indexbeleg (kein Original)“ | `index_stub` | Inhalt: Grund, Dateiname, Objekt, Dokument-UUID, Drive-ID, Drive-Link, Größe, Typ, Erstellzeit. Titel „Indexbeleg: <Name>“, Dateiname `Indexbeleg_<UUID>.pdf`, Feld Zuordnung „Indexbeleg: <Grund>“. Der Beleg macht die Datei im DMS auffindbar und ersetzt weder das Original noch eine Volltextindexierung. |

Richtung Paperless nach Anwendung: Das Original wird per Download geladen (Größengrenze `documents.max_download_bytes`, Startwert 524.288.000 Byte; darüber blockiert die Operation). Dokumente, deren UUID-Feld oder Prüfsumme ein lokales Dokument trifft, werden nur verknüpft, nicht erneut angelegt. Exportfassungen und Indexbelege tragen UUID-Feld und Rolle und werden beim Rückabgleich nie als eigenes Quelldokument übernommen. Dokumente mit Quelle Paperless werden nicht wieder nach Paperless hochgeladen; sie erhalten nach Ablage oder Zuordnung nur die Felder.

## 9 Prioritäten je Feld

| Feld | Führendes System | Verhalten der Anwendung |
|---|---|---|
| Volltext und OCR-Ergebnis in Paperless | Paperless | wird nie geschrieben oder überschrieben; die Anwendung führt ihren eigenen Text |
| Tags | Paperless | nur der eigene Tag (`paperless.tag_name`) wird ergänzt (`modify_tags`, nur hinzufügen); fremde Tags bleiben |
| Notizen | Paperless | werden nie geschrieben; der Client kann sie lesen, die Abläufe rufen das derzeit nicht auf |
| Titel | Paperless nach dem Upload | wird beim Upload aus dem Dateinamen gesetzt; spätere Änderungen in Paperless werden als Hinweis gespeichert und lösen keine Umbenennung aus |
| Objektbezug | Anwendung | Feld Objekt (`Nummer, Ort, Straße`); Abweichung in Paperless wird Konflikt oder Vorschlag, nie stillschweigend übernommen |
| Kategorie, Unterordner, Dateiname | Anwendung | Ergebnis der Pipeline und der Drive-Ablage; nicht aus Paperless änderbar |
| Zuordnungsstatus | Anwendung | Feld Zuordnung („Eingang“, „zugeordnet“, „zugeordnet und abgelegt“, „in Prüfung“, Texte für Exportfassung und Indexbeleg) |
| Dokument-UUID | Anwendung | Feld UUID, Identität über beide Systeme hinweg; wandert bei Objektübernahme mit |
| Drive-Link | Anwendung | Feld Drive-Link (`https://drive.google.com/file/d/<id>/view`), Zugriff nach Drive-Berechtigung |
| Dateiinhalt | Drive (Original) | nie überschrieben; abweichender Inhalt in Paperless oder Drive wird Konflikt |
| Übrige benutzerdefinierte Felder | Paperless | unberührt; geschrieben werden nur die vier Felder der Anwendung, und nur bei geändertem Sollwert |

## 10 Wiederanlauf und Störungen

| Störung | Erkennung | Verhalten | Maßnahme |
|---|---|---|---|
| Paperless nicht erreichbar oder 5xx | Verbindungstest „Verbindung fehlgeschlagen: PaperlessUnavailable“; `last_poll` mit `error`; Operationen `pending` mit `last_error`, später `failed` | Client unternimmt bis zu fünf Versuche je Aufruf, danach geht die Operation in Retry mit Backoff; der Cursor wird nicht vorgeschoben, nichts geht verloren; Webhook antwortet weiter mit 202 (nur Einreihen) | nach Behebung Verbindung prüfen; `failed` erneut einreihen; regelmäßiger Abgleich holt den Rückstand nach |
| Token ungültig oder Rechte fehlen (401, 403) | Verbindungstest „PaperlessAuthError“; Operationen `blocked` mit „Zugriff verweigert (Token oder Rechte prüfen)“ | keine Wiederholung, alles bleibt sichtbar stehen | Token oder Rechte in Paperless prüfen, Datei `paperless_token` neu befüllen, `web` und `worker-io` neu starten, Verbindung prüfen, blockierte Operationen erneut |
| Kennzeichnung fehlt (Tag oder Feld gelöscht oder umbenannt) | Verbindungstest meldet „Kennzeichnung unvollständig“ mit Liste; Metadatenoperationen warten 900 Sekunden | Defer ohne Fehlversuch | Kennzeichnung einrichten (nicht im Modus readonly) oder Feldnamen in der Konfiguration angleichen |
| Ratenbegrenzung (429) | `last_error` „Ratenbegrenzung“ | Wartezeit aus `Retry-After`, sonst 60 Sekunden; zählt als Versuch | `paperless.page_size` oder Intervall anpassen, wenn dauerhaft |
| Drive-Cursor ungültig | Protokoll `sync.drive_changes` mit `cursor_invalid` und Hinweis; `last_changes_poll` mit `cursor_invalid` | Cursor wird geleert, der nächste Lauf setzt einen neuen Ausgangspunkt ohne Rückblick; Änderungen dazwischen werden nicht nachgeliefert | Drive-Bestandslauf ausführen (Trockenlauf, dann echter Lauf für die betroffenen Objekte) |
| Google-Verbindung fehlt oder widerrufen | Drive-Abgleich „keine Google-Verbindung“; Operationen `pending` mit „keine Google-Verbindung“ (Defer 600 Sekunden) | kein Fehlversuch | Google Drive neu autorisieren (Runbook, Token widerrufen); Operationen laufen danach von selbst |
| Upload mit unklarem Ausgang (Verbindungsabbruch während `post_document`) | Operation `pending` mit „Upload mit unklarem Ausgang“ | nächster Versuch sucht zuerst per UUID-Feld; meldet Paperless eine Dublette mit Kennung, wird die vorhandene Kopie verknüpft | nichts; bei Servern ohne `custom_field_query` bleibt die Dublettenprüfung von Paperless (Prüfsumme) die Sicherung |
| Doppelte Ereignisse (Webhook und Abgleich, mehrfacher Webhook) | mehrere Einträge in `sync.webhook` mit `created: false` | Operationsschlüssel verhindert Doppelarbeit; abgeschlossene Schlüssel werden nicht wiederholt | nichts |
| Worker verloren | Operation `running` mit altem `heartbeat_at` | `sync-dispatch` gibt sie nach 30 Minuten frei | `docker compose logs worker-io`, bei Bedarf `docker compose restart worker-io` |
| Bestandslauf `failed` | Detailseite zeigt Fehlertext, Status fehlgeschlagen | Lauf hält an, Manifest bleibt | Ursache beheben, Fortsetzen; der Lauf setzt an der gespeicherten Seite fort |
| Externe Kennung bereits verknüpft | Operation `blocked` mit „ist bereits mit Dokument ... verknüpft“ | keine stille Übernahme | Dokumente prüfen (Dublette, frühere Übernahme), gegebenenfalls Verknüpfung im Review Center klären |

## 11 Rechte

| Recht | Rollen laut `db/seeds/roles.json` | Umfang |
|---|---|---|
| `sync.manage` | Admin | `/verwaltung/sync/` mit Verbindungstest, Kennzeichnung, Eingang einrichten (zusätzlich erneute Anmeldung), Abgleich anstoßen, Bestandsläufe starten und steuern (echte Läufe nur mit diesem Recht), Operationen erneut einreihen oder verwerfen |
| `inbox.work` | Admin, Sachbearbeiter | Dokumenteneingang `/eingang/` mit Reitern Zuordnung, Konflikt, Dublette, Format, Fehler; die Objektsuche `/eingang/objekte.json` verlangt dasselbe Recht |
| `review.decide` | Admin, Sachbearbeiter | Entscheidungen in Fällen des Review Centers: Objekt zuordnen, Vorschlag verwerfen, Konflikt entscheiden |
| `settings.write` | Admin | Konfigurationsschlüssel `paperless.*` und `sync.*` |

Die Schaltfläche erneut im Dokumenteneingang erscheint nur mit `sync.manage`. Alle Entscheidungen werden mit handelnder Person im Protokoll geführt (`inbox.assign`, `inbox.reject`, `sync.conflict_resolved`, `sync.tombstone`, `sync.operation_retry`, `sync.operation_cancelled`).

## 12 Abschaltung

- `paperless.enabled` auf false stoppt alle Schreibzugriffe nach Paperless: Übertragung, Metadaten, Indexbelege und Exportfassungen enden blockiert („Schreiben nach Paperless für dieses Objekt nicht erlaubt“), die Pipeline reiht keine neuen Übertragungen ein, der regelmäßige Abgleich wird übersprungen, der Webhook antwortet mit 202 und `ignored`. Vorhandene Verknüpfungen, Zustände und Dokumente bleiben erhalten; nichts wird zurückgenommen.
- Bereits eingereihte Operationen jeder Art, auch lesende Übernahmen aus Paperless (`paperless_pull`), warten mit „Hauptschalter paperless.enabled ist aus“ (600 Sekunden je Prüfung) und laufen nach dem Wiedereinschalten weiter; sollen sie nicht mehr laufen, wartende Operationen verwerfen.
- Verbindungstest und Trockenläufe des Bestands funktionieren ohne Hauptschalter, weil sie nur Adresse und Token brauchen.
- Vollständige Trennung: Datei `paperless_token` leeren (Platzhalter belassen, damit Compose die Datei findet), `web` und `worker-io` neu starten. Dann entsteht kein Client mehr; Operationen warten mit „Paperless nicht konfiguriert (Adresse oder Token fehlt)“ (600 Sekunden) ohne Fehlversuch.
- `paperless.mode` auf readonly ist die mildere Stufe: lesen und inventarisieren bleibt möglich, Schreiben endet blockiert.
- `sync.drive_changes_enabled` auf false stoppt das Lesen des Drive-Änderungsprotokolls; der Cursor bleibt gespeichert und wird beim Wiedereinschalten fortgesetzt, solange Drive ihn noch annimmt.
- Wiedereinschalten: Verbindung prüfen, Hauptschalter an; blockierte Operationen laufen beim nächsten Einreihen desselben Schlüssels oder über erneut wieder an.
