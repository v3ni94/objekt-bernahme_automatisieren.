# Performance-Bericht (M13, CR 14)

Stand: 10.09.2026. Vorlage mit allen Messgrößen aus Umsetzungsplan 2.17 Schritt 4. Alle Messwerte fehlen noch, weil der Zugang zum VPS (V-01), das Testobjekt im Drive-Testverzeichnis (V-08) und der Korpus nach F18 ausstehen. Felder mit „ausstehend“ werden erst nach dem Lauf gefüllt; es werden keine Werte geschätzt oder aus dem Rechenmodell übernommen. Der Rechenweg aus Prüfpunkt P1 (docs/umsetzungsplan.md, Abschnitt 2.7 und Anhang A) dient nur dem Vergleich.

## 1. Ziel und Abnahmekriterium

- Ein Objekt mit 10.000 Seiten (gemischt) wird in unter 3 Stunden vollständig verarbeitet (CR 14).
- Das Review Center antwortet währenddessen mit p95 unter 2 Sekunden bei fünf gleichzeitigen Nutzern (Ü20).
- Zweiter Lauf mit 100 Prozent Scan als ungünstigster Fall (B-35).
- Dritter Lauf mit anonymisierten Realdokumenten, falls der Auftraggeber sie bereitstellt (F18).

## 2. Testaufbau

| Punkt | Wert | Quelle |
|---|---|---|
| Server | VPS 187.124.23.80, Kerne, RAM, Platte laut Serverbefund M0 | docs/betrieb/m0-anleitung.md, Ergebnisblatt ausstehend |
| Version | Git-Tag und Image-Tag des Laufs | `tags.log`, Statusseite |
| Korpus Lauf 1 | `python tests/performance/corpus_generator.py --out <verz> --pages 10000 --scan-share 0.6 --degrade` (60 Prozent Scan, 40 Prozent Digital, ANNAHME A-04; Gesamtabrechnungen mit Einzelabrechnungen, einzelne Dokumente mit mehreren hundert Seiten) | Manifest des Generators |
| Korpus Lauf 2 | wie Lauf 1 mit `--scan-share 1.0` | Manifest |
| Korpus Lauf 3 | anonymisierte Realdokumente (nur nach F18) | Übergabeprotokoll |
| Start des Laufs | `manage.py perf_run --corpus <verz> --object 700` im Container `web` (Jobs über Celery) | Messprotokoll JSON des Kommandos |
| Begleitmessung | `scripts/perf_probe.sh` (docker stats alle 60 s, iostat), `tests/performance/latency_probe.py --users 5` gegen Objektansicht, Review-Liste, Detail, Vorschaubild, Suche, Entscheidung | Messdateien unter `/srv/objektakte/exports/perf/` |

## 3. Messwerte Lauf 1 (Basis, 60 Prozent Scan)

| Größe | Ziel oder Vergleichswert | Messwert | Bewertung |
|---|---|---|---|
| Gesamtdauer 10.000 Seiten | unter 180 min | ausstehend | ausstehend |
| Seiten je Minute gesamt | Rechenweg P1 | ausstehend | |
| Seiten je Minute OCR (`ocr_chunk`) | Rechenweg P1 (t_ocr aus M0) | ausstehend | |
| Seiten je Minute Textebene (`analyze_pages`) | | ausstehend | |
| Seiten je Minute Entitäten (`extract_entities`) | | ausstehend | |
| Seiten je Minute Vorschaubilder (`render_previews`) | | ausstehend | |
| Wartezeit der Vorschaujobs (Median, Maximum) | | ausstehend | |
| RAM-Spitze je Container (web, worker, worker-nlp, worker-io, beat, db, redis) | Limits aus docker-compose.yml | ausstehend | |
| OOM-Kills (`docker inspect --format '{{.State.OOMKilled}}'`) | 0 | ausstehend | |
| Plattenverbrauch `work/`, `ocr-cache/`, `previews/` (Spitze und Ende) | Rechenweg P1 (Plattenfaktor aus M0) | ausstehend | |
| Anteil Stufe 3 an allen Dokumenten | Kaltstart: nur unter Schwelle | ausstehend | |
| KI-Kosten des Laufs je Anbieter | Kostenlimit aus `ai.providers.<p>.cost_limit_eur_per_object` | ausstehend | |
| Drive-Ratenlimit-Antworten (403, 429) | 0 oder durch Backoff abgefangen | ausstehend | |
| Skalierungseffizienz OCR bei P = 1, 2, C minus 1 | Rechenweg P1 | ausstehend | |
| p95 und p99 je Endpunkt bei fünf Nutzern (Objektansicht, Review-Liste, Detail, Vorschaubild, Suche, Entscheidung) | p95 unter 2 s | ausstehend | |
| Anteil 06_Sonstiges brutto und bereinigt nach dem Lauf | unter 5 Prozent (CR 8) | ausstehend | |

## 4. Messwerte Lauf 2 (100 Prozent Scan)

Gleiche Tabelle wie Abschnitt 3; ausstehend.

## 5. Messwerte Lauf 3 (Realdokumente, optional)

Nur nach F18; ausstehend.

## 6. Feinabstimmung

Stellhebel aus P1 in der Reihenfolge des Aufwands (Chunkgröße `ocr.chunk_pages`, Parallelität der OCR-Worker, Rasterauflösung, `processing.max_parallel_objects`, Vorschau-Priorität). Je Änderung: geänderter Wert, wiederholter Lauf, Messwert vorher und nachher. Ausstehend.

## 7. Annahmen, die durch Messwerte ersetzt werden

Nach dem Lauf werden die betroffenen Zeilen in docs/umsetzungsplan.md Anhang A (A-04, A-11 bis A-15, A-29, A-35) mit den Messwerten fortgeschrieben; die Konfiguration nach Feinabstimmung wird in `.env` und `app_settings` eingetragen und hier dokumentiert.

## 8. Prüfabfragen nach dem Lauf

```sql
-- keine Doppelverarbeitung (T4, T5)
SELECT sha256, COUNT(*) FROM documents WHERE object_id = ? AND deleted_at IS NULL GROUP BY sha256 HAVING COUNT(*) > 1;
-- keine IBAN im Volltext (CR 10)
SELECT COUNT(*) FROM document_pages WHERE text_content REGEXP 'DE[0-9]{2}[0-9 ]{18,24}';
```

Ergebnis: ausstehend.
