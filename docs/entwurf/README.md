# Arbeitspapiere der Entwurfsphase (10.09.2026)

Dieses Verzeichnis enthält die unveränderten Arbeitspapiere, aus denen `docs/architektur.md`, `docs/umsetzungsplan.md` und `docs/betrieb.md` konsolidiert wurden. Sie dienen der Nachvollziehbarkeit der Stack-Entscheidung und der Fachentwürfe. Sie sind nicht verbindlich: Bei Abweichungen (Tabellen- und Feldnamen, Statuswerte, Konfigurationsschlüssel, Schwellwerte, Container-Namen, Meilenstein- und Fragennummern) gelten ausschließlich die drei konsolidierten Dokumente. Insbesondere verwendet der Fachentwurf E ein eigenes Datenvokabular, das im Umsetzungsplan (Anhang B, Beschluss B-01) durch das Bezeichnerregister aus `docs/architektur.md` Abschnitt 5.7 ersetzt wurde.

Die Arbeitspapiere enthalten an einigen Stellen die Altbezeichnung des Auffangordners (Präfix 05) als Literal, weil sie vor der Festlegung entstanden sind, diese nur noch als Konfigurationswert zu führen. Das Verzeichnis ist deshalb eine dokumentierte Ausnahme der Grep-Prüfung aus der Definition of Done (Umsetzungsplan, Frage F20 und Beschluss B-22).

| Datei | Inhalt | Rolle in der Konsolidierung |
|---|---|---|
| `befundakte.md` | Ermittelte Fakten vor der Entwurfsphase: leeres Repository, nicht erreichbarer VPS, Strukturbefunde des Stammdatenexports (ohne personenbezogene Daten), HVM-CI, Widersprüche im CR | Faktenbasis aller Arbeitspapiere; Widersprüche wurden zu Fragen F4, F5, F6, F10, F12, F20 |
| `stack_A_python_monolith.md` | Stack-Vorschlag A: Python-Monolith mit Django, HTMX, Celery, Redis, MariaDB | empfohlen und mit 22 Auflagen (Ü1 bis Ü22) übernommen |
| `stack_B_api_spa.md` | Stack-Vorschlag B: Python-API plus Single-Page-Anwendung (FastAPI, SQLAlchemy, Vue) | unterlegen; Übernahmen: Seitenbilder, Vorschau-Endpunkt, Netztrennung, zwei Build-Ziele |
| `stack_C_polyglott.md` | Stack-Vorschlag C: Laravel-Web-Schicht plus Python-Worker | unterlegen; Übernahmen: Chunking als Pflicht, Effizienzfaktor im Rechenweg, zwei Datenbankkonten, Audit-Trigger, Container-Härtung |
| `gutachten_1.md` | Gutachten 1: Betrieb, Wartbarkeit, Gesamtkosten über fünf Jahre | Bewertung A 84,0, B 64,0, C 60,0 |
| `gutachten_2.md` | Gutachten 2: fachliche Umsetzbarkeit, Zeit bis zum produktiven Nutzen | Bewertung A 79,0, B 64,5, C 63,5; Planänderungen (Import vor Review Center, Massenbearbeitung in M7, Prüfpunkt P1) |
| `gutachten_3.md` | Gutachten 3: Risiko, Sicherheit, Datenschutz, Performance-Belastbarkeit | Bewertung A 84,0, B 70,0, C 67,0; Auflagen Ü1 bis Ü22 |
| `fach_D_datenmodell.md` | Fachentwurf D: Datenmodell als SQL-DDL | einzige Bezeichnerquelle (Beschluss B-01), ergänzt um die Migration in `docs/architektur.md` 5.6 |
| `fach_E_pipeline.md` | Fachentwurf E: dreistufige Klassifikation, Maskierung, Idempotenz, Performance-Modell, Testkatalog mit 34 Beispieldokumenten | fachlich übernommen, Bezeichner auf D umgestellt |
| `fach_F_drive.md` | Fachentwurf F: Drive-Adapter, Ordnerabgleich, Benennungsfunktion mit Testtabelle, Listen-Versionierung | übernommen; Redirect-URIs und Audit-Aktionen aus G |
| `fach_G_betrieb_sicherheit.md` | Fachentwurf G: Compose, Härtung, Backup, Deployment, OAuth-Anleitung, Rollen | Grundlage von `docs/betrieb.md`; Verzeichnisse und KI-Konfiguration nach B-06 und B-14 angepasst |
| `fach_H_review_listen_import.md` | Fachentwurf H: Review Center, Vollständigkeitsprüfung, Nachforderung, Listen, Import-Parser, Bedienungsanleitung | übernommen; Entscheidungstabelle der Fallarten nach B-09 |
| `kritik_1.md` bis `kritik_4.md` | Prüfung der Fachentwürfe gegen den CR (Abschnitte 0 bis 6, 7 bis 10, 11 bis 16, Querschnitt), 103 Findings | jedes Finding ist im Umsetzungsplan Anhang B als Beschluss geschlossen oder als Frage geführt |

Personenbezogene Daten aus dem Verwaltungsbestand sind in keinem Arbeitspapier enthalten; Beispiele sind synthetisch.
