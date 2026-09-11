# Deployment-Tests T1 bis T14 (Protokoll)

Stand: 10.09.2026. Definition aus Fachentwurf G Abschnitt 12 und Umsetzungsplan 2.17 Schritt 6. Alle Tests laufen auf dem VPS; Ergebnisse mit Datum, Durchführendem, Befehlsausgaben und Bildschirmfotos (ohne Secrets). Nichts wird vorab als bestanden eingetragen; ausstehende Zeilen bleiben offen, bis der Nachweis auf dem Server vorliegt. Die Tests T8 (Migration vorwärts und rückwärts) und die Prüfabfragen werden zusätzlich in der CI beziehungsweise in der Entwicklungsumgebung ausgeführt; das ersetzt den Nachweis auf dem Server nicht.

| Nr. | Test | Erwartung (Kurzfassung) | Vorbereitung im Repository | Ergebnis auf dem VPS | Datum, Durchführender | Nachweis |
|---|---|---|---|---|---|---|
| T1 | Frischer Checkout | Alle Dienste `healthy` in 3 min, HTTPS mit gültigem Zertifikat, HTTP leitet um | `scripts/deploy.sh`, `docker-compose.yml`, docs/betrieb/deployment.md | **bestanden am 11.09.2026**: alle acht Dienste `healthy` (Anwendungsdienste binnen 11 Sekunden nach dem Start), HTTP leitet global auf HTTPS um (Traefik), HTTPS mit Zertifikatsprüfung antwortet mit 200 (`tls 0`), Zertifikat von Let's Encrypt (Aussteller CN = YR2, Inhaber CN = uebernahme.muellerhv.de, gültig vom 11.09.2026 bis 10.12.2026) | 11.09.2026, Deploy-Workflow | Workflow-Läufe `first-run`, `cert-retry`, `smoke` (Lauf 34555104682), Bereitschaftsabfrage `/readyz/` |
| T2 | Kein Host-Port | Nur Traefik veröffentlicht 80 und 443 | Compose ohne `ports:` an Anwendungsdiensten | **bestanden am 11.09.2026**: kein Anwendungscontainer veröffentlicht einen Host-Port (`EXPOSE` ohne Veröffentlichung); auf dem Host nur die zwei fremden Container 32768 und 32771 sowie Traefik im Host-Netz | 11.09.2026, Aktion `deploy-tests` | Lauf 34598173584 |
| T3 | Isolation `data` | Kein Weg ins Internet aus `db` und `redis` | Netz `data` mit `internal: true` | **bestanden am 11.09.2026**: Verbindungsversuch zu 1.1.1.1:443 aus `db` und `redis` blockiert | 11.09.2026, Aktion `deploy-tests` | Lauf 34598173584 |
| T4 | Serverneustart | Lauf wird fortgesetzt, keine Doppelverarbeitung, Sweeper im Log | Jobs mit Idempotenzschlüssel (B-03), Sweeper beim Start (M5) | ausstehend | | Abfrage, Log |
| T5 | Container-Abbruch Worker | Chunk wird wiederholt, Dokument endet in DONE | Stale-Fristen je Jobtyp, Wiederaufnahme ohne doppelte Seiten (Test in M5) | ausstehend | | Log, Abfrage |
| T6 | Backup und Wiederherstellung | Zeilenzahlen identisch, Stichproben nach G 7.4, Token-Status `active` | Backup-Container, `restore.sh` | ausstehend | | Protokoll G 7.4 |
| T7 | Rollback | Vorherige Version in einer Minute, `tags.log` mit ROLLBACK | `scripts/rollback.sh`, Deploy-Workflow mit Aktion rollback | ausstehend | | Ausgabe, Bildschirmfoto |
| T8 | Migration vorwärts und rückwärts | Fehlerfrei, `schema_migrations` konsistent | `scripts/check_migrations_roundtrip.sh` (in der Entwicklungsumgebung nach jeder Migration grün) | ausstehend | | Ausgabe |
| T9 | OAuth 8 Tage | Kein `drive.authorize` über 8 Tage, alle Refreshes `ok` | Beat-Tasks Lesetest und täglicher Refresh, `drive_oauth_proof` | ausstehend (V-05 bis V-09) | | Export nach G 10.7 |
| T10 | Ressourcenlimits | Kein OOM-Kill, `web` p95 unter 2 s, Seiten pro Minute protokolliert | `scripts/perf_probe.sh`, Latenzsonde | ausstehend (mit M13) | | Messdatei, Bericht |
| T11 | Logging | JSON je Zeile, keine IBAN in Logs | JSON-Logs mit Maskierung (M1), Prüfabfrage | **bestanden am 11.09.2026**: 0 IBAN-Treffer in 2.000 Zeilen je Dienst; Anwendungsmeldungen von Web und Worker als JSON je Zeile (erster Lauf zeigte Worker-Text, weil Celery den Root-Logger übernahm; behoben mit `CELERY_WORKER_HIJACK_ROOT_LOGGER = False`, Nachprüfung im zweiten Lauf) | 11.09.2026, Aktion `deploy-tests` | Läufe 34598173584, 34598772657 |
| T12 | Sicherheitseinstellungen Host | Root- und Passwort-Login aus, UFW 22, 80, 443, unattended-upgrades, Zeitzone Europe/Berlin | docs/betrieb.md Abschnitt 1.4 | ausstehend | | Ausgaben |
| T13 | Login und Rollen | TOTP-Einrichtung erzwungen, Zugriff verweigert mit `auth.denied` | Login mit TOTP-Pflicht, Rollen (M1) | ausstehend | | Bildschirmfoto, Abfrage |
| T14 | Healthcheck-Endpunkte | `/healthz/` nur `ok`, `/readyz/` ohne Token 401 | Endpunkte (M1) | **bestanden am 11.09.2026**: `/healthz/` 200 mit Inhalt `ok`, `/readyz/` ohne Token 401, mit Token vollständiger Befund | 11.09.2026, Aktionen `smoke` und `deploy-tests` | Läufe 34595946648, 34598173584 |

## Prüfabfragen

```sql
-- T4, T5: keine Doppelverarbeitung
SELECT sha256, COUNT(*) FROM documents WHERE deleted_at IS NULL GROUP BY object_id, sha256 HAVING COUNT(*) > 1;
-- T11: IBAN in Logs
docker compose logs | grep -E 'DE[0-9]{2}[0-9 ]{18,}'
```

## Abschluss

Definition of Done für den Betriebsteil: T1 bis T14 bestanden und dokumentiert, Serverbefund abgelegt, Runbook vorhanden, Wiederherstellungsprotokoll vorhanden. Stand am 11.09.2026: T1, T2, T3, T11, T14 bestanden, T4 bis T10, T12, T13 ausstehend; Serverbefund abgelegt (docs/betrieb/serverbefund.md), Runbook vorhanden (docs/betrieb.md), Wiederherstellungsprotokoll ausstehend (T6).
