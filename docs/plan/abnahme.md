# Abnahmeprotokoll gegen die Definition of Done (CR 14)

Stand: 10.09.2026 (M15, Umsetzungsplan 2.19 Schritt 8). Jede Zeile nennt den Nachweis und den tatsächlichen Stand. Nichts ist als erfüllt eingetragen, was nicht nachgewiesen ist. Die Abnahme erfolgt schriftlich durch den Auftraggeber, nachdem alle Zeilen den Stand „erfüllt“ tragen.

## 1. Die sechs Punkte der Definition of Done

| Nr. | Punkt (CR 14, wortgetreu) | Nachweis | Stand am 10.09.2026 | Offen bis |
|---|---|---|---|---|
| 1 | Anwendung läuft produktiv auf dem VPS hinter Traefik unter `uebernahme.muellerhv.de` | Deployment-Tests T1 bis T14 (docs/betrieb/deployment-test.md), Statusseite, `curl -I https://uebernahme.muellerhv.de` | nicht erfüllt: kein Serverzugang (V-01), Deploy Key und Secrets nicht eingerichtet (docs/betrieb/github-deploy.md); Compose, Deploy-Workflow, Rollback und Backup liegen im Repository und bauen in der CI | V-01, V-02 bis V-04, DNS (V-10) |
| 2 | Kein Vorkommen der Altbezeichnung des Auffangordners im Code (Grep leer), Doku aktualisiert (Wortlaut des CR; die Altbezeichnung wird hier nicht ausgeschrieben, damit der Grep-Nachweis leer bleibt) | `scripts/check_no_legacy_names.sh` (CI-Schritt); Ausnahmen db/seeds/, docs/anforderungen/, docs/entwurf/ | erfüllt in der Entwicklungsumgebung und in der CI (Lauf zu jedem Commit); Dokumentation fortgeschrieben (README, docs/plan, docs/betrieb, docs/anleitungen, docs/architektur) | erneuter Lauf am Tag der Abnahme |
| 3 | Ordnerabgleich auf allen bestehenden Objektordnern durchgeführt, Protokoll liegt vor | docs/betrieb/abgleich-protokoll.md, Export unter `exports/drive-sync/`, Prüfabfragen aus M14 | nicht erfüllt: Google-Zugang (V-05 bis V-09) und Freigabe des Dry-Run-Protokolls (V-19) fehlen; Vorlage und Ablauf liegen vor | V-05 bis V-09, V-19 |
| 4 | Alle Tests grün, Performance-Test bestanden | CI-Lauf des Abnahme-Commits (Unit, Integration, Migration vorwärts und rückwärts, Grep, Image-Build); docs/betrieb/performance-bericht.md | Testsuite: 374 Tests grün, 1 übersprungen (Stand M12, Entwicklungsumgebung); CI-Lauf 34519975229 vom 10.09.2026 grün (Tests, Migrationen vorwärts und rückwärts, Grep, Image-Build web und worker); Performance-Test nicht erfüllt (kein Server, kein Korpus nach F18) | V-01, V-08, F18 |
| 5 | Admin-Konfiguration für Namensmuster Objektordner, Unterstruktur, Schwellwerte, Duplikat-Option und KI-Provider dokumentiert | docs/anleitungen/admin-konfiguration.md (aus dem Katalog erzeugt, alle Schlüssel mit Seed, Bedeutung, Wertebereich, Wirkung) | erfüllt als Dokument; Seed-Werte sind Vorschlagswerte (ANNAHME), bis der Auftraggeber die offenen Fragen F1 bis F31 beantwortet | Antworten auf F-Fragen mit Wirkung auf Schlüssel |
| 6 | Kurze Bedienungsanleitung für Review Center und Objektanlage | docs/anleitungen/bedienung-review-center.md, docs/anleitungen/bedienung-objektanlage.md | erfüllt als Text (aufgabenorientiert nach H 8); Bildschirmfotos fehlen, weil die Oberfläche noch auf keinem Server läuft; sie werden nach T1 ohne reale Daten ergänzt | V-01 |

## 2. Weitere Nachweise aus dem Umsetzungsplan (2.19)

| Punkt | Nachweis | Stand |
|---|---|---|
| Vollständige Testsuite in der CI | GitHub Actions, Workflow `CI` je Commit | läuft; Ergebnis des jeweils letzten Laufs in docs/plan/status.md |
| Grep leer | `scripts/check_no_legacy_names.sh` | leer (10.09.2026) |
| PII-Prüfung der Dokumentation leer | `scripts/check_no_pii.sh` mit `PII_CSV_DIR` auf die Stammdaten-CSV | in der Entwicklungsumgebung ohne CSV übersprungen; Dokumentation verwendet ausschließlich die synthetischen Namen und Objektnummern aus tests/ (Mustermann, Beispiel, Altmuster, Neumuster; 623, 624, 625, 631, 700) |
| Deployment-Tests T1 bis T14 protokolliert | docs/betrieb/deployment-test.md | alle ausstehend (V-01) |
| Restore-Probe aus der jüngsten Sicherung | Protokoll nach G 7.4 | ausstehend (V-01); Skript `restore.sh` und Ablauf in docs/betrieb.md |
| Einweisung der Anwender, Nutzeranlage, TOTP | Nutzerliste (V-20), Termine | ausstehend |
| Lizenzabschnitt | docs/architektur/lizenzen.md | vorhanden; Prüfung nach F29 nur bei Weitergabe |
| Wartungsplan | Abschnitt 3 | vorhanden |

## 3. Wartungsplan (Übergabe)

| Turnus | Aufgabe | Werkzeug | Verantwortlich |
|---|---|---|---|
| täglich (automatisch) | Sicherung, Token-Refresh, Alarmprüfung alle fünf Minuten | Backup-Container, Beat-Tasks, Statusseite | System; Kontrolle durch Admin |
| wöchentlich | Statusseite prüfen (Sicherung, Token, Platte, Kosten), Review-Rückstand und Alter der Fälle in den Berichten | Statusseite, Berichte | Admin |
| monatlich | Restore-Probe auf leerer Instanz mit Stichproben nach G 7.4; Anteil 06_Sonstiges je Objekt und Trefferquote prüfen | `restore.sh`, Berichte | Admin |
| quartalsweise | Abhängigkeiten anheben (`uv pip compile`, Testsuite, Image-Build), Lizenzliste aktualisieren, Schlüsselrotation nach F28 (TOKEN_KEY, TOTP_KEY, API-Schlüssel), Aufbewahrungsfristen mit Geschäftsführung und Steuerberater prüfen | docs/architektur/image.md, docs/architektur/lizenzen.md, docs/betrieb.md 3.8 | Entwicklung, Admin |
| bei Bedarf | Nachtraining des Klassifikators prüfen (Statusseite zeigt Modellversion), Regeln in db/seeds/rules ergänzen | `manage.py classifier` | Entwicklung |

## 4. Unterschriften

| Rolle | Name | Datum | Unterschrift |
|---|---|---|---|
| Auftraggeber (Geschäftsführung Hausverwaltung Müller GmbH) | | | |
| Umsetzung | | | |
