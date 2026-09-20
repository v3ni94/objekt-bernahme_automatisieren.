# Change Request CR-06 (Entwurf): Belegprüfung je Wirtschaftsjahr unter 03_Buchhaltung

Stand: 20.09.2026, Entwurf des Auftragnehmers auf Zuruf des Auftraggebers (Nachricht vom 20.09.2026 abends). Kein freigegebener Change Request; Umsetzung erst nach Entscheidung der Geschäftsführung zu den offenen Fragen in Abschnitt 4.

## 1. Anliegen des Auftraggebers

Unter Buchhaltung soll ein Bereich Belegprüfung entstehen. Er enthält je Geschäftsjahr einen Ordner (Beispiel: 03_Buchhaltung/Belegprüfung/Belegprüfung 2026), der für den Verwaltungsbeirat oder externe Belegprüfer freigegeben wird.

## 2. Ist-Stand (aus dem Quellcode, 20.09.2026)

- Kategorie 03 „Buchhaltung“ (`db/seeds/document_categories.json`, folder_name `03_Buchhaltung`) hat im Seed keine Unterordner. Unterordner gibt es nur für 02 Stammakte (15), 05 Eigentümerakte (11, je Akte) und 06 Sonstiges (4).
- Jahresbezogene Ordner kennt die Struktur nicht. Das Wirtschaftsjahr steckt nur in den Vollständigkeitsprüfungen und in Dokumentnamen.
- Der Ordnerabgleich (`reconcile`, Sammelaktion `d reconcile-all <branch>`) legt Hauptordner und die statischen Unterordner an. Freigaben (Drive-Berechtigungen) setzt die Anwendung nicht; der Drive-Scope ist auf Dateien und Ordner begrenzt.

## 3. Vorschlag

1. Neuer Unterordner 03/01 „Belegprüfung“ (folder_name `01_Belegpruefung`) als statischer Unterordner im Seed, Anlage über den Ordnerabgleich wie die übrigen Unterordner.
2. Darunter je Wirtschaftsjahr ein Ordner „Belegprüfung JJJJ“, angelegt vom Ordnerabgleich für die Jahre des Übernahmezeitraums des Objekts (drei abgeschlossene Wirtschaftsjahre plus laufendes Jahr, wie in den Vollständigkeitsprüfungen) und danach jährlich zum Jahreswechsel. Dafür braucht der Ordnerbaum eine neue Knotenart „Jahresordner“ mit Jahr und Bezug auf den Unterordner (additive Migration, keine Änderung an bestehenden Knoten).
3. Befüllung: Keine automatische Klassifikation in diesen Bereich. Die Belegprüfung ist eine kuratierte Zusammenstellung, deshalb Befüllung über eine ausdrückliche Aktion in der Anwendung („Für Belegprüfung JJJJ bereitstellen“) je Dokument oder je Dokumentart und Jahr. Ob dabei Kopien oder Drive-Verknüpfungen (Shortcuts) abgelegt werden, ist offene Frage 4.2.
4. Freigabe an Beiräte: außerhalb der Anwendung durch die Geschäftsführung in Google Drive (Rolle Betrachter, zeitlich begrenzt, nur der Jahresordner). Die Anwendung dokumentiert die Freigabe zunächst nicht. Eine spätere Ausbaustufe kann Freigaben protokollieren, dafür wäre ein zusätzlicher Drive-Scope für Berechtigungen nötig (Datenschutzprüfung).

## 4. Offene Fragen an die Geschäftsführung

4.1 Wirtschaftsjahr gleich Kalenderjahr für alle Objekte, oder gibt es abweichende Wirtschaftsjahre? Danach richtet sich die Benennung „Belegprüfung 2026“ oder „Belegprüfung 2025/2026“.
4.2 Inhalt der Jahresordner: Kopien der Belege (Speicherverbrauch, zwei Stände) oder Drive-Verknüpfungen auf die Originale in 03_Buchhaltung (eine Wahrheit, Freigabe wirkt aber nur auf den Ordner, nicht auf die verknüpften Dateien, die Beiräte sehen die Verknüpfungen dann ohne Zugriff). Fachlich vermutlich Kopien, das ist zu entscheiden.
4.3 Welche Dokumentarten gehören hinein: nur Rechnungen und Kontoauszüge des Jahres, oder auch Verträge, Abrechnung, Wirtschaftsplan?
4.4 Gilt der Bereich nur für WEG-Objekte (Beirat) oder auch für Mietverwaltung (Belegprüfung durch Eigentümer)?
4.5 Freigabeprozess: Wer erteilt die Freigabe in Drive, wie lange bleibt sie bestehen, und wird sie in der Objektakte dokumentiert (Nachweis nach DSGVO Art. 30, Verzeichnis der Verarbeitungstätigkeiten)?

## 5. Aufwand und Risiken (Einschätzung)

- Punkte 1 und 2: mittlerer Aufwand (Seed, Knotenart Jahresordner, Ordnerabgleich, Tests, Doku), keine Auswirkung auf laufende Verarbeitung.
- Punkt 3: mittlerer Aufwand (Aktion, Ablage als Kopie oder Verknüpfung, Protokoll). Abhängig von 4.2 und 4.3.
- Risiko Datenschutz: Freigabe an Externe. Nur Unterlagen mit Bezug zur Belegprüfung, keine Mieter- oder Eigentümerakten. Freigabe ausschließlich auf den Jahresordner.
- Empfehlung: erst nach Abschluss der laufenden Aufarbeitung (Prüfcenter, Bestandslauf Paperless) umsetzen, damit der Ordnerabgleich nicht parallel zur Massenablage läuft.
