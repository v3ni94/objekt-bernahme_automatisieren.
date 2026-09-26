# Abarbeitungsplan Prüfrückstand

Stand 26.09.2026. Bezug: Bestand im Prüfcenter vom 23.09. bis 26.09.2026. Dieser Plan nennt nur die gezählten Bestandszahlen aus dem Statusbericht; Erwartungen zur Wirkung sind als Anteil der jeweiligen Menge formuliert, nicht als Prognosezahl.

## 1. Bestand

| Menge | Fälle | Quelle |
|---|---|---|
| Offene Fälle gesamt | rund 32.300 | Statusbericht 23.09. bis 26.09.2026 |
| Neue E-Mail-Fälle | 3.434 | seit Formatweiche E-Mail (25.09.2026) |
| Unterart manual_check | 2.370 | KI-Einstufung Sonstiges, Dokumente in 06/02 |
| move_proposal und unclear, Unterart below_threshold | 1.495 | aus der Nachklassifikation, mit Kandidat in context.intended |
| Unterart no_owner_no_unit | 961 | Eigentümerakte ohne Einheit und Eigentümer |
| Unterart tenant_unknown | 351 | Mieterdokument ohne bekannten Mieter |
| Unterart ai_sample | 194 | Stichproben abgelegter KI-Entscheidungen (Hinweisfälle) |
| Unterart unsupported_format | 2.007 | Restformate, Entscheidungsvorlage GF liegt vor |
| Altbestand unter Schwelle | 6.686 | Stand 23.09.2026 |
| davon ohne Stufe-3-Aufruf | 4.566 | Stand 23.09.2026 |

Die Mengen überschneiden sich teilweise (der Altbestand unter Schwelle enthält die 1.495 Fälle aus der Nachklassifikation nur, soweit sie erneut unter der Schwelle blieben). Die Zählung je Unterart liefert `review-status <branch>` jederzeit aktuell.

## 2. Werkzeuge: was das Prüfcenter heute bietet und was fehlte

Oberfläche (Liste, Detail, Massenbearbeitung):

- Einzelfall: Bestätigen, Korrigieren, Zurückstellen, Verwerfen, Zuweisen, Dublette, Aufteilen, Wiedereröffnen, Übernahme in anderes Objekt, Import starten.
- Massenbearbeitung: nur für markierte Fälle einer Listenseite oder eine Gruppe (batch_key), höchstens 500 Fälle je Aktion, Vorschau mit Ampel, Ausführung als Hintergrundjob. Sammelfelder Zielbereich, Unterordner, Unterart, Jahr.
- Kein „alle gefilterten Fälle“, keine Auswahl je Objekt und Unterart, Verwerfen nur einzeln, keine Mindestkonfidenz.

Kommandos (Server, Aktionen in deploy_remote.sh):

- `ai-reclassify`: Fälle unter Schwelle ohne Stufe-3-Aufruf erneut klassifizieren (setzt den Fall auf verworfen mit decision reclassify, reiht CLASSIFY neu ein).
- `formate-wiederaufnehmen`, `paperless-archivfassung`: Fälle unsupported_format mit inzwischen bekanntem Format oder PDF-Archivfassung zurück in die Kette.
- `drive-dubletten`: Altkopien erneut abgelegter Dokumente in den Drive-Papierkorb.
- `review-status`: Zählung je Fallart und Unterart, Kandidaten, E-Mails.

Neu seit 26.09.2026: `faelle-sammelaktion` (Kommando `faelle_sammelaktion`) mit Auswahl je Objekt und Unterart, Vorschau ohne Wirkung und echter Ausführung über die vorhandenen Dienste des Prüfcenters (bulk_rows, bulk_execute, apply_decision, dismiss). Drei Aktionen:

| Aktion | Auswahl (Standard) | Wirkung |
|---|---|---|
| kandidat-bestaetigen | Unterart below_threshold (unclear und move_proposal) mit Kandidat in context.intended, Konfidenz mindestens `--min-konfidenz` (Standard 0,7) | wie „Vorschlag bestätigen“ in der Oberfläche: Klassifikation Stufe 4, Verknüpfungen, Fall erledigt, Ablagejob; Ziel 06 und Fälle ohne Kandidaten bleiben offen |
| verwerfen | Unterart ai_sample (andere per `--unterart`) | Fall verworfen mit decision reject, Grund, gemeinsamem bulk_key; Objektfälle bleiben der Einzelbearbeitung vorbehalten |
| manuelle-pruefung | Unterart manual_check | Ablage 06/02_Manuelle_Pruefung als menschliche Entscheidung mit Begründung, Fall erledigt, Ablagejob (Datei liegt meist schon dort) |

Die Vorschau zeigt Zähler je Objekt, Unterart und Zielkategorie sowie Gründe für Überspringen und Blockierung, keine Personendaten. Jeder echte Lauf schreibt ein Audit `review.bulk_command` mit bulk_key; die Entscheidungen bleiben je Fall über den bulk_key abfragbar und über Wiedereröffnen korrigierbar.

## 3. Reihenfolge

Grundsatz: erst die Mengen, die ohne fachliche Entscheidung verschwinden (Dubletten, Restformate), dann automatisch belegbare Entscheidungen ab Schwelle, dann die E-Mails nach der neuen Regel, zuletzt der Rest manuell. Jeder Schritt zuerst als Vorschau, dann je Objekt mit `limit` als Pilot, dann vollständig.

| Schritt | Menge (Bestand) | Werkzeug | Vorschau-Befehl | Freigabe GF | Erwartete Wirkung |
|---|---|---|---|---|---|
| 1a Drive-Altkopien | nicht gezählt (Protokoll drive.upload) | `drive_dubletten_bereinigen` | `drive-dubletten <branch>` | nein (nur Papierkorb, 30 Tage wiederherstellbar) | Altkopien im Papierkorb, keine Fälle betroffen; verhindert Doppelablage bei den folgenden Schritten |
| 1b Dubletten im Prüfcenter | in den Unterarten nicht ausgewiesen | Oberfläche: Aktion Dublette (Einzelfall) | Liste mit Filter Zielbereich 06/03 | nein | Fälle mit Dublettenbezug geschlossen, Ablage in 03_Dubletten |
| 1c Restformate | 2.007 unsupported_format | `formate_wiederaufnehmen` (E-Mails), `paperless_archivfassung` (Office-Altformate, HTML) | `formate-wiederaufnehmen <branch>` und `paperless-archivfassung <branch>` | ja, liegt als Entscheidungsvorlage vor (Restformate und Objekte 133/216) | Anteil der 2.007 mit inzwischen bekanntem Format geht zurück in die Kette; der Rest bleibt als Fall und wird in Schritt 5 verworfen oder manuell abgelegt |
| 2a Stufe 3 nachholen | 4.566 ohne Stufe-3-Aufruf (Teil der 6.686) | `ai_reclassify` | `ai-reclassify <branch>` (ohne echt) | ja (Kosten Stufe 3, Kostenlimit beachten) | Fälle werden zu Fällen mit Kandidat (below_threshold mit intended) oder zu automatischen Ablagen; Menge für Schritt 2b wächst |
| 2b Kandidaten bestätigen ab Schwelle | 1.495 below_threshold mit Kandidat, plus Ergebnis aus 2a | `faelle_sammelaktion --aktion kandidat-bestaetigen` | `faelle-sammelaktion <branch> aktion=kandidat-bestaetigen+min=0.7` | ja (Sammelentscheidung ohne Sichtung je Fall; Schwelle 0,7 als Vorgabe, GF legt die Schwelle fest) | Anteil der Fälle mit Kandidat ab Schwelle wird erledigt und abgelegt; Fälle unter Schwelle, ohne Kandidat, mit Ziel 06 oder mit mehreren Eigentümerkandidaten bleiben offen und erscheinen in der Vorschau als übersprungen oder blockiert |
| 3 E-Mails nach Regel 06 | 3.434 neue E-Mail-Fälle | Regel 06 Sonstiges für E-Mails (eigenes Paket), danach `ai_reclassify --unterfall manual_check --alle` bzw. Verarbeitung | `review-status <branch>` (E-Mail-Auswertung), dann `ai-reclassify <branch>` ohne echt | ja (Regeländerung und erneuter Stufe-3-Lauf) | E-Mails mit Regeltreffer werden ohne Fall abgelegt; der Rest bleibt manual_check oder below_threshold und läuft in Schritt 4 und 5 mit |
| 4a Stichproben schließen | 194 ai_sample | `faelle_sammelaktion --aktion verwerfen` | `faelle-sammelaktion <branch> aktion=verwerfen` | ja (Stichprobe ist Qualitätskontrolle; Schließen ohne Sichtung nur, wenn GF auf die Prüfung verzichtet) | 194 Hinweisfälle verworfen, Ablage unverändert (war bereits erfolgt) |
| 4b Manuelle Prüfung ablegen | 2.370 manual_check | `faelle_sammelaktion --aktion manuelle-pruefung` | `faelle-sammelaktion <branch> aktion=manuelle-pruefung` | ja (Fälle gelten danach als erledigt, Dokumente bleiben in 06/02 und sind über Wiedereröffnen erreichbar) | 2.370 Fälle erledigt, Dokumente bestätigt in 06/02; keine Sichtung je Fall, daher nur nach Schritt 3, damit E-Mails mit Regeltreffer nicht in 06/02 bleiben |
| 5 Rest manuell | 961 no_owner_no_unit, 351 tenant_unknown, Rest aus 1c und 2b | Oberfläche: Massenbearbeitung je Gruppe (batch_key) mit Kandidat oder Einheit, Einzelfall | Liste mit Filter Unterart, Gruppe öffnen | nein | je Gruppe eine Sammelentscheidung mit Vorschau; Fälle mit fehlenden Stammdaten (Einheit, Eigentümer, Mieter) erst nach Stammdatenpflege |

## 4. Ablauf je Schritt mit faelle-sammelaktion

1. Vorschau ohne Objekt: `faelle-sammelaktion <branch> aktion=<aktion>`; Zähler je Objekt, Unterart, Ziel, übersprungen, blockiert prüfen.
2. Pilot: ein Objekt mit Begrenzung, `faelle-sammelaktion <branch> aktion=<aktion>+objekt=NR+limit=50+benutzer=EMAIL+echt`; die Begrenzung zählt ausführbare Fälle, blockierte Zeilen verbrauchen sie nicht. Der Benutzer muss aktiv sein und review.decide haben. Ergebnis im Prüfcenter (Filter Objekt, Status erledigt) und in der Dokumentansicht (Ablagejob) sichten.
3. Vollständig je Objekt oder über alle Objekte mit `echt`. Der zweite Lauf mit denselben Parametern findet nichts mehr (Idempotenz); erledigte und verworfene Fälle werden nie erneut angefasst.
4. Nachlauf: Listenerzeugung und Vollständigkeitsprüfung laufen je Objekt und bulk_key einmal; Ablage über die Queue io, Drive wird im Kommando nicht aufgerufen.
5. Korrektur: Wiedereröffnen im Prüfcenter je Fall; Suche über den bulk_key, den das Kommando bei `echt` als erste Zeile ausgibt (am Ende auch im Audit review.bulk_command; nach einem Abbruch in den Audits review.bulk_execute je Block beziehungsweise review.dismiss je Fall).

## 5. Freigaben, die vor dem echten Lauf vorliegen müssen

- Schwelle für kandidat-bestaetigen (Vorgabe 0,7, entspricht der Konfidenz des Kandidaten aus Regel oder KI).
- Verzicht auf die Sichtung der 194 Stichproben (ai_sample) oder Sichtung im Prüfcenter vor dem Verwerfen.
- Erledigen der 2.370 manual_check-Fälle ohne Sichtung je Fall (Dokumente bleiben in 06/02).
- Benutzer, unter dem die Sammelentscheidungen laufen (Pflichtangabe `benutzer=EMAIL`, erscheint in Entscheidung und Audit).
