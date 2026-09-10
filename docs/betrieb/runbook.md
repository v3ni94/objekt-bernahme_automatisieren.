# Runbook (Gerüst, Stand M1)

Störungsfälle mit Erkennung, Sofortmaßnahme und Nacharbeit. Vollständige Fassung in M15 (docs/umsetzungsplan.md 2.19). Kommandos laufen im Checkout-Verzeichnis auf dem VPS als Deploy-Nutzer.

| Fall | Erkennung | Sofortmaßnahme | Nacharbeit |
|---|---|---|---|
| Dienst nicht healthy | `docker compose ps`, Statusseite rot, `/readyz/` liefert 503 | `docker compose logs --tail 200 <dienst>`; bei `web` Schema prüfen (`docker compose run --rm --no-deps web app-migrate --check`) | Ursache im Deployment-Protokoll vermerken |
| Schema nicht aktuell | Log `Schema nicht aktuell`, `/readyz/` schema.ok false | `scripts/deploy.sh` erneut ausführen (Migration ist eigener Schritt) | prüfen, warum die Migration nicht lief |
| Fehlerhaftes Deployment | Smoke-Test rot, Fehler nach `up -d` | `scripts/rollback.sh` (ein Befehl, docs/betrieb.md 4.3) | Fehler im Repository beheben, erneut deployen |
| Backup fehlgeschlagen | Statusseite Sicherung rot, `status.json` status failed, Healthcheck `backup` rot | `docker compose exec -T backup /usr/local/bin/backup.sh`; Platte prüfen (`df -h /srv`) | Ursache beheben, Restore-Probe planen |
| Platte knapp | `/readyz/` disk.ok false, Ingest stoppt | `du -sh /srv/objektakte/*`; `work/` und `previews/` sind verlustfrei löschbar (docs/architektur.md 4.3) | Aufbewahrung der Sicherungen prüfen (F26), Tarif prüfen |
| Konto gesperrt (zu viele Fehlversuche) | Meldung beim Login | Admin: Nutzerverwaltung, „entsperren" | Bei Häufung Angriff prüfen (Protokoll auth.login_failed) |
| Zweiter Faktor verloren | Nutzer kann sich nicht anmelden | Admin: Identität außerhalb des Systems prüfen, „TOTP zurücksetzen" | Eintrag im Protokoll auth.totp_reset |
| Token widerrufen oder ungültig | Statusseite rot, Google Drive zeigt `widerrufen`, `drive.token_refresh` mit `revoked` | Admin: Google Drive, „Neu autorisieren“ mit `ablage@muellerhv.de`; Drive-Schreibjobs laufen danach weiter | Nachweiskette T9 beginnt neu; Ursache prüfen (docs/betrieb.md 7.10) |
| Drive-Ratenlimit | Statusseite zeigt Zähler 403/429, Logs `drive files.list status=403` | nichts; Backoff wiederholt automatisch | `drive.max_requests_per_second` senken, Quota in der Cloud Console prüfen |
| Dateianzahl nach Umbenennung ungleich | Lauf `failed`, Review-Fall `rename_count_mismatch`, weitere Schreibaktionen übersprungen | Ordner in Drive prüfen (gleichzeitige Änderung), Review-Fall entscheiden, Abgleich erneut ausführen | nichts wurde zurückgenommen; bei Bedarf `drive_undo_rename <action_id>` |
| Worker ohne Fortschritt, Fallback aktiv, Zertifikat, Kostenlimit | folgen mit M5 bis M13 | | |

Kontakt und Zeitfenster für Deployments: Frage F27 (docs/umsetzungsplan.md).
