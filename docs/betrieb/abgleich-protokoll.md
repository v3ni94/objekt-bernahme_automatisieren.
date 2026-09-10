# Protokoll Ordnerabgleich auf allen bestehenden Objektordnern (M14, CR 14)

Stand: 10.09.2026. Vorlage nach Umsetzungsplan 2.18. Der Ablauf ist verbindlich: Dry-Run, Sichtung, Freigabe, Ausführung, zweiter Lauf. Alle Ergebnisfelder sind „ausstehend“, bis der Zugang zu Google Drive (V-05 bis V-09) und die Freigabe des Dry-Run-Protokolls durch den Auftraggeber (V-19) vorliegen. Nichts wird vorab eingetragen.

## 1. Vorbereitung

| Schritt | Werkzeug | Ergebnis |
|---|---|---|
| Objekte aus dem Bestand angelegt (Nummer, Bezeichnung, Adresse, Verwaltungsart) | Import des Immoware24-Exports (M3) oder manuelle Erfassung; keine Neuanlage von Objektordnern | ausstehend |
| Wurzelverzeichnis aufgelöst und bestätigt | Verwaltung Google Drive | ausstehend |

## 2. Dry-Run über alle Objekte

```bash
docker compose exec -T web python manage.py drive_reconcile --all --dry-run
docker compose exec -T web python manage.py drive_export_protocol --latest --xlsx /data/exports/drive-sync/
```

| Kennzahl | Wert |
|---|---|
| Datum und Uhrzeit | ausstehend |
| Objekte gesamt | ausstehend |
| Objektordner erkannt, davon eindeutig, mehrdeutig, fehlend | ausstehend |
| Geplante Anlagen (Hauptordner, Unterordner) | ausstehend |
| Geplante Umbenennungen Altordner zu `06_Sonstiges` mit Dateianzahl je Ordner | ausstehend |
| Ignorierte Wurzelordner, zusätzliche Ordner, abweichende Schreibweisen, Verknüpfungen | ausstehend |
| Geplante Review-Fälle (`drive_structure`, `duplicate_object_number`) | ausstehend |
| Export übergeben (Datei, Datum) | ausstehend |

## 3. Sichtung und Freigabe durch den Auftraggeber

| Objekt | Freigabe (ja, nein, mit Änderung) | Datum | Bemerkung |
|---|---|---|---|
| alle oder je Objekt | ausstehend | | |

## 4. Ausführung

```bash
docker compose exec -T web python manage.py drive_reconcile --all
```

| Kennzahl | Wert |
|---|---|
| Datum und Uhrzeit | ausstehend |
| Umbenennungen mit Zählung und ID-Hash vorher gleich nachher | ausstehend |
| Abbrüche wegen Abweichung (`rename_count_mismatch`) | ausstehend |
| Anlagen ausgeführt | ausstehend |
| Dateien je Objektordner vorher gleich nachher (rekursiv) | ausstehend |

## 5. Zweiter Lauf (Idempotenznachweis)

| Kennzahl | Wert |
|---|---|
| `no_changes = 1` für alle Objekte | ausstehend |
| Nur Lesezugriffe (keine Schreibaktion im Protokoll) | ausstehend |

## 6. Prüfabfragen

```sql
-- je Objekt genau ein aktiver Hauptordner je Kategorie ohne offenen Fall
SELECT object_id, category_id, COUNT(*) FROM drive_nodes WHERE node_kind = 'main_folder' AND status = 'active' GROUP BY object_id, category_id HAVING COUNT(*) <> 1;
-- Dateianzahl vorher gleich nachher fuer alle Umbenennungen
SELECT COUNT(*) FROM drive_sync_actions WHERE action_type = 'rename' AND file_count_before <> file_count_after;
-- zweiter Lauf ohne Schreibaktion
SELECT id, no_changes FROM drive_sync_runs ORDER BY id DESC LIMIT 1;
```

Ergebnis: ausstehend.

## 7. Grep-Nachweis des Altnamens

`scripts/check_no_legacy_names.sh` am Tag der Ausführung: ausstehend (in der Entwicklungsumgebung am 10.09.2026 leer).

## 8. Offene Fälle nach dem Lauf

| Fall | Objekt | Entscheidung | Datum |
|---|---|---|---|
| ausstehend | | | |
