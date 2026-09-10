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
| Token ungültig, Drive-Quota, Worker ohne Fortschritt, Fallback aktiv, Zertifikat, Kostenlimit, Dateianzahl ungleich | folgen mit M4 bis M13 | | |

Kontakt und Zeitfenster für Deployments: Frage F27 (docs/umsetzungsplan.md).
