#!/bin/sh
# Entrypoint der Anwendungscontainer (docs/betrieb.md 4.1).
# Unterbefehle: app-migrate [--check | --down <app> <migration>], app-seed [--force], app-create-admin --email ...,
# app-grants-sql (gibt die Rechte-SQL aus). Alles andere wird unveraendert ausgefuehrt.
# Migrationen laufen nie automatisch beim Start; deploy.sh ruft app-migrate als eigenen Schritt auf.
set -eu

case "${1:-}" in
  app-migrate)
    shift
    # Migration mit dem DDL-Konto (nur web erhaelt db_migrate_password)
    if [ -n "${DB_MIGRATE_USER:-}" ] && [ -f "${DB_MIGRATE_PASSWORD_FILE:-/nonexistent}" ]; then
      export DB_USER="$DB_MIGRATE_USER" DB_PASSWORD_FILE="$DB_MIGRATE_PASSWORD_FILE"
    fi
    export OBJEKTAKTE_SKIP_SCHEMA_CHECK=1
    if [ "${1:-}" = "--down" ]; then
      shift; app="${1:?app fehlt}"; target="${2:?migration fehlt}"
      exec python manage.py migrate --noinput "$app" "$target"
    fi
    exec python manage.py migrate --noinput "$@"
    ;;
  app-seed)
    shift; export OBJEKTAKTE_SKIP_SCHEMA_CHECK=1
    exec python manage.py seed "$@"
    ;;
  app-create-admin)
    shift; export OBJEKTAKTE_SKIP_SCHEMA_CHECK=1
    exec python manage.py create_admin "$@"
    ;;
  app-grants-sql)
    shift; export OBJEKTAKTE_SKIP_SCHEMA_CHECK=1
    exec python manage.py grants_sql "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
