# Einweisungsleitfaden: Anwender der Hausverwaltung Müller GmbH

Stand: 26.09.2026. Dieser Leitfaden führt durch die Einweisung der Anwender in die Anwendung Objektübernahme und dient als Nachweis für die Zeile „Einweisung der Anwender, Nutzeranlage, TOTP“ im Abnahmeprotokoll (docs/plan/abnahme.md, Abschnitt 2). Er wiederholt die Bedienungsanleitungen nicht, sondern legt die Reihenfolge fest, nennt je Station die Stelle in der Anleitung und enthält am Ende das Einweisungsprotokoll zum Unterschreiben. Alle Beispiele verwenden die synthetischen Testdaten aus tests/ (Objekt 623 Musterstadt, Musterweg 1; Eigentümer Mustermann, Beispiel, Altmuster, Neumuster). Reale Namen, Anschriften oder Objektnummern des Bestands gehören nicht in die Einweisung und nicht in Bildschirmaufnahmen.

Verwendete Anleitungen:

| Thema | Anleitung |
|---|---|
| Anmeldung, Rollen, zweiter Faktor, Objektanlage, Ordnerabgleich, Import, Verarbeitung, Listen, Vollständigkeit, Nachforderung, Suche, Berichte | docs/anleitungen/bedienung-objektanlage.md |
| Review Center (Liste, Detailansicht, Aktionen, Massenbearbeitung, Tasten) | docs/anleitungen/bedienung-review-center.md |
| Dokumenteneingang (Objektzuordnung, Konflikte, Paperless) | docs/anleitungen/bedienung-dokumenteneingang.md |
| Konfigurationsschlüssel | docs/anleitungen/admin-konfiguration.md |
| Störungen und Maßnahmen für den Admin | docs/betrieb/runbook.md |
| Nutzeranlage auf dem Server, Deploy-Aktionen | docs/betrieb/deployment.md, docs/betrieb/github-deploy.md |

## 1. Ziel, Teilnehmer, Dauer

Ziel: Jeder Anwender kann sich nach der Einweisung selbständig anmelden, den zweiten Faktor bedienen, ein Objekt anlegen, Dokumente in die Verarbeitung geben, Fälle im Review Center entscheiden und die Statusseite und die Berichte lesen. Der Admin kann Nutzer anlegen, sperren, entsperren und den zweiten Faktor zurücksetzen.

Teilnehmer: alle Personen aus der Nutzerliste (V-20), getrennt nach Rolle. Der Admin nimmt zusätzlich an Abschnitt 2 teil, die Sachbearbeiter nicht.

Dauer: 60 bis 90 Minuten je Gruppe. Der Umsetzungsplan (docs/umsetzungsplan.md, Annahme A-39) rechnet mit 2 Stunden je Anwender; die Abweichung ergibt sich aus der Einweisung in Gruppen statt in Einzelterminen. Die tatsächliche Dauer wird je Termin in der Spalte „Dauer (Ist)“ des Einweisungsprotokolls (Abschnitt 7) erfasst und in M15 mit A-39 abgeglichen. Aufteilung etwa

| Block | Inhalt | Dauer |
|---|---|---|
| A | Anmeldung, zweiter Faktor, Konto (Abschnitt 3) | 15 Minuten |
| B | Rundgang Objektanlage und Dokumente (Abschnitt 4.1 und 4.2) | 20 Minuten |
| C | Review Center (Abschnitt 4.3) | 25 Minuten |
| D | Statusseite, Berichte, Grundregeln, Fragen (Abschnitt 4.4, 5, 6) | 15 Minuten |
| E | Protokoll und Unterschriften (Abschnitt 7) | 5 Minuten |

Voraussetzungen am Einweisungstag: Die Anwendung ist unter `https://uebernahme.muellerhv.de` erreichbar, die Statusseite zeigt „bereit“, jeder Teilnehmer hat ein eigenes Konto (Abschnitt 2) und ein Mobilgerät mit Authenticator-App, ein Testobjekt mit synthetischen Daten ist vorhanden oder wird im Rundgang angelegt.

## 2. Vorbereitung durch den Admin

### 2.1 Ersten Admin anlegen (Kommandozeile)

Der erste Admin kann nicht über die Oberfläche entstehen, weil die Nutzerverwaltung selbst ein Admin-Konto voraussetzt. Zwei gleichwertige Wege:

1. Deploy-Workflow (GitHub Actions, Workflow `Deploy`, Aktion `create-admin`, Argument ist die E-Mail-Adresse). Die Aktion erzeugt ein Zufallspasswort und legt es nur in der Datei `/home/deploy/admin-startpasswort.txt` auf dem Server ab, nie im Log (docs/betrieb/github-deploy.md, Schritt 4).
2. Auf dem Server: `docker compose exec web /usr/local/bin/entrypoint.sh app-create-admin --email [ADMIN_ADRESSE]`. Das Passwort wird zweimal abgefragt; `docker compose exec` umgeht das ENTRYPOINT des Images, deshalb der ausdrückliche Aufruf des Skripts (docs/betrieb/deployment.md, Erstinstallation Schritt 6).

Beide Wege rufen das Kommando `create_admin` auf: Rolle `admin`, Anzeigename gleich E-Mail, sofern nicht mit `--display-name` angegeben, Protokolleintrag `user.create`. Die Startpasswortdatei wird nach dem ersten erfolgreichen Login gelöscht (`shred -u /home/deploy/admin-startpasswort.txt`). Empfehlung aus docs/betrieb.md 4.5: zwei Admin-Konten anlegen, damit ein verlorener zweiter Faktor nicht die Verwaltung blockiert.

### 2.2 Weitere Nutzer anlegen (Oberfläche)

1. Als Admin anmelden, zweiter Faktor ist bereits eingerichtet.
2. Hauptnavigation Nutzer, „Nutzer anlegen“. Die Anwendung verlangt vor der Anlage eine erneute Anmeldung (Step-up), sofern die letzte Anmeldung oder Bestätigung länger als 15 Minuten zurückliegt (Vorgabe `REAUTH_TIMEOUT_SECONDS`).
3. Felder: E-Mail, Anzeigename, Rolle, Startpasswort (Mindestlänge 12 Zeichen, keine gebräuchlichen Passwörter, nicht rein numerisch).
4. „Anlegen“. Die Meldung lautet „Nutzer … angelegt. TOTP wird beim ersten Login eingerichtet.“; der Eintrag steht im Protokoll als `user.create`.
5. Das Startpasswort persönlich übergeben, nie per E-Mail. Der Anwender ändert es beim ersten Login über die Adresse `/konto/password/change/`; die Konto-Seite selbst enthält keinen Link dorthin.

Die Nutzerliste (Hauptnavigation Nutzer) zeigt je Konto E-Mail, Name, Rolle mit „ändern“, Status (aktiv, gesperrt, eingeladen), den Stand des zweiten Faktors (eingerichtet; offen, Pflicht; nicht eingerichtet, freiwillig), den letzten Login und die Aktionen „sperren“ beziehungsweise „aktivieren“, „TOTP zurücksetzen“ und, nur bei einer Sperre nach Fehlversuchen, „entsperren“. Jede dieser Aktionen verlangt den Step-up und schreibt einen Protokolleintrag (`user.status`, `user.role`, `auth.totp_reset`, `user.unlock`). Ein Konto wird nie gelöscht, sondern gesperrt. Das eigene Konto lässt sich nicht sperren.

### 2.3 Rollen

Die beiden Rollen stammen aus db/seeds/roles.json und werden mit dem Seed-Kommando eingespielt. Die Klartextbedeutung der Rechte steht in src/apps/accounts/permissions.py.

| Rolle | Code | Rechte | Bedeutung für die Einweisung |
|---|---|---|---|
| Sachbearbeiter | `sachbearbeiter` | Objekte anlegen und bearbeiten, Ordnerabgleich; Dokumente hochladen, Verarbeitung starten; Review Center bearbeiten, Massenbearbeitung; Nachforderung als Entwurf; Eigentümer- und Mieterakten sehen; Stammdaten ändern; Listen erzeugen; Statusseite lesen; eigene Protokolleinträge; KI-Aufrufe und Kosten einsehen; Import hochladen und bestätigen; Dokumenteneingang bearbeiten | Tagesgeschäft. Zweiter Faktor freiwillig (Vorgabe in `security.mfa_required_roles`). |
| Admin | `admin` | alle Rechte des Sachbearbeiters, zusätzlich Konfiguration ändern; Aufbewahrungsfristen und Löschläufe freigeben; Nutzer anlegen, sperren, Rolle ändern, TOTP zurücksetzen; Fälle zur Objektzuordnung verwerfen; Nachforderung freigeben; Statusaktionen (Sweeper, Google-Verbindung erneuern, Listen erzeugen); Protokoll vollständig; Google-Drive-Verbindung; Altbestand aufräumen (Papierkorb); Synchronisation mit Paperless und Drive verwalten | Betrieb und Freigaben. Zweiter Faktor Pflicht; ohne Einrichtung ist außer dem Bereich Konto (Einrichtung, Abmelden, Passwortänderung) keine Seite erreichbar. |

Was ein Nutzer sieht, hängt von der Rolle ab: Die Menüeinträge Altbestand, Eingang, Protokoll, Nutzer, Google Drive und Paperless erscheinen nur mit dem jeweiligen Recht. Sachbearbeiter sehen daher kein Nutzer-, Google-Drive- und Paperless-Menü.

### 2.4 Nutzerliste (Vorlage, vor der Einweisung ausfüllen)

| Nr. | Anzeigename | E-Mail | Rolle | angelegt am | Startpasswort übergeben am | Erster Login am | Zweiter Faktor eingerichtet |
|---|---|---|---|---|---|---|---|
| 1 | [Name] | [adresse@muellerhv.de] | Admin | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [ja, Datum] |
| 2 | [Name] | [adresse@muellerhv.de] | Admin | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [ja, Datum] |
| 3 | [Name] | [adresse@muellerhv.de] | Sachbearbeiter | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [freiwillig, ja oder nein] |
| 4 | [Name] | [adresse@muellerhv.de] | Sachbearbeiter | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [TT.MM.JJJJ] | [freiwillig, ja oder nein] |

Die ausgefüllte Liste ist die Nutzerliste V-20 und wird dem Abnahmeprotokoll beigefügt. Sie enthält keine Passwörter.

### 2.5 Testobjekt vorbereiten

Für den Rundgang ein Objekt mit angekreuztem Feld Testobjekt und synthetischen Daten anlegen, zum Beispiel Objektnummer 623, Bezeichnung „Musterstadt, Musterweg 1“, Verwaltungsart WEG, Sollzahl Einheiten 3. Das Objekt erscheint in der Objektliste mit der Kennzeichnung „Test“. Als Übungsdokumente nur Dateien ohne reale Personendaten verwenden. Nach der Einweisung das Testobjekt archivieren (Objektansicht, „Archivieren“, mit Grund), nicht löschen.

## 3. Anmeldung und zweiter Faktor Schritt für Schritt

Vorab jeder Teilnehmer: eine Authenticator-App auf dem Mobilgerät installieren, die zeitbasierte Einmalcodes (TOTP) erzeugt. Jede gängige App ist geeignet; die Anwendung gibt keine vor. Der Eintrag erscheint in der App unter dem Namen „Objektuebernahme HVM“.

### 3.1 Erste Anmeldung

1. `https://uebernahme.muellerhv.de` aufrufen; die Anwendung leitet auf die Anmeldeseite (`/konto/login/`).
2. E-Mail und Startpasswort eingeben. Eine Selbstregistrierung gibt es nicht; der Link „registrieren“ auf der Anmeldeseite führt auf eine geschlossene Seite, Konten legt nur der Admin an.
3. Admins werden sofort auf die Einrichtungsseite des zweiten Faktors geleitet (Meldung „Bitte richten Sie zuerst den zweiten Faktor (Authenticator-App) ein.“). Sachbearbeiter landen auf der Startseite und richten den zweiten Faktor freiwillig unter Konto ein; der Ablauf ist derselbe.
4. Einrichtung: den angezeigten QR-Code mit der Authenticator-App scannen (oder das angezeigte Geheimnis von Hand eintragen), den sechsstelligen Code aus der App eingeben, bestätigen.
5. Wiederherstellungscodes: Nach der Einrichtung zeigt die Anwendung einmalig eine Liste mit Wiederherstellungscodes. Diese Liste ausdrucken oder an einem sicheren Ort außerhalb des Mobilgeräts ablegen. Jeder Code ersetzt einmalig den Code aus der App, wenn das Gerät nicht verfügbar ist. Die Codes sind später unter Konto abrufbar und können dort neu erzeugt werden; alte Codes verlieren dann ihre Gültigkeit.
6. Das Startpasswort durch ein eigenes Passwort ersetzen (Mindestlänge 12 Zeichen). Die Passwortänderung ist nur über die Adresse `https://uebernahme.muellerhv.de/konto/password/change/` erreichbar; die Konto-Seite enthält keinen Link dorthin. Die Adresse in der Einweisung an der Tafel notieren.

### 3.2 Jede weitere Anmeldung

1. E-Mail und Passwort.
2. Ist ein zweiter Faktor eingerichtet: aktueller Code aus der App (oder ein Wiederherstellungscode).
3. Frage „Dieses Gerät merken?“: „Für 90 Tage merken“ nur auf dem eigenen Arbeitsplatzrechner, „Nicht merken“ auf fremden oder gemeinsam genutzten Geräten. Das Vertrauen liegt als signiertes Cookie im Browser und erlischt bei Passwortwechsel, beim Zurücksetzen des zweiten Faktors und beim Löschen der Browserdaten (Einzelheiten in bedienung-objektanlage.md, Kapitel 1).
4. Sitzungen enden nach 8 Stunden ohne Aktivität und spätestens nach 12 Stunden (Vorgaben `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS`); danach erscheint „Die Sitzung ist abgelaufen. Bitte erneut anmelden.“
5. Freigaben und Verwaltungsaktionen (Nutzerverwaltung, Konfiguration, Archivieren, Aufräumen, Freigabe einer Nachforderung) verlangen eine erneute Anmeldung, sofern die letzte Anmeldung oder Bestätigung länger als 15 Minuten zurückliegt (Vorgabe `REAUTH_TIMEOUT_SECONDS`). Das ist gewollt und keine Störung.
6. Abmelden über „Abmelden“ in der Hauptnavigation, insbesondere an gemeinsam genutzten Rechnern.

### 3.3 Sperre nach Fehlversuchen

Nach fünf Fehlversuchen in Folge ist das Konto 15 Minuten gesperrt (Vorgaben `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`). Die Anmeldeseite meldet „Das Konto ist nach zu vielen Fehlversuchen vorübergehend gesperrt“ mit der Restzeit. Der Anwender wartet oder bittet den Admin um „entsperren“ in der Nutzerliste. Jeder Fehlversuch steht im Protokoll (`auth.login_failed`); bei Häufung prüft der Admin nach Runbook „Konto gesperrt“.

### 3.4 Zweiter Faktor verloren oder Gerät gewechselt

1. Der Anwender meldet sich mit einem Wiederherstellungscode an und richtet unter Konto den zweiten Faktor auf dem neuen Gerät neu ein. Das ist der Regelweg und braucht keinen Admin.
2. Ohne Wiederherstellungscode: Der Admin prüft die Identität außerhalb des Systems (persönlich oder telefonisch über eine bekannte Nummer) und wählt in der Nutzerliste „TOTP zurücksetzen“. Beim nächsten Login wird der zweite Faktor neu eingerichtet. Der Vorgang steht im Protokoll als `auth.totp_reset`.

In der Einweisung wird Schritt 1 einmal je Admin geübt, damit der Umgang mit den Wiederherstellungscodes bekannt ist.

## 4. Rundgang

Der Rundgang folgt der Hauptnavigation: Objekte, Eigentümer, Altbestand, Review Center, Eingang, Suche, Berichte, Status, Protokoll, Konfiguration, Nutzer, Google Drive, Paperless, Konto. Der Einweisende zeigt jede Station am Testobjekt; die Teilnehmer führen die Schritte 1 bis 3 der Objektanlage und mindestens eine Entscheidung im Review Center selbst aus.

### 4.1 Objektanlage (bedienung-objektanlage.md, Kapitel 2)

1. Objekte, „Objekt anlegen“: Objektnummer 623, Bezeichnung „Musterstadt, Musterweg 1“, Straße Musterweg, Hausnummer 1, PLZ und Ort synthetisch, Verwaltungsart WEG, Sollzahl Einheiten 3, Testobjekt angekreuzt, Speichern.
2. Zeigen, was das Speichern auslöst: Meldung zur Ordneranlage in Drive, Abschnitt Ordnerabgleiche in der Objektansicht, die Platzhalter WE 1 bis WE 3 mit Eigentümerakten.
3. Einheit und Eigentümer: „Einheit anlegen“ (WE 4), „Eigentümer anlegen“ (Mustermann), „Zuordnung anlegen“ mit Von-Datum; Eigentümerwechsel über „Beenden (Eigentümerwechsel)“ und neue Zuordnung (Altmuster endet, Neumuster beginnt am Folgetag) erklären; „Wer war Eigentümer am Stichtag“ vorführen.
4. Hinweis auf den schnelleren Weg über „Liste importieren“ (Kapitel 4 der Anleitung) und auf die Nachforderung an die Vorverwaltung (Kapitel 8, Freigabe nur durch den Admin).
5. Objekt archivieren und wiederherstellen zeigen (Objektansicht „Archivieren“ mit Grund; Objektliste „Archiv“, „Wiederherstellen“). Betonen: In Drive wird dabei nichts gelöscht oder verschoben.

Zu vermeidende Fehler: Objekt doppelt anlegen (0623 und 623 sind dasselbe Objekt, die Anwendung lehnt das ab); Sollzahl Einheiten weglassen (dann entstehen keine Platzhalterakten, „Akten anlegen“ holt sie nach).

### 4.2 Dokumente, Verarbeitung, Dokumenteneingang (bedienung-objektanlage.md Kapitel 5, bedienung-dokumenteneingang.md)

1. Objektansicht, Dokumente und Verarbeitung: „Dokumente hochladen“ mit den Übungsdokumenten, danach „Pipeline-Dry-Run (nur Plan)“ und „Verarbeitung starten“. Fortschritt in der Tabelle Läufe und je Dokument zeigen; Dokumentansicht mit Klassifikationsverlauf je Stufe und Ablageplan öffnen.
2. Erklären, wohin ein Dokument gelangt, das nicht sicher zugeordnet werden kann: `06_Sonstiges/01_Unklar`, `02_Manuelle_Pruefung`, `03_Dubletten`, `04_Nicht_objektbezogen`, jeweils mit Fall im Review Center. Kein Dokument bleibt unabgelegt, keines wird verworfen.
3. Objektliste, „Verarbeitung für alle Objekte starten“: reiht für alle Objekte mit offenen Dokumenten je einen Lauf ein; nur zeigen, in der Einweisung nicht ausführen.
4. Eingang (Recht inbox.work): Zähler in der Kopfzeile, Bereiche Fälle, In Verarbeitung, Synchronisationsfehler, Automatisch zugeordnet (letzte 30 Tage). „Bearbeiten“ öffnet den Fall in der Detailansicht des Review Centers; dort „Zuordnen“ am Kandidaten mit Belegen. Regel: Dokumente aus Paperless ohne Objektbezug werden hier zugeordnet, nie in Paperless von Hand verschoben (Kapitel 4 und 8 der Anleitung Dokumenteneingang).

### 4.3 Review Center (bedienung-review-center.md)

Liste und Filter (Kapitel 1): Objekt, Fallart, Unterfall, Status, Bearbeiter, Zielbereich 01 bis 06, Konfidenz, Jahr, Freitext, Schalter für zurückgestellte und eigene Fälle; „Filtern“ wendet an, „zurücksetzen“ leert. Gespeicherte Sichten (Kapitel 2) für Serienarbeit anlegen: „Sicht speichern als“, wahlweise geteilt.

Fallarten, wie sie im Filter erscheinen, mit dem, was der Anwender tut:

| Fallart | Was zu tun ist |
|---|---|
| unklar | Zielbereich und Pflichtfelder setzen, Speichern |
| Eigentümer mehrdeutig | Kandidat übernehmen (Abschnitt unten) |
| Pflichtmetadatum fehlt | fehlendes Feld (Jahr, Einheit, Eigentümer) ergänzen |
| Verschiebevorschlag | Vorschlag bestätigen oder korrigieren |
| Ordnerstruktur | bestätigen, danach Abgleich wiederholen; Verwerfen nur durch den Admin |
| Objektnummer doppelt | richtigen Ordner festlegen; Verwerfen nur durch den Admin |
| Importzeile unsicher | Zeile entscheiden (Kandidat wählen oder neu anlegen) |
| Liste erkannt | „Import starten“ |
| Stammdaten widersprüchlich | Stammdaten korrigieren |
| Objektzuordnung | Dokument aus dem Eingang einem Objekt zuordnen |
| Abgleichskonflikt | lokalen oder externen Stand wählen (Anleitung Dokumenteneingang, Kapitel 5) |

Detailansicht (Kapitel 3 und 4): links Fall, Klassifikationsverlauf, Entitäten, Kandidaten, Protokoll des Falls; Mitte Dokumentvorschau mit Seitenbereich und maskiertem Textauszug; rechts das Entscheidungsformular mit Zielbereich, Eigentümer, Einheit, Mieter, Unterordner, Unterart, Jahr und Begründung.

Kandidat bestätigen: Bei Fallart Eigentümer mehrdeutig zeigt die Detailansicht die Tabelle Kandidaten mit Eigentümer, Einheit und Zeitraum. „übernehmen“ am richtigen Kandidaten füllt Eigentümer und Einheit im Formular, danach „Speichern (B / Enter)“. Stimmt die Entscheidung mit dem Vorschlag der Anwendung überein, wird sie als Bestätigung gezählt (system_was_correct), sonst als Korrektur; beides trainiert das lokale Modell. Warnt die Anwendung, dass die Entscheidung der Stichtagsabfrage widerspricht, wird die Warnung mit der Entscheidung protokolliert; sie blockiert nicht.

Zielbereich 06 Sonstiges: Wer ein Dokument bewusst nach 06 legt (kein Objektbezug, nicht lesbar, Privates), muss eine Begründung eintragen; das Feld heißt „Begründung (Pflicht bei 06, sonst optional)“. Dokumente in 06 zählen in den Berichten als Anteil 06; Ziel ist, diesen Anteil je Objekt unter dem Zielwert `reports.misc_share_target_pct` zu halten. Deshalb: 06 ist der Auffangbereich, nicht die bequeme Ablage. Ein Dokument, das zum Objekt gehört, bekommt seinen Zielbereich 01 bis 05, auch wenn dafür eine Einheit oder ein Eigentümer nachzuschlagen ist. Ausdrücklich erlaubt sind „Einheit unbekannt“ und „Eigentümer unbekannt“ in Zielbereich 05; das Dokument landet dann in den Sonderakten Unbekannte_WE beziehungsweise Unzugeordnet und bleibt auffindbar.

Weitere Aktionen (Kapitel 5): Zurückstellen (Wiedervorlage), Verwerfen (Grund Pflicht; Objektfälle nur Admin), Zuweisen, Dublette, Aufteilen, Wiedereröffnen, In anderes Objekt übernehmen, Import starten. Rückgängig gibt es nicht als Löschung: eine gespeicherte Entscheidung wird über Wiedereröffnen und eine neue Entscheidung korrigiert; beide bleiben im Protokoll.

Sammelaktionen (Kapitel 6): In der Liste Fälle markieren (Kontrollkästchen, Leertaste, Umschalt plus Klick für Bereiche) und „Markierte gemeinsam bearbeiten“, oder in der Spalte Gruppe die Zahl anklicken; damit öffnet sich die Massenbearbeitung für alle offenen Fälle derselben Gruppe. Sammelfelder Zielbereich (05, 03, 02, 06), Unterordner, Unterart, Jahr gelten für alle Zeilen, „unverändert“ lässt den Vorschlag der Zeile stehen; „Vorschau aktualisieren“ zeigt je Zeile die Ampel: grün eindeutig, gelb „Kandidat wählen“ oder „Einheit wählen“ in der Zeile, rot Konflikt (die Zeile wird nicht ausgeführt, „Ausschließen“ nimmt sie heraus). „Ausführen: N Entscheidungen in einer Aktion“ startet den Hintergrundjob; die Ergebnisseite zeigt den Fortschritt. Die Vorschau schreibt nichts, erst Ausführen ändert Daten. Höchstens 500 Fälle je Sammelaktion (`review.bulk_max_cases`). Verwerfen, Zurückstellen und Zuweisen gibt es nur je Einzelfall.

Übung in der Einweisung: Jeder Teilnehmer entscheidet einen Fall am Testobjekt einzeln, danach gemeinsam eine Sammelaktion mit Vorschau. Serienmodus mit „Speichern und nächster“ (Umschalt + Enter) und die Taste ? für die Tastenbelegung zeigen.

### 4.4 Statusseite und Berichte

Statusseite (Hauptnavigation Status, Recht status.read): Ampel „bereit“ oder „gestört“, Abschnitte Dienste, Heartbeats, Sicherung, Warteschlangen, Klassifikator (Stufe 2), Stufe 3 (externe KI) mit Kosten im Monat, Review (offene Fälle, älter als 7 Tage, mit Kandidatenliste), Google Drive, Speicher, Konfiguration, Alarmbedingungen, Läufe und Verarbeitung je Objekt. Admins sehen zusätzlich „Sweeper jetzt ausführen“ und „Google-Verbindung verwalten“. Sachbearbeiter lesen die Seite, um zu erkennen, ob eine Störung vorliegt, bevor sie eine Meldung an den Admin geben; die Maßnahmen je Fall stehen im Runbook.

Berichte (Hauptnavigation Berichte): Objektübersicht je Objekt mit Dokumenten, Anteil 06 brutto, aktuell und bereinigt, Vollständigkeit, offenen Review-Fällen mit Alter (Median und Maximum in Arbeitstagen, Warnung ab `reports.review_age_warning_days`), Trefferquote des Systems, Akten, KI-Kosten und Importen; „CSV-Export“ für Excel; „alle Objekte“ nimmt archivierte und andere Status dazu. Der Objektbericht (Klick auf die Objektnummer) zeigt die Kacheln, die offenen Punkte der Vollständigkeit und die offenen Fälle mit Verweis ins Review Center. Lesart der Kennzahlen: bedienung-objektanlage.md, Kapitel 9.

Protokoll (Hauptnavigation Protokoll): Revisionsprotokoll mit Zeitpunkt, Nutzer, Aktion, Entität, Objekt, Grund, Vorher und Nachher; Sachbearbeiter sehen die eigenen Einträge, Admins alle; Export als CSV. Jede Anmeldung, jede Entscheidung, jede Änderung an Nutzern und Konfiguration steht dort.

## 5. Grundregeln

1. Nichts wird endgültig gelöscht. Die Anwendung besitzt keine Löschfunktion für Dokumente, Objekte oder Drive-Dateien. Objekte werden archiviert, Nutzer gesperrt, Entscheidungen wiedereröffnet und neu getroffen. Wo Drive-Dateien bewegt werden (Aufräumen des Altbestands, automatisches Nachräumen), gehen sie in den Papierkorb von Google Drive und sind dort 30 Tage wiederherstellbar; der Vorgang steht als `drive.trash` im Protokoll.
2. Vorschau vor Ausführung. Sammelaktionen, Aufräumen und die Deploy-Aktionen mit Schreibwirkung zeigen erst eine Vorschau. Erst „Ausführen“ oder das Argument `echt` ändert Daten.
3. Entscheidungen sind nachvollziehbar. Jede Entscheidung trägt Nutzer, Zeit, Vorher und Nachher und ist im Protokoll des Falls und im Revisionsprotokoll sichtbar. Deshalb mit dem eigenen Konto arbeiten, nie mit dem eines Kollegen.
4. Freigaben der Geschäftsführung. Folgende Vorgänge werden vor der Ausführung mit der Geschäftsführung abgestimmt und nur durch den Admin ausgeführt: Freigabe einer Nachforderung an die Vorverwaltung (Recht demands.approve), Verwerfen von Objektfällen (Ordnerstruktur, Objektnummer doppelt), Aufräumen des Altbestands in den Papierkorb, Änderungen an der Konfiguration mit Wirkung auf Ablage oder Klassifikation (Namensmuster, Schwellwerte, KI-Provider), Löschläufe und Aufbewahrungsfristen, Anlage und Sperre von Nutzern, Rollenwechsel.
5. Keine realen Daten in Übungen, Tests und Bildschirmaufnahmen. Übungen laufen an Testobjekten mit synthetischen Daten; die Anwendung kennzeichnet sie in der Objektliste mit „Test“.
6. Der zweite Faktor gehört zur Person. Wiederherstellungscodes sicher und getrennt vom Mobilgerät aufbewahren; „Für 90 Tage merken“ nur auf dem eigenen Arbeitsplatzrechner. Ein Zurücksetzen durch den Admin erfolgt nur nach Identitätsprüfung außerhalb des Systems.
7. Bei Störung zuerst die Statusseite prüfen, dann den Admin informieren. Schreibjobs nach Drive warten bei einer Google-Störung und laufen nach der Behebung weiter; eine Entscheidung im Review Center geht dadurch nicht verloren.
8. Zielbereich 06 nur mit Begründung und nur, wenn kein Zielbereich 01 bis 05 passt.

## 6. Häufige Fragen

| Frage | Antwort |
|---|---|
| Ich habe mein Startpasswort vergessen, bevor ich mich angemeldet habe. | Die Nutzerverwaltung bietet kein Setzen eines neuen Passworts, und eine Passwortrücksetzung per E-Mail ist nicht eingerichtet (der E-Mail-Versand der Anwendung dient nur der Alarmierung). Der Admin sperrt das Konto und legt mit einer anderen E-Mail-Adresse ein neues an. Startpasswörter deshalb sofort nach Übergabe verwenden. |
| Die Anwendung leitet mich immer auf die Einrichtungsseite des zweiten Faktors. | Die Rolle Admin verlangt den zweiten Faktor. Einrichtung nach Abschnitt 3.1 abschließen; danach ist die Anwendung frei erreichbar. |
| Ich habe die Wiederherstellungscodes nicht gespeichert. | Unter Konto neue Wiederherstellungscodes erzeugen; die alten verlieren ihre Gültigkeit. |
| Mein Konto ist gesperrt, obwohl ich das Passwort kenne. | Fünf Fehlversuche in Folge sperren für 15 Minuten. Warten oder den Admin um „entsperren“ bitten. Hat der Admin das Konto absichtlich gesperrt (Status gesperrt), hebt nur er es mit „aktivieren“ auf. |
| Ich sehe das Menü Nutzer, Google Drive oder Paperless nicht. | Diese Menüs setzen Admin-Rechte voraus. |
| Warum verlangt die Anwendung erneut mein Passwort, obwohl ich angemeldet bin? | Verwaltungsaktionen und Freigaben verlangen den Step-up, wenn die letzte Anmeldung oder Bestätigung länger als 15 Minuten zurückliegt; das ist gewollt. |
| Das Dokument liegt nach dem Speichern noch nicht in Drive. | Die Verschiebung läuft als Hintergrundjob; Status in der Dokumentansicht (Jobs). Bei Google-Störung wartet der Job. |
| Ich habe im Review Center falsch entschieden. | Fall öffnen (Filter Status erledigt), Wiedereröffnen (Taste W), neu entscheiden. Beide Entscheidungen bleiben im Protokoll. Bereits verschobene Dateien werden durch die neue Entscheidung erneut abgelegt, nicht zurückbewegt. |
| Kann ich mehrere Fälle auf einmal verwerfen oder zurückstellen? | Nein. Die Massenbearbeitung bestätigt und korrigiert Ablagen; Verwerfen, Zurückstellen und Zuweisen erfolgen je Fall. |
| Wo sehe ich, wer was geändert hat? | Protokoll in der Hauptnavigation (eigene Einträge; Admins alle) und der Abschnitt Protokoll in jeder Falldetailansicht. |
| Darf ich ein Testobjekt löschen? | Nein, es gibt keine Löschfunktion. Testobjekte werden archiviert (Objektansicht, „Archivieren“). |
| Wer ändert Konfigurationswerte, zum Beispiel Schwellwerte? | Nur der Admin nach Abstimmung mit der Geschäftsführung; jede Änderung steht im Protokoll (docs/anleitungen/admin-konfiguration.md). |

## 7. Einweisungsprotokoll

Je Einweisungstermin ein Protokoll. Themen sind die Abschnitte dieses Leitfadens; abgehakt wird, was vorgeführt und von den Teilnehmern selbst ausgeführt wurde. Das unterschriebene Protokoll und die Nutzerliste aus Abschnitt 2.4 sind der Nachweis für docs/plan/abnahme.md, Abschnitt 2.

| Datum | Dauer (Ist) | Teilnehmer (Name, Rolle) | Einweisender | Themen (Abschnitte 3, 4.1, 4.2, 4.3, 4.4, 5) | Selbst ausgeführt (Login, mit zweitem Faktor sofern eingerichtet; Objekt anlegen; Fall entscheiden; Sammelaktion mit Vorschau) | Offene Fragen | Unterschrift Teilnehmer | Unterschrift Einweisender |
|---|---|---|---|---|---|---|---|---|
| [TT.MM.JJJJ] | [Minuten] | [Name, Admin] | [Name] | [3, 4.1, 4.2, 4.3, 4.4, 5] | [ja mit zweitem Faktor, ja, ja, ja] | [keine] | | |
| [TT.MM.JJJJ] | [Minuten] | [Name, Sachbearbeiter] | [Name] | [3, 4.1, 4.2, 4.3, 4.4, 5] | [ja mit oder ohne zweiten Faktor, ja, ja, ja] | [keine] | | |
| [TT.MM.JJJJ] | [Minuten] | [Name, Sachbearbeiter] | [Name] | [3, 4.1, 4.2, 4.3, 4.4, 5] | [ja mit oder ohne zweiten Faktor, ja, ja, ja] | [keine] | | |

Abnahmevermerk:

| Punkt | Nachweis | Stand | Datum |
|---|---|---|---|
| Nutzer angelegt nach Nutzerliste V-20, mindestens zwei Admin-Konten | Nutzerliste (Abschnitt 2.4), Nutzerliste in der Anwendung | [erfüllt oder offen] | [TT.MM.JJJJ] |
| Zweiter Faktor bei allen Admins eingerichtet, Wiederherstellungscodes abgelegt | Spalte „2. Faktor“ in der Nutzerliste der Anwendung zeigt „eingerichtet“ | [erfüllt oder offen] | [TT.MM.JJJJ] |
| Startpasswortdatei auf dem Server gelöscht | Deploy-Aktion `check` meldet, dass die Datei nicht mehr existiert | [erfüllt oder offen] | [TT.MM.JJJJ] |
| Einweisung aller Anwender durchgeführt | Protokolle oben, je Teilnehmer eine Zeile | [erfüllt oder offen] | [TT.MM.JJJJ] |
| Testobjekt archiviert | Objektliste, „Archiv“ | [erfüllt oder offen] | [TT.MM.JJJJ] |

Ansprechpartner nach der Einweisung: der Admin der Anwendung. Störungen mit Erkennung und Maßnahme stehen in docs/betrieb/runbook.md.
