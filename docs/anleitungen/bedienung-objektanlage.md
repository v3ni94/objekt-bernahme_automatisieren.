# Bedienungsanleitung: Anmeldung, Objektanlage, Ordnerabgleich, Import, Verarbeitung, Listen, Vollständigkeit, Nachforderung, Suche

Stand: 11.09.2026 (M15, ergänzt um Ordneranlage, Archiv, Wiederaufnahme). Gliederung nach Fachentwurf H Abschnitt 8. Jedes Kapitel enthält den Ablauf in nummerierten Schritten und einen Abschnitt „Wenn etwas nicht klappt“. Die Anleitung nennt Schaltflächen und Felder mit ihrer tatsächlichen Beschriftung in der Anwendung. Bildschirmfotos folgen, sobald die Anwendung auf dem Server läuft; sie werden ohne reale Daten aufgenommen. Das Review Center ist in docs/anleitungen/bedienung-review-center.md beschrieben, die Konfiguration in docs/anleitungen/admin-konfiguration.md.

Adresse der Anwendung: `https://uebernahme.muellerhv.de` (nach dem Produktivstart). Die Hauptnavigation zeigt: Objekte, Eigentümer, Review Center, Suche, Berichte, Status, Protokoll (nur mit Recht), Konfiguration, Nutzer (nur Admin), Google Drive (nur Admin), Konto.

## 1. Anmeldung, Rollen, Zwei-Faktor, Passwort

Rollen:

| Rolle | Darf | Darf nicht |
|---|---|---|
| Sachbearbeiter | Objekte und Stammdaten anlegen und ändern, Dokumente hochladen, Verarbeitung starten, im Review Center entscheiden, Importe durchführen, Nachforderungen entwerfen, Listen erzeugen, Eigentümer- und Mieterakten lesen, Statusseite lesen, eigenes Protokoll lesen | Konfiguration ändern, Nutzer verwalten, Nachforderungen freigeben, Objektfälle im Review Center verwerfen, Google Drive verbinden, Löschungen und Aufbewahrungsfristen freigeben |
| Admin | alles, was der Sachbearbeiter darf, zusätzlich Konfiguration, Nutzerverwaltung, Freigabe von Nachforderungen, Verwerfen von Objektfällen, Google-Drive-Verbindung, Statusaktionen, vollständiges Protokoll, Löschungen und Aufbewahrungsfristen | |

Ablauf der ersten Anmeldung:

1. Der Admin legt das Konto an (Nutzer, „Nutzer anlegen“: E-Mail, Name, Rolle) und teilt das Startpasswort außerhalb des Systems mit.
2. Anmeldung mit E-Mail und Passwort.
3. Admins: Die Anwendung verlangt sofort die Einrichtung des zweiten Faktors (TOTP). Den angezeigten QR-Code mit einer Authenticator-App scannen und den sechsstelligen Code bestätigen. Ohne zweiten Faktor ist für Admins keine Seite erreichbar. Sachbearbeiter melden sich mit E-Mail und Passwort an; sie können den zweiten Faktor freiwillig unter „Konto“ einrichten. Welche Rollen ihn brauchen, steht in der Konfiguration (`security.mfa_required_roles`, Vorgabe nur `admin`).
4. Bei jeder weiteren Anmeldung: E-Mail, Passwort, dann der aktuelle Code aus der App, sofern ein zweiter Faktor eingerichtet ist. Nach dem Code fragt die Anwendung „Dieses Gerät merken?“. Mit „Für 90 Tage merken“ entfällt die Codeabfrage auf diesem Browser für 90 Tage (Vorgabe `MFA_TRUST_DAYS` in `.env`); das Vertrauen liegt als signiertes Cookie im Browser und erlischt bei Passwortwechsel, beim Zurücksetzen des zweiten Faktors und beim Löschen der Browserdaten. Auf gemeinsam genutzten oder fremden Geräten „Nicht merken“ wählen.
5. Passwort ändern und zweiten Faktor verwalten unter „Konto“.

Wenn etwas nicht klappt:

- Konto gesperrt nach mehreren Fehlversuchen: der Admin hebt die Sperre unter Nutzer, „entsperren“ auf.
- Authenticator-App verloren: der Admin prüft die Identität außerhalb des Systems und setzt den zweiten Faktor zurück („TOTP zurücksetzen“). Beim nächsten Login wird er neu eingerichtet.
- Sitzung abgelaufen: erneut anmelden; laufende Formulareingaben gehen verloren, gespeicherte Entscheidungen nicht.
- Freigaben (Nachforderung) verlangen eine erneute Anmeldung, auch wenn die Sitzung noch gültig ist. Das ist gewollt.

## 2. Objekt anlegen

Voraussetzung: Objektnummer und Stammdaten aus dem Übernahmevertrag beziehungsweise dem Bestand. Die Objektnummer ist die Ist-Nummer des Objekts (zwei bis sechs Stellen); Nullauffüllung und Namensmuster der Ordner regelt die Konfiguration (drive.*), nicht die Eingabe.

1. Objekte, „Objekt anlegen“.
2. Felder ausfüllen: Objektnummer, Bezeichnung, Straße, Hausnummer, PLZ, Ort, Verwaltungsart (WEG, Mietverwaltung, WEG mit Sondereigentumsverwaltung), Status.
3. Übernahmedaten: Übernahmezeitraum von, Übernahmezeitraum bis (Stichtag), Wirtschaftsjahr beginnt im Monat (1 = Kalenderjahr). Ohne „von“ gilt die Vorgabe aus der Konfiguration (drei abgeschlossene Wirtschaftsjahre plus laufendes Jahr, ANNAHME H09).
4. Prüfgrundlagen für die Vollständigkeit: Sollzahl Einheiten, SEPA-Lastschrift genutzt, Sonderumlagen im Übernahmezeitraum.
5. Vorverwaltung mit Anschrift, Ansprechpartner und Zeichen; diese Angaben stehen später im Anschriftfeld der Nachforderung.
6. Testobjekt nur ankreuzen, wenn das Objekt zu Übungs- oder Messzwecken dient (es erscheint gekennzeichnet).
7. Speichern. Die Objektansicht zeigt Stammdaten, Ordnerabgleiche, Dokumente und Verarbeitung, Vollständigkeit, Nachforderungen und Listen.

Mit dem Speichern beginnt die Anwendung, den Objektordner in Drive anzulegen: Objektordner nach dem Namensmuster aus der Konfiguration und darunter die Hauptordner 01 bis 06 mit den Unterordnern von 06. Der Vorgang läuft als Auftrag im Hintergrund, die Meldung „Der Objektordner wird in Drive angelegt" erscheint sofort, der Ordner selbst nach wenigen Sekunden. Den Verlauf zeigt die Objektansicht unter Ordnerabgleiche.

Akten-Vorlage: Ist die Sollzahl Einheiten eingetragen, legt die Anwendung mit dem Speichern die Einheiten WE 1 bis WE n als Platzhalter an (Datenstatus unvollständig) und die Akten nach Verwaltungsart: WEG je Einheit eine Eigentümerakte; Mietverwaltung eine Eigentümerakte für das Objekt (ein Eigentümer je Haus, Platzhaltername „Eigentümer“, nach Zuordnung zum Beispiel `Eigentümer_Mustermann`) und je Einheit eine Mieterakte; WEG mit Sondereigentumsverwaltung je Einheit beides. Die Akten je Einheit heißen zunächst nur nach dem Einheitenkürzel (WE01, WE02, ...). Sobald der Objektordner steht, entstehen in Drive die Aktenordner: unter `05_Eigentümerakte` je Eigentümerakte ein Ordner mit den elf Unterordnern, unter `04_Mieterakte` je Mieterakte ein Ordner. Wurde die Verwaltungsart geändert oder stammen Akten aus der Zeit vor dieser Regel, legt „Akten anlegen“ unbenutzte Platzhalter still, die die Verwaltungsart nicht vorsieht (ohne Zuordnung, ohne Dokumente); bereits angelegte Ordner in Drive bleiben unverändert und können von Hand entfernt werden. Alle Ordner sind damit vorhanden, auch wenn sie leer sind. Wird später ein Eigentümer oder Mieter zugeordnet (von Hand oder per Import), erhält die Akte ihren Namen nach dem Namensschema (zum Beispiel `WE01_Mustermann`) und der Ordner in Drive wird umbenannt. Umbenannt wird nur ein Ordner, der noch den von der Anwendung vergebenen Namen trägt; ein in Drive von Hand vergebener Name bleibt bestehen. Die Bezeichnung der Einheit (WE 1) lässt sich in der Einheitenzeile mit „Bearbeiten“ anpassen, die Stammdaten der Einheit ebenso. Ohne Sollzahl oder bei später ergänzten Einheiten holt „Akten anlegen“ in der Objektansicht die Vorlage nach. Wer die Vorlage nicht will, setzt `owner_file.create_folders_eagerly` auf `false`; dann entsteht eine Akte erst mit der ersten Ablage.

Besteht in Drive schon ein Ordner mit derselben Objektnummer, übernimmt die Anwendung diesen und legt keinen zweiten an. Sind mehrere Ordner mit derselben Nummer vorhanden oder liegt ein Treffer im Papierkorb, entsteht ein Fall im Review Center und es wird nichts angelegt.

Anstelle der Ordneranlage erscheint ein Hinweis, wenn eine Voraussetzung fehlt:

| Hinweis | Bedeutung | Was zu tun ist |
|---|---|---|
| Ohne Google-Verbindung wurde kein Ordner angelegt | Das Token fehlt, ist abgelaufen oder widerrufen | Admin: Google Drive verbinden, danach in der Objektansicht „Ausführen" beim Ordnerabgleich |
| Der Wurzelordner ist nicht gesetzt | `drive.root_folder_id` fehlt | Admin: Google Drive, Pfad auflösen und Wurzel bestätigen, danach Ordnerabgleich |
| Die Ordneranlage konnte nicht eingereiht werden | Die Auftragsverwaltung war nicht erreichbar | Ordnerabgleich später von Hand starten; das Objekt selbst ist gespeichert |

Das Objekt wird immer gespeichert, auch wenn die Ordneranlage nicht möglich ist. Wer die automatische Anlage nicht will, setzt in der Konfiguration `drive.create_folders_on_object_create` auf `false`; dann bleibt es beim Ordnerabgleich von Hand.

Einheiten und Eigentümer:

1. In der Objektansicht „Einheit anlegen“: Bezeichnung (zum Beispiel WE 14), Typ, Miteigentumsanteil mit Basis, Gebäude, Lage. Die Bezeichnung wird normalisiert, WE 14 und WE14 sind dieselbe Einheit.
2. „Eigentümer anlegen“ (oder Eigentümer aus der Liste wählen). Bankdaten werden nur maskiert gespeichert; die IBAN erscheint nirgends vollständig.
3. „Zuordnung anlegen“: Einheit, Eigentümer, Von, bei Bedarf Bis und Anteil. Überschneidende Zuordnungen derselben Einheit lehnt die Anwendung ab.
4. Eigentümerwechsel: bei der bestehenden Zuordnung „Beenden (Eigentümerwechsel)“ mit Datum, dann neue Zuordnung ab dem Folgetag.
5. „Wer war Eigentümer am Stichtag“: Datum eingeben, „Anzeigen“. Dieselbe Abfrage nutzt die Klassifikation für Dokumente mit Zeitbezug.

Der schnellere Weg für viele Einheiten ist der Import der Eigentümerliste (Kapitel 4).

Wenn etwas nicht klappt:

- Objektnummer bereits vergeben: die Nummer wird über ihren Zahlenwert verglichen (0623 und 623 sind dasselbe Objekt). Bestehendes Objekt öffnen statt neu anlegen.
- Datenstatus „unvollständig“ an Einheit oder Eigentümer: Pflichtfelder fehlen oder stammen aus einem unsicheren Import; in der Zeile „Bearbeiten“ und ergänzen.

Objekt archivieren und wiederherstellen:

1. Objektansicht, „Archivieren“. Die Anwendung verlangt den zweiten Faktor erneut (Step-up), einen Grund und die Bestätigung, dass nichts gelöscht wird.
2. Das Objekt verschwindet aus der Objektliste, aus der Suche nach aktiven Objekten und aus den nächtlichen Läufen (Ordnerabgleich, Vollständigkeit, Listen). Einheiten, Eigentümer, Zuordnungen, Dokumente, Fälle und das Protokoll bleiben vollständig erhalten.
3. In Drive wird nichts gelöscht, verschoben oder umbenannt. Der Objektordner bleibt, wo er ist. Die Anwendung besitzt keine Löschfunktion für Drive; eine spätere Umsortierung (etwa bei einer Migration) wäre ein bewusster, protokollierter Schritt.
4. Archivieren ist nicht möglich, solange ein Verarbeitungslauf wartet oder läuft; erst abwarten, dann archivieren.
5. Objektliste, „Archiv“: zeigt archivierte Objekte mit Datum, Bearbeiter und Grund. „Wiederherstellen“ holt das Objekt zurück und setzt den Status auf „aktiv“ (danach prüfen). Ist die Objektnummer inzwischen an ein anderes aktives Objekt vergeben, lehnt die Anwendung die Wiederherstellung ab.

Beides steht im Protokoll (`object.archive`, `object.restore`) mit Grund und Drive-Ordner.

## 3. Ordnerabgleich

Voraussetzung: Google Drive ist verbunden (Admin: Google Drive, „Verbindung“ mit dem Konto `ablage@muellerhv.de`, dann Wurzelordner 01_Daten „Pfad auflösen“ und „Wurzel bestätigen“). Der Abgleich legt fehlende Ordner an und benennt den Altordner in `06_Sonstiges` um; er löscht nie.

1. Objektansicht, Ordnerabgleiche, „Probelauf (Dry-Run)“. Der Probelauf liest nur.
2. Den Lauf öffnen. Die Planliste zeigt je Zeile Aktion, Kategorie, Name vorher, Name nachher, Drive-ID, Dateien vorher und nachher. Darunter Hinweise (abweichende Schreibweisen, zusätzliche Ordner, Verknüpfungen), Inventur (gefundene Dateien) und Review-Fälle (zum Beispiel Ordnerstruktur, Objektnummer doppelt).
3. Plan prüfen. Export als JSON oder Excel für die Ablage oder die Freigabe.
4. „Abgleich ausführen“. Jede Umbenennung zählt die Dateien vorher und nachher; bei Abweichung bricht der Lauf weitere Schreibaktionen ab und legt einen Fall an.
5. Protokoll des Laufs prüfen: Status, ausgeführte Aktionen, Fehler.
6. Wiederholung: ein zweiter Lauf zeigt `no_changes` und führt keine Schreibaktion aus. Nach Entscheidungen im Review Center (Ordnerstruktur, doppelte Nummer) den Abgleich erneut ausführen.

Für alle Objekte gemeinsam läuft der Abgleich als Kommando auf dem Server (docs/betrieb/abgleich-protokoll.md).

Wenn etwas nicht klappt:

- Kein Objektordner gefunden: Nummer und Wurzelordner prüfen; der Abgleich sucht über die Objektnummer im Ordnernamen. Ohne Treffer plant er die Anlage.
- Mehrere Ordner mit derselben Nummer (etwa „82 …“ und „082 …“): Fall „Objektnummer doppelt“ im Review Center, der Abgleich legt nichts an und die Ablage wartet. Objektansicht und Dokumentseite zeigen den Hinweis mit Sprung in den Fall. Dort „Als Objektordner verwenden“ beim richtigen Ordner, oder in der Objektansicht „Objektordner festlegen“ mit Ordner-ID oder Drive-Link. Danach läuft der Abgleich mit diesem Ordner, legt die Struktur an, und die wartenden Ablagen setzen von selbst fort. Der andere Ordner bleibt unverändert und kann über den Altbestand übernommen werden.
- Google Drive zeigt „widerrufen“ oder die Statusseite ist rot: der Admin autorisiert unter Google Drive neu; wartende Schreibjobs laufen danach weiter.
- Ratenlimit (403, 429): nichts tun, die Anwendung wiederholt mit Wartezeit.

### 3.1 Bestand aus anderen Drive-Ordnern übernehmen

Liegen die Unterlagen eines Objekts noch in der alten Ordnerstruktur (außerhalb des neuen Objektordners), holt „Bestand aus Drive übernehmen“ sie in das Objekt. Voraussetzung: Google Drive ist verbunden, das Objekt ist angelegt und der Ordnerabgleich hat den Objektordner erzeugt.

1. Objektansicht oder Dokumentseite, „Bestand aus Drive übernehmen“. Die Seite zeigt den Wurzelordner mit Unterordnern und Dateien.
2. Ordner für Ordner anklicken; der Pfad oben zeigt, wo man steht. Ein beliebiger Ordner lässt sich auch über seine ID oder den Drive-Link öffnen.
3. Übernehmen: einzelne Dateien ankreuzen und „Ausgewählte übernehmen“, „Alle … freien Dateien dieses Ordners übernehmen“ oder „Ordner mit allen Unterordnern übernehmen“ (Höchstzahl je Vorgang `drive.takeover_max_files`, Vorgabe 500; größere Bestände ordnerweise).
4. Die Dateien werden als Bestandsdokumente registriert und ein Verarbeitungslauf startet. Die Kette liest jede Datei, erkennt Inhalt und Zuordnung und verschiebt sie in den passenden Ordner der neuen Struktur (Elternwechsel, kein Kopieren). Der Herkunftspfad steht am Dokument.
5. Fortschritt auf der Dokumentseite; unklare Dateien landen unter 06_Sonstiges mit Fall im Review Center und werden dort zugeordnet.

Was nicht passiert: Es wird nichts gelöscht. Die Quellordner bleiben stehen (nach der Übernahme leer). Verknüpfungen werden übersprungen, weil das Original an anderer Stelle liegt. Temporär- und Systemdateien (`*.tmp`, `~$…`, `Thumbs.db`, Muster in `drive.takeover_ignore_patterns`) werden nicht übernommen. Alte Word- und Excel-Dateien (`.doc`, `.xls`, `.pub`) kann die Anwendung nicht lesen; sie landen als „nicht unterstütztes Format“ im Review Center und werden dort von Hand zugeordnet. Eine Datei, die bereits einem Objekt zugeordnet ist, zeigt die Seite mit dem Hinweis „in Objekt …“ und übernimmt sie nicht erneut.

### 3.2 Altbestand: alte Objektordner ordnerweise aufarbeiten

Für die Übernahme der bisherigen Ablage führt die Anwendung unter „Altbestand“ (Hauptnavigation) eine Tabelle der Quellordner.

1. „Ordner aufnehmen“: Drive-Links oder Ordner-IDs einfügen, je Zeile einen. Doppelte werden erkannt und nicht erneut aufgenommen. Die Anwendung liest Name und Objektnummer (führende Ziffernfolge im Ordnernamen) aus Drive und ordnet ein vorhandenes Objekt automatisch zu (0623 und 623 sind dasselbe Objekt).
2. Fehlt das Objekt, führt „Objekt anlegen“ in der Zeile zum vorbelegten Formular (Nummer und Bezeichnung aus dem Ordnernamen). Verwaltungsart und Stammdaten ergänzen, speichern; der Ordner ist danach zugeordnet und der Objektordner mit der neuen Struktur wird angelegt.
3. „Aufarbeiten“ in der Zeile: alle Dateien des Ordners samt Unterordnern werden in das Objekt übernommen, ein Verarbeitungslauf startet. Die Kette liest jede Datei, erkennt Inhalt und Zuordnung und verschiebt sie in den passenden Ordner der neuen Struktur; Eigentümer- und Mieterakten entstehen dabei nach der Akten-Vorlage. Der Quellordner bleibt bestehen (nach der Übernahme leer), es wird nichts gelöscht.
4. Die Zeile zeigt Übernommen (Anzahl, übersprungene Dateien in Klammern), Status und den Lauf. Fortschritt und Fälle wie gewohnt unter „Dokumente und Verarbeitung“ und im Review Center.
5. „Entfernen“ nimmt nur die Zeile aus der Tabelle; in Drive ändert sich nichts.
6. „Aktualisieren“ liest alle Ordner neu aus Drive (Name, Objektnummer, Zuordnung). Ordner, die es nicht mehr gibt, weil sie gelöscht wurden oder im Papierkorb liegen, verschwinden aus der Liste; der Vorgang steht im Protokoll. Ist Drive vorübergehend nicht erreichbar, bleibt die Zeile mit einem Hinweis stehen. Auch hier ändert sich in Drive nichts.
7. „Aufräumen“ (nur Admin, erneute Anmeldung): Nach abgeschlossener Aufarbeitung zeigt eine Vorschau, was in der alten Struktur übrig ist: Dubletten (Dokumente, die bereits in der neuen Struktur liegen), Temporär- und Systemdateien, verbleibende Dateien (in Prüfung, fehlgeschlagen, nicht registriert, zu einem anderen Objekt gehörend) und Ordner, die danach leer sind. „In den Papierkorb verschieben“ verschiebt Dubletten, Temporärdateien und leere Ordner (Kinder vor Eltern, zuletzt den Quellordner) in den Papierkorb von Google Drive, wahlweise auch den Ordner `06_Sonstiges/03_Dubletten` des Objekts. Endgültig gelöscht wird nichts, der Papierkorb hält 30 Tage. Verbleibende Dateien bleiben liegen und schützen ihren Ordner; Ordner der neuen Struktur sind tabu. Solange Verarbeitungsjobs des Objekts offen sind, ist Aufräumen gesperrt. Jeder Vorgang steht im Protokoll (`drive.trash`); die Dokumente der Dubletten werden stillgelegt, ihre Fälle geschlossen. War der Quellordner am Ende leer, verschwindet er aus der Tabelle.

Hinweise: Ein zweites „Aufarbeiten“ übernimmt nur neue Dateien, bereits registrierte werden übersprungen. Fehlt der Objektordner noch, wird er angelegt und die Ablage wartet, bis er steht (Job zeigt „wartet: Ablageziel noch nicht vorhanden“). Mehr als `drive.takeover_max_files` Dateien in einem Ordner: Grenze in der Konfiguration anheben oder Unterordner einzeln über „Bestand aus Drive übernehmen“ holen. Liegt der alte Ordner direkt im Wurzelordner und trägt die Objektnummer, übernimmt ihn der Ordnerabgleich als Objektordner und inventarisiert seinen Inhalt ohnehin; „Aufarbeiten“ übernimmt dann nur, was noch fehlt.

## 4. Eigentümer- oder Mieterliste importieren

Angenommene Formate: Excel, CSV, PDF (digital und gescannt), Exporte aus Immoware24; für Domus liegt noch keine Beispieldatei vor (F11), dieses Profil ist nur manuell wählbar. Nichts wird ohne Bestätigung übernommen.

1. Objektansicht, „Liste importieren“, Datei wählen, „Hochladen“. Dieselbe Datei wird je Objekt nur einmal angenommen.
2. Import öffnen. Die Zähler zeigen Zeilen gesamt, sicher, unsicher, übernommen, abgelehnt. Das erkannte Profil steht im Kopf; bei falschem Profil oder falschem Blatt „Datei neu einlesen“ mit Profil und Blatt.
3. Spaltenzuordnung prüfen: je Quellspalte Beispiele und das vorgeschlagene Zielfeld. Korrigieren, dann „Zuordnung übernehmen und Zeilen erkennen“. Jedes Zielfeld darf nur einmal vorkommen.
4. Zeilen prüfen. Filter „alle, sicher, unsicher, übernommen, abgelehnt“. Sichere Zeilen markieren und „Ausgewählte übernehmen (Vorschau)“; die Vorschau zeigt je Zeile Einheit, Person, Eigentümer und Eigentumsbeginn. „Ausführen“ übernimmt.
5. Unsichere Zeilen einzeln über „Entscheiden“: Rohzeile, erkannte Felder, aktuelle Zuordnung der Einheit, Kandidaten mit Score und Signalen. Entscheidung wählen und „Speichern“. Unsichere Zeilen erscheinen zusätzlich als Fälle „Importzeile unsicher“ im Review Center.
6. „Ausgewählte nicht übernehmen (Vorschau)“ für Zeilen, die nicht in den Bestand gehören (Leerzeilen, Summenzeilen).
7. Importprotokoll (Excel) herunterladen und ablegen.

Besonderheiten:

- Mieterlisten erzeugen je Quellzeile ein Mietverhältnis; Mitmieter teilen es.
- Gescannte Listen: Zellen mit geringer OCR-Konfidenz machen die Zeile unsicher (Grund `low_ocr_confidence`), nichts wird verworfen. Bei systematisch schlechter Vorlage einen besseren Scan oder Export bei der Vorverwaltung anfordern.
- Eine Liste, die als Dokument in der Verarbeitung erkannt wird, erscheint als Fall „Liste erkannt“; „Import starten“ im Fall legt den Import an.

Wenn etwas nicht klappt:

- Kopfzeile nicht erkannt: Profil `generic_table` wählen und Spalten manuell zuordnen.
- Person passt zu keinem Eigentümer: in der Zeilenentscheidung neuen Eigentümer anlegen lassen; ein Nachname allein reicht nie für eine automatische Zuordnung.
- Konflikt mit bestehender Zuordnung (Wechsel, Mehrfacheigentum, Dublette): die Zeile ist unsicher; Entscheidung in der Zeile oder im Review Center.

## 5. Verarbeitung starten und verfolgen

1. Objektansicht, Dokumente und Verarbeitung.
2. „Dokumente hochladen“ (mehrere Dateien möglich) oder Bestandsdateien aus Drive über die Inventur des Ordnerabgleichs.
3. „Pipeline-Dry-Run (nur Plan)“ erzeugt Klassifikation und Ablageplan ohne Schreibzugriff auf Drive; sinnvoll vor der ersten Ausführung je Objekt.
4. „Verarbeitung starten“. Je Objekt läuft ein Lauf zur Zeit; weitere Objekte zeigen ihre Warteposition.
5. Fortschritt in der Tabelle Läufe (Dokumente, Seiten, Seiten je Minute, Start, Ende) und je Dokument (Status, Kategorie, Unterart, Plan und Konfidenz, Dublette von).
6. Dokumentansicht: Jobs mit Versuchen und Dauer, Klassifikationsverlauf je Stufe mit Begründung, Ablageplan, Eigentümer- und Mieterakten, Review-Fälle, Entitäten, Seitentexte (maskiert).
7. Statusseite: Queues, laufende Läufe, Kaltstart der Klassifikation, Kosten der Stufe 3 je Objekt und Monat, Sicherung, Google-Token, Speicher, Alarmbedingungen.

Abbruch und Wiederaufnahme: Jobs sind wiederaufnehmbar. Nach einem Neustart setzt der Sweeper (jede Minute und beim Start) liegen gebliebene Jobs zurück; auf der Statusseite „Sweeper jetzt ausführen“ (Admin). Kein Dokument wird doppelt verarbeitet; Dubletten landen in 03_Dubletten.

Wenn etwas nicht klappt:

- Dokument im Status Fehler: Fehlertext in der Dokumentansicht lesen (verschlüsselte oder beschädigte Datei, Größenlimit). Datei prüfen und erneut hochladen.
- Viele Dokumente in 06/01_Unklar: normal in der Kaltstartphase; die Fälle werden im Review Center entschieden und trainieren das Modell. Stufe 3 (KI) läuft nur, wenn sie freigegeben und konfiguriert ist.
- „KI nicht verfügbar“ oder „Kostenlimit erreicht“ als Grund: Admin prüft Statusseite und Konfiguration; nach Behebung Neubewertung über das Kommando `ai_reclassify` (Runbook).

Wohin ein Dokument gelangt, wenn es nicht zugeordnet werden kann:

| Befund | Ablage in Drive | Fall im Review Center | Bedeutung |
|---|---|---|---|
| Keine Kategorie erreicht die Schwelle | `06_Sonstiges/01_Unklar` | ja, Typ „unklar“ mit Vorschlag | Dokument gehört zum Objekt, Art nicht sicher erkannt |
| Erkannt, aber Regel verlangt Sichtung | `06_Sonstiges/02_Manuelle_Pruefung` | ja | zum Beispiel Datei zu groß, Format nicht lesbar |
| Gleicher Inhalt schon vorhanden | `06_Sonstiges/03_Dubletten` | ja, mit Verweis auf das Original | keine zweite OCR, keine zweite Ablage im Fachordner |
| Anderes Objekt oder kein Objektbezug | `06_Sonstiges/04_Nicht_objektbezogen` | ja, mit Vorschlag „in Objekt … übernehmen“ | erkannte fremde Objektnummer, Werbung, Privates |

Jedes angenommene Dokument wird also abgelegt, auch ohne sichere Zuordnung; die Ablage in `06_Sonstiges` ist der Auffangbereich, der Fall im Review Center die Aufgabe, es nachzuordnen. Nichts bleibt unabgelegt liegen, nichts wird verworfen.

Mieterdokumente ohne bekannten Mieter (Mietvertrag, Kaution, Betriebskostenabrechnung an Mieter): Kennt die Anwendung den Mieter noch nicht (kein Import, keine Zuordnung), bleibt das Dokument in „Prüfung“ und erzeugt den Fall „Mieter unbekannt“. Die Anwendung liest dafür die Vertragsdaten aus dem Text: Mieter und Mitmieter (nicht den Vermieter), Einheit oder Lage („WE 3“, „2. OG links“), Mietbeginn und Mietende, Kaltmiete, Betriebskosten- und Heizkostenvorauszahlung, Kaution. Ist ein KI-Provider aktiviert, liest zusätzlich Stufe 3 diese Werte (`ai.extract_lease_facts`, Vorgabe an), auch wenn die Kategorie schon sicher war. Im Fall steht unter Zielbereich 04 der Abschnitt „Mieter aus dem Dokument anlegen“ mit den vorbelegten Feldern. Prüfen, korrigieren, Einheit wählen oder als neue Einheit eintragen (unabhängig von der Sollzahl Einheiten), speichern: Damit entstehen Mieter, Mitmieter, Mietverhältnis und Zuordnung (Datenstatus bestätigt, Quelle ist das Dokument), die Mieterakte der Einheit erhält ihren Namen (`WE02_Mustermann`), das Dokument wird dorthin verschoben und die Mieterliste neu erzeugt. Ist der Mieter bereits vorhanden, stattdessen oben „Vorhandenen Mieter zuordnen“ nutzen. Stammdaten entstehen nie ohne diese Bestätigung.

Fehlgeschlagene Dokumente: Bricht ein Verarbeitungsschritt endgültig ab (Status „Fehler“, Fall „job_failed“), nimmt der nächste Klick auf „Verarbeitung starten“ diese Dokumente automatisch wieder auf. Der Wiedereinstieg erfolgt so spät wie möglich (vorhandene Hashes und erkannte Seitentexte werden nicht neu berechnet), der Fall wird als erledigt geschlossen. Scheitert es erneut, entsteht ein neuer Fall mit der neuen Fehlermeldung.

Ohne Google-Verbindung: Die Verarbeitung läuft bis zur Entscheidung durch, die Ablage in Drive wartet dann („wartet: Keine Google-Verbindung für die Ablage“ in den offenen Jobs) und prüft alle zehn Minuten erneut. Das Dokument bleibt „klassifiziert“ oder „in Prüfung“, es geht nicht auf „Fehler“. Sobald ein Admin die Verbindung herstellt, wird ohne weiteres Zutun abgelegt.

## 6. Listen (Eigentümer- und Mieterliste)

Wo sie liegen: Objektansicht, Listen. Je Liste und Format (Excel, PDF) zeigt die Seite Sollname, letzten Lauf, Status, Zeilen aktuell, Historie und offen, den Drive-Link und die lokale Datei. In Drive liegt jede Liste als eine Datei mit Revisionen, nicht als neue Datei je Lauf.

Wann sie sich aktualisieren: nach jedem Verarbeitungslauf (nicht im Dry-Run), nach Entscheidungen im Review Center (gebündelt), nach Import-Übernahmen und nach Stammdatenänderungen. Manuell: „Listen jetzt erzeugen“.

Status und Quelle lesen: Jede Zeile trägt den Datensatzstatus und die Herkunft der Angaben (aus welchem Import, welcher Review-Entscheidung oder welcher manuellen Eingabe sie stammen). „unvollständig“ bedeutet, dass eine Einheit ohne Zuordnung ist oder Pflichtfelder fehlen; die Liste zeigt die Zeile trotzdem, damit nichts verloren geht. Das Blatt Offene Punkte enthält die fehlenden Unterlagen aus der Vollständigkeitsprüfung.

Warum keine IBAN erscheint: Bankverbindungen stehen nur als letzte vier Stellen in der Liste. Enthält eine Quelle (Bemerkungsfeld) eine vollständige IBAN, bricht die Erzeugung ab und legt einen Fall an; die Quelle bereinigen und erneut erzeugen.

Wenn etwas nicht klappt:

- Status fehlgeschlagen, keine Datei in Drive: Google-Verbindung auf der Statusseite prüfen; die Datei liegt lokal vor und wird beim nächsten Lauf hochgeladen.
- Liste in Drive verschoben oder umbenannt: die Anwendung führt sie beim nächsten Lauf zurück (Konfiguration lists.restore_position).

## 7. Vollständigkeit und Offene Punkte

1. Objektansicht, Vollständigkeit. „Jetzt bewerten“ startet die Bewertung; automatisch läuft sie nach jedem Verarbeitungslauf, jeder Review-Entscheidung, jeder Import-Übernahme, jeder Stammdatenänderung und nächtlich für Objekte in Übernahme.
2. Bewertung lesen: Status je Objekt und je Einheit (fehlt, teilweise, erfüllt), Offene Punkte mit Einheit, Eigentümer, Prüfpunkt, Jahr, Status, Hinweis, Nachweis und Kennzeichen „in Nachforderung“.
3. Manuell schließen (Negativerklärung): in der Zeile Übersteuerung wählen, Grund eintragen, „Speichern“. Beispiel: die Vorverwaltung erklärt schriftlich, dass keine Sonderumlage beschlossen wurde. Die Übersteuerung ist mit Nutzer und Zeit protokolliert und bleibt bei der nächsten Bewertung bestehen.
4. Zeitraum anpassen: Übernahmezeitraum und Wirtschaftsjahr im Objekt bearbeiten; die Neubewertung folgt automatisch.
5. „Alle Prüfpositionen“ zeigt jede Position mit Ebene, Status, wirksamem Status und Übersteuerung.

Wenn etwas nicht klappt:

- Bewertung veraltet oder „noch nicht bewertet“: „Jetzt bewerten“.
- Prüfpunkt gilt fachlich nicht (zum Beispiel SEPA nicht genutzt): Stammdaten im Objekt korrigieren, nicht übersteuern.

## 8. Nachforderung an die Vorverwaltung

Die Nachforderung ist ein Schreiben im HVM-Briefbogen (PDF und DOCX) mit den offenen Punkten. Sie wird nicht aus der Anwendung versendet; der Versand erfolgt wie gewohnt per E-Mail oder Post und wird danach vermerkt.

1. Objektansicht, Nachforderungen. Unter „Neuer Entwurf“ die Frist eintragen und „Entwurf erzeugen“. Jeder Entwurf ist eine neue Version mit Momentaufnahme der offenen Punkte.
2. Entwurf öffnen. Empfänger und Frist prüfen (Vorverwaltung, Ansprechpartner, Straße, Nr., PLZ, Ort, Ihr Zeichen, Frist); Änderungen mit „Entwurf speichern“. Ohne Anschrift und Frist ist keine Freigabe möglich.
3. Betreff und Text prüfen (Textbausteine aus der Konfiguration), Positionen nach Gruppe, Einheit, Eigentümer, Unterlage, Jahr. Ab 25 Positionen (Konfiguration requests.attachment_threshold) steht die Einzelaufstellung als Anlage.
4. PDF und DOCX des Entwurfs herunterladen; der Entwurf trägt das Wasserzeichen ENTWURF.
5. „Als geprüft kennzeichnen“ (Sachbearbeiter).
6. „Freigeben (Geschäftsführung, erneute Anmeldung)“: nur Admin, mit erneuter Anmeldung. Die freigegebene Fassung trägt kein Wasserzeichen und das Unterschriftsbild, sofern es auf dem Server hinterlegt ist.
7. Versand außerhalb der Anwendung. Danach „Als versendet vermerken“ mit Kanal und Datum im Freitext.
8. Erinnerung nach Fristablauf: neuen Entwurf erzeugen; er enthält den aktuellen Stand der offenen Punkte und wird wie oben geprüft und freigegeben.
9. „Zurückziehen“ mit Pflichtgrund, wenn ein Schreiben nicht mehr gelten soll.

Wenn etwas nicht klappt:

- Freigabe nicht möglich: Frist und Anschrift der Vorverwaltung fehlen, oder der Entwurf ist nicht als geprüft gekennzeichnet.
- Freigegebenes PDF ohne Unterschriftsbild: das Bild liegt nicht auf dem Server (Runbook); Schreiben nach dem Hinterlegen erneut freigeben (neue Version).

## 9. Suche und Objektübersicht

Suche (Hauptnavigation): Felder Volltext (maskierter Seitentext), Eigentümer, Mieter, Einheit (WE 14), Objekt, Jahr sowie weitere Filter (Zeitraum, Dokumentunterart, Hauptordner, Status, Verwaltungsart). Ergebnisse nach Art: Objekte, Einheiten mit Eigentümern heute, Eigentümer mit Zuordnungen, Mieter, Dokumente mit Ablage, Akte, Seitenbereich, Jahr, Status, Volltexttreffer und Drive-Link. Eigentümerakten sieht nur, wer das Recht dazu hat.

Hinweise: Umlautvarianten werden mitgesucht (Müller findet Mueller). Sehr kurze Wörter werden übersprungen und angezeigt; dann längeres Wort oder Filter verwenden. Der Suchtext wird nicht protokolliert.

Berichte (Hauptnavigation): Objektübersicht mit Dokumenten, Anteil 06 brutto, aktuell und bereinigt, Vollständigkeit, offenen Review-Fällen mit Alter (Median und Maximum, Warnung ab der konfigurierten Anzahl Arbeitstage), Trefferquote des Systems, Akten, KI-Kosten, Importen; „CSV-Export“ für Excel (Semikolon, deutsche Dezimaltrennung). Der Objektbericht zeigt Kacheln, offene Punkte und offene Fälle.

KPI lesen: Der Anteil 06 brutto ist der Anteil der Dokumente, die der letzte Lauf zunächst nach 06_Sonstiges gelegt hat; „aktuell“ zählt den heutigen Stand nach Review; „bereinigt“ lässt Dokumente aus, die fachlich nach 06 gehören. Zielwert ist der Konfigurationswert reports.misc_share_target_pct (Vorgabe 5 Prozent).

## 10. Häufige Fragen und Fehlermeldungen

| Meldung oder Frage | Bedeutung | Was tun |
|---|---|---|
| Google Drive nicht erreichbar, Statusseite rot | Verbindung, Token oder Netz gestört | Admin prüft Google Drive; Schreibjobs warten und laufen nach der Behebung weiter |
| Token abgelaufen oder widerrufen | Autorisierung des Kontos `ablage@muellerhv.de` ungültig | Admin: Google Drive, neu autorisieren |
| Objektnummer doppelt | Zwei Drive-Ordner tragen dieselbe Nummer | Fall im Review Center entscheiden, danach Abgleich wiederholen |
| Listen veraltet | Kein Lauf seit der letzten Änderung oder Erzeugung fehlgeschlagen | „Listen jetzt erzeugen“; Fehler auf der Listenseite lesen |
| Importzeile nicht zuordenbar | Person oder Einheit ohne eindeutigen Treffer | Zeile entscheiden (Kandidat wählen oder neu anlegen) |
| Dokument in 06/01_Unklar | Klassifikation unter der Schwelle | Fall im Review Center entscheiden |
| Seite lädt langsam während eines Laufs | Verarbeitung belegt den Server | kurz warten; Zielwert p95 unter 2 Sekunden wird im Performance-Test nachgewiesen (M13) |
| Kurzes Suchwort wird übersprungen | Mindestlänge der Volltextsuche | längeres Wort oder Filter |

## 11. Begriffe

| Begriff | Bedeutung |
|---|---|
| Objektordner | Ordner des Objekts in Drive unter 01_Daten, erkannt über die Objektnummer im Namen |
| Hauptordner | die sechs Ordner je Objekt (01 bis 06), 05_Eigentümerakte und 06_Sonstiges eingeschlossen |
| Eigentümerakte | Unterordner je Einheit und Eigentümergruppe in 05 mit fester Unterstruktur; als Platzhalter nur mit Einheitenkürzel (WE01), nach Zuordnung mit Namen |
| Mieterakte | Unterordner je Einheit in 04; Mieterdokumente werden dort abgelegt, der Name folgt dem Mieter |
| Akten-Vorlage | Einheiten aus der Sollzahl und beide Akten je Einheit werden mit dem Objekt angelegt, die Ordner in Drive sofort; Schalter `owner_file.create_folders_eagerly` |
| Zuordnung | Eigentümer zu Einheit mit Zeitraum (Von, Bis) |
| Stichtag | Datum, für das die Eigentümerschaft abgefragt wird |
| Fall | offener Punkt im Review Center, immer mit Fallart |
| Unterart | Dokumentart innerhalb einer Kategorie (zum Beispiel Einzelabrechnung) |
| Zeitraum | Abrechnungs- oder Planjahr eines Dokuments |
| Lauf | eine Verarbeitung oder ein Abgleich je Objekt mit Protokoll |
| Dry-Run | Probelauf ohne Schreibzugriff |

Ansprechpartner: der Admin der Anwendung (Nutzerverwaltung, Konfiguration, Google Drive). Störungen mit Erkennung und Maßnahme stehen in docs/betrieb/runbook.md.
