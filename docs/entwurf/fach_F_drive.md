# Fachentwurf F: Google-Drive-Adapter und Ordnerabgleich (CR-05, Abschnitte 2, 4, 9, 12a, 13, 14)

> Arbeitspapier der Entwurfsphase vom 10.09.2026, nicht verbindlich. Bei Abweichungen gelten docs/architektur.md, docs/betrieb.md und docs/umsetzungsplan.md (Lesehinweise in docs/entwurf/README.md).

Stand: 10.09.2026. Faktenbasis: CR-05 (docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md) und Befundakte (docs/entwurf/befundakte.md). Tabellen- und Feldnamen folgen dem Fachentwurf D (Datenmodell), Konfigurationsschlüssel der dortigen Tabelle `app_settings`. Bibliotheken werden benannt, Versionsnummern sind zum Umsetzungszeitpunkt zu prüfen (aktuelle LTS bzw. stabile Version). Alle Beispiele sind synthetisch.

## 0. Ergebnis und Empfehlung

Der Drive-Adapter wird als schmale, austauschbare Schicht mit drei Implementierungen gebaut (echter Google-Client, In-Memory-Fake für Tests, Live-Testmodus gegen ein Test-Wurzelverzeichnis). Der Ordnerabgleich ist strikt in eine lesende Planungsphase und eine schreibende Ausführungsphase getrennt; der Dry-Run ist damit kein eigener Client, sondern die Planungsphase ohne Ausführung. Der Abgleich rät nie: jede Mehrdeutigkeit (doppelte Objektnummer, doppelte Ordnernamen, gleichzeitiges Vorkommen von Alt- und Neubezeichnung, abweichende Schreibweisen) wird als Review-Fall mit Auswahl gestellt und blockiert nur den betroffenen Teilschritt, nicht den Rest des Objekts.

Tragende Entscheidungen:

| Nr. | Entscheidung | Begründung |
|---|---|---|
| 1 | OAuth 2.0 mit Refresh-Token des technischen Kontos, interne Workspace-App, Scope `drive`. Refresh-Token verschlüsselt in `oauth_tokens`, Schlüssel außerhalb der DB. | CR 0.1: `drive.file` reicht nicht, weil bestehende Ordner gelesen und verändert werden müssen. Interne App vermeidet den Ablauf nach sieben Tagen im Testmodus. |
| 2 | Alle Drive-Aufrufe mit `supportsAllDrives=true` und `includeItemsFromAllDrives=true`, Wurzel als konfigurierte Folder-ID, optional `driveId`. | Befund 6.6: heute My Drive, Umzug in eine geteilte Ablage ohne Codeänderung möglich. |
| 3 | Objektordner werden über die führende Ziffernfolge mit konfigurierbarer Längenspanne erkannt, Vergleich über den Zahlenwert. | Befund 4: Bestand enthält 2- bis 5-stellige Nummern, CR nennt dreistellig. |
| 4 | Planungsphase erzeugt eine deterministische Aktionsliste (`drive_sync_actions`), Ausführungsphase arbeitet sie in `seq_no`-Reihenfolge ab. Dry-Run = Plan ohne Ausführung. | CR 9.5 und 9.6: Dry-Run und Idempotenz fallen aus derselben Logik heraus, kein zweiter Codepfad. |
| 5 | Umbenennung `05_Sonstiges` zu `06_Sonstiges` über `files.update` mit neuem `name`, Dateizählung rekursiv vorher und nachher inklusive Hash der sortierten Datei-IDs. | CR 9.3: Umbenennen statt Kopieren, Nachweis über Zählung. Der ID-Hash beweist zusätzlich, dass dieselben Dateien vorliegen. |
| 6 | Alt-Bezeichnung ausschließlich als Konfigurationswert `drive.legacy_folder_aliases` im Seed, kein Literal im Anwendungscode. | Befund 6.4: Definition of Done (Grep leer) und CR 9.3 (Altordner erkennen) sind nur so vereinbar. |
| 7 | Bestandsdateien werden inventarisiert und der Pipeline übergeben; Verschiebung nur per `files.update` mit `addParents`/`removeParents` und nur bei Konfidenz über `classification.auto_file_threshold`. Nichts wird gelöscht, kein Download-Upload für Verschiebungen. | CR 9.4 und 13. |
| 8 | Zentrale Benennungsfunktion `build_owner_folder_name` mit Konfiguration, Normalisierung, Kollisionssuffix und mindestens 24 Unit-Testfällen. | CR 4 und 14. |
| 9 | Listen-Dateien werden über die gespeicherte File-ID in `drive_nodes` per `files.update` mit Medieninhalt als neue Version geschrieben; verschwundene Dateien werden neu angelegt und protokolliert. | CR 12a: genau eine Datei je Format, Drive-Versionierung statt Neuanlage. |
| 10 | Exponential Backoff mit Zufallsanteil bei 403 Ratenlimit, 429 und 5xx, clientseitige Ratenbegrenzung, ein serieller Drive-Schreibkanal je Objekt. | CR 13. Drive-Quota ist projektbezogen; serielle Schreibfolge macht Läufe reproduzierbar. |

Abweichungen vom CR-Wortlaut stehen als Vorschläge in Abschnitt 11, Annahmen in Abschnitt 12, Entscheidungen des Auftraggebers in Abschnitt 13.

## 1. Geltungsbereich und Abgrenzung

In diesem Entwurf: Authentifizierung und Betrieb gegen die Drive-API, Objektordner-Erkennung und -Anlage, Ordnerabgleich (CR 9), Übergabe der Bestandsdateien an die Pipeline und deren Verschiebung, Benennung und Struktur der Eigentümerakten-Ordner (CR 4), Ablage der Listen-Dateien (CR 12a), Duplikatoption (CR 6), Teststrategie für den Drive-Anteil (CR 14), Protokoll des Abgleichs.

Nicht in diesem Entwurf: OCR und Klassifikation (Fachentwurf E), Datenmodell im Detail (Fachentwurf D), Inhalt der Listen (Spalten, Blätter, PDF-Layout), Review-Center-Oberfläche.

Schnittstellen zu anderen Entwürfen:

| Richtung | Gegenstand | Bezeichner |
|---|---|---|
| Pipeline ruft Adapter | Download einer Datei zur OCR, Verschiebung nach Klassifikation, Kopie bei Duplikatoption | `DriveAdapter.download`, `DriveAdapter.move`, `DriveAdapter.copy` |
| Abgleich ruft Pipeline | Registrierung inventarisierter Dateien als `documents` mit `source = drive_existing` und Anlage eines `processing_jobs`-Eintrags | `register_existing_file(object_id, drive_file)` |
| Listenerzeugung ruft Adapter | Aktualisierung der vier Listen-Dateien je Objekt | `DriveAdapter.update_content` |
| Review Center ruft Abgleich | Fortsetzung nach Entscheidung (Auswahl Objektordner, Auswahl Hauptordner) | `reconcile_object(object_id, dry_run=False)` erneut, idempotent |
| Statusseite liest | Token-Zustand, letzter Abgleich, Quota-Fehlerzähler | `oauth_tokens`, `drive_sync_runs`, Metrikzähler |

## 2. Drive-API-Nutzung

### 2.1 Authentifizierung: OAuth 2.0 mit Refresh-Token

Ablauf der Einrichtung (einmalig durch den Admin, Anleitung für die Cloud Console liefert der Umsetzungsplan gemäß CR 15):

1. OAuth-Client vom Typ Webanwendung in einem Google-Cloud-Projekt der Workspace-Organisation, Nutzertyp intern (CR 0.1). Client-ID und Client-Secret liegen in `.env` oder als Docker Secret, nie in `app_settings` oder im Repository.
2. Redirect-URI: Vorschlag `https://uebernahme.muellerhv.de/admin/google/oauth/callback` (der CR gibt nur den Host vor, der Pfad ist Vorschlag).
3. Admin startet im Admin-Bereich die Autorisierung. Die Anwendung baut die Autorisierungs-URL mit `access_type=offline`, `prompt=consent` (erzwingt die Ausgabe eines Refresh-Tokens), `include_granted_scopes=true` und einem zufälligen `state`, der serverseitig an die Admin-Sitzung gebunden ist. Der Admin meldet sich als `ablage@muellerhv.de` an.
4. Callback tauscht den Code gegen Access- und Refresh-Token, prüft, dass das autorisierte Konto dem konfigurierten technischen Konto entspricht (Abgleich der E-Mail aus dem Userinfo-Endpunkt oder des ID-Tokens gegen `oauth_tokens.account_email`), und speichert beide Tokens verschlüsselt.
5. Danach wird die Wurzel aufgelöst (Abschnitt 3.2) und `drive.root_folder_id` gesetzt.

Scope: `https://www.googleapis.com/auth/drive`. Begründung laut CR 0.1: `drive.file` erlaubt nur Zugriff auf Dateien, die die Anwendung selbst erzeugt hat. Die Anwendung muss aber vorhandene Objektordner lesen, umbenennen, Dateien darin verschieben und fremd erzeugte Dateien herunterladen. `drive.readonly` erlaubt keine Schreibvorgänge, `drive.metadata` keinen Inhaltszugriff. Konsequenz: Das technische Konto sieht mit diesem Scope alles, was es in Drive sehen darf. Die Begrenzung erfolgt daher auf Kontoebene: `ablage@muellerhv.de` erhält nur Zugriff auf den Wurzelpfad und die dort liegenden Objektordner. Diese Begründung wird im Admin-Bereich beim Token-Status und in der Betriebsdokumentation angezeigt.

Bibliotheken (Python-Stack, Versionsnummern zum Umsetzungszeitpunkt prüfen): `google-auth`, `google-auth-oauthlib` (Autorisierungsablauf), `google-api-python-client` (Discovery-Client für Drive v3), `cryptography` (Verschlüsselung der Tokens). Kein PyDrive2, weil resumable Upload, Felderauswahl, Versionierung und Shared-Drive-Parameter direkt am offiziellen Client kontrolliert werden.

### 2.2 Token-Speicherung, Refresh, Ablaufüberwachung, Widerruf

Speicherung (Tabelle `oauth_tokens` aus Fachentwurf D):

| Feld | Behandlung |
|---|---|
| `refresh_token_encrypted` | AES-GCM oder Fernet über `cryptography`, Schlüssel aus Docker Secret `OAUTH_TOKEN_KEY`, `key_version` gespeichert, Schlüsselwechsel durch Neuverschlüsselung aller Zeilen mit altem und neuem Schlüssel |
| `access_token_encrypted`, `access_expires_at` | ebenfalls verschlüsselt, damit mehrere Prozesse (web, worker, beat) denselben Access-Token nutzen und nicht jeder Prozess einzeln erneuert |
| `scopes` | gespeicherter Scope-String, wird bei jedem Start gegen den erwarteten Scope geprüft; Abweichung setzt Status `expired` und erzwingt Neuautorisierung |
| `storage_mode` | `db` (Standard) oder `docker_secret`; bei `docker_secret` liegt der Refresh-Token in einer Secret-Datei, die Tabelle hält nur Metadaten |

Refresh: `google-auth` erneuert den Access-Token beim ersten Aufruf nach Ablauf selbstständig. Der Adapter registriert einen Callback, der nach jedem erfolgreichen Refresh `access_token_encrypted`, `access_expires_at`, `last_refresh_at = jetzt`, `last_refresh_status = ok`, `consecutive_failures = 0` schreibt. Mehrere Prozesse werden über einen kurzen Redis-Lock (`drive:token:refresh`) serialisiert, damit nicht drei Prozesse gleichzeitig erneuern. Ein Prozess, der den Lock nicht erhält, wartet kurz und liest den frisch gespeicherten Access-Token aus der DB.

Ablaufüberwachung (Zeitplaner, täglich, zusätzlich zur bedarfsgetriebenen Erneuerung):

1. Erzwungener Refresh, unabhängig davon, ob der Access-Token noch gültig ist. Zweck: Nachweis, dass der Refresh-Token weiterhin funktioniert (Deployment-Test CR 14: Token-Refresh über mindestens 8 Tage ohne erneute Anmeldung). Der Nachweis ist die Folge von mindestens acht täglichen Einträgen `last_refresh_status = ok` in `audit_events` ohne dazwischenliegende Autorisierung.
2. Bei Fehler: `consecutive_failures + 1`, `last_refresh_error` mit dem Fehlergrund (kein Token im Klartext), Statusanzeige gelb ab einem Fehler.
3. Bei Fehlerantwort `invalid_grant` (Token widerrufen oder ungültig) sofort `status = revoked`, Statusanzeige rot.

Statusbereich (Statusseite, für Admin und Sachbearbeiter sichtbar, nur Admin darf neu autorisieren):

| Anzeige | Quelle |
|---|---|
| Konto, Scope, Status (aktiv, abgelaufen, widerrufen) | `oauth_tokens` |
| Letzter erfolgreicher Refresh, letzter Fehler, Fehlerzähler | `oauth_tokens` |
| Autorisiert am (Beginn der Nachweiskette für den 8-Tage-Test) | `refresh_obtained_at` |
| Drive-Warteschlange: Anzahl wartender Schreibjobs, Zustand `paused_auth` | Queue-Zähler |
| Quota-Ereignisse der letzten 24 Stunden (Anzahl 403 Ratenlimit, 429, 5xx, mittlere Wartezeit) | Metrikzähler des Adapters |

Verhalten bei widerrufenem oder ungültigem Token:

1. Alle Drive-Schreibjobs werden nicht verworfen, sondern bleiben in der Warteschlange; die Drive-Warteschlange wechselt in den Zustand `paused_auth`. OCR und Klassifikation laufen weiter, weil sie auf bereits heruntergeladenen Transitdateien arbeiten (Fachentwurf E).
2. Statusseite zeigt rot mit der Schaltfläche „Google-Konto neu autorisieren" (nur Admin). Optional E-Mail-Benachrichtigung an eine konfigurierte Adresse (`drive.alert_email`, Standard leer).
3. Nach erfolgreicher Neuautorisierung: Statuswechsel auf `active`, Warteschlange läuft automatisch weiter. Kein Job wird doppelt ausgeführt, weil jeder Schreibjob vor Ausführung seinen Zielzustand prüft (Abschnitt 2.7).
4. Google kann Refresh-Tokens außerdem aus eigenen Gründen ungültig machen (Widerruf durch den Workspace-Administrator, Änderung der App-Konfiguration, längere Nichtnutzung). Die genauen Regeln sind der Google-Dokumentation zum Umsetzungszeitpunkt zu entnehmen; der tägliche erzwungene Refresh deckt den Fall Nichtnutzung ab.

### 2.3 My Drive gegenüber Shared Drive

Der Wurzelpfad liegt heute im persönlichen My Drive des technischen Kontos (Befund 6.6). Der Adapter ist für beides ausgelegt:

| Punkt | Umsetzung |
|---|---|
| Parameter an jedem Aufruf | `supportsAllDrives=true` bei `files.get`, `files.create`, `files.update`, `files.copy`, `files.list`; zusätzlich `includeItemsFromAllDrives=true` bei `files.list` |
| Suchraum | `drive.root_drive_id` (Standard `null`). Bei `null`: `corpora=user`. Bei gesetzter ID: `corpora=drive`, `driveId=<id>` |
| Wurzel | immer die konfigurierte Folder-ID `drive.root_folder_id`, nie das Alias `root`; der Pfad `Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten` dient nur der Erstauflösung (Abschnitt 3.2) |
| Elternordner | Der Adapter geht von genau einem Elternordner je Datei aus; Dateien mit mehreren Elternordnern (Altbestand im My Drive) werden im Inventurprotokoll gekennzeichnet und über den ersten Elternordner behandelt |
| Umzug in eine geteilte Ablage | Konfigurationsänderung (`drive.root_folder_id`, `drive.root_drive_id`) plus vollständiger Abgleich aller Objekte; die gespeicherten Folder-IDs bleiben beim Verschieben innerhalb von Drive erhalten, beim Neuaufbau werden sie durch den Abgleich neu aufgelöst |

### 2.4 Aufrufkonventionen: Felder, Abfragen, Paginierung

Felderauswahl (`fields`), damit Antworten klein bleiben und keine unnötigen Metadaten übertragen werden:

```text
Standardfelder Datei/Ordner:
  id, name, mimeType, parents, trashed, size, md5Checksum, modifiedTime, createdTime, shortcutDetails
Listenaufruf:
  nextPageToken, files(<Standardfelder>)
```

Abfragen (`q`), immer mit `trashed = false`, sofern nicht ausdrücklich der Papierkorb geprüft wird:

```text
Ordner unterhalb eines Elternordners:
  '<parentId>' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false
Alle Einträge unterhalb eines Elternordners:
  '<parentId>' in parents and trashed = false
Datei mit Namen im Ordner (Wiederaufnahmeprüfung):
  '<parentId>' in parents and name = '<escaped>' and trashed = false
```

Namen in `q` werden escaped (Backslash und Apostroph), weil Objektordner Kommata und Straßennamen Apostrophe enthalten können. Die Objektordner-Erkennung nutzt keine `name contains`-Suche, sondern listet alle Ordner der Wurzel und filtert lokal mit dem regulären Ausdruck (Abschnitt 3.1). Bei rund 70 aktiven plus archivierten Objektordnern ist das eine bis zwei Seiten.

Paginierung: `pageSize` auf den API-Höchstwert (zum Umsetzungszeitpunkt prüfen, derzeit dokumentiert 1000), Schleife über `nextPageToken` bis leer. Ergebnisse einer Listung werden erst nach vollständiger Paginierung verarbeitet, damit ein Abgleich nie mit einer halben Liste arbeitet. `orderBy` wird nicht als Determinismusquelle genutzt; die Sortierung erfolgt lokal (Abschnitt 2.7).

### 2.5 Quota, Fehlerbehandlung, Backoff

Quota: Die Drive-Quota gilt je Cloud-Projekt und je Nutzer und wird in der Cloud Console angezeigt. Es werden keine Grenzwerte angenommen, sondern die tatsächlichen Werte bei der Einrichtung abgelesen und in der Betriebsdokumentation notiert. Die Anwendung schützt sich zweifach:

1. Clientseitige Ratenbegrenzung (Token-Bucket) mit `drive.max_requests_per_second`. ANNAHME: Startwert 5 Anfragen je Sekunde bei einem Drive-IO-Prozess; Verifikation über die Quota-Seite der Cloud Console und den Zähler der 403-Ratenlimit-Antworten im Statusbereich während des Performance-Tests (Ziel: null Ratenlimit-Antworten).
2. Exponential Backoff bei transienten Fehlern.

Fehlerklassen und Verhalten:

| HTTP-Status und Grund | Verhalten |
|---|---|
| 403 mit Grund `rateLimitExceeded` oder `userRateLimitExceeded`, 429 | Wiederholen mit Backoff, `Retry-After`-Header wird beachtet, wenn vorhanden |
| 500, 502, 503, 504 | Wiederholen mit Backoff |
| 401 | Einmal Token-Refresh erzwingen und wiederholen; bei erneutem 401 wie widerrufen behandeln (Abschnitt 2.2) |
| 403 mit Grund `insufficientFilePermissions` oder `storageQuotaExceeded` | Nicht wiederholen. Job auf `failed`, Statusseite rot mit Grund, Review-Fall nur bei Objektbezug |
| 404 auf eine gecachte ID | Nicht wiederholen. `drive_nodes.status = missing`, Neuauflösung über den Abgleich (Abschnitt 4.3) |
| 400, 409 | Nicht wiederholen, Fehler protokollieren, Job `failed` |
| Netzwerkfehler, Timeout | Wiederholen mit Backoff |

Backoff-Parameter (konfigurierbar unter `drive.backoff`): Basis 1 Sekunde, Faktor 2, voller Zufallsanteil (Wartezeit gleichverteilt zwischen 0 und dem aktuellen Maximum), Obergrenze 64 Sekunden, höchstens 8 Versuche je Aufruf. Danach `failed` mit letztem Fehler. Diese Werte sind Startwerte; ANNAHME: sie reichen für den Lastfall eines Objekts mit 10.000 Seiten, Verifikation im Performance-Test über den Zähler fehlgeschlagener Aufrufe nach Backoff (Ziel null).

Jeder Aufruf wird mit Dauer, Statuscode, Versuchsnummer und Operation als strukturiertes Log geschrieben, ohne Dateiinhalte und ohne Token. Metrikzähler je Operation und Fehlerklasse speisen den Statusbereich.

### 2.6 Upload und Versionen

| Fall | API | Umsetzung |
|---|---|---|
| Ordner anlegen | `files.create` mit `mimeType = application/vnd.google-apps.folder`, `parents = [parentId]`, `fields = id,name,parents` | Ohne Medieninhalt |
| Neue Datei ablegen (Listen-Erstanlage, Kopie bei Duplikatoption aus lokaler Datei) | `files.create` mit `MediaFileUpload(resumable=True)` ab Schwellwert, sonst einfacher Multipart-Upload | Schwellwert `drive.resumable_threshold_bytes`, ANNAHME 5 MiB; Chunkgröße `drive.upload_chunk_bytes`, ANNAHME 8 MiB (Vielfaches von 256 KiB, API-Vorgabe); Verifikation über Uploaddauer und Fehlerrate im Performance-Test |
| Neue Version einer bestehenden Datei (Listen) | `files.update(fileId, media_body=...)` | Erzeugt eine neue Revision derselben Datei, ID bleibt; `keepRevisionForever` wird nicht gesetzt, die Aufbewahrung alter Revisionen folgt den Drive-Regeln (zum Umsetzungszeitpunkt prüfen) |
| Umbenennen | `files.update(fileId, body={name})` | Kein Medieninhalt, Kinder unverändert |
| Verschieben | `files.update(fileId, addParents, removeParents)` | Kein Download, kein Upload |
| Kopie (nur bei Duplikatoption für ganze Dateien) | `files.copy(fileId, body={name, parents})` | Serverseitig, kein Download |
| Download | `files.get_media(fileId)` mit `MediaIoBaseDownload`, Chunkgröße wie Upload | In den Transitbereich `/srv/objektakte/transit/<object>/<sha256-Vorläufer>`; nach dem Download wird SHA-256 gebildet |

Resumable Upload: Abgebrochene Uploads werden innerhalb desselben Prozesses über `next_chunk()` fortgesetzt. Die Sitzungs-URI liegt im Speicher; nach einem Prozessabsturz startet der Upload neu. Doppelablage wird dabei über die Wiederaufnahmeprüfung (Abschnitt 2.7) verhindert.

Integritätsprüfung: Nach jedem Upload wird `md5Checksum` der Antwort mit dem lokalen MD5 verglichen. Abweichung führt zu Fehler und Wiederholung; die fehlerhafte Datei wird nicht gelöscht, sondern als `failed_upload` mit ID protokolliert, damit der Admin sie in Drive prüfen kann (nichts löschen).

### 2.7 Deterministische Reihenfolge und Wiederaufnahme

Reihenfolge: Alle Drive-Schreibvorgänge eines Objekts laufen in genau einer seriellen Warteschlange je Objekt (Queue `drive-io`, Nebenläufigkeit 1 je Objekt, Objekte selbst seriell laut CR 7). Sortierschlüssel je Vorgangstyp:

| Vorgang | Sortierschlüssel |
|---|---|
| Aktionen des Ordnerabgleichs | `seq_no` des Plans: Wurzel, Objektordner, Alias-Umbenennungen nach Kategoriecode, Anlagen Hauptordner nach Kategoriecode, Unterordner nach Katalogreihenfolge, Registrierungen |
| Ablage und Verschiebung klassifizierter Dateien | `(object_number_numeric, Zielpfad, casefold(Dateiname), sha256)` |
| Listen | `(object_number_numeric, list_type, list_format)` in der Reihenfolge owner_list xlsx, owner_list pdf, tenant_list xlsx, tenant_list pdf |

Wiederaufnahme nach Abbruch (Container-Neustart, Prozessabsturz, Token-Pause): Jeder Schreibjob ist idempotent formuliert und prüft vor Ausführung den Zielzustand:

| Job | Vorprüfung | Wenn Zielzustand bereits erreicht |
|---|---|---|
| Ordner anlegen | `files.list` im Elternordner nach exaktem Namen und Ordnertyp | ID registrieren, kein zweiter Ordner |
| Umbenennen | `files.get(id)` und Namensvergleich | überspringen, `result = skipped` |
| Verschieben | `files.get(id, fields=parents)` | überspringen |
| Datei hochladen (Kopie, Listen-Erstanlage) | `files.list` im Zielordner nach Name, Vergleich `md5Checksum` mit lokalem MD5 | ID übernehmen, kein Upload; bei Namensgleichheit und abweichendem MD5: Datei mit Suffix nicht anlegen, sondern Review-Fall |
| Neue Version schreiben | `list_generations.content_hash` gegen aktuellen Datenstand | `skipped_unchanged` |

Der Jobzustand liegt in `processing_jobs` beziehungsweise `drive_sync_actions` (Fachentwurf D), die Queue transportiert nur IDs. Nach Neustart werden Jobs mit veraltetem Heartbeat neu eingereiht; die Vorprüfung verhindert Doppelwirkung.

### 2.8 Adapter-Schnittstelle und Implementierungen

```python
class DriveNode(TypedDict):
    id: str; name: str; mime_type: str; parents: list[str]; trashed: bool
    size: int | None; md5: str | None; modified_time: str; created_time: str
    shortcut_target_id: str | None

class DriveAdapter(Protocol):
    def get(self, file_id: str) -> DriveNode | None: ...            # None bei 404
    def list_children(self, parent_id: str, folders_only: bool = False) -> list[DriveNode]: ...
    def list_children_including_trashed(self, parent_id: str) -> list[DriveNode]: ...
    def create_folder(self, parent_id: str, name: str) -> DriveNode: ...
    def rename(self, file_id: str, new_name: str) -> DriveNode: ...
    def move(self, file_id: str, from_parent_id: str, to_parent_id: str) -> DriveNode: ...
    def copy(self, file_id: str, to_parent_id: str, new_name: str) -> DriveNode: ...
    def upload(self, parent_id: str, local_path: Path, name: str, mime_type: str) -> DriveNode: ...
    def update_content(self, file_id: str, local_path: Path, mime_type: str) -> DriveNode: ...
    def download(self, file_id: str, target_path: Path) -> None: ...
    def walk(self, folder_id: str) -> Iterator[tuple[DriveNode, list[str]]]: ...  # rekursiv, mit Pfad-IDs
```

Implementierungen:

| Klasse | Zweck |
|---|---|
| `GoogleDriveAdapter` | echter Client, Backoff, Ratenbegrenzung, Metriken, Shared-Drive-Parameter |
| `InMemoryDriveAdapter` | Fake für Unit- und Integrationstests (Abschnitt 9), Fehlerinjektion, Operationsprotokoll |
| `RecordingDriveAdapter` | Dekorator um jede Implementierung, protokolliert alle Aufrufe für den Idempotenznachweis („zweiter Lauf enthält nur Leseaufrufe") |

Der Dry-Run ist kein eigener Adapter: Die Planungsphase des Abgleichs ruft nur Lesemethoden auf, die Ausführungsphase wird bei `dry_run=True` übersprungen (Abschnitt 4). Das vermeidet einen zweiten Codepfad, der von der echten Ausführung abweichen könnte. Diese Auslegung ist ein Vorschlag und weicht vom Stack-Vorschlag A ab, der einen Dry-Run-Client nennt.

## 3. Objektordner-Erkennung

### 3.1 Regulärer Ausdruck und Konfiguration

Der CR spricht von einer dreistelligen Objektnummer. Der Befund (Punkt 4) zeigt im Bestand 2- bis 5-stellige Nummern bei aktiven und archivierten Objekten sowie eine 6-stellige technische Nummer. Vorschlag: Erkennung über eine führende Ziffernfolge mit konfigurierbarer Längenspanne, Eindeutigkeit über den Zahlenwert.

Konfiguration (`app_settings`, Kategorie `drive`):

| Schlüssel | Seed | Bedeutung |
|---|---|---|
| `drive.object_number_digits_min` | `2` | kleinste akzeptierte Stellenzahl |
| `drive.object_number_digits_max` | `6` | größte akzeptierte Stellenzahl |
| `drive.object_number_separators` | `" _,.-"` | Zeichen, die auf die Ziffernfolge folgen dürfen; Zeilenende ist immer erlaubt |
| `drive.object_number_zero_pad_to` | `null` | Nullauffüllung bei Neuanlage (offene Frage, Abschnitt 13); `null` = Ist-Nummer |
| `drive.object_folder_name_pattern` | `"{number} {city}, {street} {house_number}"` | Namensmuster für neu angelegte Objektordner (CR 2) |

Aufbau des Ausdrucks zur Laufzeit aus der Konfiguration (Python-Notation, Code, daher Bindestriche zulässig):

```python
def object_number_regex(cfg) -> re.Pattern:
    seps = re.escape(cfg.object_number_separators)
    return re.compile(rf"^\s*(?P<number>\d{{{cfg.digits_min},{cfg.digits_max}}})(?=$|[{seps}])")

def extract_object_number(folder_name: str, cfg) -> int | None:
    m = object_number_regex(cfg).match(unicodedata.normalize("NFC", folder_name))
    return int(m.group("number")) if m else None
```

Regeln:

1. Der Ausdruck greift nur am Namensanfang (führende Leerzeichen toleriert). Ziffern mitten im Namen (Hausnummer, Postleitzahl) sind unerheblich, weil sie nicht am Anfang stehen.
2. Nach der Ziffernfolge muss ein konfiguriertes Trennzeichen oder das Namensende folgen. Damit wird `6230 Musterstadt` nicht als Objekt 623 erkannt.
3. Vergleich ausschließlich über den Zahlenwert: `082 Musterstadt` und `82 Musterstadt` sind Objekt 82 (entspricht `objects.object_number_numeric`).
4. Ordner in der Wurzel ohne Treffer werden nicht angefasst und im Protokoll als `ignoriert` aufgeführt (mit Name und ID), damit Fehlbenennungen sichtbar werden.
5. Restrisiko: Ein Fremdordner in der Wurzel, dessen Name mit einer Zahl im Bereich beginnt (Beispiel `2024 Jahresabschluss`), wird als Kandidat für ein Objekt 2024 gewertet, falls ein solches Objekt existiert. Der Protokolleintrag `ignoriert` macht solche Ordner sichtbar; die Wurzel soll ausschließlich Objektordner enthalten (Hinweis in der Betriebsdokumentation).

### 3.2 Auflösung der Wurzel

Einmalig bei der Einrichtung: Der Admin gibt den Pfad an (Standard aus dem CR: `Meine Ablage/01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten`). Die Anwendung läuft die Pfadsegmente von der Wurzel des My Drive aus ab (`files.list` je Ebene mit `name = <segment>` nach NFC-Normalisierung, Ordnertyp, `trashed = false`), zeigt dem Admin bei jeder Ebene die Treffer mit ID an und lässt ihn bei mehreren Treffern wählen. Das Ergebnis wird als `drive.root_folder_id` gespeichert und in `drive_nodes` als `data_root` registriert. Der Pfad selbst wird nur zur Anzeige gespeichert.

Bei jedem Abgleich wird die Wurzel per `files.get` geprüft: existiert, nicht im Papierkorb, Ordnertyp. Andernfalls bricht der Lauf mit `status = failed` und rotem Statuseintrag ab; es werden keine Ordner angelegt.

### 3.3 Suche und Fallunterscheidung

```python
def find_object_folder_candidates(drive, root_id, number: int, cfg):
    active, trashed = [], []
    for node in drive.list_children_including_trashed(root_id):
        if node.mime_type == FOLDER or (node.mime_type == SHORTCUT and node.shortcut_target_is_folder):
            if extract_object_number(node.name, cfg) == number:
                (trashed if node.trashed else active).append(node)
    return sort_deterministic(active), sort_deterministic(trashed)
```

| Ergebnis | Verhalten | Protokoll |
|---|---|---|
| genau ein aktiver Treffer | `objects.drive_root_folder_id`, `drive_root_folder_name`, `drive_root_verified_at` setzen; `drive_nodes` Zeile `object_root` anlegen oder bestätigen. Der Ordner wird nie umbenannt, auch wenn er vom Namensmuster abweicht (CR 2). | `find_root`, `result = ok`, `root_matches = 1` |
| kein aktiver Treffer, kein Papierkorb-Treffer | Ordner nach Namensmuster anlegen (3.4) | `create_folder` mit `name_after` |
| kein aktiver Treffer, mindestens ein Papierkorb-Treffer | Vorschlag: nicht automatisch anlegen. Review-Fall `duplicate_object_number` mit `case_subtype = candidate_in_trash`, Kandidatenliste der Papierkorb-Ordner (Name, ID, Dateianzahl, gelöscht am) und den Optionen „neu anlegen" oder „ich habe den Ordner in Drive wiederhergestellt, erneut abgleichen". Begründung: Ein wiederhergestellter Ordner neben einem neu angelegten ergäbe zwei Ordner mit derselben Nummer. Die Software stellt selbst nichts wieder her und löscht nichts. | `find_root`, `result = skipped`, `create_review` |
| mehrere aktive Treffer | Nicht raten (CR 9.1). Review-Fall `duplicate_object_number` mit Kandidatenliste: Name, ID, Anlagedatum, Änderungsdatum, Anzahl Dateien rekursiv, vorhandene Hauptordner 01 bis 06. Sachbearbeiter wählt den führenden Ordner. Alle weiteren Schritte für dieses Objekt warten. Die nicht gewählten Ordner bleiben unverändert; ihre Dateien werden nicht inventarisiert (Hinweis im Review-Fall, dass der Nutzer sie bei Bedarf manuell in den gewählten Ordner verschiebt). | `find_root`, `result = skipped`, `root_matches = n`, `create_review` |
| Treffer ist eine Verknüpfung (Shortcut) auf einen Ordner | Zielordner über `shortcutDetails.targetId` auflösen und wie einen Treffer behandeln; Kennzeichnung im Protokoll | `find_root` mit Hinweis `via_shortcut` |

Nach Entscheidung im Review Center wird `reconcile_object` erneut aufgerufen; die Entscheidung liegt in `review_cases.resolution` und wird vom Abgleich als gesetzte `drive_root_folder_id` vorgefunden.

Objekt bereits mit gespeicherter `drive_root_folder_id`: Der Abgleich prüft die ID per `files.get`. Existiert der Ordner und liegt er nicht im Papierkorb, wird nicht erneut gesucht (spart Aufrufe und verhindert, dass ein später hinzugekommener zweiter Ordner den Lauf blockiert; dieser wird aber im Protokoll als `Hinweis: weiterer Ordner mit Nummer` aufgeführt). Bei 404 oder Papierkorb: `drive_nodes.status = missing` beziehungsweise `trashed`, Review-Fall `drive_folder_missing`, keine automatische Neuanlage, weil der Ordner Dateien enthalten kann.

### 3.4 Anlage nach Namensmuster

```python
def render_object_folder_name(obj, cfg) -> str:
    number = str(obj.object_number_numeric)
    if cfg.zero_pad_to:
        number = number.zfill(cfg.zero_pad_to)
    fields = {"number": number, "city": obj.city, "street": obj.street, "house_number": obj.house_number}
    missing = [k for k, v in fields.items() if k in placeholders(cfg.pattern) and not v]
    if missing:
        raise ObjectDataIncomplete(missing)      # keine Anlage, kein Raten
    return sanitize_drive_name(cfg.pattern.format(**fields))
```

Regeln:

1. Fehlt ein im Muster verwendetes Feld (Ort, Straße, Hausnummer), wird der Ordner nicht angelegt. Der Lauf endet für dieses Objekt mit `status = failed` und dem Hinweis, welche Stammdaten fehlen. Kein Platzhaltertext im Ordnernamen.
2. `sanitize_drive_name` entfernt Steuerzeichen und den Schrägstrich, kürzt mehrfaches Leerzeichen, normalisiert NFC. Umlaute bleiben erhalten (Straßennamen werden nicht transliteriert).
3. Der erzeugte Name muss den Ausdruck aus 3.1 erfüllen (Selbsttest vor der Anlage), sonst Fehler. Damit kann ein fehlerhaftes Muster keinen unerkennbaren Ordner erzeugen.
4. Nach `files.create` wird die zurückgegebene ID sofort gespeichert; eine Neulistung ist nicht nötig.

## 4. Abgleichsalgorithmus

### 4.1 Grundsätze

1. Zwei Phasen: `plan` (nur Lesen) und `execute` (Schreiben in `seq_no`-Reihenfolge). `dry_run=True` beendet nach `plan`.
2. Jeder Lauf erzeugt eine Zeile `drive_sync_runs`, jede geplante Aktion eine Zeile `drive_sync_actions` mit `planned = 1`; ausgeführte Aktionen erhalten `executed = 1`, `result`, `executed_at`.
3. Namensvergleich in drei Stufen: exakt (nach NFC), normalisiert (casefold, Leerraum, Unterstrich gegen Leerzeichen, Umlaut-Transliteration), lose (ohne Nummernpräfix). Exakt und normalisiert gelten als „vorhanden", lose erzeugt einen Review-Fall (4.4).
4. Der Hauptordnerkatalog kommt aus `document_categories` (Codes 01 bis 06 mit `folder_name`), die Unterordner aus `document_subfolders`, die Alt-Bezeichnungen aus `drive.legacy_folder_aliases`. Im Code steht kein Ordnername als Literal.
5. Blockaden sind lokal: Ein Review-Fall zu einer Kategorie blockiert nur Anlage und Registrierung dieser Kategorie; die übrigen Kategorien werden weiter abgeglichen.
6. Auslöser: Anlage eines Objekts, Öffnen eines Objekts (asynchron, gedrosselt über `drive.reconcile_on_open_min_interval_minutes`, ANNAHME 15 Minuten, Verifikation über die Aufrufzahl je Tag im Statusbereich), manuell über die Objektansicht mit Dry-Run-Schalter, Sammellauf über alle Objekte für die Definition of Done.

### 4.2 Pseudocode

```python
def reconcile_object(object_id: int, dry_run: bool, user_id: int | None) -> SyncRun:
    obj  = load_object(object_id)
    cfg  = load_drive_config()
    cats = load_categories_ordered()             # 01..06 aus document_categories
    run  = create_sync_run(obj, dry_run, user_id)
    plan = ActionPlan(run)

    # Schritt 0: Wurzel
    root = drive.get(cfg.root_folder_id)
    if root is None or root.trashed or root.mime_type != FOLDER:
        return finish_failed(run, "Wurzelordner nicht erreichbar oder im Papierkorb")

    # Schritt 1: Objektordner
    obj_folder = resolve_object_root(obj, root, cfg, plan)      # Abschnitt 3.3, kann Review-Fall planen
    if obj_folder is None:
        return finish(run, plan, dry_run)                        # wartet auf Review oder legt an (im execute)

    # Schritt 2: Bestandsaufnahme im Objektordner (nur Ordner erster Ebene)
    children = drive.list_children(obj_folder.id, folders_only=True)
    children = sort_deterministic(children)                      # (casefold(name), id)
    count_before, idhash_before = count_tree(drive, obj_folder.id)
    run.file_count_before = count_before

    # Schritt 3: Alias-Umbenennungen zuerst (CR 9.3: erst 05_Sonstiges zu 06_Sonstiges, dann 05_Eigentümerakte anlegen)
    blocked: set[str] = set()
    for cat in cats:
        expected = cat.folder_name
        exact    = [c for c in children if nfc(c.name) == nfc(expected)]
        aliases  = [c for c in children if norm(c.name) in {norm(a) for a in cfg.legacy_aliases.get(expected, [])}]
        if aliases and exact:
            plan.review(cat, "legacy_and_target_both_exist", candidates=exact + aliases)   # 4.4 Fall A
            blocked.add(cat.code)
        elif len(aliases) > 1:
            plan.review(cat, "multiple_legacy_folders", candidates=aliases)              # 4.4 Fall B
            blocked.add(cat.code)
        elif len(aliases) == 1:
            legacy = aliases[0]
            n_before, h_before = count_tree(drive, legacy.id)
            plan.rename(legacy, expected, file_count_before=n_before, idhash_before=h_before, category=cat)
            # Sonderregel: die Kategorie, deren Code der Altordner trug, wird erst nach der Umbenennung angelegt
            plan.defer_creation_after(rename_of=legacy, category_code=legacy_code_of(legacy.name))

    # Schritt 4: Hauptordner prüfen, fehlende anlegen, vorhandene registrieren
    for cat in cats:
        if cat.code in blocked:
            continue
        expected = cat.folder_name
        exact = [c for c in children if nfc(c.name) == nfc(expected)]
        exact += plan.renamed_to(expected)                        # gerade geplante Umbenennung zählt als vorhanden
        normalized = [c for c in children if c not in exact and norm(c.name) == norm(expected)]
        loose = [c for c in children if c not in exact + normalized and loose_match(c.name, expected)]
        if len(exact) == 1:
            plan.register(cat, exact[0])                          # unverändert lassen, ID speichern
        elif len(exact) > 1:
            plan.review(cat, "multiple_folders_same_name", candidates=exact)              # 4.4 Fall C
        elif len(normalized) == 1 and not loose:
            plan.register(cat, normalized[0], expected_name=expected, note="Schreibweise abweichend")   # 4.4 Fall D
        elif normalized or loose:
            plan.review(cat, "similar_folder_name", candidates=normalized + loose)         # 4.4 Fall D
        else:
            plan.create_folder(parent=obj_folder, name=expected, category=cat, after=plan.deferred_for(cat.code))

    # Schritt 5: Unterordner von 06_Sonstiges (und aller Kategorien mit Katalog-Unterordnern)
    for cat in cats:
        if cat.code in blocked or plan.has_review_for(cat):
            continue
        parent = plan.node_for(cat)                               # registriert oder geplant
        sub_children = drive.list_children(parent.id, folders_only=True) if parent.exists else []
        for sub in load_subfolders(cat):                          # z. B. 01_Unklar .. 04_Nicht_objektbezogen
            plan_folder_in(parent, sub.folder_name, sub, sub_children, plan)   # gleiche Dreistufenlogik wie Schritt 4

    # Schritt 6: Inventur der Bestandsdateien (Abschnitt 5), nur Lesen und Registrieren
    plan.inventory(list(drive.walk(obj_folder.id)), exclude=registered_list_files(obj))

    if dry_run:
        return finish(run, plan, dry_run=True)                    # Planliste anzeigen, nichts schreiben

    # Schritt 7: Ausführung in seq_no-Reihenfolge, jede Aktion mit Vorprüfung (Abschnitt 2.7)
    for action in plan.ordered():
        execute_with_precheck(action)                             # ok | skipped | failed, Abbruch des Laufs bei failed
        if action.type == "rename_folder":
            n_after, h_after = count_tree(drive, action.target_drive_id)
            action.file_count_after = n_after
            action.result = "ok" if (n_after, h_after) == (action.file_count_before, action.idhash_before) else "failed"
            if action.result == "failed":
                plan.review(action.category, "rename_count_mismatch", context=action)   # nichts weiter schreiben

    # Schritt 8: Nachzählung und Idempotenznachweis
    count_after, idhash_after = count_tree(drive, obj_folder.id)
    run.file_count_after = count_after
    run.no_changes = int(plan.write_actions_count() == 0)
    verify_postconditions(obj, cats)                              # je Kategorie ohne Review genau ein aktiver drive_node
    return finish(run, plan, dry_run=False)
```

Hilfsfunktionen:

```python
def count_tree(drive, folder_id) -> tuple[int, str]:
    """Anzahl aller nicht gelöschten Nicht-Ordner-Einträge rekursiv (Dateien, Verknüpfungen, Google-Dokumente)
    und SHA-256 über die sortierte Liste ihrer IDs. Zählt keine Ordner und keine Papierkorb-Einträge."""

def norm(name) -> str:
    """NFC, casefold, Unterstrich zu Leerzeichen, Leerraum zusammenfassen, Transliteration ä ae, ö oe, ü ue, ß ss."""

def loose_match(name, expected) -> bool:
    """Vergleich ohne führendes Nummernpräfix NN_ oder NN und ohne Trennzeichen: 'Eigentümerakte' passt lose auf '05_Eigentümerakte',
    ebenso '5_Eigentuemerakte' und '05 Eigentümerakte'."""
```

`count_tree` wird pro Umbenennung zweimal und pro Lauf zweimal aufgerufen. Bei einem Objektordner mit einigen tausend Dateien sind das wenige hundert Listenaufrufe; die Werte fließen in das Protokoll und in den Integrationstest „Dateianzahl vorher gleich nachher".

### 4.3 Umbenennung `05_Sonstiges` zu `06_Sonstiges` im Detail

1. Der Altname wird ausschließlich über `drive.legacy_folder_aliases` erkannt (Seed: `{"06_Sonstiges": ["05_Sonstiges"]}`). Der Wert steht in der Seed-Migration im Verzeichnis `db/seeds/`, das von der Grep-Prüfung der Definition of Done ausgenommen und in der Dokumentation benannt ist. Anwendungscode unter `src/` enthält den Altnamen nicht.
2. Vor der Umbenennung: `count_tree` des Altordners (Anzahl und ID-Hash) in `drive_sync_actions.file_count_before`.
3. `files.update(fileId, body={"name": "06_Sonstiges"})`. Die ID bleibt, alle Kinder bleiben, keine Kopie, kein Verschieben.
4. Nach der Umbenennung: `files.get` bestätigt den Namen, `count_tree` erneut. Gleichheit von Anzahl und ID-Hash ist die Erfolgsbedingung. Ungleichheit ist praktisch nur durch gleichzeitige manuelle Änderungen in Drive möglich; sie führt zu `result = failed`, Review-Fall und Abbruch weiterer Schreibaktionen dieses Laufs (nichts wird zurückgenommen, weil nichts verloren ist; der nächste Lauf sieht den umbenannten Ordner als vorhanden).
5. Erst danach wird `05_Eigentümerakte` angelegt (Reihenfolge über `seq_no`, Vorgabe CR 9.3). Bestand die Umbenennung den Zähltest nicht, wird die Anlage in diesem Lauf nicht ausgeführt.
6. Rollback: Eine Umbenennung ist über `files.update` mit dem alten Namen umkehrbar; der Altname steht in `drive_sync_actions.name_before`. Ein Admin-Befehl `drive:undo-rename <action_id>` wird vorgesehen (CR 0 Punkt 5, reversible Migration).

### 4.4 Sonderfälle

| Fall | Erkennung | Verhalten (Vorschlag: nicht raten) |
|---|---|---|
| A: `05_Sonstiges` und `06_Sonstiges` existieren beide | Alias-Treffer und exakter Treffer derselben Kategorie | Review-Fall `legacy_and_target_both_exist` mit beiden Ordnern (ID, Dateianzahl, letzte Änderung). Optionen für den Sachbearbeiter: (1) Dateien des Altordners gelten als lose Bestandsdateien und gehen in die Pipeline, der Altordner bleibt bis er leer ist und wird dann vom Nutzer manuell behandelt; (2) Altordner in einen konfigurierten Archivnamen umbenennen (`drive.legacy_conflict_rename_pattern`, Standard leer, also deaktiviert). Bis zur Entscheidung: `06_Sonstiges` wird registriert (es ist eindeutig vorhanden), die Anlage von `05_Eigentümerakte` wird zurückgestellt, weil sonst zwei Ordner mit Präfix `05_` nebeneinander stehen. Inventur des Altordners erfolgt in jedem Fall (Abschnitt 5), Verschiebungen aus ihm heraus nur nach Klassifikation. |
| B: mehrere Altordner (`05_Sonstiges`, `05_sonstiges`) | mehr als ein Alias-Treffer | Review-Fall `multiple_legacy_folders`, keine Umbenennung, keine Anlage der Kategorie. |
| C: mehrere Ordner mit exakt gleichem Namen (Drive erlaubt das) | mehr als ein exakter Treffer | Review-Fall `multiple_folders_same_name` mit Kandidatenliste (Dateianzahl, Anlagedatum). Sachbearbeiter wählt den führenden Ordner; die Wahl wird als `drive_nodes`-Registrierung gespeichert. Der andere Ordner bleibt unverändert, seine Dateien sind Bestandsdateien und werden inventarisiert; Verschiebungen aus ihm heraus erfolgen nur über die Pipeline. Kein Ordner wird angelegt. |
| D: abweichende Schreibweise | normalisiert gleich (`05_eigentümerakte`, `05_Eigentuemerakte`, `05 Eigentümerakte`) oder lose gleich (`Eigentümerakte`, `5_Eigentümerakte`) | Normalisiert gleich und eindeutig: als vorhanden registrieren, `expected_name` gespeichert, Protokollhinweis „Schreibweise abweichend, nicht umbenannt" (CR 2: bestehende Ordner werden nicht umbenannt). Lose gleich oder mehrdeutig: Review-Fall `similar_folder_name` mit Optionen „diesen Ordner verwenden" oder „neuen Ordner nach Katalog anlegen". Bis dahin keine Anlage. |
| E: Hauptordner als Verknüpfung | `mimeType` Shortcut mit Ordnerziel | Ziel auflösen, registrieren, Hinweis im Protokoll. Verknüpfungen werden nicht ersetzt. |
| F: gespeicherte Folder-ID liefert 404 oder liegt im Papierkorb | `files.get` | `drive_nodes.status = missing` oder `trashed`, Review-Fall `drive_folder_missing`. Keine automatische Neuanlage, weil der Ordner Dateien enthalten kann. Nach Entscheidung „neu anlegen" wird eine neue `drive_nodes`-Zeile erzeugt, die alte bleibt mit Status. |
| G: Objektordner enthält lose Dateien auf erster Ebene | `walk` | Inventur, kein Fehler. Verschiebung nur über die Pipeline (Abschnitt 5). |
| H: Zusätzliche, nicht katalogisierte Ordner im Objektordner (z. B. `Fotos`) | kein Treffer in Katalog oder Alias | Unverändert lassen, im Protokoll als `zusätzlicher Ordner` aufführen, Inhalt inventarisieren. |

### 4.5 Dry-Run-Modus

`reconcile_object(..., dry_run=True)` durchläuft Schritte 0 bis 6 und liefert die Planliste. Die Objektansicht zeigt sie als Tabelle:

| Spalte | Inhalt |
|---|---|
| Nr. | `seq_no` |
| Aktion | Anlegen, Umbenennen, Registrieren, Review-Fall, Inventur |
| Ordner | Name vorher, Name nachher, Eltern-ID |
| Dateien | Anzahl rekursiv (bei Umbenennung Pflicht) |
| Hinweis | Schreibweise abweichend, via Verknüpfung, zusätzlicher Ordner, blockiert durch Review |

Der Dry-Run schreibt in Drive nichts. Er schreibt in die Datenbank nur `drive_sync_runs` (mit `dry_run = 1`) und `drive_sync_actions` (`planned = 1`, `executed = 0`). Review-Fälle werden im Dry-Run nicht angelegt, sondern nur als geplante Aktion `create_review` gezeigt; erst die Ausführung erzeugt sie. Die Inventur registriert im Dry-Run keine `documents`. Nachweis im Test: Der `RecordingDriveAdapter` enthält nach einem Dry-Run ausschließlich Lesemethoden.

### 4.6 Idempotenz und Nachweis

Idempotenz ergibt sich aus der Planung gegen den Ist-Zustand: Ein zweiter Lauf findet alle Hauptordner exakt vor, plant nur `register`-Aktionen (die keinen Schreibvorgang in Drive auslösen) und keine Anlagen, Umbenennungen oder Review-Fälle; die Inventur findet alle Dateien bereits in `documents` (Abgleich über `drive_file_id`, bei unverändertem `md5Checksum` und `modifiedTime` kein neuer Job).

Nachweis auf drei Ebenen:

1. `drive_sync_runs.no_changes = 1` und `actions_executed = 0` (Schreibaktionen) für den zweiten Lauf.
2. `RecordingDriveAdapter`: das Aufrufprotokoll des zweiten Laufs enthält nur `get`, `list_children`, `walk`.
3. Dateizählung: `file_count_before = file_count_after` in beiden Läufen, ID-Hash gleich.

Der Integrationstest aus CR 14 prüft alle drei Ebenen für die drei Testobjekte (ohne Unterordner, mit `05_Sonstiges` und Dateien, vollständige Struktur), jeweils Dry-Run, Ausführung, zweiter Lauf.

## 5. Bestandsdateien im Objektordner

### 5.1 Inventur

Schritt 6 des Abgleichs läuft mit `drive.walk(obj_folder.id)` rekursiv über alle Ordner des Objektordners, einschließlich nicht katalogisierter Ordner, Altordner und der bereits vorhandenen Hauptordner. Je Eintrag, der kein Ordner ist:

| Prüfung | Ergebnis |
|---|---|
| Eintrag ist eine von der Software erzeugte Listen-Datei (`drive_nodes.node_kind = list_file`, gleiche `drive_file_id`) | überspringen, nicht in die Pipeline |
| Eintrag ist eine Verknüpfung (Shortcut) | Ziel auflösen; liegt das Ziel außerhalb des Objektordners, nur protokollieren (`Hinweis: externe Verknüpfung`), nicht verarbeiten |
| Eintrag ist ein Google-Dokument (Docs, Sheets), also ohne `md5Checksum` und ohne Binärinhalt | registrieren mit `mime_type` des Google-Formats; Download über Export als PDF für die OCR-freie Textextraktion (Fachentwurf E entscheidet die Verarbeitung); Verschiebung wie bei Dateien möglich |
| `drive_file_id` bereits in `documents` | Vergleich `md5Checksum` und `modifiedTime` mit dem gespeicherten Stand; unverändert: kein neuer Job; verändert: neuer Job `reprocess` (Inhalt geändert), Protokollhinweis |
| neu | `documents`-Zeile mit `source = drive_existing`, `source_path` (Pfad aus Ordnernamen, informativ), `drive_node_id` des aktuellen Ordners, `status = registered`; `processing_jobs`-Eintrag mit `idempotency_key` aus (Jobtyp, Objekt, `drive_file_id`, `md5Checksum`) |

Der SHA-256 (`documents.sha256`, Fachentwurf D) wird erst nach dem Download im Worker gebildet, weil Drive für Binärdateien nur MD5 liefert. Vorschlag: `documents` um eine nullable Spalte `drive_md5` ergänzen, damit die Inventur Änderungen ohne Download erkennt. Vor dem Download ist der Hash-Schlüssel der Idempotenz die Kombination aus `drive_file_id` und `md5Checksum`; nach dem Download greift der SHA-256 und markiert Dubletten innerhalb des Objekts.

Reihenfolge der Registrierung: `(Pfad, casefold(Name), drive_file_id)`, damit `documents.id` und Jobreihenfolge zwischen Läufen stabil bleiben.

### 5.2 Verschiebung nach Klassifikation

Die Pipeline (Fachentwurf E) liefert je Dokument Kategorie, Unterordner, Unterart, Zeitraum und Konfidenz. Der Drive-Anteil:

1. Zielordner über `drive_nodes` auflösen: Kategorie 01 bis 06 plus Unterordner beziehungsweise Eigentümerakte plus Unterordner (Abschnitt 6.3). Fehlt der Zielordner (z. B. eine Eigentümerakte, die es noch nicht gibt), wird er über `ensure_folder_path` angelegt, mit denselben Vorprüfungen wie im Abgleich.
2. Vergleich Ist-Elternordner (aus `files.get`, nicht aus dem Cache) mit Ziel. Gleich: keine Aktion, `status = filed`.
3. Konfidenz `>= classification.auto_file_threshold` (Seed 0,90 laut Fachentwurf D, ANNAHME dort, Kalibrierung mit Testset): `files.update(fileId, addParents=ziel, removeParents=ist)`. Kein Download, kein Upload, keine Umbenennung der Datei. `documents.drive_node_id`, `drive_moved_at`, `status = filed`. Audit-Eintrag mit Quelle und Ziel.
4. Konfidenz darunter: keine Verschiebung. Review-Fall `move_proposal` mit `proposed_action = {from_node_id, to_node_id, category, subfolder, document_type_id, period_year}`; die Datei bleibt, wo sie ist. Nach Bestätigung im Review Center wird die Verschiebung als Schreibjob eingereiht.
5. Zielbereich `06_Sonstiges`: Ablage nur, wenn alle Stufen ohne ausreichende Zuordnung bleiben (CR 8); jede Ablage dort erzeugt automatisch einen Review-Fall `unclear` mit `misc_subfolder_id`. Auch Bestandsdateien, die bereits in `06_Sonstiges` liegen und dort bleiben, erhalten den Review-Fall, damit der KPI Anteil 06_Sonstiges vollständig ist.
6. Nichts wird gelöscht, auch keine leeren Ordner nach Verschiebungen; leere Altordner erscheinen im Protokoll als Hinweis.

Dateien in der Unterstruktur einer Eigentümerakte, die durch einen Eigentümerwechsel historisch einem anderen Eigentümer zuzuordnen sind, werden nicht automatisch verschoben, sondern mit Kandidatenliste ins Review Center gestellt (CR 7, Regel „nicht raten" bei mehreren historischen Eigentümern).

## 6. Eigentümerakten-Ordner

### 6.1 Zentrale Benennungsfunktion

Eine einzige Funktion erzeugt jeden Aktennamen; sie hat keine Nebenwirkungen und liest die Konfiguration aus `app_settings` (Kategorie `owner_file`). Sie wird von der Anlage der Eigentümerakte (`owner_files.folder_name`), von der Review-Vorschau und von den Unit-Tests aufgerufen.

```python
@dataclass(frozen=True)
class OwnerNameInput:
    file_kind: Literal["unit_owner", "unknown_unit", "unassigned"]
    unit_label: str | None            # Ist-Bezeichnung, z. B. "WE 14", "Garage 3", "Stellplatz Nr. 12"
    unit_type: str | None             # apartment | commercial | parking | garage | underground_parking | cellar | other
    owner_names: list[OwnerNamePart]  # je Eigentümer: kind (natural_person | legal_entity | community), last_name, company_name, company_short_name
    valid_from: date | None           # für Kollisionssuffix
    existing_names_in_object: set[str]

def build_owner_folder_name(inp: OwnerNameInput, cfg: OwnerFileNamingConfig) -> str:
    if inp.file_kind == "unassigned":
        return cfg.name_unassigned                                           # "Unzugeordnet"
    names = [normalize_name(display_name(p, cfg), cfg) for p in inp.owner_names]
    names = dedupe_keep_order(sorted(set(names), key=sort_key))               # alphabetisch, Doppelte (Ehepaar) einmal
    if not names:
        names = [cfg.name_unknown_owner]                                      # Vorschlag "Unbekannt", siehe 6.2 Punkt 8
    name_part = join_names(names, cfg)                                        # max_names, overflow_suffix
    if inp.file_kind == "unknown_unit":
        base = f"{cfg.name_unknown_unit_prefix}_{name_part}"                  # "Unbekannte_WE_<Namen>"
    else:
        base = f"{unit_token(inp.unit_label, inp.unit_type, cfg)}_{name_part}" # "WE03_<Namen>"
    base = fit_length(base, names, cfg)                                       # kürzen über weniger Namen, dann Kappung
    return resolve_collision(base, inp, cfg)                                  # Suffix bei gleichem Namen im Objekt

def join_names(names, cfg) -> str:
    if len(names) <= cfg.name_max_names:                                     # 3
        return cfg.name_separator.join(names)                                # "-"
    return f"{names[0]}{cfg.name_separator}{cfg.name_overflow_suffix}"        # "Nachname1-ua"
```

Konfiguration (Ergänzung zu den Seeds aus Fachentwurf D, Kategorie `owner_file`):

| Schlüssel | Seed | Herkunft |
|---|---|---|
| `owner_file.name_separator` | `"-"` | CR 4 |
| `owner_file.name_max_names` | `3` | CR 4 |
| `owner_file.name_overflow_suffix` | `"ua"` | CR 4 |
| `owner_file.name_unknown_unit_prefix` | `"Unbekannte_WE"` | CR 4 |
| `owner_file.name_unassigned` | `"Unzugeordnet"` | CR 4 |
| `owner_file.name_unknown_owner` | `"Unbekannt"` | Vorschlag, im CR nicht geregelt (Einheit bekannt, Eigentümer nicht) |
| `owner_file.unit_prefix_mode` | `"by_type"` | Vorschlag, offene Frage (Befund 6.7), siehe 6.2 Punkt 3 |
| `owner_file.unit_prefix_map` | `{"apartment": "WE", "commercial": "GE", "parking": "ST", "garage": "GA", "underground_parking": "TG", "cellar": "KE", "other": "VE"}` | Vorschlag, abgeleitet aus den im Befund beobachteten Präfixen |
| `owner_file.unit_number_pad` | `2` | CR 4 zeigt `WE01`, `WE03` |
| `owner_file.transliterate_umlauts` | `false` | Vorschlag, siehe offene Frage zur Umlaut-Politik |
| `owner_file.name_max_length` | `100` | ANNAHME, Verifikation gegen Pfadlängen der eingesetzten Drive-Desktop-Clients und Windows-Pfadgrenzen im Testlauf |
| `owner_file.collision_suffix_mode` | `"year_then_counter"` | Fachentwurf D, Vorschlag |
| `owner_file.legal_form_tokens` | `["GmbH", "AG", "KG", "GbR", "UG", "e.V.", "mbH", "& Co.", "OHG", "eG", "SE"]` | Vorschlag, Liste administrativ erweiterbar |

### 6.2 Normalisierung und Regeln

1. Unicode-Normalisierung NFC (Drive-Namen aus macOS-Uploads können NFD sein). Leerraum am Rand entfernen, mehrfachen Leerraum zu einem Leerzeichen.
2. Verbotene oder problematische Zeichen entfernen: Schrägstrich, Backslash, Doppelpunkt, Stern, Fragezeichen, Anführungszeichen, Kleiner- und Größerzeichen, senkrechter Strich, Steuerzeichen. Ergebnis ohne führenden oder abschließenden Punkt.
3. Einheitentoken: `unit_prefix_mode = always_we` erzeugt immer `WE` plus Nummer. Vorschlag ist `by_type`, weil bei `always_we` eine Garage Nr. 3 und eine Wohnung Nr. 3 denselben Ordner `WE03_...` ergäben und die Kollisionsregel dann Suffixe an fachlich verschiedene Akten hängt. Nummer aus der Ziffernfolge des `unit_label`, mindestens `unit_number_pad` Stellen, längere Nummern ungekürzt (`WE103`), Buchstabenzusatz kleingeschrieben angehängt (`WE14a`). Fehlt eine Ziffernfolge, wird das bereinigte `unit_label` ohne Leerzeichen verwendet (`Haus3` bleibt als `HausN`-Muster erkennbar).
4. Anzeigename je Eigentümer: natürliche Person `last_name`; juristische Person `company_short_name`, falls gepflegt, sonst `company_name` ohne Rechtsformtoken aus `legal_form_tokens` und ohne dadurch entstehende Restzeichen (`&`, Kommata); Gemeinschaft (`community`) `company_name` beziehungsweise `last_name`, wie im Datensatz gepflegt. Leerzeichen innerhalb eines Namens bleiben erhalten.
5. Doppelnamen behalten ihren Bindestrich (`Muster-Beispiel`). Der Trenner zwischen mehreren Eigentümern ist laut CR ebenfalls der Bindestrich; die daraus folgende Mehrdeutigkeit im Ordnernamen ist hinnehmbar, weil der Ordner eine Ansicht auf `owner_files` ist und die Zuordnung über IDs läuft. Wer die Mehrdeutigkeit vermeiden will, setzt `name_separator` auf ein anderes Zeichen (Vorschlag als Option, keine Abweichung ohne Entscheidung).
6. Alphabetische Sortierung über einen Sortierschlüssel mit Transliteration (ä zu ae, ö zu oe, ü zu ue, ß zu ss, weitere Diakritika entfernt) und casefold; Ausgabe behält die Originalschreibweise. Gleiche Namen (Ehepaar Mustermann und Mustermann) erscheinen einmal.
7. Umlaute im Ausgabenamen bleiben bei `transliterate_umlauts = false` erhalten (`Müller`), bei `true` werden sie wie im Sortierschlüssel ersetzt (`Mueller`).
8. Einheit bekannt, aber kein Eigentümername verwertbar (leerer Nachname, nur Vorname): Der CR regelt diesen Fall nicht. Vorschlag: `WE03_Unbekannt` über `name_unknown_owner`; der Fall wird zusätzlich als Vollständigkeitsbefund geführt.
9. Länge: Überschreitet der Name `name_max_length`, werden zuerst Namen reduziert (auf `Nachname1-ua`), dann der verbleibende Name gekappt. Das Einheitentoken wird nie gekappt.
10. Kollision innerhalb des Objekts (`owner_files.folder_name` eindeutig je Objekt): Suffix `_<Jahr aus valid_from>`, ohne bekanntes Datum `_2`, `_3` aufsteigend. Typischer Fall: Verkauf von Mustermann an Mustermann (Familie) auf derselben Einheit. Der Ordner des Alteigentümers bleibt unverändert bestehen (CR 4).
11. Eigentümerwechsel: Die Akte des Alteigentümers behält Namen und Ordner; die neue Akte erhält einen neuen Namen und einen neuen Ordner. Es wird nie umbenannt oder zusammengeführt. Ein Eigentümer mit mehreren Einheiten erhält je Einheit eine Akte (Einheit führt).

### 6.3 Unterstruktur und Folder-ID-Cache

Die Unterordner je Eigentümerakte kommen aus `document_subfolders` für Kategorie 05 (Seed: `01_Stammdaten` bis `11_Sonstiges` wortgetreu aus CR 4, ASCII-Schreibweise wie dort). Anlage:

1. Beim Anlegen einer `owner_files`-Zeile wird kein Drive-Ordner erzeugt (Vorschlag: verzögerte Anlage), sondern erst bei der ersten Ablage eines Dokuments oder auf Anforderung („Akte in Drive anlegen" in der Objektansicht). Begründung: Bei 869 Einheiten im Bestand entstünden sonst tausende leere Ordner, die den Abgleich und die Quota belasten. Abweichende Auslegung (alle Akten sofort mit voller Unterstruktur anlegen) ist per Konfiguration `owner_file.create_folders_eagerly` (Seed `false`) möglich.
2. `ensure_owner_folder(owner_file_id)`: `drive_nodes` nach `owner_file_folder` prüfen (`files.get` zur Bestätigung, bei 404 Status `missing` und Neuanlage nach Review); fehlt die Zeile, `files.list` im Hauptordner 05 nach exaktem Namen (Wiederaufnahme), sonst `files.create`. Danach die elf Unterordner ebenso (`owner_file_subfolder`), in Katalogreihenfolge, jeweils mit Vorprüfung. Ergebnis: zwölf `drive_nodes`-Zeilen je Akte.
3. Cache: `drive_nodes` ist der persistente Cache (CR 4 und 13). Ein zusätzlicher Prozess-Cache (Redis, Schlüssel `drive:node:<owner_file_id>:<subfolder_id>`, kurze Lebensdauer, ANNAHME 10 Minuten, Verifikation über Trefferquote im Statusbereich) spart Datenbankzugriffe im Worker. Bei 404 aus Drive wird beides invalidiert.
4. Die Manuell-Umbenennung eines Aktenordners in Drive durch einen Nutzer wird beim nächsten Zugriff erkannt (`files.get` liefert anderen Namen): `drive_name` wird aktualisiert, `expected_name` bleibt, Protokollhinweis. Es wird nicht zurückbenannt (Ordner sind Ansicht, IDs führen).

### 6.4 Unit-Testfälle der Benennungsfunktion

Konfiguration für alle Fälle: Seeds aus 6.1, `unit_prefix_mode = by_type`, `transliterate_umlauts = false`, sofern nicht anders angegeben. Alle Namen synthetisch.

| Nr. | Eingabe | Erwarteter Name | Prüft |
|---|---|---|---|
| 1 | unit_owner, `WE 1`, apartment, [Mustermann] | `WE01_Mustermann` | Standardfall, Auffüllen auf zwei Stellen, Leerzeichen im Label |
| 2 | unit_owner, `WE14`, apartment, [Mustermann] | `WE14_Mustermann` | zweistellige Nummer ohne Änderung |
| 3 | unit_owner, `WE 103`, apartment, [Mustermann] | `WE103_Mustermann` | dreistellige Nummer wird nicht gekürzt |
| 4 | unit_owner, `WE 14a`, apartment, [Mustermann] | `WE14a_Mustermann` | Buchstabenzusatz |
| 5 | unit_owner, `WE03`, apartment, [Zeta, Alpha] | `WE03_Alpha-Zeta` | alphabetische Reihenfolge |
| 6 | unit_owner, `WE03`, apartment, [Gamma, Alpha, Beta] | `WE03_Alpha-Beta-Gamma` | genau drei Namen |
| 7 | unit_owner, `WE03`, apartment, [Delta, Gamma, Alpha, Beta] | `WE03_Alpha-ua` | mehr als drei Namen |
| 8 | unit_owner, `WE05`, apartment, [Mustermann, Mustermann] | `WE05_Mustermann` | gleiche Nachnamen einmal |
| 9 | unit_owner, `WE06`, apartment, [Öztürk, Adler, Zimmer] | `WE06_Adler-Öztürk-Zimmer` | Umlaut sortiert wie Grundbuchstabe, Ausgabe unverändert |
| 10 | unit_owner, `WE06`, apartment, [meier, Adler] | `WE06_Adler-meier` | casefold beim Sortieren, Schreibweise erhalten |
| 11 | unit_owner, `WE02`, apartment, [Muster-Beispiel] | `WE02_Muster-Beispiel` | Doppelname behält Bindestrich |
| 12 | wie 11, `transliterate_umlauts = true`, [Müller-Lüdenscheidt] | `WE02_Mueller-Luedenscheidt` | Transliteration |
| 13 | unit_owner, `WE07`, apartment, [legal_entity company_name `Muster Immobilien GmbH & Co. KG`] | `WE07_Muster Immobilien` | Rechtsformtoken entfernt, Restzeichen bereinigt |
| 14 | unit_owner, `WE07`, apartment, [legal_entity company_short_name `MusterImmo`] | `WE07_MusterImmo` | Kurzname hat Vorrang |
| 15 | unit_owner, `WE08`, apartment, [community `Erbengemeinschaft Mustermann`] | `WE08_Erbengemeinschaft Mustermann` | Gemeinschaft, Leerzeichen erhalten |
| 16 | unit_owner, `WE04`, apartment, [`Muster/Frau: Test?`] | `WE04_MusterFrau Test` | verbotene Zeichen entfernt |
| 17 | unit_owner, `WE04`, apartment, [`  Muster   Mann `] | `WE04_Muster Mann` | Leerraum bereinigt |
| 18 | unit_owner, `Garage 3`, garage, [Mustermann] | `GA03_Mustermann` | Präfix nach Typ |
| 19 | wie 18, `unit_prefix_mode = always_we` | `WE03_Mustermann` | Modus immer WE |
| 20 | unit_owner, `Stellplatz Nr. 12`, parking, [Mustermann] | `ST12_Mustermann` | Präfix aus Label-Muster des Bestands |
| 21 | unit_owner, `TG 7`, underground_parking, [Mustermann] | `TG07_Mustermann` | Tiefgarage |
| 22 | unit_owner, `WE03`, apartment, [] | `WE03_Unbekannt` | Einheit bekannt, Eigentümer unbekannt (Vorschlag) |
| 23 | unknown_unit, None, None, [Mustermann] | `Unbekannte_WE_Mustermann` | CR-Fall Einheit unbekannt |
| 24 | unknown_unit, None, None, [Zeta, Alpha] | `Unbekannte_WE_Alpha-Zeta` | Mehrfachregel auch hier |
| 25 | unassigned, None, None, [] | `Unzugeordnet` | CR-Fall weder Einheit noch Eigentümer |
| 26 | unit_owner, `WE03`, apartment, [Mustermann], `existing_names_in_object` enthält `WE03_Mustermann`, `valid_from = 01.07.2026` | `WE03_Mustermann_2026` | Kollision, Jahr des Eigentumsbeginns |
| 27 | wie 26, `valid_from = None`, vorhanden `WE03_Mustermann` und `WE03_Mustermann_2` | `WE03_Mustermann_3` | Kollision ohne Datum, Zähler |
| 28 | unit_owner, `WE09`, apartment, drei Namen mit je 40 Zeichen, `name_max_length = 100` | `WE09_<Name1>-ua` | Länge: Reduktion auf einen Namen mit Suffix |
| 29 | unit_owner, `WE09`, apartment, ein Name mit 120 Zeichen | `WE09_` plus 95 Zeichen des Namens | Kappung, Token bleibt vollständig |
| 30 | unit_owner, `WE01`, apartment, [Name in NFD-Kodierung `Mu¨ller`] | `WE01_Müller` (NFC) | Unicode-Normalisierung |
| 31 | unit_owner, `Haus 3`, other, [Mustermann] | `VE03_Mustermann` | Typ other, Präfix aus Map |
| 32 | unit_owner, `WE 5`, apartment, [Mustermann], Objekt hat bereits `WE05_Mustermann` für dieselbe `owner_file_id` | `WE05_Mustermann` | Wiederholter Aufruf für dieselbe Akte ist idempotent, kein Suffix |

Zusätzlich ein Eigenschaftstest (property-based, z. B. mit Hypothesis, Version zum Umsetzungszeitpunkt prüfen): Für zufällige Eingaben ist das Ergebnis nie leer, enthält keine verbotenen Zeichen, ist höchstens `name_max_length` lang, beginnt bei `unit_owner` mit dem Einheitentoken und ist bei gleicher Eingabe identisch (Determinismus).

## 7. Listen-Dateien (CR 12a)

### 7.1 Ablageort und Namen

| Liste | Format | Zielordner | Dateiname |
|---|---|---|---|
| Eigentümerliste | xlsx | Hauptordner 05 (`05_Eigentümerakte`) | `00_Eigentuemerliste_NNN.xlsx` |
| Eigentümerliste | pdf | Hauptordner 05 | `00_Eigentuemerliste_NNN.pdf` |
| Mieterliste | xlsx | Hauptordner 04 (`04_Mieterakte`) | `00_Mieterliste_NNN.xlsx` |
| Mieterliste | pdf | Hauptordner 04 | `00_Mieterliste_NNN.pdf` |

`NNN` ist die Objektnummer in der Schreibweise des Objektordners (Ist-Nummer oder mit Nullauffüllung gemäß `drive.object_number_zero_pad_to`, offene Frage). Namensmuster als Konfiguration `lists.owner_list_name_pattern = "00_Eigentuemerliste_{number}.{ext}"` und `lists.tenant_list_name_pattern = "00_Mieterliste_{number}.{ext}"`. Das Präfix `00_` sorgt für die Einsortierung vor den Aktenordnern. Kein Datum im Dateinamen (CR 12a), Stand und Erzeugungszeitpunkt stehen im Dokumentkopf.

### 7.2 Aktualisierung über die gespeicherte File-ID

```python
def publish_list(object_id, list_type, list_format, local_path, content_hash):
    node = drive_nodes.find(object_id, node_kind="list_file", list_type=list_type, list_format=list_format, status="active")
    parent = drive_nodes.main_folder(object_id, category_for(list_type))          # 05 oder 04, muss registriert sein
    expected_name = render_list_name(object_id, list_type, list_format)

    if node is None:
        return create_new(parent, expected_name, local_path)                         # Erstanlage, siehe unten

    remote = drive.get(node.drive_file_id)
    if remote is None:                                                               # 404: manuell endgültig gelöscht
        node.status = "missing"; log("Liste in Drive nicht mehr vorhanden, neu angelegt")
        return create_new(parent, expected_name, local_path)
    if remote.trashed:                                                               # im Papierkorb
        node.status = "trashed"; log("Liste im Papierkorb, neu angelegt, Papierkorb unverändert")
        return create_new(parent, expected_name, local_path)
    if parent.drive_file_id not in remote.parents:                                   # manuell verschoben
        drive.move(remote.id, remote.parents[0], parent.drive_file_id); log("Liste zurück in Zielordner verschoben")
    if remote.name != expected_name:                                                 # manuell umbenannt
        drive.rename(remote.id, expected_name); log("Liste auf Sollnamen zurückbenannt")
    if last_generation(node).content_hash == content_hash:
        return record_generation(node, status="skipped_unchanged")
    result = drive.update_content(remote.id, local_path, mime_type_for(list_format))  # neue Version, gleiche ID
    verify_md5(result, local_path)
    return record_generation(node, status="done", drive_node_id=node.id)

def create_new(parent, name, local_path):
    existing = [c for c in drive.list_children(parent.drive_file_id) if c.name == name and not c.trashed]
    if len(existing) == 1:                                                           # Wiederaufnahme: Datei ohne DB-Zeile
        result = drive.update_content(existing[0].id, local_path, ...)
    elif len(existing) > 1:
        raise ReviewRequired("mehrere Listen-Dateien mit Sollnamen, Auswahl im Review Center")
    else:
        result = drive.upload(parent.drive_file_id, local_path, name, ...)
    drive_nodes.insert(node_kind="list_file", drive_file_id=result.id, created_by_app=1, ...)
    return record_generation(...)
```

Verhaltensregeln (Vorschläge, weil der CR nur den Regelfall beschreibt):

1. Manuell gelöscht (404) oder im Papierkorb: neue Datei anlegen, neue `drive_nodes`-Zeile, alte Zeile mit Status `missing` oder `trashed` behalten, Eintrag in `list_generations` mit Hinweis, Protokoll im Statusbereich. Die Software leert oder durchsucht den Papierkorb nicht.
2. Manuell verschoben oder umbenannt: Die Listen sind von der Software erzeugte Dateien; sie werden in den Zielordner zurückverschoben und auf den Sollnamen zurückbenannt, jeweils mit Protokoll. Begründung: Nur so bleibt „genau eine Datei je Format im vorgesehenen Ordner" ohne Neuanlage erfüllt. Wer das nicht möchte, setzt `lists.restore_position = false`; dann wird stattdessen ein Review-Fall erzeugt.
3. Unveränderte Datenbasis (gleicher `content_hash`): keine neue Drive-Version, `status = skipped_unchanged`. Das begrenzt die Anzahl Revisionen bei häufigen Review-Bestätigungen.
4. Auslöser (CR 12a): nach jedem Verarbeitungslauf, nach jeder Bestätigung im Review Center, manuell aus der Objektansicht. Bestätigungen werden je Objekt entprellt (`lists.debounce_seconds`, ANNAHME 60 Sekunden, Verifikation über die Anzahl `list_generations` je Objekt und Tag im ersten Betriebsmonat), damit 40 Bestätigungen einer Massenbearbeitung eine Erzeugung auslösen, nicht 40.
5. Gleichzeitigkeit: Ein Lock je Objekt (`lists:<object_id>`) serialisiert Erzeugung und Veröffentlichung; ein zweiter Auslöser innerhalb des Locks setzt nur ein Wiederholungskennzeichen.
6. MIME-Typen: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` und `application/pdf`. Es wird keine Konvertierung in Google-Formate angefordert, die Datei bleibt xlsx beziehungsweise pdf.

Integrationstest (CR 14): Zwei Läufe hintereinander, danach je Objekt genau eine Datei je Liste und Format im Zielordner (`list_children` gefiltert nach Sollnamen liefert genau einen Eintrag), `drive_nodes` enthält genau eine aktive `list_file`-Zeile je Kombination, `list_generations` zwei Zeilen, die zweite entweder `done` mit gleicher `drive_file_id` oder `skipped_unchanged`. Im Fake wird zusätzlich geprüft, dass die Revisionsliste der Datei zwei Einträge hat.

## 8. Duplikatoption `duplicate_owner_documents_in_drive`

Konfiguration `documents.duplicate_owner_documents_in_drive`, Seed `false` (CR 6).

| Zustand | Verhalten |
|---|---|
| `false` (Standard) | Gesamtdokumente liegen einmal in `03_Buchhaltung` (oder ihrer Zielkategorie). Die Zuordnung zu Eigentümerakten erfolgt ausschließlich relational über `document_owner_links` mit Seitenbereich. In der Eigentümerakte in Drive liegt keine Datei. Das Review Center und die Suche zeigen die Zuordnung mit Sprung auf den Seitenbereich. |
| `true` | Zusätzlich zur relationalen Zuordnung wird eine physische Zweitablage in der Eigentümerakte erzeugt: ganze Dateien per `files.copy` (serverseitig, kein Download) in den Zielunterordner der Akte; Seitenbereiche als extrahierte Teil-PDF (Extraktion im Worker aus der Transitdatei, Werkzeug qpdf oder pypdf, Version zum Umsetzungszeitpunkt prüfen), Upload in die Akte. Dateiname der Teilkopie nach Muster `documents.duplicate_name_pattern`, Seed `"{original_stem}_S{page_from}-{page_to}.pdf"` (Vorschlag). |

Regeln bei `true`:

1. Die Kopie ist abgeleitet, die Masterdatei bleibt Quelle. `document_owner_links` erhält eine nullable Spalte `drive_copy_file_id` (Vorschlag zur Ergänzung von Fachentwurf D), damit Kopie und Zuordnung verbunden sind und die Wiederaufnahmeprüfung keine zweite Kopie erzeugt.
2. Kopien werden nie automatisch aktualisiert oder gelöscht. Wird die Zuordnung im Review Center geändert, bleibt die alte Kopie bestehen und erhält einen Review-Fall `stale_copy` mit Vorschlag zum manuellen Umgang (nichts löschen).
3. Umschalten von `true` auf `false` lässt vorhandene Kopien unberührt; Umschalten von `false` auf `true` erzeugt keine rückwirkenden Kopien, sondern wirkt ab dann. Ein Admin-Befehl `drive:backfill-copies --object <nr> --dry-run` kann rückwirkende Kopien planen.
4. Der Integrationstest „Gesamtabrechnung mit 12 Einzelabrechnungen" (CR 14) läuft mit `false` und prüft null Kopien; ein zweiter Durchlauf mit `true` prüft zwölf Teilkopien in zwölf Akten und unveränderte Masterdatei.
5. Speicher- und Quotawirkung der Zweitablage wird im Admin-Bereich beim Schalter als Hinweis angezeigt.

## 9. Teststrategie für den Drive-Anteil

### 9.1 In-Memory-Fake `InMemoryDriveAdapter`

Der Fake implementiert die Schnittstelle aus 2.8 vollständig über einen Baum aus Knoten mit `id`, `name`, `mime_type`, `parents`, `trashed`, `content`, `md5`, `revisions`, `created_time`, `modified_time`. Eigenschaften, die für die Tests aus CR 14 nötig sind:

| Eigenschaft | Zweck |
|---|---|
| Mehrere Kinder mit gleichem Namen erlaubt | Sonderfall C (doppelte Ordnernamen), doppelte Objektnummer |
| Papierkorb (`trashed = true`) mit eigener Listung | Sonderfälle Papierkorb-Treffer, gelöschte Liste |
| Revisionen je Datei | Nachweis „Versionierung statt Neuanlage" bei Listen |
| Verknüpfungen (Shortcut) | Sonderfall E |
| Operationsprotokoll aller Aufrufe mit Parametern | Idempotenznachweis (zweiter Lauf nur Lesezugriffe), deterministische Reihenfolge |
| Fehlerinjektion je Operation: n-mal 403 Ratenlimit, 429 mit `Retry-After`, 5xx, 404 für eine ID, Abbruch eines resumable Uploads nach k Chunks | Backoff, Wiederaufnahme, Cache-Invalidierung |
| Deterministische ID-Vergabe (fortlaufend aus einem Seed) | reproduzierbare Erwartungswerte in Tests |
| Szenario-Lader aus YAML oder JSON (Baum aus Namen, Dateianzahl, Papierkorbmarken) | die drei Testobjekte aus CR 14 als Fixtures |
| `count_tree` nachrechenbar aus dem Baum | Prüfung der Vorher-nachher-Zählung des Abgleichs unabhängig vom Produktivcode |

Der Backoff selbst wird zusätzlich isoliert getestet: Der `GoogleDriveAdapter` erhält einen injizierbaren HTTP-Transport; die Testsuite nutzt die Mock-Transportklassen des offiziellen Clients (`HttpMockSequence`), um Sequenzen wie 403, 403, 200 zu simulieren, und eine gefälschte Uhr, damit Wartezeiten nicht real vergehen.

### 9.2 Abbildung der Integrationstests aus CR 14

| CR-14-Test | Fixture im Fake | Prüfungen |
|---|---|---|
| Testobjekt ohne Unterordner | Wurzel mit Objektordner `623 Musterstadt, Musterstraße 1` ohne Kinder | Dry-Run plant sechs Anlagen, null Umbenennungen; Ausführung legt sechs Hauptordner und vier Unterordner von 06 an; Zählung vorher 0, nachher 0; zweiter Lauf `no_changes = 1`, Protokoll nur Lesezugriffe |
| Testobjekt mit `05_Sonstiges` und Dateien | Objektordner mit `01` bis `04`, `05_Sonstiges` mit 7 Dateien in zwei Unterordnern | Dry-Run plant eine Umbenennung (`file_count_before = 7`) und danach Anlage `05_Eigentümerakte`; Ausführung: Ordner-ID unverändert, Name `06_Sonstiges`, `file_count_after = 7`, ID-Hash gleich; Inventur registriert 7 `documents`; zweiter Lauf ohne Änderungen; Grep über `src/` findet den Altnamen nicht (Test liest die Alias-Konfiguration aus dem Seed) |
| Testobjekt mit vollständiger Struktur | alle sechs Hauptordner und vier Unterordner vorhanden, dazu 3 lose Dateien | Dry-Run und Ausführung planen nur Registrierungen, `actions_executed = 0` Schreibaktionen; Inventur registriert 3 Dateien; zweiter Lauf identisch |
| Objektordner-Erkennung | Wurzel mit `082 Ort A`, `82 Ort A` (Duplikat über Zahlenwert), `10014 Ort B`, `6230 Ort C`, `Archiv`, `623 Ort D` im Papierkorb | Objekt 82: Review-Fall mit zwei Kandidaten; Objekt 10014: Treffer; Objekt 623: Papierkorb-Fall, kein Anlegen, Review-Fall; Objekt 700: Anlage nach Muster; `Archiv` im Protokoll als ignoriert |
| Sonderfälle A bis H | je ein Szenario | Review-Fall mit richtigem `case_subtype`, keine Schreibaktion für die betroffene Kategorie, übrige Kategorien abgeglichen |
| Listenerzeugung nach zwei Läufen | Objekt mit registriertem 04 und 05 | genau eine Datei je Format, zwei Revisionen oder `skipped_unchanged`; Szenarien gelöscht, Papierkorb, verschoben, umbenannt |
| Gesamtabrechnung mit 12 Einzelabrechnungen | Masterdatei in 03, zwölf `document_owner_links` | mit Option `false` null Kopien, mit `true` zwölf Kopien, Master unverändert |
| Pipeline-Abbruch und Wiederaufnahme | Fehlerinjektion: Upload bricht nach zwei Chunks ab, Verschiebung schlägt mit 503 fehl und wird wiederholt | nach Wiederaufnahme genau eine Datei, genau eine Verschiebung, keine Duplikate |

### 9.3 Optionaler Testlauf gegen ein echtes Test-Wurzelverzeichnis

Zweck: Verhalten der echten API (Eventual Consistency nach Anlagen, Escaping in `q`, Shared-Drive-Parameter, MIME-Typen, Revisionen) einmal je Release verifizieren. Ausgestaltung:

1. Konfiguration nur über Umgebungsvariablen der Testumgebung: `DRIVE_LIVE_TEST_ROOT_ID` (Ordner außerhalb des produktiven Wurzelpfads, vom Auftraggeber angelegt), `DRIVE_LIVE_TEST_TOKEN_FILE`. Fehlen sie, werden die Tests übersprungen (Markierung `live`).
2. Jeder Testlauf erzeugt unter dem Test-Wurzelverzeichnis einen Laufordner `TESTLAUF_<Zeitstempel>_<Zufall>` und arbeitet ausschließlich darin. Die Fixtures aus 9.2 werden dort aufgebaut (synthetische Namen, keine echten Objektdaten).
3. Aufräumen: Der Laufordner wird am Ende in den Papierkorb verschoben (`trashed = true`), nicht endgültig gelöscht; endgültiges Löschen erfolgt manuell oder über die Papierkorb-Frist von Drive. Damit gilt auch im Test „die Software löscht nichts endgültig".
4. Sicherung gegen Fehlkonfiguration: Der Live-Test bricht ab, wenn `DRIVE_LIVE_TEST_ROOT_ID` gleich `drive.root_folder_id` der Produktivkonfiguration ist oder wenn der Testordner Kinder enthält, die nicht mit `TESTLAUF_` beginnen.
5. Der Live-Test misst nebenbei Aufrufdauer je Operation und die Anzahl Ratenlimit-Antworten; die Werte gehen in den Bericht und ersetzen die Annahmen zu `drive.max_requests_per_second`.

### 9.4 Deployment-Test OAuth (8 Tage)

Nach der Erstautorisierung auf dem VPS läuft der tägliche erzwungene Refresh. Der Nachweis ist ein Export aus `audit_events` (Ereignis `oauth.refresh`, Status, Zeitpunkt) über mindestens acht aufeinanderfolgende Tage ohne Ereignis `oauth.authorized`. Der Statusbereich zeigt die Kette als „Tage seit Autorisierung ohne Neuanmeldung".

## 10. Protokoll des Abgleichs

### 10.1 Struktur

Quelle sind die Tabellen `drive_sync_runs` und `drive_sync_actions` (Fachentwurf D). Je Objekt und Lauf entsteht ein Protokoll mit folgender Struktur:

```text
Kopf
  Objektnummer, Objektbezeichnung, Verwaltungsart
  Lauf-ID, Modus (Dry-Run oder Ausführung), Auslöser (Anlage, Öffnen, manuell, Sammellauf), Nutzer
  Beginn, Ende, Dauer, Status
  Wurzel-ID, Objektordner-ID und Ist-Name, Anzahl Kandidaten (root_matches)
  Dateien rekursiv vorher, nachher, ID-Hash vorher, nachher
  Anzahl Aktionen geplant, ausgeführt, übersprungen, fehlgeschlagen; no_changes
Aktionen (eine Zeile je drive_sync_actions)
  seq_no, Typ, Kategorie, Name vorher, Name nachher, Drive-ID, Eltern-ID,
  Dateien vorher, Dateien nachher, geplant, ausgeführt, Ergebnis, Fehlertext, Zeitpunkt
Hinweise
  ignorierte Wurzelordner, zusätzliche Ordner im Objektordner, abweichende Schreibweisen,
  Verknüpfungen, externe Verknüpfungen, Dateien mit mehreren Elternordnern, leere Altordner
Review-Fälle
  erzeugte Fälle mit Typ, Untertyp, Kandidatenanzahl, Status
Inventur
  Anzahl registrierter Dateien neu, unverändert, geändert; Anzahl Google-Dokumente; Gesamtgröße
```

Die Datenbank ist der Ablageort der Wahrheit (append-only). Der Export ist eine Ansicht darauf.

### 10.2 Export

| Format | Zweck | Erzeugung |
|---|---|---|
| JSON | maschinell, vollständig, je Lauf | `GET /admin/drive/sync-runs/<id>.json` und Befehl `drive:export-protocol --run <id>` |
| XLSX | Lesefassung je Lauf und Sammelfassung über alle Objekte (Blatt „Objekte" mit einer Zeile je Objekt und letztem Lauf, Blatt „Aktionen", Blatt „Hinweise", Blatt „Review-Fälle") | Befehl `drive:export-protocol --all --format xlsx`, Erzeugung mit openpyxl |
| PDF | optional für die Abnahme, Layout der HVM-Berichte gemäß Skill hvm-ci (Kennlinie, Fußzeile mit Pflichtangaben), A4 quer | `--format pdf`, ReportLab-Bausteine aus dem hvm-ci-Skill |

Ablage der Exporte: `/srv/objektakte/exports/drive-sync/` mit Dateinamen `Abgleichsprotokoll_Bestand_<TT.MM.JJJJ>_<Lauf>.xlsx`. Optional zusätzlich Upload in einen konfigurierten Drive-Ordner `drive.protocol_folder_id` (Seed `null`, also aus). Dieser Ordner muss außerhalb des Wurzelpfads `01_Daten` liegen; die Anwendung verweigert die Konfiguration eines Ordners unterhalb der Wurzel, weil das Protokoll sonst als Bestandsdatei inventarisiert würde.

### 10.3 Nachweis für die Definition of Done

Die Definition of Done verlangt den Abgleich auf allen bestehenden Objektordnern mit vorliegendem Protokoll. Ablauf:

1. Sammellauf Dry-Run über alle Objekte (`drive:reconcile --all --dry-run`), Export der Planliste, Sichtung durch den Auftraggeber.
2. Sammellauf Ausführung, dann zweiter Sammellauf (Idempotenznachweis).
3. Export Sammelfassung XLSX: je Objekt Objektordner-ID, Dateien vorher und nachher, Anzahl Umbenennungen, Anzahl Anlagen, offene Review-Fälle, `no_changes` des zweiten Laufs. Zeilen mit offenen Review-Fällen sind gelb markiert; sie sind kein Fehler, sondern Entscheidungen, die vor Abnahme durch einen Sachbearbeiter zu treffen sind.
4. Grep-Nachweis für den Altnamen: Prüfskript `scripts/check_no_legacy_names.sh` grept `src/`, `tests/`, `docs/` mit Ausnahme von `db/seeds/` und `docs/anforderungen/` (der CR selbst enthält den Altnamen); Ergebnis wird dem Protokoll beigefügt.

## 11. Abweichungen vom CR-Wortlaut (Vorschläge, keine Entscheidungen)

| Nr. | CR | Vorschlag | Begründung |
|---|---|---|---|
| 1 | Erkennung über die dreistellige Objektnummer | führende Ziffernfolge mit konfigurierbarer Längenspanne, Vergleich über Zahlenwert | Befund 4: Bestand hat 2- bis 5-stellige aktive Nummern |
| 2 | Kein Treffer: Ordner anlegen | bei Treffer im Papierkorb nicht anlegen, sondern Review-Fall | vermeidet zwei Ordner gleicher Nummer nach Wiederherstellung |
| 3 | Dry-Run als Modus | Dry-Run als Planungsphase ohne Ausführung statt eines eigenen Dry-Run-Clients | ein Codepfad, kein Abweichungsrisiko zwischen Plan und Ausführung |
| 4 | `05_Sonstiges` umbenennen | zusätzlich zur Zählung ein Hash der sortierten Datei-IDs vorher und nachher | stärkerer Nachweis als reine Anzahl |
| 5 | Vorhandene Ordner bleiben unverändert | Ordner mit nur normalisiert gleichem Namen werden als vorhanden registriert und nicht umbenannt; lose Ähnlichkeit erzeugt Review-Fall | CR-Regel eingehalten, Doppelanlage vermieden |
| 6 | Eigentümerakte je Eigentümer mit Unterstruktur | Drive-Ordner erst bei erster Ablage oder auf Anforderung anlegen (`create_folders_eagerly = false`) | vermeidet tausende leere Ordner bei 869 Einheiten, Quota und Abgleichdauer |
| 7 | `WE01_Nachname` als Standard | Präfix nach Einheitentyp (`GA03`, `ST12`), Modus konfigurierbar | Kollision Garage 3 gegen Wohnung 3 bei einheitlichem `WE` |
| 8 | Fälle in CR 4 | zusätzlicher Fall Einheit bekannt, Eigentümer unbekannt: `WE03_Unbekannt` | Lücke im CR |
| 9 | Listen aktualisieren über File-ID | bei manuell verschobener oder umbenannter Liste Rückführung in Zielordner und Sollname mit Protokoll | Regel „genau eine Datei je Format im Zielordner" bleibt erfüllt |
| 10 | Duplikatoption als Schalter | Ergänzung `document_owner_links.drive_copy_file_id` und Backfill-Befehl mit Dry-Run | Wiederaufnahme ohne Doppelkopie, Rückwirkung steuerbar |
| 11 | Inventur über Dateihash | Ergänzung `documents.drive_md5` für Änderungserkennung ohne Download | Drive liefert MD5 in den Metadaten, SHA-256 erst nach Download |

## 12. Annahmen (mit Verifikation)

- ANNAHME: `drive.max_requests_per_second = 5` bei einem Drive-IO-Prozess als Startwert der clientseitigen Ratenbegrenzung. Verifikation: Quota-Seite der Cloud Console bei der Einrichtung ablesen, Zähler der 403-Ratenlimit-Antworten im Statusbereich während des Performance-Tests (Ziel null), Messwerte aus dem Live-Test (9.3).
- ANNAHME: Backoff-Startwerte Basis 1 Sekunde, Faktor 2, Obergrenze 64 Sekunden, 8 Versuche. Verifikation: Zähler fehlgeschlagener Aufrufe nach Ausschöpfung im Performance-Test (Ziel null), Anpassung über `drive.backoff`.
- ANNAHME: `drive.resumable_threshold_bytes = 5 MiB` und `drive.upload_chunk_bytes = 8 MiB`. Verifikation: Uploaddauer und Fehlerrate je Größenklasse im Performance-Test und Live-Test.
- ANNAHME: `drive.reconcile_on_open_min_interval_minutes = 15` als Drosselung des Abgleichs beim Öffnen eines Objekts. Verifikation: Anzahl Abgleichsläufe je Tag im Statusbereich im ersten Betriebsmonat; Ziel ist, dass das Öffnen eines Objekts keine spürbare Quota-Last erzeugt.
- ANNAHME: `owner_file.name_max_length = 100` Zeichen für Aktenordnernamen. Verifikation: Prüfung der längsten resultierenden Pfade gegen die eingesetzten Drive-Desktop-Clients und Windows-Pfadgrenzen im Testlauf; Anpassung des Werts, nicht des Codes.
- ANNAHME: Prozess-Cache für Folder-IDs in Redis mit 10 Minuten Lebensdauer. Verifikation: Trefferquote und Anzahl `files.get`-Aufrufe je Dokument im Performance-Test.
- ANNAHME: `lists.debounce_seconds = 60` für die Entprellung der Listenerzeugung nach Review-Bestätigungen. Verifikation: Anzahl `list_generations` je Objekt und Tag im ersten Betriebsmonat, Zielwert deutlich unter der Anzahl Bestätigungen.
- ANNAHME (übernommen aus Fachentwurf D): `classification.auto_file_threshold = 0,90` als Schwelle für automatische Verschiebungen. Verifikation: Kalibrierung mit dem Testset aus mindestens 20 Beispieldokumenten und KPI Anteil 06_Sonstiges nach dem ersten realen Objekt.
- ANNAHME: Der API-Höchstwert für `pageSize` und die Regeln für die Aufbewahrung alter Revisionen entsprechen der zum Umsetzungszeitpunkt gültigen Drive-Dokumentation. Verifikation: Prüfung der Dokumentation bei Umsetzungsbeginn, Werte als Konstanten mit Quellenangabe im Code.

## 13. Offene Fragen an den Auftraggeber (gebündelt)

1. Objektnummern bei Neuanlage von Objektordnern: Nullauffüllung auf drei Stellen (`082 Ort, Straße 1`) oder Ist-Nummer (`82 Ort, Straße 1`)? Betrifft `drive.object_number_zero_pad_to` und die Listen-Dateinamen (`00_Eigentuemerliste_082` oder `_82`). Die Erkennung bestehender Ordner ist davon unabhängig.
2. Präfix der Eigentümerakten-Ordner bei Nichtwohnungen: nach Einheitentyp (`GA03_Mustermann`, `ST12_Mustermann`, `GE01_Mustermann`, Empfehlung) oder immer `WE`? Falls nach Typ: Sind die vorgeschlagenen Kürzel WE, GE, ST, GA, TG, KE, VE passend?
3. Umlaut-Politik in Ordnernamen: Hauptordner `05_Eigentümerakte` mit Umlaut wie im CR, Unterordner ASCII wie im CR (`06_Wirtschaftsplaene`), Personennamen in Aktenordnern mit Umlaut (Empfehlung) oder transliteriert? Bitte alle drei Punkte bestätigen oder eine einheitliche Regel vorgeben.
4. Fall Einheit bekannt, Eigentümer unbekannt: Ordnername `WE03_Unbekannt` (Vorschlag) oder andere Bezeichnung, oder soll in diesem Fall keine Akte angelegt werden?
5. Treffer nur im Papierkorb bei der Objektordner-Suche: Review-Fall ohne automatische Anlage (Empfehlung) oder Neuanlage mit Protokollhinweis?
6. Gleichzeitiges Vorkommen von `05_Sonstiges` und `06_Sonstiges`: Review-Fall mit den beiden Optionen aus Abschnitt 4.4 Fall A (Empfehlung) oder feste Regel? Falls feste Regel: Welche?
7. Eigentümerakten-Ordner in Drive erst bei erster Ablage anlegen (Empfehlung) oder sofort für alle bekannten Einheiten mit voller Unterstruktur?
8. Manuell verschobene oder umbenannte Listen-Dateien: automatische Rückführung mit Protokoll (Empfehlung) oder Review-Fall?
9. Ist ein späterer Umzug des Wurzelpfads in eine geteilte Ablage (Shared Drive) geplant? Der Adapter ist dafür ausgelegt; die Frage betrifft nur den Zeitpunkt eines vollständigen Abgleichs nach dem Umzug.
10. Redirect-URI für die OAuth-App: Ist `https://uebernahme.muellerhv.de/admin/google/oauth/callback` als Pfad in Ordnung? Der Wert wird in der Cloud Console und in der Anwendung identisch benötigt.
11. Soll bei widerrufenem Token zusätzlich eine E-Mail-Benachrichtigung an eine feste Adresse gehen (`drive.alert_email`)? Falls ja, an welche?
12. Soll ein Test-Wurzelverzeichnis im Drive des technischen Kontos für den optionalen Live-Test (9.3) angelegt werden? Es liegt außerhalb von `01_Daten` und enthält nur synthetische Testdaten.
