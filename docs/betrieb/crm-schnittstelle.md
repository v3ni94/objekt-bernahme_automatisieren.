# CRM-Schnittstelle (M29 Stufe 3)

Stand: 26.09.2026. Lesende Schnittstelle der Anwendung für das CRM und ausgehende Webhooks (Modul `src/apps/crm_api`). Verbindlich ist der Schnittstellenvertrag M29 Stufe 3/4 zwischen objektakte und CRM; dieses Dokument beschreibt die Umsetzung auf der Seite objektakte und die Punkte, an denen der Vertrag Spielraum lässt.

## 1 Grundsätze

- Nur lesend. Es gibt keinen schreibenden Endpunkt; andere Methoden als GET beantwortet die Schnittstelle mit 405.
- Keine Datenbankkopplung. Das CRM ruft die Endpunkte serverseitig mit einem Token auf (im CRM `OBJEKTAKTE_API_URL` und `OBJEKTAKTE_API_TOKEN`), nie aus dem Browser.
- Objektschlüssel ist die Objektnummer `number` wie im Objektregister erfasst. Abfragen gehen über den Zahlenwert: `/objects/0523/` und `/objects/523/` liefern dasselbe Objekt.
- Testobjekte (Kennzeichen „Testobjekt“) und das technische Eingangsobjekt erscheinen nicht, weder in der API noch in Webhooks.

## 2 Token anlegen, auflisten, sperren

Tokens werden nur als SHA-256-Hash gespeichert (Tabelle `crm_api_tokens`), mit Scopes, Status und Zeitpunkt der letzten Nutzung (auf die Minute genau). Der Klartext erscheint genau einmal bei der Anlage.

```
docker compose exec -T web python manage.py crm_token anlegen --name crm --scopes objects:read documents:read persons:read
docker compose exec -T web python manage.py crm_token liste
docker compose exec -T web python manage.py crm_token sperren --id 3
docker compose exec -T web python manage.py crm_token sperren --name crm
```

- Scopes: `objects:read` (Endpunkte 1 und 2), `documents:read` (3), `persons:read` (4 und 5). Mehrere Scopes durch Leerzeichen oder Komma getrennt.
- Die letzte Ausgabezeile von `anlegen` ist der Klartext (Beginn `oak_`). Er wird direkt in die Secret-Verwaltung des CRM übertragen, nicht per E-Mail oder Chat.
- Rotation: neues Token anlegen, im CRM eintragen, altes Token mit `sperren --id` sperren. Gesperrte Tokens bleiben als Nachweis in der Tabelle.
- Anlage und Sperre stehen im Protokoll (`crm_token.create`, `crm_token.revoke`), abgewiesene Abrufe als `auth.denied` mit Objektart `crm_api`.

## 3 Endpunkte

Basis: `https://uebernahme.muellerhv.de/api/crm/v1/`, Kopfzeile `Authorization: Bearer <token>`. Antworten JSON in UTF-8, Zeitpunkte ISO 8601 in UTC (`2026-09-26T08:15:00+00:00`), Datumswerte `JJJJ-MM-TT`.

| Nr. | Pfad | Scope | Inhalt |
|---|---|---|---|
| 1 | `objects/` | objects:read | Objektliste mit Übernahmestatus, offenen Prüffällen, Vollständigkeit, Drive-Ordner |
| 2 | `objects/{number}/` | objects:read | wie 1, dazu Anschrift, fehlende Unterlagen, offene Fälle je Art |
| 3 | `objects/{number}/documents/` | documents:read | Dokumentliste, Filter `folder` und `since` |
| 4 | `objects/{number}/owners/` | persons:read | aktuelle Eigentümer, maskiert |
| 5 | `objects/{number}/tenants/` | persons:read | aktuelle Mieter, maskiert |

Paginierung bei 1, 3, 4 und 5: `?page=1&page_size=100`, höchstens 500 (größere Werte werden auf 500 begrenzt), Antwort `{"count", "page", "page_size", "results"}`. Eine Seite hinter dem Ende liefert eine leere Liste. Statuscodes: 400 bei ungültigem `page`, `page_size` oder `since`, 401 ohne oder mit ungültigem oder gesperrtem Token, 403 bei fehlendem Scope, 404 bei unbekannter Objektnummer, 405 bei anderer Methode als GET.

Festlegungen innerhalb des Vertrags:

- Objektliste: aktive und archivierte Objekte, je Objektnummer genau eines (das aktive, sonst das zuletzt archivierte). `archived` ist wahr bei archivierten Objekten. `takeover_status` ist der Objektstatus (`new`, `takeover`, `active`, `archived`).
- `open_review_cases`: Prüffälle mit Status offen oder in Bearbeitung.
- `completeness`: aus der Vollständigkeitsprüfung (Zusammenfassung wie auf der Seite Vollständigkeit). `required` sind die anwendbaren Prüfpunkte, `present` die erfüllten, `missing` die fehlenden und teilweise erfüllten, `percent` der Erfüllungsgrad in Prozent mit einer Nachkommastelle. Folgepunkte und der Sollbestand der Stammakte zählen wie dort nicht mit. Ohne Bewertung sind alle Werte 0.
- `drive_folder_id`: Objektordner aus `drive_nodes`, ersatzweise die am Objekt hinterlegte Ordner-ID; `drive_folder_url` der Link auf den Ordner.
- `missing_documents`: die offenen Punkte der Vollständigkeitsprüfung (fehlt und teilweise, wie Blatt „Offene Punkte“ der Listen und die Nachforderung), einschließlich Sollbestand der Stammakte. `category` und `subfolder` sind die Ordnernamen der ersten Nachweis-Dokumentart des Prüfpunkts (z. B. `02_Stammakte`); hat ein Prüfpunkt keine Dokumentart (etwa Anschriften), steht in `category` die Gruppe des Prüfpunkts (z. B. `owner_master`) und `subfolder` ist null. `label` enthält Prüfpunkt, Einheit und Jahr, keine Personennamen.
- `open_cases_by_type`: Schlüssel `case_type/case_subtype`; ohne Unterart bleibt der Teil nach dem Schrägstrich leer (`unclear/`).
- Dokumente: alle Dokumente des Objekts mit Hash, ohne gelöschte und ohne in ein anderes Objekt übernommene. Dubletten tragen keinen eigenen Hash und erscheinen nicht. `doc_type` ist der Code der Dokumentart, `category` und `subfolder` die Ordnernamen wie in Drive, `status` der Verarbeitungsstatus (`filed` bedeutet abgelegt). `folder` filtert nach Hauptordner, als Code (`02`) oder Ordnername (`02_Stammakte`). `since` (ISO 8601, ohne Zeitzone als UTC gelesen, auch reines Datum) liefert Dokumente, deren Datensatz seit diesem Zeitpunkt geändert wurde.
- Eigentümer: Zuordnungen, die heute gelten, je Eigentümer eine Zeile mit allen Einheiten des Objekts. `share` ist der Anteil an der Einheit aus der Zuordnung (z. B. `0.5`), wenn er für alle Einheiten des Eigentümers gleich ist, sonst null. Der Miteigentumsanteil der Einheit ist nicht Teil dieses Endpunkts.
- Mieter: Mieter und Mitmieter mit heute geltender Zuordnung, ohne Bürgen; bei WEG mit Sondereigentumsverwaltung nur Einheiten in eigener Verwaltung (wie die Mieterliste). `lease_start` und `lease_end` aus dem Mietverhältnis, ersatzweise aus der Zuordnung; `lease_end` ist null, solange ein Mietverhältnis unbefristet ist.

## 4 Webhooks

Ereignisse:

| Ereignis | Auslöser |
|---|---|
| `document.filed` | Ablage in Drive abgeschlossen und Dokument im Status abgelegt (Schritt `file_to_drive`, nach der Rückprüfung des Elternordners). Mit offenem Prüffall folgt das Ereignis erst nach der Entscheidung mit der erneuten Ablage. Wird ein Dokument nach einer späteren Entscheidung erneut abgelegt, geht das Ereignis erneut hinaus; das CRM verwirft es über den Schlüssel (event, object_number, document.id). |
| `object.taken_over` | Objektstatus wechselt im Objektformular von „neu“ oder „in Übernahme“ auf „aktiv“ (Übernahme abgeschlossen). Wiederherstellen aus dem Archiv löst kein Ereignis aus. |

Versand: POST an `CRM_WEBHOOK_URL`, Body `{"event", "occurred_at", "object_number", "document"}` (bei `object.taken_over` ist `document` null, bei `document.filed` ein Datensatz wie in Endpunkt 3). Kopfzeilen `X-Objektakte-Signature: sha256=<hex>` (HMAC-SHA256 über den rohen Body mit `CRM_WEBHOOK_SECRET`) und `X-Objektakte-Event`. Der Body wird beim Auslösen einmal erzeugt und unverändert versendet.

Ablauf: Einreihen nach dem Commit als Celery-Aufgabe `crm_api.send_webhook` in der Queue `io` (Container `worker-io`). Ein Versuch und höchstens drei Wiederholungen mit 30 Sekunden, 2 und 8 Minuten Abstand bei Verbindungsfehlern, Zeitüberschreitung und den Antworten 408, 425, 429 und 5xx. Andere Antworten außer 2xx gelten sofort als endgültig fehlgeschlagen. Endgültige Fehlschläge stehen als Warnung im Log des Workers; die Ablage bleibt davon unberührt, das CRM gleicht über Endpunkt 3 ab. Weiterleitungen werden nicht verfolgt.

Einstellungen (Neustart der Container `web` und `worker-io` nach Änderung):

| Name | Herkunft | Bedeutung |
|---|---|---|
| `CRM_WEBHOOK_URL` | `.env` | Ziel, z. B. `https://<crm>/api/v1/integrations/objektakte/webhook`; leer bedeutet aus |
| `CRM_WEBHOOK_SECRET` | Variable in `/srv/objektakte/.env` (alle Anwendungsdienste lesen die .env über `env_file`), alternativ Secret-Datei über `CRM_WEBHOOK_SECRET_FILE` | gemeinsames Geheimnis für die Signatur; leer bedeutet aus |
| `CRM_WEBHOOK_TIMEOUT_S` | `.env`, Standard 10 | Zeitgrenze je Versuch in Sekunden |

Einschalten (Stand 26.09.2026): URL und Geheimnis per Deploy-Aktion `env-set` in die `.env` schreiben (`CRM_WEBHOOK_URL=https://<crm-api>/api/v1/integrations/objektakte/webhook+CRM_WEBHOOK_SECRET=<geheimnis>`), danach `deploy`, damit `web` und `worker-io` neu starten. Eine Compose-Änderung ist nicht nötig, weil alle Anwendungsdienste die `.env` einlesen. Das Geheimnis wird auf dem Server erzeugt (`openssl rand -hex 32`) und im CRM als `OBJEKTAKTE_WEBHOOK_SECRET` hinterlegt, nie im Repository oder Chat. Wer lieber eine Secret-Datei nutzt, bindet sie analog zu `paperless_webhook_token` mit `CRM_WEBHOOK_SECRET_FILE: /run/secrets/crm_webhook_secret` ein.

## 5 Datenschutz

- Keine IBAN im Klartext. Gespeichert sind nur die letzten vier Stellen und ein HMAC (Beschluss B-18); die Schnittstelle gibt dieselbe Maske aus wie die Eigentümerliste (`**** **** **** **** **30 00`). Ländercode und Prüfziffer sind nicht gespeichert und erscheinen deshalb nicht.
- E-Mail-Adressen nur maskiert: erstes Zeichen von Name und Domain, Endung lesbar (`e***@b***.de`).
- Keine Anschriften, Telefonnummern, Geburtsdaten, Salden oder Bankdaten von Personen. Personendaten nur unter dem Scope `persons:read`; die Endpunkte 1 bis 3 enthalten keine Personennamen (Dokumenttitel können Namen enthalten, wenn sie im Dateinamen stehen).
- Webhooks enthalten nur Objektnummer und Dokumentdatensatz, keine Personendaten.
- Das Token erscheint nie im Protokoll; abgewiesene Abrufe werden mit Pfad und Grund protokolliert, nicht mit dem gesendeten Wert.
- Ein Token pro abrufendem System; bei Verdacht auf Offenlegung sofort sperren und neu anlegen.
