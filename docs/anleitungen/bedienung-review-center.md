# Bedienungsanleitung: Review Center

Stand: 10.09.2026 (M15). Gliederung nach Fachentwurf H Abschnitt 8, Kapitel 6 und 12. Das Review Center ist die Arbeitsliste aller Fälle, die eine Entscheidung eines Menschen brauchen. Jede Entscheidung wird mit Nutzer und Zeit protokolliert, in die Klassifikation als Stufe 4 (Mensch) geschrieben und dient dem Training des lokalen Modells. Drive-Verschiebungen laufen nach der Entscheidung als Hintergrundjob, nie in der Anfrage. Bildschirmfotos folgen nach dem Produktivstart ohne reale Daten.

## 1. Liste und Filter

Aufruf: Hauptnavigation, Review Center. Die Liste zeigt 50 Fälle je Seite mit Objekt, Fallart, Dokument, Vorschlag, Konfidenz, Kandidaten, Gruppe, Alter, Status und Bearbeiter. Blättern über die Schaltflächen am Listenende.

Filter oberhalb der Liste: Objekt (mit Zähler), Fallart (alle oder eine, mit Zähler), Unterfall, Bearbeiter, Zielbereich 01 bis 06, Konfidenz (hoch, mittel, niedrig), Jahr, Freitext „Dateiname oder Grund“, dazu Schalter für zurückgestellte und eigene Fälle. „Filtern“ wendet die Auswahl an.

Fallarten:

| Fallart | Bedeutung | Typische Entscheidung |
|---|---|---|
| unklar | Klassifikation unter der Schwelle oder widersprüchlich; Dokument liegt in 06/01_Unklar | Zielbereich und Pflichtfelder setzen, Speichern |
| Eigentümer mehrdeutig | mehrere Eigentümer kommen in Frage | Kandidat übernehmen |
| Pflichtmetadatum fehlt | Jahr, Einheit oder Eigentümer fehlt für die Ablage | Feld ergänzen |
| Verschiebevorschlag | Ablagevorschlag mit mittlerer Konfidenz, Stichprobe einer KI-Entscheidung oder Segment eines Gesamtdokuments | bestätigen oder korrigieren |
| Ordnerstruktur | Abweichung im Drive-Ordner (zusätzliche Ordner, abweichende Schreibweise, Verknüpfung) | bestätigen, danach Abgleich wiederholen |
| Objektnummer doppelt | zwei Objektordner mit derselben Nummer | richtigen Ordner festlegen |
| Importzeile unsicher | Zeile einer importierten Liste ohne sichere Zuordnung | Zeile entscheiden |
| Liste erkannt | ein verarbeitetes Dokument ist eine Eigentümer- oder Mieterliste | „Import starten“ |
| Stammdaten widersprüchlich | nächtlicher Konsistenzlauf hat einen Widerspruch gefunden | Stammdaten korrigieren |

Status: offen, in Bearbeitung, erledigt, verworfen.

## 2. Gespeicherte Sichten

1. Filter setzen und „Filtern“.
2. Namen in „Sicht speichern als“ eintragen, „Sicht speichern“. Die Sicht erscheint als Schaltfläche über der Liste; geteilte Sichten sehen alle Nutzer.
3. Sicht anklicken, um sie anzuwenden; „×“ löscht eine eigene Sicht.

Empfohlene Sichten für Serienarbeit: ein Objekt mit Fallart unklar; ein Objekt mit Zielbereich 05; eigene zurückgestellte Fälle.

## 3. Detailansicht

Drei Spalten:

- Links: Fall mit Fallart, Unterfall und Gründen, Klassifikationsverlauf (Stufe, Ergebnis, Konfidenz, Begründung), Entitäten (Einheiten, Beträge, Zeiträume, Personen), Kandidaten (Eigentümer, Einheit, Zeitraum) mit „übernehmen“, Verknüpfungen zu Akten, Protokoll des Falls.
- Mitte: Dokumentvorschau mit Miniaturen, Blättern, Seitenbereich des Falls hervorgehoben, Textauszug (maskiert).
- Rechts: Entscheidungsformular.

Ein Fall gilt für einen Seitenbereich. Gesamtdokumente mit eingebetteten Einzelabrechnungen erzeugen je Segment einen Fall; die Segmente stehen als Gruppe zusammen.

## 4. Zielbereich und Pflichtfelder

1. Zielbereich wählen (Tasten 1 bis 6 oder Auswahl). Je Zielbereich zeigt das Formular die Pflichtfelder.
2. Eigentümer (E): Suche ab zwei Zeichen, objektbezogene Treffer zuerst. Einheit (N): Auswahl aus den Einheiten des Objekts; die Zuordnung wird aus der Stichtagsabfrage vorbelegt. Ausdrücklich wählbar: Einheit unbekannt oder Eigentümer unbekannt (Ablage in den Sonderakten Unbekannte_WE beziehungsweise Unzugeordnet).
3. Neue Zuordnung anlegen (nur wenn keine passt): Eigentumsbeginn, bei Bedarf Eigentumsende. Die Anwendung warnt, wenn die Entscheidung der Stichtagsabfrage widerspricht; die Warnung wird mit der Entscheidung protokolliert.
4. Mieter für Mieterakten analog zur Eigentümersuche.
5. Unterordner und Unterart (T) je Zielbereich; Jahr (Y) beziehungsweise Zeitraum, vorbelegt aus den Entitäten.
6. Begründung: Pflicht bei Zielbereich 06, sonst optional.
7. „Speichern (B / Enter)“ oder „Speichern und nächster“ (Serienmodus).

Gültigkeit: Fehlt ein Pflichtfeld, bleibt der Fall offen und das Formular nennt das Feld. Dokumente werden nie ohne Pflichtmetadaten abgelegt.

## 5. Aktionen

| Aktion | Taste | Wirkung |
|---|---|---|
| Speichern (Bestätigen oder Umklassifizieren) | B, Enter | schreibt Klassifikation Stufe 4, Verknüpfungen, Trainingsdatum, Audit; startet den Ablagejob |
| Speichern und nächster | Umschalt + Enter | wie Speichern, öffnet den nächsten Fall der aktuellen Sicht |
| Zurückstellen | Z | Wiedervorlage nach der konfigurierten Anzahl Tage (Vorgabe 7), Grund optional; der Fall verschwindet bis dahin aus der Standardliste |
| Verwerfen | V | Fall ohne Ablage schließen, Grund ist Pflicht; Objektfälle (Ordnerstruktur, Objektnummer doppelt) darf nur der Admin verwerfen |
| Zuweisen | A | Fall an einen Bearbeiter oder an sich selbst |
| Dublette | D | Dokument als Dublette eines gewählten Originals kennzeichnen; Ablage in 03_Dubletten |
| Aufteilen | S | Seitenbereiche mit Einheit angeben, zum Beispiel `1-4:WE03, 5-8:WE05`; je Bereich entsteht ein Fall; Überschneidungen werden abgelehnt |
| Wiedereröffnen | W | erledigten oder verworfenen Fall wieder öffnen; die Korrektur ist eine neue Entscheidung mit neuem Ablagejob |
| In anderes Objekt übernehmen | | Dokument mit Grund einem anderen Objekt zuordnen; die Verarbeitung dort läuft neu |
| Import starten | I | nur bei Fallart Liste erkannt: legt den Import mit dem erkannten Profil an und schließt den Fall mit Verweis |

Rückgängig: Eine gespeicherte Entscheidung wird nicht gelöscht, sondern über Wiedereröffnen und eine neue Entscheidung korrigiert. Beide Entscheidungen bleiben im Protokoll des Falls und im Audit sichtbar.

## 6. Massenbearbeitung mit Vorschau

Für gleichartige Fälle (gleiches Objekt, gleiche Herkunft, Kategorie, Unterordner, Unterart, Jahr) und für frei markierte Fälle.

1. In der Liste Fälle markieren (Kontrollkästchen oder Leertaste) und „Markierte gemeinsam bearbeiten“, oder eine Gruppe über die Spalte Gruppe öffnen.
2. Sammelfelder setzen (gelten für alle Zeilen): Zielbereich, Unterordner, Unterart, Jahr; „unverändert“ lässt den Vorschlag der Zeile stehen. „Vorschau aktualisieren“.
3. Die Vorschau zeigt je Zeile Dokument, Seiten, Eigentümer, Einheit, Zielakte und Pfad, Unterordner, Unterart, Jahr und Hinweis. Grün: eindeutig. Gelb: Kandidat oder Einheit wählen („Kandidat wählen“, „Einheit wählen“). Rot: Konflikt, die Zeile wird nicht ausgeführt und bleibt offen. „Ausschließen“ nimmt eine Zeile heraus.
4. „Ausführen: N Entscheidungen in einer Aktion“. Die Ausführung läuft als Hintergrundjob; die Ergebnisseite zeigt den Fortschritt und je Fall Dokument, Seiten, Art und Ziel.
5. Listen und Vollständigkeit werden je Sammelaktion einmal neu bewertet.

Grenzen: höchstens 500 Fälle je Sammelaktion (Konfiguration review.bulk_max_cases). Die Vorschau schreibt nichts; erst „Ausführen“ ändert Daten.

## 7. Serienmodus und Tastatur

Serienmodus: Sicht wählen, ersten Fall öffnen, entscheiden mit Umschalt + Enter („Speichern und nächster“). Die Anwendung öffnet den nächsten offenen Fall derselben Sicht. Esc führt zur Liste zurück. Die Taste ? zeigt die Belegung.

Alle Aktionen sind auch per Schaltfläche erreichbar; die Tasten wirken nicht, während ein Eingabefeld den Fokus hat (außer Enter).

## 8. Protokoll eines Falls

Der Abschnitt Protokoll in der Detailansicht zeigt jede Aktion mit Nutzer, Zeit, Vorher und Nachher, ob das System richtig lag (system_was_correct) und die Warnungen der Stichtagsabfrage. Das vollständige Audit steht unter Protokoll in der Hauptnavigation (eigene Einträge für Sachbearbeiter, alle für Admin), exportierbar als CSV.

## 9. Wenn etwas nicht klappt

- Keine Fälle sichtbar, obwohl Dokumente in 06/01_Unklar liegen: Filter prüfen (Zielbereich, Bearbeiter, „eigene“), zurückgestellte Fälle einblenden.
- Speichern meldet ein fehlendes Pflichtfeld: Zielbereich prüfen; für 05 sind Eigentümer oder Einheit erforderlich (oder ausdrücklich „unbekannt“), für Dokumente mit Zeitbezug das Jahr.
- Ablage nach dem Speichern noch nicht in Drive: der Ablagejob läuft in der Queue io; Status in der Dokumentansicht (Jobs). Bei Google-Störung wartet der Job und läuft nach der Behebung weiter.
- Verwerfen nicht möglich: Objektfälle darf nur der Admin verwerfen; Grund ist Pflicht.
- Sammelaktion bleibt bei „läuft“: Ergebnisseite neu laden; bleibt sie stehen, Admin prüft den Worker (Runbook „Sammelaktion hängt“). Ein erneuter Start überspringt bereits erledigte Fälle.
- Vorschaubild fehlt: Vorschaubilder entstehen nach der OCR im Hintergrund; bei laufender Verarbeitung kurz warten. Ohne Bild ist der Textauszug verfügbar.

## 10. Anhang: Tastenübersicht

In der Liste:

| Taste | Wirkung |
|---|---|
| J oder Pfeil ab, K oder Pfeil auf | Zeile wechseln |
| Leertaste | Zeile markieren |
| Enter | markierten Fall öffnen |
| Umschalt + Enter | markierte Fälle gemeinsam bearbeiten |
| 1 bis 6 | Filter Zielbereich |

In der Detailansicht:

| Taste | Wirkung |
|---|---|
| J, K | nächster, vorheriger Fall |
| 1 bis 6 | Zielbereich |
| E | Eigentümersuche |
| N | Einheit |
| Y | Jahr |
| T | Unterart |
| B, Enter | Speichern |
| Umschalt + Enter | Speichern und nächster |
| Z | Zurückstellen |
| V | Verwerfen |
| A | Zuweisen |
| D | Dublette |
| S | Aufteilen |
| W | Wiedereröffnen |
| I | Import starten (nur Liste erkannt) |
| Leertaste, Umschalt + Leertaste | nächste, vorherige Seite der Vorschau |
| Esc | zurück zur Liste |
| ? | Belegung anzeigen |

## 11. Anhang: Begriffe

| Begriff | Bedeutung |
|---|---|
| Fallart | Grund, warum ein Fall entstanden ist (Tabelle in Abschnitt 1) |
| Unterfall | genauere Ursache innerhalb der Fallart, zum Beispiel unter Schwelle, Stichprobe, Segment |
| Zuordnung | Eigentümer zu Einheit mit Zeitraum; Grundlage der Eigentümerakte |
| Akte | Zielordner in 05_Eigentümerakte (je Eigentümergruppe) oder in den Mieterakten |
| Sonderakten | Unbekannte_WE und Unzugeordnet für Dokumente ohne sichere Einheit oder ohne sicheren Eigentümer |
| Unterart | Dokumentart innerhalb einer Kategorie, bestimmt Pflichtfelder wie Jahr oder Eigentümer |
| Zeitraum | Abrechnungs- oder Planjahr; bei Eigentümerwechsel entscheidet der Zeitraum über die Akte des Alt- oder Neueigentümers |
| Konfidenz | Sicherheit der Klassifikation aus Regeln, lokalem Modell und gegebenenfalls Stufe 3 |
| Stufe 4 | Entscheidung eines Menschen im Review Center |
| Gruppe | Fälle mit gleichem Schlüssel für die Massenbearbeitung |

Ansprechpartner: der Admin der Anwendung. Störungen und Maßnahmen stehen in docs/betrieb/runbook.md.
