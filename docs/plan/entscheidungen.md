# Entscheidungen des Auftraggebers

Jede Antwort des Auftraggebers wird hier mit Datum festgehalten und ist danach für den Bau bindend (Umsetzungsplan, Abschnitt 7). Nummern beziehen sich auf docs/umsetzungsplan.md.

| Datum | Nummer | Entscheidung | Wirkung |
|---|---|---|---|
| 10.09.2026 | FG-1, FG-2, FG-3 | Freigabe erteilt („weiter und auch freigabe"): Stack A mit den Auflagen aus Anhang C, Datenmodell D als Bezeichnerquelle mit Ergänzungsmigration, Meilensteinreihenfolge mit den Planänderungen | Umsetzung beginnt mit M1; M0 folgt, sobald der SSH-Zugang zum VPS besteht (V-01, V-03) |
| 10.09.2026 | FG-4, F1 bis F31 | Keine Einzelantworten. Es gelten die im Umsetzungsplan Abschnitt 4 genannten Vorschlagswerte („Ohne Antwort"), bis eine abweichende Antwort mit Nummer eingeht | Seeds, Konfiguration und Compose werden mit den Vorschlagswerten gebaut; spätere Antworten werden per Konfiguration umgesetzt, soweit im Plan so vorgesehen |
| 10.09.2026 | F1 | Umsetzung durch den beauftragten KI-Assistenten in Python und Django; Vertretung nicht benannt | Risiko R-18 bleibt offen |
| 10.09.2026 | FG-5, V-01 bis V-09 | Noch nicht erfüllt: SSH-Zugang, Messcontainer-Freigabe, DNS, technisches Konto, OAuth-App, Test-Wurzelverzeichnis | M0 und M4 verschieben sich, bis die Voraussetzungen vorliegen; alle Serverwerte bleiben Variablen |

## Offene Antworten mit Wirkung auf den Bau

Die folgenden Fragen werden mit dem Vorschlagswert gebaut und lassen sich später umstellen. Sie bleiben zur Beantwortung offen:

- F4 Objektnummer ohne Nullauffüllung, Erkennung 2 bis 6 Stellen
- F5 Ordnernamen wortgetreu mit Umlaut
- F6 Präfix der Eigentümerakten immer WE (CR-Wortlaut), Umstellung auf typabhängig per Konfiguration
- F8 Ablage bei mehrdeutiger Zuordnung nach Entscheidungstabelle docs/architektur.md 6.5
- F10 Requirement Engine und Nachforderung als minimaler Neubau, Unterstrukturen 01 bis 04 leer
- F13 OAuth-App mit den Redirect-URIs aus docs/betrieb.md 7.1 (anzulegen durch den Auftraggeber)
- F16 IBAN nur als letzte vier Stellen und Hash
- F17 Externe KI bis zur AVV-Freigabe deaktiviert
- F18 Entscheidung nach dem OCR-Probelauf in M0

## Ergänzungen aus der Umsetzung (Vorschläge des Umsetzers, gelten bis zu einer abweichenden Entscheidung)

| Datum | Meilenstein | Ergänzung | Begründung und Wirkung |
|---|---|---|---|
| 10.09.2026 | M2 | `review_cases.case_type` um `data_consistency` (Untertyp `assignment_overlap`) | Der nächtliche Konsistenzlauf meldet überlappende Zuordnungen derselben Person auf derselben Einheit; nach B-09 wäre `unclear` falsch, weil ein fachlicher Grund vorliegt. Rückbau: Wert aus CHECK und Task entfernen. |
| 10.09.2026 | M2 | Audit-Aktion `assignment.end` | Beendigung einer Zuordnung (Eigentümerwechsel) wird getrennt vom Anlegen protokolliert. |
| 10.09.2026 | M2 | Codes der 57 Dokumentunterarten (`db/seeds/document_types.json`) | CR 5 und 6 nennen Namen, keine Codes; die Codes sind ab jetzt stabil, weil Regeln, Klassifikator und KI-Schema darauf verweisen (B-12, B-21). Namen folgen dem CR-Wortlaut. Zusätzlich `sonstiges_eigentuemer` als Sammelunterart der Eigentümerakte. |
| 10.09.2026 | M2 | Vergleichsschlüssel der Einheiten ohne führende Nullen (`units.normalize_strip_leading_zeros = true`) | Vorschlag H 6.5; WE01 und WE 1 gelten als dieselbe Einheit. Abschaltbar per Konfiguration, falls eine Vorverwaltung beide Schreibweisen als verschiedene Einheiten führt. |
| 10.09.2026 | M2 | Konsistenzlauf täglich 03:15 Serverzeit (ANNAHME) | Zeitfenster außerhalb der Verarbeitung; endgültige Uhrzeit mit Antwort F27. |
| 10.09.2026 | M2 | Technische Abweichungen von der DDL in D (Typen, Primärschlüssel, CHECK mit AUTO_INCREMENT, FULLTEXT als eigene Migration) | Begründung je Punkt in docs/architektur/datenmodell.md Abschnitt 3; fachlich ohne Wirkung. |
