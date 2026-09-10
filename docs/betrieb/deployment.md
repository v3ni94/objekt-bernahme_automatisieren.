# Deployment (Kurzform, Stand M1)

Verbindliche Beschreibung in docs/betrieb.md Abschnitt 4. Hier die Befehlsfolge.

## Erstinstallation

1. Serverbefund (`scripts/measure_server.sh`) und OCR-Probelauf (`scripts/perf_probe.sh`) nach docs/betrieb/m0-anleitung.md.
2. Host-Härtung nach docs/betrieb.md Abschnitt 2, DNS-Eintrag vorhanden (V-04).
3. Verzeichnisse (docs/betrieb.md 3.3) und Secrets (3.8) anlegen; `.env` aus `.env.example` mit den Werten des Ergebnisblatts.
4. Checkout: `sudo mkdir -p /opt/objektakte && sudo chown deploy:deploy /opt/objektakte && git clone [REPO_URL] /opt/objektakte && cd /opt/objektakte`
5. `scripts/deploy.sh --first-run main` (baut Images, startet db, redis, backup, migriert, lädt Seeds, setzt Tabellenrechte, startet alle Dienste, Smoke-Test).
6. Ersten Admin anlegen: `docker compose exec web app-create-admin --email [ADMIN_ADRESSE]`; TOTP beim ersten Login einrichten. Zweiten Admin anlegen.
7. Deployment-Tests T1, T2, T3, T7, T8, T11, T12, T13, T14 durchführen und in docs/betrieb/deployment-test.md protokollieren.

## Regelbetrieb

- Deployment: `scripts/deploy.sh [branch]` (Dump vor Migration, Migration als eigener Schritt, Tabellenrechte, Containerwechsel, Smoke-Test, Tags).
- Rollback: `scripts/rollback.sh` (ein Befehl, vorheriges Image).
- Schema-Rollback nur als Ausnahme nach docs/betrieb.md 4.4: `docker compose run --rm --no-deps web app-migrate --down <app> <migration>`.
- Seeds erneut laden: `docker compose run --rm --no-deps web app-seed` (`--force` überschreibt geänderte Werte, protokolliert).
- Tabellenrechte nachziehen: `docker compose run --rm --no-deps -T web app-grants-sql | docker compose exec -T db sh -c 'mariadb -uroot -p"$(cat /run/secrets/db_root_password)" "$MARIADB_DATABASE"'`

## Container-Unterbefehle

| Befehl | Wirkung |
|---|---|
| `app-migrate [--check]` | Migrationen mit dem DDL-Konto `app_migrate` |
| `app-migrate --down <app> <migration>` | genau auf eine frühere Migration zurück |
| `app-seed [--force]` | Rollen, Dokumentkatalog (Kategorien, Unterordner, Unterarten), Aufbewahrungszeilen und `app_settings` aus `db/seeds/`; idempotent |
| `app-create-admin --email ...` | Admin anlegen |
| `app-grants-sql` | Tabellenrechte als SQL ausgeben (docs/architektur.md 9.4) |
| `manage.py bezeichnerregister --write` | Bezeichnerregister aus dem Code erzeugen (Entwicklung, vor jedem Commit mit Schemaänderung) |
