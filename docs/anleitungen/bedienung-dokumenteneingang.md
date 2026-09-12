# Bedienungsanleitung: Dokumenteneingang

Stand: 12.09.2026 (Synchronisation Paperless-ngx und Google Drive). Der Dokumenteneingang ist die Arbeitsliste für Dokumente, die ohne Objektbezug eingegangen sind (aus Paperless-ngx ohne Feld Objekt, aus dem Drive-Eingangsordner), und für Abgleichskonflikte zwischen Anwendung, Paperless und Drive. Jede Entscheidung wird mit Nutzer und Zeit protokolliert; Zuordnungen fließen als Lernbeispiel in künftige Vorschläge ein. Drive-Verschiebungen und Schreibzugriffe nach Paperless laufen als Hintergrundoperation, nie in der Anfrage. Bildschirmfotos folgen nach dem Pilotbetrieb ohne reale Daten. Voraussetzung: Recht inbox.work (Admin, Sachbearbeiter); die Entscheidungen selbst erfolgen in der Detailansicht des Review Centers mit Recht review.decide. Einrichtung und Störungen: docs/betrieb/paperless-sync.md; Regeln im Hintergrund: docs/architektur.md, Abschnitt 14.

## 1. Aufruf und Aufbau der Seite

Aufruf: Hauptnavigation, Eingang (Adresse /eingang/). Die Kopfzeile zeigt vier Zähler: Zuordnungen offen, Konflikte, in Verarbeitung, Synchronisationsfehler. Ist die Paperless-Anbindung nicht aktiv (Hauptschalter aus oder nicht konfiguriert), steht ein Hinweis; die Seite zeigt dann nur, was bereits erfasst ist.

Filter oberhalb der Liste: Art (alle Arten; ungeklärte Zuordnung und Vorschläge; Konflikte; Dublettenverdacht; Formatprobleme; Synchronisationsfehler) und Objekt (alle Objekte oder eine Objektnummer). „Filtern“ wendet die Auswahl an. Adressparameter: art=zuordnung, konflikt, dublette, format oder fehler und objekt=Nummer.

Vier Bereiche:

| Bereich | Inhalt | Spalten |
|---|---|---|
| Fälle | offene Fälle der Fallarten Objektzuordnung und Abgleichskonflikt, dazu Dublettenverdacht und Formatprobleme aus dem Eingangsobjekt oder mit Quelle Paperless; sortiert nach Priorität, dann Alter | Objekt (Eingang oder Objektnummer), Fallart mit Unterfall, Dokument, Vorschlag (Objekt-ID des Vorschlags und Gründe), Kandidaten (Anzahl), seit, Schaltfläche Bearbeiten |
| In Verarbeitung (Eingangsobjekt) | Dokumente, die noch im Eingangsobjekt liegen (neueste zuerst, bis 200) | Dokument, Quelle, Status und die Kennzeichen aus Abschnitt 2 |
| Synchronisationsfehler | Operationen im Zustand fehlgeschlagen oder blockiert (ohne Filter oder mit Art Synchronisationsfehler) | Nr., Art, Dokument, Status, Fehler; Schaltfläche erneut nur mit Recht sync.manage |
| Automatisch zugeordnet (letzte 30 Tage) | Zuordnungen mit eindeutiger Evidenz, die ohne Rückfrage in ein Objekt übernommen wurden | Objekt, Dokument, Bewertung, Gründe, wann |

Bearbeiten öffnet den Fall in der Detailansicht des Review Centers (Adresse /review/Nummer/) mit Dokumentvorschau, Textauszug und Entscheidungsbereich (docs/anleitungen/bedienung-review-center.md, Abschnitt 3).

## 2. Statuskennzeichen je Dokument

Die Tabelle In Verarbeitung zeigt je Dokument getrennte Kennzeichen (berechnet in document_sync_status der Anwendung):

| Spalte | Kennzeichen | Bedeutung | Berechnung |
|---|---|---|---|
| eingegangen | eingegangen | Dokument ist registriert | immer ja, sobald die Zeile erscheint |
| in Paperless | in Paperless vorhanden | Verknüpfung zu Paperless besteht; dahinter der Zustandscode der Verknüpfung: pending (vorgemerkt), linked (verknüpft), synced (abgeglichen), changed_remote (extern geändert), conflict (Konflikt), missing (extern nicht gefunden), trashed (im Papierkorb), tombstone (Löschung bestätigt) | Verknüpfung mit System Paperless vorhanden |
| Text | Texterkennung abgeschlossen | Seitentext liegt vor; Klassifikation und Zuordnung können arbeiten | Dokumentstatus Text erkannt, klassifiziert, in Prüfung oder abgelegt |
| Objekt | Objekt zugeordnet | Dokument gehört einem Verwaltungsobjekt | Objekt ist nicht das Eingangsobjekt und Status nicht „in anderes Objekt übernommen“; in dieser Tabelle daher immer offen. Nach der Zuordnung verschwindet die Zeile, weil das Dokument als neue Zeile im Zielobjekt weiterläuft |
| in Drive | in Drive vorhanden | Datei liegt in Drive (Eingangsordner oder Objektstruktur) | Drive-Datei-ID gesetzt; Dokumente aus Paperless erhalten sie durch die Spiegelung in den Eingangsordner |
| Abgleich | Abgleich vollständig | Zielzustand erreicht | Objekt zugeordnet, Datei in Drive, Paperless-Verknüpfung abgeglichen und, falls vorhanden, Drive-Verknüpfung abgeglichen; für Dokumente im Eingang daher immer offen |

Ein Dokument bleibt in dieser Tabelle, bis es zugeordnet ist. Ein Dokument ohne Kennzeichen Text wartet noch auf die Verarbeitung (Hash, Texterkennung); der Zuordnungsvorschlag entsteht erst danach.

## 3. Zuordnungsvorschlag prüfen

Fallart Objektzuordnung mit den Unterfällen:

| Unterfall | Bedeutung |
|---|---|
| proposal | Kandidaten gefunden, aber keine automatische Zuordnung (Bewertung unter der Schwelle, Abstand zum zweiten Kandidaten zu klein oder Widerspruch) |
| no_candidate | kein Objekt mit positiver Bewertung; Zuordnung von Hand |
| from_paperless | in Paperless wurde das Feld Objekt für ein Dokument im Eingang gesetzt; Vorschlag mit hoher Bewertung, wird trotzdem von einer Person bestätigt |
| auto | bereits erledigt: automatische Zuordnung mit eindeutiger Evidenz (nur in der Tabelle Automatisch zugeordnet) |

Die Detailansicht zeigt die Gründe der Entscheidung (bester und zweiter Kandidat mit Bewertung und Abstand, Hinweise, Widersprüche) und die Kandidatentabelle:

- Objekt: Objektnummer des Kandidaten.
- Bewertung: Zahl zwischen 0 und 1. Sie verbindet alle Belege für das Objekt und zieht Gegenbelege ab. Automatisch zugeordnet wird nur ab sync.assignment_auto_min (Vorgabe 0,85) und mit Abstand von mindestens sync.assignment_gap_min (Vorgabe 0,25) zum zweiten Kandidaten.
- Belege: bis zu sechs Fundstellen je Kandidat mit Art, Rolle, Textausschnitt und Seite. Arten: address (Objektanschrift im Text; Rolle object für Leistungsort oder Objektadresse, billing für Rechnungsanschrift, supplier für Absender, owner für Eigentümer- oder Mieteranschrift, neutral ohne Rollenwort), object_number (Objektnummer mit Kontextwort), filename_number und filename_address (Nummer oder Anschrift im Dateinamen oder im Paperless-Titel), folder (Drive-Objektordner), rule (bestätigte Zuordnungsregel), management_type (Wörter der Verwaltungsart), party_name (Name einer bekannten Partei).
- Widersprüche: Gründe, warum die Automatik nicht zugeordnet hat, zum Beispiel eine weitere Leistungsadresse für ein anderes Objekt oder ein Objektnummernbezug, der der Leistungsadresse widerspricht. Hinweise wie „gleiche Anschrift, Verwaltungsart aus dem Text nicht ableitbar“ zeigen an, dass zwei Objekte dieselbe Anschrift haben (WEG und Sondereigentumsverwaltung); dann entscheidet der Inhalt (Hausgeld und Eigentümerversammlung gegen Mietvertrag und Kaltmiete).

Prüfreihenfolge:

1. Vorschau und Textauszug lesen: Welche Anschrift ist Leistungsort, welche nur Rechnungsempfänger oder Absender?
2. Belege des besten Kandidaten gegen die Vorschau prüfen (Seitenangabe nutzen).
3. Widersprüche auflösen: Trifft die Leistungsadresse ein anderes Objekt als die Objektnummer oder der Ordner, gilt der Inhalt des Dokuments.
4. Bei gleicher Anschrift die Verwaltungsart aus dem Inhalt bestimmen.

## 4. Zuordnen, verwerfen, anderes Objekt wählen

| Aktion | Schaltfläche | Wirkung |
|---|---|---|
| Kandidat übernehmen | Zuordnen in der Zeile des Kandidaten | Das Dokument wird in das Zielobjekt übernommen: neue Dokumentzeile dort, die Zeile im Eingang erhält den Status „in anderes Objekt übernommen“, die Drive-Datei wird aus dem Eingangsordner in die Objektstruktur verschoben (nie kopiert), im Zielobjekt laufen Entitätenerkennung, Klassifikation, Entscheidung und Ablage; Paperless erhält den neuen Objektbezug in den Feldern. Lernbeispiel bestätigt (Kandidat entsprach dem Vorschlag) oder korrigiert (anderer Kandidat). Protokoll inbox.assign. Der Fall ist erledigt. |
| Anderes Objekt wählen | Auswahl Anderes Objekt (Nummer, Bezeichnung, Verwaltungsart), Feld Grund, Zuordnen | wie oben mit dem gewählten Objekt; für Dokumente ohne passenden Kandidaten. Grund empfohlen. Lernbeispiel korrigiert. |
| Vorschlag verwerfen | Vorschlag verwerfen (bleibt im Eingang), Feld Grund | Der Fall wird verworfen, das Dokument bleibt im Eingang und in der Tabelle In Verarbeitung; Lernbeispiel abgelehnt für das vorgeschlagene Objekt; Protokoll inbox.reject. Verwenden, wenn das Dokument keinem Objekt gehört oder erst geklärt werden muss. Ein neuer Vorschlag entsteht nicht von selbst (Ausnahme: wird in Paperless später das Feld Objekt gesetzt, entsteht ein Vorschlag from_paperless); die spätere Zuordnung erfolgt über Wiedereröffnen des Falls (Taste W) und Zuordnen. |

Rückgängig: Eine Zuordnung wird nicht gelöscht, sondern korrigiert. Dazu den erledigten Fall Objektzuordnung im Review Center öffnen (Filter Fallart Objektzuordnung, Status erledigt), Wiedereröffnen (Taste W) und über Anderes Objekt wählen neu zuordnen; die Korrektur ist ein neues Lernbeispiel (korrigiert) und steht im Protokoll. Die allgemeine Aktion „In anderes Objekt übernehmen“ in anderen Fallarten verschiebt das Dokument ebenfalls, speichert aber kein Lernbeispiel.

## 5. Konfliktfälle entscheiden

Fallart Abgleichskonflikt (Filter Konflikte). Die Detailansicht zeigt, mit welchem System der Konflikt besteht (Paperless oder Google Drive), die technischen Angaben des Falls (Paperless-ID, Prüfsummen, Drive-Datei-ID, Zeitpunkt) und die Verknüpfungen mit Zustand. Zur Auswahl stehen nur die für den Unterfall zulässigen Entscheidungen; Begründung eintragen und Entscheiden.

| Unterfall | Was geschehen ist | Entscheidungen | Empfehlung |
|---|---|---|---|
| content_changed_remote | In Paperless liegt eine andere Datei als bekannt | lokalen Stand behalten | Das Original in Drive bleibt maßgeblich; die Änderung in Paperless wird als bekannt vermerkt. Soll die neue Fassung in die Akte, Datei aus Paperless herunterladen und als Upload einreichen. |
| duplicate_remote | In Paperless liegt eine zweite Kopie (gleiche Dokument-UUID oder Prüfsumme) zu einem Dokument, das bereits mit einer anderen Paperless-Kennung verknüpft ist | lokalen Stand behalten | Die Verknüpfung bleibt auf der bekannten Kopie, die zweite Kopie wird nicht übernommen. Soll sie verschwinden, in Paperless bereinigen. |
| object_changed_remote | Das Feld Objekt in Paperless nennt ein anderes Objekt | Objektzuordnung aus Paperless übernehmen; eigene Zuordnung nach Paperless zurückschreiben | Inhalt prüfen; der Leistungsort im Dokument entscheidet |
| deleted_remote | Dokument in Paperless nicht mehr vorhanden | lokalen Stand behalten; Löschung bestätigen; Datei erneut nach Paperless übertragen | War die Löschung gewollt: Löschung bestätigen (keine erneute Übernahme). War sie ein Versehen: erneut übertragen. |
| trashed_remote | Dokument in Paperless im Papierkorb | lokalen Stand behalten; Löschung bestätigen | wie deleted_remote; die Wiederherstellung erfolgt in Paperless |
| checksum_mismatch | Nach dem Upload meldet Paperless eine andere Prüfsumme | lokalen Stand behalten; erneut übertragen | in der Regel erneut übertragen |
| drive_trashed | Drive-Datei im Papierkorb | lokalen Stand behalten; Löschung bestätigen | Datei in Drive wiederherstellen, dann lokalen Stand behalten; sonst Löschung bestätigen |
| drive_removed | Drive-Datei entfernt oder kein Zugriff | lokalen Stand behalten; Löschung bestätigen | Zugriff und Ablageort in Drive prüfen; bei verschobenen Ordnern Bestandslauf durch den Admin |
| drive_content_changed | Drive-Datei wurde inhaltlich ersetzt | lokalen Stand behalten | Änderung als bekannt vermerken; die Anwendung überschreibt nichts |

Löschung bestätigen löscht nichts: weder das Dokument der Anwendung noch die Drive-Datei noch etwas in Paperless. Es wird nur vermerkt, dass diese externe Kennung nicht erneut übernommen wird (Protokoll sync.tombstone). Jede Entscheidung steht im Protokoll sync.conflict_resolved mit Person, Zeit und Begründung.

## 6. Auswirkung auf die Vollständigkeitsprüfung

- Solange ein Dokument im Eingang liegt, gehört es keinem Verwaltungsobjekt. Es zählt in keiner Vollständigkeitsprüfung, in keiner Eigentümer- oder Mieterliste und in keinem Objektbericht. Das Eingangsobjekt hat keine Vollständigkeitsbewertung.
- Mit der Zuordnung entsteht eine neue Dokumentzeile im Zielobjekt, die dort vollständig verarbeitet wird (Entitäten, Klassifikation, Entscheidung, Ablage). Erst dieses Ergebnis kann eine Prüfposition erfüllen, zum Beispiel eine Einzelabrechnung für Einheit und Jahr. Die Neubewertung läuft am Ende des Verarbeitungslaufs des Zielobjekts; ein manueller Anstoß ist auf der Vollständigkeitsseite des Objekts möglich.
- Ergibt die Verarbeitung im Zielobjekt einen Fall (unklar, Eigentümer mehrdeutig, Pflichtmetadatum fehlt), erscheint er im Review Center des Zielobjekts; bis zur Entscheidung gilt die Prüfposition als nicht erfüllt.
- Eine Korrektur der Zuordnung nimmt das Dokument aus dem alten Objekt heraus; dessen Vollständigkeit wird beim nächsten Lauf neu bewertet.

## 7. Was automatisch passiert

- Neue Dokumente in Paperless werden nach Webhook oder mit dem regelmäßigen Abgleich (paperless.poll_interval_minutes, Vorgabe 5 Minuten) übernommen: mit gesetztem Feld Objekt direkt in das Objekt, sonst in den Eingang. Identische Dateien (Prüfsumme) oder Dokumente mit bekannter Dokument-UUID werden nur verknüpft, nicht erneut angelegt.
- Neue Dateien im Drive-Eingangsordner oder in Objektordnern werden registriert, wenn das Drive-Änderungsprotokoll eingeschaltet ist (sync.drive_changes_enabled).
- Dokumente im Eingang durchlaufen Hash, Texterkennung, Entitäten und Klassifikation; Dateien ohne Drive-Kopie werden in den Eingangsordner gespiegelt.
- Zuordnung ohne Rückfrage nur bei eindeutiger Evidenz: Bewertung ab 0,85, Abstand ab 0,25 zum zweiten Kandidaten, kein Widerspruch. Jede automatische Zuordnung steht 30 Tage in der Tabelle Automatisch zugeordnet und dauerhaft im Protokoll (inbox.assign_auto).
- Nach Zuordnung und Ablage: Drive-Kennzeichen an der Datei, Felder und Tag in Paperless (Dokument-UUID, Objekt, Zuordnung, Drive-Link).
- Fehlgeschlagene Operationen werden mit steigender Wartezeit wiederholt (bis sync.operation_max_attempts, Vorgabe 5) und danach in der Fehlerliste gezeigt.
- Nachts entstehen Zuordnungsregeln aus mindestens zwei bestätigten Beispielen mit gleicher Merkmalskombination (Lieferant und Vertrags-, Kunden-, Zähler-, Versicherungs- oder Liegenschaftsnummer). Voraussetzung ist ein bekannter Lieferant (Korrespondent aus Paperless); ohne ihn entsteht keine Regel.

## 8. Was nie automatisch passiert

- Löschen: weder lokal noch in Drive noch in Paperless. Löschungen und Papierkorb werden nur vermerkt und vorgelegt.
- Überschreiben eines Originals in Drive durch eine Datei aus Paperless.
- Auflösung eines Konflikts nach dem neuesten Zeitstempel.
- Zuordnung bei Widerspruch, bei zu geringem Abstand zwischen zwei Kandidaten oder unter der Schwelle; ebenso keine Zuordnung allein aus dem Feld Objekt in Paperless für ein Dokument im Eingang (Vorschlag from_paperless).
- Anlage oder Änderung von Stammdaten (Eigentümer, Einheiten, Mieter) aus dem Eingang; Listen werden weiterhin nur über den Import mit Bestätigung übernommen.
- Umsortieren bereits bestätigter Dokumente durch eine neue Regel.
- Schreiben nach Paperless außerhalb des Pilotumfangs (Modus pilot) oder bei ausgeschaltetem Hauptschalter.

## 9. Typische Fragen

- Ein Dokument steht seit Stunden in Verarbeitung ohne Kennzeichen Text: Die Pipeline wartet (Verarbeitungslauf, Google-Verbindung oder Texterkennung). Status in der Dokumentansicht (Jobs) prüfen; Störungen behebt der Admin (docs/betrieb/runbook.md).
- Kein Kandidat, obwohl die Anschrift im Text steht: Die Anschrift muss der Objektanschrift entsprechen (Straße und Hausnummer; 12a und 12b sind verschieden). Prüfen, ob das Objekt Straße, Hausnummer, Postleitzahl und Ort trägt; sonst Stammdaten ergänzen oder das Objekt von Hand wählen.
- Zwei Objekte mit derselben Anschrift: Hinweis „gleiche Anschrift“; Verwaltungsart aus dem Inhalt bestimmen (WEG-Begriffe gegen Mietbegriffe) und von Hand zuordnen.
- Vorschlag aus Paperless (from_paperless) stimmt: Zuordnen bestätigt ihn. Die Bestätigung ist erforderlich, weil das Feld in Paperless von Hand gesetzt wurde.
- Nach dem Zuordnen liegt die Datei noch im Eingangsordner in Drive: Die Verschiebung läuft als Ablagejob im Zielobjekt; bei Google-Störung wartet der Job und läuft danach weiter.
- Synchronisationsfehler blockiert: Die Ursache steht in der Spalte Fehler (Schreiben für dieses Objekt nicht erlaubt, Zugriff verweigert, in Paperless nicht gefunden). Der Admin behebt die Ursache und stößt die Operation mit erneut an.
- Automatische Zuordnung war falsch: Den erledigten Fall Objektzuordnung (Unterfall auto) im Review Center öffnen (Filter Fallart Objektzuordnung, Status erledigt), Wiedereröffnen und das richtige Objekt über Anderes Objekt wählen zuordnen; die Korrektur wird als Lernbeispiel gespeichert und verhindert eine Regel für diese Kombination.
- Dublettenverdacht oder Formatproblem im Eingang: Fälle der Fallart unklar mit Unterfall duplicate, unsupported_format oder file_too_large; Bedienung wie im Review Center (Dublette kennzeichnen, Zielbereich setzen).
- Paperless zeigt ein Dokument mit dem Tag der Anwendung, aber ohne Feld Objekt: Das Dokument liegt im Eingang oder ist noch nicht abgelegt; die Felder werden nach Zuordnung und Ablage geschrieben.

## 10. Anhang: Begriffe

| Begriff | Bedeutung |
|---|---|
| Eingangsobjekt | technisches Objekt (Nummer sync.inbox_object_number) für Dokumente ohne Objektbezug; erscheint in keiner Objektliste |
| Eingangsordner | geschützter Ordner in Drive unter der Wurzel (sync.inbox_folder_name), in dem Dateien des Eingangs liegen, bis sie zugeordnet sind |
| Kandidat | Objekt mit mindestens einem Beleg und positiver Bewertung |
| Beleg | Fundstelle eines Signals mit Art, Rolle, Seite und Gewicht |
| Widerspruch | Beleg gegen den besten Kandidaten, der die automatische Zuordnung verhindert |
| Bewertung | Zahl 0 bis 1 aus allen Belegen eines Kandidaten |
| Verknüpfung | Zuordnung eines Dokuments zu seiner Kopie in Paperless oder Drive mit Zustand |
| Tombstone | bestätigte Löschung; die externe Kennung wird nicht erneut übernommen |
| Exportfassung | PDF-Export eines Google-Dokuments in Paperless; das Original bleibt in Drive |
| Indexbeleg | einseitige PDF in Paperless für Dateien, die nicht übertragen werden (Format oder Größe) |
| Lernbeispiel | gespeicherte Entscheidung (bestätigt, korrigiert, verworfen) mit Merkmalen; Grundlage der Regeln |
| Regel | bestätigte Merkmalskombination (Lieferant und Nummer), die als starker Beleg für ein Objekt wirkt |

Ansprechpartner: der Admin der Anwendung. Einrichtung und Störungen stehen in docs/betrieb/paperless-sync.md und docs/betrieb/runbook.md.
